from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW = ROOT / "eval_runs/fresh-hindi-probe-20260629/hindi_training_review_20260630/review_set.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Add non-authoritative suggested labels to the Hindi review set.")
    parser.add_argument("--review-json", type=Path, default=DEFAULT_REVIEW)
    args = parser.parse_args()

    review_path = args.review_json.expanduser().resolve()
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    changed = False
    for row in payload.get("rows", []):
        suggestion = _suggestion(row)
        for key, value in suggestion.items():
            if row.get(key) != value:
                row[key] = value
                changed = True

    if changed:
        backup = review_path.with_suffix(f".{int(time.time())}.suggestions.bak.json")
        shutil.copy2(review_path, backup)
        review_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"updated {review_path}")
        print(f"backup {backup}")
    else:
        print(f"no changes {review_path}")


def _suggestion(row: dict) -> dict[str, str]:
    fast = str(row.get("fast_text") or "").strip()
    current = str(row.get("current_final") or "").strip()
    raw = str(row.get("srota_raw") or "").strip()
    reject_reasons = [str(item) for item in row.get("reject_reasons") or []]
    safe_update = bool(row.get("safe_update"))
    risk = bool(row.get("risk"))

    if safe_update and current:
        return {
            "suggested_gold_intent": current,
            "suggested_gold_source": "current_final_safe_update",
            "suggested_gold_warning": "Check audio. This was accepted by gates, but may still be too verbatim or awkward.",
        }
    if reject_reasons:
        reason_text = ", ".join(reject_reasons)
        return {
            "suggested_gold_intent": fast or current,
            "suggested_gold_source": "fast_text_rejected_hindi_candidate",
            "suggested_gold_warning": f"Listen closely. Hindi candidate was rejected: {reason_text}. Raw may still contain a small true Hindi phrase.",
        }
    if risk and raw and not current:
        return {
            "suggested_gold_intent": fast,
            "suggested_gold_source": "fast_text_risk_no_safe_update",
            "suggested_gold_warning": "Hindi risk was detected, but no safe update survived. Verify against audio.",
        }
    return {
        "suggested_gold_intent": current or fast,
        "suggested_gold_source": "fast_or_current_no_hindi_risk",
        "suggested_gold_warning": "Likely low Hindi value; still verify terms and meaning.",
    }


if __name__ == "__main__":
    main()
