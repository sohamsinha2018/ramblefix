from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import requests


@dataclass(frozen=True)
class SarvamTranscript:
    text: str
    language: str | None
    engine: str
    raw: dict


def transcribe_sarvam(
    audio_path: str | Path,
    *,
    api_key: str | None = None,
    model: str = "saaras:v3",
    mode: str = "codemix",
    language_code: str = "unknown",
    timeout_seconds: int = 60,
) -> SarvamTranscript:
    key = api_key or os.environ.get("SARVAM_API_KEY")
    if not key:
        raise RuntimeError("Missing SARVAM_API_KEY")

    path = Path(audio_path).expanduser().resolve()
    with path.open("rb") as file:
        response = requests.post(
            "https://api.sarvam.ai/speech-to-text",
            headers={"api-subscription-key": key},
            files={"file": (path.name, file, "audio/wav")},
            data={
                "model": model,
                "mode": mode,
                "language_code": language_code,
            },
            timeout=timeout_seconds,
        )
    response.raise_for_status()
    payload = response.json()
    return SarvamTranscript(
        text=str(payload.get("transcript", "")).strip(),
        language=payload.get("language_code"),
        engine=f"sarvam:{model}:{mode}",
        raw=payload,
    )
