from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ramblefix.external_asr import transcribe_sensevoice_small, transcribe_whisper_cpp
from ramblefix.quality import (
    is_blank_or_no_speech_transcript,
    is_degenerate_transcript,
    repeated_substring_score,
)


CHINESE_LANGUAGE_CODES = {"zh", "zh-cn", "zh-tw", "cmn", "yue", "chi", "chinese", "mandarin", "cantonese"}


@dataclass(frozen=True)
class ChineseRiskResult:
    risk: bool
    language: str | None
    probability: float | None
    seconds: float
    engine: str
    text: str = ""
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChinesePolishResult:
    text: str
    raw_text: str
    engine: str
    route: str
    seconds: float
    risk: ChineseRiskResult
    quality: dict[str, Any]
    safe_update: bool
    reject_reasons: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


def detect_chinese_risk(audio_path: str | Path, *, draft_text: str = "") -> ChineseRiskResult:
    started = time.perf_counter()
    transcript = transcribe_whisper_cpp(audio_path, language="auto")
    language = (transcript.language or "").strip().lower() or None
    probability = transcript.language_probability
    detector_text = transcript.text.strip()
    reasons: list[str] = []
    if language in CHINESE_LANGUAGE_CODES:
        reasons.append(f"language:{language}")
    if has_chinese_script(detector_text):
        reasons.append("detector_chinese_script")
    if has_chinese_script(draft_text):
        reasons.append("draft_chinese_script")
    return ChineseRiskResult(
        risk=bool(reasons),
        language=language,
        probability=probability,
        seconds=round(time.perf_counter() - started, 3),
        engine=transcript.engine,
        text=detector_text,
        reasons=sorted(set(reasons)),
    )


def polish_chinese_if_needed(
    audio_path: str | Path,
    *,
    draft_text: str,
    force: bool = False,
) -> ChinesePolishResult:
    started = time.perf_counter()
    if force:
        risk = ChineseRiskResult(
            risk=True,
            language=None,
            probability=None,
            seconds=0.0,
            engine="forced-audio-risk",
            text="",
            reasons=["forced_sensevoice"],
        )
    else:
        try:
            risk = detect_chinese_risk(audio_path, draft_text=draft_text)
        except Exception as exc:  # noqa: BLE001
            risk = ChineseRiskResult(
                risk=False,
                language=None,
                probability=None,
                seconds=round(time.perf_counter() - started, 3),
                engine="whisper_cpp:auto",
                text="",
                reasons=[],
            )
            return ChinesePolishResult(
                text=draft_text,
                raw_text="",
                engine="chinese-polish",
                route="chinese_polish_detector_error",
                seconds=round(time.perf_counter() - started, 3),
                risk=risk,
                quality={"chinese_risk": False, "risk_reasons": [], "error": f"{type(exc).__name__}: {exc}"},
                safe_update=False,
                reject_reasons=["detector_error"],
                error=f"{type(exc).__name__}: {exc}",
            )

    if not force and not risk.risk:
        return ChinesePolishResult(
            text=draft_text,
            raw_text=risk.text,
            engine="chinese-polish",
            route="chinese_polish_skipped",
            seconds=round(time.perf_counter() - started, 3),
            risk=risk,
            quality={
                "chinese_risk": False,
                "risk_reasons": [],
                "detector_text": risk.text,
                "detector_language": risk.language,
                "detector_probability": risk.probability,
                "detector_seconds": risk.seconds,
            },
            safe_update=False,
            reject_reasons=[],
        )

    try:
        transcript = transcribe_sensevoice_small(
            audio_path,
            language=os.environ.get("RAMBLEFIX_SENSEVOICE_LANGUAGE", "auto"),
        )
    except Exception as exc:  # noqa: BLE001
        quality = _quality(
            text="",
            risk=risk,
            route="chinese_polish_sensevoice_error",
            error=f"{type(exc).__name__}: {exc}",
        )
        return ChinesePolishResult(
            text=draft_text,
            raw_text="",
            engine="chinese-polish:sensevoice-error",
            route="chinese_polish_sensevoice_error",
            seconds=round(time.perf_counter() - started, 3),
            risk=risk,
            quality=quality,
            safe_update=False,
            reject_reasons=["sensevoice_error"],
            candidates=[],
            error=f"{type(exc).__name__}: {exc}",
        )

    raw_text = transcript.text.strip()
    reject_reasons = chinese_update_reject_reasons(draft_text, raw_text)
    safe_update = not reject_reasons
    route = "chinese_polish_changed" if safe_update else "chinese_polish_rejected"
    quality = _quality(text=raw_text, risk=risk, route=route)
    quality["safe_update"] = safe_update
    quality["reject_reasons"] = reject_reasons
    return ChinesePolishResult(
        text=raw_text if safe_update else draft_text,
        raw_text=raw_text,
        engine=f"chinese_polish:{transcript.engine}",
        route=route,
        seconds=round(time.perf_counter() - started, 3),
        risk=risk,
        quality=quality,
        safe_update=safe_update,
        reject_reasons=reject_reasons,
        candidates=[
            {
                "source": "script_detector_whisper_cpp_auto",
                "text": risk.text,
                "seconds": risk.seconds,
                "engine": risk.engine,
                "language": risk.language,
                "language_probability": risk.probability,
                "risk": risk.risk,
            },
            {
                "source": "sensevoice_small",
                "text": raw_text,
                "seconds": transcript.seconds,
                "engine": transcript.engine,
                "language": transcript.language,
                "language_probability": transcript.language_probability,
                "risk": False,
            },
        ],
    )


def should_use_chinese_update(draft_text: str, final_text: str) -> bool:
    return not chinese_update_reject_reasons(draft_text, final_text)


def chinese_update_reject_reasons(draft_text: str, final_text: str) -> list[str]:
    draft = _normalized_whitespace(draft_text)
    final = _normalized_whitespace(final_text)
    reasons: list[str] = []
    if not final or final == draft:
        reasons.append("empty_or_unchanged")
    if is_blank_or_no_speech_transcript(final):
        reasons.append("blank_or_no_speech")
    if final and is_degenerate_transcript(final):
        reasons.append("degenerate")
    if not has_chinese_script(final):
        reasons.append("no_chinese_script")
    if len(draft) >= 40 and len(final) < max(8, len(draft) // 4):
        reasons.append("too_short")
    return reasons


def has_chinese_script(text: str) -> bool:
    return any(
        ("\u3400" <= char <= "\u4dbf")
        or ("\u4e00" <= char <= "\u9fff")
        or ("\uf900" <= char <= "\ufaff")
        for char in text
    )


def _quality(*, text: str, risk: ChineseRiskResult, route: str, error: str = "") -> dict[str, Any]:
    return {
        "chinese_risk": risk.risk,
        "risk_reasons": risk.reasons,
        "detector_text": risk.text,
        "detector_language": risk.language,
        "detector_probability": risk.probability,
        "detector_seconds": risk.seconds,
        "repeated_substring_score": repeated_substring_score(text),
        "degenerate": is_degenerate_transcript(text) if text else False,
        "blank_or_no_speech": is_blank_or_no_speech_transcript(text),
        "char_count": len(text),
        "route": route,
        "error": error,
    }


def _normalized_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
