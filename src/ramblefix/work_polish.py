from __future__ import annotations

import re
from dataclasses import dataclass

from ramblefix.glossary import apply_glossary


@dataclass(frozen=True)
class PolishResult:
    text: str
    changed: bool
    rules: list[str]


def polish_meaning_first_work_text(text: str) -> PolishResult:
    """Constrained work-speech repair for second-pass polish candidates.

    This is intentionally narrower than an LLM rewrite. It only applies local,
    evidence-backed repairs for recurring ASR confusions in work dictation.
    """
    polished = apply_glossary(text)
    rules: list[str] = []

    next_text = _repair_product_problem_phrase(polished)
    if next_text != polished:
        polished = next_text
        rules.append("product_problem_phrase")

    next_text = _repair_local_tool_cloud_clause(polished)
    if next_text != polished:
        polished = next_text
        rules.append("local_tool_cloud_clause")

    return PolishResult(text=polished, changed=bool(rules), rules=rules)


def _repair_product_problem_phrase(text: str) -> str:
    product = r"(Cursor\s+ChatGPT|ChatGPT|Cursor|Codex)"

    def replace(match: re.Match[str]) -> str:
        verb = match.group("verb")
        product_name = match.group("product")
        return f"{verb} a {product_name} problem"

    return re.sub(
        rf"\b(?P<verb>solve|debug|fix)\s+for\s+(?:a\s+)?(?P<product>{product})\b",
        replace,
        text,
        flags=re.IGNORECASE,
    )


def _repair_local_tool_cloud_clause(text: str) -> str:
    if not _has_company_cloud_privacy_context(text):
        return text
    if _has_word(text, "tool") and _has_word(text, "local"):
        return text

    patterns = [
        r"\b(?:it|this)\s+should\s+be\s+(?:a\s+)?(?:solution|possible)\s+because\b",
        r"\b(?:it|this)\s+needs\s+to\s+be\s+(?:a\s+)?(?:solution|possible)\s+because\b",
    ]
    repaired = text
    for pattern in patterns:
        repaired = re.sub(pattern, "This tool should be local because", repaired, count=1, flags=re.IGNORECASE)
        if repaired != text:
            return repaired
    return text


def _has_company_cloud_privacy_context(text: str) -> bool:
    normalized = text.lower()
    has_company_data = bool(re.search(r"\b(company|enterprise|work)\s+data\b", normalized))
    has_cloud_block = bool(
        re.search(
            r"\b(?:cannot|can't|can\s+not|should\s+not|must\s+not)\s+(?:go|be\s+sent|send|move)\s+(?:to\s+)?(?:the\s+)?cloud\b",
            normalized,
        )
    )
    return has_company_data and has_cloud_block


def _has_word(text: str, word: str) -> bool:
    return bool(re.search(rf"\b{re.escape(word)}\b", text, flags=re.IGNORECASE))
