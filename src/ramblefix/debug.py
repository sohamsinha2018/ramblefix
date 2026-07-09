from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


@dataclass(frozen=True)
class AudioStats:
    path: str
    samplerate: int
    duration_seconds: float
    mean_abs: float
    rms: float
    peak_abs: float
    dbfs: float
    likely_silence: bool


class RunLogger:
    def __init__(self, run_id: str, log_dir: str | Path = "logs") -> None:
        self.run_id = run_id
        self.log_dir = Path(log_dir).expanduser().resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.log_dir / f"{run_id}.jsonl"

    def event(self, step: str, **payload: Any) -> None:
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "run_id": self.run_id,
            "step": step,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")


def inspect_audio(path: str | Path) -> AudioStats:
    resolved = Path(path).expanduser().resolve()
    data, samplerate = sf.read(resolved)
    if data.ndim > 1:
        data = data.mean(axis=1)
    abs_data = np.abs(data)
    rms = float(np.sqrt(np.mean(np.square(data)))) if len(data) else 0.0
    peak = float(abs_data.max()) if len(data) else 0.0
    mean_abs = float(abs_data.mean()) if len(data) else 0.0
    dbfs = 20 * math.log10(max(rms, 1e-12))
    duration = len(data) / samplerate if samplerate else 0.0
    return AudioStats(
        path=str(resolved),
        samplerate=int(samplerate),
        duration_seconds=round(duration, 3),
        mean_abs=round(mean_abs, 6),
        rms=round(rms, 6),
        peak_abs=round(peak, 6),
        dbfs=round(dbfs, 2),
        likely_silence=peak < 0.03 or rms < 0.005,
    )


def _json_default(value: Any) -> Any:
    if hasattr(value, "__dict__"):
        return asdict(value)
    return str(value)
