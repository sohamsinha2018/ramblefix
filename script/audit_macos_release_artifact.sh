#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$ROOT/dist/release/DictaHue.app}"
PUBLIC_RELEASE="${RAMBLEFIX_PUBLIC_RELEASE:-0}"

fail() {
  echo "macOS release artifact audit failed: $*" >&2
  exit 1
}

warn() {
  echo "warning: $*" >&2
}

[[ -d "$APP" ]] || fail "app bundle not found: $APP"
PLIST="$APP/Contents/Info.plist"
[[ -f "$PLIST" ]] || fail "Info.plist not found"
plutil -lint "$PLIST" >/dev/null

EXECUTABLE="$(/usr/libexec/PlistBuddy -c "Print CFBundleExecutable" "$PLIST")"
BINARY="$APP/Contents/MacOS/$EXECUTABLE"
[[ -x "$BINARY" ]] || fail "main executable missing or not executable: $BINARY"

/usr/libexec/PlistBuddy -c "Print NSMicrophoneUsageDescription" "$PLIST" >/dev/null \
  || fail "missing Microphone usage description"
/usr/libexec/PlistBuddy -c "Print NSInputMonitoringUsageDescription" "$PLIST" >/dev/null \
  || fail "missing Input Monitoring usage description"
/usr/libexec/PlistBuddy -c "Print NSAppleEventsUsageDescription" "$PLIST" >/dev/null \
  || fail "missing Apple Events usage description"
if /usr/libexec/PlistBuddy -c "Print NSScreenCaptureUsageDescription" "$PLIST" >/dev/null 2>&1; then
  fail "V0 release must not declare Screen Recording permission"
fi

if otool -L "$BINARY" | grep -q 'ScreenCaptureKit.framework'; then
  fail "V0 binary links ScreenCaptureKit; build meeting mode separately"
fi

ROOT_MARKER="$APP/Contents/Resources/ramblefix-root.txt"
if [[ -f "$ROOT_MARKER" ]]; then
  marker="$(tr -d '\r\n' < "$ROOT_MARKER")"
  if [[ "$PUBLIC_RELEASE" == "1" && "$marker" = /* ]]; then
    fail "public bundle root marker must be relative, got: $marker"
  fi
fi

codesign --verify --deep --strict --verbose=2 "$APP" >/dev/null
SIGNING="$(codesign -dvvv "$APP" 2>&1 || true)"
if [[ "$SIGNING" == *"Signature=adhoc"* ]]; then
  if [[ "$PUBLIC_RELEASE" == "1" ]]; then
    fail "public release is ad-hoc signed"
  fi
  warn "ad-hoc signed; OK for local testing only"
fi
if [[ "$PUBLIC_RELEASE" == "1" && "$SIGNING" != *"Authority=Developer ID Application:"* ]]; then
  printf '%s\n' "$SIGNING" >&2
  fail "public release must use Developer ID Application signing"
fi

RUNTIME="$APP/Contents/Resources/RambleFixRuntime"
if [[ -d "$RUNTIME" ]]; then
  "$ROOT/script/validate_public_runtime_local_only.sh" "$RUNTIME"
  if [[ "$PUBLIC_RELEASE" == "1" ]]; then
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
  if [[ -f "$RUNTIME/config/memory_terms.json" ]] \
    && ! jq -e '(.terms // []) | length == 0' "$RUNTIME/config/memory_terms.json" >/dev/null; then
    fail "packaged runtime must not include local learned memory terms"
  fi
  if [[ -f "$RUNTIME/config/phrase_fixes.json" ]] \
    && ! jq -e '(.phrase_fixes // []) | length == 0' "$RUNTIME/config/phrase_fixes.json" >/dev/null; then
    fail "packaged runtime must not include local approved phrase fixes"
  fi
fi

echo "macOS release artifact audit passed"
