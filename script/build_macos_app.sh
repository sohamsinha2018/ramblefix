#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PACKAGE_DIR="$ROOT/native/RambleFixHotkey"
CONFIGURATION="${RAMBLEFIX_BUILD_CONFIGURATION:-release}"
APP_NAME="${RAMBLEFIX_APP_NAME:-RambleFix Local}"
BUNDLE_ID="${RAMBLEFIX_BUNDLE_ID:-com.ramblefix.local}"
EXECUTABLE_NAME="${RAMBLEFIX_EXECUTABLE_NAME:-RambleFixLocal}"
VERSION="${RAMBLEFIX_APP_VERSION:-0.1.0}"
BUILD_NUMBER="${RAMBLEFIX_APP_BUILD:-1}"
APP="${1:-$ROOT/dist/$APP_NAME.app}"

if [[ "${RAMBLEFIX_SKIP_REGRESSION:-0}" != "1" ]]; then
  "$ROOT/script/regression_ramblefix_hotkey.sh"
fi

resolve_codesign_identity() {
  if [[ -n "${RAMBLEFIX_CODESIGN_IDENTITY:-}" ]]; then
    printf '%s\n' "$RAMBLEFIX_CODESIGN_IDENTITY"
    return
  fi
  local identity
  identity="$(security find-identity -v -p codesigning 2>/dev/null | awk -F '"' '/"RambleFix Local Dev"/ { print $2; exit }')"
  if [[ -n "$identity" ]]; then
    printf '%s\n' "$identity"
  else
    printf '%s\n' "-"
  fi
}

swift build --package-path "$PACKAGE_DIR" --configuration "$CONFIGURATION"
BIN_DIR="$(swift build --package-path "$PACKAGE_DIR" --configuration "$CONFIGURATION" --show-bin-path)"
BINARY="$BIN_DIR/RambleFixHotkey"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$PACKAGE_DIR/Info.plist" "$APP/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleName -string "$APP_NAME" "$APP/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleIdentifier -string "$BUNDLE_ID" "$APP/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleExecutable -string "$EXECUTABLE_NAME" "$APP/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleShortVersionString -string "$VERSION" "$APP/Contents/Info.plist"
/usr/bin/plutil -replace CFBundleVersion -string "$BUILD_NUMBER" "$APP/Contents/Info.plist"
/usr/bin/plutil -replace NSAppleEventsUsageDescription -string "$APP_NAME pastes dictated text into the app you are using." "$APP/Contents/Info.plist"
/usr/bin/plutil -replace NSMicrophoneUsageDescription -string "$APP_NAME records your voice for local dictation." "$APP/Contents/Info.plist"
/usr/bin/plutil -replace NSInputMonitoringUsageDescription -string "$APP_NAME listens for Fn or Control to start local dictation." "$APP/Contents/Info.plist"
cp "$BINARY" "$APP/Contents/MacOS/$EXECUTABLE_NAME"
printf '%s\n' "$ROOT" > "$APP/Contents/Resources/ramblefix-root.txt"
chmod +x "$APP/Contents/MacOS/$EXECUTABLE_NAME"

plutil -lint "$APP/Contents/Info.plist" >/dev/null
SIGN_IDENTITY="$(resolve_codesign_identity)"
codesign --force --deep --sign "$SIGN_IDENTITY" "$APP" >/dev/null
if [[ "$SIGN_IDENTITY" == "-" ]]; then
  echo "warning: signed ad-hoc; macOS permissions may break after rebuilds. Run script/create_local_codesign_identity.sh." >&2
fi

echo "$APP"
