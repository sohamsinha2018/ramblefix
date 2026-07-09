#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PUBLIC=0
ALLOW_PLACEHOLDERS=1
ALLOW_DIRTY_MACHINE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --public)
      PUBLIC=1
      ALLOW_PLACEHOLDERS=0
      shift
      ;;
    --allow-placeholders)
      ALLOW_PLACEHOLDERS=1
      shift
      ;;
    --no-placeholders)
      ALLOW_PLACEHOLDERS=0
      shift
      ;;
    --allow-dirty-machine)
      ALLOW_DIRTY_MACHINE=1
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="$ROOT/eval_runs/final-launch-eval-$STAMP"
QUALITY_DIR="$RUN_DIR/quality_gate"
RESIDENT_DIR="$RUN_DIR/resident_server_expanded"
RESIDENT_CORPUS="$ROOT/eval_corpus/launch_real_use_expanded_20260709_checked.json"
SUMMARY="$RUN_DIR/final_launch_eval_summary.md"
mkdir -p "$RUN_DIR"

log() {
  printf '\n== %s ==\n' "$*"
}

log "Machine health"
if [[ "$PUBLIC" == "1" && "$ALLOW_DIRTY_MACHINE" == "1" ]]; then
  echo "--allow-dirty-machine cannot be used with --public" >&2
  exit 2
fi
if [[ "$ALLOW_DIRTY_MACHINE" == "1" ]]; then
  "$ROOT/script/audit_eval_machine_health.sh" --warn-only
else
  "$ROOT/script/audit_eval_machine_health.sh" --strict
fi

log "Compile smoke"
"$ROOT/.venv/bin/python" -m compileall -q src app.py scripts

log "Native hotkey regression"
"$ROOT/script/regression_ramblefix_hotkey.sh"

log "Quality regression"
if [[ "$ALLOW_DIRTY_MACHINE" == "1" ]]; then
  RAMBLEFIX_ALLOW_DIRTY_EVAL_MACHINE=1 "$ROOT/script/regression_ramblefix_quality.sh" "$QUALITY_DIR"
else
  "$ROOT/script/regression_ramblefix_quality.sh" "$QUALITY_DIR"
fi

log "Resident server endpoint"
"$ROOT/.venv/bin/python" "$ROOT/scripts/regression_srota_inference_endpoint.py"

log "Resident server expanded eval"
"$ROOT/.venv/bin/python" "$ROOT/scripts/eval_resident_server_with_structure.py" \
  --corpus "$RESIDENT_CORPUS" \
  --output-dir "$RESIDENT_DIR"

log "Benchmark claim audit"
"$ROOT/scripts/audit_benchmark_claims.py"

log "Site visual smoke"
"$ROOT/script/smoke_site_visual.sh"

log "Package release"
"$ROOT/script/package_macos_release.sh"

log "Artifact checksums"
"$ROOT/script/audit_release_checksums.sh" "$ROOT/dist/release/DictaHue-0.1.0.SHA256SUMS"

log "Release security"
security_args=()
if [[ "$PUBLIC" == "1" ]]; then
  security_args+=(--public)
else
  security_args+=(--local)
fi
"$ROOT/script/audit_release_security.sh" "$ROOT/dist/release/DictaHue.app" "${security_args[@]}"

log "Launch readiness"
readiness_args=()
if [[ "$PUBLIC" == "1" ]]; then
  readiness_args+=(--public)
fi
if [[ "$ALLOW_PLACEHOLDERS" == "1" ]]; then
  readiness_args+=(--allow-placeholders)
fi
"$ROOT/script/audit_public_launch_readiness.sh" "${readiness_args[@]}"

{
  echo "# DictaHue Final Launch Eval"
  echo
  echo "- Created: $STAMP"
  echo "- Public mode: $PUBLIC"
  echo "- Placeholders allowed: $ALLOW_PLACEHOLDERS"
  echo "- Dirty machine allowed: $ALLOW_DIRTY_MACHINE"
  echo "- Quality gate: $QUALITY_DIR"
  echo "- Resident expanded eval: $RESIDENT_DIR"
  echo
  echo "## Quality Gate Summary"
  echo
  if [[ -f "$QUALITY_DIR/quality_gate_summary.json" ]]; then
    "$ROOT/.venv/bin/python" - <<'PY' "$QUALITY_DIR/quality_gate_summary.json"
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text())
for key, value in summary.items():
    print(f"- {key}: {value}")
PY
  else
    echo "- Missing quality_gate_summary.json"
  fi
  echo
  echo "## Resident Server Expanded Eval"
  echo
  if [[ -f "$RESIDENT_DIR/scorecard.json" ]]; then
    "$ROOT/.venv/bin/python" - <<'PY' "$RESIDENT_DIR/scorecard.json"
import json
import sys
from pathlib import Path

scorecard = json.loads(Path(sys.argv[1]).read_text())
for row in scorecard.get("summary", []):
    print(
        f"- {row['backend']}: clips={row['clips']}, "
        f"useful={row['avg_useful_score']:.3f}, meaning={row['avg_coverage']:.3f}, "
        f"p50={row['p50_seconds']:.3f}s, p95={row['p95_seconds']:.3f}s"
    )
PY
  else
    echo "- Missing resident server scorecard.json"
  fi
  echo
  if [[ -f "$RESIDENT_DIR/summary_by_category.md" ]]; then
    cat "$RESIDENT_DIR/summary_by_category.md"
  else
    echo "- Missing resident server summary_by_category.md"
  fi
  echo
  echo "## Checksums"
  echo
  echo '```text'
  cat "$ROOT/dist/release/DictaHue-0.1.0.SHA256SUMS"
  echo '```'
} > "$SUMMARY"

echo
echo "final launch eval passed"
echo "$SUMMARY"
