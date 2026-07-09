from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from ramblefix.external_asr import ExternalTranscript


def transcribe_elevenlabs_scribe(
    audio_path: str | Path,
    *,
    api_key: str | None = None,
    model_id: str = "scribe_v2",
    language_code: str | None = None,
) -> ExternalTranscript:
    """Transcribe with ElevenLabs Scribe as an eval-only cloud comparator."""
    key = api_key or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("Missing ELEVENLABS_API_KEY")
    path = Path(audio_path).expanduser().resolve()
    started = time.perf_counter()
    data: dict[str, str] = {
        "model_id": model_id,
        "tag_audio_events": "false",
        "diarize": "false",
        "timestamps_granularity": "none",
    }
    if language_code:
        data["language_code"] = language_code
    with path.open("rb") as audio_file:
        response = requests.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": key},
            files={"file": (path.name, audio_file, "audio/wav")},
            data=data,
            timeout=120,
        )
    response.raise_for_status()
    payload = response.json()
    return ExternalTranscript(
        text=str(payload.get("text", "")).strip(),
        engine=f"elevenlabs.scribe:{model_id}",
        seconds=round(time.perf_counter() - started, 3),
        language=payload.get("language_code"),
        language_probability=payload.get("language_probability"),
    )
