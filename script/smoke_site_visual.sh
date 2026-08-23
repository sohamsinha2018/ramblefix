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
curl -fsS "$URL/app-icon.png" >/dev/null
curl -fsS "$URL/analytics.js" >/tmp/ramblefix-site-analytics-smoke.js

LITE_DMG_URL="https://pub-98d9f6faa545400b9f2dd67be1585b33.r2.dev/RambleFix-Lite-0.1.0.dmg"
HI_DMG_URL="https://pub-98d9f6faa545400b9f2dd67be1585b33.r2.dev/RambleFix-HI-0.1.0.dmg"

grep -q './app-icon.png' /tmp/ramblefix-site-smoke.html
curl -fsS "$URL/styles.css" | grep -q './app-icon.png'

for text in \
  "RambleFix" \
  "Free · local · open source" \
  "Ramble. Release." \
  "Text lands." \
  "Hold the key, say what you need, release." \
  "Use Hindi + English when your thoughts mix languages" \
  "Download RambleFix English version" \
  "Download — English" \
  "Download — English + Hindi" \
  "$LITE_DMG_URL" \
  "$HI_DMG_URL" \
  "GitHub source" \
  "View source code" \
  "Fork it, build on top, or open a PR for the next language route." \
  "Apple Silicon Mac (M1+), macOS 13+" \
  "DMG install flow" \
  "2.6×" \
  "3-4× speed" \
  "for AI prompts by voice" \
  "faster local engine in our tests" \
  "English meaning kept intact" \
  "Use voice to give AI fuller instructions" \
  "Use your voice wherever you would normally type." \
  "Fn" \
  "Ctrl" \
  "Your voice stays on your Mac." \
  "Your voice and transcribed text never leave your Mac. Optional anonymous usage stats (counts & timings only) are on to help improve the app — turn them off anytime." \
  "Same-WAV local benchmark" \
  "Statistically tied with Handy on English meaning" \
  "7.2× faster in a same-audio engine test" \
  "~89% <small>meaning kept</small>" \
  "Hindi+English n=13" \
  "Whisper-based local tools" \
  "Other local ASR models" \
  "Read the public benchmark method" \
  "Built with other builders." \
  "The hard problem was not Hindi alone" \
  "keeping English fast and accurate while" \
  "adding strong Hindi + English" \
  "We crowdsourced routes through" \
  "improved verified outcomes" \
  "Amit Kumar" \
  "Sponsored the local Hindi + English challenge" \
  "Sponsor profile ↗" \
  "Arnav Chauhan" \
  "Sankeerth" \
  "Darshan" \
  "Vishwas" \
  "View Builderr profile ↗" \
  "Darshan profile ↗" \
  "Vishwas profile ↗" \
  "Profiles include their GitHub, LinkedIn, and submitted work where shared." \
  "View the Builderr challenge and final results" \
  "https://www.builderr.ai/builders/arnav" \
  "https://www.builderr.ai/builders/sankeerth" \
  "https://www.builderr.ai/builders/darshan" \
  "https://www.builderr.ai/builders/vishwas" \
  "https://in.linkedin.com/in/urbansanyasi" \
  "https://builderr.ai/speech-to-text" \
  "security-review.html" \
  "What should we make bilingual next?" \
  "Builders who want to help can fork the repo, add corpus/model/eval support, and open a PR." \
  "English + Tagalog" \
  "Tap once to vote anonymously" \
  "aria-pressed=\"false\"" \
  "Vote noted: <strong>" \
  "contribute a language route on GitHub" \
  "fork it or contribute on GitHub" \
  "Contribute a language" \
  "Download"; do
  if ! grep -Fq "$text" /tmp/ramblefix-site-smoke.html; then
    echo "site visual smoke failed: missing text: $text" >&2
    exit 1
  fi
done

hero_html="$(sed -n '/<section class="hero"/,/<section class="value-band"/p' /tmp/ramblefix-site-smoke.html)"
for competitor in "Handy" "OpenWhispr" "whisper.cpp"; do
  if grep -Fq "$competitor" <<<"$hero_html"; then
    echo "site visual smoke failed: competitor named in hero: $competitor" >&2
    exit 1
  fi
done

if grep -Fq "Star on GitHub" <<<"$hero_html"; then
  echo "site visual smoke failed: hero should invite source review before asking for stars" >&2
  exit 1
fi

poll_html="$(sed -n '/<div class="language-poll"/,/<\/div>/p' /tmp/ramblefix-site-smoke.html)"
if grep -Fq 'href=' <<<"$poll_html"; then
  echo "site visual smoke failed: language poll should vote in-page, not navigate away" >&2
  exit 1
fi
for vote in tagalog mandarin spanish other; do
  if ! grep -Fq "data-vote=\"$vote\"" <<<"$poll_html"; then
    echo "site visual smoke failed: missing language vote button: $vote" >&2
    exit 1
  fi
done

if ! grep -Fq "Download — English" /tmp/ramblefix-site-smoke.html; then
  echo "site visual smoke failed: missing download CTA state" >&2
  exit 1
