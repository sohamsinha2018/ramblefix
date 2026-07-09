from __future__ import annotations

import argparse
import json
import sys
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.quality import wav_silence_metrics


@dataclass
class WavCheck:
    path: str
    exists: bool
    valid_wav: bool
    duration_seconds: float
    audio_probably_silent: bool | None
    audio_peak: float | None
    audio_rms_max: float | None
    error: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a real native meeting-mode run captured provider/system audio, mic audio, and a dual-source transcript."
    )
    parser.add_argument("--run-id", default="", help="Meeting run id. Defaults to latest native/history meeting run.")
    parser.add_argument("--logs-root", type=Path, default=ROOT / "logs")
    parser.add_argument("--min-duration-seconds", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    logs_root = args.logs_root.expanduser().resolve()
    history_rows = read_jsonl(logs_root / "history.jsonl")
    event_rows = read_jsonl(logs_root / "native_events.jsonl")
    run_id = args.run_id.strip() or latest_meeting_run_id(history_rows, event_rows)

    if not run_id:
        payload = failure_payload(
            "no_meeting_run_found",
            "No native meeting-mode run found. Use RambleFix menu: Record Meeting, play/join a provider call, then Stop Meeting Recording.",
            logs_root=logs_root,
        )
        emit(payload, args.json)
        raise SystemExit(1)

    history = latest_row(history_rows, lambda row: str(row.get("run_id") or "") == run_id)
    recording_event = latest_row(
        event_rows,
        lambda row: row.get("event") == "recording_started"
        and row.get("mode") == "meeting"
        and str(row.get("run_id") or "") == run_id,
    )
    source_event = latest_row(
        event_rows,
        lambda row: row.get("event") == "meeting_transcription_sources"
        and str(row.get("run_id") or "") == run_id,
    )
    system_started_event = latest_row(
        event_rows,
        lambda row: row.get("event") == "meeting_system_audio_started"
        and str(row.get("run_id") or "") == run_id,
    )

    mic_audio = meeting_mic_audio_path(logs_root, run_id, history, recording_event)
    system_audio = meeting_system_audio_path(mic_audio, system_started_event)
    transcript_path = mic_audio.with_suffix(".txt") if mic_audio else logs_root / "meeting_audio" / f"{run_id}.txt"
    transcript_text = transcript_text_for_run(history, transcript_path)

    mic_check = check_wav(mic_audio, args.min_duration_seconds) if mic_audio else missing_wav("")
    system_check = check_wav(system_audio, args.min_duration_seconds) if system_audio else missing_wav("")
    source_kinds = source_event.get("sources") if isinstance(source_event.get("sources"), list) else []

    checks = [
        named_check("meeting history row exists", bool(history), "latest history row with mode=meeting/status=meeting_transcribed"),
        named_check("meeting recording start event exists", bool(recording_event), "native recording_started mode=meeting"),
        named_check("mic wav exists and is usable", wav_ok(mic_check, args.min_duration_seconds), f"valid wav >= {args.min_duration_seconds}s and non-silent"),
        named_check("system wav exists and is usable", wav_ok(system_check, args.min_duration_seconds), f"valid wav >= {args.min_duration_seconds}s and non-silent"),
        named_check("source event includes system", "system" in source_kinds, "meeting_transcription_sources includes system"),
        named_check("source event includes mic", "mic" in source_kinds, "meeting_transcription_sources includes mic"),
        named_check("transcript exists", bool(transcript_text.strip()), "history or meeting_audio txt has transcript"),
        named_check("transcript keeps meeting audio label", "[Meeting audio]" in transcript_text, "combined transcript contains [Meeting audio]"),
        named_check("transcript keeps mic label", "[My mic]" in transcript_text, "combined transcript contains [My mic]"),
    ]

    payload: dict[str, Any] = {
        "ok": all(check["passed"] for check in checks),
        "run_id": run_id,
        "logs_root": str(logs_root),
        "mic_audio": asdict(mic_check),
        "system_audio": asdict(system_check),
        "transcript_path": str(transcript_path),
        "transcript_preview": transcript_text[:500],
        "native_source_event": source_event,
        "history_status": history.get("status") or "",
        "history_target_app": history.get("target_app") or {},
        "checks": checks,
        "next_action_if_failed": "Start a real Zoom/Teams/Meet call or provider test call, choose RambleFix menu -> Record Meeting, speak once yourself, play/let remote audio speak, then Stop Meeting Recording and rerun this verifier.",
    }
    emit(payload, args.json)
    raise SystemExit(0 if payload["ok"] else 1)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def latest_meeting_run_id(history_rows: list[dict[str, Any]], event_rows: list[dict[str, Any]]) -> str:
    for row in reversed(history_rows):
        if row.get("mode") == "meeting" or str(row.get("status") or "").startswith("meeting_"):
            run_id = str(row.get("run_id") or "")
            if run_id:
                return run_id
    for row in reversed(event_rows):
        if row.get("event") == "recording_started" and row.get("mode") == "meeting":
            run_id = str(row.get("run_id") or "")
            if run_id:
                return run_id
    return ""


def latest_row(rows: list[dict[str, Any]], predicate: Any) -> dict[str, Any]:
    for row in reversed(rows):
        if predicate(row):
            return row
    return {}


def meeting_mic_audio_path(logs_root: Path, run_id: str, history: dict[str, Any], recording_event: dict[str, Any]) -> Path | None:
    for raw in (history.get("audio_path"), recording_event.get("audio_path")):
        if raw:
            return Path(str(raw)).expanduser()
    candidate = logs_root / "meeting_audio" / f"{run_id}.wav"
    return candidate


def meeting_system_audio_path(mic_audio: Path | None, system_started_event: dict[str, Any]) -> Path | None:
    if system_started_event.get("audio_path"):
        return Path(str(system_started_event["audio_path"])).expanduser()
    if mic_audio:
        return mic_audio.with_suffix(".system.wav")
    return None


def transcript_text_for_run(history: dict[str, Any], transcript_path: Path) -> str:
    for key in ("corrected_text", "pasted_text", "raw_text"):
        text = str(history.get(key) or "").strip()
        if text:
            return text
    if transcript_path.exists():
        return transcript_path.read_text(encoding="utf-8", errors="ignore")
    return ""


def check_wav(path: Path, min_duration_seconds: float) -> WavCheck:
    if not path.exists():
        return WavCheck(str(path), False, False, 0.0, None, None, None, "missing")
    try:
        with wave.open(str(path), "rb") as reader:
            rate = reader.getframerate()
            duration = reader.getnframes() / float(rate) if rate else 0.0
        metrics = wav_silence_metrics(path)
        return WavCheck(
            path=str(path),
            exists=True,
            valid_wav=True,
            duration_seconds=round(duration, 3),
            audio_probably_silent=bool(metrics.get("audio_probably_silent")),
            audio_peak=float(metrics.get("audio_peak") or 0.0),
            audio_rms_max=float(metrics.get("audio_rms_max") or 0.0),
            error="" if duration >= min_duration_seconds else f"too_short:{duration:.3f}",
        )
    except Exception as exc:  # noqa: BLE001
        return WavCheck(str(path), True, False, 0.0, None, None, None, f"{type(exc).__name__}: {exc}")


def missing_wav(path: str) -> WavCheck:
    return WavCheck(path, False, False, 0.0, None, None, None, "missing")


def wav_ok(check: WavCheck, min_duration_seconds: float) -> bool:
    return check.exists and check.valid_wav and check.duration_seconds >= min_duration_seconds and check.audio_probably_silent is False


def named_check(name: str, passed: bool, expected: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "expected": expected}


def failure_payload(code: str, message: str, *, logs_root: Path) -> dict[str, Any]:
    return {
        "ok": False,
        "error": code,
        "message": message,
        "logs_root": str(logs_root),
        "checks": [],
    }


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
