from __future__ import annotations

import re
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ramblefix.external_asr import (
    transcribe_sensevoice_small,
    transcribe_oriserve_hindi2hinglish,
    transcribe_srota_hinglish,
    transcribe_whisper_cpp,
    transcribe_whisper_cpp_server_translate,
    transcribe_whisper_cpp_translate,
)
from ramblefix.quality import is_degenerate_transcript, repeated_substring_score


@dataclass(frozen=True)
class EngineCandidate:
    source: str
    text: str
    seconds: float
    engine: str
    error: str | None = None
    language: str | None = None
    language_probability: float | None = None
    risk: bool = False


@dataclass(frozen=True)
class RoutedEngineTranscript:
    text: str
    engine: str
    seconds: float
    route: str
    candidates: list[EngineCandidate]
    risk_reasons: list[str]


def transcribe_ramblefix_engine_v1(audio_path: str | Path) -> RoutedEngineTranscript:
    """RambleFix engine stack: fast local foreground plus Hinglish finalizer.

    This intentionally avoids benchmark gold labels. It routes using only local
    ASR outputs: a fast meaning candidate and a script-risk detector candidate.
    """
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    candidates: list[EngineCandidate] = []

    fast = _fast_candidate(path)
    candidates.append(fast)

    risk_reasons = _fast_output_risk_reasons(fast.text, fast.error)

    detector = _script_detector_candidate(path)
    candidates.append(detector)
    risk_reasons.extend(_script_detector_risk_reasons(detector.text, detector.error))

    hinglish: EngineCandidate | None = None
    if risk_reasons:
        hinglish = _hinglish_candidate(path)
        candidates.append(hinglish)

    selected = _select_candidate(fast, hinglish)
    route = "fast_only"
    if hinglish is not None:
        route = "hinglish_selected" if selected.source.endswith("_hinglish") else "fast_after_hinglish_check"

    return RoutedEngineTranscript(
        text=selected.text.strip(),
        engine=f"ramblefix_engine_v1:{selected.source}",
        seconds=round(time.perf_counter() - started, 3),
        route=route,
        candidates=candidates,
        risk_reasons=sorted(set(risk_reasons)),
    )


def transcribe_ramblefix_hinglish_v1(audio_path: str | Path) -> RoutedEngineTranscript:
    """Explicit Hinglish/code-switch mode.

    Oriserve's roman-Hinglish Whisper fine-tune is the measured quality path
    for local Hindi+English dictation. The fast local server is only a safety
    fallback for empty, failed, or degenerate Hinglish outputs.
    """
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    risk_reasons: list[str] = []

    with ThreadPoolExecutor(max_workers=2) as executor:
        hinglish_future = executor.submit(_hinglish_candidate, path)
        fast_future = executor.submit(_fast_candidate, path)
        hinglish = hinglish_future.result()
        fast = fast_future.result()

    candidates: list[EngineCandidate] = [hinglish, fast]

    if hinglish.error:
        risk_reasons.append("hinglish_error")
    if not hinglish.text.strip():
        risk_reasons.append("hinglish_empty")
    if is_degenerate_transcript(hinglish.text):
        risk_reasons.append("hinglish_degenerate")

    if risk_reasons:
        if not fast.text.strip():
            risk_reasons.append("fast_empty")
        if is_degenerate_transcript(fast.text):
            risk_reasons.append("fast_degenerate")

    detector: EngineCandidate | None = None
    if risk_reasons and not _is_usable_candidate(fast):
        detector = _script_detector_candidate(path)
        candidates.append(detector)
        if detector.error:
            risk_reasons.append("detector_error")
        if not detector.text.strip():
            risk_reasons.append("detector_empty")
        if is_degenerate_transcript(detector.text):
            risk_reasons.append("detector_degenerate")

    selected = _select_hinglish_candidate(hinglish, fast, detector)
    if not risk_reasons:
        route = "hinglish_selected"
    elif selected.source.endswith("_hinglish"):
        route = "hinglish_selected_after_check"
    elif selected.source.startswith("fast_"):
        route = "fast_fallback"
    elif selected.source.startswith("script_detector_"):
        route = "verbatim_fallback"
    else:
        route = "fallback"

    return RoutedEngineTranscript(
        text=selected.text.strip(),
        engine=f"ramblefix_hinglish_v1:{selected.source}",
        seconds=round(time.perf_counter() - started, 3),
        route=route,
        candidates=candidates,
        risk_reasons=sorted(set(risk_reasons)),
    )


