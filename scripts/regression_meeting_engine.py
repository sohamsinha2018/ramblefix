from __future__ import annotations

import math
import struct
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.meeting_engine import (
    MeetingCandidate,
    normalize_meeting_text,
    select_meeting_candidate,
    split_wav_to_chunks,
    wav_duration_seconds,
)


def main() -> None:
    assert_clean_english_prefers_fast()
    assert_hinglish_prefers_hinglish_candidate()
    assert_bad_hinglish_falls_back_to_fast()
    assert_meeting_text_uses_glossary()
    assert_chunking_writes_valid_wavs()
    print("meeting engine regression passed")


def assert_clean_english_prefers_fast() -> None:
    fast = MeetingCandidate(
        source="fast",
        text="The API layer needs better documentation and action items.",
        engine="fast",
        seconds=0.5,
    )
    hinglish = MeetingCandidate(
        source="hinglish",
        text="The API layer needs better documentation and action items.",
        engine="hinglish",
        seconds=1.0,
    )
    selected = select_meeting_candidate(fast=fast, hinglish=hinglish, mode="auto")
    assert selected.source == "fast", selected


def assert_hinglish_prefers_hinglish_candidate() -> None:
    fast = MeetingCandidate(
        source="fast",
        text="Yes, look, nothing will happen if our tool cannot beat others.",
        engine="fast",
        seconds=0.5,
    )
    hinglish = MeetingCandidate(
        source="hinglish",
        text="Haan bhai dekh ye sab karne se kuch nahi hoga agar hamara tool cannot beat others.",
        engine="hinglish",
        seconds=1.0,
    )
    selected = select_meeting_candidate(fast=fast, hinglish=hinglish, mode="auto")
    assert selected.source == "hinglish", selected


def assert_bad_hinglish_falls_back_to_fast() -> None:
    fast = MeetingCandidate(
        source="fast",
        text="The meeting owner said ship the API migration by Friday.",
        engine="fast",
        seconds=0.5,
    )
    hinglish = MeetingCandidate(
        source="hinglish_error",
        text="",
        engine="hinglish",
        seconds=1.0,
        error="model failed",
    )
    selected = select_meeting_candidate(fast=fast, hinglish=hinglish, mode="auto")
    assert selected.source == "fast", selected


def assert_meeting_text_uses_glossary() -> None:
    assert normalize_meeting_text("Use Rumble Fix for mcp and fms.") == "Use RambleFix for MCP and FMS."


def assert_chunking_writes_valid_wavs() -> None:
    with tempfile.TemporaryDirectory(prefix="ramblefix-meeting-regression-") as tmp:
        tmp_path = Path(tmp)
        audio = tmp_path / "input.wav"
        write_sine_wav(audio, seconds=2.4)
        chunks = split_wav_to_chunks(audio, tmp_path / "chunks", chunk_seconds=1.0)
        assert len(chunks) == 3, chunks
        assert abs(wav_duration_seconds(chunks[0].path) - 1.0) < 0.02
        assert abs(wav_duration_seconds(chunks[-1].path) - 0.4) < 0.02
        for chunk in chunks:
            assert chunk.path.exists(), chunk.path
            with wave.open(str(chunk.path), "rb") as reader:
                assert reader.getframerate() == 16_000
                assert reader.getnchannels() == 1


def write_sine_wav(path: Path, *, seconds: float, sample_rate: int = 16_000) -> None:
    frames = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        for index in range(frames):
            value = int(0.2 * 32767 * math.sin(2 * math.pi * 440 * index / sample_rate))
            writer.writeframes(struct.pack("<h", value))


if __name__ == "__main__":
    main()
