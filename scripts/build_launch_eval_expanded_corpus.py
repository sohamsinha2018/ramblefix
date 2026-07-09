#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import wave
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHORT = ROOT / "eval_corpus/actual_user_english_hinglish_benchmark_20260705.json"
DEFAULT_HISTORY = ROOT / "logs/history.jsonl"
DEFAULT_OUTPUT = ROOT / "eval_corpus/launch_real_use_expanded_20260709_draft.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build launch eval corpus with short gold rows and longer retained clips.")
    parser.add_argument("--short-source", type=Path, default=DEFAULT_SHORT)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--short-english", type=int, default=10)
    parser.add_argument("--short-hinglish", type=int, default=8)
    parser.add_argument("--long-english", type=int, default=4)
    parser.add_argument("--long-hinglish", type=int, default=2)
    parser.add_argument("--long-min-seconds", type=float, default=60.0)
    parser.add_argument("--long-max-seconds", type=float, default=120.0)
    args = parser.parse_args()

    short_rows = load_short_rows(args.short_source, english=args.short_english, hinglish=args.short_hinglish)
    long_rows = load_long_rows(
        args.history,
        english=args.long_english,
        hinglish=args.long_hinglish,
        min_seconds=args.long_min_seconds,
        max_seconds=args.long_max_seconds,
    )
    rows = short_rows + long_rows
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)
    print(
        "rows="
        f"{len(rows)} short={len(short_rows)} long={len(long_rows)} "
        f"english={sum(1 for row in rows if row['language'] == 'English')} "
        f"hinglish={sum(1 for row in rows if row['language'] == 'Hindi+English')}"
    )


def load_short_rows(path: Path, *, english: int, hinglish: int) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    english_rows: list[dict[str, Any]] = []
    hinglish_rows: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        audio = resolve_audio(item.get("audio"))
        if not audio.is_file():
            continue
        item["audio"] = str(audio)
        item["duration_bucket"] = "lt60"
        item["eval_claim_grade"] = item.get("reference_level") in {"gold", "gold_variant"}
        if item.get("language") == "Hindi+English" and len(hinglish_rows) < hinglish:
            hinglish_rows.append(item)
        elif item.get("language") == "English" and len(english_rows) < english:
            english_rows.append(item)
        if len(english_rows) >= english and len(hinglish_rows) >= hinglish:
            break
    return english_rows + hinglish_rows


def load_long_rows(
    path: Path,
    *,
    english: int,
    hinglish: int,
    min_seconds: float,
    max_seconds: float,
) -> list[dict[str, Any]]:
    by_run: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        run_id = str(row.get("run_id") or "")
        audio = resolve_audio(row.get("audio_path") or row.get("audio"))
        if not run_id or not audio.is_file():
            continue
        if str(row.get("route") or "") == "structure":
            continue
        if row.get("status") not in {"paste_attempted", "copy_fallback_shown", "structure_saved"}:
            continue
        duration = audio_seconds(audio)
        if duration < min_seconds or duration > max_seconds:
            continue
        text = normalize_spaces(str(row.get("raw_text") or row.get("pasted_text") or row.get("corrected_text") or ""))
        if len(text.split()) < 12:
            continue
        by_run.setdefault(run_id, {**row, "_audio": str(audio), "_duration": duration, "_draft_gold": text})

    english_rows: list[dict[str, Any]] = []
    hinglish_rows: list[dict[str, Any]] = []
    for row in sorted(by_run.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True):
        text = str(row["_draft_gold"])
        language = "Hindi+English" if looks_hinglish(text) else "English"
        item = {
            "id": f"long_{row['run_id']}",
            "audio": row["_audio"],
            "gold": text,
            "category": "real_use_hindi_hinglish_probe_long" if language == "Hindi+English" else "real_use_english_dictation_long",
            "language": language,
            "critical_terms": extract_terms(text),
            "terms": extract_terms(text),
            "source": "ramblefix_retained_hotkey_audio",
            "source_corpus": str(path),
            "audio_seconds": round(float(row["_duration"]), 3),
            "duration_bucket": "60_120",
            "reference_level": "draft_needs_cloud_check",
            "reference_trust": "draft_from_existing_ramblefix_output_needs_cloud_asr",
            "eval_claim_grade": False,
            "notes": "Long retained clip. Gold is a draft seed until cloud ASR confirms/replaces it.",
        }
        if language == "Hindi+English" and len(hinglish_rows) < hinglish:
            hinglish_rows.append(item)
        elif language == "English" and len(english_rows) < english:
            english_rows.append(item)
        if len(english_rows) >= english and len(hinglish_rows) >= hinglish:
            break
    return english_rows + hinglish_rows


def resolve_audio(value: object) -> Path:
    path = Path(str(value or "")).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def audio_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())
    except (wave.Error, FileNotFoundError, ZeroDivisionError):
        return 0.0


def looks_hinglish(text: str) -> bool:
    if re.search(r"[\u0900-\u097f]", text):
        return True
    markers = {
        "agar",
        "hai",
        "hain",
        "hindi",
        "hinglish",
        "kya",
        "matlab",
        "nahi",
        "nahin",
        "phir",
        "thik",
        "yaar",
    }
    words = {word.lower() for word in re.findall(r"[A-Za-z]+", text)}
    return len(words & markers) >= 2


def extract_terms(text: str) -> list[str]:
    terms: set[str] = set()
    for match in re.finditer(r"\b[A-Z][A-Z0-9]{1,9}\b|\b[A-Za-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b", text):
        raw = match.group(0)
        if raw.lower() not in {"ok", "yes", "no"}:
            terms.add(raw)
    for term in ["RambleFix", "MCP", "UX", "ASR", "STT", "Google", "Codex", "Wispr Flow"]:
        if re.search(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE):
            terms.add(term)
    return sorted(terms, key=lambda value: value.lower())


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


if __name__ == "__main__":
    main()