def transcribe_ramblefix_multilingual_lab_v0(audio_path: str | Path) -> RoutedEngineTranscript:
    """Experimental local multilingual engine.

    This is deliberately eval-only. It is allowed to test pure Hindi and pure
    Chinese, but it must not be used as the production hotkey default until it
    beats the fixed corpora without English regressions.
    """
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    candidates: list[EngineCandidate] = []

    with ThreadPoolExecutor(max_workers=2) as executor:
        fast_future = executor.submit(_fast_candidate, path)
        detector_future = executor.submit(_script_detector_candidate, path)
        fast = fast_future.result()
        detector = detector_future.result()

    candidates.extend([fast, detector])
    risk_reasons = _fast_output_risk_reasons(fast.text, fast.error)
    detector_reasons = _script_detector_risk_reasons(detector.text, detector.error)
    risk_reasons.extend(detector_reasons)

    language = (detector.language or "").lower()
    if _is_hindi_like_detector(detector):
        finalizer = _hinglish_candidate(path)
        candidates.append(finalizer)
        selected = _select_hinglish_candidate(finalizer, fast, detector)
        route = "lab_hindi_selected" if selected is finalizer else "lab_hindi_fast_or_detector_fallback"
    elif _is_chinese_like_detector(detector):
        finalizer = _chinese_candidate(path, detector)
        candidates.append(finalizer)
        selected = _select_chinese_candidate(finalizer, fast, detector)
        route = "lab_chinese_selected" if selected is finalizer else "lab_chinese_fast_or_detector_fallback"
    elif detector.error and risk_reasons:
        selected = _select_nonempty_fallback(fast, detector)
        route = "lab_fast_after_detector_error"
    else:
        selected = fast
        route = "lab_fast_english_or_default"

    if language:
        risk_reasons.append(f"detector_language:{language}")

    return RoutedEngineTranscript(
        text=selected.text.strip(),
        engine=f"ramblefix_multilingual_lab_v0:{selected.source}",
        seconds=round(time.perf_counter() - started, 3),
        route=route,
        candidates=candidates,
        risk_reasons=sorted(set(risk_reasons)),
    )


def _fast_candidate(path: Path) -> EngineCandidate:
    started = time.perf_counter()
    try:
        tr = transcribe_whisper_cpp_server_translate(
            path,
            timeout_seconds=_optional_float_env("RAMBLEFIX_FAST_SERVER_TIMEOUT_SECONDS", default=8.0),
        )
        return EngineCandidate(
            source="fast_server_translate",
            text=tr.text,
            seconds=tr.seconds,
            engine=tr.engine,
        )
    except Exception as server_exc:
        if _env_truthy("RAMBLEFIX_SKIP_WHISPER_CPP_PROCESS_FALLBACK", default=False):
            return EngineCandidate(
                source="fast_failed",
                text="",
                seconds=round(time.perf_counter() - started, 3),
                engine="none",
                error=f"server_failed:{server_exc!r}; process_skipped",
            )
        try:
            tr = transcribe_whisper_cpp_translate(path)
            return EngineCandidate(
                source="fast_process_translate",
                text=tr.text,
                seconds=tr.seconds,
                engine=tr.engine,
                error=f"server_failed:{server_exc!r}",
            )
        except Exception as process_exc:
            return EngineCandidate(
                source="fast_failed",
                text="",
                seconds=round(time.perf_counter() - started, 3),
                engine="none",
                error=f"server_failed:{server_exc!r}; process_failed:{process_exc!r}",
            )


