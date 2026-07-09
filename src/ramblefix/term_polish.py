from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from ramblefix.external_asr import transcribe_whisper_cpp
from ramblefix.glossary import apply_glossary, known_glossary_terms


@dataclass(frozen=True)
class TermPolishResult:
    text: str
    raw_auto_text: str = ""
    engine: str = "term-polish"
    route: str = "term_polish_skipped"
    seconds: float = 0.0
    auto_seconds: float = 0.0
    risk: bool = False
    risk_reasons: list[str] = field(default_factory=list)
    merge_rules: list[str] = field(default_factory=list)
    changed: bool = False
    error: str = ""


def polish_terms_with_auto(
    audio_path: str | Path,
    *,
    draft_text: str,
    timeout_seconds: float = 45.0,
) -> TermPolishResult:
    started = time.perf_counter()
    draft = normalize_spaces(draft_text)
    risk, risk_reasons = term_risk(draft)
    if not risk:
        return TermPolishResult(
            text=draft,
            seconds=round(time.perf_counter() - started, 3),
            risk=False,
            risk_reasons=[],
        )

    glossary_text = apply_glossary(draft)
    if glossary_text != draft and is_safe_small_term_repair(draft, glossary_text):
        return TermPolishResult(
            text=glossary_text,
            engine="term-polish:glossary",
            route="term_polish_changed",
            seconds=round(time.perf_counter() - started, 3),
            risk=True,
            risk_reasons=risk_reasons,
            merge_rules=["apply_glossary"],
            changed=True,
        )

    try:
        auto = transcribe_whisper_cpp(audio_path, language="auto", timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001
        return TermPolishResult(
            text=draft,
            route="term_polish_error",
            seconds=round(time.perf_counter() - started, 3),
            risk=True,
            risk_reasons=risk_reasons,
            error=f"{type(exc).__name__}: {exc}",
        )

    merged, merge_rules = merge_terms(draft, auto.text)
    merged = apply_glossary(normalize_spaces(merged))
    changed = bool(merge_rules) and merged != draft and is_safe_small_term_repair(draft, merged)
    return TermPolishResult(
        text=merged if changed else draft,
        raw_auto_text=auto.text,
        engine=f"term-polish:{auto.engine}",
        route="term_polish_changed" if changed else "term_polish_no_change",
        seconds=round(time.perf_counter() - started, 3),
        auto_seconds=auto.seconds,
        risk=True,
        risk_reasons=risk_reasons,
        merge_rules=merge_rules if changed else [],
        changed=changed,
    )


def term_risk(text: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if spelled_letter_sequences(text):
        reasons.append("spelled_letter_sequence")
    if has_split_known_product_term(text):
        reasons.append("split_known_product_term")
    return bool(reasons), reasons


def merge_terms(fast_text: str, auto_text: str) -> tuple[str, list[str]]:
    merged = fast_text
    rules: list[str] = []
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

    for term in sorted(set(ordered_auto_terms), key=len, reverse=True):
        merged, changed = repair_split_known_term(merged, term)
        if changed:
            rules.append(f"split_known_term->{term}")

    return normalize_spaces(merged), rules


def spelled_letter_sequences(text: str) -> list[tuple[re.Match[str], list[str]]]:
    pattern = re.compile(r"(?i)(?<![A-Za-z])(?:[A-Z](?:\s*,\s*|\s+)){1,7}[A-Z](?![A-Za-z])")
    matches: list[tuple[re.Match[str], list[str]]] = []
    for match in pattern.finditer(text):
        letters = re.findall(r"[A-Z]", match.group(0).upper())
        if 2 <= len(letters) <= 8:
            matches.append((match, letters))
    return matches


def extract_auto_terms_ordered(text: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"\b[A-Z][A-Z0-9]{1,8}\b|\b[A-Z][a-z]+[A-Z][A-Za-z0-9]*\b", text):
        if token.upper() not in {"OK"}:
            terms.append(token)
    normalized = normalize_key(text)
    for canonical in sorted(set(known_glossary_terms().values()), key=str.lower):
        key = normalize_key(canonical)
        if key and re.search(rf"\b{re.escape(key)}\b", normalized):
            terms.append(canonical)
    return dedupe(terms)


def best_spelled_sequence_replacement(letters: list[str], ordered_auto_terms: list[str]) -> str | None:
    spelled = "".join(letters).upper()
    candidates = [term for term in ordered_auto_terms if term.isupper() and 2 <= len(term) <= 8]
    exact = [term for term in candidates if term.upper() == spelled]
    if exact:
        return exact[0]

    result: list[str] = []
    index = 0
    used: set[int] = set()
    while index < len(spelled):
        best: tuple[int, str, int] | None = None
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
                if best is None or score < (best[2], -len(best[1])):
                    best = (candidate_index, term, distance)
        if best is None:
            return None
        used.add(best[0])
        result.append(best[1])
        index += len(best[1])
    return ", ".join(result) if len(result) >= 2 else None


def repair_split_known_term(text: str, term: str) -> tuple[str, bool]:
    if term != "RambleFix":
        return text, False
    updated = re.sub(r"\bRamble\s+Fix\b", "RambleFix", text, flags=re.IGNORECASE)
    return updated, updated != text


def has_split_known_product_term(text: str) -> bool:
    return re.search(r"\b(?:Ramble|Rumble)\s+Fix\b", text, flags=re.IGNORECASE) is not None


def is_safe_small_term_repair(draft: str, candidate: str) -> bool:
    if not candidate.strip() or candidate == draft:
        return False
    draft_words = word_count(draft)
    candidate_words = word_count(candidate)
    if draft_words >= 8 and candidate_words < max(4, draft_words // 2):
        return False
    if candidate_words > max(6, int(max(draft_words, 1) * 1.45)):
        return False
    reduced_spelled_letters = len(spelled_letter_sequences(candidate)) < len(spelled_letter_sequences(draft))
    char_delta = abs(len(candidate) - len(draft))
    if char_delta > max(32, len(draft) // 3) and not reduced_spelled_letters:
        return False
    similarity = SequenceMatcher(None, normalize_key(draft), normalize_key(candidate)).ratio()
    return similarity >= 0.72 or reduced_spelled_letters


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", text))


def normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ca in a:
        cur = [prev[0] + 1]
        for j, cb in enumerate(b, 1):
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
