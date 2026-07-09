#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-$ROOT/dist/release/DictaHue.app/Contents/Resources/RambleFixRuntime}"

fail() {
  echo "public runtime local-only validation failed: $*" >&2
  exit 1
}

if [[ ! -d "$TARGET" ]]; then
  fail "runtime directory not found: $TARGET"
fi

RG=(
  rg -n --hidden
  --glob '!*.bin'
  --glob '!*.gguf'
  --glob '!*.npz'
  --glob '!*.safetensors'
  --glob '!*.zip'
  --glob '!*.wav'
  --glob '!*.mp3'
  --glob '!python/include/**'
  --glob '!python/share/**'
  --glob '!**/python/include/**'
  --glob '!**/python/share/**'
  --glob '!**/*.dist-info/**'
  --glob '!**/*.egg-info/**'
)

non_loopback_urls="$("${RG[@]}" 'https?://' "$TARGET" 2>/dev/null \
  | rg -v 'https?://(127\.0\.0\.1|localhost|\[?::1\]?)' \
  | rg -v 'http://\{(?:host|args\.host)\}' || true)"
if [[ -n "$non_loopback_urls" ]]; then
  printf '%s\n' "$non_loopback_urls" >&2
  fail "non-loopback URL found in packaged runtime"
fi

cloud_markers="$("${RG[@]}" 'OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|ELEVENLABS_API_KEY|SARVAM_API_KEY|api\.openai\.com|generativelanguage\.googleapis\.com|api\.elevenlabs\.io|api\.sarvam\.ai' "$TARGET" 2>/dev/null || true)"
if [[ -n "$cloud_markers" ]]; then
  printf '%s\n' "$cloud_markers" >&2
  fail "cloud API marker found in packaged runtime"
fi

personal_paths="$("${RG[@]}" '/Users/ssinha|Desktop/ludo-clips|TemporaryItems|WhatsApp/Data/tmp' "$TARGET" 2>/dev/null || true)"
if [[ -n "$personal_paths" ]]; then
  printf '%s\n' "$personal_paths" >&2
  fail "personal absolute path found in packaged runtime"
fi

echo "public runtime local-only validation passed"
