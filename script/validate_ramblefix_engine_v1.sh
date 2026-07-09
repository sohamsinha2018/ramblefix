#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${RAMBLEFIX_PYTHON:-$ROOT/.venv/bin/python}"
RUN_ID="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="$ROOT/eval_runs/engine_v1_validation/$RUN_ID"
ENGLISH_OUT="$OUT_DIR/p0_english"
HINGLISH_OUT="$OUT_DIR/p0_hinglish"
HEALTH_JSON="$OUT_DIR/local_health.json"
REPORT_JSON="$OUT_DIR/report.json"
REPORT_MD="$OUT_DIR/report.md"
WEB_PROVIDER_JSON="$OUT_DIR/p1_mirotalk_meeting/mirotalk_provider_smoke.json"
VDO_PROVIDER_JSON="$OUT_DIR/p1_vdo_ninja_meeting/vdo_ninja_provider_smoke.json"
CLEAN_ENGLISH_CORPUS="$OUT_DIR/p0_english_clean_corpus.json"

ENGLISH_CORPUS="$ROOT/eval_runs/todays-engine-20260704/local-product-corpus/goal_stt_corpus_english_eval_ready.json"
HINGLISH_CORPUS="$ROOT/eval_runs/todays-engine-20260704/local-product-corpus/confirmed_hindi_english.json"

mkdir -p "$OUT_DIR"

if [[ ! -x "$PYTHON" ]]; then
  echo "Python not found: $PYTHON" >&2
  exit 2
fi

ensure_whisper_server() {
  "$PYTHON" - <<'PY' >/dev/null 2>&1 && return 0
import socket
with socket.create_connection(("127.0.0.1", 8178), timeout=1.0):
    pass
PY
  echo "Starting local whisper server on 127.0.0.1:8178"
  "$ROOT/script/start_whisper_server.sh" > "$OUT_DIR/whisper-server.log" 2>&1 &
  local server_pid=$!
  echo "$server_pid" > "$OUT_DIR/whisper-server.pid"
  for _ in {1..30}; do
    if "$PYTHON" - <<'PY' >/dev/null 2>&1; then
import socket
with socket.create_connection(("127.0.0.1", 8178), timeout=1.0):
    pass
PY
      return 0
    fi
    sleep 1
  done
  echo "whisper server did not become ready" >&2
  exit 3
}

ensure_whisper_server

"$PYTHON" - "$ENGLISH_CORPUS" "$HINGLISH_CORPUS" "$CLEAN_ENGLISH_CORPUS" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

english_path = Path(sys.argv[1])
hinglish_path = Path(sys.argv[2])
output_path = Path(sys.argv[3])

english = json.loads(english_path.read_text(encoding="utf-8"))
hinglish = json.loads(hinglish_path.read_text(encoding="utf-8"))
hinglish_ids = {str(row.get("id") or "") for row in hinglish}
clean = [row for row in english if str(row.get("id") or "") not in hinglish_ids]
if not clean:
    raise SystemExit("clean English corpus is empty after removing Hinglish overlap")
output_path.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"clean English corpus: {len(clean)}/{len(english)} rows")
PY

echo "1/8 local health"
"$PYTHON" "$ROOT/scripts/check_local_accelerator_health.py" > "$HEALTH_JSON"

echo "2/8 native and verifier regression"
"$ROOT/script/regression_ramblefix_hotkey.sh" > "$OUT_DIR/native_regression.log" 2>&1
"$PYTHON" "$ROOT/scripts/regression_live_provider_verifier.py" > "$OUT_DIR/live_provider_verifier_regression.log" 2>&1

echo "3/8 P0 English eval"
"$PYTHON" "$ROOT/scripts/eval_two_phase_product_path.py" \
  --corpus "$CLEAN_ENGLISH_CORPUS" \
  --output-dir "$ENGLISH_OUT" \
  --first-pass native-direct \
  --polish-mode detected \
  --limit 20 > "$OUT_DIR/p0_english.log" 2>&1

echo "4/8 P0 Hinglish eval"
"$PYTHON" "$ROOT/scripts/eval_two_phase_product_path.py" \
  --corpus "$HINGLISH_CORPUS" \
  --output-dir "$HINGLISH_OUT" \
  --first-pass native-direct \
  --polish-mode force \
  --limit 10 > "$OUT_DIR/p0_hinglish.log" 2>&1

