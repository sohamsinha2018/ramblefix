from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


BACKENDS: list[tuple[str, str]] = [
    ("whisper_cpp_server_translate", "external"),
    ("whisper_cpp_translate", "external"),
    ("whisper_cpp", "external"),
    ("whisper_cpp_translate_base", "external"),
    ("faster_whisper", "external"),
    ("faster_whisper_auto", "external"),
    ("whisperkit_cli", "external"),
    ("accurate_en", "base"),
    ("accurate_auto", "base"),
]

CORPORA: list[tuple[str, str]] = [
    ("public_combined", "eval_corpus/public_benchmark_combined_20260612.json"),
    ("youtube_english", "eval_corpus/youtube_english_public_20260611.json"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="eval_runs/open-source-bakeoff-20260612")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--corpus", choices=[name for name, _ in CORPORA], action="append")
    parser.add_argument("--backend", action="append")
    args = parser.parse_args()

    selected_corpora = [(name, path) for name, path in CORPORA if not args.corpus or name in args.corpus]
    selected_backends = [(name, kind) for name, kind in BACKENDS if not args.backend or name in args.backend]

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    statuses: list[dict[str, object]] = []

    for corpus_name, corpus_path in selected_corpora:
        merged_rows: list[dict[str, object]] = []
        for backend, kind in selected_backends:
            run_dir = out / corpus_name / backend
            run_dir.mkdir(parents=True, exist_ok=True)
            cmd = [
                sys.executable,
                "-m",
                "ramblefix.cli",
                "eval-corpus",
                "--corpus",
                corpus_path,
                "--output-dir",
                str(run_dir),
            ]
            if kind == "base":
                cmd.extend(["--base-backends", backend])
            else:
                cmd.extend(["--base-backends", "none", "--external-backends", backend])

            started = time.perf_counter()
            status: dict[str, object] = {
                "corpus": corpus_name,
                "backend": backend,
                "kind": kind,
                "command": cmd,
                "output_dir": str(run_dir),
            }
            try:
                proc = subprocess.run(
                    cmd,
                    text=True,
                    capture_output=True,
                    timeout=args.timeout_seconds,
                    check=False,
                )
                status.update(
                    {
                        "returncode": proc.returncode,
                        "seconds": round(time.perf_counter() - started, 3),
                        "stdout_tail": proc.stdout[-2000:],
                        "stderr_tail": proc.stderr[-2000:],
                    }
                )
                rows_path = run_dir / "corpus_results.json"
                if proc.returncode == 0 and rows_path.exists():
                    rows = json.loads(rows_path.read_text(encoding="utf-8"))
                    merged_rows.extend(rows)
                    status["rows"] = len(rows)
                else:
                    status["rows"] = 0
            except subprocess.TimeoutExpired as exc:
                status.update(
                    {
                        "returncode": "timeout",
                        "seconds": round(time.perf_counter() - started, 3),
                        "timeout_seconds": args.timeout_seconds,
                        "stdout_tail": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
                        "stderr_tail": (exc.stderr or "")[-2000:] if isinstance(exc.stderr, str) else "",
                        "rows": 0,
                    }
                )
            statuses.append(status)
            print(f"{corpus_name} {backend}: {status['returncode']} rows={status['rows']} time={status['seconds']}s", flush=True)

        corpus_out = out / corpus_name
        (corpus_out / "corpus_results.json").write_text(json.dumps(merged_rows, indent=2, ensure_ascii=False), encoding="utf-8")

    (out / "bakeoff_status.json").write_text(json.dumps(statuses, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
