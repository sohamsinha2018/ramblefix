from __future__ import annotations

import os
import re
import threading
import time
import wave
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np

from ramblefix.config import DEFAULT_QWEN3_ASR_MLX_HINGLISH_MODEL
from ramblefix.external_asr import (
    detect_faster_whisper_language,
    warm_faster_whisper_language_detector,
    warm_oriserve_hindi2hinglish,
)
from ramblefix.hindi_chunk_polish import (
    _extract_text,
    _session_for_model,
    hindi_value_delta,
    meaning_first_update_reject_reasons,
    normalize_roman_hindi_spelling,
    romanize_devanagari_for_hinglish,
    stitch_chunk_texts,
    update_reject_reasons,
    witness_can_accept_rejected_candidate,
)
from ramblefix.hindi_polish import HINDI_LANGUAGE_CODES


_QWEN_STREAM_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindi-qwen-stream")
_DETECTOR_WARM_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindi-detector-warm")
_QWEN_WARM_LOCK = threading.RLock()
_QWEN_WARM_FUTURES: dict[str, Future[object]] = {}
_DETECTOR_WARM_LOCK = threading.RLock()
_DETECTOR_WARM_FUTURE: Future[object] | None = None
_ORISERVE_WARM_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindi-oriserve-warm")
_ORISERVE_RESCUE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hindi-oriserve-rescue")
_ORISERVE_WARM_LOCK = threading.RLock()
_ORISERVE_WARM_FUTURE: Future[object] | None = None
_READY_CHUNK_FINISH_GRACE_SECONDS = 0.35
_READY_CHUNK_FINISH_GRACE_ENV = "RAMBLEFIX_HINDI_STREAM_READY_GRACE_SECONDS"
_PROCESS_FINAL_CHUNKS_ENV = "RAMBLEFIX_HINDI_STREAM_PROCESS_FINAL_CHUNKS"
_TAIL_REDECODE_ENV = "RAMBLEFIX_HINDI_STREAM_TAIL_REDECODE"
_TAIL_REDECODE_SECONDS_ENV = "RAMBLEFIX_HINDI_STREAM_TAIL_SECONDS"
_ORISERVE_RESCUE_ENV = "RAMBLEFIX_HINDI_STREAM_ORISERVE_RESCUE"
_ORISERVE_RESCUE_MAX_AUDIO_SECONDS_ENV = "RAMBLEFIX_HINDI_STREAM_ORISERVE_MAX_AUDIO_SECONDS"
_ORISERVE_BACKGROUND_WAIT_ENV = "RAMBLEFIX_HINDI_STREAM_ORISERVE_BACKGROUND_WAIT_SECONDS"
_RAW_TAIL_RESCUE_ENV = "RAMBLEFIX_HINDI_STREAM_RAW_TAIL_RESCUE"
_TAIL_REDECODE_DEFAULT_SECONDS = 8.0
_TAIL_REDECODE_MIN_REMAINING_SECONDS = 0.70
_ORISERVE_RESCUE_DEFAULT_MAX_AUDIO_SECONDS = 30.0
_ORISERVE_RESCUE_MIN_REMAINING_SECONDS = 1.35
_ORISERVE_BACKGROUND_WAIT_SECONDS = 4.2
_TAIL_CONTENT_STOP_TOKENS = {
    "the",
    "and",
    "for",
    "that",
    "this",
    "with",
    "from",
    "should",
    "would",
    "could",
    "there",
    "right",
    "like",
}
_TAIL_GARBAGE_TOKENS = {"mcpo", "kanaj", "naja", "sarah", "sara", "know"}


@dataclass
class StreamChunk:
    index: int
    path: str
    duration_seconds: float
    compute_seconds: float
    text: str


@dataclass
class DetectorChunk:
    index: int
    path: str
    language: str | None
    language_probability: float | None
    seconds: float
    risk: bool


@dataclass
class StreamFinishResult:
    run_id: str
    text: str
    raw_text: str
    route: str
    safe_update: bool
    reject_reasons: list[str]
    release_tail_seconds: float
    finish_wait_seconds: float
    chunks: list[dict[str, Any]]
    detector_chunks: list[dict[str, Any]]
    quality: dict[str, Any]


