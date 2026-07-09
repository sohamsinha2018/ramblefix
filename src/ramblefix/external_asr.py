from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from pathlib import Path
import os
import re
import signal
import subprocess
import tempfile

import requests

from ramblefix.config import (
    DEFAULT_EXTERNAL_ASR_TIMEOUT_SECONDS,
    DEFAULT_ORISERVE_GGML_MODEL,
    DEFAULT_QWEN3_ASR_MLX_HINGLISH_MODEL,
    DEFAULT_QWEN3_ASR_MLX_MODEL,
    DEFAULT_WHISPER_CPP_BASE_MODEL,
    DEFAULT_WHISPER_CPP_BINARY,
    DEFAULT_WHISPER_CPP_SERVER_URL,
    DEFAULT_WHISPER_CPP_SMALL_MODEL,
)
from ramblefix.quality import wav_silence_metrics
_FASTER_WHISPER_CACHE: dict[tuple[str, str, str], object] = {}
_QWEN3_ASR_MLX_SESSIONS: dict[str, object] = {}
_QWEN3_ASR_TRANSFORMERS_SESSIONS: dict[tuple[str, str, str], object] = {}
_ORISERVE_HINGLISH_ASR: object | None = None
_ORISERVE_HINGLISH_DEVICE: str | None = None
_ORISERVE_HINGLISH_LOCK = threading.RLock()
_SENSEVOICE_SMALL_MODEL: object | None = None
_SENSEVOICE_SMALL_LOCK = threading.RLock()


@dataclass(frozen=True)
class ExternalTranscript:
    text: str
    engine: str
    seconds: float
    language: str | None = None
    language_probability: float | None = None


def transcribe_parakeet_mlx(
    audio_path: str | Path,
    *,
    model: str = "mlx-community/parakeet-tdt-0.6b-v3",
) -> ExternalTranscript:
    """Transcribe with the Parakeet MLX engine used by Local Whisper/OpenWhispr-style stacks.

    Parakeet v3 is useful as a fast local English baseline. It is not expected
    to solve Hindi/Hinglish because the official model supports English plus
    European languages, not Hindi.
    """
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    try:
        from parakeet_mlx import from_pretrained
    except ImportError as exc:
        raise RuntimeError("parakeet-mlx is not installed. Run `pip install parakeet-mlx`.") from exc

    model_obj = from_pretrained(model)
    result = model_obj.transcribe(str(path), chunk_duration=120.0, overlap_duration=15.0)
    text = _extract_text(result)
    return ExternalTranscript(
        text=text,
        engine=f"parakeet-mlx:{model}",
        seconds=round(time.perf_counter() - started, 3),
    )


def transcribe_qwen3_asr_mlx(
    audio_path: str | Path,
    *,
    model: str = DEFAULT_QWEN3_ASR_MLX_MODEL,
    language: str | None = None,
) -> ExternalTranscript:
    """Transcribe with Qwen3-ASR MLX.

    Keep this optional until it proves useful on the Hinglish public/gold
    buckets. The current package exposes a Session API, so cache sessions to
    avoid measuring model load on every clip.
    """
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    try:
        from mlx_qwen3_asr import Session
    except ImportError as exc:
        raise RuntimeError("mlx-qwen3-asr is not installed. Run `pip install mlx-qwen3-asr`.") from exc

    session = _QWEN3_ASR_MLX_SESSIONS.get(model)
    if session is None:
        session = Session(model=model)
        _QWEN3_ASR_MLX_SESSIONS[model] = session
    max_new_tokens = _optional_int_env("RAMBLEFIX_QWEN_ASR_MLX_MAX_NEW_TOKENS")
    result = session.transcribe(str(path), language=language, max_new_tokens=max_new_tokens)
    text = _extract_text(result)
    return ExternalTranscript(
        text=text,
        engine=f"mlx-qwen3-asr:{model}",
        seconds=round(time.perf_counter() - started, 3),
        language=getattr(result, "language", None),
    )


def transcribe_qwen3_asr_mlx_hinglish(audio_path: str | Path) -> ExternalTranscript:
    """Transcribe with a Hinglish-specialized Qwen3-ASR fine-tune if loadable."""
    return transcribe_qwen3_asr_mlx(audio_path, model=DEFAULT_QWEN3_ASR_MLX_HINGLISH_MODEL)


