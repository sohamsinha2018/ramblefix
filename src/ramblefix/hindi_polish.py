from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ramblefix.engine_router import transcribe_ramblefix_hinglish_v1
from ramblefix.external_asr import detect_faster_whisper_language
from ramblefix.processing import process_transcript
from ramblefix.quality import is_degenerate_transcript, repeated_substring_score


HINDI_LANGUAGE_CODES = {"hi", "ur"}
HINGLISH_MARKERS = {
    "aap",
    "ab",
    "agar",
    "bhai",
    "haan",
    "hai",
    "hain",
    "kare",
    "karo",
    "kaise",
    "kya",
    "matlab",
    "nahi",
    "nahin",
    "theek",
    "toh",
    "yaar",
    "yeh",
}


@dataclass(frozen=True)
class HindiRiskResult:
    risk: bool
    language: str | None
    probability: float | None
    seconds: float
    engine: str
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HindiPolishResult:
    text: str
    raw_text: str
    engine: str
    route: str
    seconds: float
    risk: HindiRiskResult
    quality: dict[str, Any]
    candidates: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


def detect_hindi_risk(
    audio_path: str | Path,
    *,
    draft_text: str = "",
    low_confidence_threshold: float = 0.50,
    min_low_confidence_words: int = 3,
) -> HindiRiskResult:
    started = time.perf_counter()
    transcript = detect_faster_whisper_language(audio_path, model="tiny", compute_type="int8")
    probability = transcript.language_probability
    language = (transcript.language or "").strip().lower() or None
    reasons: list[str] = []
    if language in HINDI_LANGUAGE_CODES:
        reasons.append(f"language:{language}")
    if probability is not None and probability < low_confidence_threshold and word_count(draft_text) >= min_low_confidence_words:
        reasons.append("low_language_confidence")
    if has_hinglish_marker(draft_text):
        reasons.append("draft_hinglish_marker")
    return HindiRiskResult(
        risk=bool(reasons),
        language=language,
        probability=probability,
        seconds=round(time.perf_counter() - started, 3),
        engine=transcript.engine,
        reasons=sorted(set(reasons)),
    )


def polish_hindi_if_needed(
    audio_path: str | Path,
    *,
    draft_text: str,
    low_confidence_threshold: float = 0.50,
    force: bool = False,
) -> HindiPolishResult:
    started = time.perf_counter()
    if force:
        risk = HindiRiskResult(
            risk=True,
            language=None,
            probability=None,
            seconds=0.0,
            engine="forced-audio-risk",
            reasons=["forced_oriserve"],
        )
    else:
        try:
            risk = detect_hindi_risk(
                audio_path,
                draft_text=draft_text,
                low_confidence_threshold=low_confidence_threshold,
            )
        except Exception as exc:  # noqa: BLE001
            risk = HindiRiskResult(
                risk=False,
                language=None,
                probability=None,
                seconds=round(time.perf_counter() - started, 3),
                engine="faster-whisper.detect-language:tiny:int8",
                reasons=[],
            )
            return HindiPolishResult(
                text=draft_text,
                raw_text="",
                engine="hindi-polish",
                route="hindi_polish_detector_error",
                seconds=round(time.perf_counter() - started, 3),
                risk=risk,
                quality={"error": f"{type(exc).__name__}: {exc}"},
                error=f"{type(exc).__name__}: {exc}",
            )

    if not force and not risk.risk:
        return HindiPolishResult(
            text=draft_text,
            raw_text="",
            engine="hindi-polish",
            route="hindi_polish_skipped",
            seconds=round(time.perf_counter() - started, 3),
            risk=risk,
            quality={"hindi_risk": False, "risk_reasons": []},
        )

    routed = transcribe_ramblefix_hinglish_v1(audio_path)
    output = process_transcript(routed.text, use_ollama=False)
    quality = {
        "hindi_risk": True,
        "risk_reasons": risk.reasons,
        "detector_seconds": risk.seconds,
        "detector_language": risk.language,
        "detector_probability": risk.probability,
        "repeated_substring_score": repeated_substring_score(routed.text),
        "degenerate": is_degenerate_transcript(routed.text),
        "char_count": len(routed.text),
        "route": routed.route,
    }
    return HindiPolishResult(
        text=output.clean_transcript,
        raw_text=routed.text,
        engine=f"hindi_polish:{routed.engine}",
        route="hindi_polish_changed",
        seconds=round(time.perf_counter() - started, 3),
        risk=risk,
        quality=quality,
        candidates=[
            {
                "source": candidate.source,
                "text": candidate.text,
                "seconds": candidate.seconds,
                "engine": candidate.engine,
                "error": candidate.error,
                "language": candidate.language,
                "language_probability": candidate.language_probability,
                "risk": candidate.risk,
            }
            for candidate in routed.candidates
        ],
    )


def has_hinglish_marker(text: str) -> bool:
    tokens = set(re.findall(r"[a-z]+", text.lower()))
    return bool(tokens.intersection(HINGLISH_MARKERS))


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", text))
