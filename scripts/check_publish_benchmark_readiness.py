#!/usr/bin/env python3
"""Check whether benchmark artifacts are quote-safe for publication."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "eval_corpus/actual_user_english_hinglish_benchmark_20260705.json"
DEFAULT_LOCAL_SCORE = ROOT / "eval_runs/actual-user-local-benchmark-20260705-v2/variant_scorecard.json"
DEFAULT_LOCAL_STATUS = ROOT / "eval_runs/actual-user-local-benchmark-20260705-v2/app_probe_status.json"
DEFAULT_WISPR_SCORE = ROOT / "eval_runs/wispr-flow-hotkey-actual-20260705-v3/variant_scorecard.json"
DEFAULT_WISPR_ROWS = ROOT / "eval_runs/wispr-flow-hotkey-actual-20260705-v3/wispr_flow_rows.json"
DEFAULT_MUESLI_SCORE = ROOT / "eval_runs/muesli-whisperkit-indicative-4-20260705/variant_scorecard.json"
DEFAULT_OUTPUT_MD = ROOT / "docs/benchmark_publish_readiness_20260705.md"
DEFAULT_OUTPUT_JSON = ROOT / "docs/benchmark_publish_readiness_20260705.json"

VIRTUAL_AUDIO_KEYWORDS = ("blackhole", "loopback", "soundflower", "vb-cable", "vb cable", "aggregate")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--local-score", type=Path, default=DEFAULT_LOCAL_SCORE)
    parser.add_argument("--local-status", type=Path, default=DEFAULT_LOCAL_STATUS)
    parser.add_argument("--wispr-score", type=Path, default=DEFAULT_WISPR_SCORE)
    parser.add_argument("--wispr-rows", type=Path, default=DEFAULT_WISPR_ROWS)
    parser.add_argument("--muesli-score", type=Path, default=DEFAULT_MUESLI_SCORE)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    args = parser.parse_args()

    report = build_report(args)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(render_markdown(report), encoding="utf-8")
    print(args.output_md)
    print(f"overall={report['overall_state']}")


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    corpus_rows = load_json(args.corpus)
    local_score = load_json(args.local_score)
    local_status = load_json(args.local_status)
    wispr_score = load_json(args.wispr_score)
    wispr_rows = load_json(args.wispr_rows)
    muesli = muesli_summary(args.muesli_score)

    corpus = corpus_summary(corpus_rows)
    local = local_summary(local_score, local_status)
    wispr = wispr_summary(wispr_score, wispr_rows)
    virtual_audio = virtual_audio_summary()

    requirements = [
        requirement(
            "real_user_corpus_under_60s",
            "quote-safe" if corpus["rows"] > 0 and corpus["max_seconds"] <= 60.0 else "blocked",
            f"{corpus['rows']} rows; max {corpus['max_seconds']:.1f}s; english={corpus['english']} hinglish={corpus['hinglish']}",
        ),
        requirement(
            "local_same_wav_openwhispr",
            "quote-safe" if local["has_ramblefix"] and local["has_openwhispr"] else "blocked",
            local["evidence"],
        ),
        requirement(
            "wispr_flow_hotkey_cloud",
            "quote-safe" if wispr["capture_path"] == "virtual_mic_same_wav" else ("caveated" if wispr["success_rows"] else "blocked"),
            wispr["evidence"],
        ),
        requirement(
            "popular_local_app_coverage",
            "caveated" if local["blocked_tools"] or muesli["state"] != "quote-safe" else "quote-safe",
            f"{local['coverage_evidence']}; muesli={muesli['evidence']}",
        ),
        requirement(
            "virtual_mic_available",
            "quote-safe" if virtual_audio["detected"] else "blocked",
            virtual_audio["evidence"],
        ),
    ]
    overall_state = "quote-safe" if all(row["state"] == "quote-safe" for row in requirements) else "not_publish_clean"
    return {
        "overall_state": overall_state,
        "corpus": corpus,
        "local": local,
        "wispr": wispr,
        "muesli": muesli,
        "virtual_audio": virtual_audio,
        "requirements": requirements,
    }


def corpus_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    seconds = [float(row.get("audio_seconds") or 0.0) for row in rows]
    return {
        "rows": len(rows),
        "english": sum(1 for row in rows if row.get("category") == "real_use_english_dictation"),
        "hinglish": sum(1 for row in rows if row.get("category") == "real_use_hindi_hinglish_probe"),
        "max_seconds": max(seconds) if seconds else 0.0,
    }


def local_summary(score: dict[str, Any], statuses: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = score.get("summary") or []
    backends = {str(row.get("backend")) for row in summaries}
    blocked_tools = [
        {
            "tool": row.get("tool"),
            "status": row.get("status"),
            "detail": " ".join(str(row.get("detail") or "").split()),
        }
        for row in statuses
        if row.get("status") in {"blocked", "missing"}
        and row.get("tool") not in {"OpenWhispr", "Wispr Flow"}
    ]
    return {
        "has_ramblefix": any(name.startswith("ramblefix_launch_engine") for name in backends),
        "has_openwhispr": any(name.startswith("openwhispr_bundle") for name in backends),
        "summary": summaries,
        "blocked_tools": blocked_tools,
        "evidence": f"measured backends={', '.join(sorted(backends))}",
        "coverage_evidence": f"blocked/missing tools={', '.join(str(row['tool']) for row in blocked_tools) or 'none'}",
    }


def wispr_summary(score: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    success_rows = [row for row in rows if str(row.get("actual") or "").strip()]
    methods = sorted({str(row.get("capture_method") or "") for row in rows})
    summary = score.get("summary") or []
    capture_path = "virtual_mic_same_wav" if methods == ["manual_virtual_mic"] else "hotkey_speaker_to_mic"
    return {
        "rows": len(rows),
        "success_rows": len(success_rows),
        "capture_methods": methods,
        "capture_path": capture_path,
        "summary": summary,
        "evidence": f"{len(success_rows)}/{len(rows)} rows with text; capture_methods={', '.join(methods) or 'none'}; path={capture_path}",
    }


def muesli_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "state": "blocked",
            "evidence": "no Muesli/WhisperKit sample scorecard found",
            "summary": [],
        }
    score = load_json(path)
    summaries = score.get("summary") or []
    return {
        "state": "caveated",
        "evidence": "WhisperKit CLI/Muesli-family indicative 4-clip run exists; full Muesli app not measured",
        "summary": summaries,
    }


def virtual_audio_summary() -> dict[str, Any]:
    hal_names = []
    for folder in [Path("/Library/Audio/Plug-Ins/HAL"), Path.home() / "Library/Audio/Plug-Ins/HAL"]:
        if folder.exists():
            hal_names.extend(path.name for path in folder.iterdir())
    ffmpeg_output = ffmpeg_devices()
    haystack = "\n".join(hal_names + [ffmpeg_output]).lower()
    detected = any(keyword in haystack for keyword in VIRTUAL_AUDIO_KEYWORDS)
    return {
        "detected": detected,
        "hal_plugins": sorted(hal_names),
        "ffmpeg_probe_ok": bool(ffmpeg_output.strip()),
        "evidence": f"HAL={', '.join(sorted(hal_names)) or 'none'}; virtual_keywords_detected={detected}",
    }


def ffmpeg_devices() -> str:
    ffmpeg = Path("/opt/homebrew/bin/ffmpeg")
    if not ffmpeg.exists():
        return ""
    try:
        proc = subprocess.run(
            [str(ffmpeg), "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            text=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return "\n".join([proc.stdout or "", proc.stderr or ""])


def requirement(name: str, state: str, evidence: str) -> dict[str, str]:
    return {"name": name, "state": state, "evidence": evidence}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Benchmark Publish Readiness",
        "",
        f"Overall: `{report['overall_state']}`",
        "",
        "## Requirements",
        "",
        "| Requirement | State | Evidence |",
        "| --- | --- | --- |",
    ]
    for row in report["requirements"]:
        lines.append(f"| `{row['name']}` | `{row['state']}` | {esc(row['evidence'])} |")
    lines.extend(
        [
            "",
            "## Current Decision",
            "",
            "- Local same-WAV RambleFix vs OpenWhispr claims are quote-safe.",
            "- Wispr Flow claims are caveated until the capture path uses a virtual mic or loopback.",
            "- Muesli coverage is indicative only: tested WhisperKit CLI/Muesli-family path, not full Muesli app.",
            "- Popular local app coverage is caveated because TypeWhisper, Handy, Apple Dictation, and VoiceInk are not all measurable yet.",
            "",
            "## Next Required Step",
            "",
            "Install/configure a virtual mic or loopback input, set Wispr Flow to that input, then rerun.",
            "",
            "Recommended setup:",
            "",
            "```bash",
            "brew install blackhole-2ch",
            "brew install switchaudio-osx",
            "```",
            "",
            "BlackHole may require a reboot before it appears as an audio device.",
            "",
            "After install:",
            "",
            "1. Set macOS output to `BlackHole 2ch` for benchmark playback.",
            "2. Set Wispr Flow microphone/input to `BlackHole 2ch`.",
            "3. Run:",
            "",
            "```bash",
            ".venv/bin/python scripts/run_wispr_flow_hotkey_benchmark.py \\",
            "  --corpus eval_corpus/actual_user_english_hinglish_benchmark_20260705.json \\",
            "  --output-dir eval_runs/wispr-flow-hotkey-actual-virtualmic-20260705 \\",
            "  --english 12 --hinglish 8 --hotkey fn --capture-method manual_virtual_mic \\",
            "  --settle-seconds 4 --post-release-timeout 25",
            "",
            ".venv/bin/python scripts/score_actual_variant_benchmark.py \\",
            "  --corpus eval_corpus/actual_user_english_hinglish_benchmark_20260705.json \\",
            "  --rows eval_runs/wispr-flow-hotkey-actual-virtualmic-20260705/wispr_flow_rows.json \\",
            "  --output-dir eval_runs/wispr-flow-hotkey-actual-virtualmic-20260705",
            "```",
            "",
            "Primary sources: `https://github.com/ExistentialAudio/BlackHole`, `https://github.com/deweller/switchaudio-osx/`.",
            "",
        ]
    )
    return "\n".join(lines)


def esc(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    main()
