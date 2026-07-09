from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix import asr as mlx_asr
from ramblefix import external_asr
from ramblefix import sidecar


@dataclass(frozen=True)
class _FakeMlxTranscript:
    text: str = "fallback text from local mlx"
    engine: str = "mlx-whisper:test"
    language: str | None = "en"


def _assert_fallback(result: external_asr.ExternalTranscript, name: str) -> None:
    if not result.text.strip():
        raise AssertionError(f"{name}: returned blank text")
    if "mlx_fallback" not in result.engine:
        raise AssertionError(f"{name}: did not use MLX fallback: {result.engine}")


def main() -> None:
    original_server = external_asr.transcribe_whisper_cpp_server_translate
    original_process = external_asr.transcribe_whisper_cpp_translate
    original_completeness = external_asr._server_completeness_fallback_reason
    original_mlx = mlx_asr.transcribe_audio
    original_sidecar_status = sidecar.status
    original_sidecar_ensure_ready = sidecar.ensure_ready

    def fake_mlx(audio_path: str | Path, **kwargs: object) -> _FakeMlxTranscript:
        return _FakeMlxTranscript()

    mlx_asr.transcribe_audio = fake_mlx  # type: ignore[method-assign]

    try:
        external_asr.transcribe_whisper_cpp_server_translate = lambda *args, **kwargs: external_asr.ExternalTranscript(  # type: ignore[method-assign]
            text="",
            engine="whisper.cpp.server.translate",
            seconds=0.01,
        )
        blank = external_asr.transcribe_local_meaning_server_with_fallback(
            "regression/nonexistent.wav",
            skip_process_fallback=True,
        )
        _assert_fallback(blank, "blank-server-output")

        external_asr.transcribe_whisper_cpp_server_translate = lambda *args, **kwargs: external_asr.ExternalTranscript(  # type: ignore[method-assign]
            text="short prefix",
            engine="whisper.cpp.server.translate",
            seconds=0.01,
        )
        external_asr._server_completeness_fallback_reason = lambda *args, **kwargs: "suspected_truncated_server_output:test"  # type: ignore[method-assign]
        truncated = external_asr.transcribe_local_meaning_server_with_fallback(
            "regression/nonexistent.wav",
            skip_process_fallback=True,
        )
        _assert_fallback(truncated, "truncated-server-output")

        external_asr.transcribe_whisper_cpp_server_translate = lambda *args, **kwargs: external_asr.ExternalTranscript(  # type: ignore[method-assign]
            text="short prefix",
            engine="whisper.cpp.server.translate",
            seconds=0.01,
        )
        external_asr.transcribe_whisper_cpp_translate = _raise_process_timeout  # type: ignore[method-assign]
        process_error = external_asr.transcribe_local_meaning_server_with_fallback(
            "regression/nonexistent.wav",
            skip_process_fallback=False,
        )
        _assert_fallback(process_error, "process-fallback-error")

        external_asr.transcribe_whisper_cpp_server_translate = _raise_server_down  # type: ignore[method-assign]
        external_asr._server_completeness_fallback_reason = original_completeness  # type: ignore[method-assign]
        sidecar.status = lambda *args, **kwargs: SimpleNamespace(ready=False, status="stopped", error="")  # type: ignore[method-assign]
        sidecar.ensure_ready = _raise_hot_path_start_forbidden  # type: ignore[method-assign]
        hot_path_down = external_asr.transcribe_local_meaning_server_with_fallback(
            "regression/nonexistent.wav",
            skip_process_fallback=True,
        )
        _assert_fallback(hot_path_down, "sidecar-down-hot-path")
    finally:
        external_asr.transcribe_whisper_cpp_server_translate = original_server  # type: ignore[method-assign]
        external_asr.transcribe_whisper_cpp_translate = original_process  # type: ignore[method-assign]
        external_asr._server_completeness_fallback_reason = original_completeness  # type: ignore[method-assign]
        mlx_asr.transcribe_audio = original_mlx  # type: ignore[method-assign]
        sidecar.status = original_sidecar_status  # type: ignore[method-assign]
        sidecar.ensure_ready = original_sidecar_ensure_ready  # type: ignore[method-assign]

    print("local ASR fallback regression passed")


def _raise_process_timeout(*args: object, **kwargs: object) -> external_asr.ExternalTranscript:
    raise TimeoutError("simulated whisper.cpp process wedge")


def _raise_server_down(*args: object, **kwargs: object) -> external_asr.ExternalTranscript:
    raise ConnectionError("simulated server down")


def _raise_hot_path_start_forbidden(*args: object, **kwargs: object) -> object:
    raise AssertionError("hot path must not start or wait for sidecar")


if __name__ == "__main__":
    main()
