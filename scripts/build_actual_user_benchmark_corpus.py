#!/usr/bin/env python3
"""Build the real-user benchmark corpus from retained RambleFix recordings."""

from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "eval_corpus/actual_user_english_hinglish_benchmark_20260705.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--english-limit", type=int, default=12)
    parser.add_argument("--hinglish-limit", type=int, default=8)
    parser.add_argument("--max-audio-seconds", type=float, default=60.0)
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    english_rows, english_hinglish_rows = load_english(limit=args.english_limit, max_audio_seconds=args.max_audio_seconds)
    rows.extend(english_rows)
    rows.extend(load_hinglish(limit=args.hinglish_limit, max_audio_seconds=args.max_audio_seconds, extra_rows=english_hinglish_rows))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)
    print(f"rows={len(rows)} english={sum(1 for r in rows if r['category'] == 'real_use_english_dictation')} hinglish={sum(1 for r in rows if r['category'] == 'real_use_hindi_hinglish_probe')}")


def load_english(*, limit: int, max_audio_seconds: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = ROOT / "eval_corpus/english_real_use_cloud_consensus_clean_20260628.json"
    source_rows = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    hinglish_out: list[dict[str, Any]] = []
    accepted_status = {"cloud_confirmed_offline", "cloud_replaces_offline", "cloud_supports_review"}
    for row in source_rows:
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        if meta.get("gold_status") not in accepted_status:
            continue
        if audio_seconds(row) > max_audio_seconds:
            continue
        category = "real_use_hindi_hinglish_probe" if has_roman_hindi_marker(str(row.get("gold") or "")) else "real_use_english_dictation"
        item = normalize_item(row, source_file=path.name, category=category)
        item["reference_level"] = "gold_variant"
        item["reference_trust"] = str(meta.get("gold_status") or "")
        item["gold_variants"] = gold_variants(
            [
                ("primary", row.get("gold")),
                ("offline_gold", meta.get("offline_gold")),
                ("gemini_flash", meta.get("gemini_flash")),
                ("gemini_pro", meta.get("gemini_pro")),
            ]
        )
        if category == "real_use_hindi_hinglish_probe":
            item["reference_trust"] = f"{item['reference_trust']}; recategorized_hinglish"
            hinglish_out.append(item)
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out, hinglish_out


def load_hinglish(*, limit: int, max_audio_seconds: float, extra_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = ROOT / "eval_corpus/latest_8_hindi_probe_cloud_checked_20260629.json"
    source_rows = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = list(extra_rows)
    for row in source_rows:
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        if audio_seconds(row) > max_audio_seconds:
            continue
        item = normalize_item(row, source_file=path.name, category="real_use_hindi_hinglish_probe")
        item["reference_level"] = "gold_variant"
        item["reference_trust"] = str(meta.get("cloud_asr_reason") or meta.get("gold_status") or "")
        item["gold_variants"] = gold_variants(
            [
                ("primary", row.get("gold")),
                ("offline_gold", meta.get("offline_gold")),
                ("cloud_gold", meta.get("cloud_gold")),
            ]
        )
        item["gold_disputed"] = "disagree" in item["reference_trust"].lower()
        item["audio_seconds"] = meta.get("audio_seconds")
        out.append(item)
        if len(out) >= limit:
            break
    return out


def normalize_item(row: dict[str, Any], *, source_file: str, category: str) -> dict[str, Any]:
    audio_value = str(resolve_audio_path(row))
    if not Path(audio_value).exists():
        raise FileNotFoundError(audio_value)
    terms = row.get("critical") or row.get("critical_terms") or row.get("terms") or []
    return {
        "id": str(row["id"]),
        "audio": audio_value,
        "gold": str(row.get("gold") or "").strip(),
        "category": category,
        "language": "English" if category == "real_use_english_dictation" else "Hindi+English",
        "critical_terms": [str(term) for term in terms if str(term).strip()],
        "terms": [str(term) for term in terms if str(term).strip()],
        "source_corpus": source_file,
        "source": "ramblefix_retained_hotkey_audio",
        "audio_seconds": audio_seconds(row),
        "notes": "Actual RambleFix-recorded clip; score with gold_variants for quote-safe review.",
    }


def audio_seconds(row: dict[str, Any]) -> float:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    for value in [meta.get("audio_seconds"), meta.get("duration_seconds"), row.get("audio_seconds"), row.get("duration_seconds")]:
        try:
            if value is not None and float(value) > 0:
                return float(value)
        except (TypeError, ValueError):
            pass
    audio = resolve_audio_path(row)
    try:
        with wave.open(str(audio), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())
    except (wave.Error, FileNotFoundError, ZeroDivisionError):
        return 0.0


def resolve_audio_path(row: dict[str, Any]) -> Path:
    audio = Path(str(row.get("audio") or ""))
    return audio if audio.is_absolute() else (ROOT / audio).resolve()
    return 0.0


def has_roman_hindi_marker(text: str) -> bool:
    markers = {
        "haan", "yaar", "kya", "karein", "bata", "tu", "kaise", "nahi", "nahin",
        "agar", "phir", "thik", "matlab", "iske", "baad", "hai", "hain",
    }
    words = {word.lower() for word in __import__("re").findall(r"[A-Za-z]+", text)}
    return len(words & markers) >= 2


def gold_variants(values: list[tuple[str, Any]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    variants: list[dict[str, str]] = []
    for label, value in values:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        variants.append({"source": label, "text": text})
    return variants


if __name__ == "__main__":
    main()
