from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import meaning_coverage, meaning_loss, repeated_substring_score, term_coverage_report, word_error_rate  # noqa: E402
from ramblefix.glossary import apply_glossary  # noqa: E402
DEFAULT_CORPUS = (
    ROOT
    / "eval_runs/goal-stt-optimization-20260703-expanded-v4/confirmed_union38_product_no_pure_hindi_20260703.json"
)
DEFAULT_SCORECARD = (
    ROOT
    / "eval_runs/goal-stt-optimization-20260703-expanded-v4/staged_selector_union38_work_words_patch_20260703/scorecard.json"
)
DEFAULT_DECISIONS = (
    ROOT
    / "eval_runs/goal-stt-optimization-20260703-expanded-v4/staged_selector_union38_work_words_patch_20260703/selector_decisions.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "eval_runs/goal-stt-optimization-20260703-expanded-v5/current_goal_state"


def main() -> None:
    parser = argparse.ArgumentParser(description="Report current RambleFix STT goal state from existing eval artifacts.")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--accelerator-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    corpus_path = _resolve(args.corpus)
    scorecard_path = _resolve(args.scorecard)
    decisions_path = _resolve(args.decisions)
    output_dir = _resolve(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    corpus = _load_json(corpus_path)
    scorecard = _load_json(scorecard_path)
    decisions = _load_json(decisions_path)
    accelerator = _load_accelerator(args.accelerator_dir)
    scored_rows, glossary_changes = _rescore_rows_with_current_glossary(scorecard["rows"], corpus)

    payload = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "objective": {
            "english_useful_score_min": 0.90,
            "hindi_english_useful_score_target": 0.85,
            "polished_output_p95_seconds_max": 4.5,
            "blind_english_overwrites_allowed": 0,
        },
        "inputs": {
            "corpus": _rel(corpus_path),
            "scorecard": _rel(scorecard_path),
            "selector_decisions": _rel(decisions_path),
            "accelerator_health": accelerator.get("path", ""),
        },
        "corpus": _corpus_summary(corpus),
        "split_metrics": _split_metrics(scored_rows),
        "glossary_changes": glossary_changes,
        "selector": _selector_summary(decisions),
        "misses": _misses(scored_rows),
        "accelerator": accelerator,
    }
    payload["decision"] = _decision(payload)

    (output_dir / "goal_current_state.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "goal_current_state.md").write_text(_markdown(payload), encoding="utf-8")

    print(_console(payload))
    print(f"wrote {output_dir / 'goal_current_state.md'}")


def _rescore_rows_with_current_glossary(
    rows: list[dict[str, Any]],
    corpus: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    product_scorecard = _load_product_scorecard()
    corpus_by_id = {str(row.get("id")): row for row in corpus}
    scored: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for row in rows:
        next_row = dict(row)
        before = str(next_row.get("actual") or "")
        after = apply_glossary(before)
        gold = str(next_row.get("gold") or "")
        terms = _explicit_corpus_terms(corpus_by_id.get(str(next_row.get("id"))))
        term_report = term_coverage_report(gold, after, terms)
        next_row.update(
            {
                "actual": after,
                "wer": word_error_rate(gold, after) if gold else None,
                "meaning_loss": meaning_loss(gold, after) if gold else None,
                "meaning_coverage": meaning_coverage(gold, after) if gold else None,
                "term_coverage": term_report["coverage"],
                "term_hits": term_report["hits"],
                "term_misses": term_report["misses"],
                "term_terms": term_report["terms"],
                "repeat": repeated_substring_score(after),
            }
        )
        if after != before:
            next_row.update(
                {
                    "seconds": round(float(next_row.get("seconds") or 0.0) + 0.002, 3),
                }
            )
            changes.append(
                {
                    "id": next_row.get("id"),
                    "backend": next_row.get("backend"),
                    "bucket": next_row.get("bucket"),
                    "before": _short(before, 160),
                    "after": _short(after, 160),
                }
            )
        scored.append(product_scorecard.score_row(next_row, mode=str(row.get("score_mode") or "meaning")))
    return scored, changes


def _explicit_corpus_terms(item: dict[str, Any] | None) -> object | None:
    if not item:
        return None
    for key in ("critical", "critical_terms", "terms", "anchors"):
        value = item.get(key)
        if value:
            return value
    return None


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"missing required artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_product_scorecard() -> Any:
    path = ROOT / "scripts/product_scorecard.py"
    spec = importlib.util.spec_from_file_location("product_scorecard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["product_scorecard"] = module
    spec.loader.exec_module(module)
    return module


def _corpus_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = Counter(str(row.get("bucket") or "unknown") for row in rows)
    return {
        "rows": len(rows),
        "buckets": dict(sorted(buckets.items())),
        "cloud_confirmed": sum(1 for row in rows if row.get("cloud_status") == "cloud_confirmed"),
        "trusted_classifications": sum(1 for row in rows if row.get("classification_status") == "trusted"),
    }


def _split_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("backend") or ""), str(row.get("bucket") or "unknown"))].append(row)

    result = []
    for (backend, bucket), group in sorted(grouped.items()):
        result.append(_metric_row(backend, bucket, group))
    return result


def _metric_row(backend: str, bucket: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [float(row.get("useful_dictation_score") or 0.0) for row in rows]
    seconds = [float(row.get("seconds") or 0.0) for row in rows]
    coverage = [float(row.get("meaning_coverage") or 0.0) for row in rows]
    terms = [float(row.get("term_coverage") if row.get("term_coverage") is not None else row.get("meaning_coverage") or 0.0) for row in rows]
    return {
        "backend": backend,
        "bucket": bucket,
        "clips": len(rows),
        "useful": round(statistics.mean(scores), 3),
        "median_useful": round(statistics.median(scores), 3),
        "coverage": round(statistics.mean(coverage), 3),
        "term_coverage": round(statistics.mean(terms), 3),
        "p50_seconds": _percentile(seconds, 50),
        "p95_seconds": _percentile(seconds, 95),
        "usable_rate": round(sum(1 for row in rows if row.get("usable")) / len(rows), 3),
        "fast_rate": round(sum(1 for row in rows if row.get("fast")) / len(rows), 3),
        "hang_risk_rate": round(sum(1 for row in rows if row.get("hang_risk")) / len(rows), 3),
    }


def _selector_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [row for row in decisions if row.get("accepted")]
    by_bucket = Counter(str(row.get("bucket") or "unknown") for row in accepted)
    reject_reasons: Counter[str] = Counter()
    for row in decisions:
        for reason in row.get("reject_reasons") or []:
            reject_reasons[str(reason).split(":", 1)[0]] += 1
    return {
        "rows": len(decisions),
        "accepted": len(accepted),
        "accepted_by_bucket": dict(sorted(by_bucket.items())),
        "english_only_accepted": by_bucket.get("english_only", 0),
        "hindi_english_accepted": by_bucket.get("hindi_english", 0),
        "reject_reasons": dict(reject_reasons.most_common()),
    }


def _misses(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    product_rows = [row for row in rows if row.get("backend") == "policy_safety_all"]
    out: dict[str, list[dict[str, Any]]] = {}
    for bucket in ("english_only", "hindi_english"):
        bucket_rows = [row for row in product_rows if row.get("bucket") == bucket]
        worst = sorted(bucket_rows, key=lambda row: (float(row.get("useful_dictation_score") or 0.0), -float(row.get("seconds") or 0.0)))[:5]
        out[bucket] = [_miss_row(row) for row in worst]
    return out


def _miss_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "score": row.get("useful_dictation_score"),
        "seconds": row.get("seconds"),
        "route": row.get("route"),
        "term_misses": row.get("term_misses") or [],
        "gold": _short(str(row.get("gold") or ""), 180),
        "actual": _short(str(row.get("actual") or ""), 180),
    }


def _load_accelerator(path: Path | None) -> dict[str, Any]:
    if path is None:
        matches = sorted(
            (ROOT / "eval_runs/goal-stt-optimization-20260703-expanded-v5").glob(
                "accelerated_frontier_union38_after_metal_recovery_*/accelerator_health.json"
            )
        )
        if not matches:
            return {"status": "missing", "path": ""}
        path = matches[-1]
    else:
        path = _resolve(path)
        if path.is_dir():
            path = path / "accelerator_health.json"
    if not path.exists():
        return {"status": "missing", "path": _rel(path)}
    payload = _load_json(path)
    return {
        "status": "ok" if payload.get("accelerator_ok") else "blocked",
        "path": _rel(path),
        "accelerator_ok": bool(payload.get("accelerator_ok")),
        "checks": payload.get("checks") or [],
    }


def _decision(payload: dict[str, Any]) -> dict[str, Any]:
    product = {
        (row["backend"], row["bucket"]): row
        for row in payload["split_metrics"]
        if row["backend"] == "policy_safety_all"
    }
    english = product.get(("policy_safety_all", "english_only"), {})
    hindi = product.get(("policy_safety_all", "hindi_english"), {})
    selector = payload["selector"]
    accelerator = payload["accelerator"]
    blockers: list[str] = []
    if float(english.get("useful") or 0.0) < payload["objective"]["english_useful_score_min"]:
        blockers.append("english_below_target")
    if float(hindi.get("useful") or 0.0) < payload["objective"]["hindi_english_useful_score_target"]:
        blockers.append("hindi_english_below_target")
    if float(hindi.get("p95_seconds") or 999.0) > payload["objective"]["polished_output_p95_seconds_max"]:
        blockers.append("hindi_english_latency_over_budget")
    if int(selector.get("english_only_accepted") or 0) > payload["objective"]["blind_english_overwrites_allowed"]:
        blockers.append("blind_english_overwrite")
    if accelerator.get("status") == "blocked":
        blockers.append("accelerated_frontier_blocked_by_metal")
    return {
        "goal_met": not blockers,
        "blockers": blockers,
        "next_action": (
            "recover Metal/reboot, then run scripts/run_accelerated_frontier_after_metal_recovery.py"
            if "accelerated_frontier_blocked_by_metal" in blockers
            else "collect more claim-grade mixed clips and run competitor app same-WAV rows"
        ),
    }


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# RambleFix Current Goal State",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        "## Inputs",
        "",
        f"- Corpus: `{payload['inputs']['corpus']}`",
        f"- Scorecard: `{payload['inputs']['scorecard']}`",
        f"- Selector decisions: `{payload['inputs']['selector_decisions']}`",
        f"- Accelerator health: `{payload['inputs']['accelerator_health']}`",
        "",
        "## Corpus",
        "",
        f"- Rows: `{payload['corpus']['rows']}`",
        f"- Buckets: `{payload['corpus']['buckets']}`",
        f"- Cloud-confirmed rows: `{payload['corpus']['cloud_confirmed']}`",
        "",
        "## Split Metrics",
        "",
        "| Backend | Bucket | Clips | Useful | Coverage | Terms | p50 | p95 | Usable | Fast | Hang |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["split_metrics"]:
        lines.append(
            f"| {row['backend']} | {row['bucket']} | {row['clips']} | {row['useful']:.3f} | "
            f"{row['coverage']:.3f} | {row['term_coverage']:.3f} | {row['p50_seconds']:.3f}s | "
            f"{row['p95_seconds']:.3f}s | {row['usable_rate']:.3f} | {row['fast_rate']:.3f} | {row['hang_risk_rate']:.3f} |"
        )
    selector = payload["selector"]
    lines.extend(
        [
            "",
            "## Deterministic Glossary Projection",
            "",
            f"- Changed scored policy rows: `{len(payload['glossary_changes'])}`",
        ]
    )
    for row in payload["glossary_changes"]:
        lines.append(f"- `{row['backend']}` / `{row['id']}`: {row['before']} -> {row['after']}")
    lines.extend(
        [
            "",
            "## Selector",
            "",
            f"- Safe updates accepted: `{selector['accepted']}/{selector['rows']}`",
            f"- Hindi+English accepted: `{selector['hindi_english_accepted']}`",
            f"- English-only accepted: `{selector['english_only_accepted']}`",
            f"- Reject reasons: `{selector['reject_reasons']}`",
            "",
            "## Worst Current Product Misses",
            "",
        ]
    )
    for bucket, misses in payload["misses"].items():
        lines.extend([f"### {bucket}", "", "| ID | Score | Seconds | Route | Term Misses | Gold | Actual |", "| --- | ---: | ---: | --- | --- | --- | --- |"])
        for row in misses:
            lines.append(
                f"| {row['id']} | {float(row['score']):.3f} | {float(row['seconds']):.3f}s | "
                f"{row['route']} | `{row['term_misses']}` | {row['gold']} | {row['actual']} |"
            )
        lines.append("")
    accel = payload["accelerator"]
    decision = payload["decision"]
    lines.extend(
        [
            "## Accelerator",
            "",
            f"- Status: `{accel['status']}`",
            f"- Accelerator OK: `{accel.get('accelerator_ok', False)}`",
            "",
            "## Decision",
            "",
            f"- Goal met: `{decision['goal_met']}`",
            f"- Blockers: `{decision['blockers']}`",
            f"- Next action: {decision['next_action']}",
            "",
        ]
    )
    return "\n".join(lines)


def _console(payload: dict[str, Any]) -> str:
    product = {
        row["bucket"]: row for row in payload["split_metrics"] if row["backend"] == "policy_safety_all"
    }
    english = product.get("english_only", {})
    hindi = product.get("hindi_english", {})
    return "\n".join(
        [
            "Current RambleFix STT goal state",
            f"corpus={payload['corpus']['rows']} rows buckets={payload['corpus']['buckets']}",
            f"english product: useful={float(english.get('useful', 0)):.3f} p95={float(english.get('p95_seconds', 0)):.3f}s",
            f"hindi+english product: useful={float(hindi.get('useful', 0)):.3f} p95={float(hindi.get('p95_seconds', 0)):.3f}s",
            f"safe updates: {payload['selector']['accepted']}/{payload['selector']['rows']} english_accept={payload['selector']['english_only_accepted']}",
            f"accelerator={payload['accelerator']['status']}",
            f"blockers={payload['decision']['blockers']}",
        ]
    )


def _percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1))))
    return round(ordered[index], 3)


def _short(text: str, limit: int) -> str:
    compact = " ".join(text.split()).replace("|", "\\|")
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


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
