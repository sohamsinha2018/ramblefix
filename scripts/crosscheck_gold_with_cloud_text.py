from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class JudgeResult:
    model: str
    ok: bool
    selected_text: str = ""
    status: str = "error"
    confidence: str = "low"
    rationale: str = ""
    risky_terms: list[str] | None = None
    candidate_support: list[str] | None = None
    seconds: float = 0.0
    error: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Use cloud text models to adjudicate offline ASR candidate transcripts.")
    parser.add_argument("--candidates", type=Path, default=ROOT / "eval_runs/offline-english-gold-20260628/candidates.json")
    parser.add_argument("--output", type=Path, default=ROOT / "eval_runs/offline-english-gold-20260628/cloud_text_crosscheck.json")
    parser.add_argument("--output-corpus", type=Path, default=ROOT / "eval_corpus/english_real_use_cloud_text_checked_20260628.json")
    parser.add_argument("--models", default="claude-fable-5,claude-opus-4-8")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    rows = json.loads(args.candidates.read_text(encoding="utf-8"))
    if args.limit:
        rows = rows[: args.limit]
    models = [part.strip() for part in args.models.split(",") if part.strip()]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY missing; cannot run cloud text adjudication")

    import anthropic

    client = anthropic.Anthropic()
    out_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        print(f"[{index}/{len(rows)}] {row['id']}", flush=True)
        judge_results: list[JudgeResult] = []
        for model in models:
            started = time.perf_counter()
            try:
                payload = judge_row(client, model, row)
                judge_results.append(
                    JudgeResult(
                        model=model,
                        ok=True,
                        selected_text=normalize_spaces(str(payload.get("selected_text") or "")),
                        status=str(payload.get("status") or "needs_audio_review"),
                        confidence=str(payload.get("confidence") or "low"),
                        rationale=normalize_spaces(str(payload.get("rationale") or "")),
                        risky_terms=list(payload.get("risky_terms") or []),
                        candidate_support=list(payload.get("candidate_support") or []),
                        seconds=round(time.perf_counter() - started, 3),
                    )
                )
                print(f"  {model}: {judge_results[-1].status} {judge_results[-1].confidence} {short(judge_results[-1].selected_text)}", flush=True)
            except Exception as exc:  # noqa: BLE001
                judge_results.append(
                    JudgeResult(
                        model=model,
                        ok=False,
                        seconds=round(time.perf_counter() - started, 3),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                print(f"  {model}: ERR {judge_results[-1].error[:180]}", flush=True)

        cloud_status, cloud_text, reason = combine(row, judge_results)
        out_rows.append(
            {
                "id": row["id"],
                "audio": row["audio"],
                "duration_seconds": row["duration_seconds"],
                "offline_gold": row["gold"],
                "offline_gold_source": row["gold_source"],
                "offline_needs_review": row["needs_human_review"],
                "cloud_text": cloud_text,
                "cloud_text_status": cloud_status,
                "cloud_text_reason": reason,
                "cloud_text_agreement_with_offline": round(similarity(cloud_text, row["gold"]), 3) if cloud_text else 0.0,
                "app_text": row["app_text"],
                "judges": [asdict(result) for result in judge_results],
                "candidates": row["candidates"],
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_corpus.parent.mkdir(parents=True, exist_ok=True)
    args.output_corpus.write_text(json.dumps(to_corpus(out_rows), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    print(f"wrote {args.output_corpus}")


def judge_row(client: Any, model: str, row: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        {
            "name": candidate["name"],
            "text": candidate["text"],
            "error": candidate["error"],
        }
        for candidate in row["candidates"]
        if candidate.get("text") or candidate.get("error")
    ]
    prompt = {
        "id": row["id"],
        "duration_seconds": row["duration_seconds"],
        "current_offline_gold": row["gold"],
        "current_offline_gold_source": row["gold_source"],
        "current_app_text": row["app_text"],
        "candidate_transcripts": candidates,
    }
    message = client.messages.create(
        model=model,
        max_tokens=900,
        temperature=0,
        system=(
            "You are judging ASR transcript candidates for one audio clip, but you CANNOT hear the audio. "
            "Do not pretend to confirm audio. Use only agreement/disagreement among candidates. "
            "Pick a transcript only when the candidate evidence strongly supports it. "
            "Preserve acronyms, product terms, Hinglish/Indian-English wording, numbers, negation, and user intent. "
            "If candidates disagree on meaning, key terms, or beginning/end coverage, set status to needs_audio_review."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    "Return ONLY valid JSON with keys: selected_text, status, confidence, rationale, risky_terms, candidate_support.\n"
                    "status must be one of: text_consensus, needs_audio_review.\n"
                    "confidence must be high, medium, or low.\n\n"
                    f"{json.dumps(prompt, ensure_ascii=False)}"
                ),
            }
        ],
    )
    text = "".join(getattr(block, "text", "") for block in message.content).strip()
    return parse_json(text)


def combine(row: dict[str, Any], judges: list[JudgeResult]) -> tuple[str, str, str]:
    good = [judge for judge in judges if judge.ok and judge.selected_text]
    if not good:
        return "cloud_text_failed", "", "no successful cloud text judges"
    review = [judge for judge in good if judge.status != "text_consensus" or judge.confidence == "low"]
    agreement_with_offline = [similarity(judge.selected_text, row["gold"]) for judge in good]
    pairwise = min(similarity(a.selected_text, b.selected_text) for a in good for b in good) if len(good) > 1 else 1.0
    selected = max(good, key=lambda judge: (similarity(judge.selected_text, row["gold"]), judge.confidence == "high")).selected_text
    if review:
        return "needs_audio_review", selected, "at least one cloud text judge requested audio review"
    if min(agreement_with_offline) >= 0.90 and pairwise >= 0.90:
        return "cloud_text_confirmed", row["gold"], "cloud text judges agree with offline gold"
    return "needs_audio_review", selected, "cloud text judges disagree with offline gold or each other"


def to_corpus(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    corpus = []
    for row in rows:
        use_cloud = row["cloud_text_status"] == "cloud_text_confirmed" and row["cloud_text"]
        gold = row["cloud_text"] if use_cloud else row["offline_gold"]
        corpus.append(
            {
                "id": row["id"],
                "category": "real_use_english_dictation",
                "audio": row["audio"],
                "gold": gold,
                "critical": extract_terms(gold),
                "meta": {
                    "gold_status": "cloud_text_confirmed" if use_cloud else "needs_audio_review",
                    "offline_gold": row["offline_gold"],
                    "offline_gold_source": row["offline_gold_source"],
                    "offline_needs_review": row["offline_needs_review"],
                    "cloud_text_status": row["cloud_text_status"],
                    "cloud_text_reason": row["cloud_text_reason"],
                    "app_text": row["app_text"],
                    "duration_seconds": row["duration_seconds"],
                },
            }
        )
    return corpus


def parse_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


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
