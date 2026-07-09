#!/usr/bin/env python3
"""Summarize public launch pool results by benchmark bucket."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from product_scorecard import score_row  # noqa: E402


def percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1))))
    return round(ordered[idx], 3)


def mean(values: list[float]) -> float:
    return round(statistics.mean(values), 3) if values else 0.0


def summarize(rows: list[dict[str, Any]], group_key: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("backend", "missing")), str(row.get(group_key, "missing")))].append(row)

    summary: list[dict[str, Any]] = []
    for (backend, group), bucket in grouped.items():
        seconds = [float(row.get("seconds") or 0.0) for row in bucket]
        wer = [float(row["wer"]) for row in bucket if row.get("wer") is not None]
        coverage = [float(row.get("meaning_coverage") or 0.0) for row in bucket]
        terms = [
            float(row.get("term_coverage"))
            for row in bucket
            if row.get("term_coverage") is not None
        ]
        meaning_scores = [float(score_row(row, mode="meaning")["useful_dictation_score"]) for row in bucket]
        verbatim_scores = [float(score_row(row, mode="verbatim")["useful_dictation_score"]) for row in bucket]
        summary.append(
            {
                "backend": backend,
                group_key: group,
                "clips": len(bucket),
                "avg_wer": mean(wer),
                "avg_meaning_coverage": mean(coverage),
                "avg_term_coverage": mean(terms),
                "avg_meaning_score": mean(meaning_scores),
                "avg_verbatim_score": mean(verbatim_scores),
                "p50_seconds": round(statistics.median(seconds), 3),
                "p95_seconds": percentile(seconds, 95),
                "hang_rate": round(
                    sum(1 for row in bucket if float(row.get("seconds") or 0.0) > 6.0 or row.get("error"))
                    / len(bucket),
                    3,
                ),
            }
        )
    return sorted(summary, key=lambda row: (row["backend"], -row["clips"], row[group_key]))


def table(rows: list[dict[str, Any]], group_key: str) -> list[str]:
    headers = [
        "backend",
        group_key,
        "clips",
        "avg_wer",
        "meaning",
        "terms",
        "score_meaning",
        "score_verbatim",
        "p50_s",
        "p95_s",
        "hang",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        values = [
            row["backend"],
            row[group_key],
            str(row["clips"]),
            f'{row["avg_wer"]:.3f}',
            f'{row["avg_meaning_coverage"]:.3f}',
            f'{row["avg_term_coverage"]:.3f}',
            f'{row["avg_meaning_score"]:.3f}',
            f'{row["avg_verbatim_score"]:.3f}',
            f'{row["p50_seconds"]:.3f}',
            f'{row["p95_seconds"]:.3f}',
            f'{row["hang_rate"]:.3f}',
        ]
        lines.append("| " + " | ".join(values) + " |")
    lines.append("")
    return lines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    corpus_rows = json.loads(args.corpus.read_text(encoding="utf-8"))
    results = json.loads(args.results.read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in corpus_rows}
    merged = []
    for result in results:
        row = dict(result)
        source = by_id.get(row["id"], {})
        for key in ("pool_bucket", "source", "region", "language", "reference_trust"):
            if key not in row and key in source:
                row[key] = source[key]
        merged.append(row)

    payload = {
        "by_pool_bucket": summarize(merged, "pool_bucket"),
        "by_category": summarize(merged, "category"),
        "rows": merged,
    }

    lines = [
        "# Public Launch Pool Result Summary",
        "",
        f"- Corpus: `{args.corpus}`",
        f"- Results: `{args.results}`",
        f"- Clips scored: {len(merged)}",
        "",
        "## By Pool Bucket",
        "",
    ]
    lines.extend(table(payload["by_pool_bucket"], "pool_bucket"))
    lines.extend(["## By Category", ""])
    lines.extend(table(payload["by_category"], "category"))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
