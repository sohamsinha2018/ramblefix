#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${RAMBLEFIX_SITE_SMOKE_PORT:-8765}"
OUT_DIR="${RAMBLEFIX_SITE_SMOKE_OUT:-$ROOT/output/playwright}"
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

python3 -m http.server "$PORT" --directory "$ROOT/site" >/tmp/ramblefix-site-smoke.log 2>&1 &
SERVER_PID="$!"

for _ in {1..50}; do
  if curl -fs "$URL" >/tmp/ramblefix-site-smoke.html 2>/dev/null; then
    break
  fi
  sleep 0.1
done

curl -fsS "$URL" >/tmp/ramblefix-site-smoke.html
curl -fsS "$URL/styles.css" >/dev/null
curl -fsS "$URL/analytics.js" >/tmp/ramblefix-site-analytics-smoke.js

for text in \
  "RambleFix" \
  "Launching August 21 · free · local · open source" \
  "Ramble freely." \
  "It gets it right." \
  "Fast, private dictation across your Mac." \
  "Star on GitHub" \
  "signed Apple silicon Mac builds" \
  "faster local engine in our tests" \
  "English meaning kept intact" \
  "Use your voice wherever you would normally type." \
  "Local by design." \
  "Same-WAV local benchmark" \
  "Statistically tied with Handy on English meaning" \
  "~89% <small>meaning kept</small>" \
  "Hindi+English n=13" \
  "Different models, different trade-offs" \
  "Other local ASR models" \
  "Read the public benchmark method" \
  "security-review.html" \
  "What should we make bilingual next?" \
  "English + Tagalog" \
  "View source"; do
  if ! grep -Fq "$text" /tmp/ramblefix-site-smoke.html; then
    echo "site visual smoke failed: missing text: $text" >&2
    exit 1
  fi
done

if ! grep -Fq "Download for Mac" /tmp/ramblefix-site-smoke.html && \
   ! grep -Fq "Star on GitHub" /tmp/ramblefix-site-smoke.html; then
  echo "site visual smoke failed: missing download CTA state" >&2
  exit 1
fi

for text in \
  'data-analytics-event="github star clicked"' \
  'data-analytics-event="language vote clicked"' \
  'src="./analytics.js?v='; do
  if ! grep -Fq "$text" /tmp/ramblefix-site-smoke.html; then
    echo "site visual smoke failed: missing analytics contract: $text" >&2
    exit 1
  fi
done

for text in \
  'navigator.doNotTrack === "1"' \
  '"/api/track"' \
  'data-analytics-event'; do
  if ! grep -Fq "$text" /tmp/ramblefix-site-analytics-smoke.js; then
    echo "site visual smoke failed: analytics client contract missing: $text" >&2
    exit 1
  fi
done

if grep -R -E -q 'phc_[A-Za-z0-9]+' "$ROOT/site" --exclude-dir=.vercel; then
  echo "site visual smoke failed: PostHog project token leaked into public site files" >&2
  exit 1
fi

for stale in \
  "all 45 real saved dictations" \
  "~90% meaning kept" \
  "~93%" \
  "English · 676 real dictations" \
  "beats every open-source" \
  "Tagalog’s likely next" \
  "beats Wispr Flow" \
  "Free forever" \
  "Works in any text box" \
  "Your voice never leaves your Mac"; do
  if grep -Fq "$stale" /tmp/ramblefix-site-smoke.html; then
    echo "site visual smoke failed: stale or unsupported claim present: $stale" >&2
    exit 1
  fi
done

curl -fsS "$URL/benchmark-method.html" >/tmp/ramblefix-site-method-smoke.html
for text in \
  "Benchmark method" \
  "Same audio, local engines, honest caveats." \
  "Gemini-cross-checked gold transcripts" \
  "confirm gold labels, not in the product path" \
  "676 saved WAVs" \
  "226 paired recordings" \
  "2.51× faster" \
  "not release-to-paste app latency" \
  "The Hindi+English comparison uses 13 clips"; do
  if ! grep -Fq "$text" /tmp/ramblefix-site-method-smoke.html; then
    echo "site visual smoke failed: missing method text: $text" >&2
    exit 1
  fi
done

curl -fsS "$URL/security-review.html" >/tmp/ramblefix-site-security-smoke.html
for text in \
  "Security review notes" \
  "Built to be easy to review before work use." \
  "No Screen Recording in V0." \
  "No cloud transcription in the shipped product path" \
  "anonymous page and CTA events only" \
  "Developer ID signed, notarized, stapled"; do
  if ! grep -Fq "$text" /tmp/ramblefix-site-security-smoke.html; then
    echo "site visual smoke failed: missing security text: $text" >&2
    exit 1
  fi
done

npx --yes playwright screenshot --browser=chromium --viewport-size=1440,1000 "$URL" "$OUT_DIR/ramblefix-site-desktop.png" >/dev/null
npx --yes playwright screenshot --browser=chromium --viewport-size=1024,768 "$URL" "$OUT_DIR/ramblefix-site-tablet.png" >/dev/null
npx --yes playwright screenshot --browser=chromium --viewport-size=390,844 "$URL" "$OUT_DIR/ramblefix-site-mobile.png" >/dev/null

for image in \
  "$OUT_DIR/ramblefix-site-desktop.png" \
  "$OUT_DIR/ramblefix-site-tablet.png" \
  "$OUT_DIR/ramblefix-site-mobile.png"; do
  if [[ ! -s "$image" ]]; then
    echo "site visual smoke failed: missing screenshot $image" >&2
    exit 1
  fi
done

echo "site visual smoke passed"
echo "$OUT_DIR/ramblefix-site-desktop.png"
echo "$OUT_DIR/ramblefix-site-tablet.png"
echo "$OUT_DIR/ramblefix-site-mobile.png"
