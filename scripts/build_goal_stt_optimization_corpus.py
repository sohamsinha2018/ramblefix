from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "eval_runs" / "goal-stt-optimization"

HINGLISH_MARKERS = {
    "bhai",
    "yaar",
    "kya",
    "nahi",
    "nahin",
    "matlab",
    "haan",
    "hai",
    "hain",
    "toh",
    "karna",
    "karo",
    "chahiye",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the goal-mode STT optimization corpus and cloud-gold queue."
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-english", type=int, default=20)
    parser.add_argument("--max-real-hindi-probe", type=int, default=8)
    parser.add_argument("--max-public-hinglish", type=int, default=30)
    parser.add_argument("--max-history-candidates", type=int, default=20)
    parser.add_argument(
        "--max-latest-history",
        type=int,
        default=0,
        help="Also include the latest valid retained hotkey clips even if they do not match work-term heuristics.",
    )
    parser.add_argument("--history", type=Path, default=ROOT / "logs/history.jsonl")
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    rows.extend(
        _from_existing_corpus(
            ROOT / "eval_corpus/english_real_use_cloud_asr_checked_20260628.json",
            bucket="english_only",
            source="real_use_english_existing",
            limit=args.max_english,
            trust_default="offline_consensus",
        )
    )
    rows.extend(
        _from_existing_corpus(
            ROOT / "eval_corpus/latest_8_hindi_probe_cloud_checked_20260629.json",
            bucket=None,
            source="real_use_recent_probe_existing",
            limit=args.max_real_hindi_probe,
            trust_default="cloud_or_offline_mixed",
        )
    )
    rows.extend(
        _from_existing_corpus(
            ROOT / "eval_corpus/public_launch_openslr104_hinglish_50_20260614.json",
            bucket="hindi_english",
            source="public_openslr104_hinglish",
            limit=args.max_public_hinglish,
            trust_default="public_silver",
        )
    )
    rows.extend(_history_candidates(args.history, limit=args.max_history_candidates))
    rows.extend(_latest_history_candidates(args.history, limit=args.max_latest_history))

    rows = _dedupe(rows)
    english_rows = [row for row in rows if row["bucket"] == "english_only"]
    mixed_rows = [row for row in rows if row["bucket"] == "hindi_english"]
    english_eval_rows = [row for row in english_rows if _is_eval_ready(row)]
    mixed_eval_rows = [row for row in mixed_rows if _is_eval_ready(row)]
    queue_rows = [row for row in rows if row["cloud_status"] not in {"cloud_confirmed", "public_silver"}]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    all_path = args.output_dir / "goal_stt_corpus_all.json"
    english_path = args.output_dir / "goal_stt_corpus_english.json"
    mixed_path = args.output_dir / "goal_stt_corpus_hindi_english.json"
    english_eval_path = args.output_dir / "goal_stt_corpus_english_eval_ready.json"
    mixed_eval_path = args.output_dir / "goal_stt_corpus_hindi_english_eval_ready.json"
    queue_path = args.output_dir / "cloud_gold_queue.json"
    readiness_path = args.output_dir / "cloud_readiness.json"
    summary_path = args.output_dir / "summary.md"

    _write_json(all_path, rows)
    _write_json(english_path, english_rows)
    _write_json(mixed_path, mixed_rows)
    _write_json(english_eval_path, english_eval_rows)
    _write_json(mixed_eval_path, mixed_eval_rows)
    _write_json(queue_path, queue_rows)
    readiness = _cloud_readiness()
    _write_json(readiness_path, readiness)
    summary_path.write_text(
        _summary_markdown(rows, english_rows, mixed_rows, queue_rows, readiness),
        encoding="utf-8",
    )

    print(json.dumps(
        {
            "all": str(all_path),
            "english": str(english_path),
            "hindi_english": str(mixed_path),
            "english_eval_ready": str(english_eval_path),
            "hindi_english_eval_ready": str(mixed_eval_path),
            "cloud_gold_queue": str(queue_path),
            "cloud_readiness": readiness,
            "summary": str(summary_path),
            "counts": _counts(rows),
        },
        ensure_ascii=False,
        indent=2,
    ))


def _from_existing_corpus(
    path: Path,
    *,
    bucket: str | None,
    source: str,
    limit: int,
    trust_default: str,
) -> list[dict[str, Any]]:
    if limit <= 0 or not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for item in data[:limit]:
        audio = _resolve_audio(item.get("audio"))
        if not audio.exists():
            continue
        gold = str(item.get("gold") or "").strip()
        if not gold:
            continue
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        cloud_status = _cloud_status_from_meta(meta, trust_default)
        final_bucket = bucket or _bucket_from_text(gold, category=str(item.get("category") or ""))
        rows.append(
            {
                "id": str(item.get("id") or audio.stem),
                "bucket": final_bucket,
                "category": str(item.get("category") or final_bucket),
                "audio": str(audio),
                "gold": gold,
                "critical": _critical_terms(item, gold),
                "source": source,
                "cloud_status": cloud_status,
                "classification_status": "trusted" if cloud_status in {"cloud_confirmed", "public_silver"} else "needs_cloud_classification",
                "classification_reason": _classification_reason(item, final_bucket, cloud_status),
                "meta": {
                    **meta,
                    "source_corpus": str(path.relative_to(ROOT)),
                    "original_source": item.get("source"),
                    "language": item.get("language"),
                },
            }
        )
    return rows


def _history_candidates(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not path.exists():
        return []
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        audio = _resolve_audio(row.get("audio_path"))
        if not _usable_history_audio(audio, row):
            continue
        text = _row_text(row)
        if not _usable_history_text(text):
            continue
        bucket = _bucket_from_text(text, category=str(row.get("mode") or ""))
        interesting = bucket == "hindi_english" or _looks_work_relevant(text)
        if not interesting:
            continue
        rows.append(
            {
                "id": f"history_{line_no}_{row.get('run_id') or audio.stem}",
                "bucket": bucket,
                "category": "history_candidate",
                "audio": str(audio),
                "gold": text,
                "critical": _extract_terms(text),
                "source": "retained_history_candidate",
                "cloud_status": "needs_cloud_gold",
                "classification_status": "needs_cloud_classification",
                "classification_reason": "Draft gold from local history only; must be cloud or human confirmed before launch claims.",
                "meta": {
                    "history_line": line_no,
                    "run_id": row.get("run_id"),
                    "created_at": row.get("created_at"),
                    "status": row.get("status"),
                    "route": row.get("route"),
                    "asr_engine": row.get("asr_engine"),
                    "release_to_paste_seconds": _release_to_paste(row),
                },
            }
        )
    return rows[-limit:]


def _latest_history_candidates(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not path.exists():
        return []
    candidates: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        audio = _resolve_audio(row.get("audio_path"))
        if not _usable_history_audio(audio, row):
            continue
        text = _row_text(row)
        if not _usable_history_text(text):
            continue
        bucket = _bucket_from_text(text, category=str(row.get("mode") or ""))
        candidates.append(
            {
                "id": f"latest_history_{line_no}_{row.get('run_id') or audio.stem}",
                "bucket": bucket,
                "category": "latest_history_candidate",
                "audio": str(audio),
                "gold": text,
                "critical": _extract_terms(text),
                "source": "retained_latest_history",
                "cloud_status": "needs_cloud_gold",
                "classification_status": "needs_cloud_classification",
                "classification_reason": "Recent retained dictation clip; must be cloud or human confirmed before launch claims.",
                "meta": {
                    "history_line": line_no,
                    "run_id": row.get("run_id"),
                    "created_at": row.get("created_at"),
                    "status": row.get("status"),
                    "route": row.get("route"),
                    "asr_engine": row.get("asr_engine"),
                    "release_to_paste_seconds": _release_to_paste(row),
                },
            }
        )
    return candidates[-limit:]


def _usable_history_audio(audio: Path, row: dict[str, Any]) -> bool:
    if not audio.is_file():
        return False
    status = str(row.get("status") or "").lower()
    error_type = str(row.get("error_type") or "").lower()
    if status in {"too_short", "no_speech"}:
        return False
    if error_type in {"too_short_capture", "blank_or_no_speech"}:
        return False
    return True


def _usable_history_text(text: str) -> bool:
    if len(text.split()) < 5:
        return False
    lowered = text.lower()
    if "asr failure detected" in lowered:
        return False
    if re.fullmatch(r"\[(?:blank_audio|no_speech|silence|noise|music|inaudible)\]", lowered.strip()):
        return False
    return True


def _cloud_status_from_meta(meta: dict[str, Any], trust_default: str) -> str:
    status = str(meta.get("gold_status") or "").lower()
    reason = str(meta.get("cloud_asr_reason") or "").lower()
    if "cloud" in status and "failed" not in status:
        return "cloud_confirmed"
    if "cloud asr models agree" in reason:
        return "cloud_confirmed"
    if trust_default == "public_silver":
        return "public_silver"
    return trust_default


def _is_eval_ready(row: dict[str, Any]) -> bool:
    return row["cloud_status"] in {"cloud_confirmed", "public_silver", "offline_consensus"}


def _classification_reason(item: dict[str, Any], bucket: str, cloud_status: str) -> str:
    if cloud_status == "cloud_confirmed":
        return f"Bucket `{bucket}` assigned from existing cloud-checked corpus metadata."
    if cloud_status == "public_silver":
        return f"Bucket `{bucket}` assigned from public corpus language metadata."
    return f"Bucket `{bucket}` is heuristic/offline and still needs cloud classification."


def _bucket_from_text(text: str, *, category: str = "") -> str:
    lowered_category = category.lower()
    if any(token in lowered_category for token in ["hinglish", "hindi", "hi-en"]):
        return "hindi_english"
    if _has_indic_or_arabic(text):
        return "hindi_english"
    tokens = set(re.findall(r"[A-Za-z]+", text.lower()))
    marker_hits = tokens & HINGLISH_MARKERS
    if len(marker_hits) >= 2:
        return "hindi_english"
    return "english_only"


def _critical_terms(item: dict[str, Any], gold: str) -> list[str]:
    for key in ("critical", "critical_terms", "terms", "anchors"):
        value = item.get(key)
        if isinstance(value, list):
            return [str(part) for part in value if str(part).strip()]
    return _extract_terms(gold)


def _extract_terms(text: str) -> list[str]:
    terms = set()
    for match in re.finditer(r"\b[A-Z][A-Z0-9]{1,9}\b|\b[A-Za-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b", text):
        raw = match.group(0).strip()
        if raw.lower() not in {"ok", "yes", "no"}:
            terms.add(raw)
    for term in ["RambleFix", "MCP", "UX", "ASR", "STT", "Codex", "Gemini", "OpenAI"]:
        if re.search(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE):
            terms.add(term)
    return sorted(terms, key=lambda value: value.lower())


def _row_text(row: dict[str, Any]) -> str:
    for key in ("corrected_text", "pasted_text", "raw_text"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return re.sub(r"\s+", " ", value).strip()
    return ""


def _release_to_paste(row: dict[str, Any]) -> float | None:
    timings = row.get("timings") if isinstance(row.get("timings"), dict) else {}
    value = timings.get("release_to_paste_seconds")
    return float(value) if isinstance(value, (int, float)) else None


def _looks_work_relevant(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in [
            "ramble",
            "codex",
            "mcp",
            "asr",
            "stt",
            "ux",
            "model",
            "pipeline",
            "latency",
            "benchmark",
            "cloud",
            "local",
        ]
    )


def _has_indic_or_arabic(text: str) -> bool:
    return any(
        (0x0900 <= ord(ch) <= 0x097F) or (0x0600 <= ord(ch) <= 0x06FF)
        for ch in text
    )


def _resolve_audio(value: Any) -> Path:
    path = Path(str(value or "")).expanduser()
    if path.is_absolute():
        return path
    return (ROOT / path).resolve()


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    rank = {"cloud_confirmed": 4, "public_silver": 3, "offline_consensus": 2, "cloud_or_offline_mixed": 2, "needs_cloud_gold": 1}
    for row in rows:
        key = (row["audio"], row["bucket"])
        existing = by_key.get(key)
        if existing is None or rank.get(row["cloud_status"], 0) > rank.get(existing["cloud_status"], 0):
            by_key[key] = row
    return sorted(by_key.values(), key=lambda row: (row["bucket"], row["source"], row["id"]))


def _cloud_readiness() -> dict[str, Any]:
    return {
        "openai_api_key": bool(os.environ.get("OPENAI_API_KEY")),
        "gemini_api_key": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
        "anthropic_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "note": "OpenAI/Gemini audio keys are required for fresh cloud ASR gold. Anthropic key alone is not used as an audio ASR source here.",
    }


def _counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(rows),
        "by_bucket": dict(Counter(row["bucket"] for row in rows)),
        "by_cloud_status": dict(Counter(row["cloud_status"] for row in rows)),
        "needs_cloud": sum(row["cloud_status"] not in {"cloud_confirmed", "public_silver"} for row in rows),
    }


def _summary_markdown(
    rows: list[dict[str, Any]],
    english_rows: list[dict[str, Any]],
    mixed_rows: list[dict[str, Any]],
    queue_rows: list[dict[str, Any]],
    readiness: dict[str, Any],
) -> str:
    counts = _counts(rows)
    return "\n".join(
        [
            "# Goal STT Optimization Corpus",
            "",
            f"- total rows: `{counts['total']}`",
            f"- English-only rows: `{len(english_rows)}`",
            f"- Hindi+English rows: `{len(mixed_rows)}`",
            f"- English eval-ready rows: `{sum(1 for row in english_rows if _is_eval_ready(row))}`",
            f"- Hindi+English eval-ready rows: `{sum(1 for row in mixed_rows if _is_eval_ready(row))}`",
            f"- rows needing fresh cloud classification/gold: `{len(queue_rows)}`",
            f"- cloud keys: OpenAI=`{readiness['openai_api_key']}`, Gemini=`{readiness['gemini_api_key']}`, Anthropic=`{readiness['anthropic_api_key']}`",
            "",
            "## Status Counts",
            "",
            f"- by bucket: `{counts['by_bucket']}`",
            f"- by cloud status: `{counts['by_cloud_status']}`",
            "",
            "Use `*_eval_ready.json` for same-WAV local bakeoffs. Use `cloud_gold_queue.json` for fresh OpenAI/Gemini audio gold when keys are available. The full bucket files include unconfirmed history candidates and must not be used for claims.",
            "",
        ]
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
