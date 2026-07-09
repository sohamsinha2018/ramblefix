#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY=1
      shift
      ;;
    --dry-run)
      APPLY=0
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

cd "$ROOT"

"$ROOT/script/audit_public_source_surface.sh"

FILES_FILE="$(mktemp)"
trap 'rm -f "$FILES_FILE"' EXIT
git ls-files --cached --others --exclude-standard -z > "$FILES_FILE"
FILE_COUNT="$(python3 - "$FILES_FILE" <<'PY'
import sys
from pathlib import Path

data = Path(sys.argv[1]).read_bytes()
print(len([item for item in data.split(b"\0") if item]))
PY
)"

if [[ "$FILE_COUNT" -eq 0 ]]; then
  echo "no publishable source files found"
  exit 0
fi

if [[ "$APPLY" != "1" ]]; then
  printf 'public source staging dry-run: %d files\n' "$FILE_COUNT"
  python3 - "$FILES_FILE" <<'PY'
import sys
from pathlib import Path

items = [item.decode() for item in Path(sys.argv[1]).read_bytes().split(b"\0") if item]
for item in items[:260]:
    print(item)
if len(items) > 260:
    print(f"... {len(items) - 260} more files")
PY
  echo
  echo "To stage exactly this audited source surface, run:"
  echo "  script/stage_public_source.sh --apply"
  exit 0
fi

xargs -0 git add -- < "$FILES_FILE"
git diff --cached --check
"$ROOT/script/audit_public_source_surface.sh"
printf 'staged audited public source: %d files\n' "$FILE_COUNT"
