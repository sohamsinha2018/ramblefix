from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ramblefix-live-provider-verifier-") as tmp:
        logs_root = Path(tmp)
        meeting_dir = logs_root / "meeting_audio"
        meeting_dir.mkdir(parents=True)
        run_id = "live-provider-regression"
        mic = meeting_dir / f"{run_id}.wav"
        system = meeting_dir / f"{run_id}.system.wav"
        write_sine_wav(mic)
        write_sine_wav(system)
        write_jsonl(
            logs_root / "native_events.jsonl",
            [
                {"event": "recording_started", "mode": "meeting", "run_id": run_id, "audio_path": str(mic)},
                {"event": "meeting_system_audio_started", "run_id": run_id, "audio_path": str(system)},
                {"event": "meeting_transcription_sources", "run_id": run_id, "sources": ["system", "mic"]},
            ],
        )
        write_jsonl(
            logs_root / "history.jsonl",
            [
                {
                    "run_id": run_id,
                    "mode": "meeting",
                    "status": "meeting_transcribed",
                    "audio_path": str(mic),
                    "corrected_text": "[Meeting audio]\nRemote SOC2 evidence.\n\n[My mic]\nMy mic response.",
                }
            ],
        )
        ok = run_verifier(logs_root, run_id)
        expect(ok["ok"] is True, f"expected verifier success, got {ok}")

        system.unlink()
        failed = run_verifier(logs_root, run_id, expect_success=False)
        expect(failed["ok"] is False, "missing system wav must fail live-provider verification")
        failed_names = {check["name"] for check in failed["checks"] if not check["passed"]}
        expect("system wav exists and is usable" in failed_names, failed_names)

    print("regression_live_provider_verifier passed")


def run_verifier(logs_root: Path, run_id: str, *, expect_success: bool = True) -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/verify_live_provider_meeting.py"),
            "--json",
            "--logs-root",
            str(logs_root),
            "--run-id",
            run_id,
            "--min-duration-seconds",
            "1.0",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if expect_success and proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    if not expect_success and proc.returncode == 0:
        raise AssertionError(proc.stdout)
    return json.loads(proc.stdout)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def write_sine_wav(path: Path, *, seconds: float = 1.4, sample_rate: int = 16_000) -> None:
    frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        for index in range(frames):
            value = int(0.2 * 32767 * math.sin(2 * math.pi * 440 * index / sample_rate))
            writer.writeframes(struct.pack("<h", value))


def expect(condition: bool, message: object) -> None:
    if not condition:
        raise AssertionError(str(message))


if __name__ == "__main__":
    main()
