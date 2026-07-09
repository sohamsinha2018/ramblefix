from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import requests


@dataclass(frozen=True)
class GeminiTranscript:
    text: str
    language: str | None
    engine: str
    raw: dict


def transcribe_gemini_audio(
    audio_path: str | Path,
    *,
    api_key: str | None = None,
    model: str = "gemini-2.5-flash",
    mime_type: str = "audio/wav",
    timeout_seconds: int = 60,
) -> GeminiTranscript:
    key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("Missing GEMINI_API_KEY or GOOGLE_API_KEY")

    path = Path(audio_path).expanduser().resolve()
    audio_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    prompt = "\n".join(
        [
            "Listen to the attached audio and return STRICT JSON only.",
            '{"heard":"short Hinglish/Hindi/English transcript of the ACTUAL user speech","intent":"one short intent","topic_tags":["real_tag_1","real_tag_2"]}',
            "Preserve Hindi words. Preserve English technical terms.",
            "Do not translate everything to English unless the user did.",
            "Do not copy this schema text. Do not return placeholder words like tag1/tag2.",
            'If unclear, heard="unclear", intent="unclear", topic_tags=[].',
        ]
    )

    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": mime_type, "data": audio_b64}},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 96,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    text = _extract_text(payload)
    parsed = _parse_jsonish(text)
    heard = str(parsed.get("heard", "")).strip() if parsed else text.strip()
    return GeminiTranscript(
        text=heard or "unclear",
        language=None,
        engine=f"gemini_audio:{model}",
        raw=payload,
    )


def _extract_text(payload: dict) -> str:
    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return ""


def _parse_jsonish(text: str) -> dict | None:
    clean = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        parsed = json.loads(clean)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None
