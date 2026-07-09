#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

swift run --package-path native/RambleFixHotkey RambleFixHotkeyRegressionTests
swift build --package-path native/RambleFixHotkey

POLICY_INPUT="$(mktemp)"
trap 'rm -f "$POLICY_INPUT"' EXIT
printf '%s\n' '[{"id":"mcp-plural-possessive","draft":"The way MCPs work is that there is an API layer.","final":"The way MCP'\''s work is that there is an API layer."},{"id":"wedge-to-bench","draft":"If the tool cannot beat others on one core problem, then there is no wedge.","final":"If the tool cannot beat others on one core problem, then there is no bench."}]' > "$POLICY_INPUT"
POLICY_OUTPUT="$(swift run --package-path native/RambleFixHotkey RambleFixHotkeyPolicyTool --input "$POLICY_INPUT" --project-root "$ROOT" --policy server-safe)"

PYTHON="${RAMBLEFIX_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

POLICY_OUTPUT="$POLICY_OUTPUT" "$PYTHON" - <<'PY'
import json
import os
import sys

payload = json.loads(os.environ["POLICY_OUTPUT"])
by_id = {row["id"]: row for row in payload}
mcp = by_id["mcp-plural-possessive"]
assert mcp["accepted"] is True, mcp
assert mcp["droppedProtectedTerms"] == [], mcp
wedge = by_id["wedge-to-bench"]
assert wedge["accepted"] is False, wedge
assert wedge["droppedProtectedTerms"] == ["wedge"], wedge
PY
"$PYTHON" -m compileall -q src app.py
"$PYTHON" scripts/regression_learning_memory.py
"$PYTHON" scripts/regression_term_repair.py
"$PYTHON" scripts/regression_srota_server_lazy_imports.py
"$PYTHON" scripts/regression_srota_inference_endpoint.py
"$PYTHON" scripts/regression_streaming_recorder_lifecycle.py
grep -q 'RAMBLEFIX_HINGLISH_FINALIZER_BACKEND.*oriserve' script/start_srota_server.sh
grep -q 'RAMBLEFIX_ORISERVE_BACKEND.*ggml' script/start_srota_server.sh
grep -q 'PYTHONDONTWRITEBYTECODE' script/start_srota_server.sh
grep -q 'PYTHONDONTWRITEBYTECODE' native/RambleFixHotkey/Sources/RambleFixHotkey/main.swift
if rg -n 'start_whisper_server|127[.]0[.]0[.]1 8178|whisper-server-8178' script/install_ramblefix_app.sh; then
  echo "Installer must not autostart legacy whisper.cpp 8178; product path is local Srota 8188" >&2
  exit 1
fi
grep -q 'AUTOSTART_NATIVE_ASR_SERVER' native/RambleFixHotkey/Sources/RambleFixHotkey/main.swift
grep -q 'script/start_srota_server.sh' script/package_macos_release.sh
if rg -n 'RAMBLEFIX_HOTKEY_CHINESE_POLISH|chinesePolish|ChinesePolishPolicy|ChineseEnglishPolicy|chineseMotionVariant|/chinese-polish|RF cn' native/RambleFixHotkey/Sources; then
  echo "Chinese native MVP path must stay removed" >&2
  exit 1
fi
grep -q 'shouldRunLearnedTermPolish' native/RambleFixHotkey/Sources/RambleFixHotkey/main.swift
./script/native_record.sh --help >/dev/null

echo "RambleFix hotkey regression gate passed"
