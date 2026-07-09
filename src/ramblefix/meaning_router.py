from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from ramblefix.asr import ACCURATE_MLX_MODEL, transcribe_audio
from ramblefix.external_asr import transcribe_whisper_cpp_translate
from ramblefix.quality import repeated_substring_score


@dataclass(frozen=True)
class MeaningCandidate:
    source: str
    text: str
    score: float
    seconds: float
    language: str | None = None
    language_probability: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class MeaningTranscript:
    text: str
    language: str | None
    engine: str
    seconds: float
    candidates: list[MeaningCandidate]
    route: str


def transcribe_meaning_router(audio_path: str | Path) -> MeaningTranscript:
    """Meaning-first local ASR router.

    Product intent: emit usable work text, usually English, while keeping raw
    candidates for audit. This is not a verbatim Hindi transcription path.
    """
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    candidates: list[MeaningCandidate] = []

    primary = _candidate_from_whisper_cpp_translate(path)
    candidates.append(primary)

    if _should_run_multilingual_fallback(primary):
        candidates.append(_candidate_from_mlx_auto(path))

    valid = [candidate for candidate in candidates if not candidate.error and candidate.text.strip()]
    if not valid:
        return MeaningTranscript(
            text="unclear",
            language=None,
            engine="meaning_router:unclear",
            seconds=round(time.perf_counter() - started, 3),
            candidates=candidates,
            route="no_valid_candidate",
        )

    best = max(valid, key=lambda candidate: candidate.score)
    if best.score < 0.25 or repeated_substring_score(best.text) >= 0.3:
        return MeaningTranscript(
            text="unclear",
            language=best.language,
            engine="meaning_router:unclear",
            seconds=round(time.perf_counter() - started, 3),
            candidates=candidates,
            route="low_confidence",
        )

    return MeaningTranscript(
        text=best.text.strip(),
        language=best.language,
        engine=f"meaning_router:{best.source}",
        seconds=round(time.perf_counter() - started, 3),
        candidates=candidates,
        route=_route_name(best, candidates),
    )


def _candidate_from_whisper_cpp_translate(path: Path) -> MeaningCandidate:
    started = time.perf_counter()
    try:
        transcript = transcribe_whisper_cpp_translate(path)
        return MeaningCandidate(
            source="whisper_cpp_translate",
            text=transcript.text,
            score=_score_meaning_candidate(transcript.text, source="whisper_cpp_translate"),
            seconds=transcript.seconds,
            language=transcript.language,
            language_probability=transcript.language_probability,
        )
    except Exception as exc:
        return MeaningCandidate(
            source="whisper_cpp_translate",
            text="",
            score=-10,
            seconds=round(time.perf_counter() - started, 3),
            error=repr(exc),
        )


def _candidate_from_mlx_auto(path: Path) -> MeaningCandidate:
    started = time.perf_counter()
    try:
        transcript = transcribe_audio(path, model=ACCURATE_MLX_MODEL, language=None)
        return MeaningCandidate(
            source="mlx_accurate_auto",
            text=transcript.text,
            score=_score_meaning_candidate(transcript.text, source="mlx_accurate_auto"),
            seconds=round(time.perf_counter() - started, 3),
            language=transcript.language,
        )
    except Exception as exc:
        return MeaningCandidate(
            source="mlx_accurate_auto",
            text="",
            score=-10,
            seconds=round(time.perf_counter() - started, 3),
            error=repr(exc),
        )


def _should_run_multilingual_fallback(primary: MeaningCandidate) -> bool:
    if primary.error or not primary.text.strip():
        return True
    if repeated_substring_score(primary.text) >= 0.2:
        return True
    if primary.score < 0.55:
        return True
    # Hindi detection alone is not enough to run the slower fallback. In
    # meaning mode, whisper.cpp translation is often the best product output.
    # Escalate only when the fast output itself looks weak.
    if primary.language and primary.language != "en" and primary.score < 0.55:
        return True
    return False


def _score_meaning_candidate(text: str, *, source: str) -> float:
    stripped = text.strip()
    if not stripped:
        return -10.0

    words = re.findall(r"\w+", stripped, flags=re.UNICODE)
    latin_words = re.findall(r"[A-Za-z][A-Za-z0-9.+#-]*", stripped)
    score = 0.0

    if len(words) >= 3:
        score += 0.2
    if len(words) >= 8:
        score += 0.2
    if len(words) >= 18:
        score += 0.1

    latin_ratio = len(latin_words) / max(1, len(words))
    if latin_ratio >= 0.8:
        score += 0.25
    elif latin_ratio >= 0.35:
        score += 0.15

    if re.search(r"\b(api|asr|cursor|codex|document|format|impress|linux|office|prompt|screen|slide|tutorial|window)\b", stripped, re.I):
        score += 0.2

    if source == "whisper_cpp_translate":
        score += 0.15

    if re.search(r"[\u0900-\u097f]", stripped):
        # Devanagari can still be useful as a fallback/raw candidate, but the
        # default product output prefers English/Roman work text.
        score += 0.05
    if re.search(r"[\u0600-\u06ff]", stripped):
        score -= 0.35

    if re.fullmatch(r"\s*(unclear|thank you|thanks)\.?\s*", stripped, flags=re.I):
        score -= 0.7
    score -= min(repeated_substring_score(stripped), 1.0) * 1.2
    return round(score, 3)


def _route_name(best: MeaningCandidate, candidates: list[MeaningCandidate]) -> str:
    if len(candidates) == 1:
        return "primary_only"
    if best.source == "whisper_cpp_translate":
        return "primary_after_multilingual_check"
    return "multilingual_fallback_selected"
