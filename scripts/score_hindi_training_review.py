from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import meaning_coverage, term_coverage_report, word_error_rate


DEFAULT_REVIEW = ROOT / "eval_runs/fresh-hindi-probe-20260629/hindi_training_review_20260630/review_set.json"
DEFAULT_OUTPUT = ROOT / "eval_runs/fresh-hindi-probe-20260629/hindi_training_review_20260630/scorecard.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Score Hindi/Hinglish review candidates against filled gold labels.")
    parser.add_argument("--review-json", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--candidates",
        default="fast_text,current_final,srota_raw,vosk_hi_large",
        help="Comma-separated review_set row fields to score.",
    )
    args = parser.parse_args()

    review = json.loads(args.review_json.read_text(encoding="utf-8"))
    candidate_fields = [item.strip() for item in args.candidates.split(",") if item.strip()]
    labeled_rows = [row for row in review.get("rows", []) if str(row.get("gold_intent") or "").strip()]
    rows: list[dict[str, Any]] = []
    for row in labeled_rows:
        gold = str(row["gold_intent"]).strip()
        terms = _expected_terms(gold)
        for field in candidate_fields:
            actual = str(row.get(field) or "").strip()
            term_report = term_coverage_report(gold, actual, terms)
            rows.append(
                {
                    "run_id": row.get("run_id"),
                    "candidate": field,
                    "audio_seconds": row.get("audio_seconds"),
                    "tail_seconds": _tail_seconds(row, field),
                    "gold": gold,
                    "actual": actual,
                    "wer": word_error_rate(gold, actual),
                    "meaning_coverage": meaning_coverage(gold, actual),
                    "term_coverage": term_report["coverage"],
                    "term_misses": term_report["misses"],
                    "repetition": _repetition_ratio(actual),
                    "empty": not bool(actual),
                }
            )

    payload = {
        "review_json": str(args.review_json),
        "labeled_rows": len(labeled_rows),
        "total_rows": len(review.get("rows", [])),
        "candidate_fields": candidate_fields,
        "summary": _summary(rows),
        "rows": rows,
        "status": "needs_labels" if not labeled_rows else "scored",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(_markdown(payload), encoding="utf-8")
    print(_markdown(payload))
    print(f"wrote {args.output}")


def _tail_seconds(row: dict[str, Any], field: str) -> float:
    if field in {"fast_text", "current_final"}:
        return 0.0
    value = row.get("tail_seconds")
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_candidate.setdefault(str(row["candidate"]), []).append(row)

    summary: list[dict[str, Any]] = []
    for candidate, bucket in sorted(by_candidate.items()):
        coverage = [float(row["meaning_coverage"]) for row in bucket]
        wer = [float(row["wer"]) for row in bucket]
        term_values = [float(row["term_coverage"]) for row in bucket if row.get("term_coverage") is not None]
        tails = [float(row["tail_seconds"]) for row in bucket]
        empty_count = sum(1 for row in bucket if row["empty"])
        repeat_risks = sum(1 for row in bucket if float(row["repetition"]) >= 0.20)
        summary.append(
            {
                "candidate": candidate,
                "clips": len(bucket),
                "avg_meaning_coverage": round(statistics.mean(coverage), 3),
                "median_meaning_coverage": round(statistics.median(coverage), 3),
                "avg_wer": round(statistics.mean(wer), 3),
                "avg_term_coverage": round(statistics.mean(term_values), 3) if term_values else None,
                "p50_tail_seconds": _percentile(tails, 50),
                "p95_tail_seconds": _percentile(tails, 95),
                "perfect_meaning_count": sum(1 for row in bucket if float(row["meaning_coverage"]) >= 0.999),
                "low_meaning_count": sum(1 for row in bucket if float(row["meaning_coverage"]) < 0.75),
                "empty_count": empty_count,
                "repeat_risk_count": repeat_risks,
            }
        )
    return sorted(summary, key=lambda item: (-float(item["avg_meaning_coverage"]), float(item["avg_wer"])))


def _percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((pct / 100) * (len(ordered) - 1))
    return round(ordered[max(0, min(len(ordered) - 1, index))], 3)


def _expected_terms(text: str) -> list[str]:
    terms: list[str] = []
    for match in re.finditer(r"\b(?:[A-Z]{2,}(?:s)?|[A-Z][a-z]+[A-Z][A-Za-z]*|[A-Z][A-Za-z0-9]*\d+[A-Za-z0-9]*)\b", text):
        term = match.group(0)
        if term.lower() not in {"I", "OK"} and term not in terms:
            terms.append(term)
    for known in ["MCP", "API", "QRS", "FMS", "Codex", "Cursor", "RambleFix"]:
        if re.search(rf"\b{re.escape(known)}\b", text, flags=re.IGNORECASE) and known not in terms:
            terms.append(known)
    return terms


def _repetition_ratio(text: str) -> float:
    tokens = re.findall(r"[\w\u0900-\u097f]+", text.lower())
    if not tokens:
        return 0.0
    repeats = sum(1 for left, right in zip(tokens, tokens[1:], strict=False) if left == right)
    return round(repeats / len(tokens), 3)


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Hindi Training Review Scorecard",
        "",
        f"- labeled_rows: `{payload['labeled_rows']}` / `{payload['total_rows']}`",
        f"- status: `{payload['status']}`",
        f"- candidates: `{', '.join(payload['candidate_fields'])}`",
        "",
    ]
    if not payload["summary"]:
        lines.extend(
            [
                "No gold labels found yet.",
                "",
                "Fill `gold_intent` in the review JSON, then rerun this script.",
            ]
        )
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| candidate | clips | avg meaning | median meaning | avg WER | avg terms | p95 tail | perfect | low meaning | empty | repeat risk |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["summary"]:
        lines.append(
            "| {candidate} | {clips} | {avg_meaning_coverage} | {median_meaning_coverage} | "
            "{avg_wer} | {avg_term_coverage} | {p95_tail_seconds} | {perfect_meaning_count} | "
            "{low_meaning_count} | {empty_count} | {repeat_risk_count} |".format(**row)
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
