#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${RAMBLEFIX_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="${1:-$ROOT/eval_runs/regression-quality-$STAMP}"
mkdir -p "$RUN_DIR"

echo "== RambleFix quality regression gate =="
echo "run_dir=$RUN_DIR"

if [[ "${RAMBLEFIX_ALLOW_DIRTY_EVAL_MACHINE:-0}" != "1" ]]; then
  "$ROOT/script/audit_eval_machine_health.sh" --strict
else
  "$ROOT/script/audit_eval_machine_health.sh" --warn-only || true
fi

"$PYTHON" scripts/build_ramblefix_sentinel_corpus.py

"$PYTHON" scripts/eval_dictate_audio_product_path.py \
  --corpus eval_corpus/ramblefix_sentinel_current.json \
  --output "$RUN_DIR/product_path.json" \
  --timeout-seconds 120

"$PYTHON" scripts/eval_dictate_audio_product_path.py \
  --corpus eval_corpus/ramblefix_known_failures.json \
  --output "$RUN_DIR/known_failures_product_path.json" \
  --timeout-seconds 120

"$PYTHON" scripts/eval_native_friendly_rewrite.py \
  --since-date 2026-07-03 \
  --limit-history 10000 \
  --corpus eval_corpus/ramblefix_sentinel_current.json \
  --output "$RUN_DIR/structure_eval.json" \
  --gold-output "$RUN_DIR/structure_gold.json"

RUN_DIR="$RUN_DIR" "$PYTHON" - <<'PY'
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

run_dir = Path(os.environ["RUN_DIR"])
product_rows = json.loads((run_dir / "product_path.json").read_text())
known_rows = json.loads((run_dir / "known_failures_product_path.json").read_text())
structure = json.loads((run_dir / "structure_eval.json").read_text())


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * q))
    return ordered[min(max(index, 0), len(ordered) - 1)]


def avg(rows: list[dict], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else 0.0


errors = [row for row in product_rows if row.get("error")]
blanks = [row for row in product_rows if not str(row.get("actual") or "").strip()]
seconds = [float(row.get("seconds") or 0.0) for row in product_rows if not row.get("error")]
p50 = percentile(seconds, 0.50)
p95 = percentile(seconds, 0.95)
max_seconds = max(seconds) if seconds else 0.0
def audio_duration(row: dict) -> float:
    quality = ((row.get("meta") or {}).get("quality") or {})
    return float(
        row.get("audio_duration_seconds")
        or quality.get("audio_duration_seconds")
        or 0.0
    )


short_rows = [row for row in product_rows if audio_duration(row) <= 30.0]
long_rows = [row for row in product_rows if audio_duration(row) > 30.0]
short_seconds = [float(row.get("seconds") or 0.0) for row in short_rows if not row.get("error")]
long_seconds = [float(row.get("seconds") or 0.0) for row in long_rows if not row.get("error")]
short_p95 = percentile(short_seconds, 0.95)
short_max_seconds = max(short_seconds) if short_seconds else 0.0
long_p95 = percentile(long_seconds, 0.95)
long_max_seconds = max(long_seconds) if long_seconds else 0.0

if errors:
    fail(f"product path errors={len(errors)} first={errors[0].get('id')} {errors[0].get('error')}")
if blanks:
    fail(f"blank product transcripts={len(blanks)} first={blanks[0].get('id')}")
if short_p95 > 3.0:
    fail(f"short product p95 too slow: {short_p95:.3f}s")
if short_max_seconds > 4.0:
    fail(f"short product max latency too slow: {short_max_seconds:.3f}s")
if long_rows and long_p95 > 6.0:
    fail(f"long product p95 too slow: {long_p95:.3f}s")
if long_rows and long_max_seconds > 8.0:
    fail(f"long product max latency too slow: {long_max_seconds:.3f}s")

by_category: dict[str, list[dict]] = defaultdict(list)
for row in product_rows:
    by_category[str(row.get("category") or "")].append(row)

trusted_hinglish = by_category.get("real_use_hindi_hinglish_probe", [])
if trusted_hinglish:
    term = avg(trusted_hinglish, "term_coverage")
    meaning = avg(trusted_hinglish, "meaning_coverage")
    if term < 0.80 or meaning < 0.85:
        fail(f"trusted Hindi+English probe dropped: term={term:.3f} meaning={meaning:.3f}")

recent = by_category.get("recent_real_use_regression_baseline", [])
if recent:
    term = avg(recent, "term_coverage")
    meaning = avg(recent, "meaning_coverage")
    if term < 0.65 or meaning < 0.85:
        fail(f"recent real-use baseline dropped: term={term:.3f} meaning={meaning:.3f}")

known_errors = [row for row in known_rows if row.get("error")]
known_blanks = [row for row in known_rows if not str(row.get("actual") or "").strip()]
if known_errors:
    fail(f"known-failure replay errored: {known_errors[0].get('error')}")
if known_blanks:
    fail("known-failure replay returned blank text")
known = known_rows[0] if known_rows else {}
known_term = float(known.get("term_coverage") or 0.0)
if known_term < 0.60:
    fail(f"known failure worsened: term_coverage={known_term:.3f}")
known_misses = [str(term) for term in (known.get("term_misses") or []) if str(term).strip()]
allow_known_failures = os.environ.get("RAMBLEFIX_ALLOW_KNOWN_FAILURES", "").strip().lower() in {"1", "true", "yes"}
if known_misses and not allow_known_failures:
    fail("known failure still misses tracked terms: " + ", ".join(known_misses))

summary = structure.get("summary") or {}
unsafe = int(summary.get("unsafe_accepted_rows") or 0)
if unsafe:
    fail(f"unsafe structure rows accepted: {unsafe}")

report = {
    "product_rows": len(product_rows),
    "short_product_rows": len(short_rows),
    "long_product_rows": len(long_rows),
    "p50_seconds": round(p50, 3),
    "p95_seconds": round(p95, 3),
    "max_seconds": round(max_seconds, 3),
    "short_p95_seconds": round(short_p95, 3),
    "short_max_seconds": round(short_max_seconds, 3),
    "long_p95_seconds": round(long_p95, 3),
    "long_max_seconds": round(long_max_seconds, 3),
    "trusted_hinglish_term": round(avg(trusted_hinglish, "term_coverage"), 3) if trusted_hinglish else None,
    "trusted_hinglish_meaning": round(avg(trusted_hinglish, "meaning_coverage"), 3) if trusted_hinglish else None,
    "recent_term": round(avg(recent, "term_coverage"), 3) if recent else None,
    "recent_meaning": round(avg(recent, "meaning_coverage"), 3) if recent else None,
    "known_failure_term": round(known_term, 3),
    "known_failure_misses": known_misses,
    "known_failure_release_allowed": allow_known_failures,
    "structure_unsafe_accepted_rows": unsafe,
    "structure_accepted_rows": summary.get("accepted_rows"),
}
(run_dir / "quality_gate_summary.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
PY

echo "RambleFix quality regression gate passed"
