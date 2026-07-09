from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    region: str
    speaker: str
    query: str


SOURCES = [
    SourceSpec("india_anand_mahindra", "India", "Anand Mahindra", "ENGLISH SPEECH Anand Mahindra Purpose in Life English subtitles"),
    SourceSpec("india_sundar_pichai", "India", "Sundar Pichai", "ENGLISH SPEECH Sundar Pichai English subtitles"),
    SourceSpec("india_ratan_tata", "India", "Ratan Tata", "ENGLISH SPEECH Ratan Tata English subtitles"),
    SourceSpec("india_priyanka_chopra", "India", "Priyanka Chopra", "ENGLISH SPEECH Priyanka Chopra English subtitles"),
    SourceSpec("us_barack_obama", "United States", "Barack Obama", "ENGLISH SPEECH Barack Obama English subtitles"),
    SourceSpec("us_steve_jobs", "United States", "Steve Jobs", "ENGLISH SPEECH Steve Jobs Stanford English subtitles"),
    SourceSpec("us_oprah_winfrey", "United States", "Oprah Winfrey", "ENGLISH SPEECH Oprah Winfrey English subtitles"),
    SourceSpec("us_bill_gates", "United States", "Bill Gates", "ENGLISH SPEECH Bill Gates English subtitles"),
    SourceSpec("canada_justin_trudeau", "Canada", "Justin Trudeau", "ENGLISH SPEECH Justin Trudeau English subtitles"),
    SourceSpec("canada_jordan_peterson", "Canada", "Jordan Peterson", "ENGLISH SPEECH Jordan Peterson English subtitles"),
    SourceSpec("canada_margaret_atwood", "Canada", "Margaret Atwood", "Margaret Atwood interview English subtitles"),
    SourceSpec("uk_emma_watson", "United Kingdom", "Emma Watson", "ENGLISH SPEECH Emma Watson English subtitles"),
    SourceSpec("uk_jk_rowling", "United Kingdom", "J.K. Rowling", "ENGLISH SPEECH J.K. Rowling Harvard English subtitles"),
    SourceSpec("uk_stephen_fry", "United Kingdom", "Stephen Fry", "Stephen Fry interview English subtitles"),
    SourceSpec("australia_tim_minchin", "Australia", "Tim Minchin", "ENGLISH SPEECH Tim Minchin graduation English subtitles"),
    SourceSpec("australia_hugh_jackman", "Australia", "Hugh Jackman", "ENGLISH SPEECH Hugh Jackman English subtitles"),
    SourceSpec("newzealand_jacinda_ardern", "New Zealand", "Jacinda Ardern", "ENGLISH SPEECH Jacinda Ardern English subtitles"),
    SourceSpec("ireland_bono", "Ireland", "Bono", "ENGLISH SPEECH Bono English subtitles"),
    SourceSpec("sweden_greta_thunberg", "Sweden", "Greta Thunberg", "ENGLISH SPEECH Greta Thunberg English subtitles"),
    SourceSpec("israel_yuval_noah_harari", "Israel", "Yuval Noah Harari", "ENGLISH SPEECH Yuval Noah Harari English subtitles"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="eval_runs/youtube-english-public-20260611")
    parser.add_argument("--corpus", default="eval_corpus/youtube_english_public_20260611.json")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--duration", type=int, default=18)
    parser.add_argument("--min-words", type=int, default=18)
    parser.add_argument("--keep-going", action="store_true", default=True)
    args = parser.parse_args()

    out = Path(args.output_dir).expanduser().resolve()
    meta_dir = out / "metadata"
    sub_dir = out / "subtitles"
    clip_dir = out / "clips"
    for directory in (meta_dir, sub_dir, clip_dir):
        directory.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for spec in SOURCES[: args.limit]:
        print(f"== {spec.source_id}: resolving {spec.query}", flush=True)
        try:
            info = resolve_video(spec, meta_dir)
            subtitle_path = download_subtitles(spec.source_id, info["webpage_url"], sub_dir)
            captions = load_json3_captions(subtitle_path)
            start, end, gold = choose_caption_window(captions, duration=args.duration, min_words=args.min_words)
            wav = clip_dir / f"{spec.source_id}.wav"
            if not wav.exists():
                download_audio_clip(info["webpage_url"], wav, start, end)
            rows.append(
                {
                    "id": f"yt_{spec.source_id}",
                    "audio": str(wav),
                    "gold": gold,
                    "source": "youtube",
                    "workflow": "Public English Accent Benchmark",
                    "category": "youtube_english",
                    "region": spec.region,
                    "speaker": spec.speaker,
                    "title": info.get("title"),
                    "uploader": info.get("uploader"),
                    "url": info.get("webpage_url"),
                    "start": start,
                    "end": end,
                    "caption_source": subtitle_path.name,
                    "notes": "Gold is YouTube caption text for this time window; verify manually before public marketing claims.",
                }
            )
            print(f"   ok {spec.region} {spec.speaker}: {start:.1f}-{end:.1f}s {len(gold.split())} words", flush=True)
        except Exception as exc:
            skipped.append({"id": spec.source_id, "error": str(exc)})
            print(f"   skip {spec.source_id}: {exc}", file=sys.stderr, flush=True)
            if not args.keep_going:
                raise

    corpus_path = Path(args.corpus).expanduser().resolve()
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    corpus_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "manifest.json").write_text(
        json.dumps({"rows": rows, "skipped": skipped}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out / "manifest.md").write_text(markdown(rows, skipped), encoding="utf-8")
    print(f"wrote {len(rows)} rows to {corpus_path}")


def resolve_video(spec: SourceSpec, meta_dir: Path) -> dict[str, Any]:
    path = meta_dir / f"{spec.source_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    proc = subprocess.run(
        ["yt-dlp", "--default-search", "ytsearch1", "--dump-json", "--skip-download", spec.query],
        text=True,
        capture_output=True,
        check=False,
        timeout=90,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    first = next((line for line in proc.stdout.splitlines() if line.strip().startswith("{")), "")
    if not first:
        raise RuntimeError("yt-dlp returned no video JSON")
    info = json.loads(first)
    path.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    return info


def download_subtitles(source_id: str, url: str, sub_dir: Path) -> Path:
    existing = sorted(sub_dir.glob(f"{source_id}.*.json3"))
    if existing:
        return prefer_english_subtitle(existing)
    output = str(sub_dir / f"{source_id}.%(ext)s")
    proc = subprocess.run(
        [
            "yt-dlp",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "en-orig,en",
            "--sub-format",
            "json3",
            "-o",
            output,
            url,
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    found = sorted(sub_dir.glob(f"{source_id}.*.json3"))
    if not found:
        raise RuntimeError("no English json3 subtitles found")
    return prefer_english_subtitle(found)


def prefer_english_subtitle(paths: list[Path]) -> Path:
    for suffix in (".en-orig.json3", ".en.json3"):
        for path in paths:
            if path.name.endswith(suffix):
                return path
    return paths[0]


def load_json3_captions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    captions: list[dict[str, Any]] = []
    for event in data.get("events", []):
        start_ms = event.get("tStartMs")
        if start_ms is None:
            continue
        duration_ms = event.get("dDurationMs") or 0
        text = "".join(seg.get("utf8", "") for seg in event.get("segs", []) if isinstance(seg, dict))
        text = clean_caption_text(text)
        if not text:
            continue
        captions.append({"start": start_ms / 1000.0, "end": (start_ms + duration_ms) / 1000.0, "text": text})
    if not captions:
        raise RuntimeError(f"no usable captions in {path}")
    return captions


def choose_caption_window(captions: list[dict[str, Any]], *, duration: int, min_words: int) -> tuple[float, float, str]:
    starts = [45, 60, 75, 90, 120, 150, 180, 240, 300]
    best: tuple[float, float, str] | None = None
    for start in starts:
        end = start + duration
        text = caption_text_for_window(captions, start, end)
        if len(text.split()) >= min_words:
            return float(start), float(end), text
        if best is None or len(text.split()) > len(best[2].split()):
            best = (float(start), float(end), text)
    if best and best[2].strip():
        return best
    raise RuntimeError("no caption window with usable text")


def caption_text_for_window(captions: list[dict[str, Any]], start: float, end: float) -> str:
    parts = [row["text"] for row in captions if row["end"] >= start and row["start"] <= end]
    return normalize_spaces(" ".join(parts))


def download_audio_clip(url: str, wav: Path, start: float, end: float) -> None:
    tmp_pattern = str(wav.with_suffix(".%(ext)s"))
    proc = subprocess.run(
        [
            "yt-dlp",
            "-f",
            "ba/bestaudio",
            "--download-sections",
            f"*{fmt_time(start)}-{fmt_time(end)}",
            "--force-keyframes-at-cuts",
            "-x",
            "--audio-format",
            "wav",
            "-o",
            tmp_pattern,
            url,
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    produced = wav if wav.exists() else next(iter(wav.parent.glob(wav.stem + ".wav")), None)
    if produced is None or not produced.exists():
        raise RuntimeError("yt-dlp did not produce wav")
    normalized = wav.with_suffix(".16k.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(produced), "-ar", "16000", "-ac", "1", str(normalized)],
        check=True,
        text=True,
        capture_output=True,
    )
    shutil.move(str(normalized), wav)


def fmt_time(seconds: float) -> str:
    seconds_int = int(seconds)
    h, rem = divmod(seconds_int, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def clean_caption_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\n", " ")
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"♪[^♪]*♪", " ", text)
    return normalize_spaces(text)


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def markdown(rows: list[dict[str, Any]], skipped: list[dict[str, str]]) -> str:
    lines = [
        "# YouTube English Public Accent Benchmark",
        "",
        "Reference text comes from YouTube English captions for the selected time window. Treat this as a public smoke benchmark; manually verify captions before using results in marketing.",
        "",
        "| ID | Region | Speaker | Window | Words | Title | URL |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        title = str(row.get("title") or "").replace("|", "\\|")
        url = str(row.get("url") or "")
        lines.append(
            f"| {row['id']} | {row['region']} | {row['speaker']} | "
            f"{float(row['start']):.0f}-{float(row['end']):.0f}s | {len(str(row['gold']).split())} | "
            f"{title} | {url} |"
        )
    if skipped:
        lines.extend(["", "## Skipped", "", "| ID | Error |", "| --- | --- |"])
        for row in skipped:
            lines.append(f"| {row['id']} | {row['error'].replace('|', '\\|')[:300]} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
