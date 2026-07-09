#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval_dictate_audio_product_path import _run_item as run_product_item  # noqa: E402
from ramblefix.eval import meaning_coverage, meaning_loss, repeated_substring_score, term_coverage_report, word_error_rate  # noqa: E402


POLICY_TOOL = ROOT / "native/RambleFixHotkey/.build/debug/RambleFixHotkeyPolicyTool"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run current RambleFix first-paste path and native structure policy.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--skip-process-fallback", action="store_true")
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    items = [item for item in json.loads(args.corpus.read_text(encoding="utf-8")) if isinstance(item, dict)]
    if args.limit:
        items = items[: args.limit]

    first_rows: list[dict[str, Any]] = []
    for item in items:
        row = run_product_item(
            item,
            timeout_seconds=args.timeout_seconds,
            skip_process_fallback=args.skip_process_fallback,
            no_cleanup=args.no_cleanup,
        )
        row["backend"] = "ramblefix_current_first_paste"
        first_rows.append(row)
        status = "ERR" if row.get("error") else "OK"
        print(f"{status} first {row['id']} {row['seconds']}s {str(row.get('actual') or '')[:100]}")

    structure_rows = structure_rows_for(items, first_rows)
    rows = []
    for first, structured in zip(first_rows, structure_rows, strict=True):
        rows.append(first)
        rows.append(structured)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


def structure_rows_for(items: list[dict[str, Any]], first_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requests = [
        {"id": str(row["id"]), "draft": str(row.get("actual") or ""), "final": str(row.get("actual") or "")}
        for row in first_rows
        if not row.get("error") and str(row.get("actual") or "").strip()
    ]
    responses = run_structure_policy(requests) if requests else {}
    policy_overhead = max(0.0, responses.pop("__wall_seconds__", 0.0) / max(1, len(requests))) if responses else 0.0

    rows: list[dict[str, Any]] = []
    for item, first in zip(items, first_rows, strict=True):
        row_id = str(first["id"])
        response = responses.get(row_id) or {}
        accepted = bool(response.get("accepted")) and not first.get("error")
        final = str(response.get("final") or first.get("actual") or "")
        actual = final if accepted else str(first.get("actual") or "")
        changed = accepted and actual != str(first.get("actual") or "")
        meta = dict(first.get("meta") or {})
        meta.update(
            {
                "first_paste_seconds": first.get("seconds"),
                "structure_policy_seconds_est": round(policy_overhead, 4),
                "structure_accepted": accepted,
                "structure_changed": changed,
                "structure_rules": response.get("rules") or [],
                "structure_dropped_terms": response.get("droppedProtectedTerms") or [],
                "structure_input": "exact_first_paste_text",
            }
        )
        rows.append(
            scored_row(
                item,
                backend="ramblefix_current_structured_if_unchanged",
                actual=actual,
                seconds=round(float(first.get("seconds") or 0.0) + policy_overhead, 3),
                meta=meta,
                error=first.get("error"),
            )
        )
    return rows


def run_structure_policy(requests: list[dict[str, str]]) -> dict[str, Any]:
    if not POLICY_TOOL.exists():
        raise RuntimeError(f"missing policy tool: {POLICY_TOOL}")
    started = time.perf_counter()
    proc = subprocess.run(
        [
            str(POLICY_TOOL),
            "--policy",
            "structure",
            "--project-root",
            str(ROOT),
        ],
        cwd=ROOT,
        input=json.dumps(requests, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    wall = time.perf_counter() - started
    payload = json.loads(proc.stdout)
    by_id = {str(row["id"]): row for row in payload}
    by_id["__wall_seconds__"] = wall
    return by_id


def scored_row(
    item: dict[str, Any],
    *,
    backend: str,
    actual: str,
    seconds: float,
    meta: dict[str, Any],
    error: object,
) -> dict[str, Any]:
    gold = str(item.get("gold") or "")
    terms = item.get("critical") or item.get("critical_terms") or item.get("terms") or item.get("anchors")
    term_report = term_coverage_report(gold, actual, terms)
    audio = audio_path(item)
    return {
        "id": str(item.get("id") or ""),
        "category": str(item.get("category") or ""),
        "backend": backend,
        "audio": str(audio),
        "gold": gold,
        "actual": actual,
        "wer": word_error_rate(gold, actual) if gold else None,
        "meaning_loss": meaning_loss(gold, actual) if gold else None,
        "meaning_coverage": meaning_coverage(gold, actual) if gold else None,
        "term_coverage": term_report["coverage"],
        "term_hits": term_report["hits"],
        "term_misses": term_report["misses"],
        "term_terms": term_report["terms"],
        "repeat": repeated_substring_score(actual),
        "seconds": seconds,
        "meta": meta,
        "error": error,
    }


def audio_path(item: dict[str, Any]) -> Path:
    raw = str(item.get("audio") or "")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


if __name__ == "__main__":
    main()
