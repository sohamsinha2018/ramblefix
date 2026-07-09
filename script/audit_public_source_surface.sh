#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

fail() {
  echo "public source surface audit failed: $*" >&2
  exit 1
}

required_ignores=(
  ".codex/"
  ".env"
  ".env.*"
  ".venv/"
  ".venvs/"
  "native/RambleFixHotkey/.build/"
  "bin/"
  "lib/"
  "logs/"
  "eval_runs/"
  "eval_corpus/"
  "recordings/"
  "models/"
  "dist/"
  "output/"
  "config/memory_terms.json"
  "config/phrase_fixes.json"
  "*.wav"
  "*.mp3"
  "*.m4a"
  "*.mp4"
  "*.mov"
  "*.bin"
  "*.gguf"
  "*.safetensors"
  "*.npz"
  "*.onnx"
  "*.tflite"
  "*.dylib"
  "*.dmg"
  "*.zip"
)

for pattern in "${required_ignores[@]}"; do
  if ! grep -Fxq "$pattern" "$ROOT/.gitignore"; then
    fail ".gitignore missing required private/generated pattern: $pattern"
  fi
done

python3 - "$ROOT" <<'PY'
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])


def fail(message: str) -> None:
    print(f"public source surface audit failed: {message}", file=sys.stderr)
    raise SystemExit(1)


result = subprocess.run(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
    cwd=root,
    check=True,
    capture_output=True,
)
files = [Path(item.decode()) for item in result.stdout.split(b"\0") if item]

danger_prefixes = (
    Path(".codex"),
    Path(".venv"),
    Path(".venvs"),
    Path("bin"),
    Path("lib"),
    Path("logs"),
    Path("eval_runs"),
    Path("eval_corpus"),
    Path("recordings"),
    Path("models"),
    Path("dist"),
    Path("output"),
    Path(".playwright-cli"),
)
danger_exact = {
    Path(".env"),
    Path("config/memory_terms.json"),
    Path("config/phrase_fixes.json"),
}
danger_suffixes = {
    ".wav",
    ".mp3",
    ".m4a",
    ".mp4",
    ".mov",
    ".bin",
    ".gguf",
    ".safetensors",
    ".npz",
    ".onnx",
    ".tflite",
    ".dylib",
    ".so",
    ".a",
    ".dmg",
    ".zip",
}

for rel in files:
    if rel in danger_exact:
        fail(f"private generated file is publishable: {rel}")
    if rel.suffix.lower() in danger_suffixes:
        fail(f"binary/media artifact is publishable: {rel}")
    for prefix in danger_prefixes:
        if rel == prefix or prefix in rel.parents:
            fail(f"private/generated directory is publishable: {rel}")
    if rel.name == ".DS_Store" or ".egg-info" in rel.parts:
        fail(f"generated metadata is publishable: {rel}")
    full = root / rel
    if full.is_file() and full.stat().st_size > 5 * 1024 * 1024:
        fail(f"large file is publishable: {rel} ({full.stat().st_size} bytes)")

secret_re = re.compile(r"(sk-[A-Za-z0-9_-]{20,}|sk_[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,})")
personal_re = re.compile(r"(/Users/ssinha|Desktop/ludo-clips|TemporaryItems|WhatsApp/Data/tmp)")
pattern_allowlist = {
    Path("script/audit_public_source_surface.sh"),
    Path("script/validate_public_runtime_local_only.sh"),
    Path("script/audit_release_security.sh"),
    Path("script/audit_public_launch_readiness.sh"),
}

for rel in files:
    full = root / rel
    if not full.is_file():
        continue
    try:
        data = full.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fail(f"non-text file is publishable without explicit ignore: {rel}")
    if secret_re.search(data) and rel not in pattern_allowlist:
        fail(f"secret/API-key marker found in publishable source: {rel}")
    if personal_re.search(data) and rel not in pattern_allowlist:
        fail(f"personal absolute path found in publishable source: {rel}")

print(f"public source surface audit passed files={len(files)}")
PY