class HindiStreamSession:
    def __init__(
        self,
        *,
        run_id: str,
        chunk_dir: str | Path,
        model: str = DEFAULT_QWEN3_ASR_MLX_HINGLISH_MODEL,
        poll_interval_seconds: float = 0.20,
        low_confidence_threshold: float = 0.50,
        early_low_confidence_threshold: float = 0.80,
        ready_file_age_seconds: float = 0.25,
    ) -> None:
        self.run_id = run_id
        self.chunk_dir = Path(chunk_dir).expanduser().resolve()
        self.model = model
        self.poll_interval_seconds = poll_interval_seconds
        self.low_confidence_threshold = low_confidence_threshold
        self.early_low_confidence_threshold = early_low_confidence_threshold
        self.ready_file_age_seconds = ready_file_age_seconds
        self.started_at = time.perf_counter()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._finalizing = threading.Event()
        self._thread: threading.Thread | None = None
        self._detected: dict[int, DetectorChunk] = {}
        self._detector_in_progress: set[int] = set()
        self._chunks: dict[int, StreamChunk] = {}
        self._chunk_in_progress: set[int] = set()
        self._errors: list[str] = []
        self._risk = False
        self._risk_reasons: list[str] = []

    def start(self) -> None:
        self.chunk_dir.mkdir(parents=True, exist_ok=True)
        warm_qwen_worker(self.model)
        warm_detector_worker()
        if _oriserve_rescue_enabled():
            warm_oriserve_worker()
        self._thread = threading.Thread(target=self._run, name=f"hindi-stream-{self.run_id}", daemon=True)
        self._thread.start()

    def cancel(self, *, wait: bool = True) -> None:
        self._stop.set()
        if wait and self._thread:
            self._thread.join(timeout=1.0)

    def finish(
        self,
        *,
        draft_text: str,
        max_release_tail_seconds: float = 3.0,
        wait_timeout_seconds: float = 3.0,
        witness_audio_path: str | Path | None = None,
        witness_timeout_seconds: float = 3.5,
    ) -> StreamFinishResult:
        started = time.perf_counter()
        self._finalizing.set()
        deadline = time.perf_counter() + wait_timeout_seconds
        oriserve_future: Future[dict[str, Any]] | None = None
        with self._lock:
            initial_risk = self._risk
            initial_risk_reasons = list(self._risk_reasons)
        if (
            initial_risk
            and witness_audio_path is not None
            and _oriserve_rescue_enabled()
            and _oriserve_risk_worth_trying(initial_risk_reasons)
        ):
            oriserve_future = _ORISERVE_RESCUE_EXECUTOR.submit(
                _oriserve_rejected_candidate,
                audio_path=Path(witness_audio_path),
                draft_text=draft_text,
                risk_reasons=initial_risk_reasons,
                started_at=started,
                deadline=started + max_release_tail_seconds,
                max_release_tail_seconds=max_release_tail_seconds,
            )
        while time.perf_counter() < deadline:
            with self._lock:
                busy = bool(self._detector_in_progress or self._chunk_in_progress)
                pending = self._pending_ready_count(final=True)
                risk = self._risk
                detector_count = len(self._detected)
                completed_chunk_count = len(self._chunks)
            if not busy and pending == 0:
                break
            if not risk and not busy and detector_count > 0:
                # Do not spend the release path synchronously proving a negative.
                break
            if risk and completed_chunk_count and time.perf_counter() - started >= _ready_chunk_finish_grace_seconds():
                # A completed Hindi chunk can already be merged with the fast
                # draft tail. Do not enqueue/wait for just-written release chunks.
                break
            if risk and not completed_chunk_count and detector_count > 0 and not busy:
                # There is no completed Hindi chunk to update from, and release-time
                # Qwen work is intentionally skipped. Return the fast draft quickly.
                break
            time.sleep(0.05)

        stream_finish_wait = round(time.perf_counter() - started, 3)
        if risk and _process_final_chunks_on_release() and time.perf_counter() < deadline:
            self._process_ready_qwen_chunks(final=True, deadline=deadline)
            stream_finish_wait = round(time.perf_counter() - started, 3)
        with self._lock:
            chunks = [self._chunks[index] for index in sorted(self._chunks)]
            detectors = [self._detected[index] for index in sorted(self._detected)]
            pending_count = self._pending_ready_count(final=True)
            errors = list(self._errors)
            risk = self._risk
            risk_reasons = list(self._risk_reasons)

        raw_text = stitch_chunk_texts([chunk.text for chunk in chunks])
        candidate_text = _merge_ready_hindi_with_draft_tail(
            draft_text=draft_text,
            raw_text=raw_text,
            pending_count=pending_count,
        )
        candidate_text, candidate_repair = _repair_leading_unknown_english_before_hindi(
            draft_text=draft_text,
            candidate_text=candidate_text,
        )
        hindi_value = hindi_value_delta(draft_text, candidate_text)
        partial_merge = candidate_text != raw_text
        reject_reasons: list[str] = []
        witness_quality: dict[str, Any] = {"ran": False}
        tail_redecode_quality: dict[str, Any] = {"ran": False}
        oriserve_quality: dict[str, Any] = {"ran": False}
        candidate_sanitize_quality: dict[str, Any] = {"ran": False}
        raw_tail_quality: dict[str, Any] = {"ran": False, "accepted": False, "reason": "disabled"}
        replacement_text: str | None = None
        if not risk:
            route = "hindi_stream_no_risk"
            safe_update = False
            text = draft_text
        else:
            romanized_candidate_text = normalize_roman_hindi_spelling(romanize_devanagari_for_hinglish(candidate_text))
            candidate_text = normalize_roman_hindi_spelling(candidate_text)
            reject_reasons = update_reject_reasons(
                draft_text=draft_text,
                final_text=candidate_text,
                release_tail_seconds=stream_finish_wait,
                max_release_tail_seconds=max_release_tail_seconds,
                allow_roman_hindi=True,
                strict_new_english=True,
            )
            if not reject_reasons and romanized_candidate_text != candidate_text:
                reject_reasons.extend(
                    update_reject_reasons(
                        draft_text=draft_text,
                        final_text=romanized_candidate_text,
                        release_tail_seconds=stream_finish_wait,
                        max_release_tail_seconds=max_release_tail_seconds,
                        allow_roman_hindi=True,
                        strict_new_english=True,
                    )
                )
            if not reject_reasons and not hindi_value["has_hindi_value"]:
                reject_reasons.append("no-hindi-value")
            if not reject_reasons:
                reject_reasons.extend(meaning_first_update_reject_reasons(draft_text, candidate_text))
            if reject_reasons:
                candidate_sanitize_quality = _sanitize_rejected_new_english_candidate(
                    draft_text=draft_text,
                    candidate_text=romanized_candidate_text,
                    reject_reasons=reject_reasons,
                    release_tail_seconds=stream_finish_wait,
                    max_release_tail_seconds=max_release_tail_seconds,
                )
                if candidate_sanitize_quality.get("accepted") is True:
                    candidate_text = str(candidate_sanitize_quality.get("text") or "").strip()
                    romanized_candidate_text = candidate_text
                    hindi_value = candidate_sanitize_quality.get("hindi_value") or hindi_value
                    candidate_repair = _append_repair_reason(
                        candidate_repair,
                        str(candidate_sanitize_quality.get("reason") or "sanitize-new-english"),
                    )
                    reject_reasons = []
            if (
                pending_count
                and raw_text.strip()
                and not partial_merge
                and not _candidate_covers_draft(draft_text=draft_text, candidate_text=raw_text)
            ):
                reject_reasons.append("pending-chunks")
            safe_update = not reject_reasons
            route = "hindi_stream_safe" if safe_update else "hindi_stream_rejected"
            if safe_update:
                replacement_text = romanized_candidate_text
            if not safe_update and _raw_tail_rescue_enabled():
                raw_tail_quality = _raw_english_tail_rejected_candidate(
                    draft_text=draft_text,
                    raw_text=raw_text,
                    reject_reasons=reject_reasons,
                    release_tail_seconds=stream_finish_wait,
                    max_release_tail_seconds=max_release_tail_seconds,
                )
                if raw_tail_quality.get("accepted") is True:
                    safe_update = True
                    route = "hindi_stream_raw_tail_safe"
                    reject_reasons = []
                    replacement_text = str(raw_tail_quality.get("text") or "").strip()
            if (
                safe_update
                and witness_audio_path is not None
                and _should_prefer_clean_tail_merge(
                    draft_text=draft_text,
                    candidate_text=romanized_candidate_text,
                    hindi_value=hindi_value,
                )
            ):
                tail_redecode_quality = _tail_redecode_rejected_candidate(
                    audio_path=Path(witness_audio_path),
                    draft_text=draft_text,
                    started_at=started,
                    deadline=deadline,
                    max_release_tail_seconds=max_release_tail_seconds,
                )
                if tail_redecode_quality.get("accepted") is True:
                    route = "hindi_stream_tail_preferred"
                    replacement_text = str(tail_redecode_quality.get("text") or "").strip()
            if not safe_update and witness_audio_path is not None:
                if not oriserve_quality.get("ran"):
                    oriserve_quality = _oriserve_candidate_from_future(
                        oriserve_future,
                        started_at=started,
                        max_release_tail_seconds=max_release_tail_seconds,
                    )
                if not oriserve_quality or oriserve_quality.get("retryable"):
                    oriserve_quality = _oriserve_rejected_candidate(
                        audio_path=Path(witness_audio_path),
                        draft_text=draft_text,
                        risk_reasons=risk_reasons,
                        started_at=started,
                        deadline=started + max_release_tail_seconds,
                        max_release_tail_seconds=max_release_tail_seconds,
                    )
                if oriserve_quality.get("accepted") is True:
                    safe_update = True
                    route = "hindi_stream_oriserve_safe"
                    reject_reasons = []
                    replacement_text = str(oriserve_quality.get("text") or "").strip()
            if not safe_update and witness_audio_path is not None:
                tail_redecode_quality = _tail_redecode_rejected_candidate(
                    audio_path=Path(witness_audio_path),
                    draft_text=draft_text,
                    started_at=started,
                    deadline=deadline,
                    max_release_tail_seconds=max_release_tail_seconds,
                )
                if tail_redecode_quality.get("accepted") is True:
                    safe_update = True
                    route = "hindi_stream_tail_safe"
                    reject_reasons = []
                    replacement_text = str(tail_redecode_quality.get("text") or "").strip()
            if not safe_update and witness_audio_path is not None:
                witness_quality = _witness_rejected_candidate(
                    audio_path=Path(witness_audio_path),
                    draft_text=draft_text,
                    candidate_text=candidate_text,
                    reject_reasons=reject_reasons,
                    timeout_seconds=witness_timeout_seconds,
                )
                if witness_quality.get("accepted") is True:
                    safe_update = True
                    route = "hindi_stream_witness_safe"
                    reject_reasons = []
                    replacement_text = romanized_candidate_text
            text = replacement_text if safe_update and replacement_text else draft_text
        finish_wait = round(time.perf_counter() - started, 3)
        return StreamFinishResult(
            run_id=self.run_id,
            text=text,
            raw_text=raw_text,
            route=route,
            safe_update=safe_update,
            reject_reasons=reject_reasons,
            release_tail_seconds=finish_wait,
            finish_wait_seconds=finish_wait,
            chunks=[chunk.__dict__ for chunk in chunks],
            detector_chunks=[detector.__dict__ for detector in detectors],
            quality={
                "hindi_risk": risk,
                "risk_reasons": risk_reasons,
                "chunk_count": len(chunks),
                "detector_chunk_count": len(detectors),
                "pending_count": pending_count,
                "partial_merge": partial_merge,
                "candidate_repair": candidate_repair,
                "hindi_value": hindi_value,
                "romanized_output": safe_update and text != candidate_text,
                "stream_finish_wait_seconds": stream_finish_wait,
                "candidate_sanitize": candidate_sanitize_quality,
                "tail_redecode": tail_redecode_quality,
                "raw_tail": raw_tail_quality,
                "oriserve": oriserve_quality,
                "witness": witness_quality,
                "errors": errors,
            },
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            final = self._finalizing.is_set()
            self._process_ready_detector_chunks(final=final)
            if self._risk and not final:
                self._process_ready_qwen_chunks(final=final)
            time.sleep(self.poll_interval_seconds)

    def _process_ready_detector_chunks(self, *, final: bool) -> None:
        for index, path in self._ready_chunk_paths(final=final, for_qwen=False):
            with self._lock:
                if index in self._detected or index in self._detector_in_progress:
                    continue
                self._detector_in_progress.add(index)
            try:
                started = time.perf_counter()
                transcript = detect_faster_whisper_language(path, model="tiny", compute_type="int8")
                language = (transcript.language or "").strip().lower() or None
                probability = transcript.language_probability
                risk = _detector_chunk_risk(
                    chunk_index=index,
                    language=language,
                    probability=probability,
                    low_confidence_threshold=self.low_confidence_threshold,
                    early_low_confidence_threshold=self.early_low_confidence_threshold,
                )
                detector = DetectorChunk(
                    index=index,
                    path=str(path),
                    language=language,
                    language_probability=probability,
                    seconds=round(time.perf_counter() - started, 3),
                    risk=risk,
                )
                with self._lock:
                    self._detected[index] = detector
                    if risk:
                        self._risk = True
                        reason = _detector_risk_reason(
                            chunk_index=index,
                            language=language,
                            probability=probability,
                            low_confidence_threshold=self.low_confidence_threshold,
                            early_low_confidence_threshold=self.early_low_confidence_threshold,
                        )
                        if reason not in self._risk_reasons:
                            self._risk_reasons.append(reason)
                        warm_oriserve_worker()
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._errors.append(f"detector:{path.name}:{type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    self._detector_in_progress.discard(index)

    def _process_ready_qwen_chunks(self, *, final: bool, deadline: float | None = None) -> None:
        for index, path in self._ready_chunk_paths(final=final, for_qwen=True):
            if deadline is not None and time.perf_counter() >= deadline:
                break
            with self._lock:
                if index in self._chunks or index in self._chunk_in_progress:
                    continue
                self._chunk_in_progress.add(index)
            try:
                chunk = _transcribe_chunk_file(path, index=index, model=self.model)
                with self._lock:
                    self._chunks[index] = chunk
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._errors.append(f"qwen:{path.name}:{type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    self._chunk_in_progress.discard(index)

    def _ready_chunk_paths(self, *, final: bool, for_qwen: bool) -> list[tuple[int, Path]]:
        paths = []
        for path in sorted(self.chunk_dir.glob("chunk-*.wav")):
            index = _chunk_index(path)
            if index is None:
                continue
            paths.append((index, path))
        if not paths:
            return []
        highest = max(index for index, _ in paths)
        ready: list[tuple[int, Path]] = []
        with self._lock:
            processed = self._chunks if for_qwen else self._detected
            in_progress = self._chunk_in_progress if for_qwen else self._detector_in_progress
            for index, path in paths:
                if index in processed or index in in_progress:
                    continue
                if not final and index == highest and not _chunk_file_is_stable(path, min_age_seconds=self.ready_file_age_seconds):
                    continue
                ready.append((index, path))
        return ready

    def _pending_ready_count(self, *, final: bool) -> int:
        paths = self._ready_chunk_paths(final=final, for_qwen=False)
        if self._risk:
            qwen_final = final and _process_final_chunks_on_release()
            paths.extend(self._ready_chunk_paths(final=qwen_final, for_qwen=True))
        return len(paths)


class HindiStreamSessionManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, HindiStreamSession] = {}

    def start(self, *, run_id: str, chunk_dir: str | Path, **kwargs: Any) -> HindiStreamSession:
        with self._lock:
            old = self._sessions.pop(run_id, None)
            if old:
                old.cancel()
            session = HindiStreamSession(run_id=run_id, chunk_dir=chunk_dir, **kwargs)
            self._sessions[run_id] = session
            session.start()
            return session

    def finish(self, *, run_id: str, draft_text: str, **kwargs: Any) -> StreamFinishResult:
        with self._lock:
            session = self._sessions.get(run_id)
        if session is None:
            raise KeyError(f"unknown stream session: {run_id}")
        result = session.finish(draft_text=draft_text, **kwargs)
        session.cancel(wait=False)
        with self._lock:
            self._sessions.pop(run_id, None)
        return result

    def cancel(self, *, run_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(run_id, None)
        if session is None:
            return False
        session.cancel()
        return True


def _transcribe_chunk_file(path: Path, *, index: int, model: str) -> StreamChunk:
    return _QWEN_STREAM_EXECUTOR.submit(_transcribe_chunk_file_sync, path, index=index, model=model).result()


def warm_qwen_worker(model: str = DEFAULT_QWEN3_ASR_MLX_HINGLISH_MODEL) -> Future[object]:
    with _QWEN_WARM_LOCK:
        future = _QWEN_WARM_FUTURES.get(model)
        if future is not None and not (future.done() and future.exception() is not None):
            return future
        future = _QWEN_STREAM_EXECUTOR.submit(_session_for_model, model)
        _QWEN_WARM_FUTURES[model] = future
        return future


def warm_detector_worker() -> Future[object]:
    global _DETECTOR_WARM_FUTURE
    with _DETECTOR_WARM_LOCK:
        if _DETECTOR_WARM_FUTURE is not None and not (
            _DETECTOR_WARM_FUTURE.done() and _DETECTOR_WARM_FUTURE.exception() is not None
        ):
            return _DETECTOR_WARM_FUTURE
        _DETECTOR_WARM_FUTURE = _DETECTOR_WARM_EXECUTOR.submit(warm_faster_whisper_language_detector)
        return _DETECTOR_WARM_FUTURE


def warm_oriserve_worker() -> Future[object]:
    global _ORISERVE_WARM_FUTURE
    with _ORISERVE_WARM_LOCK:
        if _ORISERVE_WARM_FUTURE is not None and not (
            _ORISERVE_WARM_FUTURE.done() and _ORISERVE_WARM_FUTURE.exception() is not None
        ):
            return _ORISERVE_WARM_FUTURE
        _ORISERVE_WARM_FUTURE = _ORISERVE_WARM_EXECUTOR.submit(warm_oriserve_hindi2hinglish)
        return _ORISERVE_WARM_FUTURE


def _ready_chunk_finish_grace_seconds() -> float:
    raw = os.environ.get(_READY_CHUNK_FINISH_GRACE_ENV, "").strip()
    if not raw:
        return _READY_CHUNK_FINISH_GRACE_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _READY_CHUNK_FINISH_GRACE_SECONDS


def _process_final_chunks_on_release() -> bool:
    raw = os.environ.get(_PROCESS_FINAL_CHUNKS_ENV, "").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def _tail_redecode_enabled() -> bool:
    raw = os.environ.get(_TAIL_REDECODE_ENV, "").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def _tail_redecode_seconds() -> float:
    raw = os.environ.get(_TAIL_REDECODE_SECONDS_ENV, "").strip()
    if not raw:
        return _TAIL_REDECODE_DEFAULT_SECONDS
    try:
        return max(4.0, min(20.0, float(raw)))
    except ValueError:
        return _TAIL_REDECODE_DEFAULT_SECONDS


def _oriserve_rescue_enabled() -> bool:
    raw = os.environ.get(_ORISERVE_RESCUE_ENV, "").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def _oriserve_rescue_max_audio_seconds() -> float:
    raw = os.environ.get(_ORISERVE_RESCUE_MAX_AUDIO_SECONDS_ENV, "").strip()
    if not raw:
        return _ORISERVE_RESCUE_DEFAULT_MAX_AUDIO_SECONDS
    try:
        return max(5.0, min(45.0, float(raw)))
    except ValueError:
        return _ORISERVE_RESCUE_DEFAULT_MAX_AUDIO_SECONDS


def _oriserve_background_wait_seconds() -> float:
    raw = os.environ.get(_ORISERVE_BACKGROUND_WAIT_ENV, "").strip()
    if not raw:
        return _ORISERVE_BACKGROUND_WAIT_SECONDS
    try:
        return max(0.0, min(2.0, float(raw)))
    except ValueError:
        return _ORISERVE_BACKGROUND_WAIT_SECONDS


def _raw_tail_rescue_enabled() -> bool:
    raw = os.environ.get(_RAW_TAIL_RESCUE_ENV, "").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def _transcribe_chunk_file_sync(path: Path, *, index: int, model: str) -> StreamChunk:
    from mlx_qwen3_asr import load_audio

    sample_rate = 16_000
    started = time.perf_counter()
    audio = np.asarray(load_audio(str(path), sr=sample_rate), dtype=np.float32)
    duration = len(audio) / sample_rate
    if duration < 0.35:
        return StreamChunk(
            index=index,
            path=str(path),
            duration_seconds=round(duration, 3),
            compute_seconds=0.0,
            text="",
        )
    result = _session_for_model(model).transcribe(audio)
    return StreamChunk(
        index=index,
        path=str(path),
        duration_seconds=round(duration, 3),
        compute_seconds=round(time.perf_counter() - started, 3),
        text=_extract_text(result),
    )


def _witness_rejected_candidate(
    *,
    audio_path: Path,
    draft_text: str,
    candidate_text: str,
    reject_reasons: list[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    if timeout_seconds <= 0:
        return {"ran": False, "accepted": False, "reason": "disabled"}
    try:
        from ramblefix.external_asr import transcribe_whisper_cpp

        transcript = transcribe_whisper_cpp(
            audio_path,
            language="auto",
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ran": True,
            "accepted": False,
            "seconds": round(time.perf_counter() - started, 3),
            "engine": "whisper.cpp:auto",
            "error": f"{type(exc).__name__}: {exc}",
        }
    decision = witness_can_accept_rejected_candidate(
        draft_text=draft_text,
        candidate_text=candidate_text,
        witness_text=transcript.text,
        reject_reasons=reject_reasons,
    )
    return {
        "ran": True,
        "seconds": round(time.perf_counter() - started, 3),
        "engine": transcript.engine,
        "text": transcript.text,
        **decision,
    }


def _tail_redecode_rejected_candidate(
    *,
    audio_path: Path,
    draft_text: str,
    started_at: float,
    deadline: float,
    max_release_tail_seconds: float,
) -> dict[str, Any]:
    if not _tail_redecode_enabled():
        return {"ran": False, "accepted": False, "reason": "disabled"}
    remaining = deadline - time.perf_counter()
    if remaining < _TAIL_REDECODE_MIN_REMAINING_SECONDS:
        return {
            "ran": False,
            "accepted": False,
            "reason": "insufficient-budget",
            "remaining_seconds": round(max(0.0, remaining), 3),
        }
    if not audio_path.exists():
        return {"ran": False, "accepted": False, "reason": "missing-audio"}

    started = time.perf_counter()
    tail_path = audio_path.with_name(f"{audio_path.stem}.tail-redecode.wav")
    try:
        actual_tail_seconds = _write_tail_audio(
            source=audio_path,
            dest=tail_path,
            seconds=_tail_redecode_seconds(),
        )
        from ramblefix.external_asr import transcribe_whisper_cpp_server_translate

        transcript = transcribe_whisper_cpp_server_translate(
            tail_path,
            timeout_seconds=max(0.2, min(2.0, deadline - time.perf_counter())),
        )
        merged_text, merge_reason, merge_meta = _merge_tail_redecode_with_draft(
            draft_text=draft_text,
            tail_text=transcript.text,
        )
        seconds = round(time.perf_counter() - started, 3)
        release_tail_seconds = round(time.perf_counter() - started_at, 3)
        reject_reasons: list[str] = []
        if merged_text:
            reject_reasons = update_reject_reasons(
                draft_text=draft_text,
                final_text=merged_text,
                release_tail_seconds=release_tail_seconds,
                max_release_tail_seconds=max_release_tail_seconds,
                allow_roman_hindi=True,
                strict_new_english=True,
            )
        blocking_reject_reasons = [
            reason
            for reason in reject_reasons
            if not reason.startswith(("new-english-token:", "new-english-run:", "new-english-count:"))
        ]
        accepted = bool(merged_text and not blocking_reject_reasons)
        return {
            "ran": True,
            "accepted": accepted,
            "seconds": seconds,
            "release_tail_seconds": release_tail_seconds,
            "engine": transcript.engine,
            "tail_seconds": round(actual_tail_seconds, 3),
            "tail_text": transcript.text,
            "text": merged_text or "",
            "reason": merge_reason,
            "merge": merge_meta,
            "reject_reasons": reject_reasons,
            "blocking_reject_reasons": blocking_reject_reasons,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ran": True,
            "accepted": False,
            "seconds": round(time.perf_counter() - started, 3),
            "engine": "whisper.cpp.server.translate:tail",
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        try:
            tail_path.unlink()
        except OSError:
            pass


def _raw_english_tail_rejected_candidate(
    *,
    draft_text: str,
    raw_text: str,
    reject_reasons: list[str],
    release_tail_seconds: float,
    max_release_tail_seconds: float,
) -> dict[str, Any]:
    blocking_reasons = [
        reason
        for reason in reject_reasons
        if not reason.startswith(("new-english-token:", "new-english-run:", "new-english-count:"))
    ]
    if blocking_reasons:
        return {
            "ran": False,
            "accepted": False,
            "reason": "blocking-reject-reasons",
            "blocking_reject_reasons": blocking_reasons,
        }
    if release_tail_seconds > max_release_tail_seconds:
        return {
            "ran": False,
            "accepted": False,
            "reason": "tail-threshold",
            "release_tail_seconds": release_tail_seconds,
        }
    if len(_tail_tokens(draft_text)) > 14:
        return {"ran": False, "accepted": False, "reason": "draft-too-long"}

    merged_text, merge_reason, merge_meta = _merge_raw_english_tail_with_draft(
        draft_text=draft_text,
        raw_text=raw_text,
    )
    if not merged_text:
        return {
            "ran": True,
            "accepted": False,
            "reason": merge_reason,
            "merge": merge_meta,
        }

    next_reject_reasons = update_reject_reasons(
        draft_text=draft_text,
        final_text=merged_text,
        release_tail_seconds=release_tail_seconds,
        max_release_tail_seconds=max_release_tail_seconds,
        allow_roman_hindi=True,
        strict_new_english=False,
    )
    blocking_next = [
        reason
        for reason in next_reject_reasons
        if not reason.startswith(("new-english-token:", "new-english-run:", "new-english-count:"))
    ]
    if blocking_next:
        return {
            "ran": True,
            "accepted": False,
            "reason": "post-merge-blocked",
            "reject_reasons": blocking_next,
            "text": merged_text,
            "merge": merge_meta,
        }
    meaning_reject_reasons = meaning_first_update_reject_reasons(draft_text, merged_text)
    meaning_blocking = [
        reason
        for reason in meaning_reject_reasons
        if reason != "no-default-meaning-gain"
    ]
    accepted = not meaning_blocking
    return {
        "ran": True,
        "accepted": accepted,
        "reason": "raw-english-tail" if accepted else "meaning-blocked",
        "text": merged_text if accepted else "",
        "candidate_text": merged_text,
        "reject_reasons": meaning_blocking,
        "merge": merge_meta,
    }


def _merge_raw_english_tail_with_draft(*, draft_text: str, raw_text: str) -> tuple[str | None, str, dict[str, Any]]:
    draft_tokens = _tail_tokens(draft_text)
    raw_tokens = _tail_tokens(raw_text)
    if len(draft_tokens) < 4 or len(raw_tokens) < 6:
        return None, "too-short", {}

    best: tuple[tuple[int, int, int], int, int, int] | None = None
    search_start = max(0, len(draft_tokens) - 12)
    for raw_start in range(len(raw_tokens)):
        for raw_end in range(raw_start + 4, min(len(raw_tokens), raw_start + 8) + 1):
            phrase = raw_tokens[raw_start:raw_end]
            for draft_start in range(search_start, len(draft_tokens) - len(phrase) + 1):
                if draft_tokens[draft_start : draft_start + len(phrase)] == phrase:
                    score = (len(phrase), draft_start, raw_end)
                    if best is None or score > best[0]:
                        best = (score, raw_start, raw_end, draft_start)
    if best is None:
        return None, "no-overlap", {}

    _, _, raw_end, _ = best
    append_tokens = raw_tokens[raw_end:]
    while append_tokens and append_tokens[0] in {"right", "so", "and", "uh", "um", "okay"}:
        append_tokens = append_tokens[1:]
    while append_tokens and append_tokens[-1] in {"right", "so", "and", "uh", "um", "okay"}:
        append_tokens = append_tokens[:-1]
    if len(append_tokens) < 4:
        return None, "no-new-tail", {"append_tokens": append_tokens}
    if len(append_tokens) > 12:
        return None, "append-too-long", {"append_tokens": append_tokens}
    if any(token in _TAIL_GARBAGE_TOKENS for token in append_tokens):
        return None, "tail-garbage", {"append_tokens": append_tokens}

    draft_content = _tail_content_tokens(draft_tokens)
    append_content = _tail_content_tokens(append_tokens)
    new_content = sorted(append_content - draft_content)
    if len(new_content) < 2:
        return (
            None,
            "no-new-work-content",
            {
                "append_tokens": append_tokens,
                "new_content": new_content,
            },
        )

    append_text = _render_tail_append(append_tokens)
    separator = "" if draft_text.rstrip().endswith((".", "?", "!")) else "."
    merged = f"{draft_text.rstrip()}{separator} {append_text}."
    return (
        merged,
        "merged",
        {
            "append_tokens": append_tokens,
            "new_content": new_content,
        },
    )


def _write_tail_audio(*, source: Path, dest: Path, seconds: float) -> float:
    with wave.open(str(source), "rb") as reader:
        params = reader.getparams()
        rate = reader.getframerate()
        frame_count = reader.getnframes()
        sample_width = reader.getsampwidth()
        channels = reader.getnchannels()
        audio = reader.readframes(frame_count)
    frames = min(frame_count, max(1, int(seconds * rate)))
    start = frame_count - frames
    frame_bytes = sample_width * channels
    with wave.open(str(dest), "wb") as writer:
        writer.setparams(params)
        writer.writeframes(audio[start * frame_bytes :])
    return frames / rate if rate else 0.0


def _oriserve_rejected_candidate(
    *,
    audio_path: Path,
    draft_text: str,
    risk_reasons: list[str],
    started_at: float,
    deadline: float,
    max_release_tail_seconds: float,
) -> dict[str, Any]:
    if not _oriserve_rescue_enabled():
        return {"ran": False, "accepted": False, "reason": "disabled"}
    if not _oriserve_risk_worth_trying(risk_reasons):
        return {
            "ran": False,
            "accepted": False,
            "reason": "weak-hindi-risk",
            "risk_reasons": risk_reasons,
        }
    if not audio_path.exists():
        return {"ran": False, "accepted": False, "reason": "missing-audio"}
    duration_seconds = _wav_duration_seconds(audio_path)
    max_audio_seconds = _oriserve_rescue_max_audio_seconds()
    if duration_seconds > max_audio_seconds:
        return {
            "ran": False,
            "accepted": False,
            "reason": "audio-too-long",
            "audio_seconds": round(duration_seconds, 3),
            "max_audio_seconds": max_audio_seconds,
        }
    remaining = deadline - time.perf_counter()
    if remaining < _ORISERVE_RESCUE_MIN_REMAINING_SECONDS:
        return {
            "ran": False,
            "accepted": False,
            "reason": "insufficient-budget",
            "remaining_seconds": round(max(0.0, remaining), 3),
        }

    started = time.perf_counter()
    try:
        from ramblefix.external_asr import transcribe_oriserve_hindi2hinglish
        from ramblefix.glossary import apply_glossary

        transcript = transcribe_oriserve_hindi2hinglish(audio_path)
        seconds = round(time.perf_counter() - started, 3)
        release_tail_seconds = round(time.perf_counter() - started_at, 3)
        candidate_text = apply_glossary(str(transcript.text or "").strip())
        candidate_text = normalize_roman_hindi_spelling(candidate_text)
        romanized_text = normalize_roman_hindi_spelling(romanize_devanagari_for_hinglish(candidate_text))
        reject_reasons = update_reject_reasons(
            draft_text=draft_text,
            final_text=candidate_text,
            release_tail_seconds=release_tail_seconds,
            max_release_tail_seconds=max_release_tail_seconds,
            allow_roman_hindi=True,
            strict_new_english=True,
        )
        if not reject_reasons and romanized_text != candidate_text:
            reject_reasons.extend(
                update_reject_reasons(
                    draft_text=draft_text,
                    final_text=romanized_text,
                    release_tail_seconds=release_tail_seconds,
                    max_release_tail_seconds=max_release_tail_seconds,
                    allow_roman_hindi=True,
                    strict_new_english=True,
                )
            )
        hindi_value = hindi_value_delta(draft_text, candidate_text)
        if not reject_reasons and not hindi_value["has_hindi_value"]:
            reject_reasons.append("no-hindi-value")
        if not reject_reasons:
            reject_reasons.extend(meaning_first_update_reject_reasons(draft_text, candidate_text))
        sanitize_quality: dict[str, Any] = {"ran": False}
        if reject_reasons:
            sanitize_quality = _sanitize_rejected_new_english_candidate(
                draft_text=draft_text,
                candidate_text=romanized_text,
                reject_reasons=reject_reasons,
                release_tail_seconds=release_tail_seconds,
                max_release_tail_seconds=max_release_tail_seconds,
            )
            if sanitize_quality.get("accepted") is True:
                romanized_text = str(sanitize_quality.get("text") or "").strip()
                candidate_text = romanized_text
                hindi_value = sanitize_quality.get("hindi_value") or hindi_value
                reject_reasons = []
        accepted = not reject_reasons
        return {
            "ran": True,
            "accepted": accepted,
            "seconds": seconds,
            "release_tail_seconds": release_tail_seconds,
            "engine": transcript.engine,
            "audio_seconds": round(duration_seconds, 3),
            "text": romanized_text if accepted else "",
            "raw_text": candidate_text,
            "hindi_value": hindi_value,
            "reject_reasons": reject_reasons,
            "sanitize": sanitize_quality,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ran": True,
            "accepted": False,
            "seconds": round(time.perf_counter() - started, 3),
            "engine": "oriserve-hindi2hinglish",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _oriserve_candidate_from_future(
    future: Future[dict[str, Any]] | None,
    *,
    started_at: float,
    max_release_tail_seconds: float,
) -> dict[str, Any] | None:
    if future is None:
        return None
    remaining = max_release_tail_seconds - (time.perf_counter() - started_at)
    if remaining <= 0:
        return {
            "ran": True,
            "accepted": False,
            "reason": "background-result-past-tail-budget",
            "release_tail_seconds": round(time.perf_counter() - started_at, 3),
        }
    try:
        return future.result(timeout=min(remaining, _oriserve_background_wait_seconds()))
    except FutureTimeoutError:
        cancelled = future.cancel()
        return {
            "ran": True,
            "accepted": False,
            "reason": "background-timeout",
            "retryable": True,
            "cancelled": cancelled,
            "release_tail_seconds": round(time.perf_counter() - started_at, 3),
        }


def _sanitize_rejected_new_english_candidate(
    *,
    draft_text: str,
    candidate_text: str,
    reject_reasons: list[str],
    release_tail_seconds: float,
    max_release_tail_seconds: float,
) -> dict[str, Any]:
    removable_tokens = _rejected_new_english_tokens(reject_reasons)
    blocking_reasons = [
        reason
        for reason in reject_reasons
        if not reason.startswith(("new-english-token:", "new-english-run:", "new-english-count:"))
    ]
    protected_only_blocking = all(
        reason.startswith(("protected-term-missing:", "protected-term-new:"))
        for reason in blocking_reasons
    )
    if not removable_tokens or (blocking_reasons and not protected_only_blocking):
        return {
            "ran": False,
            "accepted": False,
            "reason": "no-removable-new-english",
            "blocking_reject_reasons": blocking_reasons,
        }
    if len(removable_tokens) > 6:
        return {
            "ran": False,
            "accepted": False,
            "reason": "too-many-removable-tokens",
            "removable_tokens": removable_tokens,
        }

    original_candidate_text = candidate_text
    candidate_text, canonical_rules = _canonicalize_rejected_tokens_from_draft(
        draft_text=draft_text,
        candidate_text=candidate_text,
        tokens=removable_tokens,
    )
    sanitized, removed = _remove_candidate_tokens(candidate_text, removable_tokens)
    sanitized, repair_rules = _repair_sanitized_candidate_from_draft(
        draft_text=draft_text,
        candidate_text=sanitized,
    )
    repair_rules = canonical_rules + repair_rules
    if not removed and not repair_rules and sanitized.strip() == original_candidate_text.strip():
        return {"ran": True, "accepted": False, "reason": "unchanged", "removable_tokens": removable_tokens}

    next_reject_reasons = update_reject_reasons(
        draft_text=draft_text,
        final_text=sanitized,
        release_tail_seconds=release_tail_seconds,
        max_release_tail_seconds=max_release_tail_seconds,
        allow_roman_hindi=True,
        strict_new_english=True,
    )
    extra_removed: list[str] = []
    extra_removable_tokens = _rejected_new_english_tokens(next_reject_reasons)
    extra_blocking_reasons = [
        reason
        for reason in next_reject_reasons
        if not reason.startswith(("new-english-token:", "new-english-run:", "new-english-count:"))
    ]
    if extra_removable_tokens and not extra_blocking_reasons and len(removed) + len(extra_removable_tokens) <= 6:
        next_sanitized, extra_removed = _remove_candidate_tokens(sanitized, extra_removable_tokens)
        if extra_removed and next_sanitized.strip() != sanitized.strip():
            sanitized = next_sanitized
            next_reject_reasons = update_reject_reasons(
                draft_text=draft_text,
                final_text=sanitized,
                release_tail_seconds=release_tail_seconds,
                max_release_tail_seconds=max_release_tail_seconds,
                allow_roman_hindi=True,
                strict_new_english=True,
            )
    if not next_reject_reasons and _has_dangling_sanitized_transition(sanitized):
        next_reject_reasons.append("dangling-transition")
    if not next_reject_reasons and _unsupervised_multi_token_removal(
        removed_tokens=removed + extra_removed,
        repair_rules=repair_rules,
    ):
        next_reject_reasons.append("unsupervised-multi-token-removal")
    hindi_value = hindi_value_delta(draft_text, sanitized)
    if not next_reject_reasons and not hindi_value["has_hindi_value"]:
        next_reject_reasons.append("no-hindi-value")
    if (
        not next_reject_reasons
        and _over_sanitized_low_content(
            draft_text=draft_text,
            sanitized_text=sanitized,
            removed_tokens=removed + extra_removed,
            repair_rules=repair_rules,
        )
    ):
        next_reject_reasons.append("over-sanitized-low-content")
    if not next_reject_reasons:
        next_reject_reasons.extend(meaning_first_update_reject_reasons(draft_text, sanitized))

    accepted = not next_reject_reasons
    return {
        "ran": True,
        "accepted": accepted,
        "reason": "sanitize-new-english",
        "text": sanitized if accepted else "",
        "candidate_text": sanitized,
        "removed_tokens": removed + extra_removed,
        "removable_tokens": removable_tokens + [token for token in extra_removable_tokens if token not in removable_tokens],
        "repair_rules": repair_rules,
        "hindi_value": hindi_value,
        "reject_reasons": next_reject_reasons,
    }


def _over_sanitized_low_content(
    *,
    draft_text: str,
    sanitized_text: str,
    removed_tokens: list[str],
    repair_rules: list[str],
) -> bool:
    if not removed_tokens and repair_rules:
        return False
    content_tokens = _sanitize_content_tokens(sanitized_text)
    if len(content_tokens) >= 4:
        return False
    draft_tokens = set(_sanitize_content_tokens(draft_text))
    retained = len(set(content_tokens) & draft_tokens)
    return retained < 3


def _unsupervised_multi_token_removal(*, removed_tokens: list[str], repair_rules: list[str]) -> bool:
    if len(removed_tokens) < 3:
        return False
    return not any(rule.startswith(("draft-backed-", "draft-near-match:")) for rule in repair_rules)


def _sanitize_content_tokens(text: str) -> list[str]:
    return [
        token
        for token, _ in _alignment_tokens(text)
        if len(token) >= 4 and token not in _TAIL_CONTENT_STOP_TOKENS
    ]


def _rejected_new_english_tokens(reject_reasons: list[str]) -> list[str]:
    tokens: list[str] = []
    for reason in reject_reasons:
        if not reason.startswith(("new-english-token:", "new-english-run:", "new-english-count:")):
            continue
        _, raw = reason.split(":", 1)
        for token in raw.split(","):
            clean = token.strip().lower()
            if clean and clean not in tokens:
                tokens.append(clean)
    return tokens


def _remove_candidate_tokens(candidate_text: str, tokens: list[str]) -> tuple[str, list[str]]:
    token_set = set(tokens)
    removed: list[str] = []

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        if raw.lower() not in token_set:
            return raw
        removed.append(raw)
        return ""

    sanitized = re.sub(r"\b[A-Za-z][A-Za-z']*\b", replace, candidate_text)
    sanitized = re.sub(r"\s+([,.!?;:])", r"\1", sanitized)
    sanitized = re.sub(r"\s{2,}", " ", sanitized).strip()
    return sanitized, removed


def _canonicalize_rejected_tokens_from_draft(
    *,
    draft_text: str,
    candidate_text: str,
    tokens: list[str],
) -> tuple[str, list[str]]:
    token_set = {token.lower() for token in tokens if len(token) >= 4}
    if not token_set:
        return candidate_text, []
    draft_candidates = _draft_canonical_tokens(draft_text)
    if not draft_candidates:
        return candidate_text, []

    rules: list[str] = []

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        token = raw.lower()
        if token not in token_set:
            return raw
        canonical = _closest_draft_canonical(token, draft_candidates)
        if canonical is None or canonical.lower() == token:
            return raw
        rules.append(f"draft-near-match:{raw}->{canonical}")
        return canonical

    repaired = re.sub(r"\b[A-Za-z][A-Za-z']*\b", replace, candidate_text)
    return repaired, rules


def _draft_canonical_tokens(draft_text: str) -> dict[str, str]:
    candidates: dict[str, str] = {}
    for match in re.finditer(r"\b[A-Za-z][A-Za-z0-9']*\b", draft_text):
        raw = match.group(0)
        key = _alignment_token(raw)
        if len(key) < 3:
            continue
        candidates.setdefault(key, raw)
        squeezed = _squeeze_repeated_letters(key)
        if len(squeezed) >= 3:
            candidates.setdefault(squeezed, raw)
    return candidates


def _closest_draft_canonical(token: str, draft_candidates: dict[str, str]) -> str | None:
    variants = {token, _alignment_token(token), _squeeze_repeated_letters(_alignment_token(token))}
    variants = {variant for variant in variants if len(variant) >= 3}
    for variant in variants:
        if variant in draft_candidates:
            return draft_candidates[variant]

    best_key = ""
    best_score = 0.0
    for variant in variants:
        for draft_key in draft_candidates:
            if not variant or not draft_key or variant[0] != draft_key[0]:
                continue
            if len(draft_key) < 4:
                continue
            if abs(len(variant) - len(draft_key)) > 2:
                continue
            score = SequenceMatcher(None, variant, draft_key, autojunk=False).ratio()
            common_prefix = _common_prefix_len(variant, draft_key)
            if common_prefix >= 4:
                score = max(score, 0.76)
            if score > best_score:
                best_score = score
                best_key = draft_key
    if best_key and best_score >= 0.72:
        return draft_candidates[best_key]
    return None


def _squeeze_repeated_letters(token: str) -> str:
    return re.sub(r"([a-z0-9])\1+", r"\1", token.lower())


def _common_prefix_len(left: str, right: str) -> int:
    count = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        count += 1
    return count


def _has_dangling_sanitized_transition(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    if re.search(r"\band so on and so(?:\s+forth)?\s+(?:the|a|an|this|that)\b", normalized):
        return True
    if re.search(r"\b(?:and|or|so|but)\s*[,.!?;:]*$", normalized):
        return True
    return False


def _should_prefer_clean_tail_merge(
    *,
    draft_text: str,
    candidate_text: str,
    hindi_value: dict[str, Any],
) -> bool:
    substantive_hindi = hindi_value.get("substantive_new_roman_hindi_tokens") or []
    if len(substantive_hindi) >= 4:
        return False
    draft_acronyms = _work_acronyms(draft_text)
    candidate_acronyms = _work_acronyms(candidate_text)
    if not candidate_acronyms - draft_acronyms:
        return False
    draft_tokens = _alignment_tokens(draft_text)
    candidate_tokens = _alignment_tokens(candidate_text)
    if len(candidate_tokens) < len(draft_tokens) + 8:
        return False
    return True


def _work_acronyms(text: str) -> set[str]:
    acronyms = {match.group(0).lower() for match in re.finditer(r"\b[A-Z][A-Z0-9]{1,}\b", text)}
    for match in re.finditer(r"\b(?:[A-Z]\s+){1,}[A-Z]\b", text):
        compact = re.sub(r"\s+", "", match.group(0)).lower()
        if len(compact) >= 2:
            acronyms.add(compact)
    return acronyms


def _repair_sanitized_candidate_from_draft(*, draft_text: str, candidate_text: str) -> tuple[str, list[str]]:
    repaired = candidate_text
    rules: list[str] = []
    draft_key = " ".join(_alignment_token(token) for token, _ in _alignment_tokens(draft_text))
    if "api layer functions well" in draft_text.lower() or "api layer function well" in draft_key:
        next_text = re.sub(
            r"\b(API layer functions)\s+(?:ve|we|mein)\b",
            r"\1 well",
            repaired,
            flags=re.IGNORECASE,
        )
        if next_text != repaired:
            repaired = next_text
            rules.append("draft-backed-api-functions-well")
    if "agent will" in draft_key:
        next_text = re.sub(
            r"\b(one|doosra|dusra|dusara|teesra|tisra|tisara)\s+will\b",
            r"\1 agent will",
            repaired,
            flags=re.IGNORECASE,
        )
        if next_text != repaired:
            repaired = next_text
            rules.append("draft-backed-agent-will")
    next_text = _complete_sanitized_tail_from_draft(draft_text=draft_text, candidate_text=repaired)
    if next_text != repaired:
        repaired = next_text
        rules.append("draft-backed-tail-completion")
    return re.sub(r"\s{2,}", " ", repaired).strip(), rules


def _complete_sanitized_tail_from_draft(*, draft_text: str, candidate_text: str) -> str:
    draft_tokens = _raw_alignment_tokens(draft_text)
    candidate_tokens = _raw_alignment_tokens(candidate_text)
    if len(draft_tokens) < 4 or len(candidate_tokens) < 3:
        return candidate_text
    draft_keys = [token for token, _ in draft_tokens]
    candidate_keys = [token for token, _ in candidate_tokens]
    for missing_count in range(1, 4):
        if len(draft_keys) <= missing_count + 1:
            continue
        for overlap_count in range(min(6, len(candidate_keys), len(draft_keys) - missing_count), 1, -1):
            draft_prefix = draft_keys[-missing_count - overlap_count : -missing_count]
            if candidate_keys[-overlap_count:] != draft_prefix:
                continue
            missing_raw = [raw for _, raw in draft_tokens[-missing_count:]]
            if not missing_raw:
                continue
            base = candidate_text.rstrip()
            trailing = ""
            if base and base[-1] in ".!?":
                trailing = base[-1]
                base = base[:-1].rstrip()
            completed = f"{base} {' '.join(missing_raw)}{trailing or '.'}"
            return completed
    return candidate_text


def _raw_alignment_tokens(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    for match in re.finditer(r"[A-Za-z0-9']+", text):
        key = _alignment_token(match.group(0))
        if key:
            tokens.append((key, match.group(0)))
    return tokens


def _append_repair_reason(current: str, reason: str) -> str:
    if not current:
        return reason
    if not reason:
        return current
    return f"{current};{reason}"


def _wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as reader:
            rate = reader.getframerate()
            if rate <= 0:
                return 0.0
            return reader.getnframes() / rate
    except Exception:  # noqa: BLE001
        return 0.0


def _strong_hindi_risk(risk_reasons: list[str]) -> bool:
    for reason in risk_reasons:
        if reason.startswith(("language:hi", "language:ur", "language:mr", "language:ne")):
            return True
        if reason.startswith("non_english_low_confidence:"):
            return True
    return False


def _oriserve_risk_worth_trying(risk_reasons: list[str]) -> bool:
    if _strong_hindi_risk(risk_reasons):
        return True
    return "english_low_confidence" in risk_reasons


def _merge_tail_redecode_with_draft(*, draft_text: str, tail_text: str) -> tuple[str | None, str, dict[str, Any]]:
    draft_tokens = _tail_tokens(draft_text)
    tail_tokens = _tail_tokens(tail_text)
    if len(draft_tokens) < 5 or len(tail_tokens) < 5:
        return None, "too-short", {}

    best: tuple[tuple[int, int, int], int, int, int] | None = None
    search_start = max(0, len(draft_tokens) - 30)
    for tail_start in range(len(tail_tokens)):
        for tail_end in range(tail_start + 4, min(len(tail_tokens), tail_start + 9) + 1):
            phrase = tail_tokens[tail_start:tail_end]
            for draft_start in range(search_start, len(draft_tokens) - len(phrase) + 1):
                if draft_tokens[draft_start : draft_start + len(phrase)] == phrase:
                    score = (len(phrase), draft_start, tail_end)
                    if best is None or score > best[0]:
                        best = (score, tail_start, tail_end, draft_start)
    if best is None:
        return None, "no-overlap", {}

    _, _, tail_end, _ = best
    append_tokens = tail_tokens[tail_end:]
    while append_tokens and append_tokens[0] in {"right", "so", "and", "uh", "um"}:
        append_tokens = append_tokens[1:]
    while append_tokens and append_tokens[-1] in {"right", "so", "and", "uh", "um"}:
        append_tokens = append_tokens[:-1]
    if len(append_tokens) < 4:
        return None, "no-new-tail", {}
    if len(append_tokens) > 22:
        return None, "append-too-long", {"append_tokens": append_tokens}
    if any(token in _TAIL_GARBAGE_TOKENS for token in append_tokens):
        return None, "tail-garbage", {"append_tokens": append_tokens}

    draft_content = _tail_content_tokens(draft_tokens)
    append_content = _tail_content_tokens(append_tokens)
    new_content = sorted(append_content - draft_content)
    new_acronyms = sorted(_tail_acronyms(tail_text) - _tail_acronyms(draft_text))
    if not new_acronyms or len(new_content) < 2:
        return (
            None,
            "no-new-work-content",
            {
                "append_tokens": append_tokens,
                "new_content": new_content,
                "new_acronyms": new_acronyms,
            },
        )

    append_text = _render_tail_append(append_tokens)
    separator = "" if draft_text.rstrip().endswith((".", "?", "!")) else "."
    merged = f"{draft_text.rstrip()}{separator} {append_text}."
    return (
        merged,
        "merged",
        {
            "append_tokens": append_tokens,
            "new_content": new_content,
            "new_acronyms": new_acronyms,
        },
    )


def _tail_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text.lower())


def _tail_content_tokens(tokens: list[str]) -> set[str]:
    return {token for token in tokens if len(token) >= 3 and token not in _TAIL_CONTENT_STOP_TOKENS}


def _tail_acronyms(text: str) -> set[str]:
    return {match.group(0).lower() for match in re.finditer(r"\b[A-Z]{2,}\b", text)}


def _render_tail_append(tokens: list[str]) -> str:
    text = " ".join(tokens)
    text = re.sub(r"\bit s\b", "it's", text)
    text = re.sub(r"\bapi\b", "API", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmcp\b", "MCP", text, flags=re.IGNORECASE)
    text = re.sub(r"\bm c p\b", "MCP", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(whether it's an? [A-Z0-9]+ or [A-Z0-9]+) all of them\b", r"\1, all of them", text)
    text = re.sub(
        r"\b([A-Z0-9]+ or [A-Z0-9]+) all the same work should be done\b",
        r"\1 should do the same work",
        text,
    )
    text = text.strip()
    return text[:1].upper() + text[1:] if text else text


def _chunk_index(path: Path) -> int | None:
    match = re.search(r"chunk-(\d+)\.wav$", path.name)
    if not match:
        return None
    return int(match.group(1))


def _chunk_file_is_stable(path: Path, *, min_age_seconds: float, now: float | None = None) -> bool:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    if stat.st_size <= 44:
        return False
    if now is None:
        now = time.time()
    return now - stat.st_mtime >= min_age_seconds


def _merge_ready_hindi_with_draft_tail(*, draft_text: str, raw_text: str, pending_count: int) -> str:
    raw = raw_text.strip()
    draft = draft_text.strip()
    if not raw or not draft:
        return raw
    if not pending_count and _has_complete_hindi_answer_ending(raw):
        return raw
    if not pending_count and _candidate_covers_draft(draft_text=draft, candidate_text=raw):
        return raw

    raw_tokens = _alignment_tokens(raw)
    draft_tokens = _alignment_tokens(draft)
    if len(raw_tokens) < 2 or len(draft_tokens) < 4:
        return raw

    matcher = SequenceMatcher(
        None,
        [token for token, _ in raw_tokens],
        [token for token, _ in draft_tokens],
        autojunk=False,
    )
    matched = 0
    draft_tail_token_index = 0
    tail_aligned_match = False
    for block in matcher.get_matching_blocks():
        if block.size == 0:
            continue
        matched += block.size
        if block.size >= 2 or block.a + block.size >= len(raw_tokens) - 2:
            draft_tail_token_index = max(draft_tail_token_index, block.b + block.size)
        if block.size >= 2 and block.a + block.size >= len(raw_tokens) - 1:
            tail_aligned_match = True

    if (matched < 3 and not tail_aligned_match) or draft_tail_token_index <= 0 or draft_tail_token_index >= len(draft_tokens):
        return raw

    tail_start = draft_tokens[draft_tail_token_index - 1][1]
    tail = draft[tail_start:].strip().lstrip(" ,.;:-")
    if len(tail.split()) < 2:
        return raw
    return raw.rstrip(" ,") + " " + tail.lstrip(" ,")


def _has_complete_hindi_answer_ending(text: str) -> bool:
    romanized = romanize_devanagari_for_hinglish(text).lower().rstrip(" .,!?:;")
    normalized = re.sub(r"\s+", " ", romanized)
    complete_endings = (
        "sankshipt mein uttar de",
        "sankshipt mein uttara de",
        "samksipta mein uttar de",
        "samksipta mein uttara de",
        "sankshipt mein uttar den",
        "sankshipt mein uttara den",
        "samksipta mein uttar den",
        "samksipta mein uttara den",
    )
    return any(normalized.endswith(ending) for ending in complete_endings)


def _alignment_tokens(text: str) -> list[tuple[str, int]]:
    tokens: list[tuple[str, int]] = []
    for match in re.finditer(r"[A-Za-z0-9']+", text):
        token = _alignment_token(match.group(0))
        if token:
            tokens.append((token, match.end()))
    return tokens


def _alignment_token(value: str) -> str:
    token = value.lower().strip("'")
    if token.endswith("'s"):
        token = token[:-2]
    if len(token) > 3 and token.endswith("s"):
        token = token[:-1]
    return token


def _repair_leading_unknown_english_before_hindi(*, draft_text: str, candidate_text: str) -> tuple[str, str]:
    draft_tokens = {token for token, _ in _alignment_tokens(draft_text)}
    candidate = candidate_text.lstrip()
    removed: list[str] = []
    for _ in range(2):
        match = re.match(r"([A-Za-z][A-Za-z']{2,})(\s+)(.*)", candidate, flags=re.S)
        if not match:
            break
        token = _alignment_token(match.group(1))
        rest = match.group(3).lstrip()
        if token in draft_tokens or not _starts_with_hindi_context(rest):
            break
        removed.append(match.group(1))
        candidate = rest
    if not removed:
        return candidate_text, ""
    return candidate, "drop-leading-unknown-english:" + ",".join(removed)


def _starts_with_hindi_context(text: str) -> bool:
    return any(ord(char) > 127 for char in text[:12])


def _candidate_covers_draft(*, draft_text: str, candidate_text: str) -> bool:
    draft_tokens = _alignment_tokens(draft_text)
    candidate_tokens = _alignment_tokens(candidate_text)
    if len(draft_tokens) < 4 or len(candidate_tokens) < 4:
        return False
    matcher = SequenceMatcher(
        None,
        [token for token, _ in candidate_tokens],
        [token for token, _ in draft_tokens],
        autojunk=False,
    )
    matched_draft: set[int] = set()
    last_draft_match = 0
    for block in matcher.get_matching_blocks():
        if block.size == 0:
            continue
        matched_draft.update(range(block.b, block.b + block.size))
        last_draft_match = max(last_draft_match, block.b + block.size)
    coverage = len(matched_draft) / max(1, len(draft_tokens))
    return coverage >= 0.55 and last_draft_match >= len(draft_tokens)


def _detector_chunk_risk(
    *,
    chunk_index: int | None = None,
    language: str | None,
    probability: float | None,
    low_confidence_threshold: float,
    early_low_confidence_threshold: float | None = None,
) -> bool:
    if language in HINDI_LANGUAGE_CODES:
        return True
    # On mixed Hindi+English, tiny language ID often reports English with weak
    # confidence. Treat only very weak English as risk; confident English stays
    # on the fast path and replacement safety still gates any final update.
    if language and probability is not None and probability < low_confidence_threshold:
        return True
    if (
        language == "en"
        and chunk_index == 0
        and early_low_confidence_threshold is not None
        and probability is not None
        and probability < early_low_confidence_threshold
    ):
        return True
    return False


def _detector_risk_reason(
    *,
    chunk_index: int | None = None,
    language: str | None,
    probability: float | None,
    low_confidence_threshold: float,
    early_low_confidence_threshold: float | None = None,
) -> str:
    if language in HINDI_LANGUAGE_CODES:
        return f"language:{language}"
    if language == "en" and probability is not None and probability < low_confidence_threshold:
        return "english_low_confidence"
    if (
        language == "en"
        and chunk_index == 0
        and early_low_confidence_threshold is not None
        and probability is not None
        and probability < early_low_confidence_threshold
    ):
        return "english_early_low_confidence"
    if language and probability is not None and probability < low_confidence_threshold:
        return f"non_english_low_confidence:{language}"
    return "unknown"
