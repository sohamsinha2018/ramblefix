from __future__ import annotations

import argparse
import html
import json
import re
import wave
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SUCCESS_OR_REVIEW_STATUSES = {
    "paste_attempted",
    "finalizer_replaced",
    "finalizer_saved",
    "failed",
    "blocked_low_quality",
}
BAD_STATUS = {"too_short", "no_speech"}
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
    "corpus",
    "cursor",
    "data",
    "email",
    "eval",
    "finalizer",
    "gpu",
    "hinglish",
    "hindi",
    "english",
    "kubernetes",
    "latency",
    "local",
    "mcp",
    "metric",
    "model",
    "paste",
    "prompt",
    "regression",
    "score",
    "skill",
    "stt",
    "tool",
    "transcript",
    "ux",
    "whisper",
}
PROFANITY_ONLY = {
    "f" + "uck",
    "f" + "ucker",
    "mother" + ("f" + "ucker"),
    "sh" + "it",
    "yo",
    "come",
    "what",
    "how",
    "going",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a reviewable corpus from retained free-form RambleFix hotkey usage.")
    parser.add_argument("--history", type=Path, default=ROOT / "logs/history.jsonl")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--min-words", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows = _read_rows(args.history)[-max(args.limit, 1) :]
    groups = _group_by_audio(rows)
    review_rows = [_review_row(group, min_words=args.min_words) for group in groups]
    review_rows = [row for row in review_rows if row is not None]
    candidates = [row for row in review_rows if row["review_status"] in {"review_ready", "auto_candidate"}]
    representative = [row for row in candidates if row["representative"]]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    review_json = args.output_dir / "real_use_review_set.json"
    candidate_json = args.output_dir / "real_use_candidate_corpus.json"
    html_path = args.output_dir / "real_use_review.html"
    review_json.write_text(json.dumps(review_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidate_json.write_text(json.dumps([_to_candidate(row) for row in representative], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(_render_html(review_rows), encoding="utf-8")

    payload = {
        "history": str(args.history),
        "history_rows_scanned": len(rows),
        "retained_audio_groups": len(groups),
        "review_rows": len(review_rows),
        "candidate_rows": len(candidates),
        "representative_rows": len(representative),
        "review_json": str(review_json),
        "candidate_json": str(candidate_json),
        "html": str(html_path),
        "latest_representative": representative[-10:],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"retained_audio_groups: {payload['retained_audio_groups']}")
        print(f"candidate_rows: {payload['candidate_rows']}")
        print(f"representative_rows: {payload['representative_rows']}")
        print(f"review: {html_path}")


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


def _group_by_audio(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        audio = str(row.get("audio_path") or "")
        if not audio or not Path(audio).exists():
            continue
        grouped.setdefault(audio, []).append(row)
    return [sorted(group, key=lambda row: str(row.get("created_at") or "")) for _, group in sorted(grouped.items())]


def _review_row(group: list[dict[str, Any]], *, min_words: int) -> dict[str, Any] | None:
    best = _best_event(group)
    audio = Path(str(best.get("audio_path") or ""))
    if not audio.exists():
        return None
    text = _best_text(best)
    raw_text = _clean_text(str(best.get("raw_text") or ""))
    if _is_asr_failure_text(text) and raw_text and not _is_asr_failure_text(raw_text):
        text = raw_text
    words = _tokens(text)
    duration = _audio_seconds(audio)
    work_hits = sorted({word for word in WORK_KEYWORDS if re.search(rf"\b{re.escape(word)}\b", text.lower())})
    status = str(best.get("status") or "")
    bad = status in BAD_STATUS or _is_asr_failure_text(text) or duration < 0.8
    profanity_only = bool(words) and set(words).issubset(PROFANITY_ONLY)
    target_app = ""
    if isinstance(best.get("target_app"), dict):
        target_app = str(best["target_app"].get("name") or "")
    target_is_work_app = target_app.lower() in {"codex", "claude"}
    representative = (
        not bad
        and not profanity_only
        and len(words) >= min_words
        and (len(work_hits) >= 1 or (target_is_work_app and len(words) >= 12 and duration >= 5.0))
    )
    review_status = "skip"
    if representative:
        review_status = "review_ready"
    elif not bad and not profanity_only and len(words) >= min_words:
        review_status = "auto_candidate"
    timings = best.get("timings") if isinstance(best.get("timings"), dict) else {}
    return {
        "id": audio.stem,
        "audio": _rel(audio.resolve()),
        "audio_abs": str(audio.resolve()),
        "duration_seconds": round(duration, 3),
        "created_at": str(best.get("created_at") or ""),
        "run_id": str(best.get("run_id") or ""),
        "status": status,
        "target_app": target_app,
        "route": str(best.get("route") or ""),
        "asr_engine": str(best.get("asr_engine") or ""),
        "release_to_paste_seconds": timings.get("release_to_paste_seconds"),
        "release_to_first_output_seconds": timings.get("release_to_first_output_seconds"),
        "product_text": text,
        "raw_text": raw_text,
        "events": [_event_summary(row) for row in group],
        "word_count": len(words),
        "work_keyword_hits": work_hits,
        "representative": representative,
        "review_status": review_status,
        "notes": _notes(review_status),
    }


def _best_event(group: list[dict[str, Any]]) -> dict[str, Any]:
    def rank(row: dict[str, Any]) -> tuple[int, str]:
        status = str(row.get("status") or "")
        rank_value = {
            "finalizer_replaced": 5,
            "paste_attempted": 4,
            "finalizer_saved": 3,
            "failed": 2 if str(row.get("error_type") or "") == "paste_target_error" else 0,
            "blocked_low_quality": 1,
        }.get(status, 0)
        return rank_value, str(row.get("created_at") or "")

    return sorted(group, key=rank)[-1]


def _event_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "created_at": row.get("created_at") or "",
        "status": row.get("status") or "",
        "route": row.get("route") or "",
        "asr_engine": row.get("asr_engine") or "",
        "error_type": row.get("error_type") or "",
        "paste_success": row.get("paste_success"),
        "text": _best_text(row)[:180],
    }


def _best_text(row: dict[str, Any]) -> str:
    for key in ("pasted_text", "corrected_text", "raw_text"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_text(value)
    return ""


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_asr_failure_text(text: str) -> bool:
    lowered = text.strip().lower()
    return (
        not lowered
        or lowered.startswith("asr failure detected")
        or lowered in {"[blank_audio]", "blank_audio", "<|nospeech|>", "no speech detected"}
    )


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9+#.]+", text) if len(token) > 1]


def _audio_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / wav.getframerate()
    except Exception:
        return 0.0


def _to_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"real_use_{row['id']}",
        "audio": row["audio"],
        "gold": row["product_text"],
        "category": "real_use_review_needed",
        "critical": row["work_keyword_hits"],
        "source": "ramblefix_real_use_history",
        "notes": "Draft gold from current product text. Human review required before treating as launch evidence.",
    }


def _notes(status: str) -> str:
    if status == "review_ready":
        return "Review audio and product text; approve or edit gold before launch claims."
    if status == "auto_candidate":
        return "Possibly useful but weak work-speech signal."
    return "Skipped for now: too short, no speech, ASR failure, or not representative."


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _render_html(rows: list[dict[str, Any]]) -> str:
    cards = "\n".join(_render_card(row) for row in rows)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>RambleFix Real-Use Review</title>
  <style>
    body {{ margin: 28px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f8f5; color: #16181d; }}
    header, .card {{ max-width: 1040px; }}
    .card {{ background: rgba(255,255,255,.9); border: 1px solid #dfe3da; border-radius: 8px; padding: 14px; margin: 12px 0; }}
    .skip {{ opacity: .58; }}
    .meta, .label {{ color: #62675f; }}
    .row {{ display: grid; grid-template-columns: 150px 1fr; gap: 12px; margin: 8px 0; }}
    audio {{ width: 100%; }}
    code {{ background: #eef0ea; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <header>
    <h1>RambleFix Real-Use Review</h1>
    <p class="meta">Approve or edit gold manually before using these rows as launch-quality evidence.</p>
  </header>
  {cards}
</body>
</html>
"""


def _render_card(row: dict[str, Any]) -> str:
    klass = "card" if row["review_status"] != "skip" else "card skip"
    audio = html.escape(row["audio_abs"])
    return f"""
  <section class="{klass}">
    <h2>{html.escape(row["id"])} <code>{html.escape(row["review_status"])}</code></h2>
    <div class="row"><div class="label">Audio</div><div><audio controls src="{audio}"></audio></div></div>
    <div class="row"><div class="label">Text</div><div>{html.escape(row["product_text"])}</div></div>
    <div class="row"><div class="label">Route</div><div>{html.escape(row["route"])} / {html.escape(row["asr_engine"])}</div></div>
    <div class="row"><div class="label">Timing</div><div>{row["release_to_paste_seconds"]}s paste, {row["duration_seconds"]}s audio</div></div>
    <div class="row"><div class="label">Keywords</div><div>{html.escape(", ".join(row["work_keyword_hits"]))}</div></div>
    <div class="row"><div class="label">Notes</div><div>{html.escape(row["notes"])}</div></div>
  </section>
"""


if __name__ == "__main__":
    main()
