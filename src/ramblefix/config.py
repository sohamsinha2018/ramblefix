from __future__ import annotations

from pathlib import Path


_RUNTIME_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_EXTERNAL_ASR_TIMEOUT_SECONDS = 30.0
DEFAULT_WHISPER_CPP_BINARY = str(_RUNTIME_ROOT / "bin/whisper-cli")
DEFAULT_WHISPER_CPP_BASE_MODEL = str(_RUNTIME_ROOT / "models/ggml-base.bin")
DEFAULT_WHISPER_CPP_SMALL_MODEL = str(_RUNTIME_ROOT / "models/ggml-small.bin")
DEFAULT_WHISPER_CPP_SERVER_URL = "http://127.0.0.1:8178/inference"
DEFAULT_WHISPER_SERVER_BINARY = str(_RUNTIME_ROOT / "bin/whisper-server")
DEFAULT_ORISERVE_GGML_MODEL = "models/oriserve-ggml/ggml-oriserve-hinglish-q8_0.bin"
DEFAULT_QWEN3_ASR_MLX_MODEL = "Qwen/Qwen3-ASR-0.6B"
DEFAULT_QWEN3_ASR_MLX_HINGLISH_MODEL = "moorlee/qwen3-asr-0.6b-hinglish"
