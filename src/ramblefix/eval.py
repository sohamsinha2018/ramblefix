from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from ramblefix.asr import ACCURATE_MLX_MODEL, BALANCED_MLX_MODEL, FAST_MLX_MODEL, transcribe_audio
from ramblefix.external_asr import (
    transcribe_faster_whisper_auto,
    transcribe_faster_whisper,
    transcribe_parakeet_mlx,
    transcribe_oriserve_hindi2hinglish,
    transcribe_qwen3_asr_mlx,
    transcribe_qwen3_asr_mlx_hinglish,
    transcribe_sensevoice_small,
    transcribe_whisper_cpp,
    transcribe_whisper_cpp_server_translate,
    transcribe_whisper_cpp_translate,
    transcribe_whisper_cpp_translate_base,
    transcribe_whisperkit_cli,
)
from ramblefix.glossary import apply_glossary
from ramblefix.gemini_asr import transcribe_gemini_audio
from ramblefix.ludo_asr import transcribe_hybrid_ludo
from ramblefix.engine_router import (
    transcribe_ramblefix_engine_v1,
    transcribe_ramblefix_hinglish_v1,
    transcribe_ramblefix_multilingual_lab_v0,
)
from ramblefix.meaning_router import transcribe_meaning_router
from ramblefix.quality import repeated_substring_score
from ramblefix.tts import synthesize_with_elevenlabs


MLX_WHISPER_CORPUS_MODELS = {
    "mlx_whisper_tiny_transcribe": ("mlx-community/whisper-tiny", "transcribe"),
    "mlx_whisper_small_transcribe": ("mlx-community/whisper-small-mlx", "transcribe"),
    "mlx_whisper_large_v3_turbo_q4_transcribe": ("mlx-community/whisper-large-v3-turbo-q4", "transcribe"),
    "mlx_whisper_large_v3_turbo_q4_translate": ("mlx-community/whisper-large-v3-turbo-q4", "translate"),
    "mlx_whisper_large_v3_turbo_4bit_transcribe": ("mlx-community/whisper-large-v3-turbo-4bit", "transcribe"),
    "mlx_whisper_large_v3_turbo_4bit_translate": ("mlx-community/whisper-large-v3-turbo-4bit", "translate"),
    "mlx_whisper_large_v3_turbo_8bit_transcribe": ("mlx-community/whisper-large-v3-turbo-8bit", "transcribe"),
    "mlx_whisper_large_v3_turbo_transcribe": ("mlx-community/whisper-large-v3-turbo", "transcribe"),
}


@dataclass(frozen=True)
class EvalCase:
    name: str
    text: str
    voice: str = "Samantha"


@dataclass(frozen=True)
class EvalResult:
    case: str
    config: str
    expected: str
    actual: str
    corrected: str
    wer: float
    corrected_wer: float
    repeated_token_ratio: float
    repeated_substring_score: float
    seconds: float
    error: str | None = None


CASES = [
    EvalCase(
        name="plain_builder_prompt",
        text="I want to build a fast prompt mode for short voice notes. It should preserve names, acronyms, and constraints.",
    ),
    EvalCase(
        name="work_terms",
        text="Check whether Partner Center, Fee Admin, PCI, SOX, and Riskified are captured correctly in the transcript.",
    ),
    EvalCase(
        name="ramble",
        text="Okay, I am thinking out loud. The goal is to convert messy speech into a concise Cursor prompt without losing context.",
    ),
]

MIXED_LANGUAGE_CASES = [
    EvalCase(
        name="hinglish_prompt",
        text="Mujhe ek local voice tool banana hai that converts messy Hinglish speech into a clean Cursor prompt.",
    ),
    EvalCase(
        name="hinglish_product",
        text="Yeh tool Teams meetings mein Hindi aur English dono samjhe, and then action items nikaale.",
    ),
    EvalCase(
        name="hindi_english_terms",
        text="मुझे Partner Center, Fee Admin, PCI, SOX, aur Riskified jaise terms correctly capture karne hain.",
    ),
]

CONFIGS = [
    ("fast_auto", FAST_MLX_MODEL, None),
    ("fast_en", FAST_MLX_MODEL, "en"),
    ("fast_hi_wrong", FAST_MLX_MODEL, "hi"),
    ("balanced_auto", BALANCED_MLX_MODEL, None),
    ("balanced_en", BALANCED_MLX_MODEL, "en"),
    ("accurate_auto", ACCURATE_MLX_MODEL, None),
    ("accurate_en", ACCURATE_MLX_MODEL, "en"),
    ("accurate_hi", ACCURATE_MLX_MODEL, "hi"),
]


