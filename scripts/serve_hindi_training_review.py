from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW = ROOT / "eval_runs/fresh-hindi-probe-20260629/hindi_training_review_20260630/review_set.json"
DEFAULT_SCORECARD = ROOT / "eval_runs/fresh-hindi-probe-20260629/hindi_training_review_20260630/scorecard.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a tiny local Hindi review labeler.")
    parser.add_argument("--review-json", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--scorecard-json", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    review_path = args.review_json.expanduser().resolve()
    scorecard_path = args.scorecard_json.expanduser().resolve()
    if not review_path.exists():
        raise FileNotFoundError(review_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                self._send_text(_html(), content_type="text/html; charset=utf-8")
                return
            if path == "/api/review":
                self._send_json(_load_review(review_path))
                return
            if path == "/api/scorecard":
                self._send_json(_load_scorecard(scorecard_path))
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/labels":
                payload = self._read_json()
                review = _save_labels(review_path, payload)
                self._send_json({"ok": True, "review": review})
                return
            if path == "/api/score":
                result = _run_score(review_path, scorecard_path)
                self._send_json(result)
                return
            self.send_error(404)

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"{self.address_string()} - {fmt % args}", flush=True)

        def _read_json(self) -> dict:
            length = int(self.headers.get("content-length", "0") or "0")
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            return json.loads(raw)

        def _send_json(self, payload: object) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_text(self, text: str, *, content_type: str) -> None:
            data = text.encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"serving Hindi review labeler on http://{args.host}:{args.port}", flush=True)
    print(f"review_json={review_path}", flush=True)
    server.serve_forever()


def _load_review(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_scorecard(path: Path) -> dict:
    if not path.exists():
        return {"status": "missing"}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_labels(path: Path, payload: dict) -> dict:
    labels = payload.get("labels")
    if not isinstance(labels, dict):
        raise ValueError("payload.labels must be an object keyed by run_id")

    review = _load_review(path)
    rows = review.get("rows") or []
    changed = False
    for row in rows:
        run_id = str(row.get("run_id") or "")
        update = labels.get(run_id)
        if not isinstance(update, dict):
            continue
        gold = str(update.get("gold_intent") or "")
        notes = str(update.get("notes") or "")
        if row.get("gold_intent", "") != gold or row.get("notes", "") != notes:
            row["gold_intent"] = gold
            row["notes"] = notes
            changed = True

    if changed:
        backup = path.with_suffix(f".{int(time.time())}.bak.json")
        shutil.copy2(path, backup)
        path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return review


def _run_score(review_path: Path, scorecard_path: Path) -> dict:
    cmd = [
        sys.executable,
        str(ROOT / "scripts/score_hindi_training_review.py"),
        "--review-json",
        str(review_path),
        "--output",
        str(scorecard_path),
    ]
    completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=30)
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "scorecard": _load_scorecard(scorecard_path) if scorecard_path.exists() else None,
    }


