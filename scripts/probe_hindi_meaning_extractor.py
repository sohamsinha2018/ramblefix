from __future__ import annotations

import argparse
import difflib
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.hindi_chunk_polish import romanize_devanagari_for_hinglish


DEFAULT_INPUT = ROOT / "eval_runs/fresh-hindi-probe-20260629/retained_41_tailpreferred_default8_20260630/results.json"
DEFAULT_OUTPUT_DIR = ROOT / "eval_runs/fresh-hindi-probe-20260629/hindi_meaning_extractor_probe_20260630"

CONTENT_STOP = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "been",
    "before",
    "being",
    "could",
    "does",
    "doing",
    "done",
    "else",
    "from",
    "have",
    "here",
    "into",
    "just",
    "like",
    "maybe",
    "more",
    "much",
    "need",
    "only",
    "other",
    "right",
    "same",
    "should",
    "some",
    "that",
    "then",
    "there",
    "these",
    "thing",
    "this",
    "those",
    "through",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
    "yeah",
    "yes",
    "your",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe a local LLM as a safe Hindi meaning extractor.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model", default="gemma3:latest")
    parser.add_argument("--all", action="store_true", help="Run on all rows instead of rejected Hindi-value rows.")
    parser.add_argument("--ids", nargs="*", default=None)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    rows = payload["rows"]
    if args.ids:
        selected = {run_id.strip() for run_id in args.ids}
        rows = [row for row in rows if row["run_id"] in selected]
    elif not args.all:
        rows = [
            row
            for row in rows
            if row.get("risk")
            and not row.get("safe_update")
            and ((row.get("quality") or {}).get("hindi_value") or {}).get("has_hindi_value")
        ]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        result = _run_row(row, model=args.model)
        results.append(result)
        print(
            f"{index:02d}/{len(rows)} {result['run_id']} "
            f"decision={result['model_decision']} safe={result['safe']} "
            f"seconds={result['seconds']} reasons={result['safety_reasons']}",
            flush=True,
        )

    summary = _summary(results, model=args.model)
    output = {"summary": summary, "rows": results}
    (args.output_dir / "results.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(args.output_dir / "results.md", summary, results)
    print(json.dumps(summary, indent=2))
    print("wrote", args.output_dir / "results.json")


def _run_row(row: dict[str, Any], *, model: str) -> dict[str, Any]:
    fast = _clean(row.get("fast_text") or "")
    raw = _clean(row.get("raw_text") or "")
    started = time.perf_counter()
    error = ""
    response_text = ""
    parsed: dict[str, Any] = {}
    try:
        response_text = _ollama_generate(model=model, prompt=_prompt(fast=fast, raw=raw))
        parsed = _parse_json_response(response_text)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    seconds = round(time.perf_counter() - started, 3)

    candidate = _clean(str(parsed.get("text") or ""))
    decision = str(parsed.get("decision") or "").lower().strip()
    if decision not in {"replace", "keep"}:
        decision = "replace" if candidate and _normalize(candidate) != _normalize(fast) else "keep"
    if not candidate:
        candidate = fast
        decision = "keep"

    safe, safety = _safety_check(fast=fast, raw=raw, candidate=candidate, seconds=seconds)
    accepted = bool(decision == "replace" and safe)
    return {
        "run_id": row["run_id"],
        "audio": row.get("audio") or "",
        "model": model,
        "seconds": seconds,
        "model_decision": decision,
        "safe": safe,
        "accepted": accepted,
        "safety_reasons": safety["reasons"],
        "retained_draft_content_ratio": safety["retained_draft_content_ratio"],
        "new_content_tokens": safety["new_content_tokens"],
        "unsupported_new_content_tokens": safety["unsupported_new_content_tokens"],
        "draft_protected_terms": safety["draft_protected_terms"],
        "candidate_protected_terms": safety["candidate_protected_terms"],
        "error": error,
        "fast_text": fast,
        "raw_text": raw,
        "candidate_text": candidate,
        "response_text": response_text,
    }


def _prompt(*, fast: str, raw: str) -> str:
    return f"""
You are a local dictation repair engine for Indian English + Hindi/Hinglish speech.

You get two transcripts of the same speech:
FAST = already pasted text. It is usually fluent English but may miss Hindi meaning.
RAW = slower Hindi-aware ASR. It may contain Devanagari, Hinglish, and ASR garbage.

Task:
Return a clean final transcript only if RAW clearly adds missing meaning.
Otherwise keep FAST exactly.

Rules:
- Preserve all meaning already present in FAST.
- Preserve acronyms, product names, technical terms, numbers, and named entities from FAST.
- Add missing meaning only when it is directly supported by RAW.
- Prefer clean English. Roman Hinglish is OK only if it is clearer.
- Do not add facts, examples, names, terms, or claims not present in FAST or RAW.
- Do not explain.

Return strict JSON:
{{"decision":"replace|keep","text":"..."}}

FAST:
{fast}

RAW:
{raw}
""".strip()


def _ollama_generate(*, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": 220,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode("utf-8", errors="replace")) from exc
    return str(body.get("response") or "").strip()


def _parse_json_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return {}
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}


def _safety_check(*, fast: str, raw: str, candidate: str, seconds: float) -> tuple[bool, dict[str, Any]]:
    reasons: list[str] = []
    if seconds > 3.0:
        reasons.append("latency>3s")
    if not candidate.strip():
        reasons.append("empty")
    if _has_runaway_repetition(candidate):
        reasons.append("repetition")

    fast_tokens = _content_tokens(fast)
    candidate_tokens = _content_tokens(candidate)
    raw_tokens = set(_content_tokens(raw))
    raw_roman_tokens = set(_content_tokens(romanize_devanagari_for_hinglish(raw)))
    raw_supported = raw_tokens | raw_roman_tokens

    fast_set = set(fast_tokens)
    candidate_set = set(candidate_tokens)
    retained = len(fast_set & candidate_set) / max(1, len(fast_set))
    if fast_set and retained < 0.78:
        reasons.append(f"draft-content-drop:{retained:.2f}")

    new_tokens = sorted(candidate_set - fast_set)
    unsupported = [
        token
        for token in new_tokens
        if not _supported_by_raw(token, raw_supported)
    ]
    if unsupported:
        reasons.append("unsupported-new-content:" + ",".join(unsupported[:6]))

    draft_terms = _protected_terms(fast)
    candidate_terms = _protected_terms(candidate)
    raw_terms = _protected_terms(raw) | _protected_terms(romanize_devanagari_for_hinglish(raw))
    missing_terms = sorted(draft_terms - candidate_terms)
    if missing_terms:
        reasons.append("protected-term-missing:" + ",".join(missing_terms[:5]))
    introduced_terms = sorted(candidate_terms - draft_terms - raw_terms)
    if introduced_terms:
        reasons.append("protected-term-unsupported:" + ",".join(introduced_terms[:5]))

    fast_words = _word_count(fast)
    candidate_words = _word_count(candidate)
    if fast_words and candidate_words > max(fast_words + 45, int(fast_words * 1.45)):
        reasons.append("too-long")
    if _normalize(candidate) == _normalize(fast):
        reasons.append("no-change")

    return not reasons, {
        "reasons": reasons,
        "retained_draft_content_ratio": round(retained, 3),
        "new_content_tokens": new_tokens,
        "unsupported_new_content_tokens": unsupported,
        "draft_protected_terms": sorted(draft_terms),
        "candidate_protected_terms": sorted(candidate_terms),
    }


def _supported_by_raw(token: str, raw_tokens: set[str]) -> bool:
    if token in raw_tokens:
        return True
    if len(token) >= 5:
        for raw in raw_tokens:
            if token in raw or raw in token:
                return True
            if difflib.SequenceMatcher(None, token, raw).ratio() >= 0.86:
                return True
    return False


def _summary(rows: list[dict[str, Any]], *, model: str) -> dict[str, Any]:
    seconds = [row["seconds"] for row in rows if isinstance(row.get("seconds"), int | float)]
    return {
        "rows": len(rows),
        "model": model,
        "accepted_count": sum(1 for row in rows if row.get("accepted")),
        "safe_count": sum(1 for row in rows if row.get("safe")),
        "model_replace_count": sum(1 for row in rows if row.get("model_decision") == "replace"),
        "error_count": sum(1 for row in rows if row.get("error")),
        "p50_seconds": _median(seconds),
        "p95_seconds": _p95(seconds),
        "max_seconds": round(max(seconds), 3) if seconds else None,
        "local_only": True,
    }


def _write_markdown(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = ["# Hindi Meaning Extractor Probe", ""]
    lines.extend(f"- {key}: `{value}`" for key, value in summary.items())
    lines.extend(["", "| clip | decision | safe | seconds | reasons | fast | candidate | raw |", "| --- | --- | --- | ---: | --- | --- | --- | --- |"])
    for row in rows:
        lines.append(
            f"| {row['run_id']} | {row['model_decision']} | {row['safe']} | {row['seconds']} | "
            f"{', '.join(row['safety_reasons'])} | {_short(row['fast_text'])} | "
            f"{_short(row['candidate_text'])} | {_short(row['raw_text'])} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _content_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if len(token) < 3 or token in CONTENT_STOP:
            continue
        tokens.append(token)
    return tokens


def _protected_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw in re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:'[A-Za-z]+)?\b", text):
        token = raw.split("'", 1)[0]
        if len(token) < 2:
            continue
        if len(token) > 2 and token[-1] == "s" and token[:-1].isupper():
            terms.add(token[:-1].lower())
            continue
        if token.isupper() and any(char.isalpha() for char in token):
            terms.add(token.lower())
            continue
        if len(token) >= 4 and token[0].isupper() and any(char.isupper() for char in token[1:]) and any(char.islower() for char in token):
            terms.add(token.lower())
    return terms


def _has_runaway_repetition(text: str) -> bool:
    tokens = re.findall(r"[A-Za-z0-9]+", text.lower())
    if len(tokens) < 9:
        return False
    for width in range(1, 5):
        for start in range(0, len(tokens) - (width * 3) + 1):
            first = tokens[start : start + width]
            second = tokens[start + width : start + (2 * width)]
            third = tokens[start + (2 * width) : start + (3 * width)]
            if first == second == third:
                return True
    return False


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _short(text: str, limit: int = 180) -> str:
    clean = text.replace("|", "\\|")
    return clean if len(clean) <= limit else clean[: limit - 1] + "..."


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 3)
    ordered = sorted(values)
    index = int(0.95 * (len(ordered) - 1))
    return round(ordered[index], 3)


if __name__ == "__main__":
    main()
