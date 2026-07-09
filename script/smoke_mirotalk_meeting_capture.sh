#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${RAMBLEFIX_MIROTALK_SMOKE_OUT:-$ROOT/logs/mirotalk_meeting_smoke}"
ATTEMPTS="${RAMBLEFIX_MIROTALK_SMOKE_ATTEMPTS:-2}"
ROOM="ramblefix$(date +%Y%m%d%H%M%S)$RANDOM"
SOURCE_AIFF="$OUT_DIR/source.aiff"
SOURCE_WAV="$OUT_DIR/source.wav"
CAPTURE_WAV="$OUT_DIR/capture.wav"
CAPTURE_JSON="$OUT_DIR/capture_result.json"
TRANSCRIPT_DIR="$OUT_DIR/transcript"
TRANSCRIPT_JSON="$OUT_DIR/transcript.json"
SPEAKER_PROFILE="$OUT_DIR/chrome-speaker-profile"
LISTENER_PROFILE="$OUT_DIR/chrome-listener-profile"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

if [[ "${RAMBLEFIX_MIROTALK_SINGLE_ATTEMPT:-0}" != "1" && "$ATTEMPTS" -gt 1 ]]; then
  mkdir -p "$OUT_DIR"
  last_attempt_dir=""
  for attempt in $(seq 1 "$ATTEMPTS"); do
    attempt_dir="$OUT_DIR/attempt-$attempt"
    last_attempt_dir="$attempt_dir"
    rm -rf "$attempt_dir"
    echo "MiroTalk provider smoke attempt $attempt/$ATTEMPTS"
    if RAMBLEFIX_MIROTALK_SINGLE_ATTEMPT=1 RAMBLEFIX_MIROTALK_SMOKE_OUT="$attempt_dir" "$0"; then
      cp "$attempt_dir/mirotalk_provider_smoke.json" "$OUT_DIR/mirotalk_provider_smoke.json"
      echo "MiroTalk provider smoke passed on attempt $attempt/$ATTEMPTS"
      exit 0
    fi
    echo "MiroTalk provider smoke attempt $attempt failed" >&2
  done
  if [[ -n "$last_attempt_dir" && -f "$last_attempt_dir/mirotalk_provider_smoke.json" ]]; then
    cp "$last_attempt_dir/mirotalk_provider_smoke.json" "$OUT_DIR/mirotalk_provider_smoke.json"
  fi
  echo "MiroTalk provider smoke failed after $ATTEMPTS attempts" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
rm -rf "$TRANSCRIPT_DIR" "$SPEAKER_PROFILE" "$LISTENER_PROFILE"
mkdir -p "$TRANSCRIPT_DIR" "$SPEAKER_PROFILE" "$LISTENER_PROFILE"

if [[ ! -x "$CHROME" ]]; then
  echo "Google Chrome not found at $CHROME" >&2
  exit 2
fi

swift build -c release --package-path "$ROOT/native/RambleFixHotkey" --product RambleFixSystemAudioSmokeTool >/dev/null

say -o "$SOURCE_AIFF" "RambleFix MiroTalk provider smoke. The remote meeting speaker says SOC two evidence, Kubernetes migration, Hindi support, and Friday action item. RambleFix MiroTalk provider smoke. The remote meeting speaker says SOC two evidence, Kubernetes migration, Hindi support, and Friday action item. RambleFix MiroTalk provider smoke. The remote meeting speaker says SOC two evidence, Kubernetes migration, Hindi support, and Friday action item. RambleFix MiroTalk provider smoke. The remote meeting speaker says SOC two evidence, Kubernetes migration, Hindi support, and Friday action item."
afconvert -f WAVE -d LEI16@48000 "$SOURCE_AIFF" "$SOURCE_WAV"

url_for() {
  local name="$1"
  printf 'https://p2p.mirotalk.com/join?room=%s&name=%s&audio=1&video=0&screen=0&chat=0&hide=0&notify=0&duration=unlimited' "$ROOM" "$name"
}

