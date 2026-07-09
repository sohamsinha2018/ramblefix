from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ramblefix.asr import ACCURATE_MLX_MODEL, BALANCED_MLX_MODEL, FAST_MLX_MODEL, transcribe_audio
from ramblefix.corpus import load_corpus, save_corpus


DEFAULT_LUDO_METRICS_DIR = Path("~/Downloads")


def run_ludo_local_eval(
    *,
    metrics_dir: str | Path = DEFAULT_LUDO_METRICS_DIR,
    output_dir: str | Path = "eval_runs/ludo-local",
    limit: int = 10,
    since: str = "",
    preset: str = "accurate",
    language: str | None = None,
    progress: bool = False,
    resume: bool = True,
) -> list[dict[str, Any]]:
    """Extract Ludo saved mic clips and run local MLX Whisper on them.

    Ludo's metrics files contain per-message microphone clips in `mic_audio_b64`.
    Those are much cleaner ASR inputs than the full session recordings, which
    usually contain mixed tab/screen audio.
    """
    out = Path(output_dir).expanduser().resolve()
    clips_dir = out / "clips"
    wav_dir = out / "wav"
    clips_dir.mkdir(parents=True, exist_ok=True)
    wav_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out / "results.jsonl"
    if not resume and jsonl_path.exists():
        jsonl_path.unlink()
    cached = _load_jsonl(jsonl_path) if resume else {}

    rows = collect_ludo_mic_rows(find_ludo_metric_files(metrics_dir))
    if since:
        rows = [row for row in rows if since in str(row["metrics_file"]) or since in str(row["id"])]
    if limit > 0:
        rows = rows[-limit:]

    model = _model_for_preset(preset)
    results: list[dict[str, Any]] = []
    total = len(rows)
    for index, row in enumerate(rows, start=1):
        cached_row = cached.get(str(row["id"]))
        if cached_row:
            results.append(cached_row)
            if progress:
                print(_progress_line(index, total, cached_row, cached=True), flush=True)
            continue
        webm = _write_clip(clips_dir, row)
        wav = wav_dir / f"{row['id']}.wav"
        started = time.perf_counter()
        try:
            _convert_to_wav(webm, wav)
            transcript = transcribe_audio(wav, model=model, language=language)
            text = transcript.text
            error = None
            engine = transcript.engine
            detected_language = transcript.language
        except Exception as exc:
            text = ""
            error = str(exc)
            engine = f"mlx-whisper:{model}"
            detected_language = None
        result = {
            "id": row["id"],
            "metrics_file": str(row["metrics_file"]),
            "eid": row.get("eid"),
            "quality": row.get("quality"),
            "old_heard_reference": row.get("old_heard") or "",
            "old_source": row.get("old_source"),
            "local_text": text,
            "reference_overlap": _token_overlap(row.get("old_heard") or "", text),
            "language": detected_language,
            "engine": engine,
            "seconds": round(time.perf_counter() - started, 3),
            "webm": str(webm),
            "wav": str(wav),
            "error": error,
        }
        results.append(result)
        _append_jsonl(jsonl_path, result)
        if progress:
            print(_progress_line(index, total, result), flush=True)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "metrics_dir": str(Path(metrics_dir).expanduser()),
        "rows": len(results),
        "preset": preset,
        "language": language,
        "note": "old_heard_reference is Ludo's historical selected transcript, not human gold.",
        "results": results,
    }
    (out / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "results.md").write_text(_markdown(payload), encoding="utf-8")
    return results


def find_ludo_metric_files(metrics_dir: str | Path = DEFAULT_LUDO_METRICS_DIR) -> list[Path]:
    root = Path(metrics_dir).expanduser()
    if not root.exists():
        return []
    files = sorted(root.glob("ludo_metrics_*.json"))
    return [path for path in files if _contains_mic_audio(path)]