def _script_detector_candidate(path: Path) -> EngineCandidate:
    started = time.perf_counter()
    try:
        tr = transcribe_whisper_cpp(path, language="auto")
        return EngineCandidate(
            source="script_detector_whisper_cpp_auto",
            text=tr.text,
            seconds=tr.seconds,
            engine=tr.engine,
            language=tr.language,
            risk=_has_non_latin_script(tr.text),
        )
    except Exception as exc:
        return EngineCandidate(
            source="script_detector_failed",
            text="",
            seconds=round(time.perf_counter() - started, 3),
            engine="none",
            error=repr(exc),
            risk=True,
        )


def _srota_candidate(path: Path) -> EngineCandidate:
    started = time.perf_counter()
    try:
        tr = transcribe_srota_hinglish(path)
        return EngineCandidate(
            source="srota_hinglish",
            text=tr.text,
            seconds=tr.seconds,
            engine=tr.engine,
            language=tr.language,
        )
    except Exception as exc:
        return EngineCandidate(
            source="srota_failed",
            text="",
            seconds=round(time.perf_counter() - started, 3),
            engine="none",
            error=repr(exc),
        )


def _oriserve_candidate(path: Path) -> EngineCandidate:
    started = time.perf_counter()
    try:
        tr = transcribe_oriserve_hindi2hinglish(path)
        return EngineCandidate(
            source="oriserve_hinglish",
            text=tr.text,
            seconds=tr.seconds,
            engine=tr.engine,
            language=tr.language,
            language_probability=tr.language_probability,
        )
    except Exception as exc:
        return EngineCandidate(
            source="oriserve_failed",
            text="",
            seconds=round(time.perf_counter() - started, 3),
            engine="none",
            error=repr(exc),
        )


def _sensevoice_candidate(path: Path, *, language: str = "auto") -> EngineCandidate:
    started = time.perf_counter()
    try:
        tr = transcribe_sensevoice_small(path, language=language)
        return EngineCandidate(
            source="sensevoice_small",
            text=tr.text,
            seconds=tr.seconds,
            engine=tr.engine,
            language=tr.language,
        )
    except Exception as exc:
        return EngineCandidate(
            source="sensevoice_failed",
            text="",
            seconds=round(time.perf_counter() - started, 3),
            engine="none",
            error=repr(exc),
        )


def _hinglish_candidate(path: Path) -> EngineCandidate:
    backend = os.environ.get("RAMBLEFIX_HINGLISH_FINALIZER_BACKEND", "oriserve").strip().lower()
    if backend == "srota":
        return _srota_candidate(path)
    return _oriserve_candidate(path)


def _chinese_candidate(path: Path, detector: EngineCandidate) -> EngineCandidate:
    backend = os.environ.get("RAMBLEFIX_CHINESE_LAB_BACKEND", "sensevoice").strip().lower()
    if backend in {"sensevoice", "sensevoice_small", "funasr"}:
        candidate = _sensevoice_candidate(path, language=os.environ.get("RAMBLEFIX_SENSEVOICE_LANGUAGE", "auto"))
        if _is_usable_candidate(candidate):
            return candidate
        if backend != "sensevoice":
            return candidate
    if backend in {"whisper_cpp_zh", "whisper_cpp", "auto", "sensevoice"}:
        try:
            tr = transcribe_whisper_cpp(path, language="zh")
            return EngineCandidate(
                source="whisper_cpp_zh",
                text=tr.text,
                seconds=tr.seconds,
                engine=tr.engine,
                language=tr.language,
                language_probability=tr.language_probability,
            )
        except Exception as exc:
            if detector.text.strip():
                return EngineCandidate(
                    source="script_detector_chinese_fallback",
                    text=detector.text,
                    seconds=detector.seconds,
                    engine=detector.engine,
                    error=f"whisper_cpp_zh_failed:{exc!r}",
                    language=detector.language,
                    language_probability=detector.language_probability,
                )
            return EngineCandidate(
                source="whisper_cpp_zh_failed",
                text="",
                seconds=0.0,
                engine="none",
                error=repr(exc),
            )
    return detector


