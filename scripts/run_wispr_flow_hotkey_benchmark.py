#!/usr/bin/env python3
"""Capture Wispr Flow rows through its real hotkey path.

This is an app-level harness, not an engine API benchmark. It focuses a
TextEdit document, holds the configured hotkey, plays a corpus WAV, releases
the hotkey, then reads Wispr Flow's local history DB plus the TextEdit text.

Fair same-WAV use requires a virtual mic/loopback input. If you run this with
speaker playback into the room mic, keep the output as exploratory evidence.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WISPR_DB = Path.home() / "Library/Application Support/Wispr Flow/flow.sqlite"
DEFAULT_CORPUS = ROOT / "eval_corpus/public_launch_dictation_pool_20260613.json"


@dataclass
class WisprCaptureRow:
    corpus_id: str
    tool: str
    tool_version: str
    capture_method: str
    audio: str
    gold: str
    actual: str
    textedit_text: str
    wispr_transcript_id: str | None
    wispr_timestamp: str | None
    wispr_status: str | None
    wispr_detected_language: str | None
    wispr_duration: float | None
    wispr_e2e_latency_ms: float | None
    timestamps: dict[str, float]
    cloud_disabled: bool
    error: str | None
    meta: dict[str, Any]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "eval_runs/wispr-flow-hotkey-benchmark")
    parser.add_argument("--db", type=Path, default=DEFAULT_WISPR_DB)
    parser.add_argument("--english", type=int, default=3)
    parser.add_argument("--hinglish", type=int, default=3)
    parser.add_argument("--item-id", action="append", default=[])
    parser.add_argument("--hotkey", default="fn", help="fn, control, option, command, shift, ctrl-option-space, option-1, or none")
    parser.add_argument("--capture-method", default="manual_virtual_mic", choices=["manual_virtual_mic", "hotkey_live"])
    parser.add_argument("--settle-seconds", type=float, default=7.0)
    parser.add_argument("--post-release-timeout", type=float, default=25.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    corpus = load_corpus(args.corpus)
    selected = select_cases(corpus, english=args.english, hinglish=args.hinglish, item_ids=args.item_id)

    rows: list[WisprCaptureRow] = []
    for item in selected:
        rows.append(capture_one(item, args))
        write_rows(output_dir, rows)

    write_rows(output_dir, rows)
    (output_dir / "summary.md").write_text(render_summary(rows, args), encoding="utf-8")
    print(output_dir / "summary.md")


def load_corpus(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"corpus must be a JSON list: {path}")
    return rows


def select_cases(
    corpus: list[dict[str, Any]],
    *,
    english: int,
    hinglish: int,
    item_ids: list[str],
) -> list[dict[str, Any]]:
    if item_ids:
        by_id = {str(row.get("id")): row for row in corpus}
        missing = [item_id for item_id in item_ids if item_id not in by_id]
        if missing:
            raise ValueError(f"missing corpus ids: {', '.join(missing)}")
        return [by_id[item_id] for item_id in item_ids]
    selected: list[dict[str, Any]] = []
    selected.extend(
        [
            row
            for row in corpus
            if row.get("category") in {"fleurs_english", "youtube_english", "real_use_english_dictation"}
        ][:english]
    )
    selected.extend(
        [
            row
            for row in corpus
            if row.get("category")
            in {"openslr104_hinglish", "real_use_hindi_hinglish_probe", "real_use_hindi_hinglish_dictation", "real_use_hinglish_dictation"}
        ][:hinglish]
    )
    return selected


def capture_one(item: dict[str, Any], args: argparse.Namespace) -> WisprCaptureRow:
    audio = resolve_audio(item, args.corpus)
    gold = str(item.get("gold") or "")
    before_id = latest_wispr_id(args.db)
    timestamps: dict[str, float] = {}
    error: str | None = None
    textedit_text = ""

    try:
        if args.dry_run:
            return build_row(
                item,
                audio,
                gold,
                textedit_text="",
                wispr_row=None,
                timestamps={},
                args=args,
                error="dry_run",
            )

        focus_textedit()
        clear_textedit()
        time.sleep(0.4)

        timestamps["hotkey_down"] = time.time()
        hotkey_down(args.hotkey)
        time.sleep(0.15)

        timestamps["playback_start"] = time.time()
        subprocess.run(["afplay", str(audio)], check=True, timeout=max(15.0, audio_duration_hint(item) + 20.0))
        timestamps["speech_end"] = time.time()

        time.sleep(0.10)
        hotkey_up(args.hotkey)
        timestamps["hotkey_up"] = time.time()

        wispr_row = wait_for_new_wispr_row(args.db, before_id=before_id, timeout=args.post_release_timeout)
        timestamps["final_visible"] = time.time()
        time.sleep(args.settle_seconds)
        textedit_text = read_textedit()
        timestamps["paste_done"] = time.time()
    except Exception as exc:  # noqa: BLE001 - benchmark rows should preserve exact failure
        error = repr(exc)
        try:
            hotkey_up(args.hotkey)
        except Exception:
            pass
        wispr_row = None
        try:
            textedit_text = read_textedit()
        except Exception:
            textedit_text = ""
        now = time.time()
        timestamps.setdefault("hotkey_up", now)
        timestamps.setdefault("paste_done", now)

    return build_row(
        item,
        audio,
        gold,
        textedit_text=textedit_text,
        wispr_row=wispr_row,
        timestamps=timestamps,
        args=args,
        error=error,
    )


def build_row(
    item: dict[str, Any],
    audio: Path,
    gold: str,
    *,
    textedit_text: str,
    wispr_row: sqlite3.Row | None,
    timestamps: dict[str, float],
    args: argparse.Namespace,
    error: str | None,
) -> WisprCaptureRow:
    actual = ""
    if wispr_row is not None:
        actual = str(wispr_row["formattedText"] or wispr_row["asrText"] or "")
    if not actual:
        actual = textedit_text
    return WisprCaptureRow(
        corpus_id=str(item.get("id") or ""),
        tool="wispr_flow",
        tool_version=wispr_version(),
        capture_method=args.capture_method,
        audio=str(audio),
        gold=gold,
        actual=actual,
        textedit_text=textedit_text,
        wispr_transcript_id=str(wispr_row["transcriptEntityId"]) if wispr_row is not None else None,
        wispr_timestamp=str(wispr_row["timestamp"]) if wispr_row is not None else None,
        wispr_status=str(wispr_row["status"]) if wispr_row is not None else None,
        wispr_detected_language=str(wispr_row["detectedLanguage"]) if wispr_row is not None else None,
        wispr_duration=float(wispr_row["duration"]) if wispr_row is not None and wispr_row["duration"] is not None else None,
        wispr_e2e_latency_ms=float(wispr_row["e2eLatency"]) if wispr_row is not None and wispr_row["e2eLatency"] is not None else None,
        timestamps=timestamps,
        cloud_disabled=False,
        error=error,
        meta={
            "benchmark_type": "cloud_app_hotkey",
            "hotkey": args.hotkey,
            "requires_virtual_mic_for_fair_same_wav": args.capture_method == "manual_virtual_mic",
            "reference_trust": item.get("reference_trust"),
            "category": item.get("category"),
        },
    )


def resolve_audio(item: dict[str, Any], corpus_path: Path) -> Path:
    audio = Path(str(item["audio"]))
    if audio.is_absolute():
        return audio
    return corpus_path.resolve().parent.parent / audio


def audio_duration_hint(item: dict[str, Any]) -> float:
    value = item.get("audio_seconds") or item.get("duration") or item.get("speech_duration") or 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def latest_wispr_id(db_path: Path) -> str | None:
    with connect_wispr(db_path) as conn:
        row = conn.execute("select transcriptEntityId from History order by timestamp desc limit 1").fetchone()
    return str(row[0]) if row else None


def wait_for_new_wispr_row(db_path: Path, *, before_id: str | None, timeout: float) -> sqlite3.Row | None:
    deadline = time.monotonic() + timeout
    latest_new_row: sqlite3.Row | None = None
    while time.monotonic() < deadline:
        with connect_wispr(db_path) as conn:
            row = conn.execute(
                """
                select transcriptEntityId,timestamp,status,duration,numWords,detectedLanguage,e2eLatency,
                       formattedText,asrText
                from History
                order by timestamp desc
                limit 1
                """
            ).fetchone()
        if row and str(row["transcriptEntityId"]) != str(before_id):
            latest_new_row = row
            text = str(row["formattedText"] or row["asrText"] or "").strip()
            status = str(row["status"] or "").lower()
            if text or status in {"no_audio", "failed", "error", "transcription_error"}:
                return row
        time.sleep(0.5)
    return latest_new_row


def connect_wispr(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.expanduser()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def focus_textedit() -> None:
    run_osascript(
        """
        tell application "TextEdit"
          activate
          make new document
        end tell
        delay 0.2
        """
    )


def clear_textedit() -> None:
    run_osascript(
        """
        tell application "TextEdit"
          set text of front document to ""
          activate
        end tell
        """
    )


def read_textedit() -> str:
    return run_osascript('tell application "TextEdit" to get text of front document').strip()


def hotkey_down(spec: str) -> None:
    run_osascript(hotkey_script(spec, down=True))


def hotkey_up(spec: str) -> None:
    run_osascript(hotkey_script(spec, down=False))


def hotkey_script(spec: str, *, down: bool) -> str:
    spec = spec.lower().strip()
    action = "down" if down else "up"
    if spec == "none":
        return "return"
    if spec == "fn":
        return f"cliclick:k{action[0]}:fn"
    if spec in {"control", "ctrl", "option", "command", "shift"}:
        key = "control" if spec == "ctrl" else spec
        return f'tell application "System Events" to key {action} {key}'
    if spec == "ctrl-option-space":
        if down:
            return 'tell application "System Events" to key down control\ntell application "System Events" to key down option\ntell application "System Events" to key code 49'
        return 'tell application "System Events" to key up option\ntell application "System Events" to key up control'
    if spec == "option-1":
        if down:
            return 'tell application "System Events" to key down option\ntell application "System Events" to key code 18'
        return 'tell application "System Events" to key up option'
    raise ValueError(f"unsupported hotkey spec: {spec}")


def run_osascript(script: str) -> str:
    if script.startswith("cliclick:"):
        command = script.split(":", 1)[1]
        proc = subprocess.run(["/opt/homebrew/bin/cliclick", command], text=True, capture_output=True, timeout=20)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or f"cliclick failed {proc.returncode}").strip())
        return proc.stdout
    proc = subprocess.run(["osascript", "-e", script], text=True, capture_output=True, timeout=20)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"osascript failed {proc.returncode}").strip())
    return proc.stdout


def wispr_version() -> str:
    plist = Path("/Applications/Wispr Flow.app/Contents/Info.plist")
    if not plist.exists():
        return "unknown"
    proc = subprocess.run(
        ["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString", str(plist)],
        text=True,
        capture_output=True,
        timeout=10,
    )
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "unknown"


def write_rows(output_dir: Path, rows: list[WisprCaptureRow]) -> None:
    jsonl = "\n".join(json.dumps(asdict(row), ensure_ascii=False) for row in rows)
    (output_dir / "wispr_flow_rows.jsonl").write_text(jsonl + ("\n" if jsonl else ""), encoding="utf-8")
    (output_dir / "wispr_flow_rows.json").write_text(
        json.dumps([asdict(row) for row in rows], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def render_summary(rows: list[WisprCaptureRow], args: argparse.Namespace) -> str:
    ok_rows = [row for row in rows if not row.error and row.actual.strip()]
    e2e = [row.wispr_e2e_latency_ms / 1000.0 for row in ok_rows if row.wispr_e2e_latency_ms is not None]
    release_to_paste = [
        row.timestamps["paste_done"] - row.timestamps["hotkey_up"]
        for row in ok_rows
        if "paste_done" in row.timestamps and "hotkey_up" in row.timestamps
    ]
    lines = [
        "# Wispr Flow Hotkey Benchmark",
        "",
        f"- Rows: `{len(rows)}`",
        f"- Successful text rows: `{len(ok_rows)}`",
        f"- Capture method: `{args.capture_method}`",
        f"- Hotkey: `{args.hotkey}`",
        f"- Cloud benchmark: `true`",
        "",
        "## Aggregate",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Wispr DB e2e p50 seconds | {fmt(percentile(e2e, 0.50))} |",
        f"| Wispr DB e2e p95 seconds | {fmt(percentile(e2e, 0.95))} |",
        f"| Harness release-to-paste p50 seconds | {fmt(percentile(release_to_paste, 0.50))} |",
        f"| Harness release-to-paste p95 seconds | {fmt(percentile(release_to_paste, 0.95))} |",
        "",
        "## Rows",
        "",
        "| ID | Lang | e2e s | Actual | Error |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        e2e_s = None if row.wispr_e2e_latency_ms is None else row.wispr_e2e_latency_ms / 1000.0
        actual = " ".join(row.actual.split())[:120]
        lines.append(
            f"| {row.corpus_id} | {row.wispr_detected_language or ''} | {fmt(e2e_s)} | {escape(actual)} | {escape(row.error or '')} |"
        )
    return "\n".join(lines) + "\n"


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * q
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def fmt(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    main()