echo "5/8 P1 system audio smoke"
"$ROOT/script/smoke_system_audio_meeting_capture.sh" > "$OUT_DIR/p1_system_audio_smoke.log" 2>&1

echo "6/8 P1 browser meeting smoke"
"$ROOT/script/smoke_browser_meeting_capture.sh" > "$OUT_DIR/p1_browser_meeting_smoke.log" 2>&1

echo "7/8 P1 short dual-source meeting smoke"
"$PYTHON" "$ROOT/scripts/smoke_dual_source_meeting.py" \
  --output-dir "$OUT_DIR/p1_dual_source_meeting" > "$OUT_DIR/p1_dual_source_meeting.log" 2>&1

echo "8/8 P1 long dual-source meeting smoke"
"$PYTHON" "$ROOT/scripts/smoke_dual_source_meeting.py" \
  --output-dir "$OUT_DIR/p1_long_dual_source_meeting" \
  --scenario long \
  --chunk-seconds 10 > "$OUT_DIR/p1_long_dual_source_meeting.log" 2>&1

if [[ "${RAMBLEFIX_RUN_WEB_PROVIDER_SMOKE:-0}" == "1" ]]; then
  if [[ "${RAMBLEFIX_RUN_MIROTALK_SMOKE:-0}" == "1" || "${RAMBLEFIX_RUN_ALL_WEB_PROVIDER_SMOKES:-0}" == "1" ]]; then
    echo "optional P1 MiroTalk web provider smoke"
    if RAMBLEFIX_MIROTALK_SMOKE_OUT="$OUT_DIR/p1_mirotalk_meeting" \
      "$ROOT/script/smoke_mirotalk_meeting_capture.sh" > "$OUT_DIR/p1_mirotalk_meeting.log" 2>&1; then
      true
    else
      cat "$OUT_DIR/p1_mirotalk_meeting.log" >&2
    fi
  fi

  echo "optional P1 VDO.Ninja web provider smoke"
  if RAMBLEFIX_VDO_NINJA_SMOKE_OUT="$OUT_DIR/p1_vdo_ninja_meeting" \
    "$ROOT/script/smoke_vdo_ninja_meeting_capture.sh" > "$OUT_DIR/p1_vdo_ninja_meeting.log" 2>&1; then
    true
  else
    cat "$OUT_DIR/p1_vdo_ninja_meeting.log" >&2
  fi
fi

if [[ "${RAMBLEFIX_REQUIRE_LIVE_PROVIDER:-0}" == "1" ]]; then
  "$PYTHON" "$ROOT/scripts/verify_live_provider_meeting.py" --json > "$OUT_DIR/live_provider_meeting.json"
else
  "$PYTHON" "$ROOT/scripts/verify_live_provider_meeting.py" --json > "$OUT_DIR/live_provider_meeting.json" 2>/dev/null || true
fi

"$PYTHON" - "$ROOT" "$OUT_DIR" "$HEALTH_JSON" "$ENGLISH_OUT/summary.json" "$HINGLISH_OUT/summary.json" "$REPORT_JSON" "$REPORT_MD" <<'PY'
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
out_dir = Path(sys.argv[2])
health_path = Path(sys.argv[3])
english_path = Path(sys.argv[4])
hinglish_path = Path(sys.argv[5])
report_json_path = Path(sys.argv[6])
report_md_path = Path(sys.argv[7])
live_provider_path = out_dir / "live_provider_meeting.json"
long_dual_path = out_dir / "p1_long_dual_source_meeting/dual_source_meeting.json"
web_provider_path = out_dir / "p1_mirotalk_meeting/mirotalk_provider_smoke.json"
vdo_provider_path = out_dir / "p1_vdo_ninja_meeting/vdo_ninja_provider_smoke.json"

