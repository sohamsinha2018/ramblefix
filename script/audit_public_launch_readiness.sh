#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUBLIC=0
ALLOW_PLACEHOLDERS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --public)
      PUBLIC=1
      shift
      ;;
    --allow-placeholders)
      ALLOW_PLACEHOLDERS=1
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

fail() {
  echo "public launch readiness failed: $*" >&2
  exit 1
}

warn() {
  echo "warning: $*" >&2
}

SITE="$ROOT/site/index.html"
STYLES="$ROOT/site/styles.css"
APP="$ROOT/dist/release/DictaHue.app"
DMG="$ROOT/dist/release/DictaHue-0.1.0.dmg"
ZIP="$ROOT/dist/release/DictaHue-0.1.0.zip"
SUMS="$ROOT/dist/release/DictaHue-0.1.0.SHA256SUMS"
READOUT="$ROOT/docs/current_regression_readout_20260709.md"

[[ -f "$SITE" ]] || fail "site/index.html missing"
[[ -f "$STYLES" ]] || fail "site/styles.css missing"
[[ -f "$READOUT" ]] || fail "current regression readout missing"
"$ROOT/scripts/audit_benchmark_claims.py"
"$ROOT/script/audit_public_source_surface.sh"
if [[ "$PUBLIC" == "1" ]]; then
  "$ROOT/script/audit_eval_machine_health.sh" --strict
else
  "$ROOT/script/audit_eval_machine_health.sh" --warn-only || true
fi

placeholder_hits="$(rg -n 'href="#"|placeholder|Replace these placeholders' "$SITE" || true)"
if [[ -n "$placeholder_hits" ]]; then
  if [[ "$ALLOW_PLACEHOLDERS" == "1" && "$PUBLIC" != "1" ]]; then
    warn "site still contains launch placeholders"
  else
    printf '%s\n' "$placeholder_hits" >&2
    fail "replace site download/GitHub/community placeholder links before public launch"
  fi
fi

for text in "No signup" "No cloud product path" "No screen recording permission" "Same-WAV local benchmark"; do
  rg -q "$text" "$SITE" || fail "site missing required launch message: $text"
done

[[ -d "$APP" ]] || fail "release app missing: $APP"
[[ -f "$DMG" ]] || fail "release DMG missing: $DMG"
[[ -f "$ZIP" ]] || fail "release ZIP missing: $ZIP"
[[ -f "$SUMS" ]] || fail "release checksum manifest missing: $SUMS"
"$ROOT/script/audit_release_checksums.sh" "$SUMS"
"$ROOT/script/audit_macos_release_artifact.sh" "$APP"
security_args=()
if [[ "$PUBLIC" == "1" ]]; then
  security_args+=(--public)
else
  security_args+=(--local)
fi
"$ROOT/script/audit_release_security.sh" "$APP" "${security_args[@]}"

SIGNING="$(codesign -dvvv "$APP" 2>&1 || true)"
if [[ "$PUBLIC" == "1" ]]; then
  [[ "$SIGNING" == *"Authority=Developer ID Application:"* ]] || fail "public app is not Developer ID signed"
  spctl -a -vv "$APP" >/dev/null 2>&1 || fail "Gatekeeper rejected app"
  xcrun stapler validate "$DMG" >/dev/null 2>&1 || fail "DMG is not stapled/notarized"
else
  if [[ "$SIGNING" == *"Signature=adhoc"* ]]; then
    warn "release app is ad-hoc signed; OK for local smoke only"
  fi
fi

secret_hits="$(rg -n --hidden \
  --glob '!dist/**' \
  --glob '!models/**' \
  --glob '!logs/**' \
  --glob '!eval_runs/**' \
  --glob '!recordings/**' \
  --glob '!eval_corpus/**' \
  --glob '!native/RambleFixHotkey/.build/**' \
  --glob '!*.bin' \
  --glob '!*.wav' \
  --glob '!*.dmg' \
  --glob '!*.zip' \
  'sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,}' \
  "$ROOT" || true)"
if [[ -n "$secret_hits" ]]; then
  printf '%s\n' "$secret_hits" >&2
  fail "possible secret found in public source surface"
fi

echo "public launch readiness audit passed"