def warm_oriserve_hindi2hinglish() -> None:
    """Load the Oriserve Hinglish Whisper fine-tune into the resident process."""
    backend = os.environ.get("RAMBLEFIX_ORISERVE_BACKEND", "auto").strip().lower()
    if backend in {"auto", "ggml", "whisper_cpp", "whisper-cpp"} and _oriserve_ggml_model_path().exists():
        return
    _oriserve_hindi2hinglish_pipeline()


def transcribe_oriserve_hindi2hinglish(audio_path: str | Path) -> ExternalTranscript:
    """Transcribe with Oriserve's Whisper Hindi-to-Hinglish fine-tune.

    Prefer the local whisper.cpp/Metal GGML runtime when the converted model is
    available. Fall back to the older local Transformers/MPS path only when the
    GGML model is missing or explicitly disabled.
    """
    backend = os.environ.get("RAMBLEFIX_ORISERVE_BACKEND", "auto").strip().lower()
    errors: list[str] = []
    if backend in {"auto", "ggml", "whisper_cpp", "whisper-cpp"}:
        try:
            return transcribe_oriserve_hindi2hinglish_ggml(audio_path)
        except Exception as exc:
            errors.append(f"ggml={type(exc).__name__}: {exc}")
            if backend in {"ggml", "whisper_cpp", "whisper-cpp"}:
                raise
    if backend in {"auto", "transformers", "mps", "torch"}:
        try:
            return _transcribe_oriserve_hindi2hinglish_transformers(audio_path)
        except Exception as exc:
            errors.append(f"transformers={type(exc).__name__}: {exc}")
            raise RuntimeError("; ".join(errors)) from exc
    raise RuntimeError(f"unsupported RAMBLEFIX_ORISERVE_BACKEND={backend!r}")


def transcribe_oriserve_hindi2hinglish_ggml(audio_path: str | Path) -> ExternalTranscript:
    """Run Oriserve's Hinglish Whisper-base fine-tune through whisper.cpp/Metal."""
    model = _oriserve_ggml_model_path()
    if not model.exists():
        raise RuntimeError(f"missing Oriserve GGML model: {model}")
    timeout = _optional_float_env("RAMBLEFIX_ORISERVE_GGML_TIMEOUT_SECONDS", default=8.0)
    language = os.environ.get("RAMBLEFIX_ORISERVE_GGML_LANGUAGE", "hi")
    used_cpu_fallback = False
    try:
        transcript = transcribe_whisper_cpp(
            audio_path,
            model=model,
            language=language,
            timeout_seconds=timeout,
        )
    except RuntimeError as exc:
        if not _is_whisper_cpp_metal_failure(str(exc)):
            raise
        used_cpu_fallback = True
        transcript = transcribe_whisper_cpp(
            audio_path,
            model=model,
            language=language,
            timeout_seconds=timeout,
            no_gpu=True,
        )
    engine_suffix = ":cpu-fallback" if used_cpu_fallback else ""
    return ExternalTranscript(
        text=transcript.text,
        engine=f"whisper.cpp.oriserve{engine_suffix}:{model.name}",
        seconds=transcript.seconds,
        language=transcript.language,
        language_probability=transcript.language_probability,
    )


def _is_whisper_cpp_metal_failure(message: str) -> bool:
    lowered = message.lower()
    return (
        "mtlcompilerservice" in lowered
        or "ggml_metal_library_init" in lowered
        or "failed to create library" in lowered
        or "cannot run the operation" in lowered
    )


def _transcribe_oriserve_hindi2hinglish_transformers(audio_path: str | Path) -> ExternalTranscript:
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    asr = _oriserve_hindi2hinglish_pipeline()
    result = asr(str(path), generate_kwargs={"task": "transcribe"}, return_timestamps=False)
    text = str(result.get("text", "") if isinstance(result, dict) else result).strip()
    return ExternalTranscript(
        text=text,
        engine=f"transformers:Oriserve/Whisper-Hindi2Hinglish-Swift:{_ORISERVE_HINGLISH_DEVICE or 'unknown'}",
        seconds=round(time.perf_counter() - started, 3),
    )