health = json.loads(health_path.read_text())
english = json.loads(english_path.read_text())
hinglish = json.loads(hinglish_path.read_text())
live_provider = json.loads(live_provider_path.read_text()) if live_provider_path.exists() else {"ok": False, "error": "not_run"}
long_dual = json.loads(long_dual_path.read_text()) if long_dual_path.exists() else {"ok": False, "checks": []}
web_provider = json.loads(web_provider_path.read_text()) if web_provider_path.exists() else {"ok": False, "error": "not_run"}
vdo_provider = json.loads(vdo_provider_path.read_text()) if vdo_provider_path.exists() else {"ok": False, "error": "not_run"}
provider_results = [
    ("MiroTalk", web_provider),
    ("VDO.Ninja", vdo_provider),
]
provider_runs = [(name, payload) for name, payload in provider_results if payload.get("error") != "not_run"]
provider_passes = [(name, payload) for name, payload in provider_runs if payload.get("ok")]
require_web_provider = os.environ.get("RAMBLEFIX_REQUIRE_WEB_PROVIDER", "0") == "1"
require_all_web_providers = os.environ.get("RAMBLEFIX_REQUIRE_ALL_WEB_PROVIDER_SMOKES", "0") == "1"

checks: list[dict[str, object]] = []

def add_check(name: str, value: float | bool, passed: bool, threshold: str) -> None:
    checks.append({
        "name": name,
        "value": value,
        "threshold": threshold,
        "passed": passed,
    })

english_first = english["two_phase_first_output"]
english_final = english["two_phase_final_selected"]
hinglish_first = hinglish["two_phase_first_output"]
hinglish_final = hinglish["two_phase_final_selected"]
native_remote_urls: list[str] = []
for source in (root / "native/RambleFixHotkey/Sources").rglob("*.swift"):
    text = source.read_text(encoding="utf-8", errors="ignore")
    for match in re.finditer(r"https?://[^\"\\s)]+", text):
        url = match.group(0)
        if "127.0.0.1" in url or "localhost" in url or "example.com/inference" in url:
            continue
        native_remote_urls.append(f"{source.relative_to(root)}:{url}")

add_check("local whisper server reachable", any(c["name"] == "whisper_server_8178" and c["ok"] for c in health["checks"]), any(c["name"] == "whisper_server_8178" and c["ok"] for c in health["checks"]), "must be true")
add_check("native product endpoints loopback-only", len(native_remote_urls) == 0, len(native_remote_urls) == 0, "no remote URLs in native runtime sources")
add_check("P1 long meeting dual-source smoke", bool(long_dual.get("ok")), bool(long_dual.get("ok")), "labels, multiple chunks, and term recall gate")
long_term_recall = next((check for check in long_dual.get("checks", []) if check.get("name") == "long meeting term recall"), {})
long_recall_value = int(long_term_recall.get("value") or 0)
long_recall_minimum = int(long_term_recall.get("minimum") or 8)
long_recall_total = sum(1 for check in long_dual.get("checks", []) if check.get("required") is False)
add_check("P1 long meeting term recall", long_recall_value, long_recall_value >= long_recall_minimum, f">= {long_recall_minimum}")
if require_web_provider or provider_runs:
    add_check(
        "P1 real web provider smoke pass count",
        len(provider_passes),
        len(provider_passes) >= 1,
        ">= 1 configured real WebRTC provider captured and transcribed locally",
    )
if require_all_web_providers:
    for provider_name, provider_payload in provider_runs:
        add_check(
            f"P1 {provider_name} web provider smoke",
            bool(provider_payload.get("ok")),
            bool(provider_payload.get("ok")),
            f"{provider_name} provider audio captured and transcribed locally",
        )
add_check("P0 English first score", english_first["avg_score"], english_first["avg_score"] >= 0.88, ">= 0.88")
add_check("P0 English first p95 seconds", english_first["p95_seconds"], english_first["p95_seconds"] <= 2.1, "<= 2.1")
add_check("P0 English final score", english_final["avg_score"], english_final["avg_score"] >= 0.88, ">= 0.88")
add_check("P0 English final p95 seconds", english_final["p95_seconds"], english_final["p95_seconds"] <= 2.1, "<= 2.1")
add_check("P0 Hinglish first p95 seconds", hinglish_first["p95_seconds"], hinglish_first["p95_seconds"] <= 3.5, "<= 3.5")
add_check("P0 Hinglish final score", hinglish_final["avg_score"], hinglish_final["avg_score"] >= 0.74, ">= 0.74")
add_check("P0 Hinglish final p95 seconds", hinglish_final["p95_seconds"], hinglish_final["p95_seconds"] <= 4.0, "<= 4.0")

