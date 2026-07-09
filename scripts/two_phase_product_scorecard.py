from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from product_scorecard import score_row


def main() -> None:
    parser = argparse.ArgumentParser(description="Score fast-first dictation plus optional async polish candidates.")
    parser.add_argument("--first-results", type=Path, required=True)
    parser.add_argument("--polish-results", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--policy",
        choices=["empty-only", "oracle-under-2.5", "oracle-any"],
        default="empty-only",
        help="Which first-pass rows are eligible for a polish update.",
    )
    args = parser.parse_args()

    first_rows = _load_rows(args.first_results)
    polish_by_id = _load_polish(args.polish_results)

    first_scored = [score_row(row, mode="meaning") for row in first_rows]
    polished_rows, updates = _apply_polish(first_rows, polish_by_id, policy=args.policy)
    polished_scored = [score_row(row, mode="meaning") for row in polished_rows]

    payload = {
        "policy": args.policy,
        "first": _summary(first_scored),
        "polished": _summary(polished_scored),
        "updates": updates,
    }
    text = _markdown(payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        args.output.with_suffix(".json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        print(text)


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError(f"expected list in {path}")
    return rows


def _load_polish(paths: list[Path]) -> dict[str, list[dict[str, Any]]]:
    by_id: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        for row in _load_rows(path):
            by_id.setdefault(str(row.get("id") or ""), []).append(row)
    return by_id


def _apply_polish(
    first_rows: list[dict[str, Any]],
    polish_by_id: dict[str, list[dict[str, Any]]],
    *,
    policy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    for row in first_rows:
        first = score_row(row, mode="meaning")
        candidates = polish_by_id.get(str(row.get("id") or ""), [])
        selected = _select_candidate(row, first, candidates, policy=policy)
        if selected is None:
            out.append(dict(row))
            continue
        updated = dict(row)
        for key in (
            "actual",
            "wer",
            "meaning_loss",
            "meaning_coverage",
            "term_coverage",
            "term_hits",
            "term_misses",
            "term_terms",
            "repeat",
        ):
            if key in selected:
                updated[key] = selected[key]
        updated["seconds"] = selected.get("seconds", row.get("seconds"))
        meta = dict(updated.get("meta") or {})
        meta["first_seconds"] = row.get("seconds")
        meta["polish_backend"] = selected.get("backend")
        meta["polish_seconds"] = selected.get("seconds")
        updated["meta"] = meta
        out.append(updated)
        polished = score_row(updated, mode="meaning")
        updates.append(
            {
                "id": row.get("id"),
                "backend": selected.get("backend"),
                "first_score": first["useful_dictation_score"],
                "polished_score": polished["useful_dictation_score"],
                "first_seconds": row.get("seconds"),
                "polish_seconds": selected.get("seconds"),
                "first_actual": str(row.get("actual") or "")[:160],
                "polished_actual": str(selected.get("actual") or "")[:160],
            }
        )
    return out, updates


def _select_candidate(
    first_row: dict[str, Any],
    first_score: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    policy: str,
) -> dict[str, Any] | None:
    if not candidates:
        return None
    if policy == "empty-only" and not _is_fast_failure(first_row):
        return None

    scored: list[tuple[float, float, dict[str, Any]]] = []
    for candidate in candidates:
        candidate_score = score_row(candidate, mode="meaning")
        seconds = float(candidate.get("seconds") or 999.0)
        if policy == "oracle-under-2.5" and seconds > 2.5:
            continue
        if policy in {"oracle-under-2.5", "oracle-any"}:
            if candidate_score["useful_dictation_score"] <= first_score["useful_dictation_score"]:
                continue
        scored.append((float(candidate_score["useful_dictation_score"]), -seconds, candidate))
    if not scored:
        return None
    return sorted(scored, reverse=True)[0][2]


def _is_fast_failure(row: dict[str, Any]) -> bool:
    actual = str(row.get("actual") or "")
    if actual.lower().startswith("asr failure detected"):
        return True
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    return str(meta.get("route") or "") == "fast_server_process_fallback_skipped"


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    seconds = sorted(float(row.get("seconds") or 0.0) for row in rows)
    return {
        "rows": len(rows),
        "useful": round(sum(float(row["useful_dictation_score"]) for row in rows) / len(rows), 3),
        "usable": round(sum(1 for row in rows if row["usable"]) / len(rows), 3),
        "p50": round(statistics.median(seconds), 3),
        "p95": round(seconds[min(len(seconds) - 1, int(0.95 * (len(seconds) - 1)))], 3),
        "hang": round(sum(1 for row in rows if row["hang_risk"]) / len(rows), 3),
    }


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Two-Phase Product Scorecard",
        "",
        f"Policy: `{payload['policy']}`",
        "",
        "| Phase | Rows | Useful | Usable | p50 sec | p95 sec | Hang |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ("first", "polished"):
        summary = payload[name]
        lines.append(
            f"| {name} | {summary['rows']} | {summary['useful']:.3f} | {summary['usable']:.3f} | "
            f"{summary['p50']:.3f} | {summary['p95']:.3f} | {summary['hang']:.3f} |"
        )
    lines.extend(["", f"Updates: `{len(payload['updates'])}`", ""])
    for update in payload["updates"][:40]:
        lines.append(
            f"- `{update['id']}` via `{update['backend']}`: "
            f"{update['first_score']:.3f} -> {update['polished_score']:.3f}, "
            f"{update['first_seconds']}s -> {update['polish_seconds']}s"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