def run_eval(
    output_dir: str | Path = "eval_runs/latest",
    *,
    provider: str = "say",
    cases: list[EvalCase] | None = None,
    elevenlabs_api_key: str | None = None,
    external_backends: set[str] | None = None,
) -> list[EvalResult]:
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    selected_cases = cases or CASES
    results: list[EvalResult] = []
    for case in selected_cases:
        wav_path = _synthesize_case(case, out, provider=provider, elevenlabs_api_key=elevenlabs_api_key)
        for config_name, model, language in CONFIGS:
            started = time.perf_counter()
            elapsed = time.perf_counter() - started
            try:
                transcript = transcribe_audio(wav_path, model=model, language=language)
                corrected = apply_glossary(transcript.text)
                elapsed = time.perf_counter() - started
                results.append(
                    EvalResult(
                        case=case.name,
                        config=config_name,
                        expected=case.text,
                        actual=transcript.text,
                        corrected=corrected,
                        wer=word_error_rate(case.text, transcript.text),
                        corrected_wer=word_error_rate(case.text, corrected),
                        repeated_token_ratio=repeated_token_ratio(corrected),
                        repeated_substring_score=repeated_substring_score(corrected),
                        seconds=round(elapsed, 3),
                    )
                )
            except Exception as exc:
                elapsed = time.perf_counter() - started
                results.append(
                    EvalResult(
                        case=case.name,
                        config=config_name,
                        expected=case.text,
                        actual="",
                        corrected="",
                        wer=1.0,
                        corrected_wer=1.0,
                        repeated_token_ratio=0.0,
                        repeated_substring_score=0.0,
                        seconds=round(elapsed, 3),
                        error=str(exc),
                    )
                )

        for backend in sorted(external_backends or []):
            started = time.perf_counter()
            try:
                transcript = _transcribe_external_backend(backend, wav_path)
                corrected = apply_glossary(transcript.text)
                elapsed = time.perf_counter() - started
                results.append(
                    EvalResult(
                        case=case.name,
                        config=backend,
                        expected=case.text,
                        actual=transcript.text,
                        corrected=corrected,
                        wer=word_error_rate(case.text, transcript.text),
                        corrected_wer=word_error_rate(case.text, corrected),
                        repeated_token_ratio=repeated_token_ratio(corrected),
                        repeated_substring_score=repeated_substring_score(corrected),
                        seconds=round(elapsed, 3),
                    )
                )
            except Exception as exc:
                elapsed = time.perf_counter() - started
                results.append(
                    EvalResult(
                        case=case.name,
                        config=backend,
                        expected=case.text,
                        actual="",
                        corrected="",
                        wer=1.0,
                        corrected_wer=1.0,
                        repeated_token_ratio=0.0,
                        repeated_substring_score=0.0,
                        seconds=round(elapsed, 3),
                        error=str(exc),
                    )
                )

    (out / "results.json").write_text(
        json.dumps([asdict(result) for result in results], indent=2),
        encoding="utf-8",
    )
    (out / "results.md").write_text(_markdown(results), encoding="utf-8")
    return results


