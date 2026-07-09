#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$ROOT/native/RambleFixHotkey/Info.plist"
README="$ROOT/README.md"
LAUNCH_DOC="$ROOT/docs/mac_v0_public_launch.md"

fail() {
  echo "v0 release scope failed: $*" >&2
  exit 1
}

plutil -lint "$PLIST" >/dev/null

/usr/libexec/PlistBuddy -c "Print NSMicrophoneUsageDescription" "$PLIST" >/dev/null \
  || fail "Info.plist must explain Microphone permission"
/usr/libexec/PlistBuddy -c "Print NSInputMonitoringUsageDescription" "$PLIST" >/dev/null \
  || fail "Info.plist must explain Input Monitoring permission"
/usr/libexec/PlistBuddy -c "Print NSAppleEventsUsageDescription" "$PLIST" >/dev/null \
  || fail "Info.plist must explain paste/Apple Events usage"

if /usr/libexec/PlistBuddy -c "Print NSScreenCaptureUsageDescription" "$PLIST" >/dev/null 2>&1; then
  fail "V0 dictation app must not declare Screen Recording permission"
fi

grep -q 'RAMBLEFIX_ENABLE_MEETING_MODE.*defaultValue: false' \
  "$ROOT/native/RambleFixHotkey/Sources/RambleFixHotkey/main.swift" \
  || fail "meeting mode must be disabled by default"

grep -qi "No meeting recorder" "$README" \
  || fail "README must explicitly say V0 has no meeting recorder"
grep -qi "No screen recording permission" "$README" \
  || fail "README must explicitly say V0 has no screen recording permission"
grep -qi "Mac stable first" "$LAUNCH_DOC" \
  || fail "launch doc must keep Windows out of V0 stable"

echo "RambleFix V0 release scope gate passed"
