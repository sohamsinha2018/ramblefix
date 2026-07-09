from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
import time
from typing import Callable

import numpy as np
import sounddevice as sd
import soundfile as sf


def record_microphone(
    output_path: str | Path,
    *,
    seconds: int,
    samplerate: int = 16_000,
) -> Path:
    return record_microphone_monitored(output_path, seconds=seconds, samplerate=samplerate)


def record_microphone_monitored(
    output_path: str | Path,
    *,
    seconds: int,
    samplerate: int = 16_000,
    chunk_seconds: float = 0.25,
    on_progress: Callable[[float, float, float], None] | None = None,
) -> Path:
    """Record microphone audio in chunks.

    Calls on_progress(elapsed_seconds, rms, peak) after each chunk so the UI can
    prove recording is alive and show input level.
    """
    native_script = _native_recorder_script()
    if (
        platform.system() == "Darwin"
        and native_script.exists()
        and os.environ.get("RAMBLEFIX_USE_NATIVE_RECORDER", "1") != "0"
    ):
        return _record_with_native_recorder(
            native_script,
            output_path,
            seconds=seconds,
            on_progress=on_progress,
            progress_interval=chunk_seconds,
        )

    return _record_with_sounddevice(
        output_path,
        seconds=seconds,
        samplerate=samplerate,
        chunk_seconds=chunk_seconds,
        on_progress=on_progress,
    )


def recorder_backend() -> str:
    native_script = _native_recorder_script()
    if (
        platform.system() == "Darwin"
        and native_script.exists()
        and os.environ.get("RAMBLEFIX_USE_NATIVE_RECORDER", "1") != "0"
    ):
        return "native-macos-avfoundation"
    return "python-sounddevice"


def _record_with_sounddevice(
    output_path: str | Path,
    *,
    seconds: int,
    samplerate: int,
    chunk_seconds: float,
    on_progress: Callable[[float, float, float], None] | None,
) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    chunks: list[np.ndarray] = []
    total_frames = int(seconds * samplerate)
    recorded_frames = 0
    last_progress = 0.0

    def callback(indata: np.ndarray, frames: int, _time_info: object, status: sd.CallbackFlags) -> None:
        nonlocal recorded_frames
        if status:
            # Keep recording; the caller can inspect the final audio/logs.
            pass
        mono = np.asarray(indata[:, 0], dtype=np.float32).copy()
        chunks.append(mono)
        recorded_frames += frames

    start = time.perf_counter()
    blocksize = max(256, int(samplerate * 0.05))
    with sd.InputStream(
        samplerate=samplerate,
        channels=1,
        dtype="float32",
        blocksize=blocksize,
        callback=callback,
    ):
        while recorded_frames < total_frames:
            time.sleep(0.03)
            elapsed = min(time.perf_counter() - start, float(seconds))
            if on_progress and elapsed - last_progress >= chunk_seconds:
                recent = _recent_audio(chunks, int(samplerate * min(1.0, max(chunk_seconds, 0.25))))
                rms = float(np.sqrt(np.mean(np.square(recent)))) if len(recent) else 0.0
                peak = float(np.abs(recent).max()) if len(recent) else 0.0
                on_progress(elapsed, rms, peak)
                last_progress = elapsed

    final = np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)
    final = final[:total_frames]
    sf.write(path, final, samplerate)
    return path


def _record_with_native_recorder(
    script: Path,
    output_path: str | Path,
    *,
    seconds: int,
    on_progress: Callable[[float, float, float], None] | None,
    progress_interval: float,
) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    root = script.parent.parent
    command = [
        str(script),
        "--seconds",
        str(seconds),
        "--output",
        str(path),
        "--progress-interval",
        str(progress_interval),
    ]
    process = subprocess.Popen(
        command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stderr_lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("event") == "level" and on_progress:
            on_progress(
                float(payload.get("elapsed") or 0.0),
                float(payload.get("rms") or 0.0),
                float(payload.get("peak") or 0.0),
            )
        elif payload.get("event") == "error":
            stderr_lines.append(str(payload.get("error") or "native recorder error"))

    stderr = process.stderr.read() if process.stderr else ""
    code = process.wait()
    if code != 0:
        details = "\n".join(stderr_lines + ([stderr.strip()] if stderr.strip() else []))
        raise RuntimeError(f"Native recorder failed with code {code}: {details}")
    if not path.exists():
        raise RuntimeError(f"Native recorder did not create output: {path}")
    return path


def _native_recorder_script() -> Path:
    return Path(__file__).resolve().parents[2] / "script" / "native_record.sh"


def _recent_audio(chunks: list[np.ndarray], frames: int) -> np.ndarray:
    if not chunks:
        return np.array([], dtype=np.float32)
    selected: list[np.ndarray] = []
    remaining = frames
    for chunk in reversed(chunks):
        selected.append(chunk)
        remaining -= len(chunk)
        if remaining <= 0:
            break
    recent = np.concatenate(list(reversed(selected)))
    return recent[-frames:]