def _html() -> str:
    return r"""<!doctype html>
<meta charset="utf-8">
<title>RambleFix Hindi Labels</title>
<style>
:root { font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif; color: #171513; background: #f7f5f2; }
body { margin: 0; }
header { position: sticky; top: 0; z-index: 2; padding: 14px 22px; background: rgba(247,245,242,.9); backdrop-filter: blur(14px); border-bottom: 1px solid rgba(0,0,0,.08); display: flex; gap: 12px; align-items: center; justify-content: space-between; }
h1 { margin: 0; font-size: 18px; letter-spacing: 0; }
button { border: 1px solid rgba(0,0,0,.14); background: #171513; color: #fff; border-radius: 7px; padding: 8px 11px; font: inherit; cursor: pointer; }
button.secondary { background: #fff; color: #171513; }
main { max-width: 1180px; margin: 0 auto; padding: 18px 22px 60px; }
.status { display: flex; gap: 8px; flex-wrap: wrap; font-size: 12px; color: #514c45; }
.pill { background: #fff; border: 1px solid rgba(0,0,0,.08); border-radius: 999px; padding: 5px 9px; }
.clip { background: rgba(255,255,255,.78); border: 1px solid rgba(0,0,0,.08); border-radius: 8px; padding: 14px; margin: 12px 0; box-shadow: 0 6px 22px rgba(0,0,0,.04); }
.head { display: flex; gap: 12px; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.id { font-size: 14px; font-weight: 700; }
audio { width: 330px; max-width: 100%; height: 32px; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
.box { border: 1px solid rgba(0,0,0,.07); background: rgba(250,250,250,.92); border-radius: 7px; padding: 9px; }
.box h3 { margin: 0 0 5px; color: #756d63; font-size: 11px; letter-spacing: .04em; text-transform: uppercase; }
.box p { margin: 0; white-space: pre-wrap; font-size: 13px; line-height: 1.38; }
textarea { width: 100%; box-sizing: border-box; min-height: 84px; border: 1px solid rgba(0,0,0,.14); border-radius: 7px; padding: 9px; font: 14px/1.4 inherit; resize: vertical; background: #fff; }
.gold { margin-top: 9px; display: grid; grid-template-columns: minmax(0, 2fr) minmax(220px, 1fr); gap: 9px; }
.suggestion { margin-top: 9px; border: 1px solid rgba(138,90,0,.20); background: rgba(255,247,230,.72); border-radius: 7px; padding: 9px; }
.suggestion .top { display: flex; gap: 8px; justify-content: space-between; align-items: center; margin-bottom: 5px; }
.suggestion p { margin: 0; white-space: pre-wrap; font-size: 13px; line-height: 1.38; }
.suggestion button { padding: 5px 8px; background: #fff; color: #171513; }
.muted { color: #7a7267; font-size: 12px; }
.bad { color: #9b1c1c; } .ok { color: #146c43; } .warn { color: #8a5a00; }
pre { white-space: pre-wrap; background: #fff; border: 1px solid rgba(0,0,0,.08); border-radius: 7px; padding: 10px; max-height: 240px; overflow: auto; }
@media (max-width: 820px) { .grid, .gold { grid-template-columns: 1fr; } .head, header { align-items: flex-start; flex-direction: column; } }
</style>
<header>
  <div>
    <h1>RambleFix Hindi Labels</h1>
    <div class="status" id="summary"></div>
  </div>
  <div>
    <button class="secondary" onclick="score()">Score</button>
    <button onclick="save()">Save Labels</button>
  </div>
</header>
<main>
  <p class="pill">Label intended meaning. Clean English is fine if it preserves meaning; Roman Hinglish is fine when it preserves Hindi better.</p>
  <div id="clips"></div>
  <h2>Score</h2>
  <pre id="score">No score yet.</pre>
</main>
<script>
let review = null;

async function load() {
  review = await fetch('/api/review').then(r => r.json());
  render();
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

function render() {
  const labeled = review.rows.filter(r => (r.gold_intent || '').trim()).length;
  document.getElementById('summary').innerHTML = [
    ['rows', review.rows.length],
    ['labeled', labeled],
    ['risk', review.summary.risk_count],
    ['safe', review.summary.safe_update_count],
    ['p95 tail', review.summary.hindi_stream_tail_p95 + 's'],
  ].map(([k,v]) => `<span class="pill">${esc(k)}: <b>${esc(v)}</b></span>`).join('');
  document.getElementById('clips').innerHTML = review.rows.map(row => {
    const reject = (row.reject_reasons || []).join(', ');
    return `<section class="clip">
      <div class="head">
        <div>
          <div class="id">${esc(row.run_id)}</div>
          <div class="status">
            <span class="${row.safe_update ? 'ok' : row.risk ? 'warn' : ''}">route: ${esc(row.route)}</span>
            <span>dur: ${esc(row.audio_seconds)}s</span>
            <span>tail: ${esc(row.tail_seconds)}s</span>
            ${reject ? `<span class="bad">reject: ${esc(reject)}</span>` : ''}
          </div>
        </div>
        <audio controls preload="none" src="file://${esc(row.audio)}"></audio>
      </div>
      <div class="grid">
        ${box('Fast', row.fast_text)}
        ${box('Srota raw', row.srota_raw)}
        ${box('Current final', row.current_final)}
        ${box('Vosk witness', row.vosk_hi_large)}
      </div>
      ${suggestion(row)}
      <div class="gold">
        <div>
          <div class="muted">Gold intent</div>
          <textarea data-run="${esc(row.run_id)}" data-field="gold_intent">${esc(row.gold_intent)}</textarea>
        </div>
        <div>
          <div class="muted">Notes</div>
          <textarea data-run="${esc(row.run_id)}" data-field="notes">${esc(row.notes)}</textarea>
        </div>
      </div>
    </section>`;
  }).join('');
}

function box(title, text) {
  return `<div class="box"><h3>${esc(title)}</h3><p>${esc(text)}</p></div>`;
}

function suggestion(row) {
  const text = row.suggested_gold_intent || '';
  if (!text.trim()) return '';
  return `<div class="suggestion">
    <div class="top">
      <div class="muted">Suggestion: ${esc(row.suggested_gold_source || 'generated')}</div>
      <button type="button" onclick="useSuggestion('${esc(row.run_id)}')">Use suggestion</button>
    </div>
    <p>${esc(text)}</p>
    <div class="muted">${esc(row.suggested_gold_warning || 'Suggestion is not a gold label until copied/edited.')}</div>
  </div>`;
}

function useSuggestion(runId) {
  const row = review.rows.find(item => item.run_id === runId);
  if (!row) return;
  const area = document.querySelector(`textarea[data-run="${CSS.escape(runId)}"][data-field="gold_intent"]`);
  if (area) area.value = row.suggested_gold_intent || '';
}

function collectLabels() {
  const labels = {};
  for (const area of document.querySelectorAll('textarea[data-run]')) {
    const run = area.dataset.run;
    labels[run] ||= {};
    labels[run][area.dataset.field] = area.value;
  }
  return labels;
}

async function save() {
  const payload = { labels: collectLabels() };
  const response = await fetch('/api/labels', { method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(payload) });
  const data = await response.json();
  review = data.review;
  render();
}

async function score() {
  await save();
  const response = await fetch('/api/score', { method: 'POST' });
  const data = await response.json();
  document.getElementById('score').textContent = data.stdout || data.stderr || JSON.stringify(data, null, 2);
}

load();
</script>
"""


if __name__ == "__main__":
    main()