def build_ludo_review_set(
    *,
    results_path: str | Path = "eval_runs/ludo-local-all/results.json",
    output_dir: str | Path = "eval_runs/ludo-review-set",
    corpus_path: str | Path = "eval_corpus/ramblefix_corpus.json",
    recordings_dir: str | Path = "recordings/ludo-review",
    count: int = 50,
    write_corpus: bool = True,
) -> list[dict[str, Any]]:
    results_file = Path(results_path).expanduser().resolve()
    data = json.loads(results_file.read_text(encoding="utf-8"))
    rows = list(data.get("results") or [])

    selected = _select_review_rows(rows, count=count)
    project_root = Path(corpus_path).expanduser().resolve().parent.parent
    recordings_root = Path(recordings_dir).expanduser()
    if not recordings_root.is_absolute():
        recordings_root = project_root / recordings_root
    recordings_root.mkdir(parents=True, exist_ok=True)

    corpus_items = load_corpus(corpus_path)
    by_id = {str(item.get("id")): item for item in corpus_items}
    review_rows: list[dict[str, Any]] = []
    for row in selected:
        corpus_id = _ludo_corpus_id(str(row["id"]))
        source_wav = Path(str(row.get("wav") or "")).expanduser()
        if not source_wav.is_absolute():
            source_wav = (results_file.parent / source_wav).resolve()
        target_wav = recordings_root / f"{corpus_id}.wav"
        if source_wav.exists() and not target_wav.exists():
            shutil.copy2(source_wav, target_wav)

        rel_audio = _relative_to_project(target_wav, project_root)
        corpus_item = {
            "id": corpus_id,
            "audio": rel_audio,
            "gold": "",
            "source": "ludo_metrics",
            "workflow": "Ludo Review Set",
            "notes": f"Review bucket: {row['review_bucket']}. Old Ludo reference is weak, not human gold.",
            "category": "ludo_hinglish",
            "ludo_id": row["id"],
            "review_bucket": row["review_bucket"],
            "benchmarks": {
                "ludo_old_reference": row.get("old_heard_reference") or "",
                "local_accurate_hi": row.get("local_text") or "",
            },
        }
        if write_corpus:
            existing = by_id.get(corpus_id)
            if existing:
                existing.setdefault("benchmarks", {}).update(corpus_item["benchmarks"])
                existing["review_bucket"] = corpus_item["review_bucket"]
                existing["ludo_id"] = corpus_item["ludo_id"]
                existing["notes"] = corpus_item["notes"]
                existing["category"] = corpus_item["category"]
            else:
                corpus_items.append(corpus_item)
                by_id[corpus_id] = corpus_item
        review_rows.append({**row, "corpus_id": corpus_id, "review_wav": str(target_wav), "corpus_audio": rel_audio})

    if write_corpus:
        save_corpus(corpus_items, corpus_path)

    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_results": str(results_file),
        "count": len(review_rows),
        "write_corpus": write_corpus,
        "corpus_path": str(Path(corpus_path).expanduser()),
        "recordings_dir": str(recordings_root),
        "rows": review_rows,
    }
    (out / "review_set.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "review_set.md").write_text(_review_markdown(payload), encoding="utf-8")
    return review_rows


def collect_ludo_mic_rows(files: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for file in files:
        try:
            metrics = json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(metrics, list):
            continue
        for item in metrics:
            if not isinstance(item, dict):
                continue
            if item.get("evt") != "conversationalReply" or not item.get("mic_audio_b64"):
                continue
            rows.append(
                {
                    "id": f"{file.stem}__{item.get('eid')}",
                    "metrics_file": file,
                    "eid": item.get("eid"),
                    "old_heard": _clean(item.get("heard")),
                    "old_source": item.get("transcript_source"),
                    "quality": item.get("mic_audio_quality_status"),
                    "mic_audio_b64": item.get("mic_audio_b64"),
                    "mic_audio_mime": item.get("mic_audio_mime") or "audio/webm",
                }
            )
    return rows


def _contains_mic_audio(path: Path) -> bool:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return isinstance(rows, list) and any(isinstance(row, dict) and row.get("mic_audio_b64") for row in rows)


def _write_clip(clips_dir: Path, row: dict[str, Any]) -> Path:
    path = clips_dir / f"{row['id']}.webm"
    if not path.exists():
        path.write_bytes(base64.b64decode(str(row["mic_audio_b64"])))
    return path


def _convert_to_wav(source: Path, target: Path) -> None:
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-ar", "16000", "-ac", "1", str(target)],
        check=True,
        text=True,
        capture_output=True,
    )


def _load_jsonl(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("id"):
            rows[str(row["id"])] = row
    return rows


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _progress_line(index: int, total: int, row: dict[str, Any], *, cached: bool = False) -> str:
    overlap = "n/a" if row["reference_overlap"] is None else f"{float(row['reference_overlap']):.3f}"
    status = "cached" if cached else "done"
    text = _clean(row.get("local_text") or row.get("error") or "")
    if len(text) > 160:
        text = f"{text[:157]}..."
    return (
        f"{index}/{total} {status} {row['id']}: overlap={overlap} "
        f"lang={row.get('language') or 'unknown'} time={float(row['seconds']):.3f}s text={text}"
    )


def _select_review_rows(rows: list[dict[str, Any]], *, count: int) -> list[dict[str, Any]]:
    rows = [row for row in rows if row.get("wav") and row.get("local_text")]
    for row in rows:
        row["review_flags"] = _review_flags(row)

    buckets = [
        ("repeat_loop", lambda row: "repeat_loop" in row["review_flags"], 10),
        ("roman_ref_script_mismatch", lambda row: _script(row.get("old_heard_reference") or "") == "latin" and _script(row.get("local_text") or "") in {"devanagari", "mixed"}, 10),
        ("high_overlap", lambda row: _overlap(row) >= 0.85 and "repeat_loop" not in row["review_flags"], 10),
        ("medium_overlap", lambda row: 0.50 <= _overlap(row) < 0.85 and "repeat_loop" not in row["review_flags"], 12),
        ("low_overlap", lambda row: _overlap(row) < 0.50 and "repeat_loop" not in row["review_flags"], 12),
        ("slow_or_long", lambda row: float(row.get("seconds") or 0) >= 6.0 and "repeat_loop" not in row["review_flags"], 6),
    ]
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bucket, predicate, quota in buckets:
        candidates = [row for row in rows if str(row.get("id")) not in seen and predicate(row)]
        for row in _even_sample(candidates, quota):
            copied = dict(row)
            copied["review_bucket"] = bucket
            selected.append(copied)
            seen.add(str(row["id"]))
            if len(selected) >= count:
                return selected

    remaining = [row for row in rows if str(row.get("id")) not in seen]
    for row in _even_sample(remaining, count - len(selected)):
        copied = dict(row)
        copied["review_bucket"] = "filler"
        selected.append(copied)
    return selected[:count]


def _review_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if _repeat_token_ratio(str(row.get("local_text") or "")) >= 0.35 and len(_tokens(str(row.get("local_text") or ""))) >= 12:
        flags.append("repeat_loop")
    if _script(row.get("old_heard_reference") or "") != _script(row.get("local_text") or ""):
        flags.append("script_mismatch")
    if _overlap(row) < 0.50:
        flags.append("low_overlap")
    if float(row.get("seconds") or 0) >= 6.0:
        flags.append("slow")
    return flags


def _even_sample(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not rows:
        return []
    rows = sorted(rows, key=lambda row: str(row.get("id") or ""))
    if len(rows) <= limit:
        return rows
    if limit == 1:
        return [rows[len(rows) // 2]]
    indexes = [round(i * (len(rows) - 1) / (limit - 1)) for i in range(limit)]
    return [rows[index] for index in indexes]


def _overlap(row: dict[str, Any]) -> float:
    value = row.get("reference_overlap")
    return float(value) if value is not None else -1.0


def _repeat_token_ratio(text: str) -> float:
    tokens = _tokens(text)
    if not tokens:
        return 0.0
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return max(counts.values()) / len(tokens)


def _script(text: str) -> str:
    has_devanagari = bool(re.search(r"[\u0900-\u097F]", text))
    has_latin = bool(re.search(r"[A-Za-z]", text))
    if has_devanagari and has_latin:
        return "mixed"
    if has_devanagari:
        return "devanagari"
    if has_latin:
        return "latin"
    return "other"


def _ludo_corpus_id(ludo_id: str) -> str:
    match = re.match(r"ludo_metrics_(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})-\d+Z__(\d+)", ludo_id)
    if match:
        year, month, day, hour, minute, second, eid = match.groups()
        return f"ludo_{year}{month}{day}_{hour}{minute}{second}_{eid}"
    return "ludo_" + re.sub(r"[^a-zA-Z0-9]+", "_", ludo_id).strip("_").lower()


def _relative_to_project(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _review_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Ludo Review Set",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Count: `{payload['count']}`",
        f"- Source results: `{payload['source_results']}`",
        f"- Corpus path: `{payload['corpus_path']}`",
        "",
        "| Corpus ID | Bucket | Overlap | Seconds | Flags | Audio | Old Ludo Reference | Local Accurate Hindi |",
        "| --- | --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for row in payload["rows"]:
        overlap = "" if row.get("reference_overlap") is None else f"{float(row['reference_overlap']):.3f}"
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_md(str(row["corpus_id"])),
                    _escape_md(str(row["review_bucket"])),
                    overlap,
                    f"{float(row.get('seconds') or 0):.3f}",
                    _escape_md(",".join(row.get("review_flags") or [])),
                    _escape_md(str(row["corpus_audio"])),
                    _escape_md(str(row.get("old_heard_reference") or "")),
                    _escape_md(str(row.get("local_text") or "")),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _model_for_preset(preset: str) -> str:
    if preset == "fast":
        return FAST_MLX_MODEL
    if preset == "balanced":
        return BALANCED_MLX_MODEL
    if preset == "accurate":
        return ACCURATE_MLX_MODEL
    return preset


def _token_overlap(reference: str, actual: str) -> float | None:
    ref = set(_tokens(reference))
    got = set(_tokens(actual))
    if not ref or not got:
        return None
    return round(len(ref & got) / max(len(ref), len(got)), 3)


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE).split()


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Ludo Local ASR Eval",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Rows: `{payload['rows']}`",
        f"- Preset: `{payload['preset']}`",
        f"- Forced language: `{payload['language'] or 'auto'}`",
        "- Reference note: `old_heard_reference` is Ludo's historical selected transcript, not human gold.",
        "",
        "| ID | Seconds | Lang | Reference overlap | Old Ludo reference | Local text |",
        "| --- | ---: | --- | ---: | --- | --- |",
    ]
    for row in payload["results"]:
        overlap = "" if row["reference_overlap"] is None else f"{row['reference_overlap']:.3f}"
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_md(str(row["id"])),
                    f"{float(row['seconds']):.3f}",
                    _escape_md(str(row["language"] or "")),
                    overlap,
                    _escape_md(str(row["old_heard_reference"])),
                    _escape_md(str(row["local_text"] or row["error"] or "")),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _escape_md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()
