#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/native/RambleFixRecorderObjC/main.m"
BIN="$ROOT/native/RambleFixRecorderObjC/ramblefix-recorder"

clang -fobjc-arc -framework Foundation -framework AVFoundation "$SRC" -o "$BIN"
"$BIN" "$@"
