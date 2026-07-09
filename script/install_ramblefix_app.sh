#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST_DIR="${RAMBLEFIX_INSTALL_DIR:-/Applications}"
CHANNEL="${RAMBLEFIX_INSTALL_CHANNEL:-local}"

case "$CHANNEL" in
  local|stable)
    APP_NAME="${RAMBLEFIX_APP_NAME:-RambleFix Local}"
    BUNDLE_ID="${RAMBLEFIX_BUNDLE_ID:-com.ramblefix.local}"
    EXECUTABLE_NAME="${RAMBLEFIX_EXECUTABLE_NAME:-RambleFixLocal}"
    ;;
  canary)
    APP_NAME="${RAMBLEFIX_APP_NAME:-RambleFix Canary}"
    BUNDLE_ID="${RAMBLEFIX_BUNDLE_ID:-com.ramblefix.canary}"
    EXECUTABLE_NAME="${RAMBLEFIX_EXECUTABLE_NAME:-RambleFixCanary}"
    ;;
  dev)
    APP_NAME="${RAMBLEFIX_APP_NAME:-RambleFix Dev}"
    BUNDLE_ID="${RAMBLEFIX_BUNDLE_ID:-com.ramblefix.dev}"
    EXECUTABLE_NAME="${RAMBLEFIX_EXECUTABLE_NAME:-RambleFixDev}"
    ;;
  *)
    echo "Unknown RAMBLEFIX_INSTALL_CHANNEL=$CHANNEL. Use local, stable, canary, or dev." >&2
    exit 2
    ;;
esac

BUILD_APP="$ROOT/dist/$APP_NAME.app"

if [[ ! -d "$DEST_DIR" || ! -w "$DEST_DIR" ]]; then
  DEST_DIR="$HOME/Applications"
  mkdir -p "$DEST_DIR"
fi

DEST_APP="$DEST_DIR/$APP_NAME.app"
PREVIOUS_APP="$DEST_DIR/$APP_NAME.previous.app"

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

RAMBLEFIX_APP_NAME="$APP_NAME" \
RAMBLEFIX_BUNDLE_ID="$BUNDLE_ID" \
RAMBLEFIX_EXECUTABLE_NAME="$EXECUTABLE_NAME" \
"$ROOT/script/build_macos_app.sh" "$BUILD_APP"

pkill -x "$EXECUTABLE_NAME" 2>/dev/null || true
if [[ "$CHANNEL" == "local" || "$CHANNEL" == "stable" ]]; then
  pkill -x RambleFixHotkey 2>/dev/null || true
  launchctl remove com.ramblefix.local 2>/dev/null || true
  launchctl remove com.ramblefix.hotkey 2>/dev/null || true
fi

screen -S ramblefix-srota-server -X quit 2>/dev/null || true
pgrep -f 'ramblefix[.]srota_server' | xargs -r kill 2>/dev/null || true
screen -dmS ramblefix-srota-server zsh -lc "cd '$ROOT' && script/start_srota_server.sh > logs/srota-server-8188.log 2>&1"

rm -rf "$PREVIOUS_APP"
if [[ -d "$DEST_APP" ]]; then
  cp -R "$DEST_APP" "$PREVIOUS_APP"
fi
rm -rf "$DEST_APP"
cp -R "$BUILD_APP" "$DEST_APP"
xattr -dr com.apple.quarantine "$DEST_APP" 2>/dev/null || true
SIGN_IDENTITY="$(resolve_codesign_identity)"
codesign --force --deep --sign "$SIGN_IDENTITY" "$DEST_APP" >/dev/null
if [[ "$SIGN_IDENTITY" == "-" ]]; then
  echo "warning: signed ad-hoc; macOS permissions may break after rebuilds. Run script/create_local_codesign_identity.sh." >&2
else
  echo "Signed with: $SIGN_IDENTITY"
fi

open -n "$DEST_APP"

echo "Installed and launched $DEST_APP"
if [[ -d "$PREVIOUS_APP" ]]; then
  echo "Previous build kept at $PREVIOUS_APP"
fi
echo "Bundle ID: $BUNDLE_ID"
echo "Hotkey: hold Fn or Control. Fallback: Ctrl-Option-Space."
