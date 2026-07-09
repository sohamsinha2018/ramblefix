#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT/logs/system_audio_smoke"
ATTEMPTS="${RAMBLEFIX_SYSTEM_AUDIO_SMOKE_ATTEMPTS:-2}"
SOURCE_AIFF="$OUT_DIR/source.aiff"
CAPTURE_WAV="$OUT_DIR/capture.wav"
CAPTURE_JSON="$OUT_DIR/capture_result.json"
TRANSCRIPT_DIR="$OUT_DIR/transcript"
TRANSCRIPT_JSON="$OUT_DIR/transcript.json"

mkdir -p "$OUT_DIR"
rm -rf "$TRANSCRIPT_DIR"
mkdir -p "$TRANSCRIPT_DIR"

swift build -c release --package-path "$ROOT/native/RambleFixHotkey" --product RambleFixSystemAudioSmokeTool >/dev/null
say -o "$SOURCE_AIFF" "RambleFix system audio smoke test. Provider independent meeting capture works locally."

for attempt in $(seq 1 "$ATTEMPTS"); do
  rm -f "$CAPTURE_WAV" "$CAPTURE_JSON"
  "$ROOT/native/RambleFixHotkey/.build/release/RambleFixSystemAudioSmokeTool" \
    --seconds 10 \
    --output "$CAPTURE_WAV" > "$CAPTURE_JSON" &
  capture_pid=$!

  sleep 2
  afplay "$SOURCE_AIFF" >/dev/null 2>&1 || true
  afplay "$SOURCE_AIFF" >/dev/null 2>&1 || true
  wait "$capture_pid"

  if "$ROOT/.venv/bin/python" - "$CAPTURE_WAV" "$attempt" <<'PY'
import json
import sys
from pathlib import Path

from ramblefix.quality import wav_silence_metrics

audio = Path(sys.argv[1])
attempt = int(sys.argv[2])
metrics = wav_silence_metrics(audio)
print(json.dumps({"attempt": attempt, "capture_metrics": metrics}, ensure_ascii=False, indent=2))
if not audio.exists():
    raise SystemExit("system audio capture WAV missing")
if metrics.get("audio_probably_silent"):
    raise SystemExit("system audio capture is probably silent")
if float(metrics.get("audio_duration_seconds") or 0) < 3:
    raise SystemExit("system audio capture too short")
PY
  then
    break
  fi
  if [[ "$attempt" == "$ATTEMPTS" ]]; then
    exit 1
  fi
  sleep 1
done

"$ROOT/.venv/bin/python" -m ramblefix.cli meeting-transcribe-audio "$CAPTURE_WAV" \
  --json \
  --output-dir "$TRANSCRIPT_DIR" \
  --chunk-seconds 5 \
  --mode fast > "$TRANSCRIPT_JSON"

"$ROOT/.venv/bin/python" - "$CAPTURE_JSON" "$TRANSCRIPT_JSON" <<'PY'
import json
import sys
from pathlib import Path

capture = json.loads(Path(sys.argv[1]).read_text())
transcript = json.loads(Path(sys.argv[2]).read_text())
text = (transcript.get("text") or "").strip()
print(json.dumps({
    "capture_ok": capture.get("ok"),
    "capture_audio": capture.get("audio_path"),
    "capture_duration_seconds": capture.get("duration_seconds"),
    "transcript_seconds": transcript.get("seconds"),
    "transcript_preview": text[:240],
}, ensure_ascii=False, indent=2))
if not text:
    raise SystemExit("system audio transcript is empty")
PY
