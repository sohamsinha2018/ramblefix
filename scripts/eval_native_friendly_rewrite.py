from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native" / "RambleFixHotkey"


NEGATION_TOKENS = {
    "no",
    "not",
    "never",
    "cannot",
    "cant",
    "can't",
    "dont",
    "don't",
    "doesnt",
    "doesn't",
    "didnt",
    "didn't",
    "wont",
    "won't",
    "without",
}

HINGLISH_MARKERS = {
    "haan",
    "han",
    "kya",
    "hai",
    "hain",
    "nahi",
    "nahin",
    "matlab",
    "yaar",
    "mujhe",
    "mera",
    "meri",
    "kaise",
    "hoga",
    "hogi",
    "karo",
    "karna",
    "karenge",
    "achcha",
    "acha",
    "thik",
    "theek",
    "phir",
    "agar",
    "aisa",
    "waise",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate native Swift structure safety on real and public text.")
    parser.add_argument("--history", type=Path, default=ROOT / "logs" / "history.jsonl")
    parser.add_argument("--corpus", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gold-output", type=Path)
    parser.add_argument("--limit-history", type=int, default=160)
    parser.add_argument("--since-date", default="", help="Keep history rows with created_at >= this YYYY-MM-DD date.")
    args = parser.parse_args()

    examples = collect_examples(args.history, args.corpus, args.limit_history, args.since_date)
    requests = [{"id": item["id"], "draft": item["text"], "final": ""} for item in examples]
    responses = run_swift_rewrite(requests)
    by_id = {row["id"]: row for row in responses}

    rows: list[dict[str, Any]] = []
    unsafe: list[dict[str, Any]] = []
    changed = 0
    accepted = 0
    for item in examples:
        response = by_id[item["id"]]
        final = str(response.get("final") or item["text"])
        is_changed = bool(response.get("changed"))
        is_accepted = bool(response.get("accepted"))
        changed += int(is_changed)
        accepted += int(is_accepted)
        safety = safety_report(item["text"], final, item.get("terms") or [])
        row = {
            **item,
            "final": final,
            "changed": is_changed,
            "accepted": is_accepted,
            "rules": response.get("rules") or [],
            "dropped_protected_terms": response.get("droppedProtectedTerms") or [],
            "safety": safety,
            "char_delta": len(final) - len(item["text"]),
            "sentence_delta": sentence_count(final) - sentence_count(item["text"]),
        }
        if is_accepted and (
            row["dropped_protected_terms"]
            or safety["dropped_numbers"]
            or safety["dropped_negations"]
            or safety["dropped_terms"]
            or safety["changed_mixed_language"]
        ):
            unsafe.append(row)
        rows.append(row)

    summary = {
        "input_rows": len(rows),
        "changed_rows": changed,
        "accepted_rows": accepted,
        "unsafe_accepted_rows": len(unsafe),
        "accepted_rate": round(accepted / len(rows), 4) if rows else 0,
        "changed_rate": round(changed / len(rows), 4) if rows else 0,
        "accepted_sentence_delta_rows": sum(1 for row in rows if row["accepted"] and row["sentence_delta"] > 0),
        "accepted_rule_counts": rule_counts(rows),
        "unsafe_examples": unsafe[:20],
        "accepted_examples": [row for row in rows if row["accepted"]][:20],
        "review_candidates": review_candidates(rows)[:30],
    }
    payload = {"summary": summary, "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.gold_output:
        gold_payload = build_gold_payload(summary, rows)
        args.gold_output.parent.mkdir(parents=True, exist_ok=True)
        args.gold_output.write_text(json.dumps(gold_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        "structure_eval "
        f"rows={summary['input_rows']} changed={summary['changed_rows']} "
        f"accepted={summary['accepted_rows']} unsafe={summary['unsafe_accepted_rows']} "
        f"accepted_rate={summary['accepted_rate']}"
    )
    if unsafe:
        raise SystemExit(1)


def collect_examples(history_path: Path, corpus_paths: list[Path], limit_history: int, since_date: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    if history_path.exists():
        history_rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(history_path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            text = str(payload.get("corrected_text") or payload.get("pasted_text") or payload.get("raw_text") or "")
            status = str(payload.get("status") or "")
            if status in {"no_speech", "too_short"}:
                continue
            created_at = str(payload.get("created_at") or "")
            if since_date and created_at[:10] < since_date:
                continue
            text = preferred_history_text(payload)
            if should_keep_text(text):
                history_rows.append(
                    {
                        "id": f"history:{line_number}:{payload.get('run_id') or len(history_rows)}",
                        "source": "history",
                        "text": normalize_space(text),
                        "terms": [],
                        "history_status": status,
                        "history_processor": str(payload.get("processor") or ""),
                        "history_route": str(payload.get("route") or ""),
                        "history_created_at": created_at,
                        "history_run_id": str(payload.get("run_id") or ""),
                    }
                )
        for item in history_rows[-limit_history:]:
            add_unique(items, seen, item)

    for corpus_path in corpus_paths:
        if not corpus_path.exists():
            continue
        data = json.loads(corpus_path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("rows") or data.get("items") or []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            text = str(row.get("gold") or row.get("text") or row.get("actual") or "")
            if not should_keep_text(text):
                continue
            terms = row.get("terms") or row.get("critical_terms") or row.get("critical") or []
            add_unique(
                items,
                seen,
                {
                    "id": f"{corpus_path.name}:{row.get('id') or index}",
                    "source": corpus_path.name,
                    "text": normalize_space(text),
                    "terms": [str(term) for term in terms if str(term).strip()],
                },
            )
    return items


def preferred_history_text(payload: dict[str, Any]) -> str:
    """Use the first pasted/raw text as the meaning reference, not old polish candidates."""
    processor = str(payload.get("processor") or "")
    raw_text = str(payload.get("raw_text") or "")
    pasted_text = str(payload.get("pasted_text") or "")
    corrected_text = str(payload.get("corrected_text") or "")
    if processor in {"light-polish", "friendly-rewrite", "structure"}:
        return raw_text or pasted_text or corrected_text
    return pasted_text or raw_text or corrected_text


def add_unique(items: list[dict[str, Any]], seen: set[str], item: dict[str, Any]) -> None:
    key = normalize_for_compare(item["text"])
    if key in seen:
        return
    seen.add(key)
    items.append(item)


def should_keep_text(text: str) -> bool:
    compact = normalize_space(text)
    words = compact.split()
    return 3 <= len(words) <= 220 and 12 <= len(compact) <= 1400


def run_swift_rewrite(requests: list[dict[str, str]]) -> list[dict[str, Any]]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(requests, handle, ensure_ascii=False)
        input_path = Path(handle.name)
    try:
        command = [
            "swift",
            "run",
            "RambleFixHotkeyPolicyTool",
            "--policy",
            "structure",
            "--project-root",
            str(ROOT),
            "--input",
            str(input_path),
        ]
        result = subprocess.run(command, cwd=NATIVE, check=True, text=True, capture_output=True)
        return json.loads(result.stdout)
    finally:
        input_path.unlink(missing_ok=True)


def safety_report(draft: str, final: str, explicit_terms: list[str]) -> dict[str, Any]:
    draft_numbers = number_tokens(draft)
    final_numbers = number_tokens(final)
    draft_negations = negations(draft)
    final_negations = negations(final)
    terms = sorted(set(explicit_terms + protected_pattern_terms(draft)))
    draft_compare = normalize_for_compare(draft)
    final_compare = normalize_for_compare(final)
    dropped_terms = [
        term
        for term in terms
        if normalize_for_compare(term) in draft_compare
        and normalize_for_compare(term) not in final_compare
    ]
    return {
        "dropped_numbers": sorted(draft_numbers - final_numbers),
        "dropped_negations": sorted(draft_negations - final_negations),
        "dropped_terms": dropped_terms,
        "changed_mixed_language": looks_mixed_language(draft) and normalize_for_compare(draft) != normalize_for_compare(final),
    }


def number_tokens(text: str) -> set[str]:
    return set(re.findall(r"\b\d+(?:[.,]\d+)?\b", text))


def negations(text: str) -> set[str]:
    return {token for token in tokens(text) if token in NEGATION_TOKENS}


def protected_pattern_terms(text: str) -> list[str]:
    terms = set(re.findall(r"\b[A-Z][A-Z0-9]{1,9}s?\b", text))
    terms.update(re.findall(r"\b[A-Za-z]+[0-9][A-Za-z0-9]*\b", text))
    terms.update(re.findall(r"\b[A-Za-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b", text))
    return sorted(terms)


def looks_mixed_language(text: str) -> bool:
    if re.search(r"[\u0900-\u097F\u0600-\u06FF\u4E00-\u9FFF]", text):
        return True
    return any(token in HINGLISH_MARKERS for token in tokens(text))


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def sentence_count(text: str) -> int:
    return len(re.findall(r"[.?!]", text))


def rule_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not row["accepted"]:
            continue
        for rule in row["rules"]:
            counts[rule] = counts.get(rule, 0) + 1
    return dict(sorted(counts.items()))


def review_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not row["accepted"]:
            continue
        reasons: list[str] = []
        if abs(int(row["char_delta"])) >= 12:
            reasons.append("large_char_delta")
        if int(row["sentence_delta"]) >= 4:
            reasons.append("large_sentence_delta")
        if row["final"].endswith("?") and not row["text"].rstrip().endswith("?"):
            reasons.append("question_mark_added")
        if reasons:
            candidates.append(
                {
                    "id": row["id"],
                    "source": row["source"],
                    "review_reasons": reasons,
                    "text": row["text"],
                    "final": row["final"],
                    "rules": row["rules"],
                    "safety": row["safety"],
                }
            )
    return candidates


def build_gold_payload(summary: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    gold_rows = []
    for row in rows:
        review = review_reasons(row)
        gold_rows.append(
            {
                "id": row["id"],
                "source": row["source"],
                "meaning_gold": row["text"],
                "structure_candidate": row["final"],
                "expected_runtime_behavior": "replace_if_unchanged" if row["accepted"] else "skip",
                "meaning_preserved_by_automatic_checks": row["accepted"] and not review["unsafe"],
                "requires_human_review": bool(review["review_reasons"] or review["unsafe"]),
                "review_reasons": review["review_reasons"],
                "rules": row["rules"],
                "safety": row["safety"],
                "metadata": {
                    key: row[key]
                    for key in (
                        "history_created_at",
                        "history_status",
                        "history_processor",
                        "history_route",
                        "history_run_id",
                    )
                    if key in row
                },
            }
        )
    return {
        "schema": "ramblefix_structure_meaning_gold_v1",
        "definition": "meaning_gold is the original raw/pasted text. structure_candidate may only improve formatting/readability without changing meaning.",
        "summary": summary,
        "rows": gold_rows,
    }


def review_reasons(row: dict[str, Any]) -> dict[str, Any]:
    unsafe = bool(
        row.get("dropped_protected_terms")
        or row["safety"]["dropped_numbers"]
        or row["safety"]["dropped_negations"]
        or row["safety"]["dropped_terms"]
        or row["safety"]["changed_mixed_language"]
    )
    reasons: list[str] = []
    if row["accepted"]:
        if abs(int(row["char_delta"])) >= 12:
            reasons.append("large_char_delta")
        if int(row["sentence_delta"]) >= 4:
            reasons.append("large_sentence_delta")
        if row["final"].endswith("?") and not row["text"].rstrip().endswith("?"):
            reasons.append("question_mark_added")
    return {"unsafe": unsafe, "review_reasons": reasons}


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_for_compare(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


if __name__ == "__main__":
    main()