def _oriserve_ggml_model_path() -> Path:
    env_value = os.environ.get("RAMBLEFIX_ORISERVE_GGML_MODEL", "").strip()
    if env_value:
        return Path(env_value).expanduser()
    default = Path(DEFAULT_ORISERVE_GGML_MODEL).expanduser()
    if default.is_absolute():
        return default
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / default


def _oriserve_hindi2hinglish_pipeline() -> object:
    global _ORISERVE_HINGLISH_ASR, _ORISERVE_HINGLISH_DEVICE
    with _ORISERVE_HINGLISH_LOCK:
        if _ORISERVE_HINGLISH_ASR is not None:
            return _ORISERVE_HINGLISH_ASR
        try:
            import torch
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
        except ImportError as exc:
            raise RuntimeError("transformers and torch are required for Oriserve Hinglish ASR.") from exc

        model_id = "Oriserve/Whisper-Hindi2Hinglish-Swift"
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        dtype = torch.float16 if device == "mps" else torch.float32
        asr_model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_id,
            dtype=dtype,
            low_cpu_mem_usage=True,
            use_safetensors=True,
            local_files_only=True,
        )
        if device == "mps":
            asr_model.to("mps")
        processor = AutoProcessor.from_pretrained(model_id, local_files_only=True)
        _ORISERVE_HINGLISH_DEVICE = device
        _ORISERVE_HINGLISH_ASR = pipeline(
            "automatic-speech-recognition",
            model=asr_model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            dtype=dtype,
            device=device,
        )
        return _ORISERVE_HINGLISH_ASR


def transcribe_qwen3_asr_transformers(
    audio_path: str | Path,
    *,
    model: str = DEFAULT_QWEN3_ASR_MLX_MODEL,
    language: str | None = None,
    local_files_only: bool | None = None,
) -> ExternalTranscript:
    """Transcribe with the official qwen-asr Transformers backend.

    This is the non-MLX path for Linux/headless scoring. It is slower on CPU
    but avoids requiring a Metal device and can use the same cached Qwen/Srota
    model weights with outbound network blocked.
    """
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    _ensure_numba_cache_dir()
    try:
        import torch
        from qwen_asr import Qwen3ASRModel
    except ImportError as exc:
        raise RuntimeError("qwen-asr is not installed. Run `pip install qwen-asr`.") from exc

    device_map = os.environ.get("RAMBLEFIX_QWEN_ASR_DEVICE_MAP")
    if not device_map:
        device_map = "auto" if torch.cuda.is_available() else "cpu"
    dtype_name = os.environ.get("RAMBLEFIX_QWEN_ASR_DTYPE")
    if not dtype_name:
        dtype_name = "bfloat16" if torch.cuda.is_available() else "float32"
    dtype = getattr(torch, dtype_name, torch.float32)
    local_only = _env_truthy("RAMBLEFIX_QWEN_ASR_LOCAL_ONLY", default=True)
    if local_files_only is not None:
        local_only = local_files_only

    cache_key = (model, device_map, dtype_name)
    session = _QWEN3_ASR_TRANSFORMERS_SESSIONS.get(cache_key)
    if session is None:
        session = Qwen3ASRModel.from_pretrained(
            model,
            dtype=dtype,
            device_map=device_map,
            local_files_only=local_only,
            max_inference_batch_size=int(os.environ.get("RAMBLEFIX_QWEN_ASR_BATCH_SIZE", "1")),
        )
        _QWEN3_ASR_TRANSFORMERS_SESSIONS[cache_key] = session

    result = session.transcribe(str(path), language=language)
    first = result[0] if isinstance(result, list) and result else result
    text = _extract_text(first)
    detected_language = getattr(first, "language", None)
    return ExternalTranscript(
        text=text,
        engine=f"qwen-asr.transformers:{model}",
        seconds=round(time.perf_counter() - started, 3),
        language=detected_language,
    )


def transcribe_qwen3_asr_transformers_hinglish(audio_path: str | Path) -> ExternalTranscript:
    """Faithful Hinglish/code-switch finalizer using qwen-asr."""
    language = os.environ.get("RAMBLEFIX_QWEN_ASR_HINGLISH_LANGUAGE")
    if language is not None and language.strip().lower() in {"", "auto", "none"}:
        language = None
    return transcribe_qwen3_asr_transformers(
        audio_path,
        model=DEFAULT_QWEN3_ASR_MLX_HINGLISH_MODEL,
        language=language,
    )


