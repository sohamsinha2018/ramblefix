from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from ramblefix.eval import meaning_coverage, meaning_loss, term_coverage_report, word_error_rate  # noqa: E402
from ramblefix.quality import repeated_substring_score  # noqa: E402
from product_scorecard import markdown, score_row, summarize  # noqa: E402


DEFAULT_SERVER_BACKEND = "whisper_cpp_server_translate"
DEFAULT_CANDIDATE_BACKEND = "parakeet_mlx"


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate server-first + Parakeet async selector from saved bakeoff rows.")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ollama-model", default="")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--server-backend", default=DEFAULT_SERVER_BACKEND)
    parser.add_argument("--candidate-backend", default=DEFAULT_CANDIDATE_BACKEND)
    parser.add_argument("--candidate-max-seconds", type=float, default=4.2)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    rows = json.loads(args.results.read_text(encoding="utf-8"))
    grouped = _group_by_id(rows)
    ids = sorted(grouped)
    if args.limit:
        ids = ids[: args.limit]

    simulated: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    for row_id in ids:
        bucket = grouped[row_id]
        server = bucket.get(args.server_backend)
        candidate = bucket.get(args.candidate_backend)
        if not server or not candidate:
            continue

        simulated.append(_candidate_row(server, "selector_server_only", "server", server["actual"], server["seconds"], {}))
        candidate_label = _safe_backend_name(args.candidate_backend)
        simulated.append(
            _candidate_row(
                candidate,
                f"selector_{candidate_label}_only",
                args.candidate_backend,
                candidate["actual"],
                candidate["seconds"],
                {},
            )
        )

        rule_text, rule_source, rule_meta = choose_rule_based(
            server["actual"],
            candidate["actual"],
            candidate["seconds"],
            candidate_max_seconds=args.candidate_max_seconds,
        )
        rule_seconds = candidate["seconds"] if rule_source == "candidate" else server["seconds"]
        simulated.append(_candidate_row(server, "selector_rule_safe", rule_source, rule_text, rule_seconds, rule_meta))

        if args.ollama_model:
            started = time.perf_counter()
            merged_text, merge_meta = merge_with_ollama(
                server["actual"],
                candidate["actual"],
                model=args.ollama_model,
                url=args.ollama_url,
                timeout_seconds=args.ollama_timeout_seconds,
            )
            merge_seconds = round(time.perf_counter() - started, 3)
            source = "ollama_merge"
            final_text = merged_text
            if not final_text:
                final_text = rule_text
                source = f"fallback_{rule_source}"
            # Conservative release-to-polish estimate for current sequential app shape:
            # first server output, then candidate, then merge.
            polished_seconds = round(float(server["seconds"]) + float(candidate["seconds"]) + merge_seconds, 3)
            simulated.append(
                _candidate_row(
                    server,
                    f"selector_ollama_merge_{_safe_backend_name(args.ollama_model)}",
                    source,
                    final_text,
                    polished_seconds,
                    {
                        **merge_meta,
                        "server_seconds": server["seconds"],
                        "candidate_backend": args.candidate_backend,
                        "candidate_seconds": candidate["seconds"],
                        "merge_seconds": merge_seconds,
                    },
                )
            )
            diagnostics.append(
                {
                    "id": row_id,
                    "server": server["actual"],
                    "candidate_backend": args.candidate_backend,
                    "candidate": candidate["actual"],
                    "merged": final_text,
                    "merge_seconds": merge_seconds,
                    "polished_seconds": polished_seconds,
                    "merge_meta": merge_meta,
                }
            )

    scored = [score_row(row, mode="meaning") for row in simulated]
    payload = {"mode": "meaning", "summary": summarize(scored), "rows": scored}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "selector_results.json").write_text(json.dumps(scored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "selector_scorecard.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "selector_scorecard.md").write_text(markdown(payload), encoding="utf-8")
    (args.output_dir / "selector_diagnostics.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(markdown(payload))


def choose_rule_based(
    server_text: str,
    candidate_text: str,
    candidate_seconds: float,
    *,
    candidate_max_seconds: float,
) -> tuple[str, str, dict[str, Any]]:
    junk = suspicious_tokens(candidate_text, known_text=server_text)
    retention = content_retention(server_text, candidate_text)
    hindi_signal = has_meaningful_hinglish_signal(candidate_text)
    policy_ok = process_second_pass_policy_ok(server_text, candidate_text)
    keep_candidate = (
        candidate_seconds <= candidate_max_seconds
        and hindi_signal
        and policy_ok
        and retention >= 0.35
        and not junk
    )
    return (
        (
            candidate_text,
            "candidate",
            {
                "reason": "hindi_signal_safe",
                "junk": junk,
                "retention": round(retention, 3),
                "hindi_signal": hindi_signal,
                "process_second_pass_policy_ok": policy_ok,
            },
        )
        if keep_candidate
        else (
            server_text,
            "server",
            {
                "reason": "reject_candidate",
                "junk": junk,
                "retention": round(retention, 3),
                "hindi_signal": hindi_signal,
                "process_second_pass_policy_ok": policy_ok,
            },
        )
    )


def merge_with_ollama(server_text: str, parakeet_text: str, *, model: str, url: str, timeout_seconds: float) -> tuple[str, dict[str, Any]]:
    prompt = (
        "You are cleaning local ASR for Indian English/Hinglish dictation.\n"
        "Use Candidate A and Candidate B only. Preserve meaning, product terms, acronyms, numbers, and negation.\n"
        "If Candidate B has phonetic garbage, ignore that part and use Candidate A.\n"
        "Prefer clean English when it preserves meaning. Use Roman Hinglish only when it preserves meaning better.\n"
        "Do not add facts. Do not summarize. Return one clean transcript only, no commentary.\n\n"
        f"Candidate A:\n{server_text}\n\n"
        f"Candidate B:\n{parakeet_text}\n\n"
        "Clean transcript:"
    )
    try:
        response = requests.post(
            url.rstrip("/") + "/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": "10m",
                "options": {"temperature": 0},
            },
            timeout=timeout_seconds,
        )
        if response.status_code >= 400:
            return "", {"error": f"ollama_http_{response.status_code}: {response.text[:300]}"}
        text = _clean_model_text(str(response.json().get("response") or ""))
        return text, {"error": "", "model": model}
    except Exception as exc:  # noqa: BLE001 - eval should keep moving.
        return "", {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "model": model}


def _candidate_row(template: dict[str, Any], backend: str, source: str, actual: str, seconds: float, meta: dict[str, Any]) -> dict[str, Any]:
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


def process_second_pass_policy_ok(draft: str, final: str) -> bool:
    draft_text = draft.strip()
    final_text = final.strip()
    if not final_text or final_text == draft_text:
        return False
    if not has_meaningful_hinglish_signal(final_text):
        return False
    if not draft_text:
        return True
    draft_words = len(_tokens(draft_text))
    final_words = len(_tokens(final_text))
    if draft_words >= 6 and final_words < max(3, draft_words // 2):
        return False
    if len(final_text) < max(20, len(draft_text) // 2):
        return False
    return True


def has_meaningful_hinglish_signal(text: str) -> bool:
    if re.search(r"[\u0900-\u097f]", text):
        return True
    tokens = set(_tokens(text))
    return bool(tokens & MEANINGFUL_ROMAN_HINDI)


def suspicious_tokens(text: str, *, known_text: str) -> list[str]:
    known = set(_tokens(known_text)) | COMMON_ENGLISH | ROMAN_HINDI
    bad: list[str] = []
    forced_bad = {
        "kakareapata",
        "vedik",
        "yesap",
        "khanya",
        "sakuj",
        "kuchar",
        "lagala",
        "hiya",
    }
    for token in _tokens(text):
        if len(token) < 5 or token in known:
            continue
        if token in forced_bad:
            bad.append(token)
            continue
        vowels = sum(ch in "aeiou" for ch in token)
        ratio = vowels / max(1, len(token))
        if ratio < 0.20 or ratio > 0.76:
            bad.append(token)
    return bad


def content_retention(left: str, right: str) -> float:
    left_tokens = [token for token in _tokens(left) if len(token) >= 4]
    right_tokens = set(token for token in _tokens(right) if len(token) >= 4)
    if not left_tokens:
        return 1.0
    return sum(1 for token in left_tokens if token in right_tokens) / len(left_tokens)


def _term_coverage(template: dict[str, Any], actual: str) -> float:
    terms = [str(term) for term in template.get("term_terms") or [] if str(term).strip()]
    return float(term_coverage_report(template["gold"], actual, terms)["coverage"])


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\u0900-\u097f]+", " ", text.lower()).strip()


def _clean_model_text(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    text = text.replace("\x08", "")
    text = re.sub(r"^(clean transcript|transcript)\s*:\s*", "", text.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip().strip("\"'")


def _safe_backend_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()


ROMAN_HINDI = {
    "aap", "aapko", "agar", "aisa", "alag", "aur", "baat", "baatein",
    "bhai", "chahiye", "dekh", "dusra", "haan", "hai", "hain", "hamara",
    "har", "hoga", "honi", "hota", "humein", "isme", "ismein", "kaam",
    "kaise", "karna", "karne", "karke", "kuch", "kya", "matlab", "mein",
    "nahi", "nahin", "par", "saath", "sakta", "sakte", "samjhaun",
    "samajho", "theek", "toh", "tu", "uske", "vagairah", "vahi", "wo",
    "woh", "yaar", "ye", "yeh",
}

MEANINGFUL_ROMAN_HINDI = {
    "aap", "aapko", "agar", "aisa", "aur", "bhai", "chahiye", "dekh",
    "haan", "hai", "hain", "hamara", "hoga", "karna", "karne",
    "karke", "kuch", "kya", "matlab", "nahi", "nahin", "saath",
    "sakta", "sakte", "theek", "toh", "yaar", "yeh",
}

COMMON_ENGLISH = {
    "about", "after", "again", "agent", "agents", "also", "answer", "api",
    "because", "before", "being", "brief", "code", "core", "could",
    "different", "does", "done", "english", "factor", "from", "goal",
    "have", "hindi", "into", "legal", "maybe", "need", "only", "problem",
    "profession", "quick", "same", "should", "skeptical", "solve",
    "structure", "task", "that", "then", "there", "thing", "this",
    "through", "tool", "what", "when", "where", "which", "will", "with",
    "work", "would", "wedge", "your",
}


if __name__ == "__main__":
    main()
