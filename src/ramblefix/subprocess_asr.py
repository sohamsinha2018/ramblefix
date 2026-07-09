from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SubprocessTranscript:
    text: str
    engine: str
    language: str | None


def transcribe_with_timeout(
    audio_path: str | Path,
    *,
    model: str,
    language: str | None,
    timeout_seconds: int = 30,
) -> SubprocessTranscript:
    command = [
        sys.executable,
        "-m",
        "ramblefix.cli",
        "audio",
        str(audio_path),
        "--model",
        model,
        "--mode",
        "prompt",
        "--json",
    ]
    if language:
        command.extend(["--language", language])

    completed = subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    payload = _extract_json(completed.stdout)
    return SubprocessTranscript(
        text=str(payload["text"]),
        engine=str(payload["engine"]),
        language=payload.get("language"),
    )


def _extract_json(stdout: str) -> dict[str, object]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise ValueError(f"No JSON payload found in ASR output: {stdout[-500:]}")
