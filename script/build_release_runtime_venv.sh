#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-$ROOT/dist/release/DictaHue.app/Contents/Resources/RambleFixRuntime}"
VENV="$TARGET/.venv"
REQ="${RAMBLEFIX_RUNTIME_REQUIREMENTS:-$ROOT/requirements-runtime.txt}"
PYTHON_VERSION="${RAMBLEFIX_RUNTIME_PYTHON_VERSION:-3.12}"

fail() {
  echo "release runtime venv build failed: $*" >&2
  exit 1
}

command -v uv >/dev/null 2>&1 || fail "uv not found; install uv or set RAMBLEFIX_PACKAGE_EMBED_VENV=0 for local smoke only"
[[ -d "$TARGET" ]] || fail "runtime target missing: $TARGET"
[[ -f "$REQ" ]] || fail "runtime requirements missing: $REQ"

rm -rf "$VENV"
uv venv --managed-python --python "$PYTHON_VERSION" --relocatable --seed "$VENV"
UV_LINK_MODE=copy uv pip install --python "$VENV/bin/python" --requirements "$REQ" --strict

BASE_PREFIX="$("$VENV/bin/python" - <<'PY'
import sys
print(sys.base_prefix)
PY
)"
[[ -d "$BASE_PREFIX" ]] || fail "managed Python base prefix missing: $BASE_PREFIX"
rm -rf "$TARGET/python"
rsync -a --delete \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  "$BASE_PREFIX/" "$TARGET/python/"

rm -f "$VENV/bin/python" "$VENV/bin/python3" "$VENV/bin/python3.12"
ln -s "../../python/bin/python3.12" "$VENV/bin/python"
ln -s "python" "$VENV/bin/python3"
ln -s "python" "$VENV/bin/python3.12"
python3 - "$VENV/pyvenv.cfg" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
out = []
home_written = False
for line in lines:
    if line.startswith("home = "):
        out.append("home = ../../python/bin")
        home_written = True
    else:
        out.append(line)
if not home_written:
    out.append("home = ../../python/bin")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

find "$VENV" \( -name '__pycache__' -o -name '*.pyc' -o -name '*.pyo' \) -prune -exec rm -rf {} +
find "$TARGET/python" \( -name '__pycache__' -o -name '*.pyc' -o -name '*.pyo' \) -prune -exec rm -rf {} +

PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" - <<'PY'
import importlib.util
import sys
from pathlib import Path

required = ["mlx_whisper", "requests", "soundfile"]
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit(f"missing runtime imports: {missing}")
prefix = Path(sys.prefix).resolve()
base_prefix = Path(sys.base_prefix).resolve()
if "RambleFixRuntime" in str(prefix) and "RambleFixRuntime" not in str(base_prefix):
    raise SystemExit(f"runtime Python base is outside bundle: {base_prefix}")
print("release runtime venv smoke passed", sys.executable)
PY

find "$VENV" \( -name '__pycache__' -o -name '*.pyc' -o -name '*.pyo' \) -prune -exec rm -rf {} +
find "$TARGET/python" \( -name '__pycache__' -o -name '*.pyc' -o -name '*.pyo' \) -prune -exec rm -rf {} +
