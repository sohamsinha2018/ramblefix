from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import meaning_coverage, meaning_loss, repeated_substring_score, term_coverage_report, word_error_rate
from ramblefix.external_asr import transcribe_local_meaning_server_with_fallback, transcribe_whisper_cpp
from ramblefix.glossary import apply_glossary
from ramblefix.processing import process_transcript


KNOWN_TERMS = {
    "API",
    "ASR",
    "BCom",
    "BPD",
    "ChatGPT",
    "Claude",
    "Codex",
    "Cursor",
    "Google",
    "LLM",
    "MCP",
    "OpenAI",
    "PRD",
    "RambleFix",
    "SDK",
    "STT",
    "UI",
    "UX",
}


@dataclass(frozen=True)
class VariantResult:
    id: str
    audio: str
    category: str
    gold: str
    actual: str
    first_output: str
    auto_text: str
    route: str
    risk: bool
    risk_reasons: list[str]
    merge_rules: list[str]
    first_seconds: float
    final_seconds: float
    auto_seconds: float
    wer: float | None
    meaning_loss: float | None
    meaning_coverage: float | None
    term_coverage: float | None
    term_hits: list[str]
    term_misses: list[str]
    term_terms: list[str]
    error: str | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay current fast path vs local term-risk second-pass flow.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--risk-mode", choices=["broad", "narrow"], default="broad")
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()

    items = json.loads(args.corpus.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        print(f"[{index}/{len(items)}] {item.get('id')}", flush=True)
        current = run_variant(item, use_second_pass=False, timeout_seconds=args.timeout_seconds, risk_mode=args.risk_mode)
        proposed = run_variant(item, use_second_pass=True, timeout_seconds=args.timeout_seconds, risk_mode=args.risk_mode)
        rows.append({"id": item.get("id"), "current": asdict(current), "proposed": asdict(proposed)})
        delta_terms = (proposed.term_coverage or 0) - (current.term_coverage or 0)
        delta_meaning = (proposed.meaning_coverage or 0) - (current.meaning_coverage or 0)
        delta_latency = proposed.final_seconds - current.final_seconds
        print(
            f"  current term={current.term_coverage} meaning={current.meaning_coverage} sec={current.final_seconds} | "
            f"proposed term={proposed.term_coverage} meaning={proposed.meaning_coverage} first={proposed.first_seconds} "
            f"final={proposed.final_seconds} risk={proposed.risk} "
            f"dterm={delta_terms:.3f} dmeaning={delta_meaning:.3f} dsec={delta_latency:.3f}",
            flush=True,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    print_summary(rows)


def run_variant(item: dict[str, Any], *, use_second_pass: bool, timeout_seconds: float, risk_mode: str) -> VariantResult:
    row_id = str(item.get("id") or "")
    audio = audio_path(item)
    gold = str(item.get("gold") or "")
    category = str(item.get("category") or "")
    terms = item.get("critical") or item.get("critical_terms") or item.get("terms") or item.get("anchors")
    try:
        started = time.perf_counter()
        fast = transcribe_local_meaning_server_with_fallback(audio, timeout_seconds=timeout_seconds, skip_process_fallback=True)
        fast_raw = fast.text
        first_output = process_transcript(fast_raw, use_ollama=False).clean_transcript
        first_seconds = round(time.perf_counter() - started, 3)

        final_output = first_output
        auto_text = ""
        auto_seconds = 0.0
        merge_rules: list[str] = []
        risk, risk_reasons = term_risk(first_output, mode=risk_mode)
        route = "fast_only"
        if use_second_pass and risk:
            auto_started = time.perf_counter()
            auto = transcribe_whisper_cpp(audio, language="auto", timeout_seconds=timeout_seconds)
            auto_seconds = round(time.perf_counter() - auto_started, 3)
            auto_text = auto.text
            final_output, merge_rules = merge_terms(first_output, auto_text)
            final_output = process_transcript(final_output, use_ollama=False).clean_transcript
            route = "fast_then_auto_terms" if merge_rules else "fast_then_auto_no_change"

        final_seconds = round(time.perf_counter() - started, 3)
        term_report = term_coverage_report(gold, final_output, terms)
        return VariantResult(
            id=row_id,
            audio=str(audio),
            category=category,
            gold=gold,
            actual=final_output,
            first_output=first_output,
            auto_text=auto_text,
            route=route,
            risk=risk if use_second_pass else False,
            risk_reasons=risk_reasons if use_second_pass else [],
            merge_rules=merge_rules,
            first_seconds=first_seconds,
            final_seconds=final_seconds,
            auto_seconds=auto_seconds,
            wer=word_error_rate(gold, final_output) if gold else None,
            meaning_loss=meaning_loss(gold, final_output) if gold else None,
            meaning_coverage=meaning_coverage(gold, final_output) if gold else None,
            term_coverage=term_report["coverage"],
            term_hits=list(term_report["hits"]),
            term_misses=list(term_report["misses"]),
            term_terms=list(term_report["terms"]),
        )
    except Exception as exc:  # noqa: BLE001
        term_report = term_coverage_report(gold, "", terms)
        return VariantResult(
            id=row_id,
            audio=str(audio),
            category=category,
            gold=gold,
            actual="",
            first_output="",
            auto_text="",
            route="error",
            risk=False,
            risk_reasons=[],
            merge_rules=[],
            first_seconds=0.0,
            final_seconds=0.0,
            auto_seconds=0.0,
            wer=None,
            meaning_loss=1.0 if gold else None,
            meaning_coverage=0.0 if gold else None,
            term_coverage=term_report["coverage"],
            term_hits=[],
            term_misses=list(term_report["terms"]),
            term_terms=list(term_report["terms"]),
            error=f"{type(exc).__name__}: {exc}",
        )


def term_risk(text: str, *, mode: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if spelled_letter_sequences(text):
        reasons.append("spelled_letter_sequence")
    if re.search(r"\bRamble\s+Fix\b", text, re.IGNORECASE):
        reasons.append("split_known_product_term")
    if mode == "broad":
        if extract_known_terms(text):
            reasons.append("known_term_present")
        if re.search(r"\b[A-Z]{2,8}\b", text):
            reasons.append("acronym_present")
        if re.search(r"\b[A-Z][a-z]+[A-Z][A-Za-z0-9]*\b", text):
            reasons.append("camel_case_present")
        if re.search(r"\b(?:Google|Codex|Cursor|Claude|OpenAI|Ramble\s+Fix|RambleFix)\b", text, re.IGNORECASE):
            reasons.append("proper_or_product_noun_present")
    return bool(reasons), sorted(set(reasons))


def merge_terms(fast_text: str, auto_text: str) -> tuple[str, list[str]]:
    merged = fast_text
    rules: list[str] = []
    auto_terms = extract_auto_terms(auto_text)
    ordered_auto_terms = extract_auto_terms_ordered(auto_text)

    for match, letters in reversed(spelled_letter_sequences(merged)):
        replacement = best_spelled_sequence_replacement(letters, ordered_auto_terms)
        if replacement:
            merged = merged[: match.start()] + replacement + merged[match.end() :]
            rules.append(f"spelled:{''.join(letters)}->{replacement}")

    before = merged
    merged = apply_glossary(merged)
    if merged != before:
        rules.append("apply_glossary")

    for term in sorted(auto_terms, key=len, reverse=True):
        merged, changed = repair_split_known_term(merged, term)
        if changed:
            rules.append(f"split_known_term->{term}")

    return normalize_spaces(merged), rules


def spelled_letter_sequences(text: str) -> list[tuple[re.Match[str], list[str]]]:
    pattern = re.compile(r"(?<![A-Za-z])(?:[A-Z](?:\s*,\s*|\s+)){1,7}[A-Z](?![A-Za-z])")
    matches: list[tuple[re.Match[str], list[str]]] = []
    for match in pattern.finditer(text):
        letters = re.findall(r"[A-Z]", match.group(0))
        if 2 <= len(letters) <= 8:
            matches.append((match, letters))
    return matches


def extract_auto_terms(text: str) -> set[str]:
    return set(extract_auto_terms_ordered(text))


def extract_auto_terms_ordered(text: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"\b[A-Z][A-Z0-9]{1,8}\b|\b[A-Z][a-z]+[A-Z][A-Za-z0-9]*\b", text):
        if len(token) >= 2 and token.upper() not in {"OK"}:
            terms.append(token)
    found = extract_known_terms(text)
    for term in sorted(found, key=str.lower):
        terms.append(term)
    return dedupe(terms)


def extract_known_terms(text: str) -> set[str]:
    found: set[str] = set()
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    for term in KNOWN_TERMS:
        term_key = re.sub(r"[^a-z0-9]+", " ", term.lower()).strip()
        if re.search(rf"\b{re.escape(term_key)}\b", normalized):
            found.add(term)
    return found


def best_spelled_sequence_replacement(letters: list[str], ordered_auto_terms: list[str]) -> str | None:
    spelled = "".join(letters).upper()
    candidates = [term for term in ordered_auto_terms if term.isupper() and 2 <= len(term) <= 8]
    if not candidates:
        return None
    exact = best_acronym_replacement(letters, set(candidates))
    if exact:
        return exact

    # Adjacent spoken acronyms often arrive as one span: "A, S, R, M, C, B".
    # If auto heard "ASR, MCP", split the letter stream into those local terms.
    result: list[str] = []
    index = 0
    used: set[int] = set()
    while index < len(spelled):
        best: tuple[int, str] | None = None
        for candidate_index, term in enumerate(candidates):
            if candidate_index in used:
                continue
            size = len(term)
            segment = spelled[index : index + size]
            if len(segment) != size:
                continue
            distance = levenshtein(term.upper(), segment)
            if distance <= 1:
                score = (distance, -size)
                if best is None or score < (levenshtein(best[1].upper(), spelled[index : index + len(best[1])]), -len(best[1])):
                    best = (candidate_index, term)
        if best is None:
            return None
        used.add(best[0])
        result.append(best[1])
        index += len(best[1])
    return ", ".join(result) if len(result) >= 2 else None


def best_acronym_replacement(letters: list[str], auto_terms: set[str]) -> str | None:
    spelled = "".join(letters).upper()
    candidates = [term for term in auto_terms if term.isupper() and 2 <= len(term) <= 8]
    exact = [term for term in candidates if term.upper() == spelled]
    if exact:
        return exact[0]
    near = [
        term
        for term in candidates
        if len(term) == len(spelled) and levenshtein(term.upper(), spelled) <= 1
    ]
    if near:
        return sorted(near, key=lambda term: (levenshtein(term.upper(), spelled), term))[0]
    return None


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def repair_split_known_term(text: str, term: str) -> tuple[str, bool]:
    if term not in KNOWN_TERMS:
        return text, False
    if re.search(rf"\b{re.escape(term)}\b", text):
        return text, False
    if term == "RambleFix":
        updated = re.sub(r"\bRamble\s+Fix\b", "RambleFix", text, flags=re.IGNORECASE)
        return updated, updated != text
    return text, False


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def audio_path(item: dict[str, Any]) -> Path:
    raw = str(item.get("audio") or "")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def print_summary(rows: list[dict[str, Any]]) -> None:
    current = [row["current"] for row in rows]
    proposed = [row["proposed"] for row in rows]
    for label, data in [("current", current), ("proposed", proposed)]:
        ok = [row for row in data if not row["error"]]
        if not ok:
            print(f"{label}: no successful rows")
            continue
        print(
            f"{label}: n={len(ok)} errors={len(data)-len(ok)} "
            f"meaning={avg(ok, 'meaning_coverage'):.3f} term={avg(ok, 'term_coverage'):.3f} "
            f"wer={avg(ok, 'wer'):.3f} p50={median(ok, 'final_seconds'):.3f} "
            f"p95={percentile([row['final_seconds'] for row in ok], 0.95):.3f}"
        )
    triggered = [row["proposed"] for row in rows if row["proposed"]["risk"]]
    print(f"triggered={len(triggered)}/{len(rows)}")
    if triggered:
        print(
            f"triggered_auto_p50={median(triggered, 'auto_seconds'):.3f} "
            f"triggered_final_p50={median(triggered, 'final_seconds'):.3f}"
        )


def avg(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return statistics.mean(values) if values else 0.0


def median(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return statistics.median(values) if values else 0.0


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * q
    f = math.floor(k)
    c = math.ceil(k)
    return values[f] if f == c else values[f] * (c - k) + values[c] * (k - f)


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


if __name__ == "__main__":
    main()