def _fast_output_risk_reasons(text: str, error: str | None) -> list[str]:
    reasons: list[str] = []
    stripped = text.strip()
    words = re.findall(r"\w+", stripped, flags=re.UNICODE)
    if error:
        reasons.append("fast_error")
    if not stripped:
        reasons.append("fast_empty")
    if len(words) <= 3:
        reasons.append("fast_too_short")
    if repeated_substring_score(stripped) >= 0.2:
        reasons.append("fast_repetition")
    if re.search(r"\b(speaking in foreign language|unclear|inaudible)\b", stripped, re.I):
        reasons.append("fast_unclear_marker")
    return reasons


def _script_detector_risk_reasons(text: str, error: str | None) -> list[str]:
    reasons: list[str] = []
    if error:
        reasons.append("detector_error")
    if _has_devanagari(text):
        reasons.append("detector_devanagari")
    if _has_arabic(text):
        reasons.append("detector_arabic")
    if _has_chinese(text):
        reasons.append("detector_chinese")
    return reasons


def _has_non_latin_script(text: str) -> bool:
    return _has_devanagari(text) or _has_arabic(text) or _has_chinese(text)


def _has_devanagari(text: str) -> bool:
    return bool(re.search(r"[\u0900-\u097f]", text))


def _has_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06ff]", text))


def _has_chinese(text: str) -> bool:
    return bool(
        re.search(
            r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002a6df\U0002a700-\U0002b73f\U0002b740-\U0002b81f\U0002b820-\U0002ceaf\U0002ceb0-\U0002ebef]",
            text,
        )
    )


def _is_hindi_like_detector(detector: EngineCandidate) -> bool:
    language = (detector.language or "").lower()
    return language in {"hi", "ur", "hindi", "urdu"} or _has_devanagari(detector.text) or _has_arabic(detector.text)


def _is_chinese_like_detector(detector: EngineCandidate) -> bool:
    language = (detector.language or "").lower()
    return language in {"zh", "zh-cn", "zh-tw", "cmn", "yue", "chinese", "mandarin", "cantonese"} or _has_chinese(detector.text)


def _select_candidate(fast: EngineCandidate, srota: EngineCandidate | None) -> EngineCandidate:
    if srota is None:
        return fast
    if srota.error or not srota.text.strip():
        return fast
    if repeated_substring_score(srota.text) >= 0.25:
        return fast
    if _looks_substantially_empty(srota.text) and not _looks_substantially_empty(fast.text):
        return fast
    return srota


def _select_chinese_candidate(
    primary: EngineCandidate,
    fast_fallback: EngineCandidate,
    detector_fallback: EngineCandidate,
) -> EngineCandidate:
    if _is_usable_candidate(primary):
        return primary
    if _is_usable_candidate(detector_fallback):
        return detector_fallback
    if _is_usable_candidate(fast_fallback):
        return fast_fallback
    if primary.text.strip():
        return primary
    if detector_fallback.text.strip():
        return detector_fallback
    return fast_fallback


def _select_nonempty_fallback(primary: EngineCandidate, fallback: EngineCandidate) -> EngineCandidate:
    if _is_usable_candidate(primary):
        return primary
    if _is_usable_candidate(fallback):
        return fallback
    if primary.text.strip():
        return primary
    return fallback


def _select_hinglish_candidate(
    primary: EngineCandidate,
    fast_fallback: EngineCandidate,
    detector_fallback: EngineCandidate | None,
) -> EngineCandidate:
    if _is_usable_candidate(primary):
        return primary
    for candidate in (fast_fallback, detector_fallback):
        if candidate is not None and _is_usable_candidate(candidate):
            return candidate
    if primary.text.strip():
        return primary
    for candidate in (fast_fallback, detector_fallback):
        if candidate is not None and candidate.text.strip():
            return candidate
    return primary


def _is_usable_candidate(candidate: EngineCandidate) -> bool:
    return not candidate.error and bool(candidate.text.strip()) and not is_degenerate_transcript(candidate.text)


def _looks_substantially_empty(text: str) -> bool:
    words = re.findall(r"\w+", text.strip(), flags=re.UNICODE)
    return len(words) <= 2


def _env_truthy(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _optional_float_env(name: str, *, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default
