from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import meaning_coverage, meaning_loss, repeated_substring_score, term_coverage_report, word_error_rate

ASR_TOOL = ROOT / "native/RambleFixHotkey/.build/debug/RambleFixHotkeyASRTool"
DEFAULT_ENDPOINT = "http://127.0.0.1:8188/inference"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run exact current product ASR path on a corpus.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--legacy-cli", action="store_true", help="Use old ramblefix.cli dictate-audio path instead of native app ASR tool.")
    parser.add_argument("--skip-process-fallback", action="store_true")
    parser.add_argument("--no-cleanup", action="store_true")
    args = parser.parse_args()

    items = json.loads(args.corpus.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise RuntimeError("corpus must be a JSON list")
    missing_audio = [(str(item.get("id") or ""), _audio_path(item)) for item in items if not _audio_path(item).exists()]
    if missing_audio:
        preview = ", ".join(f"{row_id}:{path}" for row_id, path in missing_audio[:10])
        raise RuntimeError(f"corpus has {len(missing_audio)} missing audio file(s): {preview}")

    rows: list[dict[str, Any]] = []
    for item in items:
        row = _run_item(
            item,
            timeout_seconds=args.timeout_seconds,
            endpoint=args.endpoint,
            legacy_cli=args.legacy_cli,
            skip_process_fallback=args.skip_process_fallback,
            no_cleanup=args.no_cleanup,
        )
        rows.append(row)
        status = "ERR" if row.get("error") else "OK"
        print(f"{status} {row['id']} wall={row['seconds']} text={str(row.get('actual') or '')[:120]}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


def _run_item(
    item: dict[str, Any],
    *,
    timeout_seconds: float,
    skip_process_fallback: bool,
    no_cleanup: bool,
    endpoint: str = DEFAULT_ENDPOINT,
    legacy_cli: bool = False,
) -> dict[str, Any]:
    row_id = str(item.get("id") or "")
    audio = _audio_path(item)
    gold = str(item.get("gold") or "")
    category = str(item.get("category") or "")
    terms = item.get("critical") or item.get("critical_terms") or item.get("terms") or item.get("anchors")
    started = time.perf_counter()
    try:
        if legacy_cli:
            cmd = [str(ROOT / ".venv/bin/python"), "-m", "ramblefix.cli", "dictate-audio", str(audio), "--json"]
            if skip_process_fallback:
                cmd.append("--skip-process-fallback")
            if no_cleanup:
                cmd.append("--no-cleanup")
        else:
            if not ASR_TOOL.exists():
                raise RuntimeError(f"missing native ASR tool: {ASR_TOOL}")
            cmd = [
                str(ASR_TOOL),
                "--audio",
                str(audio),
                "--endpoint",
                endpoint,
                "--timeout",
                str(timeout_seconds),
            ]
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
            timeout=timeout_seconds,
        )
        wall_seconds = round(time.perf_counter() - started, 3)
        payload = json.loads(proc.stdout)
        actual = str(payload.get("text") or "")
        term_report = term_coverage_report(gold, actual, terms)
        return {
            "id": row_id,
            "category": category,
            "backend": "dictate_audio_product_path_wall",
            "audio": str(audio),
            "gold": gold,
            "actual": actual,
            "wer": word_error_rate(gold, actual) if gold else None,
            "meaning_loss": meaning_loss(gold, actual) if gold else None,
            "meaning_coverage": meaning_coverage(gold, actual) if gold else None,
            "term_coverage": term_report["coverage"],
            "term_hits": term_report["hits"],
            "term_misses": term_report["misses"],
            "term_terms": term_report["terms"],
            "repeat": repeated_substring_score(actual),
            "seconds": wall_seconds,
            "meta": {
                "payload_seconds": payload.get("seconds"),
                "engine": payload.get("engine"),
                "processor": payload.get("processor"),
                "route": payload.get("route"),
                "raw_text": payload.get("raw_text"),
                "fallback_reason": payload.get("fallback_reason"),
                "quality": payload.get("quality") or {},
                "product_path_eval": "legacy_cli" if legacy_cli else "native_asr_tool",
            },
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - eval should report all rows.
        wall_seconds = round(time.perf_counter() - started, 3)
        term_report = term_coverage_report(gold, "", terms)
        return {
            "id": row_id,
            "category": category,
            "backend": "dictate_audio_product_path_wall",
            "audio": str(audio),
            "gold": gold,
            "actual": "",
            "wer": None,
            "meaning_loss": 1.0 if gold else None,
            "meaning_coverage": 0.0 if gold else None,
            "term_coverage": term_report["coverage"],
            "term_hits": [],
            "term_misses": term_report["terms"],
            "term_terms": term_report["terms"],
            "repeat": 0.0,
            "seconds": wall_seconds,
            "meta": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def _audio_path(item: dict[str, Any]) -> Path:
    raw = str(item.get("audio") or "")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


if __name__ == "__main__":
    main()
