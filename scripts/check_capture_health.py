from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class WavProbe:
    path: str
    size_bytes: int
    valid_wav: bool
    duration_seconds: float
    error: str


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose whether RambleFix hotkey capture is producing usable eval audio.")
    parser.add_argument("--history", type=Path, default=ROOT / "logs/history.jsonl")
    parser.add_argument("--native-events", type=Path, default=ROOT / "logs/native_events.jsonl")
    parser.add_argument("--audio-dir", type=Path, default=ROOT / "logs/hotkey_audio")
    parser.add_argument("--min-duration-seconds", type=float, default=0.5)
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when the latest root WAV is missing or unusable.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    latest_wav = _latest_wav(args.audio_dir)
    wav_probe = _probe_wav(latest_wav) if latest_wav else None
    latest_history = _latest_json_row(args.history)
    latest_event = _latest_json_row(args.native_events)
    app_processes = _pgrep("RambleFixLocal")

    latest_wav_ok = bool(
        wav_probe
        and wav_probe.valid_wav
        and wav_probe.duration_seconds >= args.min_duration_seconds
    )
    payload: dict[str, Any] = {
        "app_processes": app_processes,
        "app_running": bool(app_processes),
        "history_path": str(args.history),
        "history_mtime": _mtime(args.history),
        "latest_history": _row_summary(latest_history),
        "native_events_path": str(args.native_events),
        "native_events_mtime": _mtime(args.native_events),
        "latest_native_event": _row_summary(latest_event),
        "audio_dir": str(args.audio_dir),
        "root_wav_count": len(list(args.audio_dir.glob("*.wav"))) if args.audio_dir.exists() else 0,
        "latest_wav": asdict(wav_probe) if wav_probe else None,
        "latest_wav_ok": latest_wav_ok,
        "issues": _issues(wav_probe, latest_wav_ok, latest_history, latest_event, args.min_duration_seconds),
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human(payload)

    if args.strict and not latest_wav_ok:
        sys.exit(1)


def _latest_wav(audio_dir: Path) -> Path | None:
    if not audio_dir.exists():
        return None
    wavs = [path for path in audio_dir.glob("*.wav") if path.is_file()]
    if not wavs:
        return None
    return max(wavs, key=lambda path: path.stat().st_mtime)


def _probe_wav(path: Path) -> WavProbe:
    size = path.stat().st_size
    try:
        with wave.open(str(path), "rb") as audio:
            frames = audio.getnframes()
            rate = audio.getframerate()
            duration = frames / rate if rate else 0.0
        return WavProbe(
            path=str(path),
            size_bytes=size,
            valid_wav=True,
            duration_seconds=round(duration, 3),
            error="",
        )
    except Exception as exc:  # noqa: BLE001
        return WavProbe(
            path=str(path),
            size_bytes=size,
            valid_wav=False,
            duration_seconds=0.0,
            error=str(exc),
        )


def _latest_json_row(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    latest: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            latest = row
    return latest


def _row_summary(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "created_at": row.get("created_at") or "",
        "event": row.get("event") or "",
        "run_id": row.get("run_id") or "",
        "status": row.get("status") or "",
        "audio_path": row.get("audio_path") or "",
        "write_succeeded": row.get("write_succeeded"),
    }


def _mtime(path: Path) -> float | None:
    if not path.exists():
        return None
    return round(path.stat().st_mtime, 3)


def _pgrep(pattern: str) -> list[str]:
    try:
        result = subprocess.run(
            ["pgrep", "-fl", pattern],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _issues(
    wav_probe: WavProbe | None,
    latest_wav_ok: bool,
    latest_history: dict[str, Any],
    latest_event: dict[str, Any],
    min_duration_seconds: float,
) -> list[str]:
    issues: list[str] = []
    if wav_probe is None:
        issues.append("no-root-hotkey-wav")
    elif not wav_probe.valid_wav:
        issues.append("latest-wav-invalid")
    elif wav_probe.duration_seconds < min_duration_seconds:
        issues.append("latest-wav-too-short")
    if wav_probe is not None and not latest_history:
        issues.append("no-history-row")
    if wav_probe is not None and not latest_event:
        issues.append("no-native-event-row")
    if wav_probe is not None and not latest_wav_ok:
        issues.append("capture-not-usable-for-eval")
    return issues


def _print_human(payload: dict[str, Any]) -> None:
    print(f"app_running: {payload['app_running']}")
    if payload["app_processes"]:
        print("app_processes:")
        for process in payload["app_processes"]:
            print(f"- {process}")
    print(f"history_mtime: {payload['history_mtime']}")
    print(f"latest_history: {payload['latest_history']}")
    print(f"native_events_mtime: {payload['native_events_mtime']}")
    print(f"latest_native_event: {payload['latest_native_event']}")
    print(f"root_wav_count: {payload['root_wav_count']}")
    print(f"latest_wav_ok: {payload['latest_wav_ok']}")
    print(f"latest_wav: {payload['latest_wav']}")
    if payload["issues"]:
        print("issues:")
        for issue in payload["issues"]:
            print(f"- {issue}")


if __name__ == "__main__":
    main()
