#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${RAMBLEFIX_PYTHON:-$ROOT/.venv/bin/python}"

exec "$PYTHON" "$ROOT/scripts/run_live_provider_meeting_check.py" "$@"
