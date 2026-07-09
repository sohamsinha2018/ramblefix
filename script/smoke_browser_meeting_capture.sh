#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${RAMBLEFIX_BROWSER_MEETING_SMOKE_OUT:-$ROOT/logs/browser_meeting_smoke}"
if [[ "$OUT_DIR" != /* ]]; then
  OUT_DIR="$ROOT/$OUT_DIR"
fi
ATTEMPTS="${RAMBLEFIX_BROWSER_MEETING_SMOKE_ATTEMPTS:-2}"
SOURCE_AIFF="$OUT_DIR/source.aiff"
SOURCE_WAV="$OUT_DIR/source.wav"
HTML="$OUT_DIR/player.html"
CAPTURE_WAV="$OUT_DIR/capture.wav"
CAPTURE_JSON="$OUT_DIR/capture_result.json"
TRANSCRIPT_DIR="$OUT_DIR/transcript"
TRANSCRIPT_JSON="$OUT_DIR/transcript.json"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_PROFILE="$OUT_DIR/chrome-profile"

if [[ "${RAMBLEFIX_BROWSER_MEETING_SINGLE_ATTEMPT:-0}" != "1" && "$ATTEMPTS" -gt 1 ]]; then
  mkdir -p "$OUT_DIR"
  last_attempt_dir=""
  for attempt in $(seq 1 "$ATTEMPTS"); do
    attempt_dir="$OUT_DIR/attempt-$attempt"
    last_attempt_dir="$attempt_dir"
    rm -rf "$attempt_dir"
    echo "browser meeting smoke attempt $attempt/$ATTEMPTS"
    if RAMBLEFIX_BROWSER_MEETING_SINGLE_ATTEMPT=1 RAMBLEFIX_BROWSER_MEETING_SMOKE_OUT="$attempt_dir" "$0"; then
      echo "browser meeting smoke passed on attempt $attempt/$ATTEMPTS"
      exit 0
    fi
    echo "browser meeting smoke attempt $attempt failed" >&2
  done
  echo "browser meeting smoke failed after $ATTEMPTS attempts; last attempt: $last_attempt_dir" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
rm -rf "$TRANSCRIPT_DIR" "$CHROME_PROFILE"
mkdir -p "$TRANSCRIPT_DIR" "$CHROME_PROFILE"

if [[ ! -x "$CHROME" ]]; then
  echo "Google Chrome not found at $CHROME" >&2
  exit 2
fi

swift build -c release --package-path "$ROOT/native/RambleFixHotkey" --product RambleFixSystemAudioSmokeTool >/dev/null
say -o "$SOURCE_AIFF" "RambleFix browser meeting smoke test. Chrome audio capture works locally across provider style web calls. This repeats the provider independent browser meeting capture proof with enough duration for a stable smoke test. Browser capture proof repeats near the end. Browser meeting capture works locally."
afconvert -f WAVE -d LEI16@16000 "$SOURCE_AIFF" "$SOURCE_WAV"

cat > "$HTML" <<HTML
<!doctype html>
<meta charset="utf-8">
<title>RambleFix Browser Meeting Smoke</title>
<body>
  <audio id="meeting-audio" src="source.wav" autoplay controls></audio>
  <script>
    const audio = document.getElementById("meeting-audio");
    audio.volume = 1.0;
    window.addEventListener("load", () => {
      setTimeout(() => audio.play().catch(() => {}), 3000);
    });
  </script>
</body>
HTML

"$ROOT/native/RambleFixHotkey/.build/release/RambleFixSystemAudioSmokeTool" \
  --seconds 20 \
  --output "$CAPTURE_WAV" > "$CAPTURE_JSON" &
capture_pid=$!

sleep 1
"$CHROME" \
  --user-data-dir="$CHROME_PROFILE" \
  --no-sandbox \
  --no-first-run \
  --no-default-browser-check \
  --use-fake-ui-for-media-stream \
  --autoplay-policy=no-user-gesture-required \
  "file://$HTML" >/dev/null 2>&1 &
chrome_pid=$!

cleanup() {
  kill "$chrome_pid" >/dev/null 2>&1 || true
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
    raise SystemExit("browser capture WAV missing")
if metrics.get("audio_probably_silent"):
    raise SystemExit("browser capture is probably silent")
if float(metrics.get("audio_duration_seconds") or 0) < 6:
    raise SystemExit("browser capture too short")
if float(metrics.get("audio_rms_max") or 0) < 0.01:
    raise SystemExit("browser capture level too low")
PY

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
    raise SystemExit("browser meeting transcript is empty")
lower = text.lower()
if "browser" not in lower or "capture" not in lower:
    raise SystemExit("browser meeting transcript missed expected words")
PY
