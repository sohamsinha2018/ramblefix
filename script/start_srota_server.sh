#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export RAMBLEFIX_SROTA_BACKEND="${RAMBLEFIX_SROTA_BACKEND:-mlx}"
export RAMBLEFIX_HINGLISH_FINALIZER_BACKEND="${RAMBLEFIX_HINGLISH_FINALIZER_BACKEND:-oriserve}"
export RAMBLEFIX_ORISERVE_BACKEND="${RAMBLEFIX_ORISERVE_BACKEND:-ggml}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
export PYTHONNOUSERSITE="${PYTHONNOUSERSITE:-1}"
PYTHON="${RAMBLEFIX_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
  else
    PYTHON="$(command -v python3)"
  fi
fi
exec "$PYTHON" -B -m ramblefix.srota_server "$@"
