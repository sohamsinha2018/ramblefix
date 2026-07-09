#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
exec script/install_ramblefix_app.sh