def transcribe_srota_hinglish(audio_path: str | Path) -> ExternalTranscript:
    """Best available local Srota/Qwen Hinglish path.

    Default order keeps the fast MLX path on Apple Silicon, then falls back to
    the official qwen-asr Transformers backend for Linux/headless runs.
    Set RAMBLEFIX_SROTA_BACKEND=mlx or qwen_asr to force one path.
    """
    server_url = os.environ.get("RAMBLEFIX_SROTA_SERVER_URL", "").strip()
    if server_url:
        try:
            return transcribe_srota_server(audio_path, url=server_url)
        except Exception:
            if _env_truthy("RAMBLEFIX_SROTA_SERVER_REQUIRED", default=False):
                raise

    backend = os.environ.get("RAMBLEFIX_SROTA_BACKEND", "auto").strip().lower()
    errors: list[str] = []
    if backend in {"auto", "mlx", "mlx_qwen3_asr"}:
        try:
            return transcribe_qwen3_asr_mlx_hinglish(audio_path)
        except Exception as exc:
            errors.append(f"mlx={type(exc).__name__}: {exc}")
            if backend in {"mlx", "mlx_qwen3_asr"}:
                raise
    if backend in {"auto", "qwen", "qwen_asr", "transformers"}:
        try:
            return transcribe_qwen3_asr_transformers_hinglish(audio_path)
        except Exception as exc:
            errors.append(f"qwen_asr={type(exc).__name__}: {exc}")
            if backend in {"qwen", "qwen_asr", "transformers"}:
                raise
    raise RuntimeError("all Srota Hinglish backends failed: " + "; ".join(errors))


def transcribe_srota_server(
    audio_path: str | Path,
    *,
    url: str,
    timeout_seconds: float | None = None,
) -> ExternalTranscript:
    """Transcribe through a resident Srota/Qwen sidecar."""
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    timeout = timeout_seconds
    if timeout is None:
        timeout = float(os.environ.get("RAMBLEFIX_SROTA_SERVER_TIMEOUT_SECONDS", "60"))
    endpoint = url.rstrip("/") + "/transcribe"
    response = requests.post(endpoint, json={"audio_path": str(path)}, timeout=timeout)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Srota server returned non-JSON response: {response.text[:500]}") from exc
    return ExternalTranscript(
        text=str(payload.get("text", "")).strip(),
        engine=str(payload.get("engine", "srota.server")),
        seconds=round(time.perf_counter() - started, 3),
        language=payload.get("language"),
        language_probability=payload.get("language_probability"),
    )


def transcribe_sensevoice_small(
    audio_path: str | Path,
    *,
    language: str = "auto",
) -> ExternalTranscript:
    """Transcribe with SenseVoiceSmall through FunASR.

    This is a lab-only multilingual challenger for Chinese/English and other
    mixed-language experiments. It is intentionally not part of the production
    hotkey path until same-WAV evals prove a win.
    """
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    model = _sensevoice_small_model()
    result = model.generate(
        input=str(path),
        cache={},
        language=language,
        use_itn=True,
        batch_size_s=float(os.environ.get("RAMBLEFIX_SENSEVOICE_BATCH_SIZE_S", "60")),
        merge_vad=True,
        merge_length_s=float(os.environ.get("RAMBLEFIX_SENSEVOICE_MERGE_LENGTH_S", "15")),
    )
    text = _extract_text(result)
    return ExternalTranscript(
        text=text,
        engine=f"funasr.sensevoice:{os.environ.get('RAMBLEFIX_SENSEVOICE_MODEL', 'iic/SenseVoiceSmall')}",
        seconds=round(time.perf_counter() - started, 3),
        language=language,
    )


def _sensevoice_small_model() -> object:
    global _SENSEVOICE_SMALL_MODEL
    with _SENSEVOICE_SMALL_LOCK:
        if _SENSEVOICE_SMALL_MODEL is not None:
            return _SENSEVOICE_SMALL_MODEL
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError("funasr is not installed. Run `pip install funasr`.") from exc

        model_id = os.environ.get("RAMBLEFIX_SENSEVOICE_MODEL", "iic/SenseVoiceSmall")
        vad_model = os.environ.get("RAMBLEFIX_SENSEVOICE_VAD_MODEL", "fsmn-vad")
        device = os.environ.get("RAMBLEFIX_SENSEVOICE_DEVICE", "cpu")
        kwargs = {
            "model": model_id,
            "vad_model": vad_model,
            "vad_kwargs": {"max_single_segment_time": int(os.environ.get("RAMBLEFIX_SENSEVOICE_MAX_SEGMENT_MS", "30000"))},
            "device": device,
            "disable_update": True,
        }
        _SENSEVOICE_SMALL_MODEL = AutoModel(**kwargs)
        return _SENSEVOICE_SMALL_MODEL


