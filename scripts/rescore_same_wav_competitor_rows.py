#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import meaning_coverage, meaning_loss, repeated_substring_score, term_coverage_report, word_error_rate  # noqa: E402


DEFAULT_INPUT = (
    ROOT
    / "eval_runs/same-wav-app-competitor-probe-20260614/public95-openwhispr-ramblefix/app_competitor_rows.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "eval_runs/same-wav-app-competitor-probe-20260614/public95-openwhispr-ramblefix-current-score"


def main() -> None:
    parser = argparse.ArgumentParser(description="Rescore saved same-WAV competitor rows with the current product scorer.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--mode", choices=["meaning", "verbatim"], default="meaning")
    args = parser.parse_args()

    input_path = _resolve(args.input)
    output_dir = _resolve(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = json.loads(input_path.read_text(encoding="utf-8"))
    scorer = _load_product_scorecard()
    rescored = [scorer.score_row(_recompute_row(row), mode=args.mode) for row in rows]
    payload = {
        "mode": args.mode,
        "input": _rel(input_path),
        "rows": rescored,
        "summary": _summary(rescored),
        "claim_boundary": _claim_boundary(rescored),
    }
    (output_dir / "scorecard.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_dir / "scorecard.md").write_text(_markdown(payload), encoding="utf-8")
    print(_console(payload))
    print(f"wrote {output_dir / 'scorecard.md'}")


def _recompute_row(row: dict[str, Any]) -> dict[str, Any]:
    gold = str(row.get("gold") or "")
    actual = str(row.get("actual") or "")
    term_report = term_coverage_report(gold, actual)
    out = dict(row)
    out.update(
        {
            "wer": word_error_rate(gold, actual) if gold else None,
            "meaning_loss": meaning_loss(gold, actual) if gold else None,
            "meaning_coverage": meaning_coverage(gold, actual) if gold else None,
            "term_coverage": term_report["coverage"],
            "term_hits": term_report["hits"],
            "term_misses": term_report["misses"],
            "term_terms": term_report["terms"],
            "repeat": repeated_substring_score(actual),
        }
    )
    return out


def _summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("backend") or ""), _bucket(row))].append(row)
    return [_metric_row(backend, bucket, group) for (backend, bucket), group in sorted(grouped.items())]


def _metric_row(backend: str, bucket: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [float(row.get("useful_dictation_score") or 0.0) for row in rows]
    wers = [float(row.get("wer") or 0.0) for row in rows]
    coverage = [float(row.get("meaning_coverage") or 0.0) for row in rows]
    terms = [float(row.get("term_coverage") if row.get("term_coverage") is not None else row.get("meaning_coverage") or 0.0) for row in rows]
    seconds = [float(row.get("seconds") or 0.0) for row in rows]
    return {
        "backend": backend,
        "bucket": bucket,
        "rows": len(rows),
        "useful": round(statistics.mean(scores), 3),
        "wer": round(statistics.mean(wers), 3),
        "coverage": round(statistics.mean(coverage), 3),
        "term_coverage": round(statistics.mean(terms), 3),
        "p50_seconds": _percentile(seconds, 50),
        "p95_seconds": _percentile(seconds, 95),
        "usable_rate": round(sum(1 for row in rows if row.get("usable")) / len(rows), 3),
        "fast_rate": round(sum(1 for row in rows if row.get("fast")) / len(rows), 3),
        "hang_risk_rate": round(sum(1 for row in rows if row.get("hang_risk")) / len(rows), 3),
    }


def _claim_boundary(rows: list[dict[str, Any]]) -> list[str]:
    backends = {str(row.get("backend") or "") for row in rows}
    claims = []
    if "ramblefix_launch_engine_v1_hinglish" in backends:
        claims.append("Hinglish/code-switch engine comparison is available against OpenWhispr bundled whisper-server.")
    if "ramblefix_launch_engine_v1_fast" in backends:
        claims.append("English engine comparison is available against OpenWhispr bundled whisper-server.")
    claims.append("This is engine-level evidence, not full app UX evidence.")
    claims.append("TypeWhisper, Wispr Flow, Handy, Apple Dictation, VoiceInk, and OpenWhispr Parakeet remain unproven same-WAV app-level comparisons.")
    return claims


def _bucket(row: dict[str, Any]) -> str:
    category = str(row.get("category") or "").lower()
    if "openslr" in category or "hinglish" in category or "hindi" in category:
        return "hindi_english"
    return "english_only"


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Same-WAV Competitor Rescore",
        "",
        f"- Input: `{payload['input']}`",
        f"- Mode: `{payload['mode']}`",
        "",
        "## Summary",
        "",
        "| Backend | Bucket | Rows | Useful | WER | Coverage | Terms | p50 | p95 | Usable | Fast | Hang |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["summary"]:
        lines.append(
            f"| {row['backend']} | {row['bucket']} | {row['rows']} | {row['useful']:.3f} | {row['wer']:.3f} | "
            f"{row['coverage']:.3f} | {row['term_coverage']:.3f} | {row['p50_seconds']:.3f}s | {row['p95_seconds']:.3f}s | "
            f"{row['usable_rate']:.3f} | {row['fast_rate']:.3f} | {row['hang_risk_rate']:.3f} |"
        )
    lines.extend(["", "## Claim Boundary", ""])
    lines.extend(f"- {claim}" for claim in payload["claim_boundary"])
    lines.append("")
    return "\n".join(lines)


def _console(payload: dict[str, Any]) -> str:
    rows = payload["summary"]
    interesting = [
        row
        for row in rows
        if row["backend"] in {
            "ramblefix_launch_engine_v1_fast",
            "ramblefix_launch_engine_v1_hinglish",
            "openwhispr_bundle_whisper_server_small",
            "openwhispr_bundle_whisper_server_base",
        }
    ]
    return "\n".join(
        [
            "Same-WAV competitor rescore",
            *[
                f"{row['backend']} {row['bucket']}: useful={row['useful']:.3f} p95={row['p95_seconds']:.3f}s rows={row['rows']}"
                for row in interesting
            ],
        ]
    )


def _load_product_scorecard() -> Any:
    path = ROOT / "scripts/product_scorecard.py"
    spec = importlib.util.spec_from_file_location("product_scorecard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["product_scorecard"] = module
    spec.loader.exec_module(module)
    return module


def _percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1))))
    return round(ordered[index], 3)


def _resolve(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


if __name__ == "__main__":
    main()
