from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results_json")
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--mode",
        choices=["meaning", "verbatim"],
        default="meaning",
        help="meaning scores usable work output; verbatim also penalizes losing expected Hindi/script form.",
    )
    args = parser.parse_args()

    rows = json.loads(Path(args.results_json).read_text(encoding="utf-8"))
    scored = [score_row(row, mode=args.mode) for row in rows]
    summary = summarize(scored)

    payload = {"mode": args.mode, "summary": summary, "rows": scored}
    text = markdown(payload)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        out.with_suffix(".json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        print(text)


def score_row(row: dict[str, Any], *, mode: str = "meaning") -> dict[str, Any]:
    wer = float(row["wer"]) if row.get("wer") is not None else None
    coverage = float(row.get("meaning_coverage") or 0.0)
    term_coverage = row.get("term_coverage")
    term_score = float(term_coverage) if term_coverage is not None else coverage
    repeat = float(row.get("repeat") or 0.0)
    seconds = float(row.get("seconds") or 0.0)
    error = row.get("error")

    literal_score = 0.0 if wer is None else max(0.0, 1.0 - min(wer, 1.0))
    speed_score = latency_score(seconds)
    reliability_score = 0.0 if error else max(0.0, 1.0 - min(repeat, 1.0))
    language_score = language_preservation_score(str(row.get("gold") or ""), str(row.get("actual") or ""))
    critical_semantic_error = has_critical_semantic_error(row)

    # Product score answers: "Can I use this transcript without painful cleanup?"
    # Meaning and terms dominate; raw WER is only one signal.
    useful_score = (
        0.45 * coverage
        + 0.20 * term_score
        + 0.15 * literal_score
        + 0.10 * speed_score
        + 0.10 * reliability_score
    )
    if mode == "verbatim" and is_mixed_language_row(row):
        # Verbatim transcript mode should preserve expected non-Latin/script form.
        # Meaning mode does not apply this penalty because clean English output
        # can be the desired product behavior.
        useful_score *= 0.65 + 0.35 * language_score
    if critical_semantic_error:
        useful_score = min(useful_score, 0.50)

    out = dict(row)
    out.update(
        {
            "literal_score": round(literal_score, 3),
            "speed_score": round(speed_score, 3),
            "reliability_score": round(reliability_score, 3),
            "language_preservation_score": round(language_score, 3),
            "critical_semantic_error": critical_semantic_error,
            "score_mode": mode,
            "useful_dictation_score": round(useful_score, 3),
            "usable": useful_score >= 0.75 and seconds <= 2.5 and not error and repeat < 0.2 and not critical_semantic_error,
            "fast": seconds <= 1.5,
            "hang_risk": seconds > 6.0 or repeat >= 0.2 or bool(error),
        }
    )
    return out


def is_mixed_language_row(row: dict[str, Any]) -> bool:
    category = str(row.get("category") or "").lower()
    gold = str(row.get("gold") or "")
    return (
        "hinglish" in category
        or "hindi" in category
        or "hi-en" in category
        or "chinese" in category
        or "mandarin" in category
        or "cmn" in category
        or "zh" in category
        or bool(re.search(r"[\u0900-\u097f\u4e00-\u9fff]", gold))
    )


def language_preservation_score(gold: str, actual: str) -> float:
    expected = required_non_latin_scripts(gold)
    if not expected:
        return 1.0
    actual_scripts = required_non_latin_scripts(actual)
    return len(expected & actual_scripts) / len(expected)


def required_non_latin_scripts(text: str) -> set[str]:
    scripts: set[str] = set()
    if re.search(r"[\u0900-\u097f]", text):
        scripts.add("devanagari")
    if re.search(r"[\u0600-\u06ff]", text):
        scripts.add("arabic")
    if re.search(r"[\u4e00-\u9fff]", text):
        scripts.add("han")
    return scripts


def latency_score(seconds: float) -> float:
    if seconds <= 1.0:
        return 1.0
    if seconds <= 2.0:
        return 0.85
    if seconds <= 4.0:
        return 0.65
    if seconds <= 8.0:
        return 0.35
    return 0.0


def has_critical_semantic_error(row: dict[str, Any]) -> bool:
    actual = str(row.get("actual") or "").strip().lower()
    if actual.startswith("asr failure detected"):
        return True
    if actual in {"[blank_audio]", "blank_audio", "<|nospeech|>", "[no speech]", "no speech detected"}:
        return True

    direct_keys = (
        "critical_semantic_error",
        "critical_fact_error",
        "dangerous_error",
        "semantic_error",
    )
    if any(is_truthy_flag(row.get(key)) for key in direct_keys):
        return True

    error_keys = (
        "critical_errors",
        "critical_fact_errors",
        "semantic_errors",
        "fact_errors",
    )
    if any(has_error_items(row.get(key)) for key in error_keys):
        return True

    flags = row.get("quality_flags")
    if isinstance(flags, str):
        values = re.split(r"[,;\s]+", flags.lower())
    elif isinstance(flags, list):
        values = [str(flag).lower() for flag in flags]
    else:
        values = []
    critical_flags = {
        "critical_semantic_error",
        "critical_fact_error",
        "dangerous_error",
        "wrong_action",
        "negation_flip",
        "entity_substitution",
        "number_error",
        "date_error",
    }
    return any(flag in critical_flags for flag in values)


def is_truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "critical", "fail", "failed"}
    return False