def warm_sensevoice_small() -> None:
    _sensevoice_small_model()


def transcribe_faster_whisper(
    audio_path: str | Path,
    *,
    model: str = "small",
    language: str | None = "en",
    compute_type: str = "int8",
) -> ExternalTranscript:
    """Transcribe with faster-whisper/CTranslate2, a common local ASR baseline."""
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    try:
        import faster_whisper  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("faster-whisper is not installed. Run `pip install faster-whisper`.") from exc

    model_obj = _faster_whisper_model(model, compute_type)
    segments, info = model_obj.transcribe(
        str(path),
        language=language,
        beam_size=5,
        condition_on_previous_text=False,
        vad_filter=False,
    )
    text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    return ExternalTranscript(
        text=text,
        engine=f"faster-whisper:{model}:{compute_type}",
        seconds=round(time.perf_counter() - started, 3),
        language=getattr(info, "language", None),
        language_probability=getattr(info, "language_probability", None),
    )


def transcribe_faster_whisper_auto(audio_path: str | Path) -> ExternalTranscript:
    """Transcribe with faster-whisper using language auto-detection."""
    return transcribe_faster_whisper(audio_path, language=None)


def detect_faster_whisper_language(
    audio_path: str | Path,
    *,
    model: str = "tiny",
    compute_type: str = "int8",
) -> ExternalTranscript:
    """Detect spoken language without running full Whisper decoding."""
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    try:
        from faster_whisper.audio import decode_audio
    except ImportError as exc:
        raise RuntimeError("faster-whisper is not installed. Run `pip install faster-whisper`.") from exc

    model_obj = _faster_whisper_model(model, compute_type)
    audio = decode_audio(str(path), sampling_rate=16000)
    language, probability, _ = model_obj.detect_language(
        audio=audio,
        vad_filter=False,
        language_detection_segments=int(os.environ.get("RAMBLEFIX_LANGUAGE_DETECTION_SEGMENTS", "1")),
        language_detection_threshold=float(os.environ.get("RAMBLEFIX_LANGUAGE_DETECTION_THRESHOLD", "0.5")),
    )
    return ExternalTranscript(
        text="",
        engine=f"faster-whisper.detect-language:{model}:{compute_type}",
        seconds=round(time.perf_counter() - started, 3),
        language=language,
        language_probability=probability,
    )


def warm_faster_whisper_language_detector(
    *,
    model: str = "tiny",
    compute_type: str = "int8",
) -> None:
    """Load the tiny language detector before the first streaming chunk arrives."""
    _faster_whisper_model(model, compute_type)


def _faster_whisper_model(model: str, compute_type: str) -> object:
    from faster_whisper import WhisperModel

    cache_key = (model, "cpu", compute_type)
    model_obj = _FASTER_WHISPER_CACHE.get(cache_key)
    if model_obj is None:
        model_obj = WhisperModel(model, device="cpu", compute_type=compute_type)
        _FASTER_WHISPER_CACHE[cache_key] = model_obj
    return model_obj


def transcribe_whisper_cpp(
    audio_path: str | Path,
    *,
    binary: str | Path = DEFAULT_WHISPER_CPP_BINARY,
    model: str | Path = DEFAULT_WHISPER_CPP_SMALL_MODEL,
    language: str = "en",
    timeout_seconds: float = DEFAULT_EXTERNAL_ASR_TIMEOUT_SECONDS,
    no_gpu: bool = False,
) -> ExternalTranscript:
    """Transcribe with whisper.cpp, the engine behind several open-source dictation apps."""
    path = Path(audio_path).expanduser().resolve()
    binary_path = _whisper_cpp_binary_path(binary)
    model_path = _whisper_cpp_model_path(model)
    if not binary_path.exists():
        raise RuntimeError(f"missing whisper.cpp binary: {binary_path}")
    if not model_path.exists():
        raise RuntimeError(f"missing whisper.cpp model: {model_path}")

    started = time.perf_counter()
    text, detected_language, language_probability = _run_whisper_cpp(
        path=path,
        binary_path=binary_path,
        model_path=model_path,
        language=language,
        translate=False,
        timeout_seconds=timeout_seconds,
        no_gpu=no_gpu,
    )

    return ExternalTranscript(
        text=text,
        engine=f"whisper.cpp:{model_path.name}",
        seconds=round(time.perf_counter() - started, 3),
        language=detected_language,
        language_probability=language_probability,
    )


