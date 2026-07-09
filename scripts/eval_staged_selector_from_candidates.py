from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import (  # noqa: E402
    _corpus_terms,
    meaning_coverage,
    meaning_loss,
    repeated_substring_score,
    term_coverage_report,
    word_error_rate,
)
from ramblefix.glossary import apply_glossary  # noqa: E402
from ramblefix.hindi_chunk_polish import (  # noqa: E402
    hindi_value_delta,
    meaning_first_update_reject_reasons,
    normalize_roman_hindi_spelling,
    romanize_devanagari_for_hinglish,
    update_reject_reasons,
)
from ramblefix.hindi_stream_session import _sanitize_rejected_new_english_candidate  # noqa: E402


FAST_BACKEND = "whisper_cpp_server_translate"
HINDI_BACKEND = "oriserve_hindi2hinglish_ggml"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate fast-first + safe-overwrite policies from saved candidates.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-tail-seconds", type=float, default=4.5)
    parser.add_argument("--mode", choices=["meaning", "verbatim"], default="meaning")
    parser.add_argument("--sanitize-rejected", action="store_true")
    args = parser.parse_args()

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    candidate_rows = json.loads(args.candidates.read_text(encoding="utf-8"))
    candidate_by_key = {(row["id"], row["backend"]): row for row in candidate_rows}

    rows: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for item in corpus:
        clip_id = str(item["id"])
        fast = candidate_by_key[(clip_id, FAST_BACKEND)]
        hindi = candidate_by_key[(clip_id, HINDI_BACKEND)]
        bucket = str(item.get("bucket") or item.get("language_bucket") or "")
        rows.append(_selected_row(item, fast, backend="policy_fast_only", route="fast_only"))
        rows.append(_selected_row(item, hindi, backend="policy_oriserve_only", route="oriserve_only"))

        safety = _safe_oriserve_decision(
            fast,
            hindi,
            max_tail_seconds=args.max_tail_seconds,
            sanitize_rejected=args.sanitize_rejected,
        )
        decisions.append({"id": clip_id, "bucket": bucket, **safety})
        chosen = _candidate_with_selector_text(hindi, safety) if safety["accepted"] else fast
        rows.append(
            _selected_row(
                item,
                chosen,
                backend="policy_safety_all",
                route="oriserve_safe" if safety["accepted"] else "fast_kept",
                selector=safety,
            )
        )

        oracle_wants_hindi = bucket == "hindi_english"
        chosen = hindi if oracle_wants_hindi else fast
        rows.append(
            _selected_row(
                item,
                chosen,
                backend="policy_oracle_language",
                route="oracle_hindi" if oracle_wants_hindi else "oracle_fast",
            )
        )

        chosen = _candidate_with_selector_text(hindi, safety) if oracle_wants_hindi and safety["accepted"] else fast
        rows.append(
            _selected_row(
                item,
                chosen,
                backend="policy_oracle_language_plus_safety",
                route="oracle_hindi_safe" if oracle_wants_hindi and safety["accepted"] else "fast_kept",
                selector=safety if oracle_wants_hindi else {"accepted": False, "reason": "oracle-english"},
            )
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    product_scorecard = _load_product_scorecard()
    scored = [product_scorecard.score_row(row, mode=args.mode) for row in rows]
    payload = {"mode": args.mode, "summary": product_scorecard.summarize(scored), "rows": scored}
    (args.output_dir / "scorecard.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_dir / "scorecard.md").write_text(product_scorecard.markdown(payload), encoding="utf-8")
    (args.output_dir / "selector_decisions.json").write_text(json.dumps(decisions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_dir / "summary.json").write_text(
        json.dumps(_summary(payload, decisions), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(_summary(payload, decisions), indent=2, ensure_ascii=False))


def _safe_oriserve_decision(
    fast: dict[str, Any],
    hindi: dict[str, Any],
    *,
    max_tail_seconds: float,
    sanitize_rejected: bool,
) -> dict[str, Any]:
    draft_text = apply_glossary(str(fast.get("actual") or "").strip())
    raw_candidate = apply_glossary(str(hindi.get("actual") or "").strip())
    candidate_text = normalize_roman_hindi_spelling(raw_candidate)
    romanized_text = normalize_roman_hindi_spelling(romanize_devanagari_for_hinglish(candidate_text))
    release_tail_seconds = float(hindi.get("seconds") or 0.0)
    reject_reasons = update_reject_reasons(
        draft_text=draft_text,
        final_text=candidate_text,
        release_tail_seconds=release_tail_seconds,
        max_release_tail_seconds=max_tail_seconds,
        allow_roman_hindi=True,
        strict_new_english=True,
    )
    if not reject_reasons and romanized_text != candidate_text:
        reject_reasons.extend(
            update_reject_reasons(
                draft_text=draft_text,
                final_text=romanized_text,
                release_tail_seconds=release_tail_seconds,
                max_release_tail_seconds=max_tail_seconds,
                allow_roman_hindi=True,
                strict_new_english=True,
            )
        )
    hindi_value = hindi_value_delta(draft_text, candidate_text)
    if not reject_reasons and not hindi_value["has_hindi_value"]:
        reject_reasons.append("no-hindi-value")
    if not reject_reasons:
        reject_reasons.extend(meaning_first_update_reject_reasons(draft_text, candidate_text))
    sanitize_result: dict[str, Any] = {"ran": False}
    if reject_reasons and sanitize_rejected:
        sanitize_result = _sanitize_rejected_new_english_candidate(
            draft_text=draft_text,
            candidate_text=romanized_text,
            reject_reasons=reject_reasons,
            release_tail_seconds=release_tail_seconds,
            max_release_tail_seconds=max_tail_seconds,
        )
        if sanitize_result.get("accepted") is True:
            romanized_text = str(sanitize_result.get("text") or "").strip()
            candidate_text = romanized_text
            hindi_value = sanitize_result.get("hindi_value") or hindi_value
            reject_reasons = []
    accepted = not reject_reasons
    return {
        "accepted": accepted,
        "release_tail_seconds": round(release_tail_seconds, 3),
        "reject_reasons": reject_reasons,
        "hindi_value": hindi_value,
        "candidate_text": romanized_text,
        "sanitize": sanitize_result,
    }


def _candidate_with_selector_text(candidate: dict[str, Any], selector: dict[str, Any]) -> dict[str, Any]:
    selected = dict(candidate)
    text = str(selector.get("candidate_text") or "").strip()
    if text:
        selected["actual"] = text
    return selected


def _selected_row(
    item: dict[str, Any],
    candidate: dict[str, Any],
    *,
    backend: str,
    route: str,
    selector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gold = str(item.get("gold") or item.get("text") or item.get("reference") or "").strip()
    actual = apply_glossary(str(candidate.get("actual") or "").strip())
    terms = _corpus_terms(item)
    expected_terms = list(term_coverage_report(gold, "", terms)["terms"])
    term_report = term_coverage_report(gold, actual, expected_terms)
    seconds = float(candidate.get("seconds") or 0.0)
    return {
        "id": str(item["id"]),
        "category": str(item.get("category") or item.get("bucket") or "frontier"),
        "bucket": str(item.get("bucket") or ""),
        "backend": backend,
        "route": route,
        "selected_source_backend": candidate.get("backend"),
        "audio": str(item.get("audio") or candidate.get("audio") or ""),
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
        "selector": selector or {},
        "error": candidate.get("error"),
    }


def _load_product_scorecard() -> Any:
    path = ROOT / "scripts/product_scorecard.py"
    spec = importlib.util.spec_from_file_location("product_scorecard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["product_scorecard"] = module
    spec.loader.exec_module(module)
    return module


def _summary(payload: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    rows = payload["rows"]
    return {
        "summary": payload["summary"],
        "safety_accept_count": sum(1 for decision in decisions if decision["accepted"]),
        "safety_accept_hindi_english_count": sum(
            1 for decision in decisions if decision["accepted"] and decision["bucket"] == "hindi_english"
        ),
        "safety_accept_english_only_count": sum(
            1 for decision in decisions if decision["accepted"] and decision["bucket"] == "english_only"
        ),
        "reject_reasons": _reason_counts(decisions),
        "worst_misses": _examples(rows, reverse=False),
        "best_hits": _examples(rows, reverse=True),
    }


def _reason_counts(decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for decision in decisions:
        for reason in decision.get("reject_reasons") or []:
            key = str(reason).split(":", 1)[0]
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _examples(rows: list[dict[str, Any]], *, reverse: bool) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: float(row.get("useful_dictation_score") or 0.0), reverse=reverse)
    return [
        {
            "id": row["id"],
            "backend": row["backend"],
            "route": row.get("route"),
            "score": row["useful_dictation_score"],
            "seconds": row["seconds"],
            "gold": row["gold"],
            "actual": row["actual"],
        }
        for row in ordered[:8]
    ]


if __name__ == "__main__":
    main()
