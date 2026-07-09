from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from verify_live_provider_meeting import latest_meeting_run_id, read_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wait for a new native RambleFix meeting-mode recording, then run the live-provider verifier."
    )
    parser.add_argument("--logs-root", type=Path, default=ROOT / "logs")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--min-duration-seconds", type=float, default=3.0)
    parser.add_argument("--provider-url", default="", help="Optional URL to open before waiting, e.g. a Meet/Zoom/Teams test call.")
    args = parser.parse_args()

    logs_root = args.logs_root.expanduser().resolve()
    before = latest_meeting_run_id(
        read_jsonl(logs_root / "history.jsonl"),
        read_jsonl(logs_root / "native_events.jsonl"),
    )

    if args.provider_url:
        subprocess.run(["open", args.provider_url], check=False)

    print(
        "\n".join(
            [
                "RambleFix live-provider meeting proof",
                "",
                "Do this now:",
                "1. Join or start a real Zoom, Google Meet, or Teams call/test call.",
                "2. In the RambleFix menu, choose Record Meeting.",
                "3. Make the meeting/provider produce audible remote audio.",
                "4. Speak once into your mic.",
                "5. In the RambleFix menu, choose Stop Meeting Recording.",
                "",
                f"Waiting for a new native meeting run after: {before or '<none>'}",
            ]
        ),
        flush=True,
    )

    run_id = wait_for_new_meeting_run(
        logs_root=logs_root,
        previous_run_id=before,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=max(0.5, args.poll_seconds),
    )
    if not run_id:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "timeout_waiting_for_new_meeting_run",
                    "logs_root": str(logs_root),
                    "previous_run_id": before,
                    "timeout_seconds": args.timeout_seconds,
                },
                indent=2,
            )
        )
        raise SystemExit(1)

    cmd = [
        sys.executable,
        str(ROOT / "scripts/verify_live_provider_meeting.py"),
        "--json",
        "--run-id",
        run_id,
        "--logs-root",
        str(logs_root),
        "--min-duration-seconds",
        str(args.min_duration_seconds),
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, end="")
    raise SystemExit(proc.returncode)


def wait_for_new_meeting_run(
    *,
    logs_root: Path,
    previous_run_id: str,
    timeout_seconds: float,
    poll_seconds: float,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        run_id = latest_meeting_run_id(
            read_jsonl(logs_root / "history.jsonl"),
            read_jsonl(logs_root / "native_events.jsonl"),
        )
        if run_id and run_id != previous_run_id:
            return run_id
        time.sleep(poll_seconds)
    return ""


if __name__ == "__main__":
    main()
