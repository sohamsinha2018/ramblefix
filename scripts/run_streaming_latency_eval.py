from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import meaning_coverage, term_coverage_report, word_error_rate
from ramblefix.sidecar import ensure_ready
from ramblefix.streaming import stream_wav_file
from ramblefix.tts import synthesize_with_elevenlabs


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RambleFix streaming latency lab.")
    parser.add_argument("--corpus", type=Path, help="Corpus JSON with id/audio/gold rows.")
    parser.add_argument("--ids", default="", help="Comma-separated corpus ids to include.")
    parser.add_argument("--audio", action="append", default=[], help="Standalone WAV path. Can be repeated.")
    parser.add_argument("--snippets", type=Path, help="JSON snippets to synthesize with ElevenLabs before eval.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "eval_runs" / "streaming-lab-latest")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--chunk-seconds", type=float, default=0.25)
    parser.add_argument("--draft-min-seconds", type=float, default=1.5)
    parser.add_argument("--draft-every-seconds", type=float, default=1.5)
    parser.add_argument("--finalizer", choices=["none", "auto", "always"], default="always")
    parser.add_argument("--no-real-time", action="store_true", help="Run as fast as possible; not UX-realistic.")
    parser.add_argument("--no-ensure-sidecar", action="store_true")
    parser.add_argument("--elevenlabs-voice-id", default=os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"))
    parser.add_argument("--elevenlabs-model-id", default=os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"))
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not args.no_ensure_sidecar:
        state = ensure_ready(warm=True, timeout_seconds=20.0)
        if not state.ready:
            raise RuntimeError(f"whisper.cpp sidecar not ready: {state.status} {state.error}")

    rows = []
    if args.corpus:
        rows.extend(_load_corpus_rows(args.corpus, args.ids))
    for audio in args.audio:
        path = Path(audio).expanduser().resolve()
        rows.append({"id": path.stem, "audio": str(path), "gold": "", "category": "manual_audio"})
    if args.snippets:
        rows.extend(_synthesize_snippets(args.snippets, args.output_dir, args.elevenlabs_voice_id, args.elevenlabs_model_id))
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        raise ValueError("No rows to evaluate. Pass --corpus, --audio, or --snippets.")

    scored_rows = []
    for index, row in enumerate(rows, 1):
        audio = _resolve_audio(row["audio"], args.corpus)
        print(f"[{index}/{len(rows)}] {row['id']} streaming {audio.name}", flush=True)
        result = stream_wav_file(
            audio,
            real_time=not args.no_real_time,
            chunk_seconds=args.chunk_seconds,
            draft_min_seconds=args.draft_min_seconds,
            draft_every_seconds=args.draft_every_seconds,
            finalizer=args.finalizer,
        )
        scored_rows.append(_score_row(row, result))
        print(
            f"  draft={result.timings_ms['release_to_paste']}ms final={result.timings_ms['release_to_final']}ms "
            f"route={result.route}",
            flush=True,
        )

    payload = {"summary": _summary(scored_rows), "rows": scored_rows}
    (args.output_dir / "streaming_results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "streaming_results.md").write_text(_markdown(payload), encoding="utf-8")
    print(_markdown(payload))
    print(f"wrote {args.output_dir / 'streaming_results.json'}")


def _load_corpus_rows(path: Path, ids_value: str) -> list[dict[str, Any]]:
    corpus_path = path.expanduser().resolve()
    rows = json.loads(corpus_path.read_text(encoding="utf-8"))
    selected_ids = {item.strip() for item in ids_value.split(",") if item.strip()}
    out = []
    for row in rows:
        row_id = str(row.get("id") or row.get("clip_id") or "")
        if selected_ids and row_id not in selected_ids:
            continue
        out.append(
            {
                "id": row_id,
                "audio": row.get("audio"),
                "gold": row.get("gold") or row.get("text") or "",
                "category": row.get("category") or row.get("language") or "",
                "terms": row.get("terms") or row.get("critical_terms") or row.get("must_have") or [],
            }
        )
    return out


def _synthesize_snippets(snippets_path: Path, output_dir: Path, voice_id: str, model_id: str) -> list[dict[str, Any]]:
    payload = json.loads(snippets_path.expanduser().resolve().read_text(encoding="utf-8"))
    snippets = payload.get("snippets", payload) if isinstance(payload, dict) else payload
    if not isinstance(snippets, list):
        raise ValueError("snippets JSON must be a list or an object with a snippets list")
    out = []
    audio_dir = output_dir / "elevenlabs_audio"
    for index, item in enumerate(snippets, 1):
        text = str(item.get("text") or item.get("gold") or "").strip()
        if not text:
            raise ValueError(f"snippet {index} missing text")
        row_id = str(item.get("id") or f"elevenlabs_hinglish_{index:03d}")
        mp3 = audio_dir / f"{row_id}.mp3"
        wav = audio_dir / f"{row_id}.wav"
        if not wav.exists():
            synthesize_with_elevenlabs(text, mp3, voice_id=voice_id, model_id=model_id)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "16000", "-ac", "1", str(wav)],
                check=True,
            )
        out.append(
            {
                "id": row_id,
                "audio": str(wav),
                "gold": str(item.get("gold") or text),
                "category": str(item.get("category") or "elevenlabs_hinglish"),
                "terms": item.get("terms") or item.get("critical_terms") or [],
            }
        )
    return out


def _resolve_audio(audio_value: str, corpus_path: Path | None) -> Path:
    audio = Path(str(audio_value)).expanduser()
    if audio.is_absolute() and audio.exists():
        return audio
    bases = [ROOT]
    if corpus_path:
        bases.append(corpus_path.expanduser().resolve().parent)
    bases.append(Path.cwd())
    for base in bases:
        candidate = (base / audio).resolve()
        if candidate.exists():
            return candidate
    return (ROOT / audio).resolve()


def _score_row(row: dict[str, Any], result: Any) -> dict[str, Any]:
    gold = str(row.get("gold") or "")
    terms = row.get("terms") or []
    term_report = term_coverage_report(gold, result.final_text, terms) if gold else {"coverage": None, "misses": []}
    return {
        "id": row["id"],
        "category": row.get("category", ""),
        "audio": result.audio,
        "audio_seconds": result.audio_seconds,
        "gold": gold,
        "paste_text": result.paste_text,
        "final_text": result.final_text,
        "route": result.route,
        "timings_ms": result.timings_ms,
        "churn_wer": result.churn_wer,
        "wer": word_error_rate(gold, result.final_text) if gold else None,
        "meaning_coverage": meaning_coverage(gold, result.final_text) if gold else None,
        "term_coverage": term_report["coverage"],
        "term_misses": term_report["misses"],
        "events": [event.__dict__ for event in result.events],
        "errors": result.errors,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n": len(rows),
        "p50_ttfp_ms": _median(_timing(rows, "time_to_first_partial")),
        "p50_ttfs_ms": _median(_timing(rows, "time_to_first_stable")),
        "p50_release_to_paste_ms": _median(_timing(rows, "release_to_paste")),
        "p95_release_to_paste_ms": _percentile(_timing(rows, "release_to_paste"), 0.95),
        "p50_release_to_final_ms": _median(_timing(rows, "release_to_final")),
        "p95_release_to_final_ms": _percentile(_timing(rows, "release_to_final"), 0.95),
        "avg_wer": _avg_optional(row.get("wer") for row in rows),
        "avg_meaning_coverage": _avg_optional(row.get("meaning_coverage") for row in rows),
        "avg_term_coverage": _avg_optional(row.get("term_coverage") for row in rows),
        "error_rows": sum(1 for row in rows if row.get("errors")),
    }


def _timing(rows: list[dict[str, Any]], key: str) -> list[float]:
    values = []
    for row in rows:
        value = row.get("timings_ms", {}).get(key)
        if value is not None:
            values.append(float(value))
    return values


def _median(values: list[float]) -> float | None:
    return None if not values else round(statistics.median(values), 1)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))
    return round(ordered[index], 1)