fi

if grep -Fq 'href="https://github.com/sohamsinha2018/ramblefix/releases"' /tmp/ramblefix-site-smoke.html; then
  echo "site visual smoke failed: bare GitHub releases page linked from public site" >&2
  exit 1
fi

for contract in \
  'styles.css?v=20260823e' \
  'class="nav-action nav-source"' \
  'data-analytics-target="nav_source"' \
  'data-analytics-target="nav_lite"' \
  'aria-label="Download RambleFix English version"'; do
  if ! grep -Fq "$contract" /tmp/ramblefix-site-smoke.html; then
    echo "site visual smoke failed: missing header/download contract: $contract" >&2
    exit 1
  fi
done

curl -fsSIL --max-time 15 "$LITE_DMG_URL" >/tmp/ramblefix-lite-dmg-headers
curl -fsSIL --max-time 15 "$HI_DMG_URL" >/tmp/ramblefix-hi-dmg-headers
grep -q "200" /tmp/ramblefix-lite-dmg-headers
grep -q "200" /tmp/ramblefix-hi-dmg-headers

for text in \
  'data-analytics-event="download requested"' \
  'data-analytics-event="site cta clicked"' \
  'data-analytics-event="language vote clicked"' \
  'data-analytics-target="hero_source"' \
  'data-analytics-target="footer_source"' \
  'data-analytics-target="language_contribution"' \
  'data-analytics-event="builder profile clicked"' \
  'data-analytics-event="builderr challenge clicked"' \
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

for text in \
  '"builderr clicked"' \
  '"builderr challenge clicked"' \
  '"builder profile clicked"'; do
  if ! grep -Fq "$text" "$ROOT/site/api/track.js"; then
    echo "site visual smoke failed: analytics API does not allow event: $text" >&2
    exit 1
  fi
done

if grep -R -E -q 'phc_[A-Za-z0-9]+' "$ROOT/site" --exclude-dir=.vercel; then
  echo "site visual smoke failed: PostHog project token leaked into public site files" >&2
  exit 1
fi

for stale in \
  '$500 Builderr challenge' \
  "Ten builders took it on" \
  "all 45 real saved dictations" \
  "~90% meaning kept" \
  "~93%" \
  "English · 676 real dictations" \
  "beats every open-source" \
  "Tagalog’s likely next" \
  "Open your choice and tap" \
  "discussioncomment-" \
  "Signed Apple silicon builds are ready now" \
  "Download opens the canonical GitHub Releases page" \
  "Download for Mac" \
  "Star on GitHub" \
  "beats Wispr Flow" \
  "Free forever" \
  "Works in any text box" \
  "Your voice never leaves your Mac"; do
  if grep -Fq "$stale" /tmp/ramblefix-site-smoke.html; then
    echo "site visual smoke failed: stale or unsupported claim present: $stale" >&2
    exit 1
  fi
done


for misleading in \
  "before you stop talking" \
  "Speech → text in ~150ms" \
  "under 1 second" \
  "fastest local dictation" \
  "increase your AI productivity by 3x" \
  "makes you 3x more productive"; do
  if grep -Fiq "$misleading" /tmp/ramblefix-site-smoke.html; then
    echo "site visual smoke failed: misleading speed claim present: $misleading" >&2
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
  "2.68× measured and reported conservatively as 2.6×" \
  "40 saved English clips" \
  "7.2× faster" \
  "Wispr Flow cites 45 wpm keyboard vs 220 wpm Flow" \
  "Willow cites 150 wpm speech vs 40 wpm typing" \
  "not as a RambleFix-specific productivity guarantee" \
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
  "Your voice and transcribed text never leave your Mac. Optional anonymous usage stats (counts & timings only)" \
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
npx --yes playwright screenshot --browser=chromium --viewport-size=2940,1536 "$URL" "$OUT_DIR/ramblefix-site-wide.png" >/dev/null
npx --yes playwright screenshot --browser=chromium --viewport-size=1024,768 "$URL" "$OUT_DIR/ramblefix-site-tablet.png" >/dev/null
npx --yes playwright screenshot --browser=chromium --viewport-size=390,844 "$URL" "$OUT_DIR/ramblefix-site-mobile.png" >/dev/null

for image in \
  "$OUT_DIR/ramblefix-site-desktop.png" \
  "$OUT_DIR/ramblefix-site-wide.png" \
  "$OUT_DIR/ramblefix-site-tablet.png" \
  "$OUT_DIR/ramblefix-site-mobile.png"; do
  if [[ ! -s "$image" ]]; then
    echo "site visual smoke failed: missing screenshot $image" >&2
    exit 1
  fi
done

echo "site visual smoke passed"
echo "$OUT_DIR/ramblefix-site-desktop.png"
echo "$OUT_DIR/ramblefix-site-wide.png"
echo "$OUT_DIR/ramblefix-site-tablet.png"
echo "$OUT_DIR/ramblefix-site-mobile.png"
