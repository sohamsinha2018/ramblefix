#!/usr/bin/env bash
set -euo pipefail

SUMS="${1:-}"

fail() {
  echo "release checksum audit failed: $*" >&2
  exit 1
}

[[ -n "$SUMS" ]] || fail "usage: $0 path/to/SHA256SUMS"
[[ -f "$SUMS" ]] || fail "checksum file not found: $SUMS"

DIR="$(cd "$(dirname "$SUMS")" && pwd)"
FILE="$(basename "$SUMS")"

grep -q 'DictaHue-0.1.0.dmg' "$SUMS" || fail "DMG checksum missing"
grep -q 'DictaHue-0.1.0.zip' "$SUMS" || fail "ZIP checksum missing"

(
  cd "$DIR"
  shasum -a 256 -c "$FILE" >/dev/null
)

echo "release checksum audit passed"
