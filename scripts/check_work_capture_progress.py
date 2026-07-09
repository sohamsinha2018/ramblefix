from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SUCCESS_STATUSES = {"paste_attempted", "finalizer_replaced"}
WORK_KEYWORDS = {
    "ai",
    "api",
    "asr",
    "bcom",
    "builder",
    "chatgpt",
    "claude",
    "cloud",
    "codex",
    "company",
    "corpus",
    "cursor",
    "data",
    "eval",
    "finalizer",
    "gpu",
    "hinglish",
    "kubernetes",
    "latency",
    "local",
    "mcp",
    "metric",
    "model",
    "paste",
    "prompt",
    "regression",
    "skill",
    "stt",
    "tool",
    "transcript",
    "ux",
}
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "but",
    "do",
    "for",
    "from",
    "in",
    "is",
    "it",
    "me",
    "not",
    "of",
    "on",
    "or",
    "should",
    "so",
    "that",
    "the",
    "this",
    "to",
    "with",
    "you",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Check progress against the targeted RambleFix work/Hinglish capture sheet.")
    parser.add_argument("--capture-sheet", type=Path, required=True)
    parser.add_argument("--history", type=Path, default=ROOT / "logs/history.jsonl")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--min-retained", type=int, default=20)
    parser.add_argument("--min-representative", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sheet = json.loads(args.capture_sheet.read_text(encoding="utf-8"))
    marker = str(sheet.get("created_at") or "")
    prompts = sheet.get("prompts") if isinstance(sheet.get("prompts"), list) else []
    rows = _read_rows(args.history)
    rows = [row for row in rows if str(row.get("created_at") or "") >= marker]
    rows = rows[-max(args.limit, 1) :]
    clips = _unique_success_clips(rows)
    analyses = [_analyze_clip(clip, prompts) for clip in clips]
    representative = [item for item in analyses if item["representative"]]
    matched_prompt_ids = {item["matched_prompt_id"] for item in representative if item["matched_prompt_id"]}
    payload = {
        "capture_sheet": str(args.capture_sheet),
        "marker": marker,
        "prompt_count": len(prompts),
        "history_rows_since_marker": len(rows),
        "retained_success_clips": len(clips),
        "representative_clips": len(representative),
        "matched_prompt_count": len(matched_prompt_ids),
        "matched_prompt_ids": sorted(matched_prompt_ids),
        "missing_prompt_ids": [str(prompt.get("id") or "") for prompt in prompts if str(prompt.get("id") or "") not in matched_prompt_ids],
        "latest_clips": analyses[-12:],
    }
    payload["ok"] = (
        payload["retained_success_clips"] >= args.min_retained
        and payload["representative_clips"] >= args.min_representative
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"marker: {marker}")
        print(f"prompts: {payload['prompt_count']}")
        print(f"history_rows_since_marker: {payload['history_rows_since_marker']}")
        print(f"retained_success_clips: {payload['retained_success_clips']} / {args.min_retained}")
        print(f"representative_clips: {payload['representative_clips']} / {args.min_representative}")
        print(f"matched_prompt_count: {payload['matched_prompt_count']}")
        if payload["latest_clips"]:
            print("latest clips:")
            for clip in payload["latest_clips"]:
                marker_text = "OK" if clip["representative"] else "skip"
                print(
                    f"- {marker_text} {clip['created_at']} {clip['audio_name']} "
                    f"score={clip['match_score']:.2f} prompt={clip['matched_prompt_id'] or '-'} "
                    f"text={clip['text']}"
                )
        if payload["missing_prompt_ids"]:
            print("missing prompts: " + ", ".join(payload["missing_prompt_ids"][:20]))

    if not payload["ok"]:
        sys.exit(1)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _unique_success_clips(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        if str(row.get("status") or "") not in SUCCESS_STATUSES or row.get("paste_success") is not True:
            continue
        audio_path = str(row.get("audio_path") or "")
        if not audio_path or not Path(audio_path).exists():
            continue
        key = audio_path or str(row.get("run_id") or "")
        current = by_key.get(key)
        if current is None or _clip_rank(row) >= _clip_rank(current):
            by_key[key] = row
    return sorted(by_key.values(), key=lambda row: str(row.get("created_at") or ""))


def _clip_rank(row: dict[str, Any]) -> int:
    status = str(row.get("status") or "")
    if status == "finalizer_replaced":
        return 2
    if status == "paste_attempted":
        return 1
    return 0


def _analyze_clip(row: dict[str, Any], prompts: list[Any]) -> dict[str, Any]:
    text = _row_text(row)
    best_prompt, best_score, critical_hits = _best_prompt(text, prompts)
    work_hits = sorted({word for word in WORK_KEYWORDS if re.search(rf"\b{re.escape(word)}\b", text.lower())})
    representative = bool(best_prompt) and (best_score >= 0.32 or critical_hits >= 2)
    representative = representative or (len(work_hits) >= 2 and len(_content_tokens(text)) >= 5)
    audio_path = Path(str(row.get("audio_path") or ""))
    timings = row.get("timings") if isinstance(row.get("timings"), dict) else {}
    return {
        "created_at": row.get("created_at") or "",
        "run_id": row.get("run_id") or "",
        "audio_path": str(audio_path),
        "audio_name": audio_path.name,
        "release_to_paste_seconds": timings.get("release_to_paste_seconds"),
        "text": text[:160],
        "matched_prompt_id": str(best_prompt.get("id") or "") if isinstance(best_prompt, dict) else "",
        "match_score": round(best_score, 3),
        "critical_hits": critical_hits,
        "work_keyword_hits": work_hits,
        "representative": representative,
    }


def _row_text(row: dict[str, Any]) -> str:
    for key in ("pasted_text", "corrected_text", "raw_text"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return re.sub(r"\s+", " ", value).strip()
    return ""


def _best_prompt(text: str, prompts: list[Any]) -> tuple[dict[str, Any], float, int]:
    text_tokens = _content_tokens(text)
    best_prompt: dict[str, Any] = {}
    best_score = 0.0
    best_critical_hits = 0
    for prompt in prompts:
        if not isinstance(prompt, dict):
            continue
        prompt_text = str(prompt.get("text") or "")
        prompt_tokens = _content_tokens(prompt_text)
        overlap = len(text_tokens & prompt_tokens)
        denominator = max(1, min(len(text_tokens), len(prompt_tokens)))
        score = overlap / denominator
        critical_hits = _critical_hits(text, prompt.get("critical"))
        score = max(score, min(1.0, critical_hits / max(2, len(prompt.get("critical") or []))))
        if score > best_score:
            best_score = score
            best_prompt = prompt
            best_critical_hits = critical_hits
    return best_prompt, best_score, best_critical_hits


def _content_tokens(text: str) -> set[str]:
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9+#.]+", text)
        if len(token) > 1 and token.lower() not in STOPWORDS
    }
    return tokens


def _critical_hits(text: str, critical: Any) -> int:
    if not isinstance(critical, list):
        return 0
    lowered = text.lower()
    hits = 0
    for item in critical:
        term = str(item or "").strip().lower()
        if not term:
            continue
        if re.search(rf"\b{re.escape(term)}\b", lowered):
            hits += 1
            continue
        term_tokens = _content_tokens(term)
        if term_tokens and term_tokens.issubset(_content_tokens(text)):
            hits += 1
    return hits


if __name__ == "__main__":
    main()
