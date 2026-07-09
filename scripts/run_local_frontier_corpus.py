from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import (  # noqa: E402
    _corpus_terms,
    meaning_coverage,
    meaning_loss,
    repeated_substring_score,
    term_coverage_report,
    word_error_rate,
)


DEFAULT_MODELS = (
    "whisper_cpp_server_translate,"
    "whisper_cpp_auto_small,"
    "mlx_whisper_large_v3_turbo_q4_transcribe,"
    "oriserve_hindi2hinglish_ggml,"
    "srota_qwen3_hinglish_mlx,"
    "oriserve_hindi2hinglish_transformers,"
    "shunya_zero_stt_hinglish,"
    "parakeet_mlx,"
    "nemotron35_nemo"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local frontier ASR candidates on a small scored corpus.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--models", default=DEFAULT_MODELS)
    parser.add_argument("--ids", default="", help="Comma-separated corpus IDs. Empty means all rows.")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--mode", choices=["meaning", "verbatim"], default="meaning")
    args = parser.parse_args()

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    id_filter = {value.strip() for value in args.ids.split(",") if value.strip()}
    rows = [row for row in corpus if not id_filter or str(row.get("id")) in id_filter]
    if not rows:
        raise SystemExit("no corpus rows selected")

    models = [value.strip() for value in args.models.split(",") if value.strip()]
    bakeoff = _load_bakeoff_module()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    scored_rows: list[dict[str, Any]] = []
    for model in models:
        for item in rows:
            row = _run_one(bakeoff, model, item, args.timeout_seconds)
            scored_rows.append(row)
            if row.get("error"):
                print(f"{model} {row['id']}: ERROR {row['seconds']:.3f}s {row['error'][:180]}", flush=True)
            else:
                print(
                    f"{model} {row['id']}: {row['seconds']:.3f}s "
                    f"meaning={row['meaning_coverage']:.3f} text={_short(row['actual'])}",
                    flush=True,
                )
            _write_outputs(args.output_dir, scored_rows, args.mode)


def _load_bakeoff_module() -> Any:
    path = ROOT / "scripts/run_local_same_wav_bakeoff.py"
    spec = importlib.util.spec_from_file_location("run_local_same_wav_bakeoff", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_local_same_wav_bakeoff"] = module
    spec.loader.exec_module(module)
    return module


def _run_one(bakeoff: Any, model: str, item: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    audio = Path(str(item["audio"])).expanduser().resolve()
    gold = str(item.get("gold") or item.get("text") or item.get("reference") or "").strip()
    terms = _corpus_terms(item)
    expected_terms = list(term_coverage_report(gold, "", terms)["terms"])
    raw = bakeoff._run_model_with_timeout(
        model=model,
        audio=audio,
        reference=gold,
        terms=expected_terms,
        prefixes=[],
        timeout_seconds=timeout_seconds,
    )
    actual = str(raw.get("text") or "").strip()
    term_report = term_coverage_report(gold, actual, expected_terms)
    seconds = float(raw.get("wall_seconds") or timeout_seconds)
    error = str(raw.get("error") or "").strip()
    return {
        "id": str(item["id"]),
        "category": str(item.get("category") or "frontier"),
        "backend": model,
        "audio": str(audio),
        "gold": gold,
        "actual": actual,
        "wer": word_error_rate(gold, actual) if gold else None,
        "meaning_loss": meaning_loss(gold, actual) if gold else None,
        "meaning_coverage": meaning_coverage(gold, actual) if gold else None,
        "term_coverage": term_report["coverage"],
        "term_hits": term_report["hits"],
        "term_misses": term_report["misses"],
        "term_terms": term_report["terms"],
        "repeat": repeated_substring_score(actual),
        "seconds": round(seconds, 3),
        "meta": {
            "engine": raw.get("engine"),
            "language": raw.get("language"),
            "language_probability": raw.get("language_probability"),
        },
        "error": error or None,
    }


def _write_outputs(output_dir: Path, rows: list[dict[str, Any]], mode: str) -> None:
    (output_dir / "corpus_results.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    product_scorecard = _load_product_scorecard()
    scored = [product_scorecard.score_row(row, mode=mode) for row in rows]
    payload = {"mode": mode, "summary": product_scorecard.summarize(scored), "rows": scored}
    (output_dir / "scorecard.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_dir / "scorecard.md").write_text(product_scorecard.markdown(payload), encoding="utf-8")
    (output_dir / "summary.json").write_text(json.dumps(_compact_summary(payload), indent=2) + "\n", encoding="utf-8")


def _load_product_scorecard() -> Any:
    path = ROOT / "scripts/product_scorecard.py"
    spec = importlib.util.spec_from_file_location("product_scorecard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["product_scorecard"] = module
    spec.loader.exec_module(module)
    return module


def _compact_summary(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload["rows"]
    return {
        "summary": payload["summary"],
        "top_hits": _examples(rows, reverse=True),
        "worst_misses": _examples(rows, reverse=False),
    }


def _examples(rows: list[dict[str, Any]], *, reverse: bool) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: float(row.get("useful_dictation_score") or 0.0), reverse=reverse)
    picked = ordered[:5]
    return [
        {
            "id": row["id"],
            "backend": row["backend"],
            "score": row["useful_dictation_score"],
            "seconds": row["seconds"],
            "gold": row["gold"],
            "actual": row["actual"],
            "error": row.get("error"),
        }
        for row in picked
    ]


def _short(text: str, limit: int = 140) -> str:
    normalized = " ".join(str(text).split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


if __name__ == "__main__":
    main()
