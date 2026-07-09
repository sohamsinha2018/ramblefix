from __future__ import annotations

import argparse
import json
import re
import wave
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Split long retained RambleFix recordings into <=30s cloud-gold queue clips.")
    parser.add_argument("--review-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--chunk-seconds", type=float, default=24.0)
    parser.add_argument("--overlap-seconds", type=float, default=1.0)
    parser.add_argument("--min-source-seconds", type=float, default=30.0)
    parser.add_argument("--min-chunk-seconds", type=float, default=4.0)
    parser.add_argument("--limit-chunks", type=int, default=12)
    parser.add_argument(
        "--prefer-keywords",
        default="hindi,english,local,model,metric,goal,corpus,asr,stt,mcp,skill,ux,regression,builder",
    )
    args = parser.parse_args()

    if args.chunk_seconds <= 0:
        raise SystemExit("--chunk-seconds must be positive")
    if args.overlap_seconds < 0 or args.overlap_seconds >= args.chunk_seconds:
        raise SystemExit("--overlap-seconds must be >=0 and < --chunk-seconds")

    rows = json.loads(args.review_json.read_text(encoding="utf-8"))
    keywords = [item.strip().lower() for item in args.prefer_keywords.split(",") if item.strip()]
    candidates = _rank_candidates(rows, keywords=keywords, min_source_seconds=args.min_source_seconds)

    chunk_dir = args.output_dir / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    queue: list[dict[str, Any]] = []
    for source in candidates:
        if args.limit_chunks and len(queue) >= args.limit_chunks:
            break
        audio = _resolve_audio(source.get("audio_abs") or source.get("audio"))
        if not audio.exists():
            continue
        for chunk in _split_wav(
            audio,
            chunk_dir=chunk_dir,
            source_id=str(source.get("id") or audio.stem),
            chunk_seconds=args.chunk_seconds,
            overlap_seconds=args.overlap_seconds,
            min_chunk_seconds=args.min_chunk_seconds,
        ):
            if args.limit_chunks and len(queue) >= args.limit_chunks:
                break
            queue.append(_queue_row(source, audio=audio, chunk=chunk))

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output_json),
                "chunks": len(queue),
                "sources_considered": len(candidates),
                "source_ids": sorted({row["meta"]["source_id"] for row in queue}),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def _rank_candidates(rows: list[dict[str, Any]], *, keywords: list[str], min_source_seconds: float) -> list[dict[str, Any]]:
    candidates = []
    for row in rows:
        if not row.get("representative"):
            continue
        duration = float(row.get("duration_seconds") or 0.0)
        if duration < min_source_seconds:
            continue
        text = str(row.get("product_text") or row.get("raw_text") or "")
        lowered = text.lower()
        keyword_score = sum(1 for keyword in keywords if re.search(rf"\b{re.escape(keyword)}\b", lowered))
        hindi_signal = sum(1 for keyword in ("hindi", "english", "local", "model", "goal", "metric") if keyword in lowered)
        candidates.append((hindi_signal * 5 + keyword_score, duration, row))
    return [row for _, _, row in sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)]


def _split_wav(
    audio: Path,
    *,
    chunk_dir: Path,
    source_id: str,
    chunk_seconds: float,
    overlap_seconds: float,
    min_chunk_seconds: float,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    safe_source_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_id).strip("_") or audio.stem
    with wave.open(str(audio), "rb") as reader:
        params = reader.getparams()
        frame_rate = reader.getframerate()
        total_frames = reader.getnframes()
        chunk_frames = max(1, int(chunk_seconds * frame_rate))
        overlap_frames = max(0, int(overlap_seconds * frame_rate))
        step_frames = max(1, chunk_frames - overlap_frames)
        start_frame = 0
        index = 0
        while start_frame < total_frames:
            remaining = total_frames - start_frame
            if remaining / frame_rate < min_chunk_seconds:
                break
            frames_to_read = min(chunk_frames, remaining)
            reader.setpos(start_frame)
            data = reader.readframes(frames_to_read)
            duration = frames_to_read / frame_rate
            start_seconds = start_frame / frame_rate
            end_seconds = start_seconds + duration
            out = chunk_dir / f"{safe_source_id}__chunk_{index:03d}_{start_seconds:.1f}-{end_seconds:.1f}.wav"
            with wave.open(str(out), "wb") as writer:
                writer.setparams(params)
                writer.writeframes(data)
            chunks.append(
                {
                    "path": out,
                    "index": index,
                    "start_seconds": round(start_seconds, 3),
                    "end_seconds": round(end_seconds, 3),
                    "duration_seconds": round(duration, 3),
                }
            )
            index += 1
            start_frame += step_frames
    return chunks


def _queue_row(source: dict[str, Any], *, audio: Path, chunk: dict[str, Any]) -> dict[str, Any]:
    source_id = str(source.get("id") or audio.stem)
    product_text = str(source.get("product_text") or "").strip()
    raw_text = str(source.get("raw_text") or "").strip()
    work_hits = source.get("work_keyword_hits") if isinstance(source.get("work_keyword_hits"), list) else []
    return {
        "id": f"seg_{source_id}_{chunk['index']:03d}",
        "bucket": "unknown_cloud_classify",
        "category": "segmented_real_use_le30",
        "audio": str(Path(chunk["path"]).resolve()),
        "gold": product_text or raw_text,
        "critical": work_hits,
        "source": "segmented_retained_hotkey_long_recording",
        "cloud_status": "needs_cloud_gold",
        "classification_status": "needs_cloud_classification",
        "classification_reason": "Derived <=30s chunk from a longer retained real-use recording; classify from audio.",
        "meta": {
            "source_id": source_id,
            "source_audio": str(audio.resolve()),
            "source_duration_seconds": source.get("duration_seconds"),
            "source_target_app": source.get("target_app"),
            "source_route": source.get("route"),
            "source_asr_engine": source.get("asr_engine"),
            "chunk_index": chunk["index"],
            "chunk_start_seconds": chunk["start_seconds"],
            "chunk_end_seconds": chunk["end_seconds"],
            "duration_seconds": chunk["duration_seconds"],
            "draft_product_text": product_text,
            "draft_raw_text": raw_text,
        },
    }


def _resolve_audio(value: object) -> Path:
    path = Path(str(value or "")).expanduser()
    if path.is_absolute():
        return path
    return (ROOT / path).resolve()


if __name__ == "__main__":
    main()
