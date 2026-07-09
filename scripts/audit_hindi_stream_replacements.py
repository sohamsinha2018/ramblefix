from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
DEFAULT_INPUT = ROOT / "eval_runs/fresh-hindi-probe-20260629/dense_15_style_guard_clean_20260630/results.json"
DEFAULT_OUTPUT_DIR = ROOT / "eval_runs/fresh-hindi-probe-20260629/hindi_replacement_audit_20260630"

from ramblefix.hindi_chunk_polish import hindi_value_delta


CONTENT_STOP = {
    "aap",
    "aapko",
    "agar",
    "aisa",
    "aur",
    "bhai",
    "bhi",
    "dekh",
    "haan",
    "hai",
    "hain",
    "hoga",
    "humein",
    "kaam",
    "karna",
    "karne",
    "kuch",
    "kya",
    "matlab",
    "mein",
    "nahi",
    "par",
    "right",
    "sab",
    "saath",
    "that",
    "theek",
    "this",
    "thik",
    "uske",
    "vagairah",
    "what",
    "when",
    "which",
    "will",
    "with",
    "woh",
    "yaar",
    "yeh",
}


@dataclass(frozen=True)
class AuditRow:
    run_id: str
    route: str
    decision: str
    confidence: str
    audio_seconds: float
    fast_release_to_paste: float | None
    hindi_tail_seconds: float | None
    draft_word_count: int
    final_word_count: int
    retained_draft_content_ratio: float
    new_final_content_count: int
    missing_draft_content_count: int
    protected_terms_preserved: bool
    draft_protected_terms: list[str]
    final_protected_terms: list[str]
    notes: list[str]
    fast_text: str
    final_text: str
    raw_text: str
    audio: str


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit whether Hindi stream replacements improve default meaning.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    rows = [_audit_row(row) for row in payload["rows"]]
    summary = _summary(rows, payload.get("summary") or {})

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_json = args.output_dir / "replacement_audit.json"
    output_md = args.output_dir / "replacement_audit.md"
    output_html = args.output_dir / "replacement_audit.html"
    output_json.write_text(
        json.dumps({"summary": summary, "rows": [asdict(row) for row in rows]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output_md.write_text(_markdown(summary, rows), encoding="utf-8")
    output_html.write_text(_html(summary, rows), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"wrote {output_json}")
    print(f"wrote {output_html}")


def _audit_row(row: dict[str, Any]) -> AuditRow:
    fast_text = _clean(row.get("fast_text") or "")
    final_text = _clean(row.get("text") or "")
    raw_text = _clean(row.get("raw_text") or "")
    replacement_text = final_text if row.get("safe_update") else fast_text

    draft_content = _content_tokens(fast_text)
    final_content = _content_tokens(replacement_text)
    missing = sorted(set(draft_content) - set(final_content))
    new = sorted(set(final_content) - set(draft_content))
    retained = 1.0
    if draft_content:
        retained = (len(set(draft_content) & set(final_content)) / len(set(draft_content)))

    draft_terms = _protected_terms(fast_text)
    final_terms = _protected_terms(replacement_text)
    protected_preserved = set(draft_terms).issubset(set(final_terms))
    notes: list[str] = []
    if not row.get("safe_update"):
        decision = "keep_fast"
        confidence = "high" if row.get("route") in {"hindi_stream_no_risk", "hindi_stream_rejected"} else "medium"
        notes.extend(str(reason) for reason in row.get("reject_reasons") or [])
    else:
        if not protected_preserved:
            decision = "reject_drop"
            confidence = "high"
            notes.append("drops protected terms")
        elif retained < 0.78:
            hindi_value = hindi_value_delta(fast_text, replacement_text)
            if retained >= 0.65 and len(hindi_value["substantive_new_roman_hindi_tokens"]) >= 5:
                decision = "replace_candidate"
                confidence = "medium"
                notes.append("meaningful Hinglish replacement; English-token audit undercounts it")
            else:
                decision = "reject_drop"
                confidence = "medium"
                notes.append("drops too much draft content")
        elif len(new) == 0 and retained >= 0.95:
            decision = "save_alternate"
            confidence = "high"
            notes.append("no new default-meaning content")
        elif len(new) <= 2 and retained >= 0.90 and _mostly_hindi_or_function_words(new):
            decision = "save_alternate"
            confidence = "medium"
            notes.append("mostly phrasing/verbatim change")
        elif retained >= 0.82:
            decision = "replace_candidate"
            confidence = "medium"
            notes.append("candidate may add missing spoken meaning; audio review still needed")
        else:
            decision = "needs_review"
            confidence = "low"
            notes.append("unclear meaning tradeoff")

    return AuditRow(
        run_id=str(row["run_id"]),
        route=str(row.get("route") or ""),
        decision=decision,
        confidence=confidence,
        audio_seconds=float(row.get("audio_seconds") or 0.0),
        fast_release_to_paste=row.get("fast_release_to_paste"),
        hindi_tail_seconds=row.get("release_tail_seconds"),
        draft_word_count=_word_count(fast_text),
        final_word_count=_word_count(replacement_text),
        retained_draft_content_ratio=round(retained, 3),
        new_final_content_count=len(new),
        missing_draft_content_count=len(missing),
        protected_terms_preserved=protected_preserved,
        draft_protected_terms=draft_terms,
        final_protected_terms=final_terms,
        notes=notes,
        fast_text=fast_text,
        final_text=replacement_text,
        raw_text=raw_text,
        audio=str(row.get("audio") or ""),
    )


def _summary(rows: list[AuditRow], eval_summary: dict[str, Any]) -> dict[str, Any]:
    decisions = sorted({row.decision for row in rows})
    accepted = [row for row in rows if row.route == "hindi_stream_safe"]
    replace_candidates = [row for row in rows if row.decision == "replace_candidate"]
    alternates = [row for row in rows if row.decision == "save_alternate"]
    rejects = [row for row in rows if row.decision in {"reject_drop", "keep_fast"}]
    return {
        "rows": len(rows),
        "stream_safe_updates": len(accepted),
        "replace_candidates_after_audit": len(replace_candidates),
        "save_alternate_after_audit": len(alternates),
        "keep_fast_or_reject_after_audit": len(rejects),
        "decision_counts": {decision: sum(1 for row in rows if row.decision == decision) for decision in decisions},
        "hindi_stream_tail_p95": eval_summary.get("hindi_stream_tail_p95"),
        "fast_release_to_paste_p95": eval_summary.get("fast_release_to_paste_p95"),
        "local_only": True,
    }


def _markdown(summary: dict[str, Any], rows: list[AuditRow]) -> str:
    lines = ["# Hindi Stream Replacement Audit", ""]
    lines.extend(f"- {key}: `{value}`" for key, value in summary.items())
    lines.extend(
        [
            "",
            "| clip | route | decision | conf | retained | new | missing | fast | final | notes |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row.run_id} | {row.route} | {row.decision} | {row.confidence} | "
            f"{row.retained_draft_content_ratio:.3f} | {row.new_final_content_count} | "
            f"{row.missing_draft_content_count} | {_short(row.fast_text)} | {_short(row.final_text)} | "
            f"{', '.join(row.notes)} |"
        )
    return "\n".join(lines) + "\n"


def _html(summary: dict[str, Any], rows: list[AuditRow]) -> str:
    cards = "\n".join(_html_card(row) for row in rows)
    summary_items = "\n".join(f"<li><code>{html.escape(str(key))}</code>: {html.escape(str(value))}</li>" for key, value in summary.items())
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Hindi Stream Replacement Audit</title>
  <style>
    body {{ margin: 24px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #15171a; background: #f7f8f5; }}
    header, section {{ max-width: 1120px; }}
    section {{ background: rgba(255,255,255,.88); border: 1px solid #dfe3da; border-radius: 8px; margin: 12px 0; padding: 14px; }}
    .replace_candidate {{ border-left: 5px solid #35a060; }}
    .save_alternate {{ border-left: 5px solid #d89b20; }}
    .reject_drop, .keep_fast {{ border-left: 5px solid #cc4b4b; }}
    .meta {{ color: #61665f; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: 120px 1fr; gap: 8px 12px; }}
    audio {{ width: 100%; }}
    code {{ background: #eef0ea; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <header>
    <h1>Hindi Stream Replacement Audit</h1>
    <ul>{summary_items}</ul>
  </header>
  {cards}
</body>
</html>
"""


def _html_card(row: AuditRow) -> str:
    audio = html.escape(str((ROOT / row.audio).resolve()) if row.audio and not Path(row.audio).is_absolute() else row.audio)
    return f"""
  <section class="{html.escape(row.decision)}">
    <h2>{html.escape(row.run_id)} <code>{html.escape(row.decision)}</code></h2>
    <p class="meta">route={html.escape(row.route)} confidence={html.escape(row.confidence)} retained={row.retained_draft_content_ratio} new={row.new_final_content_count} missing={row.missing_draft_content_count}</p>
    <div class="grid">
      <div>Audio</div><div><audio controls src="{audio}"></audio></div>
      <div>Fast</div><div>{html.escape(row.fast_text)}</div>
      <div>Final</div><div>{html.escape(row.final_text)}</div>
      <div>Raw</div><div>{html.escape(row.raw_text)}</div>
      <div>Notes</div><div>{html.escape(", ".join(row.notes))}</div>
    </div>
  </section>
"""


def _mostly_hindi_or_function_words(tokens: list[str]) -> bool:
    return all(token in CONTENT_STOP or len(token) <= 4 for token in tokens)


def _protected_terms(text: str) -> list[str]:
    terms = set()
    for raw in re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:'[A-Za-z]+)?\b", text):
        token = raw.split("'", 1)[0]
        if token.isupper() and len(token) >= 2:
            terms.add(token.lower())
        elif len(token) >= 4 and token[0].isupper() and any(char.isupper() for char in token[1:]):
            terms.add(token.lower())
    return sorted(terms)


def _content_tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z0-9]+", text.lower())
        if len(token) >= 4 and token not in CONTENT_STOP
    ]


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", text))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _short(text: str, width: int = 120) -> str:
    text = _clean(text).replace("|", "/")
    return text if len(text) <= width else text[: width - 3] + "..."


if __name__ == "__main__":
    main()
