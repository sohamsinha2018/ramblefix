from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_parakeet_async_selector import (  # noqa: E402
    choose_rule_based,
    content_retention,
    has_meaningful_hinglish_signal,
    process_second_pass_policy_ok,
    suspicious_tokens,
)
from product_scorecard import markdown, score_row, summarize  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate fast first output plus multiple local async candidates.")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--server-backend", required=True)
    parser.add_argument("--candidate-backends", required=True)
    parser.add_argument("--candidate-max-seconds", type=float, default=4.5)
    args = parser.parse_args()

    rows = json.loads(args.results.read_text(encoding="utf-8"))
    grouped = _group_by_id(rows)
    candidates = [value.strip() for value in args.candidate_backends.split(",") if value.strip()]

    simulated: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for row_id in sorted(grouped):
        bucket = grouped[row_id]
        server = bucket.get(args.server_backend)
        if not server:
            continue
        simulated.append(_candidate_row(server, "selector_server_only", "server", server["actual"], server["seconds"], {}))
        candidate_rows = [bucket[name] for name in candidates if name in bucket]
        for candidate in candidate_rows:
            simulated.append(
                _candidate_row(
                    candidate,
                    f"selector_{_safe_backend_name(candidate['backend'])}_only",
                    str(candidate["backend"]),
                    candidate["actual"],
                    candidate["seconds"],
                    {},
                )
            )

        selected_text, selected_source, meta = choose_multi_candidate(
            server,
            candidate_rows,
            candidate_max_seconds=args.candidate_max_seconds,
        )
        selected_seconds = float(server["seconds"])
        if selected_source != "server":
            selected_seconds = max(selected_seconds, float(meta.get("candidate_seconds") or selected_seconds))
        simulated.append(_candidate_row(server, "selector_multi_safe", selected_source, selected_text, selected_seconds, meta))
        diagnostics.append({"id": row_id, "selected_source": selected_source, "meta": meta})

    scored = [score_row(row, mode="meaning") for row in simulated]
    payload = {"mode": "meaning", "summary": summarize(scored), "rows": scored}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "selector_results.json").write_text(json.dumps(scored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "selector_scorecard.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "selector_scorecard.md").write_text(markdown(payload), encoding="utf-8")
    (args.output_dir / "selector_diagnostics.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(markdown(payload))


def choose_multi_candidate(
    server: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    candidate_max_seconds: float,
) -> tuple[str, str, dict[str, Any]]:
    server_text = str(server.get("actual") or "")
    viable: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for candidate in candidates:
        text = str(candidate.get("actual") or "")
        seconds = float(candidate.get("seconds") or 0.0)
        rule_text, source, rule_meta = choose_rule_based(
            server_text,
            text,
            seconds,
            candidate_max_seconds=candidate_max_seconds,
        )
        backend = str(candidate.get("backend") or "")
        accepted = source == "candidate" and rule_text == text
        if accepted:
            score = _candidate_selection_score(server_text, text, seconds, backend)
            viable.append(
                (
                    score,
                    candidate,
                    {
                        **rule_meta,
                        "candidate_backend": backend,
                        "candidate_seconds": seconds,
                        "selection_score": round(score, 3),
                    },
                )
            )
        else:
            rule_meta = {**rule_meta, "candidate_backend": backend, "candidate_seconds": seconds}
    if not viable:
        return server_text, "server", {"reason": "no_safe_candidate"}
    _, best, meta = max(viable, key=lambda item: item[0])
    return str(best.get("actual") or ""), str(best.get("backend") or "candidate"), {**meta, "reason": "best_safe_candidate"}


def _candidate_selection_score(server_text: str, candidate_text: str, seconds: float, backend: str) -> float:
    tokens = set(_tokens(candidate_text))
    hindi_count = len(tokens & MEANINGFUL_SELECTOR_HINDI)
    retention = content_retention(server_text, candidate_text)
    latency_penalty = max(0.0, seconds - 1.5) * 0.12
    backend_bonus = {
        "accurate_en": 0.20,
        "mlx_whisper_large_v3_turbo_q4_translate": 0.10,
        "parakeet_mlx": -0.10,
    }.get(backend, 0.0)
    return (0.7 * min(hindi_count, 4)) + (0.6 * retention) + backend_bonus - latency_penalty


def _candidate_row(template: dict[str, Any], backend: str, source: str, actual: str, seconds: float, meta: dict[str, Any]) -> dict[str, Any]:
    from eval_parakeet_async_selector import _term_coverage  # local import keeps this eval script small.
    from ramblefix.eval import meaning_coverage, meaning_loss, word_error_rate
    from ramblefix.quality import repeated_substring_score

    return {
        **template,
        "backend": backend,
        "actual": actual,
        "wer": word_error_rate(template["gold"], actual),
        "meaning_loss": meaning_loss(template["gold"], actual),
        "meaning_coverage": meaning_coverage(template["gold"], actual),
        "term_coverage": _term_coverage(template, actual),
        "repeat": repeated_substring_score(actual),
        "seconds": seconds,
        "error": None if actual else "empty_selector_output",
        "meta": {**(template.get("meta") if isinstance(template.get("meta"), dict) else {}), "selected_source": source, **meta},
    }


def _group_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["id"]), {})[str(row["backend"])] = row
    return grouped


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _safe_backend_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()


MEANINGFUL_SELECTOR_HINDI = {
    "aap",
    "aapko",
    "agar",
    "aisa",
    "asa",
    "bhai",
    "hamein",
    "hoga",
    "hooga",
    "karna",
    "kare",
    "kya",
    "sakte",
    "yaar",
}


if __name__ == "__main__":
    main()
