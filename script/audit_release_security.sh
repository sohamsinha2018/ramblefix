#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$ROOT/dist/release/DictaHue.app}"
PUBLIC=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --public)
      PUBLIC=1
      shift
      ;;
    --local)
      PUBLIC=0
      shift
      ;;
    *.app)
      APP="$1"
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

fail() {
  echo "release security audit failed: $*" >&2
  exit 1
}

warn() {
  echo "warning: $*" >&2
}

[[ -d "$APP" ]] || fail "app bundle not found: $APP"
PLIST="$APP/Contents/Info.plist"
[[ -f "$PLIST" ]] || fail "Info.plist not found: $PLIST"
plutil -lint "$PLIST" >/dev/null

EXECUTABLE="$(/usr/libexec/PlistBuddy -c "Print CFBundleExecutable" "$PLIST")"
BINARY="$APP/Contents/MacOS/$EXECUTABLE"
[[ -x "$BINARY" ]] || fail "main executable missing or not executable: $BINARY"

required_permissions=(
  NSMicrophoneUsageDescription
  NSInputMonitoringUsageDescription
  NSAppleEventsUsageDescription
)
for key in "${required_permissions[@]}"; do
  value="$(/usr/libexec/PlistBuddy -c "Print $key" "$PLIST" 2>/dev/null || true)"
  [[ -n "$value" ]] || fail "missing required permission string: $key"
done

for key in NSScreenCaptureUsageDescription NSCameraUsageDescription NSLocationUsageDescription NSCalendarsUsageDescription NSContactsUsageDescription; do
  if /usr/libexec/PlistBuddy -c "Print $key" "$PLIST" >/dev/null 2>&1; then
    fail "unexpected V0 permission string: $key"
  fi
done

if otool -L "$BINARY" | grep -q 'ScreenCaptureKit.framework'; then
  fail "V0 binary links ScreenCaptureKit"
fi

codesign --verify --deep --strict --verbose=2 "$APP" >/dev/null
SIGNING="$(codesign -dvvv --entitlements :- "$APP" 2>&1 || true)"
[[ "$SIGNING" == *"runtime"* ]] || fail "hardened runtime flag missing"
if [[ "$SIGNING" == *"Signature=adhoc"* ]]; then
  if [[ "$PUBLIC" == "1" ]]; then
    printf '%s\n' "$SIGNING" >&2
    fail "public release cannot be ad-hoc signed"
  fi
  warn "ad-hoc signed; OK for local testing only"
else
  [[ "$SIGNING" == *"Authority=Developer ID Application:"* ]] || fail "not signed with Developer ID Application"
fi

SPCTL_OUTPUT="$(spctl -a -vv "$APP" 2>&1 || true)"
if [[ "$PUBLIC" == "1" ]]; then
  [[ "$SPCTL_OUTPUT" != *"rejected"* ]] || {
    printf '%s\n' "$SPCTL_OUTPUT" >&2
    fail "Gatekeeper rejected app"
  }
else
  if [[ "$SPCTL_OUTPUT" == *"rejected"* ]]; then
    warn "Gatekeeper rejects local/ad-hoc build; expected until Developer ID + notarization"
  fi
fi

RUNTIME="$APP/Contents/Resources/RambleFixRuntime"
if [[ -d "$RUNTIME" ]]; then
  "$ROOT/script/validate_public_runtime_local_only.sh" "$RUNTIME"
  [[ -x "$RUNTIME/script/start_srota_server.sh" ]] || fail "packaged runtime missing executable script/start_srota_server.sh"
  if [[ "$PUBLIC" == "1" ]]; then
    [[ -x "$RUNTIME/.venv/bin/python" ]] || fail "public packaged runtime missing executable .venv/bin/python"
    [[ -f "$RUNTIME/requirements-runtime.txt" ]] || fail "public packaged runtime missing requirements-runtime.txt"
  fi
  external_symlink="$(
    find "$RUNTIME" -type l -print0 \
      | while IFS= read -r -d '' link; do
          target="$(readlink "$link")"
          if [[ "$target" == /* ]]; then
            printf '%s -> %s\n' "$link" "$target"
          fi
        done \
      | head -1
  )"
  if [[ -n "$external_symlink" ]]; then
    printf '%s\n' "$external_symlink" >&2
    fail "packaged runtime contains absolute symlink"
  fi
  if find "$RUNTIME" \( -name '*.pyc' -o -name '__pycache__' \) -print -quit | grep -q .; then
    fail "packaged runtime contains Python bytecode/cache files"
  fi
  RUNTIME="$RUNTIME" python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

runtime = Path(os.environ["RUNTIME"])


def fail(message: str) -> None:
    print(f"release security audit failed: {message}", file=sys.stderr)
    raise SystemExit(1)


memory_path = runtime / "config" / "memory_terms.json"
phrase_path = runtime / "config" / "phrase_fixes.json"
for path in (memory_path, phrase_path):
    if not path.exists():
        fail(f"packaged public config missing: {path.relative_to(runtime)}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"invalid packaged public config JSON: {path.relative_to(runtime)}: {exc}")
    if path == memory_path:
        if payload.get("terms") != []:
            fail("packaged memory_terms.json must be empty for public release")
    if path == phrase_path:
        if payload.get("phrase_fixes") != []:
            fail("packaged phrase_fixes.json must be empty for public release")
PY
fi

binary_markers="$(
  /usr/bin/strings -a "$BINARY" \
    | rg -n 'https?://|api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|api\.elevenlabs\.io|api\.sarvam\.ai|OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|ELEVENLABS_API_KEY|SARVAM_API_KEY|sk-[A-Za-z0-9_-]{20,}|sk_[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,}' \
    | rg -v 'https?://(127\.0\.0\.1|localhost|\[?::1\]?)' \
    || true
)"
if [[ -n "$binary_markers" ]]; then
  printf '%s\n' "$binary_markers" >&2
  fail "cloud endpoint or secret marker found in app binary"
fi

secret_hits="$(
  rg -n --hidden \
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
    'sk-[A-Za-z0-9_-]{20,}|sk_[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,}' \
    "$ROOT" || true
)"
if [[ -n "$secret_hits" ]]; then
  printf '%s\n' "$secret_hits" >&2
  fail "possible secret found in public source surface"
fi

echo "release security audit passed"
