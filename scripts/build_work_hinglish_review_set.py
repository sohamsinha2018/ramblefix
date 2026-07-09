from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.learning_memory import extract_terms


WORK_KEYWORDS = {
    "ai",
    "api",
    "asr",
    "bcom",
    "builder",
    "chatgpt",
    "cloud",
    "codex",
    "corpus",
    "cursor",
    "data",
    "eval",
    "goal",
    "hinglish",
    "latency",
    "local",
    "metric",
    "mcp",
    "model",
    "prompt",
    "regression",
    "skill",
    "stt",
    "tool",
    "transcript",
    "ux",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an auditable work/Hinglish review set from corpus + retained history audio.")
    parser.add_argument("--corpus", type=Path, default=ROOT / "eval_corpus/ramblefix_corpus.json")
    parser.add_argument("--history", type=Path, default=ROOT / "logs/history.jsonl")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-history", type=int, default=80)
    parser.add_argument("--run-product", action="store_true")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = _seed_rows(args.corpus)
    rows.extend(_history_candidate_rows(args.history, limit=args.max_history, existing_ids={row["id"] for row in rows}))
    rows = sorted(rows, key=lambda row: (row["review_status"] != "gold", row["id"]))

    if args.run_product:
        for row in rows:
            product = _run_product_transcript(Path(row["audio_abs"]))
            row["product_text"] = product.get("text", "")
            row["product_seconds"] = product.get("seconds")
            row["product_engine"] = product.get("engine", "")
            row["product_processor"] = product.get("processor", "")

    review_json = args.output_dir / "work_hinglish_review_set.json"
    review_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path = args.output_dir / "work_hinglish_review.html"
    html_path.write_text(_render_html(rows), encoding="utf-8")
    gold_path = args.output_dir / "gold_ready_corpus.json"
    gold_rows = [_to_corpus_row(row) for row in rows if row["review_status"] == "gold"]
    gold_path.write_text(json.dumps(gold_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"rows={len(rows)} gold={len(gold_rows)} needs_review={len(rows)-len(gold_rows)}")
    print(f"review_json={review_json}")
    print(f"html={html_path}")
    print(f"gold_ready_corpus={gold_path}")


def _seed_rows(corpus_path: Path) -> list[dict[str, Any]]:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for item in corpus:
        category = str(item.get("category") or "").lower()
        if category not in {"english", "hinglish"}:
            continue
        audio = (ROOT / str(item.get("audio") or "")).resolve()
        if not audio.exists():
            continue
        gold = str(item.get("gold") or "").strip()
        rows.append(
            {
                "id": str(item["id"]),
                "source": "eval_corpus",
                "category": category,
                "audio": _rel(audio),
                "audio_abs": str(audio),
                "candidate_gold": gold,
                "review_status": "gold" if gold else "needs_review",
                "suggested_critical_terms": _critical_terms(item, gold),
                "notes": str(item.get("notes") or ""),
                "history_created_at": "",
                "history_status": "",
                "history_error_type": "",
                "history_route": "",
                "history_duration_seconds": None,
                "product_text": "",
                "product_seconds": None,
                "product_engine": "",
                "product_processor": "",
            }
        )
    return rows


def _history_candidate_rows(history_path: Path, *, limit: int, existing_ids: set[str]) -> list[dict[str, Any]]:
    if not history_path.exists():
        return []
    parsed: list[dict[str, Any]] = []
    for line in history_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            parsed.append(payload)

    rows: list[dict[str, Any]] = []
    for row in parsed:
        audio_raw = str(row.get("audio_path") or "")
        if not audio_raw:
            continue
        audio = Path(audio_raw).expanduser().resolve()
        if not audio.exists():
            continue
        status = str(row.get("status") or "")
        error_type = str(row.get("error_type") or "")
        if status in {"too_short", "no_speech"} or error_type in {"too_short_capture", "blank_or_no_speech"}:
            continue
        duration = _duration(row)
        if duration is not None and duration < 1.5:
            continue
        text = _history_text(row)
        if _is_asr_failure_text(text):
            continue
        if len(text) < 25 or not _looks_work_like(text):
            continue
        candidate_id = f"history_{str(row.get('run_id') or audio.stem).replace('-', '_')}"
        if candidate_id in existing_ids:
            continue
        rows.append(
            {
                "id": candidate_id,
                "source": "history",
                "category": "work_hinglish_candidate",
                "audio": _rel(audio),
                "audio_abs": str(audio),
                "candidate_gold": text,
                "review_status": "needs_review",
                "suggested_critical_terms": _suggest_terms(text),
                "notes": "Candidate from retained hotkey history. Review before using as gold.",
                "history_created_at": str(row.get("created_at") or ""),
                "history_status": status,
                "history_error_type": error_type,
                "history_route": str(row.get("route") or ""),
                "history_duration_seconds": duration,
                "product_text": "",
                "product_seconds": None,
                "product_engine": "",
                "product_processor": "",
            }
        )
    return rows


def _history_text(row: dict[str, Any]) -> str:
    for key in ("corrected_text", "pasted_text", "raw_text"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_text(value)
    return ""


def _clean_text(text: str) -> str:
    text = re.sub(r"\[(?:BLANK_AUDIO|NOISE|SILENCE|INAUDIBLE)\]", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _looks_work_like(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(rf"\b{re.escape(keyword)}\b", lowered) for keyword in WORK_KEYWORDS)


def _is_asr_failure_text(text: str) -> bool:
    lowered = text.lower()
    return (
        "asr failure detected" in lowered
        or "transcript looks empty or repetitive" in lowered
        or "retry with language set" in lowered
    )


def _duration(row: dict[str, Any]) -> float | None:
    timings = row.get("timings") if isinstance(row.get("timings"), dict) else {}
    for value in (timings.get("audio_duration_seconds"), row.get("quality_flags", {}).get("audio_duration_seconds") if isinstance(row.get("quality_flags"), dict) else None):
        if isinstance(value, (int, float)):
            return round(float(value), 3)
    return None


def _critical_terms(item: dict[str, Any], gold: str) -> list[str]:
    explicit = item.get("critical") or item.get("critical_terms") or item.get("terms") or item.get("anchors")
    if isinstance(explicit, list) and explicit:
        return [str(term) for term in explicit if str(term).strip()]
    return _suggest_terms(gold)


def _suggest_terms(text: str) -> list[str]:
    terms = extract_terms(text)
    lowered = text.lower()
    for keyword in sorted(WORK_KEYWORDS):
        if re.search(rf"\b{re.escape(keyword)}\b", lowered):
            terms.append(keyword.upper() if keyword in {"ai", "api", "asr", "stt", "mcp", "ux"} else keyword)
    return _dedupe(terms)[:12]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value.strip())
    return out


def _run_product_transcript(audio: Path) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [str(ROOT / ".venv/bin/python"), "-m", "ramblefix.cli", "dictate-audio", str(audio), "--json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        payload = json.loads(proc.stdout)
        return payload if isinstance(payload, dict) else {"text": ""}
    except Exception as exc:  # noqa: BLE001 - review artifact should record failures and continue.
        return {"text": "", "error": f"{type(exc).__name__}: {exc}"}


def _to_corpus_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "audio": row["audio"],
        "gold": row["candidate_gold"],
        "category": "hinglish" if "hinglish" in row["category"] else row["category"],
        "critical": row["suggested_critical_terms"],
        "source": row["source"],
        "notes": row["notes"],
    }


def _render_html(rows: list[dict[str, Any]]) -> str:
    cards = "\n".join(_render_card(row) for row in rows)
    gold = sum(1 for row in rows if row["review_status"] == "gold")
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>RambleFix Work Hinglish Review Set</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; background: #f7f7f4; color: #181818; }}
    header {{ max-width: 980px; margin-bottom: 24px; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; }}
    .meta {{ color: #5f625d; }}
    .card {{ max-width: 980px; background: rgba(255,255,255,.82); border: 1px solid #deded8; border-radius: 8px; padding: 16px; margin: 12px 0; }}
    .row {{ display: grid; grid-template-columns: 160px 1fr; gap: 12px; margin: 8px 0; }}
    .label {{ color: #686b66; font-size: 13px; }}
    textarea {{ width: 100%; min-height: 86px; box-sizing: border-box; font: 14px ui-monospace, SFMono-Regular, Menlo, monospace; border: 1px solid #ccc; border-radius: 6px; padding: 8px; }}
    code {{ background: #eee; padding: 2px 4px; border-radius: 4px; }}
    audio {{ width: 100%; }}
    .gold {{ color: #166534; }}
    .review {{ color: #92400e; }}
  </style>
</head>
<body>
  <header>
    <h1>RambleFix Work Hinglish Review Set</h1>
    <div class="meta">Rows: {len(rows)}. Gold-ready: {gold}. Needs review: {len(rows)-gold}.</div>
    <p class="meta">Use this page to listen, correct candidate gold, and verify critical terms before adding rows to the benchmark.</p>
  </header>
  {cards}
</body>
</html>
"""


def _render_card(row: dict[str, Any]) -> str:
    status_class = "gold" if row["review_status"] == "gold" else "review"
    return f"""
  <section class="card">
    <h2>{html.escape(row["id"])} <span class="{status_class}">[{html.escape(row["review_status"])}]</span></h2>
    <div class="row"><div class="label">Audio</div><audio controls src="{html.escape(row["audio_abs"])}"></audio></div>
    <div class="row"><div class="label">Source</div><div>{html.escape(row["source"])} / {html.escape(row["category"])}</div></div>
    <div class="row"><div class="label">Candidate Gold</div><textarea>{html.escape(row["candidate_gold"])}</textarea></div>
    <div class="row"><div class="label">Product Output</div><textarea>{html.escape(str(row.get("product_text") or ""))}</textarea></div>
    <div class="row"><div class="label">Critical Terms</div><textarea>{html.escape(", ".join(row["suggested_critical_terms"]))}</textarea></div>
    <div class="row"><div class="label">Timing</div><div>history duration: <code>{html.escape(str(row.get("history_duration_seconds") or ""))}</code> product seconds: <code>{html.escape(str(row.get("product_seconds") or ""))}</code></div></div>
    <div class="row"><div class="label">Notes</div><div>{html.escape(row["notes"])}</div></div>
  </section>
"""


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


if __name__ == "__main__":
    main()