def has_error_items(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        return any(str(item).strip() for item in value)
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["backend"]), []).append(row)

    summary: list[dict[str, Any]] = []
    for backend, bucket in sorted(grouped.items()):
        useful = [float(row["useful_dictation_score"]) for row in bucket]
        seconds = [float(row["seconds"]) for row in bucket]
        wer_values = [float(row["wer"]) for row in bucket if row.get("wer") is not None]
        coverage = [float(row.get("meaning_coverage") or 0.0) for row in bucket]
        language_scores = [float(row.get("language_preservation_score", 1.0)) for row in bucket]
        summary.append(
            {
                "backend": backend,
                "clips": len(bucket),
                "avg_useful_score": round(statistics.mean(useful), 3),
                "median_useful_score": round(statistics.median(useful), 3),
                "avg_wer": round(statistics.mean(wer_values), 3) if wer_values else None,
                "avg_coverage": round(statistics.mean(coverage), 3),
                "avg_language_preservation": round(statistics.mean(language_scores), 3),
                "p50_seconds": round(statistics.median(seconds), 3),
                "p95_seconds": percentile(seconds, 95),
                "usable_rate": round(sum(1 for row in bucket if row["usable"]) / len(bucket), 3),
                "fast_rate": round(sum(1 for row in bucket if row["fast"]) / len(bucket), 3),
                "hang_risk_rate": round(sum(1 for row in bucket if row["hang_risk"]) / len(bucket), 3),
            }
        )
    return sorted(summary, key=lambda row: (-float(row["avg_useful_score"]), float(row["p50_seconds"])))


def percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1))))
    return round(ordered[index], 3)


def markdown(payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode") or "meaning")
    mode_note = (
        "Meaning mode scores usable work output and does not penalize translating Hindi/Chinese into clear English."
        if mode == "meaning"
        else "Verbatim mode also penalizes losing expected non-Latin/script form."
    )
    lines = [
        "# Product Dictation Scorecard",
        "",
        f"Mode: `{mode}`. {mode_note}",
        "",
        "Useful score weights: 45% meaning coverage, 20% key-term coverage, 15% literal closeness, 10% speed, 10% reliability.",
        "",
        "| Backend | Clips | Useful Score | Median Score | Avg WER | Avg Coverage | Lang Preserve | p50 sec | p95 sec | Usable Rate | Fast Rate | Hang Risk |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["summary"]:
        avg_wer = "" if row["avg_wer"] is None else f"{float(row['avg_wer']):.3f}"
        lines.append(
            f"| {row['backend']} | {row['clips']} | {float(row['avg_useful_score']):.3f} | "
            f"{float(row['median_useful_score']):.3f} | {avg_wer} | {float(row['avg_coverage']):.3f} | "
            f"{float(row['avg_language_preservation']):.3f} | "
            f"{float(row['p50_seconds']):.3f} | {float(row['p95_seconds']):.3f} | "
            f"{float(row['usable_rate']):.3f} | {float(row['fast_rate']):.3f} | {float(row['hang_risk_rate']):.3f} |"
        )
    lines.extend(["", "## Per-Clip Scores", "", "| ID | Backend | Score | WER | Coverage | Lang | Seconds | Usable | Hang Risk | Actual |", "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |"])
    for row in payload["rows"]:
        wer = "" if row.get("wer") is None else f"{float(row['wer']):.3f}"
        actual = str(row.get("actual") or row.get("error") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {row['id']} | {row['backend']} | {float(row['useful_dictation_score']):.3f} | "
            f"{wer} | {float(row.get('meaning_coverage') or 0.0):.3f} | "
            f"{float(row.get('language_preservation_score', 1.0)):.3f} | {float(row['seconds']):.3f} | "
            f"{row['usable']} | {row['hang_risk']} | {actual[:220]} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