def run_audio_sweep(audio_path: str | Path, output_dir: str | Path = "eval_runs/audio-sweep") -> list[dict[str, object]]:
    path = Path(audio_path).expanduser().resolve()
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    for config_name, model, language in CONFIGS:
        started = time.perf_counter()
        try:
            transcript = transcribe_audio(path, model=model, language=language)
            corrected = apply_glossary(transcript.text)
            rows.append(
                {
                    "config": config_name,
                    "detected_language": transcript.language,
                    "seconds": round(time.perf_counter() - started, 3),
                    "repeat": repeated_substring_score(corrected),
                    "raw": transcript.text,
                    "corrected": corrected,
                    "error": None,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "config": config_name,
                    "detected_language": None,
                    "seconds": round(time.perf_counter() - started, 3),
                    "repeat": 0.0,
                    "raw": "",
                    "corrected": "",
                    "error": str(exc),
                }
            )

    (out / "audio_sweep.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (out / "audio_sweep.md").write_text(_audio_sweep_markdown(rows), encoding="utf-8")
    return rows


def run_corpus_eval(
    corpus_path: str | Path = "eval_corpus/ramblefix_corpus.json",
    output_dir: str | Path = "eval_runs/corpus",
    *,
    include_gemini: bool = False,
    external_backends: set[str] | None = None,
    base_backends: list[str] | None = None,
    ids: set[str] | None = None,
    row_timeout_seconds: float | None = None,
) -> list[dict[str, object]]:
    corpus_file = Path(corpus_path).expanduser().resolve()
    root = corpus_file.parent.parent
    corpus = json.loads(corpus_file.read_text(encoding="utf-8"))
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []

    def flush_rows() -> None:
        _write_corpus_outputs(out, rows)

    for item in corpus:
        if ids is not None and str(item.get("id")) not in ids:
            continue
        gold = str(item.get("gold", "")).strip()
        audio = Path(item["audio"])
        if not audio.is_absolute():
            audio = root / audio

        backends = list(base_backends) if base_backends is not None else ["hybrid_ludo", "accurate_en", "accurate_auto"]
        if include_gemini:
            backends.append("gemini_audio")
        if external_backends:
            for backend in sorted(external_backends):
                if backend not in backends:
                    backends.append(backend)

        for backend in backends:
            started = time.perf_counter()
            category = _corpus_category(item)
            try:
                with _row_timeout(row_timeout_seconds, f"{item.get('id')} {backend}"):
                    if backend == "hybrid_ludo":
                        tr = transcribe_hybrid_ludo(audio)
                        text = tr.text
                        meta = {"engine": tr.engine, "candidates": [c.__dict__ for c in tr.candidates]}
                    elif backend == "gemini_audio":
                        tr = transcribe_gemini_audio(audio)
                        text = tr.text
                        meta = {"engine": tr.engine}
                    elif backend == "parakeet_mlx":
                        tr = transcribe_parakeet_mlx(audio)
                        text = tr.text
                        meta = {"engine": tr.engine}
                    elif backend == "qwen3_asr_mlx":
                        tr = transcribe_qwen3_asr_mlx(audio)
                        text = tr.text
                        meta = {"engine": tr.engine, "language": tr.language}
                    elif backend == "qwen3_asr_mlx_hinglish":
                        tr = transcribe_qwen3_asr_mlx_hinglish(audio)
                        text = tr.text
                        meta = {"engine": tr.engine, "language": tr.language}
                    elif backend == "oriserve_hindi2hinglish_ggml":
                        tr = transcribe_oriserve_hindi2hinglish(audio)
                        text = tr.text
                        meta = {
                            "engine": tr.engine,
                            "language": tr.language,
                            "language_probability": tr.language_probability,
                        }
                    elif backend == "sensevoice_small":
                        tr = transcribe_sensevoice_small(audio)
                        text = tr.text
                        meta = {"engine": tr.engine, "language": tr.language}
                    elif backend == "faster_whisper":
                        tr = transcribe_faster_whisper(audio)
                        text = tr.text
                        meta = {"engine": tr.engine}
                    elif backend == "faster_whisper_auto":
                        tr = transcribe_faster_whisper_auto(audio)
                        text = tr.text
                        meta = {"engine": tr.engine}
                    elif backend == "whisper_cpp":
                        tr = transcribe_whisper_cpp(audio)
                        text = tr.text
                        meta = {"engine": tr.engine, "language": tr.language, "language_probability": tr.language_probability}
                    elif backend == "whisper_cpp_translate":
                        tr = transcribe_whisper_cpp_translate(audio)
                        text = tr.text
                        meta = {"engine": tr.engine, "language": tr.language, "language_probability": tr.language_probability}
                    elif backend == "whisper_cpp_server_translate":
                        tr = transcribe_whisper_cpp_server_translate(audio)
                        text = tr.text
                        meta = {"engine": tr.engine}
                    elif backend == "whisper_cpp_translate_base":
                        tr = transcribe_whisper_cpp_translate_base(audio)
                        text = tr.text
                        meta = {"engine": tr.engine, "language": tr.language, "language_probability": tr.language_probability}
                    elif backend == "meaning_router":
                        tr = transcribe_meaning_router(audio)
                        text = tr.text
                        meta = {
                            "engine": tr.engine,
                            "language": tr.language,
                            "route": tr.route,
                            "candidates": [candidate.__dict__ for candidate in tr.candidates],
                        }
                    elif backend == "ramblefix_engine_v1":
                        tr = transcribe_ramblefix_engine_v1(audio)
                        text = tr.text
                        meta = {
                            "engine": tr.engine,
                            "route": tr.route,
                            "risk_reasons": tr.risk_reasons,
                            "candidates": [candidate.__dict__ for candidate in tr.candidates],
                        }
                    elif backend == "ramblefix_hinglish_v1":
                        tr = transcribe_ramblefix_hinglish_v1(audio)
                        text = tr.text
                        meta = {
                            "engine": tr.engine,
                            "route": tr.route,
                            "risk_reasons": tr.risk_reasons,
                            "candidates": [candidate.__dict__ for candidate in tr.candidates],
                        }
                    elif backend == "ramblefix_multilingual_lab_v0":
                        tr = transcribe_ramblefix_multilingual_lab_v0(audio)
                        text = tr.text
                        meta = {
                            "engine": tr.engine,
                            "route": tr.route,
                            "risk_reasons": tr.risk_reasons,
                            "candidates": [candidate.__dict__ for candidate in tr.candidates],
                        }
                    elif backend == "whisperkit_cli":
                        tr = transcribe_whisperkit_cli(audio)
                        text = tr.text
                        meta = {"engine": tr.engine}
                    elif backend in MLX_WHISPER_CORPUS_MODELS:
                        text, meta = _transcribe_mlx_whisper_corpus_model(audio, backend)
                    elif backend == "accurate_en":
                        tr = transcribe_audio(audio, model=ACCURATE_MLX_MODEL, language="en")
                        text = tr.text
                        meta = {"engine": tr.engine, "language": tr.language}
                    else:
                        tr = transcribe_audio(audio, model=ACCURATE_MLX_MODEL, language=None)
                        text = tr.text
                        meta = {"engine": tr.engine, "language": tr.language}
                raw_text = text
                text = apply_glossary(text)
                if text != raw_text:
                    meta = {**meta, "raw_actual": raw_text}
                term_report = term_coverage_report(gold, text, _corpus_terms(item))
                rows.append(
                    {
                        "id": item["id"],
                        "category": category,
                        "backend": backend,
                        "audio": str(audio),
                        "gold": gold,
                        "actual": text,
                        "wer": word_error_rate(gold, text) if gold else None,
                        "meaning_loss": meaning_loss(gold, text) if gold else None,
                        "meaning_coverage": meaning_coverage(gold, text) if gold else None,
                        "term_coverage": term_report["coverage"],
                        "term_hits": term_report["hits"],
                        "term_misses": term_report["misses"],
                        "term_terms": term_report["terms"],
                        "repeat": repeated_substring_score(text),
                        "seconds": round(time.perf_counter() - started, 3),
                        "meta": meta,
                        "error": None,
                    }
                )
                flush_rows()
            except Exception as exc:
                term_report = term_coverage_report(gold, "", _corpus_terms(item))
                rows.append(
                    {
                        "id": item["id"],
                        "category": category,
                        "backend": backend,
                        "audio": str(audio),
                        "gold": gold,
                        "actual": "",
                        "wer": 1.0 if gold else None,
                        "meaning_loss": 1.0 if gold else None,
                        "meaning_coverage": 0.0 if gold else None,
                        "term_coverage": term_report["coverage"],
                        "term_hits": [],
                        "term_misses": term_report["terms"],
                        "term_terms": term_report["terms"],
                        "repeat": 0.0,
                        "seconds": round(time.perf_counter() - started, 3),
                        "meta": {},
                        "error": repr(exc),
                    }
                )
                flush_rows()

        benchmarks = item.get("benchmarks", {})
        if isinstance(benchmarks, dict):
            for name, text_value in benchmarks.items():
                text = str(text_value).strip()
                if not text:
                    continue
                term_report = term_coverage_report(gold, text, _corpus_terms(item))
                rows.append(
                    {
                        "id": item["id"],
                        "category": category,
                        "backend": f"benchmark_{name}",
                        "audio": str(audio),
                        "gold": gold,
                        "actual": text,
                        "wer": word_error_rate(gold, text) if gold else None,
                        "meaning_loss": meaning_loss(gold, text) if gold else None,
                        "meaning_coverage": meaning_coverage(gold, text) if gold else None,
                        "term_coverage": term_report["coverage"],
                        "term_hits": term_report["hits"],
                        "term_misses": term_report["misses"],
                        "term_terms": term_report["terms"],
                        "repeat": repeated_substring_score(text),
                        "seconds": 0.0,
                        "meta": {"source": name},
                        "error": None,
                    }
                )
                flush_rows()

    flush_rows()
    return rows


def _transcribe_external_backend(backend: str, audio: str | Path):
    if backend in MLX_WHISPER_CORPUS_MODELS:
        text, meta = _transcribe_mlx_whisper_corpus_model(audio, backend)
        return type("ExternalResult", (), {"text": text, "engine": meta["engine"], "seconds": meta["seconds"]})()
    if backend == "parakeet_mlx":
        return transcribe_parakeet_mlx(audio)
    if backend == "qwen3_asr_mlx":
        return transcribe_qwen3_asr_mlx(audio)
    if backend == "oriserve_hindi2hinglish_ggml":
        return transcribe_oriserve_hindi2hinglish(audio)
    if backend == "sensevoice_small":
        return transcribe_sensevoice_small(audio)
    if backend == "faster_whisper":
        return transcribe_faster_whisper(audio)
    if backend == "faster_whisper_auto":
        return transcribe_faster_whisper_auto(audio)
    if backend == "whisper_cpp":
        return transcribe_whisper_cpp(audio)
    if backend == "whisper_cpp_translate":
        return transcribe_whisper_cpp_translate(audio)
    if backend == "whisper_cpp_server_translate":
        return transcribe_whisper_cpp_server_translate(audio)
    if backend == "whisper_cpp_translate_base":
        return transcribe_whisper_cpp_translate_base(audio)
    if backend == "meaning_router":
        return transcribe_meaning_router(audio)
    if backend == "ramblefix_engine_v1":
        return transcribe_ramblefix_engine_v1(audio)
    if backend == "ramblefix_hinglish_v1":
        return transcribe_ramblefix_hinglish_v1(audio)
    if backend == "ramblefix_multilingual_lab_v0":
        return transcribe_ramblefix_multilingual_lab_v0(audio)
    if backend == "whisperkit_cli":
        return transcribe_whisperkit_cli(audio)
    raise ValueError(f"Unsupported external backend: {backend}")


def _transcribe_mlx_whisper_corpus_model(audio: str | Path, backend: str) -> tuple[str, dict[str, object]]:
    repo, task = MLX_WHISPER_CORPUS_MODELS[backend]
    started = time.perf_counter()
    try:
        import mlx_whisper
    except ImportError as exc:
        raise RuntimeError("mlx-whisper is not installed") from exc
    result = mlx_whisper.transcribe(
        str(Path(audio).expanduser().resolve()),
        path_or_hf_repo=repo,
        verbose=False,
        temperature=0.0,
        condition_on_previous_text=False,
        task=task,
    )
    return (
        str(result.get("text") or "").strip(),
        {
            "engine": f"mlx-whisper:{repo}:{task}",
            "language": result.get("language"),
            "seconds": round(time.perf_counter() - started, 3),
            "device_path": "MLX/GPU",
        },
    )


@contextmanager
def _row_timeout(seconds: float | None, label: str):
    if seconds is None or seconds <= 0:
        yield
        return

    def timeout_handler(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"{label} timed out after {seconds:.3f}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _write_corpus_outputs(out: Path, rows: list[dict[str, object]]) -> None:
    _write_text_atomic(out / "corpus_results.json", json.dumps(rows, indent=2, ensure_ascii=False))
    _write_text_atomic(out / "corpus_results.md", _corpus_markdown(rows))


def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def word_error_rate(expected: str, actual: str) -> float:
    ref = _tokens(expected)
    hyp = _tokens(actual)
    if not ref:
        return 0.0 if not hyp else 1.0

    previous = list(range(len(hyp) + 1))
    for i, ref_token in enumerate(ref, start=1):
        current = [i]
        for j, hyp_token in enumerate(hyp, start=1):
            substitution = previous[j - 1] + (ref_token != hyp_token)
            insertion = current[j - 1] + 1
            deletion = previous[j] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return round(previous[-1] / len(ref), 3)


def meaning_coverage(expected: str, actual: str) -> float:
    expected_concepts = _concepts(expected)
    if not expected_concepts:
        return 1.0
    actual_concepts = _concepts(actual)
    return round(len(expected_concepts & actual_concepts) / len(expected_concepts), 3)


def meaning_loss(expected: str, actual: str) -> float:
    return round(1.0 - meaning_coverage(expected, actual), 3)


def term_coverage_report(expected: str, actual: str, terms: object | None = None) -> dict[str, object]:
    expected_terms = _expected_terms(expected, terms)
    if not expected_terms:
        return {"coverage": None, "terms": [], "hits": [], "misses": []}
    hits = [term for term in expected_terms if _term_hit(actual, term)]
    misses = [term for term in expected_terms if term not in hits]
    return {
        "coverage": round(len(hits) / len(expected_terms), 3),
        "terms": expected_terms,
        "hits": hits,
        "misses": misses,
    }


def repeated_token_ratio(text: str) -> float:
    tokens = _tokens(text)
    if not tokens:
        return 0.0
    repeated = sum(1 for previous, current in zip(tokens, tokens[1:]) if previous == current)
    return round(repeated / len(tokens), 3)


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower(), flags=re.UNICODE)


TERM_ALIASES: dict[str, tuple[str, ...]] = {
    "api": ("api", "एपीआई"),
    "asr": ("asr", "एएसआर"),
    "bcom": ("bcom",),
    "bpd": ("bpd",),
    "chatgpt": ("chatgpt", "chat gpt", "चैटजीपीटी"),
    "claude": ("claude", "क्लॉड"),
    "clean": ("clean", "क्लीन"),
    "cloud": ("cloud", "क्लाउड"),
    "codex": ("codex", "कोडेक्स", "कोडिक्स"),
    "company": ("company", "कंपनी"),
    "cursor": ("cursor", "करसर"),
    "data": ("data", "डेटा", "डाटा"),
    "document": ("document", "documents", "डॉक्यूमेंट", "डॉक्यूमेंट्स"),
    "documents": ("documents", "document", "डॉक्यूमेंट", "डॉक्यूमेंट्स"),
    "duplicate": ("duplicate", "duplicates", "डुप्लिकेट"),
    "english": ("english", "इंग्लिश"),
    "font": ("font", "fonts", "फॉन्ट", "फ़ॉन्ट"),
    "hindi": ("hindi", "हिंदी"),
    "hinglish": ("hinglish", "हिंग्लिश"),
    "growth": ("growth", "ग्रोथ"),
    "libreoffice": ("libreoffice", "libre office", "लिबरऑफिस", "लिबर ऑफिस"),
    "linux": ("linux", "लिनक्स"),
    "local": ("local", "लोकल"),
    "lose": ("lose", "loose", "लूज"),
    "mat": ("mat", "मत", "do not", "don't", "dont", "not"),
    "meaning": ("meaning", "मीनिंग"),
    "pci": ("pci", "पीसीआई"),
    "pii": ("pii", "पीआईआई"),
    "prd": ("prd", "पीआरडी"),
    "prompt": ("prompt", "प्रॉम्प्ट", "प्रॉंप्ट"),
    "problem": ("problem", "problems", "प्रॉब्लम"),
    "sdk": ("sdk", "एसडीके"),
    "section": ("section", "sections", "सेक्शन"),
    "skill": ("skill", "skills", "स्किल"),
    "slide": ("slide", "slides", "स्लाइड"),
    "task": ("task", "tasks", "टास्क"),
    "tasks": ("tasks", "task", "टास्क"),
    "text": ("text", "टेक्स्ट"),
    "title": ("title", "टाइटल"),
    "sox": ("sox", "सॉक्स"),
    "teams": ("teams", "टीम्स"),
    "tool": ("tool", "टूल"),
    "toolbar": ("toolbar", "tool bar", "टूलबार"),
    "tutorial": ("tutorial", "tutorials", "ट्यूटोरियल"),
    "wispr": ("wispr", "whisper", "विस्पर"),
}

ROMAN_HINDI_TERM_ALIASES: dict[str, tuple[str, ...]] = {
    # Meaning-first eval should not penalize harmless Roman-Hindi spelling
    # choices when the spoken term is clearly preserved.
    "aata": ("ata",),
    "bangal": ("bangaal", "bengal"),
    "bangaal": ("bangal", "bengal"),
    "hoon": ("hun",),
    "jahan": ("jahaan", "jaha", "jahaa"),
    "jahaan": ("jahan", "jaha", "jahaa"),
    "kaafi": ("kafi", "kaaphi", "kaphi"),
    "kafi": ("kaafi", "kaaphi", "kaphi"),
    "kaaphi": ("kaafi", "kafi", "kaphi"),
    "karein": ("karen",),
    "karen": ("karein",),
    "vaise": ("waise",),
    "waise": ("vaise",),
    "wahan": ("vahaan", "vahan"),
}

ANCHOR_STOP_WORDS = {
    "about",
    "after",
    "again",
    "against",
    "also",
    "because",
    "before",
    "being",
    "between",
    "could",
    "does",
    "doing",
    "done",
    "from",
    "have",
    "into",
    "like",
    "main",
    "make",
    "more",
    "need",
    "okay",
    "only",
    "other",
    "really",
    "right",
    "should",
    "something",
    "still",
    "that",
    "then",
    "there",
    "these",
    "thing",
    "things",
    "this",
    "very",
    "want",
    "what",
    "when",
    "where",
    "whether",
    "which",
    "with",
    "without",
    "would",
    "your",
    "aap",
    "aage",
    "aata",
    "agar",
    "aur",
    "bana",
    "banana",
    "banado",
    "banae",
    "banaye",
    "batao",
    "bhai",
    "bahiya",
    "cheeze",
    "cheezein",
    "hai",
    "hain",
    "haan",
    "hoon",
    "hua",
    "hum",
    "isko",
    "jaldi",
    "jahan",
    "jahaan",
    "kaise",
    "kar",
    "kare",
    "karke",
    "karna",
    "kya",
    "kyun",
    "mat",
    "mein",
    "mujhe",
    "nahi",
    "sake",
    "sakte",
    "samajh",
    "samajhna",
    "taki",
    "tha",
    "toh",
    "vaise",
    "waise",
    "wahan",
    "yaar",
    "yeh",
}


CONCEPT_PATTERNS: list[tuple[str, str]] = [
    ("understand", r"\b(understand|samajh\w*|samajna|samajhna|getting to understand)\b|समझ"),
    ("idea", r"\b(idea|soch)\b"),
    ("execution", r"\b(execution|execute|hakikat)\b"),
    ("fast", r"\b(fast|quickly|jaldi|jald[yi]|bahut jaldi)\b"),
    ("build", r"\b(build|banaye|banana|banaye|kare)\b|बनाये|करें"),
    ("solve", r"\b(solve|samadhan)\b|सॉल्फ|solve"),
    ("real_problem", r"\b(real problem|problem)\b"),
    ("fun", r"\b(fun|maza)\b"),
    ("waste", r"\b(waste|time waste|faltu)\b"),
    ("how", r"\b(how|kaise|kaisse|kaisi)\b|कैसे"),
    ("tell", r"\b(tell|batao|badao)\b|बताओ"),
    ("move_forward", r"\b(aage|badh|badhe|move forward|aage badh|aake bahar)\b|आगे|बढ़"),
    ("things", r"\b(cheez|cheeze|cheezein|things)\b|चीज"),
    ("brother", r"\b(bahiya|bhaiya|bhai)\b|भैया"),
    ("zero_to_one", r"\b(zero to one|0 to 1)\b"),
    ("results", r"\b(result|results|meaningful results)\b"),
    ("recording_eval", r"\b(recording|audio|eval|evals|transcript|transcription)\b"),
    ("loading", r"\b(loading)\b"),
    ("share", r"\b(share|sharing)\b"),
]


def _concepts(text: str) -> set[str]:
    normalized = text.lower()
    concepts = {
        concept
        for concept, pattern in CONCEPT_PATTERNS
        if re.search(pattern, normalized, flags=re.UNICODE)
    }
    tokens = set(_tokens(normalized))
    # Preserve only searchable anchors. Ignore filler and roman-Hindi grammar,
    # otherwise translated English summaries get unfairly penalized.
    stop = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "from",
        "in",
        "on",
        "it",
        "is",
        "this",
        "that",
        "then",
        "with",
        "for",
        "can",
        "do",
        "we",
        "you",
        "i",
        "me",
        "my",
        "so",
        "ok",
        "okay",
        "aa",
        "hai",
        "ki",
        "ka",
        "ke",
        "ko",
        "yeh",
        "toh",
        "ab",
        "aap",
        "aar",
        "yaar",
        "mujhe",
        "samajna",
        "samajhna",
        "samajh",
        "kaise",
        "kaisse",
        "kare",
        "isko",
        "tha",
        "tak",
        "sakte",
        "sakti",
        "shaasakti",
        "jaldi",
        "quickly",
        "because",
        "otherwise",
        "want",
        "getting",
        "going",
        "would",
        "could",
    }
    concepts.update(token for token in tokens if len(token) > 4 and token not in stop)
    return concepts


