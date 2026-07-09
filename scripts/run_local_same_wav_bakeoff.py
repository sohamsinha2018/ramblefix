from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import meaning_coverage, term_coverage_report, word_error_rate


DEFAULT_AUDIO = ROOT / "eval_runs/streaming-lab-manual/incoming/20260621-071303.16k.wav"
DEFAULT_REFERENCE_JSON = ROOT / "eval_runs/streaming-lab-manual/20260621-071303/references.json"
DEFAULT_OUTPUT_DIR = ROOT / "eval_runs/local-same-wav-bakeoff-20260621"
DEFAULT_TERMS = ["bhai", "kickass", "llm", "edit", "translate", "convert", "understand"]
ORISERVE_APEX_GGML_Q5_MODEL = ROOT / "models/oriserve-apex-ggml/ggml-apex-q5_0.bin"

DEFAULT_MODELS = [
    "whisper_cpp_server_translate",
    "whisper_cpp_translate_small",
    "whisper_cpp_auto_small",
    "mlx_whisper_tiny_transcribe",
    "mlx_whisper_large_v3_turbo_q4_transcribe",
    "mlx_whisper_large_v3_turbo_q4_translate",
    "qwen3_asr_mlx_auto",
    "qwen3_asr_mlx_hindi",
    "srota_qwen3_hinglish_mlx",
    "oriserve_hindi2hinglish_transformers",
    "oriserve_apex_transformers_mps",
    "oriserve_apex_transformers_cpu",
    "shunya_zero_stt_hinglish",
    "shunya_zero_stt_hinglish_cpu",
    "trelis_whisper_hinglish_preview_mps",
    "trelis_whisper_hinglish_preview_cpu",
    "vosk_hi_small",
    "vosk_hi_large",
    "mms_1b_hindi",
    "omnilingual_ctc_300m",
    "faster_whisper_small_auto",
    "faster_whisper_large_v3_turbo_auto",
    "parakeet_mlx",
    "nemotron35_nemo",
    "voxtral_realtime_vllm",
    "higgs_audio_v3_stt",
    "lama_ut",
]


@dataclass(frozen=True)
class TranscriptResult:
    model: str
    engine: str
    seconds: float
    wall_seconds: float
    text: str
    language: str | None = None
    language_probability: float | None = None


@dataclass(frozen=True)
class PrefixResult:
    prefix_seconds: float
    event_time_ms: int
    compute_ms: int
    text: str
    error: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local ASR candidates on one saved WAV.")
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--reference-json", type=Path, default=DEFAULT_REFERENCE_JSON)
    parser.add_argument("--reference-name", default="elevenlabs_scribe_v2")
    parser.add_argument("--reference-text", default="")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--stream-models", default="")
    parser.add_argument("--prefix-seconds", default="3,8,15,22")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--terms", default=",".join(DEFAULT_TERMS))
    args = parser.parse_args()

    audio = args.audio.expanduser().resolve()
    if not audio.exists():
        raise FileNotFoundError(audio)
    reference = args.reference_text.strip() or _load_reference(args.reference_json, args.reference_name)
    if not reference:
        raise ValueError("Missing reference text")

    models = _split_csv(args.models)
    stream_models = set(_split_csv(args.stream_models))
    prefixes = _parse_prefixes(args.prefix_seconds)
    terms = _split_csv(args.terms)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for index, model in enumerate(models, 1):
        print(f"[{index}/{len(models)}] {model}", flush=True)
        row = _run_model_with_timeout(
            model=model,
            audio=audio,
            reference=reference,
            terms=terms,
            prefixes=prefixes if model in stream_models else [],
            timeout_seconds=args.timeout_seconds,
        )
        rows.append(row)
        if row.get("error"):
            print(f"  error={row['error'][:220]}", flush=True)
        else:
            print(
                f"  {row['wall_seconds']:.3f}s wer={row['wer']} meaning={row['meaning_coverage']} "
                f"text={_short(row['text'])}",
                flush=True,
            )

    payload = {
        "audio": str(audio),
        "audio_seconds": _audio_seconds(audio),
        "reference_name": args.reference_name,
        "reference_text": reference,
        "terms": terms,
        "rows": rows,
    }
    (args.output_dir / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_dir / "results.md").write_text(_markdown(payload), encoding="utf-8")
    print(_markdown(payload))
    print(f"wrote {args.output_dir / 'results.json'}")


