from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

import sys

SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.hindi_chunk_polish import update_reject_reasons


DEFAULT_INPUT = ROOT / "eval_runs/fresh-hindi-probe-20260628/progressive_stream_4s_hindi10_final_20260628.json"
DEFAULT_OUTPUT = ROOT / "eval_runs/fresh-hindi-probe-20260628/oriserve_streaming_candidate_latest.json"
MODEL_ID = "Oriserve/Whisper-Hindi2Hinglish-Swift"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Oriserve as a local streaming Hindi/Hinglish polish candidate.")
    parser.add_argument("--stream-results", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--chunk-seconds", type=float, default=12.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--include-rejected", action="store_true")
    args = parser.parse_args()

    rows = _load_rows(args.stream_results, include_rejected=args.include_rejected)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        raise RuntimeError("No rows to evaluate")

    asr, device = _load_oriserve()
    scored = []
    with tempfile.TemporaryDirectory(prefix="ramblefix-oriserve-stream-") as tmp_value:
        tmp_root = Path(tmp_value)
        for index, row in enumerate(rows, 1):
            print(f"[{index}/{len(rows)}] {row['name']}", flush=True)
            scored.append(_score_row(row, asr=asr, tmp_root=tmp_root, chunk_seconds=args.chunk_seconds))

    payload = {"summary": _summary(scored, device=device, chunk_seconds=args.chunk_seconds), "rows": scored}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(_markdown(payload), encoding="utf-8")
    print(_markdown(payload), end="")
    print(f"wrote {args.output}")


def _load_rows(path: Path, *, include_rejected: bool) -> list[dict[str, Any]]:
    payload = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    rows = payload.get("rows", payload if isinstance(payload, list) else [])
    out = []
    for row in rows:
        finish = row.get("finish") or {}
        if not include_rejected and finish.get("route") != "hindi_stream_safe":
            continue
        audio = Path(str(row.get("audio") or "")).expanduser()
        if not audio.exists():
            continue
        out.append(row)
    return out


def _load_oriserve() -> tuple[Any, str]:
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    dtype = torch.float16 if device == "mps" else torch.float32
    started = time.perf_counter()
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        MODEL_ID,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
        use_safetensors=True,
        local_files_only=True,
    )
    if device == "mps":
        model.to("mps")
    processor = AutoProcessor.from_pretrained(MODEL_ID, local_files_only=True)
    asr = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        torch_dtype=dtype,
        device=device,
    )
    print(f"loaded {MODEL_ID} on {device} in {time.perf_counter() - started:.3f}s", flush=True)
    return asr, device


def _score_row(row: dict[str, Any], *, asr: Any, tmp_root: Path, chunk_seconds: float) -> dict[str, Any]:
    audio = Path(str(row["audio"])).expanduser().resolve()
    draft_text = str((row.get("fast") or {}).get("text") or "")
    chunk_dir = tmp_root / audio.stem
    chunks = _split_wav(audio, chunk_dir, chunk_seconds=chunk_seconds)
    parts: list[str] = []
    chunk_payloads: list[dict[str, Any]] = []
    compute_done = 0.0
    for chunk_index, (chunk_path, start_seconds, end_seconds, duration_seconds) in enumerate(chunks):
        if duration_seconds < 0.35:
            continue
        started = time.perf_counter()
        error = ""
        text = ""
        try:
            result = asr(str(chunk_path), generate_kwargs={"task": "transcribe"})
            text = str(result.get("text", "")).strip()
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        compute_seconds = round(time.perf_counter() - started, 3)
        compute_done = max(compute_done, end_seconds) + compute_seconds
        parts.append(text)
        chunk_payloads.append(
            {
                "index": chunk_index,
                "start_seconds": round(start_seconds, 3),
                "end_seconds": round(end_seconds, 3),
                "duration_seconds": round(duration_seconds, 3),
                "compute_seconds": compute_seconds,
                "text": text,
                "error": error,
            }
        )
    final_text = _stitch(parts)
    audio_seconds = _audio_seconds(audio)
    release_tail = round(max(0.0, compute_done - audio_seconds), 3)
    reject_reasons = update_reject_reasons(
        draft_text=draft_text,
        final_text=final_text,
        release_tail_seconds=release_tail,
        max_release_tail_seconds=3.0,
        allow_roman_hindi=True,
        strict_new_english=True,
    )
    safe_update = not reject_reasons
    print(
        f"  tail={release_tail:.3f}s safe={safe_update} reject={reject_reasons[:3]} text={_short(final_text)}",
        flush=True,
    )
    return {
        "name": row.get("name") or audio.name,
        "audio": str(audio),
        "audio_seconds": round(audio_seconds, 3),
        "draft_text": draft_text,
        "candidate_text": final_text,
        "release_tail_seconds": release_tail,
        "safe_update": safe_update,
        "reject_reasons": reject_reasons,
        "chunk_count": len(chunk_payloads),
        "chunks": chunk_payloads,
    }


def _split_wav(audio: Path, chunk_dir: Path, *, chunk_seconds: float) -> list[tuple[Path, float, float, float]]:
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunks = []
    cursor = 0.0
    with wave.open(str(audio), "rb") as reader:
        params = reader.getparams()
        frame_rate = reader.getframerate()
        chunk_frames = max(1, int(frame_rate * chunk_seconds))
        index = 0
        while True:
            data = reader.readframes(chunk_frames)
            if not data:
                break
            frames = len(data) // max(1, params.sampwidth * params.nchannels)
            duration = frames / frame_rate if frame_rate else 0.0
            path = chunk_dir / f"chunk-{index:03d}.wav"
            with wave.open(str(path), "wb") as writer:
                writer.setparams(params)
                writer.writeframes(data)
            chunks.append((path, cursor, cursor + duration, duration))
            cursor += duration
            index += 1
    return chunks


def _audio_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as reader:
        return reader.getnframes() / reader.getframerate()


def _stitch(parts: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(part.strip() for part in parts if part.strip())).strip()


def _summary(rows: list[dict[str, Any]], *, device: str, chunk_seconds: float) -> dict[str, Any]:
    tails = [float(row["release_tail_seconds"]) for row in rows]
    return {
        "model": MODEL_ID,
        "runtime": "transformers",
        "device": device,
        "local_files_only": True,
        "chunk_seconds": chunk_seconds,
        "rows": len(rows),
        "safe_update_count": sum(1 for row in rows if row["safe_update"]),
        "tail_p50": round(statistics.median(tails), 3) if tails else None,
        "tail_p95": round(sorted(tails)[min(len(tails) - 1, int(0.95 * (len(tails) - 1)))], 3) if tails else None,
    }


def _markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Oriserve Streaming Candidate Eval",
        "",
        f"- model: `{summary['model']}`",
        f"- runtime: `{summary['runtime']}`",
        f"- device: `{summary['device']}`",
        f"- chunk seconds: `{summary['chunk_seconds']}`",
        f"- rows: `{summary['rows']}`",
        f"- safe updates: `{summary['safe_update_count']}`",
        f"- tail p50: `{summary['tail_p50']}`",
        f"- tail p95: `{summary['tail_p95']}`",
        "",
        "| clip | tail s | safe | reject | candidate |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| {row['name']} | {row['release_tail_seconds']:.3f} | {row['safe_update']} | "
            f"{', '.join(row['reject_reasons'][:3])} | {_short(row['candidate_text'], 120)} |"
        )
    return "\n".join(lines) + "\n"


def _short(text: str, limit: int = 160) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "..."


if __name__ == "__main__":
    main()
