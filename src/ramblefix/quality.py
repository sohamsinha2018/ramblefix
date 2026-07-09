from __future__ import annotations

import math
import re
import sys
import wave
from array import array
from pathlib import Path


def repeated_substring_score(text: str) -> float:
    compact = re.sub(r"[^a-zA-Z]+", "", text.lower())
    worst = repeated_ngram_score(text)
    if len(compact) < 24:
        return worst

    for size in range(3, 16):
        pattern = re.compile(rf"([a-z]{{{size}}})(?:\1){{2,}}")
        for match in pattern.finditer(compact):
            worst = max(worst, len(match.group(0)) / len(compact))
    return round(worst, 3)


def repeated_ngram_score(text: str) -> float:
    tokens = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
    if len(tokens) < 8:
        return 0.0

    worst = 0.0
    for size in range(2, min(8, len(tokens) // 3) + 1):
        index = 0
        while index + (size * 3) <= len(tokens):
            chunk = tokens[index : index + size]
            repeats = 1
            cursor = index + size
            while cursor + size <= len(tokens) and tokens[cursor : cursor + size] == chunk:
                repeats += 1
                cursor += size
            if repeats >= 3:
                worst = max(worst, (size * repeats) / len(tokens))
                index = cursor
            else:
                index += 1
    return round(worst, 3)


def is_degenerate_transcript(text: str) -> bool:
    if not text.strip():
        return True
    return repeated_substring_score(text) >= 0.25


def is_blank_or_no_speech_transcript(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if lowered == "<|nospeech|>":
        return True
    marker = re.sub(r"[_\s]+", " ", lowered.strip("[](){}<>._- ")).strip()
    return marker in {
        "blank",
        "blank audio",
        "silence",
        "silent audio",
        "no speech",
        "no speech detected",
        "inaudible",
        "music",
        "noise",
    }


def wav_silence_metrics(path: str | Path) -> dict[str, float | bool]:
    """Cheap recorder guard for the dictation hot path.

    This intentionally supports the native app's 16-bit PCM WAVs. If the file is
    not a simple WAV, return an empty dict and let ASR/text guards decide.
    """
    try:
        with wave.open(str(path), "rb") as wav:
            frame_count = wav.getnframes()
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            frames = wav.readframes(frame_count)
    except Exception:
        return {}

    if frame_count <= 0 or sample_rate <= 0 or channels <= 0 or sample_width != 2:
        return {}

    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return {}

    duration_seconds = frame_count / float(sample_rate)
    peak = max(abs(sample) for sample in samples) / 32768.0
    window_size = max(channels, int(sample_rate * channels * 0.1))
    rms_values: list[float] = []
    for start in range(0, len(samples), window_size):
        window = samples[start : start + window_size]
        if not window:
            continue
        rms = math.sqrt(sum(float(sample) * float(sample) for sample in window) / len(window)) / 32768.0
        rms_values.append(rms)
    silent_window_ratio = (
        sum(1 for rms in rms_values if rms < 0.002) / len(rms_values)
        if rms_values
        else 1.0
    )
    rms_max = max(rms_values) if rms_values else 0.0
    probably_silent = peak < 0.004 and silent_window_ratio >= 0.95
    return {
        "audio_duration_seconds": round(duration_seconds, 3),
        "audio_peak": round(peak, 6),
        "audio_rms_max": round(rms_max, 6),
        "audio_silent_window_ratio": round(silent_window_ratio, 3),
        "audio_probably_silent": probably_silent,
    }