report = {
    "ok": all(bool(check["passed"]) for check in checks),
    "output_dir": str(out_dir),
    "checks": checks,
    "p0_english": english,
    "p0_hinglish": hinglish,
    "local_health": health,
    "native_remote_urls": native_remote_urls,
    "live_provider_meeting": live_provider,
    "web_provider_meeting": web_provider,
    "vdo_provider_meeting": vdo_provider,
    "web_provider_passes": [name for name, _payload in provider_passes],
    "web_provider_runs": [name for name, _payload in provider_runs],
    "p1_long_dual_source_meeting": long_dual,
    "p1_smokes": {
        "system_audio_log": str(out_dir / "p1_system_audio_smoke.log"),
        "browser_meeting_log": str(out_dir / "p1_browser_meeting_smoke.log"),
        "dual_source_meeting_log": str(out_dir / "p1_dual_source_meeting.log"),
        "dual_source_meeting_json": str(out_dir / "p1_dual_source_meeting/dual_source_meeting.json"),
        "long_dual_source_meeting_log": str(out_dir / "p1_long_dual_source_meeting.log"),
        "long_dual_source_meeting_json": str(long_dual_path),
        "web_provider_meeting_log": str(out_dir / "p1_mirotalk_meeting.log"),
        "web_provider_meeting_json": str(web_provider_path),
        "vdo_provider_meeting_log": str(out_dir / "p1_vdo_ninja_meeting.log"),
        "vdo_provider_meeting_json": str(vdo_provider_path),
    },
}
report_json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

lines = [
    "# RambleFix Engine V1 Validation",
    "",
    f"Output: `{out_dir}`",
    f"Overall: `{'PASS' if report['ok'] else 'FAIL'}`",
    "",
    "## Checks",
]
for check in checks:
    status = "PASS" if check["passed"] else "FAIL"
    lines.append(f"- `{status}` {check['name']}: `{check['value']}` ({check['threshold']})")
lines.extend([
    "",
    "## P0 English",
    f"- first score `{english_first['avg_score']}`, p50 `{english_first['p50_seconds']}s`, p95 `{english_first['p95_seconds']}s`",
    f"- final score `{english_final['avg_score']}`, p50 `{english_final['p50_seconds']}s`, p95 `{english_final['p95_seconds']}s`",
    "",
    "## P0 Hinglish",
    f"- first score `{hinglish_first['avg_score']}`, p50 `{hinglish_first['p50_seconds']}s`, p95 `{hinglish_first['p95_seconds']}s`",
    f"- final score `{hinglish_final['avg_score']}`, p50 `{hinglish_final['p50_seconds']}s`, p95 `{hinglish_final['p95_seconds']}s`",
    "",
    "## P1 Meeting",
    "- system audio smoke: see `p1_system_audio_smoke.log`",
    "- browser meeting smoke: see `p1_browser_meeting_smoke.log`",
    "- dual-source meeting smoke: see `p1_dual_source_meeting/dual_source_meeting.json`",
    f"- long dual-source meeting smoke: term recall `{long_recall_value}/{long_recall_total}` (threshold `>= {long_recall_minimum}`); see `p1_long_dual_source_meeting/dual_source_meeting.json`",
    f"- real web provider pass count: `{len(provider_passes)}/{len(provider_runs)}`; passes `{', '.join(name for name, _payload in provider_passes) or 'none'}`",
    f"- MiroTalk web provider diagnostic: `{'PASS' if web_provider.get('ok') else 'NOT RUN' if web_provider.get('error') == 'not_run' else 'FAIL'}`; see `p1_mirotalk_meeting/mirotalk_provider_smoke.json`",
    f"- VDO.Ninja web provider diagnostic: `{'PASS' if vdo_provider.get('ok') else 'NOT RUN' if vdo_provider.get('error') == 'not_run' else 'FAIL'}`; see `p1_vdo_ninja_meeting/vdo_ninja_provider_smoke.json`",
    f"- actual-provider live verifier: `{'PASS' if live_provider.get('ok') else 'NOT PROVEN'}`; see `live_provider_meeting.json`",
    "",
])
report_md_path.write_text("\n".join(lines), encoding="utf-8")
print(json.dumps(report, indent=2, ensure_ascii=False))
raise SystemExit(0 if report["ok"] else 1)
PY

echo "Validation report: $REPORT_MD"
