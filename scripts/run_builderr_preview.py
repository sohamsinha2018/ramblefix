from __future__ import annotations

import argparse
import json
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solution.transcribe import transcribe


DEFAULT_BUILDERR_REPO = Path(os.environ.get("BUILDERR_STT_REPO", "~/Code/builderr-speech-to-text")).expanduser()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RambleFix against the Builderr STT sample scorecard.")
    parser.add_argument("--builderr-repo", type=Path, default=DEFAULT_BUILDERR_REPO)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--mode", default="auto", choices=["auto", "fast", "hinglish", "verbatim"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", type=Path, default=ROOT / "eval_runs" / "builderr-preview-latest" / "rows.json")
    args = parser.parse_args()

    builderr_repo = args.builderr_repo.expanduser().resolve()
    manifest_path = (args.manifest or builderr_repo / "samples" / "manifest.json").expanduser().resolve()
    if str(builderr_repo) not in sys.path:
        sys.path.insert(0, str(builderr_repo))
    from scorecard import score_run

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if args.limit:
        manifest = manifest[: args.limit]

    rows = []
    for item in manifest:
        audio = _resolve_audio(item["audio"], manifest_path, builderr_repo)
        result = transcribe(str(audio), args.mode)
        rows.append(
            {
                "clip_id": item["clip_id"],
                "gold": item["gold"],
                "pred": result.get("text", ""),
                "must_have": item.get("must_have", []),
                "timings_ms": result.get("timings_ms"),
                "local_only": result.get("local_only", False),
                "audit": {"model_ids": result.get("model_ids"), "route": result.get("route")},
                "result": result,
            }
        )
        print(f"{item['clip_id']}: {result.get('route')} {result.get('timings_ms', {}).get('total')}ms")

    score = score_run(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"score": score, "rows": rows}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\noverall score   {score['overall_score']}/100")
    print(f"meaning proxy   {score['useful_mean']}   WER {score['wer_mean']}")
    print(f"p50 {score['p50_ms']}ms  p95 {score['p95_ms']}ms  blanks {score['blank_rate']}  hangs {score['hang_rate']}")
    print(f"clips capped    {score['clips_capped']}/{score['n']}")
    print(f"wrote {args.output}")


def _resolve_audio(audio_value: str, manifest_path: Path, builderr_repo: Path) -> Path:
    audio = Path(audio_value).expanduser()
    if audio.is_absolute():
        return audio
    for base in (builderr_repo, manifest_path.parent, Path.cwd()):
        candidate = (base / audio).resolve()
        if candidate.exists():
            return candidate
    return (builderr_repo / audio).resolve()


if __name__ == "__main__":
    main()