def _avg_optional(values: Any) -> float | None:
    present = [float(value) for value in values if value is not None]
    return None if not present else round(sum(present) / len(present), 3)


def _markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Streaming Latency Lab",
        "",
        "## Summary",
        "",
        f"- rows: `{summary['n']}`",
        f"- p50 time to first partial: `{_fmt_ms(summary['p50_ttfp_ms'])}`",
        f"- p50 time to stable draft: `{_fmt_ms(summary['p50_ttfs_ms'])}`",
        f"- p50 release to paste: `{_fmt_ms(summary['p50_release_to_paste_ms'])}`",
        f"- p95 release to paste: `{_fmt_ms(summary['p95_release_to_paste_ms'])}`",
        f"- p50 release to final: `{_fmt_ms(summary['p50_release_to_final_ms'])}`",
        f"- p95 release to final: `{_fmt_ms(summary['p95_release_to_final_ms'])}`",
        f"- avg WER: `{summary['avg_wer']}`",
        f"- avg meaning coverage: `{summary['avg_meaning_coverage']}`",
        f"- avg term coverage: `{summary['avg_term_coverage']}`",
        f"- error rows: `{summary['error_rows']}`",
        "",
        "## Rows",
        "",
        "| id | route | audio s | release->paste ms | release->final ms | WER | meaning | paste text | final text |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in payload["rows"]:
        wer = "" if row["wer"] is None else f"{float(row['wer']):.3f}"
        meaning = "" if row["meaning_coverage"] is None else f"{float(row['meaning_coverage']):.3f}"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["id"]),
                    str(row["route"]),
                    f"{float(row['audio_seconds']):.1f}",
                    str(row["timings_ms"].get("release_to_paste")),
                    str(row["timings_ms"].get("release_to_final")),
                    wer,
                    meaning,
                    _clip(row["paste_text"]),
                    _clip(row["final_text"]),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _clip(text: str, limit: int = 90) -> str:
    clean = " ".join(str(text).split()).replace("|", "\\|")
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _fmt_ms(value: object) -> str:
    return "n/a" if value is None else f"{value}ms"


if __name__ == "__main__":
    main()