def _corpus_terms(item: dict[str, object]) -> object | None:
    return item.get("critical") or item.get("critical_terms") or item.get("terms") or item.get("anchors")


def _expected_terms(expected: str, terms: object | None = None) -> list[str]:
    explicit = _explicit_terms(terms)
    if explicit:
        return explicit
    return _auto_anchor_terms(expected)


def _explicit_terms(terms: object | None) -> list[str]:
    if terms is None:
        return []
    if isinstance(terms, str):
        raw_terms = re.split(r"[,;\n]+", terms)
    elif isinstance(terms, list):
        raw_terms = [str(term) for term in terms]
    else:
        return []
    return _dedupe_terms(_term_key(term) for term in raw_terms if str(term).strip())


def _auto_anchor_terms(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9+.#'-]*", text)
    anchors: list[str] = []
    for token in tokens:
        term = _term_key(token)
        if not term or term in ANCHOR_STOP_WORDS:
            continue
        if len(term) >= 4 or token.isupper() or term in TERM_ALIASES:
            anchors.append(term)
    return _dedupe_terms(anchors)


def _term_key(term: str) -> str:
    normalized = re.sub(r"[^a-z0-9+# ]+", " ", term.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _dedupe_terms(terms: object) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for term in terms:
        value = str(term).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _term_hit(actual: str, term: str) -> bool:
    raw = actual.lower()
    normalized = _normalize_for_term_match(actual)
    aliases = _term_aliases(term)
    return any(_contains_term(raw, normalized, alias) for alias in aliases)


def _term_aliases(term: str) -> tuple[str, ...]:
    aliases = list(TERM_ALIASES.get(term, (term,)))
    aliases.extend(ROMAN_HINDI_TERM_ALIASES.get(term, ()))
    for alias in list(aliases):
        alias_norm = _normalize_for_term_match(alias).strip()
        if not alias_norm or " " in alias_norm:
            continue
        if alias_norm.endswith("ies") and len(alias_norm) > 4:
            aliases.append(f"{alias_norm[:-3]}y")
        if alias_norm.endswith("es") and len(alias_norm) > 3:
            aliases.append(alias_norm[:-2])
        if alias_norm.endswith("s") and len(alias_norm) > 3:
            aliases.append(alias_norm[:-1])
        else:
            aliases.append(f"{alias_norm}s")
    return tuple(_dedupe_terms(aliases))


def _normalize_for_term_match(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9+# ]+", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized)
    return f" {normalized.strip()} "


def _contains_term(raw: str, normalized: str, alias: str) -> bool:
    if re.search(r"[\u0900-\u097f]", alias):
        return alias.lower() in raw
    alias_norm = _normalize_for_term_match(alias).strip()
    if not alias_norm:
        return False
    return f" {alias_norm} " in normalized


def _corpus_category(item: dict[str, object]) -> str:
    category = str(item.get("category", "")).strip().lower()
    if category:
        return category
    gold = str(item.get("gold", "")).lower()
    has_devanagari = bool(re.search(r"[\u0900-\u097f]", gold))
    has_roman_hindi = bool(
        re.search(r"\b(mujhe|samajh|kaise|kare|batao|bhai|bahiya|aage|jaldi|taki|yeh|hai)\b", gold)
    )
    has_english = bool(re.search(r"\b(the|what|how|build|solve|problem|question|cursor|chatgpt)\b", gold))
    if has_devanagari and not has_english:
        return "hindi"
    if has_devanagari or (has_roman_hindi and has_english):
        return "hinglish"
    return "english"


def _synthesize_case(
    case: EvalCase,
    output_dir: Path,
    *,
    provider: str,
    elevenlabs_api_key: str | None,
) -> Path:
    if provider == "elevenlabs":
        mp3 = output_dir / f"{case.name}.mp3"
        wav = output_dir / f"{case.name}.wav"
        synthesize_with_elevenlabs(case.text, mp3, api_key=elevenlabs_api_key)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "16000", "-ac", "1", str(wav)], check=True)
        return wav
    if provider != "say":
        raise ValueError(f"Unsupported eval provider: {provider}")

    aiff = output_dir / f"{case.name}.aiff"
    wav = output_dir / f"{case.name}.wav"
    subprocess.run(["say", "-v", case.voice, "-o", str(aiff), case.text], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), str(wav)], check=True)
    return wav


