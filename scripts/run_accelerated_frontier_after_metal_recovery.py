from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = (
    ROOT
    / "eval_runs/goal-stt-optimization-20260703-expanded-v4/confirmed_union38_product_no_pure_hindi_20260703.json"
)
DEFAULT_MODELS = (
    "whisper_cpp_server_translate,"
    "oriserve_hindi2hinglish_ggml,"
    "oriserve_apex_mlx,"
    "srota_qwen3_hinglish_mlx,"
    "qwen3_asr_mlx_auto,"
    "qwen3_asr_mlx_hindi"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the post-reboot accelerated Hindi+English frontier bakeoff. "
            "Refuses to judge MLX/MPS candidates unless the accelerator health gate passes."
        )
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--models", default=DEFAULT_MODELS)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument("--mode", choices=["meaning", "verbatim"], default="meaning")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="Allow model downloads/lookups. Default is offline/local-only to preserve the product constraint.",
    )
    parser.add_argument(
        "--skip-accelerator-gate",
        action="store_true",
        help="For debugging only. Do not use for promotion decisions.",
    )
    args = parser.parse_args()

    corpus = args.corpus.expanduser().resolve()
    if not corpus.exists():
        raise SystemExit(f"missing corpus: {corpus}")

    output_dir = args.output_dir or _default_output_dir()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    env["RAMBLEFIX_SROTA_BACKEND"] = "mlx"
    env["RAMBLEFIX_SROTA_SERVER_URL"] = ""
    if not args.allow_network:
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["HF_DATASETS_OFFLINE"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"

    health = None
    if not args.skip_accelerator_gate:
        health = _run(
            [
                sys.executable,
                "scripts/check_local_accelerator_health.py",
                "--fail-on-broken-accelerator",
            ],
            env=env,
        )
        (output_dir / "accelerator_health.json").write_text(health.stdout or health.stderr, encoding="utf-8")
        if health.returncode != 0:
            _write_manifest(output_dir, args, corpus, command=None, health_returncode=health.returncode)
            print(health.stdout or health.stderr, end="")
            print(
                "accelerator unhealthy; not running MLX/MPS frontier. Reboot/recover Metal and rerun this script.",
                file=sys.stderr,
            )
            raise SystemExit(2)

    command = [
        sys.executable,
        "scripts/run_local_frontier_corpus.py",
        "--corpus",
        str(corpus),
        "--output-dir",
        str(output_dir),
        "--models",
        args.models,
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--mode",
        args.mode,
    ]
    _write_manifest(
        output_dir,
        args,
        corpus,
        command=command,
        health_returncode=health.returncode if health else None,
    )
    result = _run(command, env=env)
    (output_dir / "frontier_stdout.log").write_text(result.stdout, encoding="utf-8")
    (output_dir / "frontier_stderr.log").write_text(result.stderr, encoding="utf-8")
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    raise SystemExit(result.returncode)


def _default_output_dir() -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return (
        ROOT
        / "eval_runs/goal-stt-optimization-20260703-expanded-v5"
        / f"accelerated_frontier_union38_after_metal_recovery_{stamp}"
    )


def _run(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_manifest(
    output_dir: Path,
    args: argparse.Namespace,
    corpus: Path,
    *,
    command: list[str] | None,
    health_returncode: int | None,
) -> None:
    payload = {
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "corpus": str(corpus),
        "models": [value.strip() for value in args.models.split(",") if value.strip()],
        "mode": args.mode,
        "timeout_seconds": args.timeout_seconds,
        "allow_network": bool(args.allow_network),
        "skip_accelerator_gate": bool(args.skip_accelerator_gate),
        "accelerator_health_returncode": health_returncode,
        "command": command,
        "promotion_bar": {
            "english_useful_score_min": 0.90,
            "hindi_english_useful_score_target": 0.85,
            "polished_output_p95_seconds_max": 4.5,
            "blind_english_overwrites_allowed": 0,
        },
    }
    (output_dir / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
