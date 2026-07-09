from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class CheckResult:
    name: str
    ok: bool
    seconds: float
    detail: str
    returncode: int | None = None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check local ASR accelerator health without letting Metal/MPS crashes kill the parent process."
    )
    parser.add_argument("--whisper-host", default="127.0.0.1")
    parser.add_argument("--whisper-port", type=int, default=8178)
    parser.add_argument("--srota-host", default="127.0.0.1")
    parser.add_argument("--srota-port", type=int, default=8188)
    parser.add_argument("--timeout-seconds", type=float, default=12.0)
    parser.add_argument(
        "--fail-on-broken-accelerator",
        action="store_true",
        help="Exit 2 when MLX or MPS is broken. Useful before running MLX/MPS model evals.",
    )
    args = parser.parse_args()

    checks = [
        _python_check(
            "mlx_core_matmul",
            "import mlx.core as mx\n"
            "x=mx.random.normal((64,64)); y=x@x; mx.eval(y); print('ok', float(y[0,0]))\n",
            timeout_seconds=args.timeout_seconds,
        ),
        _python_check(
            "torch_mps_matmul",
            "import torch\n"
            "print('mps_available', torch.backends.mps.is_available(), 'mps_built', torch.backends.mps.is_built())\n"
            "x=torch.randn(64,64,device='mps'); y=x@x; torch.mps.synchronize(); print('ok', float(y[0,0].cpu()))\n",
            timeout_seconds=args.timeout_seconds,
        ),
        _tcp_check("whisper_server_8178", args.whisper_host, args.whisper_port, timeout_seconds=2.0),
        _tcp_check("srota_server_8188", args.srota_host, args.srota_port, timeout_seconds=2.0),
    ]

    payload: dict[str, Any] = {
        "ok": all(check.ok for check in checks),
        "accelerator_ok": all(check.ok for check in checks if check.name in {"mlx_core_matmul", "torch_mps_matmul"}),
        "checks": [asdict(check) for check in checks],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.fail_on_broken_accelerator and not payload["accelerator_ok"]:
        raise SystemExit(2)


def _python_check(name: str, code: str, *, timeout_seconds: float) -> CheckResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CheckResult(
            name=name,
            ok=False,
            seconds=round(time.perf_counter() - started, 3),
            detail=f"timeout after {timeout_seconds:.1f}s: {str(exc)[:500]}",
            returncode=None,
        )
    detail = (completed.stdout + completed.stderr).strip()
    return CheckResult(
        name=name,
        ok=completed.returncode == 0,
        seconds=round(time.perf_counter() - started, 3),
        detail=detail[-1200:],
        returncode=completed.returncode,
    )


def _tcp_check(name: str, host: str, port: int, *, timeout_seconds: float) -> CheckResult:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            pass
        return CheckResult(
            name=name,
            ok=True,
            seconds=round(time.perf_counter() - started, 3),
            detail=f"listening on {host}:{port}",
            returncode=0,
        )
    except OSError as exc:
        return CheckResult(
            name=name,
            ok=False,
            seconds=round(time.perf_counter() - started, 3),
            detail=f"{type(exc).__name__}: {exc}",
            returncode=None,
        )


if __name__ == "__main__":
    main()
