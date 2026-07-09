from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@dataclass
class Candidate:
    name: str
    text: str
    seconds: float
    error: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an offline draft-gold English corpus from retained RambleFix recordings.")
    parser.add_argument("--history", type=Path, default=ROOT / "logs/history.jsonl")
    parser.add_argument("--output-corpus", type=Path, default=ROOT / "eval_corpus/english_real_use_offline_gold_draft_20260628.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "eval_runs/offline-english-gold-20260628")
    parser.add_argument("--max-clips", type=int, default=20)
    parser.add_argument(
        "--models",
        default="whisper_cpp_server_translate,whisper_cpp_translate_small,whisper_cpp_auto_small,mlx_large_v3_turbo_q4_transcribe",
    )
    parser.add_argument("--allow-downloads", action="store_true", help="Allow HF/model downloads. Default is offline/cache-only.")
    args = parser.parse_args()

    if not args.allow_downloads:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    rows = load_history(args.history)
    items = select_english_items(rows, max_clips=args.max_clips)
    models = [part.strip() for part in args.models.split(",") if part.strip()]
    runners = build_runners(models)

    out_rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        audio = Path(item["audio"])
        print(f"[{index}/{len(items)}] {item['id']} {audio.name} {item['duration_seconds']:.1f}s", flush=True)
        candidates = [
            Candidate("app_raw_log", item["app_raw"], 0.0),
            Candidate("app_corrected_log", item["app_corrected"], 0.0),
        ]
        for name, runner in runners:
            started = time.perf_counter()
            try:
                text = runner(audio)
                candidates.append(Candidate(name, normalize_spaces(text), round(time.perf_counter() - started, 3)))
                print(f"  {name}: {candidates[-1].seconds:.2f}s {short(candidates[-1].text)}", flush=True)
            except Exception as exc:  # noqa: BLE001
                candidates.append(Candidate(name, "", round(time.perf_counter() - started, 3), f"{type(exc).__name__}: {exc}"))
                print(f"  {name}: ERR {candidates[-1].error[:180]}", flush=True)

        gold, gold_source = choose_draft_gold(candidates)
        app_text = item["app_corrected"] or item["app_raw"]
        agreement = similarity(gold, app_text)
        out_rows.append(
            {
                "id": item["id"],
                "category": "real_use_english_dictation",
                "audio": item["audio"],
                "gold": gold,
                "gold_status": "offline_draft",
                "gold_source": gold_source,
                "needs_human_review": agreement < 0.90 or has_suspicious_terms(gold),
                "app_text": app_text,
                "app_raw": item["app_raw"],
                "created_at": item["created_at"],
                "history_line": item["history_line"],
                "duration_seconds": item["duration_seconds"],
                "agreement_with_app": round(agreement, 3),
                "critical_terms": extract_terms(gold),
                "candidates": [asdict(candidate) for candidate in candidates],
            }
        )

    args.output_corpus.parent.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.output_corpus.write_text(
        json.dumps(
            [
                {
                    "id": row["id"],
                    "category": row["category"],
                    "audio": row["audio"],
                    "gold": row["gold"],
                    "critical": row["critical_terms"],
                    "meta": {
                        "gold_status": row["gold_status"],
                        "gold_source": row["gold_source"],
                        "needs_human_review": row["needs_human_review"],
                        "app_text": row["app_text"],
                        "created_at": row["created_at"],
                        "history_line": row["history_line"],
                        "duration_seconds": row["duration_seconds"],
                        "agreement_with_app": row["agreement_with_app"],
                    },
                }
                for row in out_rows
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    candidates_path = args.output_dir / "candidates.json"
    candidates_path.write_text(json.dumps(out_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path = args.output_dir / "review.html"
    html_path.write_text(render_html(out_rows), encoding="utf-8")
    print(f"wrote {args.output_corpus}")
    print(f"wrote {candidates_path}")
    print(f"wrote {html_path}")


def load_history(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        row["_line"] = index
        rows.append(row)
    return rows


def select_english_items(rows: list[dict[str, Any]], *, max_clips: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_audio: set[str] = set()
    for row in rows:
        audio_raw = row.get("audio_path") or ""
        if not audio_raw:
            continue
        audio = Path(str(audio_raw))
        if not audio.exists() or str(audio) in seen_audio:
            continue
        text = normalize_spaces(str(row.get("raw_text") or row.get("pasted_text") or row.get("corrected_text") or ""))
        corrected = normalize_spaces(str(row.get("corrected_text") or row.get("pasted_text") or row.get("raw_text") or ""))
        duration = audio_duration(row)
        if row.get("status") != "paste_attempted" or row.get("paste_success") is not True:
            continue
        if row.get("error_type"):
            continue
        if duration < 2.0 or len(text) < 20:
            continue
        if has_indic_script(text):
            continue
        selected.append(
            {
                "id": f"rf_english_{row['_line']}",
                "audio": str(audio),
                "created_at": row.get("created_at") or "",
                "history_line": row["_line"],
                "duration_seconds": round(duration, 3),
                "app_raw": text,
                "app_corrected": corrected,
            }
        )
        seen_audio.add(str(audio))
    return selected[-max_clips:]


def build_runners(models: list[str]) -> list[tuple[str, Callable[[Path], str]]]:
    from ramblefix.external_asr import (
        transcribe_whisper_cpp,
        transcribe_whisper_cpp_server_translate,
        transcribe_whisper_cpp_translate,
    )

    runners: list[tuple[str, Callable[[Path], str]]] = []
    for model in models:
        if model == "whisper_cpp_server_translate":
            runners.append((model, lambda audio: transcribe_whisper_cpp_server_translate(audio).text))
        elif model == "whisper_cpp_translate_small":
            runners.append((model, lambda audio: transcribe_whisper_cpp_translate(audio).text))
        elif model == "whisper_cpp_auto_small":
            runners.append((model, lambda audio: transcribe_whisper_cpp(audio, language="auto").text))
        elif model == "mlx_large_v3_turbo_8bit_transcribe":
            runners.append((model, lambda audio: transcribe_mlx(audio, repo="mlx-community/whisper-large-v3-turbo-8bit", task="transcribe")))
        elif model == "mlx_large_v3_turbo_q4_transcribe":
            runners.append((model, lambda audio: transcribe_mlx(audio, repo="mlx-community/whisper-large-v3-turbo-q4", task="transcribe")))
        else:
            raise ValueError(f"unknown model: {model}")
    return runners


def transcribe_mlx(audio: Path, *, repo: str, task: str) -> str:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=repo,
        language="en",
        task=task,
        temperature=0.0,
        verbose=False,
        condition_on_previous_text=False,
        hallucination_silence_threshold=2.0,
        compression_ratio_threshold=2.2,
        no_speech_threshold=0.6,
    )
    return str(result.get("text") or "").strip()


def choose_draft_gold(candidates: list[Candidate]) -> tuple[str, str]:
    # Draft-gold selection is consensus-first. Auto-language Whisper is useful,
    # but on Indian English it sometimes flips into German/Devanagari; do not let
    # that override the English translate path unless other candidates agree.
    weights = {
        "mlx_large_v3_turbo_8bit_transcribe": 1.4,
        "mlx_large_v3_turbo_q4_transcribe": 1.4,
        "whisper_cpp_server_translate": 1.3,
        "whisper_cpp_translate_small": 1.1,
        "app_corrected_log": 0.8,
        "app_raw_log": 0.7,
        "whisper_cpp_auto_small": 0.6,
    }
    priority = [
        "mlx_large_v3_turbo_8bit_transcribe",
        "mlx_large_v3_turbo_q4_transcribe",
        "whisper_cpp_server_translate",
        "whisper_cpp_translate_small",
        "app_corrected_log",
        "app_raw_log",
        "whisper_cpp_auto_small",
    ]
    usable = [
        candidate
        for candidate in candidates
        if candidate.text
        and not candidate.error
        and not is_degenerate(candidate.text)
        and not looks_like_wrong_language(candidate.text)
    ]
    if not usable:
        for candidate in candidates:
            if candidate.text and not candidate.error:
                return candidate.text, candidate.name
        return "", "none"

    def agreement_threshold(text: str) -> float:
        word_count = len(re.findall(r"[A-Za-z0-9']+", text))
        return 0.94 if word_count <= 18 else 0.82

    def support_for(candidate: Candidate) -> float:
        threshold = agreement_threshold(candidate.text)
        return sum(
            weights.get(other.name, 0.5)
            for other in usable
            if similarity(candidate.text, other.text) >= threshold
        )

    def score(candidate: Candidate) -> tuple[float, int, float]:
        support = support_for(candidate)
        if candidate.name.startswith("mlx_") and support <= weights.get(candidate.name, 0.5):
            support -= 1.0
        priority_score = -priority.index(candidate.name) if candidate.name in priority else -99
        return support, priority_score, weights.get(candidate.name, 0.5)

    winner = max(usable, key=score)
    return winner.text, winner.name


def is_degenerate(text: str) -> bool:
    words = re.findall(r"[A-Za-z0-9']+", text.lower())
    if len(words) < 2:
        return True
    if len(set(words)) <= 2 and len(words) >= 8:
        return True
    return False


def looks_like_wrong_language(text: str) -> bool:
    if has_indic_script(text):
        return True
    lower_words = set(re.findall(r"[a-z']+", text.lower()))
    foreign_markers = {
        "sieht",
        "gut",
        "aus",
        "nicht",
        "danke",
        "bonjour",
        "gracias",
    }
    return bool(lower_words & foreign_markers)


def has_suspicious_terms(text: str) -> bool:
    lower = text.lower()
    suspicious = ["deepless", "coil", "rookie r google", "test-cell"]
    return any(term in lower for term in suspicious)


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


def audio_duration(row: dict[str, Any]) -> float:
    timings = row.get("timings") if isinstance(row.get("timings"), dict) else {}
    quality = row.get("quality_flags") if isinstance(row.get("quality_flags"), dict) else {}
    value = timings.get("audio_duration_seconds", quality.get("audio_duration_seconds", 0))
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def has_indic_script(text: str) -> bool:
    return any("\u0900" <= char <= "\u097F" for char in text)


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_for_compare(a), normalize_for_compare(b)).ratio()


def normalize_for_compare(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def short(text: str, width: int = 100) -> str:
    clean = normalize_spaces(text)
    return clean if len(clean) <= width else clean[: width - 3] + "..."


def render_html(rows: list[dict[str, Any]]) -> str:
    body = []
    for row in rows:
        candidate_blocks = []
        for candidate in row["candidates"]:
            label = html.escape(candidate["name"])
            text = html.escape(candidate["error"] or candidate["text"])
            seconds = candidate["seconds"]
            cls = "error" if candidate["error"] else ""
            candidate_blocks.append(f"<details><summary>{label} <span>{seconds}s</span></summary><p class='{cls}'>{text}</p></details>")
        body.append(
            f"""
            <section class="clip">
              <h2>{html.escape(row['id'])} <span>{row['duration_seconds']}s</span></h2>
              <audio controls src="file://{html.escape(row['audio'])}"></audio>
              <div class="grid">
                <div><h3>Draft gold ({html.escape(row['gold_source'])})</h3><p>{html.escape(row['gold'])}</p></div>
                <div><h3>App output</h3><p>{html.escape(row['app_text'])}</p></div>
              </div>
              <p class="meta">review={row['needs_human_review']} agreement={row['agreement_with_app']} terms={html.escape(', '.join(row['critical_terms']))}</p>
              {''.join(candidate_blocks)}
            </section>
            """
        )
    return (
        "<!doctype html><meta charset='utf-8'><title>RambleFix English Offline Gold Draft</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:28px;background:#f7f8fb;color:#17191f}"
        ".clip{background:white;border:1px solid #e0e3ea;border-radius:8px;padding:16px;margin:0 0 16px;box-shadow:0 1px 3px #0001}"
        "h1{font-size:22px}h2{font-size:16px;margin:0 0 10px}h2 span,.meta,summary span{color:#687083;font-weight:400}"
        "audio{width:100%;margin:6px 0 12px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}"
        ".grid>div{background:#f7f8fb;border-radius:6px;padding:10px}h3{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#687083;margin:0 0 6px}"
        "p{line-height:1.45}details{border-top:1px solid #eef0f4;padding:8px 0}summary{cursor:pointer}.error{color:#b42318}</style>"
        "<h1>RambleFix English Offline Gold Draft</h1>"
        "<p>Offline model draft only. Use this to review and promote clips to human-confirmed gold.</p>"
        + "\n".join(body)
    )


if __name__ == "__main__":
    main()
