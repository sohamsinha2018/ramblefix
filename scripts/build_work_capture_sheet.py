from __future__ import annotations

import argparse
import html
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


PROMPTS: list[dict[str, Any]] = [
    {
        "id": "english_cursor_codex_prompt",
        "mode": "English work",
        "text": "Create a clean prompt for Cursor and Codex, but do not lose the meaning.",
        "critical": ["Cursor", "Codex", "prompt", "meaning"],
    },
    {
        "id": "english_local_company_data",
        "mode": "English work",
        "text": "This tool should run locally because company data cannot go to the cloud.",
        "critical": ["local", "company data", "cloud"],
    },
    {
        "id": "english_score_drawdown",
        "mode": "English work",
        "text": "Is the score really risk adjusted, or is the drawdown hiding the actual problem?",
        "critical": ["score", "risk adjusted", "drawdown"],
    },
    {
        "id": "english_builder_metric",
        "mode": "English work",
        "text": "For the Builder challenge, optimize meaning first and then reduce release to paste latency.",
        "critical": ["Builder", "meaning", "release to paste", "latency"],
    },
    {
        "id": "english_regression_gate",
        "mode": "English work",
        "text": "Run the regression gate before changing the native hotkey or paste behavior.",
        "critical": ["regression gate", "native hotkey", "paste"],
    },
    {
        "id": "english_mcp_kernel_compiler",
        "mode": "English work",
        "text": "Check if the MCP server, AI kernel, compiler, and Kubernetes terms are preserved.",
        "critical": ["MCP", "AI", "kernel", "compiler", "Kubernetes"],
    },
    {
        "id": "english_stanford_report",
        "mode": "English work",
        "text": "Search the Stanford AI report and pull out the benchmarks for local models.",
        "critical": ["Stanford AI report", "benchmarks", "local models"],
    },
    {
        "id": "english_product_wedge",
        "mode": "English work",
        "text": "The product wedge is free, local, fast dictation for Indian work speech.",
        "critical": ["free", "local", "fast dictation", "Indian work speech"],
    },
    {
        "id": "english_capture_eval_audio",
        "mode": "English work",
        "text": "Turn on Capture Eval Audio so every successful dictation can be replayed and scored.",
        "critical": ["Capture Eval Audio", "successful dictation", "replayed", "scored"],
    },
    {
        "id": "english_review_packet",
        "mode": "English work",
        "text": "Build the review packet, show the audio, candidate gold, product output, and critical terms.",
        "critical": ["review packet", "audio", "candidate gold", "product output", "critical terms"],
    },
    {
        "id": "hinglish_local_cloud",
        "mode": "Light Hinglish",
        "text": "Yeh tool local hona chahiye because company data cloud mein nahi jaana chahiye.",
        "critical": ["local", "company data", "cloud"],
    },
    {
        "id": "hinglish_prd_stt",
        "mode": "Light Hinglish",
        "text": "Mujhe local STT app ke liye ek PRD draft karna hai with latency and privacy metrics.",
        "critical": ["local", "STT", "PRD", "latency", "privacy metrics"],
    },
    {
        "id": "hinglish_cursor_bug",
        "mode": "Light Hinglish",
        "text": "Cursor mein jo paste bug aa raha hai, uska root cause samjho and fix karo.",
        "critical": ["Cursor", "paste bug", "root cause", "fix"],
    },
    {
        "id": "hinglish_codex_skill",
        "mode": "Light Hinglish",
        "text": "Codex skill mein yeh rule add karo ki har iteration metric ke against check ho.",
        "critical": ["Codex", "skill", "iteration", "metric"],
    },
    {
        "id": "hinglish_regression",
        "mode": "Light Hinglish",
        "text": "Regression test chalaye bina native app ko update mat karo.",
        "critical": ["regression test", "native app", "update"],
    },
    {
        "id": "hinglish_builder_score",
        "mode": "Light Hinglish",
        "text": "Builder score tab improve hoga jab meaning preserve hoga aur latency low rahegi.",
        "critical": ["Builder score", "meaning", "latency"],
    },
    {
        "id": "hinglish_meeting_summary",
        "mode": "Light Hinglish",
        "text": "Meeting ke baad mujhe concise summary chahiye with decisions, blockers, and next steps.",
        "critical": ["meeting", "summary", "decisions", "blockers", "next steps"],
    },
    {
        "id": "hinglish_model_runtime",
        "mode": "Light Hinglish",
        "text": "Model ka naam mat dekho, runtime check karo ki GPU pe chal raha hai ya CPU pe.",
        "critical": ["model", "runtime", "GPU", "CPU"],
    },
    {
        "id": "hinglish_eval_corpus",
        "mode": "Light Hinglish",
        "text": "Eval corpus mein pure Hindi nahi, real urban Hindi plus English work examples chahiye.",
        "critical": ["eval corpus", "Hindi plus English", "work examples"],
    },
    {
        "id": "hinglish_work_terms",
        "mode": "Light Hinglish",
        "text": "MCP, Codex, Cursor, Kubernetes, aur compiler jaise work terms miss nahi hone chahiye.",
        "critical": ["MCP", "Codex", "Cursor", "Kubernetes", "compiler"],
    },
    {
        "id": "indian_english_agoda_bcom",
        "mode": "Indian English terms",
        "text": "Check whether Agoda, BCom, Priceline, OpenAI, and Claude are transcribed correctly.",
        "critical": ["Agoda", "BCom", "Priceline", "OpenAI", "Claude"],
    },
    {
        "id": "indian_english_strategy",
        "mode": "Indian English work",
        "text": "Think like a product builder and tell me the fastest path to test this strategy.",
        "critical": ["product builder", "fastest path", "strategy"],
    },
    {
        "id": "indian_english_permission",
        "mode": "Indian English work",
        "text": "Stop asking for permission on every step and just run the local regression gate.",
        "critical": ["permission", "local", "regression gate"],
    },
    {
        "id": "indian_english_audio_quality",
        "mode": "Indian English work",
        "text": "If the audio volume is low, normalize it before judging model quality.",
        "critical": ["audio volume", "normalize", "model quality"],
    },
    {
        "id": "indian_english_finalizer",
        "mode": "Indian English work",
        "text": "The finalizer can update the transcript later, but the first paste must be fast.",
        "critical": ["finalizer", "transcript", "first paste", "fast"],
    },
    {
        "id": "hinglish_no_hardcode",
        "mode": "Light Hinglish",
        "text": "Hard coded fallback mat lagao; pattern safe ho tabhi correction apply karo.",
        "critical": ["hard coded", "fallback", "pattern", "correction"],
    },
    {
        "id": "hinglish_user_memory",
        "mode": "Light Hinglish",
        "text": "Agar user bole skill not skid, toh next time skid ko skill correct karna chahiye.",
        "critical": ["skill", "skid", "correct"],
    },
    {
        "id": "english_no_blank",
        "mode": "English reliability",
        "text": "Never paste blank audio or low quality failure text into the active editor.",
        "critical": ["blank audio", "low quality", "active editor"],
    },
    {
        "id": "hinglish_latency_target",
        "mode": "Light Hinglish",
        "text": "Release ke baad output do seconds ke andar aana chahiye, warna UX weak lagega.",
        "critical": ["release", "two seconds", "UX"],
    },
    {
        "id": "english_representative_corpus",
        "mode": "English work",
        "text": "Do not optimize on random smoke clips; use representative work speech for the corpus.",
        "critical": ["smoke clips", "representative work speech", "corpus"],
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a targeted RambleFix work/Hinglish capture sheet.")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--marker", default="")
    args = parser.parse_args()

    marker = args.marker or _utc_now()
    output_dir = args.output_dir or ROOT / "eval_runs" / f"work-capture-sheet-{marker.replace(':', '').replace('-', '').replace('Z', '')}"
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "capture_prompts.json"
    html_path = output_dir / "capture_prompts.html"
    payload = {
        "created_at": marker,
        "target_metric": "useful>=0.75 usable>=0.80 p95<=2.5s hang<=0.02 on representative English/Hindi+English work speech",
        "prompts": PROMPTS,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(_render_html(marker, json_path), encoding="utf-8")

    print(f"marker={marker}")
    print(f"json={json_path}")
    print(f"html={html_path}")
    print(
        "capture_progress="
        f".venv/bin/python scripts/check_work_capture_progress.py --capture-sheet {json_path} "
        "--limit 120 --min-retained 20 --min-representative 20"
    )
    print(
        "goal_gate="
        f".venv/bin/python scripts/check_hinglish_english_goal.py --capture-sheet {json_path} "
        "--output-dir eval_runs/goal-status-after-capture"
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _render_html(marker: str, json_path: Path) -> str:
    cards = "\n".join(_render_card(index + 1, prompt) for index, prompt in enumerate(PROMPTS))
    capture_sheet = html.escape(str(json_path))
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>RambleFix Work/Hinglish Capture Sheet</title>
  <style>
    body {{ margin: 28px; max-width: 980px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f8f5; color: #171a1f; }}
    header {{ margin-bottom: 20px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .meta, .hint {{ color: #5d635b; line-height: 1.45; }}
    .commands {{ background: #15171c; color: #f7f7f0; border-radius: 8px; padding: 12px; overflow: auto; font: 13px ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }}
    .card {{ background: rgba(255,255,255,.84); border: 1px solid #dde1d8; border-radius: 8px; padding: 14px; }}
    .mode {{ display: inline-block; font-size: 12px; color: #475244; background: #e8eee3; border-radius: 999px; padding: 3px 8px; margin-bottom: 8px; }}
    .text {{ font-size: 17px; line-height: 1.38; margin: 8px 0 12px; }}
    .terms {{ color: #60665d; font-size: 13px; }}
    code {{ background: #eef0eb; padding: 1px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <header>
    <h1>RambleFix Work/Hinglish Capture Sheet</h1>
    <div class="meta">Marker: <code>{html.escape(marker)}</code>. Target: 20-40 retained clips, real work English / Indian English / light Hinglish.</div>
    <p class="hint">Before capture, use the RambleFix menu to turn on Capture Eval Audio. For each card: click any text box, hold Fn or Control, read the sentence naturally, release. Do not over-enunciate. The point is your normal work speech.</p>
    <pre class="commands">.venv/bin/python scripts/check_work_capture_progress.py --capture-sheet {capture_sheet} --limit 120 --min-retained 20 --min-representative 20
.venv/bin/python scripts/check_hinglish_english_goal.py --capture-sheet {capture_sheet} --output-dir eval_runs/goal-status-after-capture</pre>
  </header>
  <main class="grid">
    {cards}
  </main>
</body>
</html>
"""


def _render_card(index: int, prompt: dict[str, Any]) -> str:
    return f"""
    <section class="card">
      <div class="mode">{index:02d} · {html.escape(prompt["mode"])}</div>
      <div class="text">{html.escape(prompt["text"])}</div>
      <div class="terms">Critical: {html.escape(", ".join(prompt["critical"]))}</div>
    </section>
"""


if __name__ == "__main__":
    main()
