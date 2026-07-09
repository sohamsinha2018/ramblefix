#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODEL="${RAMBLEFIX_WHISPER_MODEL:-$ROOT/models/ggml-small.bin}"
SERVER="${RAMBLEFIX_WHISPER_SERVER_BINARY:-$ROOT/bin/whisper-server}"
HOST="${RAMBLEFIX_WHISPER_HOST:-127.0.0.1}"
PORT="${RAMBLEFIX_WHISPER_PORT:-8178}"

if [[ ! -x "$SERVER" ]]; then
  SERVER="$(command -v whisper-server)"
fi

exec "$SERVER" \
  -m "$MODEL" \
  -l auto \
  -tr \
  -nt \
  --host "$HOST" \
  --port "$PORT"
