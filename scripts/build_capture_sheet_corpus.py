from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SUCCESS_STATUSES = {"paste_attempted", "finalizer_replaced"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a scoreable corpus from retained RambleFix capture-sheet recordings.")
    parser.add_argument("--capture-sheet", type=Path, required=True)
    parser.add_argument("--history", type=Path, default=ROOT / "logs/history.jsonl")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--match-mode", choices=["order"], default="order")
    parser.add_argument("--min-match-score", type=float, default=0.32)
    parser.add_argument("--trust-order", action="store_true", help="Promote order-mapped rows to gold even when text match is weak.")
    parser.add_argument("--min-rows", type=int, default=1)
    args = parser.parse_args()

    sheet = json.loads(args.capture_sheet.read_text(encoding="utf-8"))
    marker = str(sheet.get("created_at") or "")
    prompts = [prompt for prompt in sheet.get("prompts", []) if isinstance(prompt, dict)]
    rows = _read_rows(args.history)
    rows = [row for row in rows if str(row.get("created_at") or "") >= marker]
    clips = _unique_success_clips(rows[-max(1, args.limit) :])
    mapped = _order_map(clips, prompts, min_match_score=args.min_match_score, trust_order=args.trust_order)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    review_json = args.output_dir / "capture_sheet_review_set.json"
    corpus_json = args.output_dir / "capture_sheet_corpus.json"
    html_path = args.output_dir / "capture_sheet_review.html"
    review_json.write_text(json.dumps(mapped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    corpus = [_to_corpus_row(row) for row in mapped if row["review_status"] == "gold"]
    corpus_json.write_text(json.dumps(corpus, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(_render_html(mapped, marker), encoding="utf-8")

    payload = {
        "marker": marker,
        "prompt_count": len(prompts),
        "retained_success_clips": len(clips),
        "mapped_rows": len(mapped),
        "corpus_rows": len(corpus),
        "trust_order": args.trust_order,
        "min_match_score": args.min_match_score,
        "review_json": str(review_json),
        "corpus_json": str(corpus_json),
        "html": str(html_path),
        "ok": len(corpus) >= args.min_rows,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
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
    by_audio: dict[str, dict[str, Any]] = {}
    for row in rows:
        if str(row.get("status") or "") not in SUCCESS_STATUSES or row.get("paste_success") is not True:
            continue
        audio_path = str(row.get("audio_path") or "")
        if not audio_path or not Path(audio_path).exists():
            continue
        current = by_audio.get(audio_path)
        if current is None or _clip_rank(row) >= _clip_rank(current):
            by_audio[audio_path] = row
    return sorted(by_audio.values(), key=lambda row: str(row.get("created_at") or ""))


def _clip_rank(row: dict[str, Any]) -> int:
    return 2 if str(row.get("status") or "") == "finalizer_replaced" else 1


def _order_map(
    clips: list[dict[str, Any]],
    prompts: list[dict[str, Any]],
    *,
    min_match_score: float,
    trust_order: bool,
) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for index, clip in enumerate(clips):
        if index >= len(prompts):
            break
        prompt = prompts[index]
        audio = Path(str(clip.get("audio_path") or "")).resolve()
        product_text = _row_text(clip)
        gold = str(prompt.get("text") or "").strip()
        critical = [str(item) for item in prompt.get("critical", []) if str(item).strip()]
        match_score = _token_overlap(product_text, gold)
        critical_hits = _critical_hits(product_text, critical)
        review_status = "gold" if trust_order or match_score >= min_match_score or critical_hits >= 2 else "needs_review"
        mapped.append(
            {
                "id": f"capture_{index + 1:02d}_{prompt.get('id') or audio.stem}",
                "capture_index": index + 1,
                "prompt_id": str(prompt.get("id") or ""),
                "mode": str(prompt.get("mode") or ""),
                "audio": _rel(audio),
                "audio_abs": str(audio),
                "gold": gold,
                "product_text": product_text,
                "critical": critical,
                "history_created_at": str(clip.get("created_at") or ""),
                "history_status": str(clip.get("status") or ""),
                "history_run_id": str(clip.get("run_id") or ""),
                "release_to_paste_seconds": _release_to_paste(clip),
                "match_score": round(match_score, 3),
                "critical_hits": critical_hits,
                "review_status": review_status,
                "notes": _notes(review_status, trust_order),
            }
        )
    return mapped


def _notes(review_status: str, trust_order: bool) -> str:
    if trust_order:
        return "Gold by trusted prompt order. Assumption: prompts were read in sheet order."
    if review_status == "gold":
        return "Gold by prompt order plus transcript/prompt match. Review before launch claims."
    return "Needs review: order suggests this prompt, but transcript/prompt match is weak."


def _to_corpus_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "audio": row["audio"],
        "gold": row["gold"],
        "category": "hinglish" if "hinglish" in row["mode"].lower() else "english",
        "critical": row["critical"],
        "source": "capture_sheet",
        "notes": row["notes"],
    }


def _row_text(row: dict[str, Any]) -> str:
    for key in ("pasted_text", "corrected_text", "raw_text"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return re.sub(r"\s+", " ", value).strip()
    return ""


def _release_to_paste(row: dict[str, Any]) -> float | None:
    timings = row.get("timings") if isinstance(row.get("timings"), dict) else {}
    value = timings.get("release_to_paste_seconds")
    return float(value) if isinstance(value, (int, float)) else None


def _token_overlap(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))


def _critical_hits(text: str, critical: list[str]) -> int:
    text_tokens = _tokens(text)
    lowered = text.lower()
    hits = 0
    for term in critical:
        normalized = term.lower().strip()
        if not normalized:
            continue
        if re.search(rf"\b{re.escape(normalized)}\b", lowered):
            hits += 1
            continue
        term_tokens = _tokens(normalized)
        if term_tokens and term_tokens.issubset(text_tokens):
            hits += 1
    return hits


def _tokens(text: str) -> set[str]:
    stop = {"a", "an", "and", "are", "as", "be", "but", "for", "from", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "with", "you"}
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9+#.]+", text)
        if len(token) > 1 and token.lower() not in stop
    }


def _render_html(rows: list[dict[str, Any]], marker: str) -> str:
    cards = "\n".join(_render_card(row) for row in rows)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>RambleFix Capture Sheet Review</title>
  <style>
    body {{ margin: 28px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f8f5; color: #16181d; }}
    header, .card {{ max-width: 980px; }}
    .meta {{ color: #62675f; }}
    .card {{ background: rgba(255,255,255,.86); border: 1px solid #dfe3da; border-radius: 8px; padding: 14px; margin: 12px 0; }}
    .row {{ display: grid; grid-template-columns: 150px 1fr; gap: 12px; margin: 8px 0; }}
    .label {{ color: #62675f; font-size: 13px; }}
    audio {{ width: 100%; }}
    textarea {{ width: 100%; min-height: 72px; box-sizing: border-box; border: 1px solid #ccd1c8; border-radius: 6px; padding: 8px; font: 14px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    code {{ background: #eef0eb; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <header>
    <h1>RambleFix Capture Sheet Review</h1>
    <p class="meta">Marker: <code>{html.escape(marker)}</code>. Rows: {len(rows)}. Gold rows require either trusted order or enough transcript/prompt match; review weak rows before launch claims.</p>
  </header>
  {cards}
</body>
</html>
"""


def _render_card(row: dict[str, Any]) -> str:
    return f"""
  <section class="card">
    <h2>{html.escape(str(row["capture_index"]))}. {html.escape(row["prompt_id"])} [{html.escape(row["review_status"])}]</h2>
    <div class="row"><div class="label">Audio</div><audio controls src="{html.escape(row["audio_abs"])}"></audio></div>
    <div class="row"><div class="label">Mode</div><div>{html.escape(row["mode"])}</div></div>
    <div class="row"><div class="label">Gold</div><textarea>{html.escape(row["gold"])}</textarea></div>
    <div class="row"><div class="label">Product Output</div><textarea>{html.escape(row["product_text"])}</textarea></div>
    <div class="row"><div class="label">Critical Terms</div><textarea>{html.escape(", ".join(row["critical"]))}</textarea></div>
    <div class="row"><div class="label">Evidence</div><div>match <code>{row["match_score"]}</code>, critical hits <code>{row["critical_hits"]}</code>, release-to-paste <code>{html.escape(str(row["release_to_paste_seconds"]))}</code>, run <code>{html.escape(row["history_run_id"])}</code></div></div>
    <div class="row"><div class="label">Note</div><div>{html.escape(row["notes"])}</div></div>
  </section>
"""


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


if __name__ == "__main__":
    main()
