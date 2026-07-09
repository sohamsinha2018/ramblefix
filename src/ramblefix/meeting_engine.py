from __future__ import annotations

import json
import re
import tempfile
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from ramblefix.external_asr import (
    ExternalTranscript,
    transcribe_local_meaning_server_with_fallback,
    transcribe_oriserve_hindi2hinglish,
)
from ramblefix.glossary import apply_glossary, dictionary_version
from ramblefix.quality import (
    is_blank_or_no_speech_transcript,
    is_degenerate_transcript,
    repeated_substring_score,
    wav_silence_metrics,
)


MeetingEngineMode = Literal["fast", "hinglish", "auto"]

HINGLISH_MARKERS = {
    "aap",
    "aapko",
    "agar",
    "aisa",
    "aur",
    "bata",
    "bhai",
    "bhi",
    "chahiye",
    "chaahie",
    "dekh",
    "haan",
    "hai",
    "hain",
    "ham",
    "hamara",
    "hame",
    "hoga",
    "hona",
    "kaise",
    "kar",
    "kare",
    "karna",
    "karke",
    "kya",
    "kyon",
    "kyunki",
    "matlab",
    "mujhe",
    "nahi",
    "nahin",
    "par",
    "saare",
    "saari",
    "sakta",
    "sakte",
    "samajh",
    "taaki",
    "taki",
    "theek",
    "thik",
    "toh",
    "yaar",
    "yeh",
}


@dataclass(frozen=True)
class MeetingCandidate:
    source: str
    text: str
    engine: str
    seconds: float
    error: str = ""


@dataclass(frozen=True)
class MeetingSegment:
    index: int
    start_seconds: float
    end_seconds: float
    audio: str
    text: str
    route: str
    engine: str
    seconds: float
    candidates: list[MeetingCandidate]
    quality: dict[str, object]


@dataclass(frozen=True)
class MeetingTranscript:
    audio: str
    text: str
    engine: str
    mode: MeetingEngineMode
    seconds: float
    audio_seconds: float
    chunk_seconds: float
    segments: list[MeetingSegment]
    output_dir: str

    def to_json(self) -> dict[str, object]:
        payload = asdict(self)
        payload["segments"] = [
            {
                **asdict(segment),
                "candidates": [asdict(candidate) for candidate in segment.candidates],
            }
            for segment in self.segments
        ]
        return payload


