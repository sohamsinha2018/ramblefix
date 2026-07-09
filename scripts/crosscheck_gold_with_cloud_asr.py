from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

import requests


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CloudTranscript:
    name: str
    ok: bool
    text: str = ""
    seconds: float = 0.0
    error: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-check draft gold with cloud audio transcription models.")
    parser.add_argument("--corpus", type=Path, default=ROOT / "eval_corpus/english_real_use_offline_gold_draft_20260628.json")
    parser.add_argument("--output", type=Path, default=ROOT / "eval_runs/offline-english-gold-20260628/cloud_asr_crosscheck.json")
    parser.add_argument("--output-corpus", type=Path, default=ROOT / "eval_corpus/english_real_use_cloud_asr_checked_20260628.json")
    parser.add_argument(
        "--models",
        default="openai:gpt-4o-transcribe,openai:gpt-4o-mini-transcribe,gemini:gemini-2.5-pro,gemini:gemini-2.5-flash",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()

    items = json.loads(args.corpus.read_text(encoding="utf-8"))
    if args.limit:
        items = items[: args.limit]
    runners = build_runners([part.strip() for part in args.models.split(",") if part.strip()], timeout_seconds=args.timeout_seconds)
    if not runners:
        raise SystemExit("No cloud ASR runners available. Set OPENAI_API_KEY or GOOGLE_API_KEY/GEMINI_API_KEY.")

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        audio = Path(item["audio"])
        print(f"[{index}/{len(items)}] {item['id']} {audio.name}", flush=True)
        transcripts: list[CloudTranscript] = []
        for name, runner in runners:
            started = time.perf_counter()
            try:
                text = normalize_spaces(runner(audio))
                transcripts.append(CloudTranscript(name=name, ok=True, text=text, seconds=round(time.perf_counter() - started, 3)))
                print(f"  {name}: {transcripts[-1].seconds:.2f}s {short(text)}", flush=True)
            except Exception as exc:  # noqa: BLE001
                transcripts.append(CloudTranscript(name=name, ok=False, seconds=round(time.perf_counter() - started, 3), error=f"{type(exc).__name__}: {exc}"))
                print(f"  {name}: ERR {transcripts[-1].error[:180]}", flush=True)
        status, chosen, reason = combine(item["gold"], transcripts)
        rows.append(
            {
                "id": item["id"],
                "audio": item["audio"],
                "offline_gold": item["gold"],
                "offline_meta": item.get("meta", {}),
                "cloud_gold": chosen,
                "cloud_asr_status": status,
                "cloud_asr_reason": reason,
                "cloud_agreement_with_offline": round(similarity(chosen, item["gold"]), 3) if chosen else 0.0,
                "transcripts": [asdict(transcript) for transcript in transcripts],
            }
        )
        write_outputs(args.output, args.output_corpus, rows)

    write_outputs(args.output, args.output_corpus, rows)
    print(f"wrote {args.output}")
    print(f"wrote {args.output_corpus}")


def write_outputs(output: Path, output_corpus: Path, rows: list[dict[str, Any]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_corpus.parent.mkdir(parents=True, exist_ok=True)
    output_corpus.write_text(json.dumps(to_corpus(rows), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_runners(models: list[str], *, timeout_seconds: float) -> list[tuple[str, Callable[[Path], str]]]:
    runners: list[tuple[str, Callable[[Path], str]]] = []
    openai_key = os.environ.get("OPENAI_API_KEY")
    google_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    for spec in models:
        provider, _, model = spec.partition(":")
        if provider == "openai":
            if not openai_key:
                print(f"skip {spec}: OPENAI_API_KEY missing", file=sys.stderr)
                continue
            runners.append((spec, lambda audio, model=model, key=openai_key: transcribe_openai(audio, model, key, timeout_seconds)))
        elif provider == "gemini":
            if not google_key:
                print(f"skip {spec}: GOOGLE_API_KEY/GEMINI_API_KEY missing", file=sys.stderr)
                continue
            runners.append((spec, lambda audio, model=model, key=google_key: transcribe_gemini(audio, model, key, timeout_seconds)))
        else:
            raise ValueError(f"unknown cloud model spec: {spec}")
    return runners


def transcribe_openai(audio: Path, model: str, api_key: str, timeout_seconds: float) -> str:
    with audio.open("rb") as fh:
        response = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data={
                "model": model,
                "response_format": "json",
                "temperature": "0",
                "language": "en",
                "prompt": "Indian English work dictation. Preserve acronyms and product terms such as MCP, UX, ASR, STT, RambleFix, Codex, Google. Transcribe what was said; do not summarize.",
            },
            files={"file": (audio.name, fh, "audio/wav")},
            timeout=timeout_seconds,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI {response.status_code}: {response.text[:500]}")
    payload = response.json()
    return str(payload.get("text") or "").strip()


def transcribe_gemini(audio: Path, model: str, api_key: str, timeout_seconds: float) -> str:
    data = base64.b64encode(audio.read_bytes()).decode("ascii")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key},
        json={
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Transcribe this audio exactly as Indian English work dictation. "
                                "Preserve acronyms and product terms such as MCP, UX, ASR, STT, RambleFix, Codex, Google. "
                                "Do not summarize. Return only the transcript."
                            )
                        },
                        {"inline_data": {"mime_type": "audio/wav", "data": data}},
                    ],
                }
            ],
            "generationConfig": {"temperature": 0},
        },
        timeout=timeout_seconds,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Gemini {response.status_code}: {response.text[:500]}")
    payload = response.json()
    parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return " ".join(str(part.get("text") or "") for part in parts).strip()


