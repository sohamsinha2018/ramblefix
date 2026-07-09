from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


DEFAULT_HISTORY_PATH = Path("logs/history.jsonl")


def append_history_record(
    *,
    run_id: str,
    mode: str,
    audio_path: str | Path | None,
    raw_text: str,
    corrected_text: str,
    pasted_text: str = "",
    asr_engine: str = "",
    sidecar_state: dict[str, Any] | None = None,
    fallback_reason: str = "",
    dictionary_version: str = "",
    timings: list[str] | None = None,
    quality_flags: dict[str, Any] | None = None,
    offline_mode: bool = True,
    status: str = "completed",
    error_type: str = "",
    path: str | Path = DEFAULT_HISTORY_PATH,
) -> dict[str, Any]:
    if os.environ.get("RAMBLEFIX_HISTORY_ENABLED", "1") == "0":
        return {}
    row = {
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": mode,
        "audio_path": "" if audio_path is None else str(audio_path),
        "raw_text": raw_text,
        "corrected_text": corrected_text,
        "pasted_text": pasted_text,
        "asr_engine": asr_engine,
        "sidecar_state": sidecar_state or {},
        "fallback_reason": fallback_reason,
        "dictionary_version": dictionary_version,
        "timings": timings or [],
        "quality_flags": quality_flags or {},
        "offline_mode": offline_mode,
        "status": status,
        "error_type": error_type,
    }
    history_path = Path(path).expanduser()
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return row


def compact_sidecar_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {}
    url = str(state.get("url") or "")
    url_class = "loopback" if "127.0.0.1" in url or "localhost" in url else "other"
    return {
        "status": state.get("status"),
        "ready": state.get("ready"),
        "port_open": state.get("port_open"),
        "owned": state.get("owned"),
        "warmed": state.get("warmed"),
        "url_class": url_class,
    }


def extract_fallback_reason(engine: str) -> str:
    marker = "fallback_reason="
    if marker not in engine:
        return ""
    return engine.split(marker, 1)[1].split("|", 1)[0].strip()
