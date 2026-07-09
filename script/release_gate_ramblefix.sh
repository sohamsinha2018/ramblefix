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

OUT_DIR="$ROOT/logs/release_gate"
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"

echo "== RambleFix release gate =="
echo "python=$PYTHON"
echo "stamp=$STAMP"

echo "== Native hotkey regression =="
"$ROOT/script/regression_ramblefix_hotkey.sh"

echo "== Saved-audio STT quality regression =="
"$ROOT/script/regression_ramblefix_quality.sh" "$OUT_DIR/quality_$STAMP"

echo "== V0 release scope =="
"$ROOT/script/validate_v0_release_scope.sh"

echo "== Python compile =="
"$PYTHON" -m compileall -q src app.py scripts

echo "== Work polish regression =="
"$PYTHON" scripts/regression_work_polish.py

echo "== Friendly rewrite safety eval =="
"$PYTHON" scripts/eval_native_friendly_rewrite.py \
  --history "$ROOT/logs/history.jsonl" \
  --corpus "$ROOT/eval_corpus/ramblefix_corpus.json" \
  --limit-history 300 \
  --output "$OUT_DIR/friendly_rewrite_$STAMP.json"

echo "== Learning memory regression =="
"$PYTHON" scripts/regression_learning_memory.py
"$PYTHON" scripts/regression_term_repair.py

KNOWN_TRUNCATION_WAV="$ROOT/logs/hotkey_audio/20260706-203258-308FF1.wav"
if [[ -f "$KNOWN_TRUNCATION_WAV" ]]; then
  echo "== Known long-clip nonhang replay =="
  REPLAY_JSON="$OUT_DIR/known_truncation_replay_$STAMP.json"
  "$PYTHON" -m ramblefix.cli dictate-audio "$KNOWN_TRUNCATION_WAV" --json --no-cleanup --skip-process-fallback > "$REPLAY_JSON"
  REPLAY_JSON="$REPLAY_JSON" "$PYTHON" - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["REPLAY_JSON"])
payload = json.loads(path.read_text())
text = (payload.get("text") or payload.get("corrected_text") or "").strip()
route = payload.get("route") or ""
fallback_reason = payload.get("fallback_reason") or ""
if len(text) < 250:
    raise SystemExit(f"known long replay too short: chars={len(text)} route={route}")
if "process_fallback_skipped" not in route:
    raise SystemExit(f"known long replay should skip process fallback in V0: route={route}")
if "suspected_truncated_server_output" not in fallback_reason:
    raise SystemExit(f"known long replay should log truncation risk: {fallback_reason}")
print(f"known long replay passed chars={len(text)} route={route} fallback_reason={fallback_reason}")
PY
else
  echo "skip known long-clip replay: $KNOWN_TRUNCATION_WAV not found"
fi

echo "== Build signed app bundle without install =="
RAMBLEFIX_APP_NAME="${RAMBLEFIX_APP_NAME:-RambleFix Local}" \
RAMBLEFIX_BUNDLE_ID="${RAMBLEFIX_BUNDLE_ID:-com.ramblefix.local}" \
RAMBLEFIX_EXECUTABLE_NAME="${RAMBLEFIX_EXECUTABLE_NAME:-RambleFixLocal}" \
"$ROOT/script/build_macos_app.sh" "$ROOT/dist/release-gate/RambleFix Local.app" >/dev/null
"$ROOT/script/audit_macos_release_artifact.sh" "$ROOT/dist/release-gate/RambleFix Local.app"

echo "RambleFix release gate passed"