def combine(offline_gold: str, transcripts: list[CloudTranscript]) -> tuple[str, str, str]:
    good = [transcript for transcript in transcripts if transcript.ok and transcript.text]
    if not good:
        return "cloud_asr_failed", "", "no successful cloud ASR transcript"
    if len(good) == 1:
        agreement = similarity(good[0].text, offline_gold)
        status = "cloud_asr_confirmed" if agreement >= 0.90 else "needs_human_review"
        return status, good[0].text, "single cloud ASR result"
    pairwise = min(similarity(a.text, b.text) for a in good for b in good if a.name != b.name)
    best = max(good, key=lambda transcript: similarity(transcript.text, offline_gold))
    agreement = similarity(best.text, offline_gold)
    if pairwise >= 0.90 and agreement >= 0.90:
        return "cloud_asr_confirmed", offline_gold, "cloud ASR models agree with offline gold"
    if pairwise >= 0.90:
        return "cloud_asr_replaces_offline", best.text, "cloud ASR models agree with each other but not offline gold"
    return "needs_human_review", best.text, "cloud ASR models disagree on meaning or terms"


def to_corpus(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    corpus = []
    for row in rows:
        use_cloud = row["cloud_asr_status"] in {"cloud_asr_confirmed", "cloud_asr_replaces_offline"}
        gold = row["cloud_gold"] if use_cloud and row["cloud_gold"] else row["offline_gold"]
        corpus.append(
            {
                "id": row["id"],
                "category": "real_use_english_dictation",
                "audio": row["audio"],
                "gold": gold,
                "critical": extract_terms(gold),
                "meta": {
                    "gold_status": row["cloud_asr_status"],
                    "offline_gold": row["offline_gold"],
                    "cloud_gold": row["cloud_gold"],
                    "cloud_asr_reason": row["cloud_asr_reason"],
                    **row.get("offline_meta", {}),
                },
            }
        )
    return corpus


def extract_terms(text: str) -> list[str]:
    terms: set[str] = set()
    for match in re.finditer(r"\b[A-Z][A-Z0-9]{1,9}\b|\b[A-Za-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b", text):
        raw = match.group(0).strip()
        if raw.lower() not in {"ok", "yes", "no"}:
            terms.add(raw)
    for term in ["RambleFix", "MCP", "UX", "ASR", "STT", "Google", "Codex"]:
        if re.search(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE):
            terms.add(term)
    return sorted(terms, key=lambda value: value.lower())


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_for_compare(a), normalize_for_compare(b)).ratio()


def normalize_for_compare(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def short(text: str, width: int = 100) -> str:
    clean = normalize_spaces(text)
    return clean if len(clean) <= width else clean[: width - 3] + "..."


if __name__ == "__main__":
    main()
