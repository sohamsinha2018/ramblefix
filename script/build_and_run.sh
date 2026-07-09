#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "--record-test" ]]; then
  shift
  ./script/native_record.sh --seconds "${1:-5}" --output "recordings/native-test.wav"
  source .venv/bin/activate
  python "${CODEX_HOME:-$HOME/.codex}/skills/ramblefix-builder/scripts/audio_diagnostics.py" recordings/native-test.wav
  exit 0
fi

source .venv/bin/activate
python -m compileall -q src app.py
./script/native_record.sh --help >/dev/null
pkill -f 'streamlit run app.py' || true
streamlit run app.py --server.port 8501 --server.address 127.0.0.1