def transcribe_meeting_audio(
    audio_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    chunk_seconds: float = 30.0,
    mode: MeetingEngineMode = "auto",
    skip_process_fallback: bool = False,
) -> MeetingTranscript:
    """Transcribe a long local meeting recording by chunking and routing locally.

    This is provider-agnostic at the transcription layer: it accepts any WAV the
    app captures or imports. Capturing other apps' audio is a separate native
    macOS capture problem.
    """

    if mode not in {"fast", "hinglish", "auto"}:
        raise ValueError(f"unsupported meeting engine mode: {mode}")
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if chunk_seconds < 5:
        raise ValueError("chunk_seconds must be at least 5")

    started = time.perf_counter()
    audio_seconds = wav_duration_seconds(path)
    if output_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="ramblefix-meeting-")
        out_dir = Path(tmp.name)
    else:
        tmp = None
        out_dir = Path(output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

    try:
        chunk_dir = out_dir / "chunks"
        chunks = split_wav_to_chunks(path, chunk_dir, chunk_seconds=chunk_seconds)
        segments = [
            _transcribe_meeting_chunk(
                index=index,
                chunk=chunk,
                mode=mode,
                skip_process_fallback=skip_process_fallback,
            )
            for index, chunk in enumerate(chunks)
        ]
        text = "\n".join(segment.text for segment in segments if segment.text.strip()).strip()
        transcript = MeetingTranscript(
            audio=str(path),
            text=text,
            engine="ramblefix_meeting_engine_v1",
            mode=mode,
            seconds=round(time.perf_counter() - started, 3),
            audio_seconds=round(audio_seconds, 3),
            chunk_seconds=chunk_seconds,
            segments=segments,
            output_dir=str(out_dir),
        )
        if output_dir is not None:
            (out_dir / "meeting_transcript.txt").write_text(text + ("\n" if text else ""), encoding="utf-8")
            (out_dir / "meeting_transcript.json").write_text(
                json.dumps(transcript.to_json(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return transcript
    finally:
        if tmp is not None:
            tmp.cleanup()


@dataclass(frozen=True)
class WavChunk:
    path: Path
    start_seconds: float
    end_seconds: float


def split_wav_to_chunks(audio_path: str | Path, output_dir: str | Path, *, chunk_seconds: float) -> list[WavChunk]:
    path = Path(audio_path).expanduser().resolve()
    out_dir = Path(output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[WavChunk] = []
    with wave.open(str(path), "rb") as reader:
        params = reader.getparams()
        frame_rate = reader.getframerate()
        total_frames = reader.getnframes()
        frames_per_chunk = max(1, int(frame_rate * chunk_seconds))
        cursor = 0
        index = 0
        while cursor < total_frames:
            reader.setpos(cursor)
            frames_to_read = min(frames_per_chunk, total_frames - cursor)
            data = reader.readframes(frames_to_read)
            if not data:
                break
            chunk_path = out_dir / f"chunk-{index:04d}.wav"
            with wave.open(str(chunk_path), "wb") as writer:
                writer.setparams(params)
                writer.writeframes(data)
            start = cursor / frame_rate
            end = (cursor + frames_to_read) / frame_rate
            chunks.append(WavChunk(path=chunk_path, start_seconds=round(start, 3), end_seconds=round(end, 3)))
            cursor += frames_to_read
            index += 1
    return chunks


def wav_duration_seconds(audio_path: str | Path) -> float:
    with wave.open(str(Path(audio_path).expanduser()), "rb") as reader:
        return reader.getnframes() / float(reader.getframerate())


def _transcribe_meeting_chunk(
    *,
    index: int,
    chunk: WavChunk,
    mode: MeetingEngineMode,
    skip_process_fallback: bool,
) -> MeetingSegment:
    started = time.perf_counter()
    candidates: list[MeetingCandidate] = []

    fast = _fast_meeting_candidate(chunk.path, skip_process_fallback=skip_process_fallback)
    if mode in {"fast", "auto"}:
        candidates.append(fast)

    hinglish: MeetingCandidate | None = None
    if mode in {"hinglish", "auto"}:
        hinglish = _hinglish_meeting_candidate(chunk.path)
        candidates.append(hinglish)

    selected = select_meeting_candidate(fast=fast, hinglish=hinglish, mode=mode)
    normalized_text = normalize_meeting_text(selected.text)
    audio_quality = wav_silence_metrics(chunk.path)
    quality = {
        "blank_or_no_speech": is_blank_or_no_speech_transcript(selected.text)
        or bool(audio_quality.get("audio_probably_silent")),
        "degenerate": is_degenerate_transcript(selected.text),
        "repeat": repeated_substring_score(selected.text),
        "hinglish_markers": sorted(hinglish_marker_tokens(selected.text)),
        "glossary_changed": normalized_text != selected.text.strip(),
        "dictionary_version": dictionary_version(),
        **audio_quality,
    }
    return MeetingSegment(
        index=index,
        start_seconds=chunk.start_seconds,
        end_seconds=chunk.end_seconds,
        audio=str(chunk.path),
        text=normalized_text,
        route=selected.source,
        engine=selected.engine,
        seconds=round(time.perf_counter() - started, 3),
        candidates=candidates,
        quality=quality,
    )


def _fast_meeting_candidate(path: Path, *, skip_process_fallback: bool) -> MeetingCandidate:
    started = time.perf_counter()
    try:
        transcript = transcribe_local_meaning_server_with_fallback(
            path,
            skip_process_fallback=skip_process_fallback,
        )
        return _candidate_from_transcript("fast", transcript)
    except Exception as exc:  # noqa: BLE001 - keep meeting transcription moving chunk by chunk.
        return MeetingCandidate(
            source="fast_error",
            text="",
            engine="fast_error",
            seconds=round(time.perf_counter() - started, 3),
            error=f"{type(exc).__name__}: {exc}",
        )


def _hinglish_meeting_candidate(path: Path) -> MeetingCandidate:
    started = time.perf_counter()
    try:
        transcript = transcribe_oriserve_hindi2hinglish(path)
        return _candidate_from_transcript("hinglish", transcript)
    except Exception as exc:  # noqa: BLE001 - keep meeting transcription moving chunk by chunk.
        return MeetingCandidate(
            source="hinglish_error",
            text="",
            engine="hinglish_error",
            seconds=round(time.perf_counter() - started, 3),
            error=f"{type(exc).__name__}: {exc}",
        )


def _candidate_from_transcript(source: str, transcript: ExternalTranscript) -> MeetingCandidate:
    return MeetingCandidate(
        source=source,
        text=transcript.text.strip(),
        engine=transcript.engine,
        seconds=transcript.seconds,
    )


def normalize_meeting_text(text: str) -> str:
    return apply_glossary(text.strip())


def select_meeting_candidate(
    *,
    fast: MeetingCandidate,
    hinglish: MeetingCandidate | None,
    mode: MeetingEngineMode,
) -> MeetingCandidate:
    if mode == "fast" or hinglish is None:
        return fast
    if mode == "hinglish":
        return hinglish if usable_candidate(hinglish) else fast
    if not usable_candidate(fast):
        return hinglish if usable_candidate(hinglish) else fast
    if not usable_candidate(hinglish):
        return fast

    fast_markers = hinglish_marker_tokens(fast.text)
    hinglish_markers = hinglish_marker_tokens(hinglish.text)
    if len(hinglish_markers) >= 2 and len(hinglish_markers) > len(fast_markers):
        if word_count(hinglish.text) >= max(4, int(word_count(fast.text) * 0.55)):
            return hinglish
    if len(hinglish_markers) >= 1 and is_translation_loss_risk(fast.text):
        return hinglish
    return fast


def usable_candidate(candidate: MeetingCandidate) -> bool:
    text = candidate.text.strip()
    if candidate.error or not text:
        return False
    if is_blank_or_no_speech_transcript(text) or is_degenerate_transcript(text):
        return False
    return word_count(text) >= 3


def is_translation_loss_risk(text: str) -> bool:
    lowered = text.lower()
    generic_starts = (
        "yes, look",
        "yes, see",
        "now tell me",
        "what is it that",
        "it should be a solution",
    )
    return lowered.startswith(generic_starts)


def hinglish_marker_tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z]+", text.lower()))
    return tokens.intersection(HINGLISH_MARKERS)


def word_count(text: str) -> int:
    return len(re.findall(r"[\w']+", text, flags=re.UNICODE))
