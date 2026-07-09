from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPENSLR104_HINDI_ENGLISH_TEST_URL = "https://openslr.trmal.net/resources/104/Hindi-English_test.tar.gz"


@dataclass(frozen=True)
class SourceResult:
    source: str
    rows: list[dict[str, Any]]
    skipped: list[str]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build capped RambleFix corpora from public ASR benchmark datasets.")
    parser.add_argument("--sources", default="all", help="Comma-separated sources: fleurs,earnings22,svarah,common_voice,openslr104,all")
    parser.add_argument("--limit-per-source", type=int, default=5)
    parser.add_argument("--output-dir", default="eval_runs/public-benchmarks-20260612")
    parser.add_argument("--corpus", default="eval_corpus/public_benchmarks_20260612.json")
    parser.add_argument("--fleurs-configs", default="en_us,hi_in")
    parser.add_argument("--common-voice-dir", help="Path to extracted Mozilla Common Voice locale dir with clips/ and TSV files")
    parser.add_argument("--allow-heavy-downloads", action="store_true", help="Allow direct downloads over a few hundred MB, such as OpenSLR 104")
    parser.add_argument("--keep-going", action="store_true", default=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    sources = parse_sources(args.sources)
    all_rows: list[dict[str, Any]] = []
    skipped: dict[str, list[str]] = {}

    builders = {
        "fleurs": lambda: build_fleurs(audio_dir, limit=args.limit_per_source, configs=parse_csv_arg(args.fleurs_configs)),
        "earnings22": lambda: build_earnings22(audio_dir, limit=args.limit_per_source, allow_heavy_downloads=args.allow_heavy_downloads),
        "svarah": lambda: build_hf_streaming_dataset(
            source="svarah",
            repo_id="ai4bharat/Svarah",
            config=None,
            split="test",
            category="svarah_indian_english",
            reference_trust="silver",
            workflow="Public Benchmark: Svarah Indian-accented English",
            audio_dir=audio_dir,
            limit=args.limit_per_source,
            notes="Svarah is Indian-accented English. The HF dataset is gated; authenticate with HF_TOKEN if access is approved.",
        ),
        "common_voice": lambda: build_common_voice(
            audio_dir,
            common_voice_dir=Path(args.common_voice_dir).expanduser().resolve() if args.common_voice_dir else None,
            limit=args.limit_per_source,
        ),
        "openslr104": lambda: build_openslr104(
            audio_dir,
            output_dir=output_dir,
            limit=args.limit_per_source,
            allow_heavy_downloads=args.allow_heavy_downloads,
        ),
    }

    for source in sources:
        print(f"== {source}", flush=True)
        try:
            result = builders[source]()
            all_rows.extend(result.rows)
            skipped[result.source] = result.skipped
            print(f"   rows={len(result.rows)} skipped={len(result.skipped)}", flush=True)
        except Exception as exc:
            skipped.setdefault(source, []).append(repr(exc))
            print(f"   skipped {source}: {exc}", file=sys.stderr, flush=True)
            if not args.keep_going:
                raise

    corpus_path = Path(args.corpus).expanduser().resolve()
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    corpus_path.write_text(json.dumps(all_rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    manifest = {
        "corpus": str(corpus_path),
        "output_dir": str(output_dir),
        "sources": sources,
        "limit_per_source": args.limit_per_source,
        "rows": all_rows,
        "skipped": skipped,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_dir / "manifest.md").write_text(markdown_manifest(manifest), encoding="utf-8")
    print(f"wrote {len(all_rows)} rows to {corpus_path}", flush=True)


def build_fleurs(audio_dir: Path, *, limit: int, configs: list[str]) -> SourceResult:
    skipped: list[str] = []
    rows: list[dict[str, Any]] = []
    for config in configs:
        category = "fleurs_english" if config.startswith("en") else "fleurs_hindi" if config.startswith("hi") else f"fleurs_{config}"
        try:
            rows.extend(
                build_hf_streaming_dataset(
                    source=f"fleurs_{config}",
                    repo_id="google/fleurs",
                    config=config,
                    split="test",
                    category=category,
                    reference_trust="silver",
                    workflow=f"Public Benchmark: FLEURS {config}",
                    audio_dir=audio_dir,
                    limit=limit,
                    notes="FLEURS benchmark transcript. Use for broad multilingual sanity, not conversational dictation proof.",
                    streaming=False,
                ).rows
            )
        except Exception as exc:
            skipped.append(f"{config}: {exc!r}")
    return SourceResult("fleurs", rows, skipped)


def build_earnings22(audio_dir: Path, *, limit: int, allow_heavy_downloads: bool) -> SourceResult:
    if not allow_heavy_downloads:
        return SourceResult(
            "earnings22",
            [],
            ["Earnings22 chunked parquet shard is about 610 MB. Rerun with --allow-heavy-downloads."],
        )
    return build_hf_streaming_dataset(
        source="earnings22",
        repo_id="distil-whisper/earnings22",
        config="chunked",
        split="test",
        category="earnings22_business_english",
        reference_trust="silver",
        workflow="Public Benchmark: Earnings22 accented business English",
        audio_dir=audio_dir,
        limit=limit,
        notes="Earnings22 chunked benchmark transcript. Use for long/business accented English; spot-check before marketing claims.",
    )


def build_hf_streaming_dataset(
    *,
    source: str,
    repo_id: str,
    config: str | None,
    split: str,
    category: str,
    reference_trust: str,
    workflow: str,
    audio_dir: Path,
    limit: int,
    notes: str,
    streaming: bool = True,
) -> SourceResult:
    try:
        from datasets import Audio, load_dataset
    except Exception as exc:
        raise RuntimeError("Install datasets first: python -m pip install 'datasets>=5.0'") from exc

    selected_split = split if streaming else f"{split}[:{max(limit * 3, limit)}]"
    load_kwargs: dict[str, Any] = {"split": selected_split, "streaming": streaming}
    dataset = load_dataset(repo_id, config, **load_kwargs) if config else load_dataset(repo_id, **load_kwargs)
    if "audio" in getattr(dataset, "features", {}):
        dataset = dataset.cast_column("audio", Audio(decode=False))

    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for row in dataset:
        if len(rows) >= limit:
            break
        text = extract_text(row)
        audio_obj = row.get("audio")
        if not text or not isinstance(audio_obj, dict):
            skipped.append(f"missing text/audio row_keys={sorted(row.keys())}")
            continue
        raw_id = str(row.get("id") or row.get("file") or row.get("path") or len(rows))
        item_id = safe_id(f"{source}_{split}_{raw_id}")
        wav = audio_dir / f"{item_id}.wav"
        try:
            write_audio_object_to_wav(audio_obj, wav)
        except Exception as exc:
            skipped.append(f"{item_id}: audio conversion failed: {exc!r}")
            continue
        rows.append(
            {
                "id": item_id,
                "audio": relpath(wav),
                "gold": text,
                "source": source,
                "dataset_repo": repo_id,
                "dataset_config": config,
                "dataset_split": split,
                "workflow": workflow,
                "category": category,
                "reference_trust": reference_trust,
                "reference_source": "public_benchmark_transcript",
                "language": row.get("language") or row.get("locale") or config,
                "terms": extract_terms(text),
                "notes": notes,
            }
        )
    return SourceResult(source, rows, skipped)


def build_common_voice(audio_dir: Path, *, common_voice_dir: Path | None, limit: int) -> SourceResult:
    if common_voice_dir is None:
        return SourceResult(
            "common_voice",
            [],
            [
                "Common Voice official releases now require Mozilla Data Collective access. "
                "Download/extract a locale, then rerun with --common-voice-dir /path/to/cv-corpus-*/en."
            ],
        )
    if not common_voice_dir.exists():
        return SourceResult("common_voice", [], [f"missing directory: {common_voice_dir}"])

    tsv = first_existing(
        [
            common_voice_dir / "test.tsv",
            common_voice_dir / "validated.tsv",
            common_voice_dir / "dev.tsv",
            common_voice_dir / "train.tsv",
        ]
    )
    clips_dir = common_voice_dir / "clips"
    if tsv is None or not clips_dir.exists():
        return SourceResult("common_voice", [], [f"expected TSV and clips/ under {common_voice_dir}"])

    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    with tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if len(rows) >= limit:
                break
            sentence = clean_text(row.get("sentence") or row.get("text") or "")
            path_value = row.get("path") or row.get("filename") or ""
            source_audio = clips_dir / path_value
            if not sentence or not source_audio.exists():
                skipped.append(f"missing sentence/audio: {path_value}")
                continue
            item_id = safe_id(f"common_voice_{common_voice_dir.name}_{Path(path_value).stem}")
            wav = audio_dir / f"{item_id}.wav"
            try:
                convert_audio_file_to_wav(source_audio, wav)
            except Exception as exc:
                skipped.append(f"{item_id}: {exc!r}")
                continue
            rows.append(
                {
                    "id": item_id,
                    "audio": relpath(wav),
                    "gold": sentence,
                    "source": "common_voice",
                    "workflow": "Public Benchmark: Mozilla Common Voice validated/read speech",
                    "category": "common_voice",
                    "reference_trust": "silver",
                    "reference_source": "common_voice_validated_transcript",
                    "language": common_voice_dir.name,
                    "accent": row.get("accent", ""),
                    "terms": extract_terms(sentence),
                    "notes": "Common Voice is useful for accent/read-speech coverage, but not sufficient for work dictation claims.",
                }
            )
    return SourceResult("common_voice", rows, skipped)


def build_openslr104(audio_dir: Path, *, output_dir: Path, limit: int, allow_heavy_downloads: bool) -> SourceResult:
    archive = output_dir / "downloads" / "Hindi-English_test.tar.gz"
    extract_dir = output_dir / "openslr104_hindi_english_test"
    skipped: list[str] = []
    if not extract_dir.exists():
        if not archive.exists():
            if not allow_heavy_downloads:
                return SourceResult(
                    "openslr104",
                    [],
                    [
                        "OpenSLR 104 Hindi-English test is 443 MB. Rerun with --allow-heavy-downloads to download it."
                    ],
                )
            archive.parent.mkdir(parents=True, exist_ok=True)
            download_file(OPENSLR104_HINDI_ENGLISH_TEST_URL, archive)
        extract_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(extract_dir)

    transcripts = load_kaldi_text_files(extract_dir)
    if not transcripts:
        return SourceResult("openslr104", [], [f"no Kaldi-style text transcript files found under {extract_dir}"])

    segments = load_kaldi_segments(extract_dir)
    wav_scp = load_kaldi_wav_scp(extract_dir)
    audio_files = index_audio_files(extract_dir)
    rows: list[dict[str, Any]] = []
    for utt_id, text in transcripts.items():
        if len(rows) >= limit:
            break
        segment = segments.get(utt_id)
        source_audio: Path | None = None
        start = end = None
        if segment:
            recording_id, start, end = segment
            source_audio = wav_scp.get(recording_id) or find_audio_for_utterance(recording_id, audio_files)
        if source_audio is None:
            source_audio = find_audio_for_utterance(utt_id, audio_files)
        if source_audio is None:
            skipped.append(f"{utt_id}: missing matching audio")
            continue
        item_id = safe_id(f"openslr104_hi_en_{utt_id}")
        wav = audio_dir / f"{item_id}.wav"
        try:
            if start is not None and end is not None:
                extract_audio_segment_to_wav(source_audio, wav, start=start, end=end)
            else:
                convert_audio_file_to_wav(source_audio, wav)
        except Exception as exc:
            skipped.append(f"{item_id}: {exc!r}")
            continue
        rows.append(
            {
                "id": item_id,
                "audio": relpath(wav),
                "gold": clean_text(text),
                "source": "openslr104",
                "workflow": "Public Benchmark: MUCS/OpenSLR 104 Hindi-English code-switching",
                "category": "openslr104_hinglish",
                "reference_trust": "silver",
                "reference_source": "openslr104_transcript",
                "language": "hi-en",
                "start": start,
                "end": end,
                "terms": extract_terms(text),
                "notes": "Hindi-English code-switched technical tutorial speech. Strong benchmark for the Hinglish/work-term wedge.",
            }
        )
    return SourceResult("openslr104", rows, skipped)


def parse_sources(value: str) -> list[str]:
    allowed = ["fleurs", "earnings22", "svarah", "common_voice", "openslr104"]
    requested = parse_csv_arg(value)
    if not requested or "all" in requested:
        return allowed
    unknown = sorted(set(requested) - set(allowed))
    if unknown:
        raise SystemExit(f"Unknown sources: {', '.join(unknown)}")
    return requested


def parse_csv_arg(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def extract_text(row: dict[str, Any]) -> str:
    for key in ("raw_transcription", "transcription", "sentence", "text", "normalized_text", "transcript"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return clean_text(value)
    return ""


def write_audio_object_to_wav(audio_obj: dict[str, Any], wav: Path) -> None:
    if wav.exists():
        return
    audio_bytes = audio_obj.get("bytes")
    audio_path = audio_obj.get("path")
    with tempfile.TemporaryDirectory() as tmp:
        if isinstance(audio_bytes, bytes):
            suffix = Path(str(audio_path or "audio.wav")).suffix or ".audio"
            source = Path(tmp) / f"source{suffix}"
            source.write_bytes(audio_bytes)
            convert_audio_file_to_wav(source, wav)
        elif audio_path:
            convert_audio_file_to_wav(Path(str(audio_path)), wav)
        else:
            raise RuntimeError("audio object had neither bytes nor path")


def convert_audio_file_to_wav(source: Path, wav: Path) -> None:
    wav.parent.mkdir(parents=True, exist_ok=True)
    if wav.exists():
        return
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-ar", "16000", "-ac", "1", str(wav)],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip())


def extract_audio_segment_to_wav(source: Path, wav: Path, *, start: float, end: float) -> None:
    wav.parent.mkdir(parents=True, exist_ok=True)
    if wav.exists():
        return
    duration = max(0.0, end - start)
    if duration <= 0:
        raise RuntimeError(f"invalid segment {start}-{end}")
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(wav),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip())


def download_file(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    proc = subprocess.run(
        ["curl", "-L", "--fail", "--continue-at", "-", "-o", str(tmp), url],
        text=True,
        check=False,
        timeout=3600,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"download failed: {url}")
    shutil.move(str(tmp), path)


def load_kaldi_text_files(root: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for path in root.rglob("text"):
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2:
                    rows.setdefault(parts[0], parts[1])
    return rows


def load_kaldi_segments(root: Path) -> dict[str, tuple[str, float, float]]:
    rows: dict[str, tuple[str, float, float]] = {}
    for path in root.rglob("segments"):
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                parts = line.strip().split()
                if len(parts) < 4:
                    continue
                try:
                    rows.setdefault(parts[0], (parts[1], float(parts[2]), float(parts[3])))
                except ValueError:
                    continue
    return rows


def load_kaldi_wav_scp(root: Path) -> dict[str, Path]:
    rows: dict[str, Path] = {}
    for path in root.rglob("wav.scp"):
        base = path.parent.parent
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                parts = line.strip().split(maxsplit=1)
                if len(parts) != 2:
                    continue
                wav_value = parts[1].strip()
                if "|" in wav_value:
                    continue
                wav_path = Path(wav_value)
                if not wav_path.is_absolute():
                    wav_path = base / wav_path
                rows.setdefault(parts[0], wav_path)
    return rows


def index_audio_files(root: Path) -> dict[str, Path]:
    exts = {".wav", ".flac", ".mp3", ".m4a", ".ogg"}
    return {path.stem: path for path in root.rglob("*") if path.suffix.lower() in exts}


def find_audio_for_utterance(utt_id: str, audio_files: dict[str, Path]) -> Path | None:
    if utt_id in audio_files:
        return audio_files[utt_id]
    for stem, path in audio_files.items():
        if stem in utt_id or utt_id in stem:
            return path
    return None


def first_existing(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def extract_terms(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9.+#-]{2,}", text)
    terms: list[str] = []
    for word in words:
        lower = word.lower()
        if lower in {"the", "and", "that", "this", "with", "from", "have", "were", "will", "your", "they", "been"}:
            continue
        if word[:1].isupper() or any(ch.isdigit() for ch in word) or len(word) >= 7:
            if word not in terms:
                terms.append(word)
    return terms[:12]


def safe_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:140]


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def markdown_manifest(manifest: dict[str, Any]) -> str:
    rows = manifest["rows"]
    skipped = manifest["skipped"]
    lines = [
        "# Public Benchmark Corpus",
        "",
        f"- Corpus: `{manifest['corpus']}`",
        f"- Output dir: `{manifest['output_dir']}`",
        f"- Sources: `{', '.join(manifest['sources'])}`",
        f"- Limit per source: `{manifest['limit_per_source']}`",
        f"- Rows: `{len(rows)}`",
        "",
        "## Rows",
        "",
        "| ID | Category | Trust | Language | Words | Audio |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {id} | {category} | {trust} | {language} | {words} | `{audio}` |".format(
                id=row["id"],
                category=row.get("category", ""),
                trust=row.get("reference_trust", ""),
                language=row.get("language", ""),
                words=len(str(row.get("gold", "")).split()),
                audio=row.get("audio", ""),
            )
        )
    lines.extend(["", "## Skipped", ""])
    for source, messages in skipped.items():
        if not messages:
            continue
        lines.append(f"### {source}")
        for message in messages:
            lines.append(f"- {message}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    main()
