from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an HTML review page for non-confirmed cloud ASR rows.")
    parser.add_argument("--crosscheck", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--status", default="needs_human_review")
    args = parser.parse_args()

    rows = json.loads(args.crosscheck.read_text(encoding="utf-8"))
    wanted = [row for row in rows if str(row.get("cloud_status") or "") == args.status]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_render(wanted, source=args.crosscheck), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "rows": len(wanted)}, indent=2))


def _render(rows: list[dict[str, Any]], *, source: Path) -> str:
    cards = "\n".join(_card(row) for row in rows)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>RambleFix Cloud Gold Review</title>
  <style>
    body {{ margin: 28px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f8f5; color: #16181d; }}
    header, section {{ max-width: 1120px; }}
    section {{ background: rgba(255,255,255,.88); border: 1px solid #dde3db; border-radius: 8px; padding: 14px; margin: 14px 0; }}
    h1 {{ margin-bottom: 6px; }}
    h2 {{ font-size: 17px; margin: 0 0 10px; }}
    audio {{ width: 100%; height: 34px; }}
    .meta {{ color: #63685f; font-size: 13px; }}
    .row {{ display: grid; grid-template-columns: 150px 1fr; gap: 12px; margin: 8px 0; }}
    .label {{ color: #63685f; }}
    .transcript {{ white-space: pre-wrap; line-height: 1.45; }}
    code {{ background: #edf0ea; border-radius: 4px; padding: 2px 5px; }}
  </style>
</head>
<body>
  <header>
    <h1>RambleFix Cloud Gold Review</h1>
    <p class="meta">Source: {html.escape(_rel(source))}. These rows are not claim-grade until reviewed or rerun.</p>
  </header>
  {cards}
</body>
</html>
"""


def _card(row: dict[str, Any]) -> str:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    results = meta.get("cloud_results") if isinstance(meta.get("cloud_results"), list) else []
    result_html = "\n".join(_result_block(item) for item in results if isinstance(item, dict))
    audio = str(Path(str(row.get("audio") or "")).expanduser())
    return f"""
  <section>
    <h2>{html.escape(str(row.get("id") or ""))} <code>{html.escape(str(row.get("bucket") or ""))}</code></h2>
    <div class="row"><div class="label">Audio</div><div><audio controls src="{html.escape(audio)}"></audio></div></div>
    <div class="row"><div class="label">Reason</div><div>{html.escape(str(row.get("classification_reason") or ""))}</div></div>
    <div class="row"><div class="label">Selected gold</div><div class="transcript">{html.escape(str(row.get("gold") or ""))}</div></div>
    <div class="row"><div class="label">Cloud results</div><div>{result_html}</div></div>
  </section>
"""


def _result_block(item: dict[str, Any]) -> str:
    status = "ok" if item.get("ok") else "error"
    transcript = str(item.get("transcript") or item.get("error") or "")
    return (
        f"<p><strong>{html.escape(str(item.get('model') or ''))}</strong> "
        f"<code>{html.escape(status)}</code> "
        f"<code>{html.escape(str(item.get('language_class') or ''))}</code> "
        f"<span class=\"meta\">{html.escape(str(item.get('seconds') or ''))}s</span><br>"
        f"<span class=\"transcript\">{html.escape(transcript)}</span></p>"
    )


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    main()