"$ROOT/native/RambleFixHotkey/.build/release/RambleFixSystemAudioSmokeTool" \
  --seconds 65 \
  --output "$CAPTURE_WAV" > "$CAPTURE_JSON" &
capture_pid=$!

sleep 2
"$CHROME" \
  --user-data-dir="$LISTENER_PROFILE" \
  --no-sandbox \
  --no-first-run \
  --no-default-browser-check \
  --use-fake-ui-for-media-stream \
  --use-fake-device-for-media-stream \
  --autoplay-policy=no-user-gesture-required \
  "$(url_for listener)" > "$OUT_DIR/listener.log" 2>&1 &
listener_pid=$!

sleep 8
"$CHROME" \
  --user-data-dir="$SPEAKER_PROFILE" \
  --no-sandbox \
  --no-first-run \
  --no-default-browser-check \
  --use-fake-ui-for-media-stream \
  --use-fake-device-for-media-stream \
  --use-file-for-fake-audio-capture="$SOURCE_WAV" \
  --autoplay-policy=no-user-gesture-required \
  "$(url_for speaker)" > "$OUT_DIR/speaker.log" 2>&1 &
speaker_pid=$!

cleanup() {
  kill "$speaker_pid" >/dev/null 2>&1 || true
  kill "$listener_pid" >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait "$capture_pid"

"$ROOT/.venv/bin/python" - "$CAPTURE_WAV" <<'PY'
import json
import sys
from pathlib import Path

from ramblefix.quality import wav_silence_metrics

audio = Path(sys.argv[1])
metrics = wav_silence_metrics(audio)
print(json.dumps({"capture_metrics": metrics}, ensure_ascii=False, indent=2))
if not audio.exists():
    raise SystemExit("MiroTalk capture WAV missing")
if metrics.get("audio_probably_silent"):
    raise SystemExit("MiroTalk capture is probably silent")
if float(metrics.get("audio_duration_seconds") or 0) < 30:
    raise SystemExit("MiroTalk capture too short")
if float(metrics.get("audio_rms_max") or 0) < 0.02:
    raise SystemExit("MiroTalk capture level too low")
PY

"$ROOT/.venv/bin/python" -m ramblefix.cli meeting-transcribe-audio "$CAPTURE_WAV" \
  --json \
  --output-dir "$TRANSCRIPT_DIR" \
  --chunk-seconds 10 \
  --mode fast > "$TRANSCRIPT_JSON"

"$ROOT/.venv/bin/python" - "$CAPTURE_JSON" "$TRANSCRIPT_JSON" "$ROOM" <<'PY'
import json
import sys
from pathlib import Path

capture = json.loads(Path(sys.argv[1]).read_text())
transcript = json.loads(Path(sys.argv[2]).read_text())
room = sys.argv[3]
text = (transcript.get("text") or "").strip()
lower = text.lower()
checks = [
    ("RambleFix", ["ramblefix", "ramble fix", "rumble fix"]),
    ("MiroTalk", ["mirotalk", "miro talk", "mirror talk", "hero talk"]),
    ("provider", ["provider"]),
    ("SOC2", ["soc2", "soc 2", "soc two"]),
    ("Kubernetes", ["kubernetes"]),
    ("Hindi support", ["hindi support"]),
    ("Friday", ["friday"]),
]
check_rows = [
    {"name": name, "passed": any(value in lower for value in values), "expected_any": values}
    for name, values in checks
]
payload = {
    "ok": bool(text) and all(row["passed"] for row in check_rows),
    "provider": "p2p.mirotalk.com",
    "room": room,
    "capture_ok": capture.get("ok"),
    "capture_audio": capture.get("audio_path"),
    "capture_duration_seconds": capture.get("duration_seconds"),
    "transcript_seconds": transcript.get("seconds"),
    "transcript_preview": text[:500],
    "checks": check_rows,
}
(Path(sys.argv[2]).parent / "mirotalk_provider_smoke.json").write_text(
    json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
print(json.dumps(payload, ensure_ascii=False, indent=2))
raise SystemExit(0 if payload["ok"] else 1)
PY
