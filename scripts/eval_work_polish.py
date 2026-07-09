from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
import sys

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import meaning_coverage, meaning_loss, repeated_substring_score, term_coverage_report, word_error_rate
from ramblefix.work_polish import polish_meaning_first_work_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate constrained work-text polish on existing scorecard rows.")
    parser.add_argument("scorecard_json", type=Path)
    parser.add_argument("--backend", default="whisper_cpp_server_translate")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    payload = json.loads(args.scorecard_json.read_text(encoding="utf-8"))
    rows = payload.get("rows", [])
    polished_rows: list[dict[str, Any]] = []
    for row in rows:
        if row.get("backend") != args.backend:
            continue
        started = time.perf_counter()
        result = polish_meaning_first_work_text(str(row.get("actual") or ""))
        polish_seconds = round(time.perf_counter() - started, 6)
        terms = row.get("term_terms") or []
        term_report = term_coverage_report(str(row.get("gold") or ""), result.text, terms)
        seconds = round(float(row.get("seconds") or 0.0) + polish_seconds, 6)
        next_row = {
            **row,
            "backend": f"{args.backend}_work_polish",
            "actual": result.text,
            "wer": word_error_rate(str(row.get("gold") or ""), result.text),
            "meaning_loss": meaning_loss(str(row.get("gold") or ""), result.text),
            "meaning_coverage": meaning_coverage(str(row.get("gold") or ""), result.text),
            "term_coverage": term_report["coverage"],
            "term_hits": term_report["hits"],
            "term_misses": term_report["misses"],
            "term_terms": term_report["terms"],
            "repeat": repeated_substring_score(result.text),
            "seconds": seconds,
            "meta": {
                **(row.get("meta") if isinstance(row.get("meta"), dict) else {}),
                "source_backend": args.backend,
                "polish_changed": result.changed,
                "polish_rules": result.rules,
                "polish_seconds": polish_seconds,
            },
            "error": row.get("error"),
        }
        polished_rows.append(next_row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(polished_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output} rows={len(polished_rows)}")


if __name__ == "__main__":
    main()
