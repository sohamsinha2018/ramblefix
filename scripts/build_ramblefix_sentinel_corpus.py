#!/usr/bin/env python3
"""Build stable RambleFix sentinel corpora from retained local WAVs only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "eval_corpus/ramblefix_sentinel_current.json"
DEFAULT_FAILURE_OUTPUT = ROOT / "eval_corpus/ramblefix_known_failures.json"


RECENT_BASELINE_RUNS = [
    "20260708-233621-8C9795",
    "20260708-233923-68F814",
    "20260708-234934-264618",
    "20260709-003603-271639",
    "20260709-003845-BD347E",
    "20260709-003955-D99333",
    "20260709-004024-6BD108",
    "20260709-004808-E56993",
    "20260709-005244-A32650",
    "20260709-005742-CC59D6",
]


KNOWN_FAILURES = {
    "20260709-004744-F45AE3": {
        "gold": (
            "Why is the safe replacement layer failing? Is it a Codex thing? "
            "What is the end-to-end safe replacement? That doesn't mean that if it is split flow "
            "or whatever, it cannot replace this. Is that accurate?"
        ),
        "terms": ["safe replacement", "Codex", "end-to-end", "split flow", "replace"],
        "notes": "User-reported bad live transcript; inferred gold needs human review.",
    }
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--known-failures-output", type=Path, default=DEFAULT_FAILURE_OUTPUT)
    args = parser.parse_args()

    benchmark_corpus = ROOT / "eval_corpus/actual_user_english_hinglish_benchmark_20260705.json"
    rows: list[dict[str, Any]] = []
    rows.extend(existing_rows(benchmark_corpus, max_rows=8, category="real_use_english_dictation", trusted_only=True))
    rows.extend(existing_rows(benchmark_corpus, max_rows=6, category="real_use_hindi_hinglish_probe", trusted_only=True))
    rows.extend(existing_rows(ROOT / "eval_corpus/actual_user_hindi_hinglish_gold_seed_20260708.json", max_rows=4, trusted_only=True))
    rows.extend(recent_history_rows(RECENT_BASELINE_RUNS))
    rows = dedupe(rows)
    validate_audio(rows)

    failure_rows = known_failure_rows()
    validate_audio(failure_rows)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.known_failures_output.parent.mkdir(parents=True, exist_ok=True)
    args.known_failures_output.write_text(json.dumps(failure_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(args.output)
    print(f"sentinel_rows={len(rows)}")
    print(args.known_failures_output)
    print(f"known_failure_rows={len(failure_rows)}")


def existing_rows(path: Path, *, max_rows: int, category: str | None = None, trusted_only: bool = False) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    source_rows = data if isinstance(data, list) else data.get("rows", [])
    rows: list[dict[str, Any]] = []
    for row in source_rows:
        if category and str(row.get("category") or "") != category:
            continue
        if trusted_only and not is_release_trusted(row):
            continue
        audio = resolve_audio(row)
        if not audio.exists():
            continue
        item = dict(row)
        item["audio"] = str(audio)
        item.setdefault("source", "ramblefix_retained_hotkey_audio")
        item.setdefault("source_corpus", path.name)
        item.setdefault("reference_trust", row.get("reference_trust") or row.get("reference_level") or "existing_corpus")
        rows.append(item)
        if len(rows) >= max_rows:
            break
    return rows


def is_release_trusted(row: dict[str, Any]) -> bool:
    if row.get("gold_disputed") is True or row.get("needs_human_review") is True:
        return False
    trust = str(row.get("reference_trust") or row.get("reference_level") or "").lower()
    if "needs_human_review" in trust or "needs human review" in trust or "needs_review" in trust:
        return False
    return bool(str(row.get("gold") or "").strip())


def recent_history_rows(run_ids: list[str]) -> list[dict[str, Any]]:
    history_by_run: dict[str, dict[str, Any]] = {}
    history_path = ROOT / "logs/history.jsonl"
    if not history_path.exists():
        return []
    wanted = set(run_ids)
    for line in history_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        run_id = str(row.get("run_id") or "")
        if run_id not in wanted:
            continue
        if row.get("status") != "paste_attempted":
            continue
        audio = Path(str(row.get("audio_path") or ""))
        if not audio.exists():
            continue
        text = normalize_space(str(row.get("pasted_text") or row.get("corrected_text") or row.get("raw_text") or ""))
        if not text:
            continue
        history_by_run[run_id] = {
            "id": f"recent_{run_id}",
            "audio": str(audio),
            "gold": text,
            "category": "recent_real_use_regression_baseline",
            "language": "English",
            "critical_terms": extract_terms(text),
            "terms": extract_terms(text),
            "source": "ramblefix_history_baseline",
            "source_corpus": "logs/history.jsonl",
            "reference_level": "weak_baseline",
            "reference_trust": "previous_product_output_not_human_gold",
            "audio_seconds": row.get("timings", {}).get("audio_duration_seconds"),
            "notes": "Regression sentinel row: catches product drift, not a claim-grade gold transcript.",
        }
    return [history_by_run[run_id] for run_id in run_ids if run_id in history_by_run]


def known_failure_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run_id, spec in KNOWN_FAILURES.items():
        audio = ROOT / f"logs/hotkey_audio/{run_id}.wav"
        rows.append(
            {
                "id": f"known_failure_{run_id}",
                "audio": str(audio),
                "gold": spec["gold"],
                "category": "known_failure_english_terms",
                "language": "English",
                "critical_terms": spec["terms"],
                "terms": spec["terms"],
                "source": "user_reported_live_failure",
                "source_corpus": "logs/history.jsonl",
                "reference_level": "inferred_gold_needs_review",
                "reference_trust": "user_reported_failure_inferred_gold_needs_human_review",
                "notes": spec["notes"],
            }
        )
    return rows


def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        row_id = str(row.get("id") or row.get("audio") or "")
        if row_id in seen:
            continue
        seen.add(row_id)
        out.append(row)
    return out


def validate_audio(rows: list[dict[str, Any]]) -> None:
    missing = [f"{row.get('id')}:{row.get('audio')}" for row in rows if not Path(str(row.get("audio") or "")).exists()]
    if missing:
        raise FileNotFoundError("missing audio: " + ", ".join(missing[:10]))


def resolve_audio(row: dict[str, Any]) -> Path:
    audio = Path(str(row.get("audio") or ""))
    return audio if audio.is_absolute() else (ROOT / audio).resolve()


def normalize_space(text: str) -> str:
    return " ".join(text.split())


def extract_terms(text: str) -> list[str]:
    candidates = [
        "AI",
        "API",
        "Apple",
        "Codex",
        "Gemini",
        "Hindi",
        "Hinglish",
        "MCP",
        "MLX",
        "RambleFix",
        "safe replacement",
        "structure",
        "terms",
    ]
    lower = text.lower()
    return [term for term in candidates if term.lower() in lower]


if __name__ == "__main__":
    main()