def transcribe_whisper_cpp_translate(
    audio_path: str | Path,
    *,
    binary: str | Path = DEFAULT_WHISPER_CPP_BINARY,
    model: str | Path = DEFAULT_WHISPER_CPP_SMALL_MODEL,
    language: str = "auto",
    timeout_seconds: float = DEFAULT_EXTERNAL_ASR_TIMEOUT_SECONDS,
    no_gpu: bool = False,
) -> ExternalTranscript:
    """Use whisper.cpp's local translate task as the fast meaning-first baseline.

    This is intentionally different from verbatim transcription. With
    `language=auto` and `--translate`, whisper.cpp detects Hindi/Hinglish-ish
    speech and emits English meaning text in one local pass, which matches the
    default RambleFix product objective.
    """
    path = Path(audio_path).expanduser().resolve()
    binary_path = _whisper_cpp_binary_path(binary)
    model_path = _whisper_cpp_model_path(model)
    if not binary_path.exists():
        raise RuntimeError(f"missing whisper.cpp binary: {binary_path}")
    if not model_path.exists():
        raise RuntimeError(f"missing whisper.cpp model: {model_path}")

    started = time.perf_counter()
    text, detected_language, language_probability = _run_whisper_cpp(
        path=path,
        binary_path=binary_path,
        model_path=model_path,
        language=language,
        translate=True,
        timeout_seconds=timeout_seconds,
        no_gpu=no_gpu,
    )

    return ExternalTranscript(
        text=text,
        engine=f"whisper.cpp.translate:{model_path.name}",
        seconds=round(time.perf_counter() - started, 3),
        language=detected_language,
        language_probability=language_probability,
    )


def transcribe_whisper_cpp_translate_base(audio_path: str | Path) -> ExternalTranscript:
    """Fast local meaning baseline using the smaller whisper.cpp base model."""
    return transcribe_whisper_cpp_translate(audio_path, model=DEFAULT_WHISPER_CPP_BASE_MODEL)


def _whisper_cpp_binary_path(binary: str | Path) -> Path:
    if str(binary) == DEFAULT_WHISPER_CPP_BINARY:
        override = os.environ.get("RAMBLEFIX_WHISPER_CPP_BINARY", "").strip()
        if override:
            return Path(override).expanduser()
    return Path(binary).expanduser()


def _whisper_cpp_model_path(model: str | Path) -> Path:
    if str(model) == DEFAULT_WHISPER_CPP_SMALL_MODEL:
        override = os.environ.get("RAMBLEFIX_WHISPER_MODEL", "").strip()
        if override:
            return Path(override).expanduser()
    return Path(model).expanduser()


def transcribe_whisper_cpp_server_translate(
    audio_path: str | Path,
    *,
    url: str = DEFAULT_WHISPER_CPP_SERVER_URL,
    timeout_seconds: float = 60.0,
) -> ExternalTranscript:
    """Transcribe through a resident whisper.cpp server.

    Start the sidecar with:

    `whisper-server -m <ggml-small.bin> -l auto -tr -nt --host 127.0.0.1 --port 8178`

    The point is to keep the model loaded and avoid `whisper-cli` process
    startup on every dictation.
    """
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    with path.open("rb") as audio_file:
        response = requests.post(
            url,
            files={"file": (path.name, audio_file, "audio/wav")},
            data={
                "response_format": "json",
                "temperature": "0.0",
                "translate": "true",
            },
            timeout=timeout_seconds,
        )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"whisper.cpp server returned non-JSON response: {response.text[:500]}") from exc
    text = str(payload.get("text", "")).strip()
    return ExternalTranscript(
        text=text,
        engine="whisper.cpp.server.translate",
        seconds=round(time.perf_counter() - started, 3),
    )


