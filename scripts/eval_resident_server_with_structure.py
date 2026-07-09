#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import (  # noqa: E402
    meaning_coverage,
    meaning_loss,
    repeated_substring_score,
    term_coverage_report,
    word_error_rate,
)


ASR_TOOL = ROOT / "native/RambleFixHotkey/.build/debug/RambleFixHotkeyASRTool"
POLICY_TOOL = ROOT / "native/RambleFixHotkey/.build/debug/RambleFixHotkeyPolicyTool"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the resident local server path plus native structure policy.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=150.0)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8188/inference")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    items = [item for item in json.loads(args.corpus.read_text(encoding="utf-8")) if isinstance(item, dict)]
    if args.limit:
        items = items[: args.limit]
    if not items:
        raise SystemExit("no corpus rows")
    missing_audio = [(str(item.get("id") or ""), audio_path(item)) for item in items if not audio_path(item).exists()]
    if missing_audio:
        details = "\n".join(f"- {row_id}: {path}" for row_id, path in missing_audio[:10])
        raise SystemExit(f"missing corpus audio files:\n{details}")

    require_tool(ASR_TOOL, "Run `swift build --package-path native/RambleFixHotkey` first")
    require_tool(POLICY_TOOL, "Run `swift build --package-path native/RambleFixHotkey` first")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    first_rows = [run_asr_item(item, endpoint=args.endpoint, timeout_seconds=args.timeout_seconds) for item in items]
    structured_rows = structure_rows_for(items, first_rows)
    rows: list[dict[str, Any]] = []
    for first, structured in zip(first_rows, structured_rows, strict=True):
        rows.append(first)
        rows.append(structured)

    raw_path = args.output_dir / "results.json"
    raw_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    scorecard = load_product_scorecard()
    scored = [scorecard.score_row(row, mode="meaning") for row in rows]
    score_payload = {"mode": "meaning", "summary": scorecard.summarize(scored), "rows": scored}
    (args.output_dir / "scorecard.json").write_text(json.dumps(score_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_dir / "scorecard.md").write_text(scorecard.markdown(score_payload), encoding="utf-8")

    by_category = summarize_by(scored, "category")
    by_payload = {"by_category": by_category}
    (args.output_dir / "summary_by_category.json").write_text(json.dumps(by_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_dir / "summary_by_category.md").write_text(markdown_by_category(by_category), encoding="utf-8")

    print(f"wrote {raw_path}")
    print(f"wrote {args.output_dir / 'scorecard.json'}")
    for row in score_payload["summary"]:
        print(
            f"{row['backend']}: clips={row['clips']} useful={row['avg_useful_score']:.3f} "
            f"meaning={row['avg_coverage']:.3f} p50={row['p50_seconds']:.3f}s p95={row['p95_seconds']:.3f}s"
        )


def run_asr_item(item: dict[str, Any], *, endpoint: str, timeout_seconds: float) -> dict[str, Any]:
    audio = audio_path(item)
    gold = str(item.get("gold") or "")
    started = time.perf_counter()
    proc = subprocess.run(
        [
            str(ASR_TOOL),
            "--audio",
            str(audio),
            "--endpoint",
            endpoint,
            "--timeout",
            str(timeout_seconds),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout_seconds + 20.0,
    )
    wall = round(time.perf_counter() - started, 3)
    if proc.returncode != 0:
        row = base_row(item, backend="resident_server_first_paste", actual="", seconds=wall, error=proc.stderr.strip() or proc.stdout.strip())
        print(f"ERR first {row['id']} {wall}s {row['error'][:140]}")
        return row

    payload = json.loads(proc.stdout)
    actual = str(payload.get("text") or "")
    seconds = float(payload.get("seconds") or wall)
    meta = {
        "wall_seconds": wall,
        "engine": payload.get("engine"),
        "route": payload.get("route"),
        "processor": payload.get("processor"),
        "fallback_reason": payload.get("fallback_reason"),
        "quality": payload.get("quality") or {},
    }
    row = scored_row(item, backend="resident_server_first_paste", actual=actual, seconds=seconds, meta=meta, error=None)
    print(f"OK first {row['id']} {seconds:.3f}s {actual[:100]}")
    return row


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
                backend="resident_server_structured_if_unchanged",
                actual=actual,
                seconds=round(float(first.get("seconds") or 0.0) + policy_overhead, 3),
                meta=meta,
                error=first.get("error"),
            )
        )
    return rows


def run_structure_policy(requests: list[dict[str, str]]) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(
        [str(POLICY_TOOL), "--policy", "structure", "--project-root", str(ROOT)],
        cwd=ROOT,
        input=json.dumps(requests, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    by_id = {str(row["id"]): row for row in json.loads(proc.stdout)}
    by_id["__wall_seconds__"] = round(time.perf_counter() - started, 6)
    return by_id


def base_row(item: dict[str, Any], *, backend: str, actual: str, seconds: float, error: object) -> dict[str, Any]:
    return scored_row(item, backend=backend, actual=actual, seconds=seconds, meta={}, error=error)


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
    return {
        "id": str(item.get("id") or ""),
        "category": str(item.get("category") or ""),
        "duration_bucket": str(item.get("duration_bucket") or ""),
        "eval_claim_grade": bool(item.get("eval_claim_grade")),
        "reference_trust": str(item.get("reference_trust") or ""),
        "backend": backend,
        "audio": str(audio_path(item)),
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
        "seconds": round(seconds, 3),
        "meta": meta,
        "error": error,
    }


def summarize_by(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row.get("backend") or ""), str(row.get(key) or "")), []).append(row)
    out = []
    for (backend, bucket), bucket_rows in sorted(grouped.items()):
        out.append(summarize_bucket(bucket_rows, backend=backend, key=key, value=bucket))
    return out


def summarize_bucket(rows: list[dict[str, Any]], *, backend: str, key: str, value: str) -> dict[str, Any]:
    scores = [float(row.get("useful_dictation_score") or 0.0) for row in rows]
    seconds = [float(row.get("seconds") or 0.0) for row in rows]
    wer = [float(row.get("wer") or 0.0) for row in rows if row.get("wer") is not None]
    meaning = [float(row.get("meaning_coverage") or 0.0) for row in rows]
    terms = [float(row.get("term_coverage") or 0.0) for row in rows]
    return {
        "backend": backend,
        key: value,
        "clips": len(rows),
        "avg_meaning_score": round(statistics.mean(scores), 3),
        "avg_wer": round(statistics.mean(wer), 3) if wer else None,
        "avg_meaning_coverage": round(statistics.mean(meaning), 3),
        "avg_term_coverage": round(statistics.mean(terms), 3),
        "p50_seconds": round(statistics.median(seconds), 3),
        "p95_seconds": percentile(seconds, 95),
        "hang_rate": round(sum(1 for row in rows if float(row.get("seconds") or 0.0) > 6.0 or row.get("error")) / len(rows), 3),
    }


def percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1))))
    return round(ordered[index], 3)


def markdown_by_category(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Resident Server Eval By Category",
        "",
        "| Backend | Category | Clips | Useful | Meaning | Terms | p50 sec | p95 sec | Hang Rate |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['backend']} | {row['category']} | {row['clips']} | "
            f"{float(row['avg_meaning_score']):.3f} | {float(row['avg_meaning_coverage']):.3f} | "
            f"{float(row['avg_term_coverage']):.3f} | {float(row['p50_seconds']):.3f} | "
            f"{float(row['p95_seconds']):.3f} | {float(row['hang_rate']):.3f} |"
        )
    return "\n".join(lines) + "\n"


def audio_path(item: dict[str, Any]) -> Path:
    path = Path(str(item.get("audio") or "")).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def require_tool(path: Path, hint: str) -> None:
    if not path.exists():
        raise SystemExit(f"missing {path}: {hint}")


def load_product_scorecard() -> Any:
    path = ROOT / "scripts/product_scorecard.py"
    spec = importlib.util.spec_from_file_location("product_scorecard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["product_scorecard"] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    main()