def _run_model_with_timeout(
    *,
    model: str,
    audio: Path,
    reference: str,
    terms: list[str],
    prefixes: list[float],
    timeout_seconds: float,
) -> dict[str, Any]:
    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue()
    process = ctx.Process(target=_worker, args=(model, str(audio), reference, terms, prefixes, queue))
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        _terminate_process_tree(process.pid, include_parent=True)
        process.join(5)
        if process.is_alive():
            _kill_process_tree(process.pid, include_parent=True)
            process.join(5)
        return {
            "model": model,
            "engine": "",
            "seconds": None,
            "wall_seconds": timeout_seconds,
            "text": "",
            "wer": None,
            "meaning_coverage": None,
            "term_coverage": None,
            "term_misses": terms,
            "prefixes": [],
            "error": f"timed out after {timeout_seconds:.1f}s",
        }
    if queue.empty():
        return {
            "model": model,
            "engine": "",
            "seconds": None,
            "wall_seconds": None,
            "text": "",
            "wer": None,
            "meaning_coverage": None,
            "term_coverage": None,
            "term_misses": terms,
            "prefixes": [],
            "error": f"worker exited with code {process.exitcode}",
        }
    return queue.get()


def _descendant_pids(parent_pid: int | None) -> list[int]:
    if not parent_pid or parent_pid <= 0:
        return []
    proc = subprocess.run(
        ["pgrep", "-P", str(parent_pid)],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode not in {0, 1}:
        return []
    children: list[int] = []
    for value in proc.stdout.split():
        try:
            child_pid = int(value)
        except ValueError:
            continue
        children.append(child_pid)
        children.extend(_descendant_pids(child_pid))
    return children


def _terminate_process_tree(parent_pid: int | None, *, include_parent: bool) -> None:
    _signal_process_tree(parent_pid, include_parent=include_parent, sig=signal.SIGTERM)
    _wait_for_exit(_descendant_pids(parent_pid), timeout_seconds=2.0)


def _kill_process_tree(parent_pid: int | None, *, include_parent: bool) -> None:
    _signal_process_tree(parent_pid, include_parent=include_parent, sig=signal.SIGKILL)


def _signal_process_tree(parent_pid: int | None, *, include_parent: bool, sig: signal.Signals) -> None:
    if not parent_pid or parent_pid <= 0:
        return
    pids = list(reversed(_descendant_pids(parent_pid)))
    if include_parent:
        pids.append(parent_pid)
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass


def _wait_for_exit(pids: list[int], *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    pending = set(pids)
    while pending and time.monotonic() < deadline:
        for pid in list(pending):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pending.discard(pid)
            except PermissionError:
                pending.discard(pid)
        if pending:
            time.sleep(0.05)


def _worker(model: str, audio_value: str, reference: str, terms: list[str], prefixes: list[float], queue: mp.Queue) -> None:
    audio = Path(audio_value)
    try:
        prefix_rows: list[dict[str, Any]] = []
        if prefixes:
            with tempfile.TemporaryDirectory(prefix="ramblefix-local-bakeoff-") as tmp_dir:
                for seconds in prefixes:
                    prefix_path = Path(tmp_dir) / f"{model}_{seconds:.1f}.wav"
                    actual_prefix_seconds = _write_prefix(audio, prefix_path, seconds)
                    started = time.perf_counter()
                    try:
                        result = _run_adapter(model, prefix_path)
                        compute_ms = round((time.perf_counter() - started) * 1000)
                        prefix_rows.append(
                            asdict(
                                PrefixResult(
                                    prefix_seconds=round(actual_prefix_seconds, 3),
                                    event_time_ms=round(actual_prefix_seconds * 1000) + compute_ms,
                                    compute_ms=compute_ms,
                                    text=result.text,
                                )
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        compute_ms = round((time.perf_counter() - started) * 1000)
                        prefix_rows.append(
                            asdict(
                                PrefixResult(
                                    prefix_seconds=round(actual_prefix_seconds, 3),
                                    event_time_ms=round(actual_prefix_seconds * 1000) + compute_ms,
                                    compute_ms=compute_ms,
                                    text="",
                                    error=f"{type(exc).__name__}: {exc}",
                                )
                            )
                        )

        started = time.perf_counter()
        result = _run_adapter(model, audio)
        wall_seconds = round(time.perf_counter() - started, 3)
        term_report = term_coverage_report(reference, result.text, terms)
        queue.put(
            {
                "model": model,
                "engine": result.engine,
                "seconds": result.seconds,
                "wall_seconds": wall_seconds,
                "language": result.language,
                "language_probability": result.language_probability,
                "text": result.text,
                "wer": round(word_error_rate(reference, result.text), 3),
                "meaning_coverage": round(meaning_coverage(reference, result.text), 3),
                "term_coverage": term_report["coverage"],
                "term_misses": term_report["misses"],
                "prefixes": prefix_rows,
                "error": "",
            }
        )
    except Exception as exc:  # noqa: BLE001
        queue.put(
            {
                "model": model,
                "engine": "",
                "seconds": None,
                "wall_seconds": None,
                "language": None,
                "language_probability": None,
                "text": "",
                "wer": None,
                "meaning_coverage": None,
                "term_coverage": None,
                "term_misses": terms,
                "prefixes": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


def _run_adapter(model: str, audio: Path) -> TranscriptResult:
    from ramblefix.external_asr import (
        transcribe_faster_whisper,
        transcribe_oriserve_hindi2hinglish,
        transcribe_parakeet_mlx,
        transcribe_qwen3_asr_mlx,
        transcribe_srota_hinglish,
        transcribe_whisper_cpp,
        transcribe_whisper_cpp_server_translate,
        transcribe_whisper_cpp_translate,
        transcribe_whisper_cpp_translate_base,
    )

    if model == "whisper_cpp_server_translate":
        tr = transcribe_whisper_cpp_server_translate(audio)
        return _from_external(model, tr)
    if model == "whisper_cpp_translate_small":
        tr = transcribe_whisper_cpp_translate(audio)
        return _from_external(model, tr)
    if model == "whisper_cpp_translate_base":
        tr = transcribe_whisper_cpp_translate_base(audio)
        return _from_external(model, tr)
    if model == "whisper_cpp_auto_small":
        tr = transcribe_whisper_cpp(audio, language="auto")
        return _from_external(model, tr)
    if model == "faster_whisper_small_auto":
        tr = transcribe_faster_whisper(audio, model="small", language=None)
        return _from_external(model, tr)
    if model == "faster_whisper_large_v3_turbo_auto":
        tr = transcribe_faster_whisper(audio, model="large-v3-turbo", language=None)
        return _from_external(model, tr)
    if model == "qwen3_asr_mlx_auto":
        tr = transcribe_qwen3_asr_mlx(audio, language=None)
        return _from_external(model, tr)
    if model == "qwen3_asr_mlx_hindi":
        tr = transcribe_qwen3_asr_mlx(audio, language="Hindi")
        return _from_external(model, tr)
    if model == "qwen3_asr_mlx_english":
        tr = transcribe_qwen3_asr_mlx(audio, language="English")
        return _from_external(model, tr)
    if model == "srota_qwen3_hinglish_mlx":
        os.environ.setdefault("RAMBLEFIX_SROTA_BACKEND", "mlx")
        tr = transcribe_srota_hinglish(audio)
        return _from_external(model, tr)
    if model == "oriserve_hindi2hinglish_transformers":
        return _run_oriserve_hindi2hinglish(model, audio)
    if model == "oriserve_apex_transformers_mps":
        return _run_hf_whisper_transformers(
            model=model,
            audio=audio,
            model_id=str(ROOT / "models/oriserve-apex-hf") if (ROOT / "models/oriserve-apex-hf").exists() else "Oriserve/Whisper-Hindi2Hinglish-Apex",
            device_preference="mps",
        )
    if model == "oriserve_apex_transformers_cpu":
        return _run_hf_whisper_transformers(
            model=model,
            audio=audio,
            model_id=str(ROOT / "models/oriserve-apex-hf") if (ROOT / "models/oriserve-apex-hf").exists() else "Oriserve/Whisper-Hindi2Hinglish-Apex",
            device_preference="cpu",
        )
    if model == "oriserve_apex_mlx":
        local_apex_mlx = ROOT / "models/oriserve-apex-mlx"
        return _run_mlx_whisper_custom(
            model=model,
            audio=audio,
            repo=str(local_apex_mlx) if local_apex_mlx.exists() else "knownsense/whisper-hindi-apex-mlx",
            task="transcribe",
            language="hi",
        )
    if model == "oriserve_hindi2hinglish_ggml":
        tr = transcribe_oriserve_hindi2hinglish(audio)
        return _from_external(model, tr)
    if model == "oriserve_hindi2hinglish_apex_ggml_q5":
        return _run_oriserve_apex_ggml_q5(model, audio, transcribe_whisper_cpp)
    if model == "shunya_zero_stt_hinglish":
        return _run_shunya_zero_stt_hinglish(model, audio)
    if model == "shunya_zero_stt_hinglish_cpu":
        return _run_hf_whisper_transformers(
            model=model,
            audio=audio,
            model_id="shunyalabs/zero-stt-hinglish",
            device_preference="cpu",
        )
    if model == "trelis_whisper_hinglish_preview_mps":
        return _run_hf_whisper_transformers(
            model=model,
            audio=audio,
            model_id="Trelis/whisper-hinglish-preview",
            device_preference="mps",
        )
    if model == "trelis_whisper_hinglish_preview_cpu":
        return _run_hf_whisper_transformers(
            model=model,
            audio=audio,
            model_id="Trelis/whisper-hinglish-preview",
            device_preference="cpu",
        )
    if model == "vosk_hi_small":
        return _run_vosk(model, audio, ROOT / "models/vosk-model-small-hi-0.22")
    if model == "vosk_hi_large":
        return _run_vosk(model, audio, ROOT / "models/vosk-model-hi-0.22")
    if model == "mms_1b_hindi":
        return _run_mms_1b_hindi(model, audio)
    if model == "omnilingual_ctc_300m":
        return _run_omnilingual_ctc_300m(model, audio)
    if model == "parakeet_mlx":
        tr = transcribe_parakeet_mlx(audio)
        return _from_external(model, tr)
    if model.startswith("mlx_whisper_"):
        return _run_mlx_whisper(model, audio)
    if model == "nemotron35_nemo":
        return _run_nemotron_nemo(model, audio)
    if model == "voxtral_realtime_vllm":
        return _run_voxtral_probe(model)
    if model == "higgs_audio_v3_stt":
        return _run_higgs_probe(model)
    if model == "lama_ut":
        raise RuntimeError("LAMA-UT is a paper pipeline; I found no packaged checkpoint/inference adapter to run locally.")
    raise ValueError(f"unknown model: {model}")


def _run_oriserve_apex_ggml_q5(model: str, audio: Path, transcribe_whisper_cpp: Any) -> TranscriptResult:
    if not ORISERVE_APEX_GGML_Q5_MODEL.exists():
        raise RuntimeError(f"missing Oriserve Apex GGML q5 model: {ORISERVE_APEX_GGML_Q5_MODEL}")
    started = time.perf_counter()
    try:
        tr = transcribe_whisper_cpp(
            audio,
            model=ORISERVE_APEX_GGML_Q5_MODEL,
            language="hi",
            timeout_seconds=30.0,
        )
        return _from_external(model, tr)
    except RuntimeError as exc:
        if not _looks_like_metal_failure(str(exc)):
            raise
        tr = transcribe_whisper_cpp(
            audio,
            model=ORISERVE_APEX_GGML_Q5_MODEL,
            language="hi",
            timeout_seconds=30.0,
            no_gpu=True,
        )
        return TranscriptResult(
            model=model,
            engine=f"{getattr(tr, 'engine', 'whisper.cpp.apex')}:cpu-fallback",
            seconds=float(getattr(tr, "seconds", time.perf_counter() - started)),
            wall_seconds=round(time.perf_counter() - started, 3),
            text=str(getattr(tr, "text", "")).strip(),
            language=getattr(tr, "language", None),
            language_probability=getattr(tr, "language_probability", None),
        )


def _looks_like_metal_failure(message: str) -> bool:
    lowered = message.lower()
    return "mtlcompilerservice" in lowered or "ggml_metal_library_init" in lowered or "failed to create library" in lowered


def _from_external(model: str, tr: Any) -> TranscriptResult:
    return TranscriptResult(
        model=model,
        engine=str(getattr(tr, "engine", model)),
        seconds=float(getattr(tr, "seconds", 0.0) or 0.0),
        wall_seconds=float(getattr(tr, "seconds", 0.0) or 0.0),
        text=str(getattr(tr, "text", "") or "").strip(),
        language=getattr(tr, "language", None),
        language_probability=getattr(tr, "language_probability", None),
    )


def _run_mlx_whisper(model: str, audio: Path) -> TranscriptResult:
    repos = {
        "mlx_whisper_tiny_transcribe": ("mlx-community/whisper-tiny", "transcribe"),
        "mlx_whisper_large_v3_turbo_q4_transcribe": ("mlx-community/whisper-large-v3-turbo-q4", "transcribe"),
        "mlx_whisper_large_v3_turbo_q4_translate": ("mlx-community/whisper-large-v3-turbo-q4", "translate"),
        "mlx_whisper_large_v3_turbo_4bit_transcribe": ("mlx-community/whisper-large-v3-turbo-4bit", "transcribe"),
        "mlx_whisper_large_v3_turbo_4bit_translate": ("mlx-community/whisper-large-v3-turbo-4bit", "translate"),
        "mlx_whisper_large_v3_turbo_8bit_transcribe": ("mlx-community/whisper-large-v3-turbo-8bit", "transcribe"),
        "mlx_whisper_large_v3_turbo_transcribe": ("mlx-community/whisper-large-v3-turbo", "transcribe"),
    }
    if model not in repos:
        raise ValueError(f"unknown MLX Whisper model: {model}")
    repo, task = repos[model]
    return _run_mlx_whisper_custom(model=model, audio=audio, repo=repo, task=task)


def _run_mlx_whisper_custom(
    *,
    model: str,
    audio: Path,
    repo: str,
    task: str,
    language: str | None = None,
) -> TranscriptResult:
    import mlx_whisper

    started = time.perf_counter()
    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=repo,
        verbose=False,
        temperature=0.0,
        condition_on_previous_text=False,
        task=task,
        language=language,
    )
    return TranscriptResult(
        model=model,
        engine=f"mlx-whisper:{repo}:{task}",
        seconds=round(time.perf_counter() - started, 3),
        wall_seconds=round(time.perf_counter() - started, 3),
        text=str(result.get("text", "")).strip(),
        language=result.get("language"),
        language_probability=None,
    )


def _run_oriserve_hindi2hinglish(model: str, audio: Path) -> TranscriptResult:
    started = time.perf_counter()
    try:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
    except ImportError as exc:
        raise RuntimeError("transformers and torch are required for the Oriserve quality probe.") from exc

    model_id = "Oriserve/Whisper-Hindi2Hinglish-Swift"
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    dtype = torch.float16 if device == "mps" else torch.float32
    asr_model = AutoModelForSpeechSeq2Seq.from_pretrained(
        model_id,
        dtype=dtype,
        low_cpu_mem_usage=True,
        use_safetensors=True,
    )
    if device == "mps":
        asr_model.to("mps")
    processor = AutoProcessor.from_pretrained(model_id)
    asr = pipeline(
        "automatic-speech-recognition",
        model=asr_model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        dtype=dtype,
        device=device,
    )
    result = asr(str(audio), generate_kwargs={"task": "transcribe"}, return_timestamps=True)
    return TranscriptResult(
        model=model,
        engine=f"transformers:{model_id}:{device}",
        seconds=round(time.perf_counter() - started, 3),
        wall_seconds=round(time.perf_counter() - started, 3),
        text=str(result.get("text", "")).strip(),
        language=None,
        language_probability=None,
    )


def _run_hf_whisper_transformers(
    *,
    model: str,
    audio: Path,
    model_id: str,
    device_preference: str,
) -> TranscriptResult:
    started = time.perf_counter()
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

    use_mps = device_preference == "mps" and torch.backends.mps.is_available()
    device = "mps" if use_mps else "cpu"
    dtype = torch.float16 if use_mps else torch.float32
    asr_model = AutoModelForSpeechSeq2Seq.from_pretrained(
        model_id,
        dtype=dtype,
        low_cpu_mem_usage=True,
        use_safetensors=True,
        local_files_only=_is_local_or_cached_model(model_id),
    )
    if use_mps:
        asr_model.to("mps")
    processor = AutoProcessor.from_pretrained(
        model_id,
        local_files_only=_is_local_or_cached_model(model_id),
    )
    asr = pipeline(
        "automatic-speech-recognition",
        model=asr_model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        dtype=dtype,
        device=device,
    )
    result = asr(
        str(audio),
        generate_kwargs={"task": "transcribe"},
        return_timestamps=False,
    )
    return TranscriptResult(
        model=model,
        engine=f"transformers:{model_id}:{device}",
        seconds=round(time.perf_counter() - started, 3),
        wall_seconds=round(time.perf_counter() - started, 3),
        text=str(result.get("text", "")).strip(),
    )


def _is_local_or_cached_model(model_id: str) -> bool:
    if Path(model_id).exists():
        return True
    cache_name = "models--" + model_id.replace("/", "--")
    return (Path.home() / ".cache/huggingface/hub" / cache_name).exists()


def _run_nemotron_nemo(model: str, audio: Path) -> TranscriptResult:
    started = time.perf_counter()
    try:
        import nemo.collections.asr as nemo_asr
    except ImportError as exc:
        raise RuntimeError(
            "nemo_toolkit[asr] is not installed. NVIDIA's official local path requires NeMo and is documented for Linux/NVIDIA runtimes."
        ) from exc
    asr_model = nemo_asr.models.ASRModel.from_pretrained("nvidia/nemotron-3.5-asr-streaming-0.6b")
    original_setup = asr_model._setup_dataloader_from_config

    def _setup_with_auto_prompt(config: Any) -> Any:
        config["default_prompt_mode"] = "auto"
        return original_setup(config)

    asr_model._setup_dataloader_from_config = _setup_with_auto_prompt
    output = asr_model.transcribe([str(audio)], target_lang="auto")
    text = _extract_nemo_text(output)
    return TranscriptResult(
        model=model,
        engine="nemo:nvidia/nemotron-3.5-asr-streaming-0.6b",
        seconds=round(time.perf_counter() - started, 3),
        wall_seconds=round(time.perf_counter() - started, 3),
        text=str(text).strip(),
    )


def _extract_nemo_text(output: Any) -> str:
    first = output[0] if isinstance(output, list) and output else output
    if isinstance(first, str):
        return first
    for attr in ("text", "pred_text"):
        value = getattr(first, attr, None)
        if value:
            return str(value)
    return str(first)


def _run_shunya_zero_stt_hinglish(model: str, audio: Path) -> TranscriptResult:
    import torch
    from transformers import pipeline

    started = time.perf_counter()
    use_mps = torch.backends.mps.is_available()
    transcriber = pipeline(
        "automatic-speech-recognition",
        model="shunyalabs/zero-stt-hinglish",
        device="mps" if use_mps else -1,
        dtype=torch.float16 if use_mps else torch.float32,
    )
    result = transcriber(str(audio), generate_kwargs={"task": "transcribe"}, return_timestamps=False)
    return TranscriptResult(
        model=model,
        engine="transformers:shunyalabs/zero-stt-hinglish",
        seconds=round(time.perf_counter() - started, 3),
        wall_seconds=round(time.perf_counter() - started, 3),
        text=str(result.get("text", "")).strip(),
    )


def _run_vosk(model: str, audio: Path, model_dir: Path) -> TranscriptResult:
    from vosk import KaldiRecognizer, Model, SetLogLevel

    if not model_dir.exists():
        raise RuntimeError(f"missing Vosk model directory: {model_dir}")
    SetLogLevel(-1)
    started = time.perf_counter()
    vosk_model = Model(str(model_dir))
    parts: list[str] = []
    with wave.open(str(audio), "rb") as reader:
        recognizer = KaldiRecognizer(vosk_model, reader.getframerate())
        recognizer.SetWords(False)
        while True:
            data = reader.readframes(4000)
            if not data:
                break
            if recognizer.AcceptWaveform(data):
                parts.append(json.loads(recognizer.Result()).get("text", ""))
        parts.append(json.loads(recognizer.FinalResult()).get("text", ""))
    text = " ".join(part for part in parts if part).strip()
    return TranscriptResult(
        model=model,
        engine=f"vosk:{model_dir.name}",
        seconds=round(time.perf_counter() - started, 3),
        wall_seconds=round(time.perf_counter() - started, 3),
        text=text,
    )


def _run_mms_1b_hindi(model: str, audio: Path) -> TranscriptResult:
    import numpy as np
    import torch
    from transformers import AutoProcessor, Wav2Vec2ForCTC

    with wave.open(str(audio), "rb") as reader:
        sample_rate = reader.getframerate()
        channels = reader.getnchannels()
        frames = reader.readframes(reader.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)

    started = time.perf_counter()
    model_id = "facebook/mms-1b-all"
    processor = AutoProcessor.from_pretrained(model_id)
    asr_model = Wav2Vec2ForCTC.from_pretrained(model_id)
    processor.tokenizer.set_target_lang("hin")
    asr_model.load_adapter("hin")
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    asr_model.to(device)
    asr_model.eval()
    inputs = processor(samples, sampling_rate=sample_rate, return_tensors="pt")
    inputs = {key: value.to(device) for key, value in inputs.items()}
    with torch.no_grad():
        logits = asr_model(**inputs).logits
    ids = torch.argmax(logits, dim=-1)[0]
    text = processor.decode(ids)
    return TranscriptResult(
        model=model,
        engine="transformers:facebook/mms-1b-all:hin",
        seconds=round(time.perf_counter() - started, 3),
        wall_seconds=round(time.perf_counter() - started, 3),
        text=str(text).strip(),
    )


def _run_omnilingual_ctc_300m(model: str, audio: Path) -> TranscriptResult:
    python = ROOT / ".venvs/omni-asr/bin/python"
    helper = ROOT / "scripts/probe_omnilingual_ctc.py"
    if not python.exists():
        raise RuntimeError(f"missing isolated Omnilingual venv: {python}")
    started = time.perf_counter()
    completed = subprocess.run(
        [str(python), str(helper), str(audio), "--model-card", "omniASR_CTC_300M"],
        cwd=str(ROOT),
        check=True,
        capture_output=True,
        text=True,
        timeout=240,
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    return TranscriptResult(
        model=model,
        engine=f"omnilingual-asr:{payload.get('model_card', 'omniASR_CTC_300M')}",
        seconds=round(time.perf_counter() - started, 3),
        wall_seconds=round(time.perf_counter() - started, 3),
        text=str(payload.get("text", "")).strip(),
    )


def _run_voxtral_probe(model: str) -> TranscriptResult:
    try:
        import vllm  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("vLLM is not installed; Voxtral Realtime currently requires vLLM serving for local inference.") from exc
    raise RuntimeError("vLLM is installed, but this repo has no Voxtral realtime websocket/client adapter yet.")


def _run_higgs_probe(model: str) -> TranscriptResult:
    try:
        import boson_multimodal  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("boson_multimodal is not installed; Higgs Audio v3 STT requires custom remote-code preprocessing.") from exc
    raise RuntimeError("boson_multimodal is installed, but this repo has no Higgs Audio v3 STT adapter yet.")


def _write_prefix(source: Path, dest: Path, seconds: float) -> float:
    with wave.open(str(source), "rb") as reader:
        params = reader.getparams()
        frame_rate = reader.getframerate()
        max_frames = reader.getnframes()
        frames = min(max_frames, max(1, int(seconds * frame_rate)))
        audio = reader.readframes(frames)
    with wave.open(str(dest), "wb") as writer:
        writer.setparams(params)
        writer.writeframes(audio)
    return frames / frame_rate if frame_rate else 0.0


def _audio_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as reader:
        return round(reader.getnframes() / reader.getframerate(), 3)


def _load_reference(path: Path, name: str) -> str:
    payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("references", [])
    for item in payload:
        if str(item.get("name")) == name:
            return str(item.get("text", "")).strip()
    return ""


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_prefixes(value: str) -> list[float]:
    return [float(item) for item in _split_csv(value)]


def _short(text: str, limit: int = 180) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Local Same-WAV ASR Bakeoff",
        "",
        f"- audio: `{payload['audio']}`",
        f"- audio seconds: `{payload['audio_seconds']}`",
        f"- reference: `{payload['reference_name']}`",
        "",
        "## Results",
        "",
        "| model | status | wall s | WER | meaning | terms | text |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload["rows"]:
        status = "error" if row.get("error") else "ok"
        wall = "" if row.get("wall_seconds") is None else f"{float(row['wall_seconds']):.3f}"
        wer = "" if row.get("wer") is None else f"{float(row['wer']):.3f}"
        meaning = "" if row.get("meaning_coverage") is None else f"{float(row['meaning_coverage']):.3f}"
        terms = "" if row.get("term_coverage") is None else f"{float(row['term_coverage']):.3f}"
        text = row.get("error") or row.get("text") or ""
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["model"]),
                    status,
                    wall,
                    wer,
                    meaning,
                    terms,
                    _escape_md(_short(text, 220)),
                ]
            )
            + " |"
        )
    streamed = [row for row in payload["rows"] if row.get("prefixes")]
    if streamed:
        lines.extend(["", "## Prefix Streaming Proxy", ""])
        for row in streamed:
            lines.append(f"### {row['model']}")
            for prefix in row["prefixes"]:
                err = prefix.get("error") or ""
                text = err or prefix.get("text", "")
                lines.append(
                    f"- `{prefix['prefix_seconds']}s` audio -> event `{prefix['event_time_ms']}ms`, "
                    f"compute `{prefix['compute_ms']}ms`: {_escape_md(_short(text, 180))}"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _escape_md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    main()
