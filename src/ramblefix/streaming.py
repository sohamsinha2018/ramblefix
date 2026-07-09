from __future__ import annotations

import re
import tempfile
import time
import wave
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ramblefix.engine_router import transcribe_ramblefix_hinglish_v1
from ramblefix.external_asr import transcribe_whisper_cpp_server_translate


@dataclass(frozen=True)
class StreamingEvent:
    type: str
    text: str
    t_ms: int
    source: str
    stable: bool = False
    audio_seconds: float | None = None
    compute_ms: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class StreamingResult:
    audio: str
    audio_seconds: float
    paste_text: str
    final_text: str
    route: str
    events: list[StreamingEvent]
    timings_ms: dict[str, int | None]
    churn_wer: float | None
    errors: list[str]

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["events"] = [asdict(event) for event in self.events]
        return payload


@dataclass(frozen=True)
class _DraftResult:
    text: str
    source: str
    compute_ms: int
    error: str | None = None


def stream_wav_file(
    audio_path: str | Path,
    *,
    real_time: bool = True,
    chunk_seconds: float = 0.25,
    draft_min_seconds: float = 1.5,
    draft_every_seconds: float = 1.5,
    min_stable_words: int = 4,
    finalizer: str = "auto",
    paste_overhead_ms: int = 50,
) -> StreamingResult:
    """Simulate product streaming on a WAV file.

    This is a streaming product lab, not a true streaming decoder. It feeds the
    WAV in real-time chunks, runs warm fast-ASR on rolling prefixes, emits
    unstable/stable draft events, pastes the latest draft at key-up, and runs
    the slower Hinglish finalizer asynchronously only when configured.
    """
    path = Path(audio_path).expanduser().resolve()
    events: list[StreamingEvent] = []
    errors: list[str] = []
    latest_draft = ""
    previous_draft = ""
    latest_stable = ""
    first_partial_ms: int | None = None
    first_stable_ms: int | None = None
    paste_done_ms: int | None = None
    final_visible_ms: int | None = None
    speech_end_ms: int | None = None
    route = "draft_only"

    with wave.open(str(path), "rb") as reader:
        params = reader.getparams()
        frame_rate = reader.getframerate()
        total_frames = reader.getnframes()
        audio_seconds = total_frames / frame_rate if frame_rate else 0.0
        chunk_frames = max(1, int(frame_rate * chunk_seconds))

        with tempfile.TemporaryDirectory(prefix="ramblefix-streaming-") as tmp_dir:
            tmp = Path(tmp_dir)
            buffered_chunks: list[bytes] = []
            start = time.perf_counter()
            audio_cursor = 0.0
            next_draft_at = max(draft_min_seconds, chunk_seconds)
            draft_future: Future[_DraftResult] | None = None
            draft_index = 0

            with ThreadPoolExecutor(max_workers=2) as executor:
                while True:
                    chunk = reader.readframes(chunk_frames)
                    if not chunk:
                        break
                    buffered_chunks.append(chunk)
                    audio_cursor += len(chunk) / max(1, params.sampwidth * params.nchannels * frame_rate)

                    if real_time:
                        _sleep_until(start + audio_cursor)

                    if draft_future is not None and draft_future.done():
                        result = draft_future.result()
                        draft_future = None
                        event, previous_draft, latest_draft, latest_stable = _draft_event(
                            result=result,
                            previous_draft=previous_draft,
                            latest_stable=latest_stable,
                            elapsed_ms=_elapsed_ms(start),
                            audio_cursor=audio_cursor,
                            min_stable_words=min_stable_words,
                        )
                        events.append(event)
                        if event.error:
                            errors.append(event.error)
                        if first_partial_ms is None and event.text.strip():
                            first_partial_ms = event.t_ms
                        if first_stable_ms is None and event.stable and event.text.strip():
                            first_stable_ms = event.t_ms

                    if audio_cursor >= next_draft_at and draft_future is None:
                        prefix_path = tmp / f"prefix_{draft_index:04d}.wav"
                        _write_wav(prefix_path, params, buffered_chunks)
                        draft_index += 1
                        draft_future = executor.submit(_draft_transcribe, prefix_path)
                        next_draft_at += draft_every_seconds

                speech_end_ms = _elapsed_ms(start)

                if latest_stable or latest_draft:
                    paste_text = latest_stable or latest_draft
                    paste_done_ms = speech_end_ms + paste_overhead_ms
                else:
                    if draft_future is None:
                        prefix_path = tmp / f"prefix_{draft_index:04d}.wav"
                        _write_wav(prefix_path, params, buffered_chunks)
                        draft_future = executor.submit(_draft_transcribe, prefix_path)
                    result = draft_future.result()
                    draft_future = None
                    event, previous_draft, latest_draft, latest_stable = _draft_event(
                        result=result,
                        previous_draft=previous_draft,
                        latest_stable=latest_stable,
                        elapsed_ms=_elapsed_ms(start),
                        audio_cursor=audio_cursor,
                        min_stable_words=min_stable_words,
                    )
                    events.append(event)
                    if event.error:
                        errors.append(event.error)
                    if first_partial_ms is None and event.text.strip():
                        first_partial_ms = event.t_ms
                    if first_stable_ms is None and event.stable and event.text.strip():
                        first_stable_ms = event.t_ms
                    if not latest_draft.strip():
                        prefix_path = tmp / f"prefix_{draft_index:04d}_full.wav"
                        _write_wav(prefix_path, params, buffered_chunks)
                        result = executor.submit(_draft_transcribe, prefix_path).result()
                        event, previous_draft, latest_draft, latest_stable = _draft_event(
                            result=result,
                            previous_draft=previous_draft,
                            latest_stable=latest_stable,
                            elapsed_ms=_elapsed_ms(start),
                            audio_cursor=audio_cursor,
                            min_stable_words=min_stable_words,
                        )
                        events.append(event)
                        if event.error:
                            errors.append(event.error)
                        if first_partial_ms is None and event.text.strip():
                            first_partial_ms = event.t_ms
                        if first_stable_ms is None and event.stable and event.text.strip():
                            first_stable_ms = event.t_ms
                    paste_text = latest_stable or latest_draft
                    paste_done_ms = max(_elapsed_ms(start), speech_end_ms) + paste_overhead_ms

                final_text = paste_text
                if _should_finalize(finalizer, paste_text):
                    route = "draft_then_srota_final"
                    final_future = executor.submit(_srota_finalize, path)
                    final_result = final_future.result()
                    final_visible_ms = _elapsed_ms(start)
                    if final_result.error:
                        errors.append(final_result.error)
                    elif final_result.text.strip():
                        final_text = final_result.text.strip()
                    events.append(
                        StreamingEvent(
                            type="final",
                            text=final_text,
                            t_ms=final_visible_ms,
                            source=final_result.source,
                            stable=True,
                            audio_seconds=audio_seconds,
                            compute_ms=final_result.compute_ms,
                            error=final_result.error,
                        )
                    )
                else:
                    final_visible_ms = paste_done_ms
                    events.append(
                        StreamingEvent(
                            type="final",
                            text=final_text,
                            t_ms=final_visible_ms,
                            source="draft",
                            stable=True,
                            audio_seconds=audio_seconds,
                            compute_ms=0,
                        )
                    )

    return StreamingResult(
        audio=str(path),
        audio_seconds=round(audio_seconds, 3),
        paste_text=paste_text,
        final_text=final_text,
        route=route,
        events=events,
        timings_ms={
            "time_to_first_partial": first_partial_ms,
            "time_to_first_stable": first_stable_ms,
            "speech_end": speech_end_ms,
            "paste_done": paste_done_ms,
            "release_to_paste": None if paste_done_ms is None or speech_end_ms is None else max(0, paste_done_ms - speech_end_ms),
            "final_visible": final_visible_ms,
            "release_to_final": None if final_visible_ms is None or speech_end_ms is None else max(0, final_visible_ms - speech_end_ms),
        },
        churn_wer=_safe_churn_wer(paste_text, final_text),
        errors=errors,
    )