def transcribe_local_meaning_server_with_fallback(
    audio_path: str | Path,
    *,
    timeout_seconds: float = 20.0,
    skip_process_fallback: bool | None = None,
) -> ExternalTranscript:
    """Default product ASR path: owned resident server, then process fallback.

    The fallback is intentionally explicit in the engine string so app history
    and eval rows can separate healthy-server runs from degraded runs.
    """
    started = time.perf_counter()
    fallback_reason = ""
    if skip_process_fallback is None:
        skip_process_fallback = _env_truthy("RAMBLEFIX_SKIP_WHISPER_CPP_PROCESS_FALLBACK", default=True)

    def mlx_fallback(reason: str) -> ExternalTranscript:
        from ramblefix.asr import transcribe_audio

        model = os.environ.get("RAMBLEFIX_MLX_FALLBACK_MODEL", "mlx-community/whisper-large-v3-turbo-q4")
        fallback = transcribe_audio(audio_path, model=model, language=None)
        return ExternalTranscript(
            text=fallback.text,
            engine=f"{fallback.engine}|fallback_reason={reason}|mlx_fallback",
            seconds=round(time.perf_counter() - started, 3),
            language=fallback.language,
        )

    def process_fallback(reason: str) -> ExternalTranscript:
        try:
            fallback = transcribe_whisper_cpp_translate(audio_path)
            if fallback.text.strip():
                return ExternalTranscript(
                    text=fallback.text,
                    engine=f"{fallback.engine}|fallback_reason={reason}",
                    seconds=round(time.perf_counter() - started, 3),
                    language=fallback.language,
                    language_probability=fallback.language_probability,
                )
            return mlx_fallback(f"{reason}|empty_process_fallback")
        except Exception as exc:
            return mlx_fallback(f"{reason}|process_fallback_error:{exc}")

    try:
        transcript = transcribe_whisper_cpp_server_translate(audio_path, timeout_seconds=timeout_seconds)
        if transcript.text.strip():
            fallback_reason = _server_completeness_fallback_reason(audio_path, transcript.text)
            if fallback_reason:
                if skip_process_fallback:
                    return mlx_fallback(fallback_reason)
                return process_fallback(fallback_reason)
            return transcript
        fallback_reason = "empty_server_output"
        if skip_process_fallback:
            return mlx_fallback(fallback_reason)
    except Exception as exc:
        fallback_reason = f"direct_server_error:{exc}"

    try:
        from ramblefix.sidecar import ensure_ready, status

        # Dictation is latency-sensitive: the native app can start the sidecar
        # ahead of time, but release-to-paste must not wait for server startup.
        state = status() if skip_process_fallback else ensure_ready(warm=False, timeout_seconds=8.0)
        if not state.ready:
            fallback_reason = f"sidecar_{state.status}"
            raise RuntimeError(state.error or "sidecar unavailable")
        transcript = transcribe_whisper_cpp_server_translate(audio_path, url=state.url, timeout_seconds=timeout_seconds)
        if transcript.text.strip():
            fallback_reason = _server_completeness_fallback_reason(audio_path, transcript.text)
            if fallback_reason:
                if skip_process_fallback:
                    return mlx_fallback(fallback_reason)
                return process_fallback(fallback_reason)
            return transcript
        fallback_reason = "empty_server_output"
    except Exception as exc:
        fallback_reason = fallback_reason or f"sidecar_error:{exc}"

    if skip_process_fallback:
        return mlx_fallback(fallback_reason)

    return process_fallback(fallback_reason)


def _server_completeness_fallback_reason(audio_path: str | Path, text: str) -> str:
    """Catch whisper-server returning a fast prefix for longer dictations."""
    stripped = text.strip()
    if not stripped:
        return ""
    metrics = wav_silence_metrics(audio_path)
    duration = float(metrics.get("audio_duration_seconds") or 0.0)
    if duration < 18.0 or bool(metrics.get("audio_probably_silent")):
        return ""
    chars = len(stripped)
    words = len(re.findall(r"\w+", stripped, flags=re.UNICODE))
    very_short_chars = chars < max(120, int(duration * 4.0))
    very_short_words = words < max(20, int(duration * 0.45))
    prefix_like = chars < int(duration * 8.0) and words < 55
    if very_short_chars or very_short_words or prefix_like:
        return f"suspected_truncated_server_output:duration={duration:.1f},chars={chars},words={words}"
    return ""


