#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${DICTAHUE_SITE_SMOKE_PORT:-8765}"
OUT_DIR="${DICTAHUE_SITE_SMOKE_OUT:-$ROOT/output/playwright}"
URL="http://127.0.0.1:$PORT"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

mkdir -p "$OUT_DIR"

python3 -m http.server "$PORT" --directory "$ROOT/site" >/tmp/dictahue-site-smoke.log 2>&1 &
SERVER_PID="$!"

for _ in {1..50}; do
  if curl -fs "$URL" >/tmp/dictahue-site-smoke.html 2>/dev/null; then
    break
  fi
  sleep 0.1
done

curl -fsS "$URL" >/tmp/dictahue-site-smoke.html
curl -fsS "$URL/styles.css" >/dev/null

for text in \
  "DictaHue" \
  "No signup. No cloud product path. No screen recording permission in V0." \
  "Same-WAV local benchmark" \
  "Expanded real-use check" \
  "0.872 useful" \
  "0.906 useful" \
  "0 unsafe" \
  "Long English is not a launch claim yet." \
  "Star on GitHub"; do
  if ! grep -Fq "$text" /tmp/dictahue-site-smoke.html; then
    echo "site visual smoke failed: missing text: $text" >&2
    exit 1
  fi
done

if ! grep -Fq "Download for Mac" /tmp/dictahue-site-smoke.html && \
   ! grep -Fq "Download after signed build" /tmp/dictahue-site-smoke.html; then
  echo "site visual smoke failed: missing download CTA state" >&2
  exit 1
fi

npx --yes playwright screenshot --browser=chromium --viewport-size=1440,1000 "$URL" "$OUT_DIR/dictahue-site-desktop.png" >/dev/null
npx --yes playwright screenshot --browser=chromium --viewport-size=1024,768 "$URL" "$OUT_DIR/dictahue-site-tablet.png" >/dev/null
npx --yes playwright screenshot --browser=chromium --viewport-size=390,844 "$URL" "$OUT_DIR/dictahue-site-mobile.png" >/dev/null

for image in \
  "$OUT_DIR/dictahue-site-desktop.png" \
  "$OUT_DIR/dictahue-site-tablet.png" \
  "$OUT_DIR/dictahue-site-mobile.png"; do
  if [[ ! -s "$image" ]]; then
    echo "site visual smoke failed: missing screenshot $image" >&2
    exit 1
  fi
done

echo "site visual smoke passed"
echo "$OUT_DIR/dictahue-site-desktop.png"
echo "$OUT_DIR/dictahue-site-tablet.png"
echo "$OUT_DIR/dictahue-site-mobile.png"
