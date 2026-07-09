#!/usr/bin/env python3
"""Score same-WAV results against real-user gold variants."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from ramblefix.eval import meaning_coverage, meaning_loss, term_coverage_report, word_error_rate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--rows", type=Path, required=True, help="app_competitor_rows.json from same-WAV probe")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    corpus = {str(row["id"]): row for row in json.loads(args.corpus.read_text(encoding="utf-8"))}
    raw_rows = json.loads(args.rows.read_text(encoding="utf-8"))
    scored = []
    for row in raw_rows:
        row_id = row_id_for(row)
        if row_id in corpus:
            scored.append(score_row(row, corpus[row_id]))
    payload = {"summary": summarize(scored), "rows": scored}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "variant_scorecard.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_dir / "variant_scorecard.md").write_text(markdown(payload), encoding="utf-8")
    print(args.output_dir / "variant_scorecard.md")


def score_row(row: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    actual = str(row.get("actual") or "")
    variants = item.get("gold_variants") or [{"source": "primary", "text": item.get("gold", "")}]
    variant_scores = []
    for variant in variants:
        gold = str(variant.get("text") or "")
        terms = [str(term) for term in item.get("critical_terms") or item.get("terms") or [] if str(term).strip()]
        term_report = term_coverage_report(gold, actual, terms)
        coverage = meaning_coverage(gold, actual) if gold else 0.0
        wer = word_error_rate(gold, actual) if gold else 1.0
        score = 0.70 * coverage + 0.20 * (term_report["coverage"] if term_report["coverage"] is not None else coverage) + 0.10 * max(0.0, 1.0 - min(wer, 1.0))
        variant_scores.append(
            {
                "source": variant.get("source") or "variant",
                "gold": gold,
                "score": score,
                "meaning_coverage": coverage,
                "wer": wer,
                "meaning_loss": meaning_loss(gold, actual) if gold else 1.0,
                "term_coverage": term_report["coverage"],
                "term_misses": term_report["misses"],
            }
        )
    best = max(variant_scores, key=lambda candidate: candidate["score"]) if variant_scores else {}
    out = dict(row)
    out.update(
        {
            "id": row_id_for(row),
            "backend": backend_for(row),
            "seconds": seconds_for(row),
            "bucket": "hinglish" if item.get("category") == "real_use_hindi_hinglish_probe" else "english",
            "reference_trust": item.get("reference_trust"),
            "gold_disputed": bool(item.get("gold_disputed")),
            "best_gold_source": best.get("source"),
            "best_gold": best.get("gold"),
            "variant_useful_score": round(float(best.get("score") or 0.0), 3),
            "variant_meaning_coverage": round(float(best.get("meaning_coverage") or 0.0), 3),
            "variant_wer": round(float(best.get("wer") or 1.0), 3),
            "variant_term_coverage": best.get("term_coverage"),
            "variant_term_misses": best.get("term_misses") or [],
        }
    )
    return out


def row_id_for(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("corpus_id") or "")


def backend_for(row: dict[str, Any]) -> str:
    return str(row.get("backend") or row.get("tool") or "unknown")


def seconds_for(row: dict[str, Any]) -> float | None:
    if backend_for(row) == "wispr_flow":
        if row.get("wispr_e2e_latency_ms") is not None:
            try:
                return float(row["wispr_e2e_latency_ms"]) / 1000.0
            except (TypeError, ValueError):
                pass
        return None
    if row.get("seconds") is not None:
        try:
            return float(row["seconds"])
        except (TypeError, ValueError):
            pass
    if row.get("wispr_e2e_latency_ms") is not None:
        try:
            return float(row["wispr_e2e_latency_ms"]) / 1000.0
        except (TypeError, ValueError):
            pass
    timestamps = row.get("timestamps")
    if isinstance(timestamps, dict) and timestamps.get("hotkey_up") is not None and timestamps.get("paste_done") is not None:
        try:
            return float(timestamps["paste_done"]) - float(timestamps["hotkey_up"])
        except (TypeError, ValueError):
            pass
    return None


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for backend in sorted({backend_for(row) for row in rows}):
        backend_rows = [row for row in rows if backend_for(row) == backend]
        for bucket in ["english", "hinglish", "all"]:
            bucket_rows = backend_rows if bucket == "all" else [row for row in backend_rows if row.get("bucket") == bucket]
            if not bucket_rows:
                continue
            seconds = [float(row["seconds"]) for row in bucket_rows if row.get("seconds") is not None]
            summaries.append(
                {
                    "backend": backend,
                    "bucket": bucket,
                    "rows": len(bucket_rows),
                    "useful": round(mean(bucket_rows, "variant_useful_score"), 3),
                    "meaning": round(mean(bucket_rows, "variant_meaning_coverage"), 3),
                    "wer": round(mean(bucket_rows, "variant_wer"), 3),
                    "terms": round(mean_optional(bucket_rows, "variant_term_coverage"), 3) if any(row.get("variant_term_coverage") is not None for row in bucket_rows) else None,
                    "p50_seconds": round(percentile(seconds, 0.50), 3) if seconds else None,
                    "p95_seconds": round(percentile(seconds, 0.95), 3) if seconds else None,
                    "errors": sum(1 for row in bucket_rows if row.get("error")),
                    "disputed_gold_rows": sum(1 for row in bucket_rows if row.get("gold_disputed")),
                }
            )
    return summaries


def mean(rows: list[dict[str, Any]], key: str) -> float:
    return statistics.mean(float(row.get(key) or 0.0) for row in rows)


def mean_optional(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return statistics.mean(values) if values else 0.0


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * q
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Actual User Gold-Variant Scorecard",
        "",
        "## Summary",
        "",
        "| Backend | Bucket | Rows | Useful | Meaning | WER | Terms | p50 s | p95 s | Errors | Disputed Gold |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["summary"]:
        lines.append(
            "| {backend} | {bucket} | {rows} | {useful:.3f} | {meaning:.3f} | {wer:.3f} | {terms} | {p50} | {p95} | {errors} | {disputed_gold_rows} |".format(
                backend=esc(str(row["backend"])),
                bucket=row["bucket"],
                rows=row["rows"],
                useful=row["useful"],
                meaning=row["meaning"],
                wer=row["wer"],
                terms="" if row["terms"] is None else f"{row['terms']:.3f}",
                p50="" if row["p50_seconds"] is None else f"{row['p50_seconds']:.3f}",
                p95="" if row["p95_seconds"] is None else f"{row['p95_seconds']:.3f}",
                errors=row["errors"],
                disputed_gold_rows=row["disputed_gold_rows"],
            )
        )
    lines.extend(["", "## Miss Examples", ""])
    misses = sorted(payload["rows"], key=lambda row: (float(row.get("variant_useful_score") or 0.0), row.get("id", "")))[:8]
    lines.append("| ID | Backend | Bucket | Score | Gold Variant | Actual | Term Misses |")
    lines.append("| --- | --- | --- | ---: | --- | --- | --- |")
    for row in misses:
        lines.append(
            f"| {esc(str(row.get('id')))} | {esc(str(row.get('backend')))} | {row.get('bucket')} | {float(row.get('variant_useful_score') or 0):.3f} | {esc(short(str(row.get('best_gold') or '')))} | {esc(short(str(row.get('actual') or '')))} | {esc(', '.join(row.get('variant_term_misses') or []))} |"
        )
    return "\n".join(lines) + "\n"


def short(text: str, limit: int = 140) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def esc(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    main()
