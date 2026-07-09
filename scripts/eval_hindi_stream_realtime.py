from __future__ import annotations

import argparse
import json
import statistics
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import requests
from mlx_qwen3_asr import load_audio

from ramblefix.hindi_chunk_polish import split_silence_chunks


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "eval_runs/fresh-hindi-probe-20260629/new_retained_1418_1445_current_chunk10_eval.json"
DEFAULT_OUT_BASE = ROOT / "eval_runs/fresh-hindi-probe-20260629"
DEFAULT_IDS = [
    "20260628-213227-8B83FA",
    "20260628-213245-6E888E",
    "20260628-213308-6342B3",
    "20260628-213328-8E04C9",
    "20260628-213350-A96491",
    "20260628-213415-D98152",
    "20260628-213446-D33327",
    "20260628-213518-2431F9",
    "20260628-214446-AFCA3F",
    "20260628-214540-FE1428",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay saved Hindi/Hinglish clips through realtime Srota stream.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--target", type=float, required=True)
    parser.add_argument("--min", dest="minimum", type=float, required=True)
    parser.add_argument("--max", dest="maximum", type=float, required=True)
    parser.add_argument("--lookaround", type=float, default=1.5)
    parser.add_argument("--low-confidence", type=float, default=0.50)
    parser.add_argument("--early-low-confidence", type=float, default=0.80)
    parser.add_argument("--wait-timeout", type=float, default=3.0)
    parser.add_argument("--max-release-tail", type=float, default=3.0)
    parser.add_argument("--witness-timeout", type=float, default=0.0)
    parser.add_argument("--ids", nargs="*", default=DEFAULT_IDS)
    args = parser.parse_args()

    _assert_local_server("http://127.0.0.1:8188/health")
    rows = _load_rows(args.input, args.ids)
    out_dir = args.out_dir or (
        DEFAULT_OUT_BASE
        / f"hindi_stream_realtime_t{args.target:g}_m{args.minimum:g}_x{args.maximum:g}_{time.strftime('%Y%m%d_%H%M%S')}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk_base = ROOT / "logs/hindi_stream_chunks" / out_dir.name
    chunk_base.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        result = _run_clip(row, args, chunk_base=chunk_base)
        results.append(result)
        print(
            f"{index:02d}/{len(rows)} {result['run_id']} "
            f"route={result['route']} risk={result['risk']} safe={result['safe_update']} "
            f"merge={result['partial_merge']} tail={result['release_tail_seconds']} "
            f"finish={result['finish_client_seconds']} pending={result['pending_count']} "
            f"reject={result['reject_reasons']}",
            flush=True,
        )

    summary = _summary(results, candidate=f"realtime Srota stream target={args.target:g} min={args.minimum:g} max={args.maximum:g}")
    payload = {"summary": summary, "rows": results}
    (out_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(out_dir / "results.md", summary, results)
    print("WROTE", out_dir / "results.json")
    print(json.dumps(summary, indent=2))


def _assert_local_server(url: str) -> None:
    response = requests.get(url, timeout=5)
    response.raise_for_status()


def _load_rows(path: Path, ids: list[str]) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    by_id = {row["run_id"]: row for row in data["rows"]}
    return [by_id[run_id] for run_id in ids]


def _run_clip(row: dict[str, Any], args: argparse.Namespace, *, chunk_base: Path) -> dict[str, Any]:
    sample_rate = 16_000
    audio = np.asarray(load_audio(str(row["audio"]), sr=sample_rate), dtype=np.float32)
    duration = len(audio) / sample_rate
    chunks = split_silence_chunks(
        audio,
        sample_rate=sample_rate,
        target_seconds=args.target,
        min_seconds=args.minimum,
        max_seconds=args.maximum,
        lookaround_seconds=args.lookaround,
    )

    session_id = f"realtime-{row['run_id']}-{int(time.time())}"
    chunk_dir = chunk_base / session_id
    chunk_dir.mkdir(parents=True, exist_ok=True)
    requests.post(
        "http://127.0.0.1:8188/hindi-stream/start",
        json={
            "run_id": session_id,
            "chunk_dir": str(chunk_dir),
            "low_confidence_threshold": args.low_confidence,
            "early_low_confidence_threshold": args.early_low_confidence,
            "poll_interval_seconds": 0.20,
        },
        timeout=5,
    ).raise_for_status()

    started = time.perf_counter()
    written_chunks: list[dict[str, float | int]] = []
    for chunk_index, (start, end) in enumerate(chunks):
        end_seconds = end / sample_rate
        while time.perf_counter() - started < end_seconds:
            remaining = end_seconds - (time.perf_counter() - started)
            time.sleep(min(0.20, max(0.01, remaining)))
        chunk_path = chunk_dir / f"chunk-{chunk_index:03d}.wav"
        _write_wav(chunk_path, audio[start:end], sample_rate=sample_rate)
        written_chunks.append(
            {
                "index": chunk_index,
                "start_seconds": round(start / sample_rate, 3),
                "end_seconds": round(end_seconds, 3),
                "duration_seconds": round((end - start) / sample_rate, 3),
            }
        )

    release_started = time.perf_counter()
    finish = requests.post(
        "http://127.0.0.1:8188/hindi-stream/finish",
        json={
            "run_id": session_id,
            "draft_text": row["fast_text"],
            "max_release_tail_seconds": args.max_release_tail,
            "wait_timeout_seconds": args.wait_timeout,
            "audio_path": row["audio"],
            "witness_timeout_seconds": args.witness_timeout,
        },
        timeout=10 + max(0.0, args.witness_timeout),
    )
    finish.raise_for_status()
    response = finish.json()
    quality = response.get("quality") or {}
    return {
        "run_id": row["run_id"],
        "session_run_id": session_id,
        "audio": row["audio"],
        "audio_seconds": round(duration, 3),
        "fast_text": row["fast_text"],
        "fast_release_to_paste": row.get("fast_release_to_paste"),
        "route": response.get("route"),
        "risk": bool(response.get("risk")),
        "safe_update": bool(response.get("safe_update")),
        "release_tail_seconds": response.get("release_tail_seconds"),
        "finish_client_seconds": round(time.perf_counter() - release_started, 3),
        "partial_merge": bool(quality.get("partial_merge")),
        "pending_count": quality.get("pending_count"),
        "raw_text": response.get("raw_text") or "",
        "text": response.get("text") or "",
        "reject_reasons": response.get("reject_reasons") or [],
        "risk_reasons": response.get("risk_reasons") or [],
        "quality": quality,
        "chunks": response.get("chunks") or [],
        "detector_chunks": response.get("detector_chunks") or [],
        "written_chunks": written_chunks,
    }


def _write_wav(path: Path, audio: np.ndarray, *, sample_rate: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def _summary(rows: list[dict[str, Any]], *, candidate: str) -> dict[str, Any]:
    fast = [row["fast_release_to_paste"] for row in rows if isinstance(row.get("fast_release_to_paste"), int | float)]
    tails = [row["release_tail_seconds"] for row in rows if isinstance(row.get("release_tail_seconds"), int | float)]
    routes = sorted({str(row["route"]) for row in rows})
    return {
        "rows": len(rows),
        "total_audio_seconds": round(sum(float(row["audio_seconds"]) for row in rows), 3),
        "risk_count": sum(1 for row in rows if row["risk"]),
        "safe_update_count": sum(1 for row in rows if row["safe_update"]),
        "hindi_value_count": sum(1 for row in rows if (row.get("quality") or {}).get("hindi_value", {}).get("has_hindi_value")),
        "safe_hindi_value_count": sum(
            1 for row in rows if row["safe_update"] and (row.get("quality") or {}).get("hindi_value", {}).get("has_hindi_value")
        ),
        "partial_merge_count": sum(1 for row in rows if row["partial_merge"]),
        "fast_release_to_paste_p50": _median(fast),
        "fast_release_to_paste_p95": _p95(fast),
        "hindi_stream_tail_p50": _median(tails),
        "hindi_stream_tail_p95": _p95(tails),
        "routes": {route: sum(1 for row in rows if row["route"] == route) for route in routes},
        "local_only": True,
        "candidate": candidate,
    }


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 3)
    return round(statistics.quantiles(values, n=20)[18], 3)


def _write_markdown(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = ["# Hindi Realtime Stream Eval", ""]
    lines.extend(f"- {key}: `{value}`" for key, value in summary.items())
    lines.extend(
        [
            "",
            "| clip | dur | fast s | route | risk | tail | safe | merge | final/candidate preview | reject |",
            "| --- | ---: | ---: | --- | --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        preview = (row["text"] if row["safe_update"] else row["raw_text"]).replace("|", "/").replace("\n", " ")[:180]
        lines.append(
            f"| {row['run_id']} | {row['audio_seconds']} | {row['fast_release_to_paste']} | "
            f"{row['route']} | {row['risk']} | {row['release_tail_seconds']} | {row['safe_update']} | "
            f"{row['partial_merge']} | {preview} | {', '.join(row['reject_reasons'])} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
