#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WARN_ONLY=0
MAX_AGE_SECONDS="${RAMBLEFIX_EVAL_STALE_PROCESS_SECONDS:-300}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --warn-only)
      WARN_ONLY=1
      shift
      ;;
    --strict)
      WARN_ONLY=0
      shift
      ;;
    --max-age-seconds)
      MAX_AGE_SECONDS="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

ROOT="$ROOT" WARN_ONLY="$WARN_ONLY" MAX_AGE_SECONDS="$MAX_AGE_SECONDS" python3 - <<'PY'
from __future__ import annotations

import os
import subprocess
import sys


root = os.environ["ROOT"]
warn_only = os.environ.get("WARN_ONLY") == "1"
max_age = int(os.environ.get("MAX_AGE_SECONDS") or "300")


def elapsed_seconds(raw: str) -> int:
    # ps etime: [[dd-]hh:]mm:ss
    days = 0
    rest = raw.strip()
    if "-" in rest:
        day_raw, rest = rest.split("-", 1)
        days = int(day_raw)
    parts = [int(part) for part in rest.split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    else:
        return 0
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def emit(message: str) -> None:
    prefix = "warning: " if warn_only else "eval machine health failed: "
    print(prefix + message, file=sys.stderr)


ps = subprocess.run(
    ["ps", "-axo", "pid=,ppid=,state=,etime=,command="],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
)
if ps.returncode != 0:
    emit("could not inspect process table: " + ps.stderr.strip())
    raise SystemExit(0 if warn_only else 1)

stale: list[tuple[str, str, str, int, str]] = []
repo_binaries = (
    f"{root}/bin/whisper-cli",
    f"{root}/bin/whisper-server",
)
for line in ps.stdout.splitlines():
    parts = line.strip().split(None, 4)
    if len(parts) < 5:
        continue
    pid, ppid, state, etime, command = parts
    if not any(binary in command for binary in repo_binaries):
        continue
    age = elapsed_seconds(etime)
    if "U" in state or age >= max_age:
        stale.append((pid, state, etime, age, command))

if stale:
    details = "\n".join(
        f"  pid={pid} state={state} etime={etime} age={age}s command={command}"
        for pid, state, etime, age, command in stale[:12]
    )
    emit(
        "stale repo whisper processes detected; reboot before trusting latency benchmarks.\n"
        + details
    )
    raise SystemExit(0 if warn_only else 1)

print("eval machine health audit passed")
PY