def _draft_transcribe(path: Path) -> _DraftResult:
    started = time.perf_counter()
    try:
        transcript = transcribe_whisper_cpp_server_translate(path, timeout_seconds=10.0)
        return _DraftResult(
            text=_clean_draft_text(transcript.text),
            source="whisper_cpp_server_translate_prefix",
            compute_ms=round((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 - surface as event error.
        return _DraftResult(
            text="",
            source="whisper_cpp_server_translate_prefix",
            compute_ms=round((time.perf_counter() - started) * 1000),
            error=repr(exc),
        )


def _srota_finalize(path: Path) -> _DraftResult:
    started = time.perf_counter()
    try:
        routed = transcribe_ramblefix_hinglish_v1(path)
        return _DraftResult(
            text=routed.text.strip(),
            source=routed.engine,
            compute_ms=round((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 - finalizer failures should not kill draft paste.
        return _DraftResult(
            text="",
            source="srota_finalizer",
            compute_ms=round((time.perf_counter() - started) * 1000),
            error=repr(exc),
        )


def _draft_event(
    *,
    result: _DraftResult,
    previous_draft: str,
    latest_stable: str,
    elapsed_ms: int,
    audio_cursor: float,
    min_stable_words: int,
) -> tuple[StreamingEvent, str, str, str]:
    text = result.text.strip()
    stable_text = _common_prefix_text(previous_draft, text)
    is_stable = len(_words(stable_text)) >= min_stable_words
    if is_stable and len(stable_text) >= len(latest_stable):
        latest_stable = stable_text
    event = StreamingEvent(
        type="partial",
        text=text,
        t_ms=elapsed_ms,
        source=result.source,
        stable=is_stable,
        audio_seconds=round(audio_cursor, 3),
        compute_ms=result.compute_ms,
        error=result.error,
    )
    return event, text or previous_draft, text, latest_stable


def _should_finalize(mode: str, paste_text: str) -> bool:
    normalized = mode.strip().lower()
    if normalized == "always":
        return True
    if normalized in {"none", "never", "off"}:
        return False
    text = paste_text.strip()
    if not text:
        return True
    if re.search(r"\b(unclear|inaudible|foreign language|speaking in foreign language)\b", text, flags=re.I):
        return True
    words = _words(text)
    return len(words) <= 4


def _write_wav(path: Path, params: wave._wave_params, chunks: list[bytes]) -> None:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(params.nchannels)
        writer.setsampwidth(params.sampwidth)
        writer.setframerate(params.framerate)
        writer.writeframes(b"".join(chunks))


def _sleep_until(target: float) -> None:
    remaining = target - time.perf_counter()
    if remaining > 0:
        time.sleep(remaining)


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def _common_prefix_text(left: str, right: str) -> str:
    left_words = _words(left)
    right_words = _words(right)
    prefix: list[str] = []
    for a, b in zip(left_words, right_words):
        if a.lower() != b.lower():
            break
        prefix.append(b)
    return " ".join(prefix)


def _safe_churn_wer(before: str, after: str) -> float | None:
    if not before.strip() and not after.strip():
        return 0.0
    if not before.strip() or not after.strip():
        return None
    try:
        from ramblefix.eval import word_error_rate

        return word_error_rate(before, after)
    except Exception:
        return None


def _words(text: str) -> list[str]:
    return re.findall(r"[\w'.-]+", text, flags=re.UNICODE)


def _clean_draft_text(text: str) -> str:
    stripped = text.strip()
    if re.fullmatch(r"\[(?:BLANK_AUDIO|MUSIC|NOISE|SILENCE|INAUDIBLE)\]", stripped, flags=re.IGNORECASE):
        return ""
    return stripped
