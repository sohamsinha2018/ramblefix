from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

TARGETS = {
    "useful": 0.75,
    "usable": 0.80,
    "p95": 2.50,
    "hang": 0.02,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the RambleFix Hindi+English / English goal status.")
    parser.add_argument(
        "--fixed-corpus",
        type=Path,
        default=ROOT / "eval_runs/work-hinglish-review-20260627-174147/gold_ready_corpus.json",
        help="Gold corpus used for the repeatable loop metric.",
    )
    parser.add_argument(
        "--capture-sheet",
        type=Path,
        default=ROOT / "eval_runs/work-capture-sheet-20260627T104147/capture_prompts.json",
        help="Capture sheet used to check representative corpus growth.",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--min-representative", type=int, default=20)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if the fixed metric fails or representative corpus is not ready.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir or ROOT / "eval_runs" / f"goal-status-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    output_dir.mkdir(parents=True, exist_ok=True)

    fixed = run_fixed_scorecard(args.fixed_corpus, output_dir)
    capture = run_capture_progress(args.capture_sheet, args.limit)
    real_use = run_real_use_progress(output_dir, args.limit)

    fixed_pass = (
        fixed["useful"] >= TARGETS["useful"]
        and fixed["usable"] >= TARGETS["usable"]
        and fixed["p95"] <= TARGETS["p95"]
        and fixed["hang"] <= TARGETS["hang"]
    )
    capture_ready = int(capture.get("representative_clips") or 0) >= args.min_representative
    real_use_ready = int(real_use.get("representative_rows") or 0) >= args.min_representative
    corpus_ready = capture_ready or real_use_ready
    bottleneck = classify_bottleneck(
        fixed_pass=fixed_pass,
        corpus_ready=corpus_ready,
        capture_ready=capture_ready,
        real_use_ready=real_use_ready,
        capture=capture,
        real_use=real_use,
    )

    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "target_metric": {
            "useful_min": TARGETS["useful"],
            "usable_min": TARGETS["usable"],
            "p95_max_seconds": TARGETS["p95"],
            "hang_max": TARGETS["hang"],
            "min_representative_clips": args.min_representative,
        },
        "fixed_corpus": fixed,
        "fixed_metric_pass": fixed_pass,
        "capture_progress": capture,
        "capture_ready": capture_ready,
        "real_use_progress": real_use,
        "real_use_ready": real_use_ready,
        "corpus_ready": corpus_ready,
        "bottleneck": bottleneck,
        "decision": decision(fixed_pass=fixed_pass, corpus_ready=corpus_ready),
    }

    (output_dir / "goal_status.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_dir / "goal_status.md").write_text(render_markdown(payload), encoding="utf-8")

    print(render_console(payload))
    print(f"wrote {output_dir / 'goal_status.md'}")

    if args.strict and (not fixed_pass or not corpus_ready):
        raise SystemExit(2)


def run_fixed_scorecard(corpus: Path, output_dir: Path) -> dict[str, Any]:
    corpus = resolve(corpus)
    results = output_dir / "fixed_product_path_results.json"
    scorecard = output_dir / "fixed_product_path_scorecard.md"

    run(
        [
            str(ROOT / ".venv/bin/python"),
            "scripts/eval_dictate_audio_product_path.py",
            "--corpus",
            str(corpus),
            "--output",
            str(results),
        ]
    )
    run(
        [
            str(ROOT / ".venv/bin/python"),
            "scripts/product_scorecard.py",
            str(results),
            "--mode",
            "meaning",
            "--output",
            str(scorecard),
        ]
    )
    score_json = scorecard.with_suffix(".json")
    payload = json.loads(score_json.read_text(encoding="utf-8"))
    summary = payload["summary"][0]
    return {
        "corpus": rel(corpus),
        "rows": int(summary["clips"]),
        "useful": float(summary["avg_useful_score"]),
        "usable": float(summary["usable_rate"]),
        "p50": float(summary["p50_seconds"]),
        "p95": float(summary["p95_seconds"]),
        "hang": float(summary["hang_risk_rate"]),
        "scorecard": rel(scorecard),
        "results": rel(results),
    }


def run_capture_progress(capture_sheet: Path, limit: int) -> dict[str, Any]:
    capture_sheet = resolve(capture_sheet)
    proc = run(
        [
            str(ROOT / ".venv/bin/python"),
            "scripts/check_work_capture_progress.py",
            "--capture-sheet",
            str(capture_sheet),
            "--limit",
            str(limit),
            "--json",
        ],
        check=False,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"capture progress did not return JSON: {proc.stdout[:500]}") from exc
    payload["capture_sheet"] = rel(capture_sheet)
    return payload


def run_real_use_progress(output_dir: Path, limit: int) -> dict[str, Any]:
    real_dir = output_dir / "real_use_review"
    proc = run(
        [
            str(ROOT / ".venv/bin/python"),
            "scripts/build_real_use_review_set.py",
            "--output-dir",
            str(real_dir),
            "--limit",
            str(limit),
            "--json",
        ],
        check=False,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"real-use progress did not return JSON: {proc.stdout[:500]}") from exc
    for key in ("review_json", "candidate_json", "html"):
        if key in payload:
            payload[key] = rel(Path(str(payload[key])))
    return payload


def classify_bottleneck(
    *,
    fixed_pass: bool,
    corpus_ready: bool,
    capture_ready: bool,
    real_use_ready: bool,
    capture: dict[str, Any],
    real_use: dict[str, Any],
) -> str:
    if not fixed_pass:
        return "quality_or_latency_on_fixed_corpus"
    if not corpus_ready:
        retained = int(capture.get("retained_success_clips") or 0)
        capture_representative = int(capture.get("representative_clips") or 0)
        real_representative = int(real_use.get("representative_rows") or 0)
        real_candidates = int(real_use.get("candidate_rows") or 0)
        if retained == 0 and real_candidates == 0:
            return "representative_audio_missing"
        if capture_representative == 0 and real_representative == 0:
            return "retained_audio_not_representative"
        return "representative_corpus_too_small"
    if capture_ready and real_use_ready:
        return "ready_for_strategy_bakeoff"
    if real_use_ready:
        return "ready_for_real_use_strategy_bakeoff"
    if capture_ready:
        return "ready_for_capture_sheet_strategy_bakeoff"
    return "ready_for_strategy_bakeoff"


def decision(*, fixed_pass: bool, corpus_ready: bool) -> str:
    if not fixed_pass:
        return "Fix the foreground product path before broadening experiments."
    if not corpus_ready:
        return "Do not run more model tweaks yet; collect or promote representative replayable clips first."
    return "Run same-WAV candidate bakeoffs and keep only objective improvements."


def render_console(payload: dict[str, Any]) -> str:
    fixed = payload["fixed_corpus"]
    capture = payload["capture_progress"]
    real_use = payload["real_use_progress"]
    return "\n".join(
        [
            "Hinglish/English goal status",
            f"fixed: useful={fixed['useful']:.3f} usable={fixed['usable']:.3f} p95={fixed['p95']:.3f}s hang={fixed['hang']:.3f} pass={payload['fixed_metric_pass']}",
            f"capture: representative={capture.get('representative_clips', 0)} retained={capture.get('retained_success_clips', 0)} matched={capture.get('matched_prompt_count', 0)}/{capture.get('prompt_count', 0)} ready={payload['capture_ready']}",
            f"real_use: representative={real_use.get('representative_rows', 0)} candidates={real_use.get('candidate_rows', 0)} retained={real_use.get('retained_audio_groups', 0)} ready={payload['real_use_ready']}",
            f"bottleneck: {payload['bottleneck']}",
            f"decision: {payload['decision']}",
        ]
    )


def render_markdown(payload: dict[str, Any]) -> str:
    fixed = payload["fixed_corpus"]
    capture = payload["capture_progress"]
    real_use = payload["real_use_progress"]
    target = payload["target_metric"]
    return f"""# Hinglish + English Goal Status

Checked: `{payload["checked_at"]}`

## Target Metric

- useful score >= `{target["useful_min"]}`
- usable rate >= `{target["usable_min"]}`
- p95 release-to-output <= `{target["p95_max_seconds"]}s`
- hang risk <= `{target["hang_max"]}`
- representative replayable clips >= `{target["min_representative_clips"]}`

## Fixed Corpus

| Rows | Useful | Usable | p50 | p95 | Hang | Pass |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| {fixed["rows"]} | {fixed["useful"]:.3f} | {fixed["usable"]:.3f} | {fixed["p50"]:.3f}s | {fixed["p95"]:.3f}s | {fixed["hang"]:.3f} | `{payload["fixed_metric_pass"]}` |

Scorecard: `{fixed["scorecard"]}`

## Corpus Readiness

### Prompt Sheet

| History Rows Since Marker | Retained Success | Representative | Matched Prompts | Ready |
| ---: | ---: | ---: | ---: | --- |
| {capture.get("history_rows_since_marker", 0)} | {capture.get("retained_success_clips", 0)} | {capture.get("representative_clips", 0)} | {capture.get("matched_prompt_count", 0)} / {capture.get("prompt_count", 0)} | `{payload["capture_ready"]}` |

### Free-Form Real Use

| Retained Audio | Candidate Rows | Representative | Ready |
| ---: | ---: | ---: | --- |
| {real_use.get("retained_audio_groups", 0)} | {real_use.get("candidate_rows", 0)} | {real_use.get("representative_rows", 0)} | `{payload["real_use_ready"]}` |

Review HTML: `{real_use.get("html", "")}`

## Bottleneck

`{payload["bottleneck"]}`

## Decision

{payload["decision"]}
"""


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=check)


def resolve(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


if __name__ == "__main__":
    main()
