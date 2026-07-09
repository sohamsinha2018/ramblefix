#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


FIRST_OUTPUT_STATUSES = {"paste_attempted", "failed", "no_speech", "too_short"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize recent RambleFix hotkey dictation rows.")
    parser.add_argument("--history", default="logs/history.jsonl")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    rows = _read_rows(Path(args.history))
    rows = [
        row
        for row in rows
        if row.get("mode") == "dictation"
        and str(row.get("status") or "") in FIRST_OUTPUT_STATUSES
    ][-args.limit :]

    if not rows:
        print("No hotkey dictation rows found.")
        return

    latencies = [_release_to_paste_seconds(row) for row in rows]
    latencies = [value for value in latencies if value is not None]
    paste_success = [_paste_success(row) for row in rows]
    blanks = [_is_blank_or_no_speech(row) for row in rows]

    print(f"rows: {len(rows)}")
    print(f"paste_success_rate: {_rate(paste_success):.3f}")
    print(f"blank_or_no_speech_rate: {_rate(blanks):.3f}")
    if latencies:
        print(f"p50_release_to_paste_seconds: {statistics.median(latencies):.3f}")
        print(f"p95_release_to_paste_seconds: {_percentile(latencies, 0.95):.3f}")
    else:
        print("p50_release_to_paste_seconds: n/a")
        print("p95_release_to_paste_seconds: n/a")

    print()
    print("recent:")
    for row in rows:
        latency = _release_to_paste_seconds(row)
        latency_text = "n/a" if latency is None else f"{latency:.3f}s"
        text = str(row.get("corrected_text") or row.get("raw_text") or "").replace("\n", " ")
        if len(text) > 90:
            text = text[:87] + "..."
        print(
            f"- {row.get('run_id')} | {row.get('status')} | "
            f"paste={_paste_success(row)} | blank={_is_blank_or_no_speech(row)} | "
            f"{latency_text} | {row.get('route') or row.get('asr_engine') or ''} | {text}"
        )


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _release_to_paste_seconds(row: dict[str, Any]) -> float | None:
    timings = row.get("timings")
    if isinstance(timings, dict) and timings.get("release_to_paste_seconds") is not None:
        return float(timings["release_to_paste_seconds"])
    return None


def _paste_success(row: dict[str, Any]) -> bool:
    if "paste_success" in row:
        return bool(row["paste_success"])
    return row.get("status") == "paste_attempted"


def _is_blank_or_no_speech(row: dict[str, Any]) -> bool:
    if row.get("blank_or_no_speech") is True:
        return True
    if row.get("error_type") in {"blank_or_no_speech", "too_short_capture"}:
        return True
    text = str(row.get("corrected_text") or row.get("raw_text") or "").strip().lower()
    return text in {"[blank_audio]", "blank audio", "no speech", "no speech detected", "silence"}


def _rate(values: list[bool]) -> float:
    return sum(1 for value in values if value) / len(values) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


if __name__ == "__main__":
    main()