def _run_whisper_cpp(
    *,
    path: Path,
    binary_path: Path,
    model_path: Path,
    language: str,
    translate: bool,
    timeout_seconds: float,
    no_gpu: bool = False,
) -> tuple[str, str | None, float | None]:
    with tempfile.TemporaryDirectory(prefix="ramblefix-whispercpp-") as tmp:
        out_base = Path(tmp) / "whisper"
        cmd = [
            str(binary_path),
            "-m",
            str(model_path),
            "-f",
            str(path),
            "-l",
            language,
            "-otxt",
            "-of",
            str(out_base),
            "-nt",
        ]
        if translate:
            cmd.append("-tr")
        else:
            cmd.append("-np")
        if no_gpu or _env_truthy("RAMBLEFIX_WHISPER_CPP_NO_GPU", default=False):
            cmd.append("-ng")
        proc: subprocess.Popen[str] | None = None
        try:
            proc = subprocess.Popen(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            if proc is not None:
                _terminate_process_group(proc)
            raise RuntimeError(f"whisper.cpp timed out after {timeout_seconds:.1f}s") from exc
        if proc.returncode != 0:
            raise RuntimeError((stderr or stdout).strip())
        txt_path = out_base.with_suffix(".txt")
        text = txt_path.read_text(encoding="utf-8").strip() if txt_path.exists() else stdout.strip()
    detected_language, language_probability = _parse_whisper_cpp_language(stderr)
    return text, detected_language, language_probability


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.communicate(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _env_truthy(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _optional_int_env(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"invalid {name}={value!r}") from exc
    if parsed < 0:
        raise RuntimeError(f"invalid {name}={value!r}")
    return parsed


def _optional_float_env(name: str, *, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise RuntimeError(f"invalid {name}={value!r}") from exc
    if parsed <= 0:
        raise RuntimeError(f"invalid {name}={value!r}")
    return parsed


def _ensure_numba_cache_dir() -> None:
    if "NUMBA_CACHE_DIR" in os.environ:
        return
    cache_dir = Path(tempfile.gettempdir()) / "ramblefix-numba-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["NUMBA_CACHE_DIR"] = str(cache_dir)


def _parse_whisper_cpp_language(stderr: str) -> tuple[str | None, float | None]:
    match = re.search(r"auto-detected language:\s*([a-zA-Z_-]+)\s*\(p\s*=\s*([0-9.]+)\)", stderr)
    if not match:
        return None, None
    try:
        probability = float(match.group(2))
    except ValueError:
        probability = None
    return match.group(1), probability


def transcribe_whisperkit_cli(
    audio_path: str | Path,
    *,
    binary: str = "whisperkit-cli",
    model: str = "tiny",
    language: str = "en",
    timeout_seconds: float = DEFAULT_EXTERNAL_ASR_TIMEOUT_SECONDS,
) -> ExternalTranscript:
    """Transcribe with WhisperKit CLI, the engine used by Mac-native local dictation apps."""
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [
                binary,
                "transcribe",
                "--audio-path",
                str(path),
                "--model",
                model,
                "--language",
                language,
                "--skip-special-tokens",
                "--without-timestamps",
                "--chunking-strategy",
                "none",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"WhisperKit CLI timed out after {timeout_seconds:.1f}s") from exc
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    text = (proc.stdout or "").strip()
    return ExternalTranscript(
        text=text,
        engine=f"whisperkit-cli:{model}",
        seconds=round(time.perf_counter() - started, 3),
    )


def _extract_text(result: object) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return _clean_asr_text(result)
    if isinstance(result, tuple) and result:
        return _extract_text(result[0])
    text = getattr(result, "text", None)
    if text is not None:
        return _clean_asr_text(str(text))
    if isinstance(result, dict):
        return _extract_text(result.get("text", ""))
    if isinstance(result, list):
        return _clean_asr_text(" ".join(_extract_text(item) for item in result))
    return _clean_asr_text(str(result))


def _clean_asr_text(text: str) -> str:
    text = re.sub(r"<\|[^|]*\|>", "", text)
    return re.sub(r"\s+", " ", text).strip()
