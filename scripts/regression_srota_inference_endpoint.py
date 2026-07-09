#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import ramblefix.asr as asr_module
from ramblefix.asr import Transcript
from ramblefix.srota_server import SrotaHandler, ThreadingHTTPServer


@contextlib.contextmanager
def deterministic_glossary_config():
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="ramblefix-srota-config-") as raw_dir:
        config_dir = Path(raw_dir) / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "phrase_fixes.json").write_text(
            json.dumps(
                {
                    "version": "regression.fixture",
                    "phrase_fixes": [
                        {
                            "source": "end-all safe replacement",
                            "replacement": "end-to-end safe replacement",
                            "enabled": True,
                            "approved": True,
                            "note": "Deterministic endpoint regression fixture.",
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chdir(raw_dir)
        try:
            yield
        finally:
            os.chdir(original_cwd)


def main() -> None:
    original_transcribe = asr_module.transcribe_audio

    def fake_transcribe_audio(audio_path: str | Path, **_: Any) -> Transcript:
        assert Path(audio_path).exists(), audio_path
        return Transcript(
            text="Why is the end-all safe replacement failing?",
            language="en",
            segments=[],
            engine="mlx-whisper:test-stub",
        )

    asr_module.transcribe_audio = fake_transcribe_audio  # type: ignore[assignment]
    with deterministic_glossary_config():
        server = ThreadingHTTPServer(("127.0.0.1", 0), SrotaHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            wav_path = _write_wav()
            url = f"http://127.0.0.1:{server.server_address[1]}/inference"
            with wav_path.open("rb") as audio_file:
                response = requests.post(
                    url,
                    files={"file": (wav_path.name, audio_file, "audio/wav")},
                    timeout=5,
                )
            if response.status_code >= 400:
                raise AssertionError(f"Srota inference failed status={response.status_code} body={response.text}")
            response.raise_for_status()
            payload = response.json()
            assert payload["text"] == "Why is the end-to-end safe replacement failing?", payload
            assert payload["raw_text"] == "Why is the end-all safe replacement failing?", payload
            assert payload["route"] == "srota_local_mlx_inference", payload
            assert payload["engine"].startswith("srota.local_mlx:mlx-whisper:test-stub"), payload
            assert payload["quality"]["blank_or_no_speech"] is False, payload
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            asr_module.transcribe_audio = original_transcribe  # type: ignore[assignment]
    print("regression_srota_inference_endpoint passed")


def _write_wav() -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="ramblefix-srota-regression-", suffix=".wav")
    os.close(fd)
    Path(raw_path).unlink(missing_ok=True)
    path = Path(raw_path)
    with contextlib.closing(wave.open(str(path), "wb")) as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes((b"\x00\x10" * 16_000))
    return path


if __name__ == "__main__":
    main()