def _markdown(results: list[EvalResult]) -> str:
    lines = [
        "# RambleFix ASR Eval",
        "",
        "| Case | Config | Raw WER | Corrected WER | Token Repeat | Substring Repeat | Seconds | Actual | Corrected |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for result in results:
        actual = (result.error or result.actual).replace("|", "\\|").replace("\n", " ")
        corrected = result.corrected.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {result.case} | {result.config} | {result.wer:.3f} | "
            f"{result.corrected_wer:.3f} | {result.repeated_token_ratio:.3f} | "
            f"{result.repeated_substring_score:.3f} | {result.seconds:.3f} | {actual} | {corrected} |"
        )
    return "\n".join(lines) + "\n"


def _audio_sweep_markdown(rows: list[dict[str, object]]) -> str:
    lines = [
        "# RambleFix Audio Sweep",
        "",
        "| Config | Detected | Repeat | Seconds | Corrected |",
        "|---|---:|---:|---:|---|",
    ]
    for row in rows:
        corrected = str(row["error"] or row["corrected"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {row['config']} | {row['detected_language']} | {float(row['repeat']):.3f} | "
            f"{float(row['seconds']):.3f} | {corrected} |"
        )
    return "\n".join(lines) + "\n"


def _corpus_markdown(rows: list[dict[str, object]]) -> str:
    lines = [
        "# RambleFix Corpus Eval",
        "",
        "Lower literal WER and meaning loss are better. Higher coverage is better. Term coverage checks whether English/work anchors from the gold transcript survived without adding phrase-specific runtime repairs.",
        "",
        "## Category Summary",
        "",
        "| Category | Backend | Clips | Avg Literal WER | Avg Meaning Loss | Avg Coverage | Avg Term Coverage | Avg Seconds |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for summary in _corpus_summary(rows):
        term_coverage = "" if summary["term_coverage"] is None else f"{float(summary['term_coverage']):.3f}"
        lines.append(
            f"| {summary['category']} | {summary['backend']} | {summary['count']} | "
            f"{summary['wer']:.3f} | {summary['meaning_loss']:.3f} | "
            f"{summary['meaning_coverage']:.3f} | {term_coverage} | {summary['seconds']:.3f} |"
        )

    comparisons = _hybrid_benchmark_comparisons(rows, "benchmark_wispr")
    if comparisons:
        lines.extend(
            [
                "",
                "## Hybrid vs Wispr Flow",
                "",
                "Negative deltas mean RambleFix is better. Positive deltas mean Wispr is better.",
                "",
                "| Category | Clips | WER Delta | Meaning Loss Delta | Coverage Delta | Term Coverage Delta |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in comparisons:
            lines.append(
                f"| {row['category']} | {row['count']} | {row['wer_delta']:.3f} | "
                f"{row['meaning_loss_delta']:.3f} | {row['coverage_delta']:.3f} | {row['term_coverage_delta']:.3f} |"
            )

    lines.extend(
        [
            "",
            "## Per-Clip Results",
            "",
            "| ID | Category | Backend | Literal WER | Meaning Loss | Coverage | Term Coverage | Missed Terms | Repeat | Seconds | Actual | Error |",
            "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|---|",
        ]
    )
    for row in rows:
        wer = "" if row["wer"] is None else f"{float(row['wer']):.3f}"
        meaning_loss_value = "" if row.get("meaning_loss") is None else f"{float(row['meaning_loss']):.3f}"
        coverage = "" if row.get("meaning_coverage") is None else f"{float(row['meaning_coverage']):.3f}"
        term_coverage = "" if row.get("term_coverage") is None else f"{float(row['term_coverage']):.3f}"
        missed_terms = ", ".join(str(term) for term in row.get("term_misses", [])).replace("|", "\\|")
        actual = str(row["actual"]).replace("|", "\\|").replace("\n", " ")
        error = str(row["error"] or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {row['id']} | {row.get('category', '')} | {row['backend']} | {wer} | {meaning_loss_value} | {coverage} | {term_coverage} | {missed_terms} | {float(row['repeat']):.3f} | "
            f"{float(row['seconds']):.3f} | {actual} | {error} |"
        )
    return "\n".join(lines) + "\n"


def _corpus_summary(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        if row.get("wer") is None or row.get("meaning_loss") is None or row.get("meaning_coverage") is None:
            continue
        key = (str(row.get("category", "unknown")), str(row["backend"]))
        grouped.setdefault(key, []).append(row)

    summaries: list[dict[str, object]] = []
    for (category, backend), bucket in sorted(grouped.items()):
        summaries.append(
            {
                "category": category,
                "backend": backend,
                "count": len(bucket),
                "wer": _avg(bucket, "wer"),
                "meaning_loss": _avg(bucket, "meaning_loss"),
                "meaning_coverage": _avg(bucket, "meaning_coverage"),
                "term_coverage": _avg_optional(bucket, "term_coverage"),
                "seconds": _avg(bucket, "seconds"),
            }
        )
    return summaries


def _hybrid_benchmark_comparisons(rows: list[dict[str, object]], benchmark_backend: str) -> list[dict[str, object]]:
    by_id_backend = {(str(row["id"]), str(row["backend"])): row for row in rows}
    grouped: dict[str, list[tuple[dict[str, object], dict[str, object]]]] = {}
    for (item_id, backend), hybrid in by_id_backend.items():
        if backend != "hybrid_ludo":
            continue
        benchmark = by_id_backend.get((item_id, benchmark_backend))
        if not benchmark:
            continue
        grouped.setdefault(str(hybrid.get("category", "unknown")), []).append((hybrid, benchmark))

    comparisons: list[dict[str, object]] = []
    for category, pairs in sorted(grouped.items()):
        comparisons.append(
            {
                "category": category,
                "count": len(pairs),
                "wer_delta": _avg_delta(pairs, "wer"),
                "meaning_loss_delta": _avg_delta(pairs, "meaning_loss"),
                "coverage_delta": _avg_delta(pairs, "meaning_coverage"),
                "term_coverage_delta": _avg_delta(pairs, "term_coverage"),
            }
        )
    return comparisons


def _avg(rows: list[dict[str, object]], field: str) -> float:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return round(sum(values) / len(values), 3) if values else 0.0


def _avg_optional(rows: list[dict[str, object]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return round(sum(values) / len(values), 3) if values else None


def _avg_delta(pairs: list[tuple[dict[str, object], dict[str, object]]], field: str) -> float:
    values = [
        float(hybrid[field]) - float(benchmark[field])
        for hybrid, benchmark in pairs
        if hybrid.get(field) is not None and benchmark.get(field) is not None
    ]
    return round(sum(values) / len(values), 3) if values else 0.0
