from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SUCCESS_STATUSES = {"paste_attempted", "finalizer_replaced"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether RambleFix hotkey successes have replayable retained audio.")
    parser.add_argument("--history", type=Path, default=Path("logs/history.jsonl"))
    parser.add_argument("--audio-dir", type=Path, default=Path("logs/hotkey_audio"))
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--since-created-at", default="", help="Only include history rows with created_at >= this ISO timestamp.")
    parser.add_argument("--bundle-id", default="com.ramblefix.local")
    parser.add_argument("--min-retained-success", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows = _read_rows(args.history)
    if args.since_created_at:
        rows = [row for row in rows if str(row.get("created_at") or "") >= args.since_created_at]
    recent = rows[-max(1, args.limit) :]
    success_rows = [row for row in recent if _is_success(row)]
    retained_success = [row for row in success_rows if _audio_exists(row)]
    missing_success = [row for row in success_rows if not _audio_exists(row)]
    payload = {
        "history": str(args.history),
        "since_created_at": args.since_created_at,
        "history_rows": len(rows),
        "recent_rows": len(recent),
        "capture_eval_audio_pref": _read_capture_pref(args.bundle_id),
        "root_wav_count": len(list(args.audio_dir.glob("*.wav"))) if args.audio_dir.exists() else 0,
        "success_rows": len(success_rows),
        "retained_success_rows": len(retained_success),
        "missing_success_audio_rows": len(missing_success),
        "latest_successes": [_row_summary(row) for row in success_rows[-8:]],
        "latest_missing_success_audio": [_row_summary(row) for row in missing_success[-8:]],
    }
    ok = len(retained_success) >= args.min_retained_success
    payload["ok"] = ok

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"capture_eval_audio_pref: {payload['capture_eval_audio_pref']}")
        print(f"history_rows: {payload['history_rows']} recent_rows: {payload['recent_rows']}")
        print(f"success_rows: {payload['success_rows']}")
        print(f"retained_success_rows: {payload['retained_success_rows']}")
        print(f"missing_success_audio_rows: {payload['missing_success_audio_rows']}")
        print(f"root_wav_count: {payload['root_wav_count']}")
        if payload["latest_missing_success_audio"]:
            print("latest missing success audio:")
            for row in payload["latest_missing_success_audio"]:
                print(f"- {row['created_at']} {row['status']} r2p={row['release_to_paste_seconds']} text={row['text']}")

    if not ok:
        sys.exit(1)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _is_success(row: dict[str, Any]) -> bool:
    if str(row.get("mode") or "") != "dictation":
        return False
    if str(row.get("status") or "") not in SUCCESS_STATUSES:
        return False
    return row.get("paste_success") is True


def _audio_exists(row: dict[str, Any]) -> bool:
    audio_path = str(row.get("audio_path") or "")
    return bool(audio_path) and Path(audio_path).exists()


def _row_summary(row: dict[str, Any]) -> dict[str, Any]:
    timings = row.get("timings") if isinstance(row.get("timings"), dict) else {}
    text = str(row.get("pasted_text") or row.get("corrected_text") or row.get("raw_text") or "")
    return {
        "created_at": row.get("created_at") or "",
        "run_id": row.get("run_id") or "",
        "status": row.get("status") or "",
        "audio_path": row.get("audio_path") or "",
        "audio_exists": _audio_exists(row),
        "audio_retained": row.get("audio_retained"),
        "audio_retention_reason": row.get("audio_retention_reason"),
        "release_to_paste_seconds": timings.get("release_to_paste_seconds"),
        "text": text[:120],
    }


def _read_capture_pref(bundle_id: str) -> str:
    try:
        result = subprocess.run(
            ["defaults", "read", bundle_id, "ramblefix.captureEvalAudio"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return "unknown"
    if result.returncode != 0:
        return "unset"
    return result.stdout.strip()


if __name__ == "__main__":
    main()
