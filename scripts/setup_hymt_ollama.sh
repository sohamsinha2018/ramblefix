#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_DIR="$ROOT_DIR/models/hymt"
MODEL_FILE="$MODEL_DIR/Hy-MT1.5-1.8B-1.25bit.gguf"
MODEL_NAME="${1:-hymt-1.8b}"

mkdir -p "$MODEL_DIR"

if [[ ! -f "$MODEL_FILE" ]]; then
  "$ROOT_DIR/.venv/bin/python" - <<'PY'
from huggingface_hub import hf_hub_download
path = hf_hub_download(
    repo_id="tencent/Hy-MT1.5-1.8B-1.25bit-GGUF",
    filename="Hy-MT1.5-1.8B-1.25bit.gguf",
    local_dir="models/hymt",
)
print(path)
PY
fi

cat > "$MODEL_DIR/Modelfile" <<EOF
FROM $MODEL_FILE
TEMPLATE """{{ .Prompt }}"""
PARAMETER temperature 0
PARAMETER top_p 0.9
EOF

ollama create "$MODEL_NAME" -f "$MODEL_DIR/Modelfile"
echo "$MODEL_NAME"
