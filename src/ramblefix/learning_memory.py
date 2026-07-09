from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MEMORY_TERMS_PATH = Path("config/memory_terms.json")
MAX_MEMORY_TERMS = 200

_TERM_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "auto",
    "balanced",
    "be",
    "because",
    "by",
    "but",
    "can",
    "cool",
    "do",
    "either",
    "fantastic",
    "for",
    "from",
    "get",
    "go",
    "got",
    "hello",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "just",
    "let",
    "like",
    "me",
    "maybe",
    "more",
    "my",
    "no",
    "not",
    "now",
    "of",
    "okay",
    "out",
    "or",
    "right",
    "say",
    "see",
    "so",
    "that",
    "the",
    "then",
    "thing",
    "things",
    "this",
    "to",
    "what",
    "when",
    "where",
    "with",
    "yeah",
    "yes",
    "you",
    "your",
}

_BLOCKED_TERM_WORDS = {
    "background",
    "bro",
    "detected",
    "failure",
    "noise",
    "retry",
    "sh" + "it",
    "term",
    "terms",
}

_CORRECTION_TRAILING_FILLER = {
    "bro",
    "dude",
    "man",
    "mf",
    "mother" + ("f" + "ucker"),
    "please",
    "pls",
    "yaar",
}

_CORRECTION_BLOCKED_WORDS = {
    "do",
    "does",
    "did",
    "should",
    "would",
    "could",
    "cannot",
    "can't",
    "dont",
    "don't",
    "isnt",
    "isn't",
    "wasnt",
    "wasn't",
}


def add_memory_term(
    term: str,
    *,
    aliases: list[str] | None = None,
    source: str = "manual",
    status: str = "approved",
    path: Path = MEMORY_TERMS_PATH,
) -> dict[str, Any]:
    canonical = _normalize_term(term)
    if not _valid_term(canonical):
        raise ValueError("term is empty, too long, or too generic")
    payload = _load_payload(path)
    terms = _merge_terms(
        payload.get("terms", []),
        {canonical: 1},
        source=source,
        status=status,
        aliases={canonical: aliases or []},
    )
    payload["terms"] = terms
    _write_payload(path, payload)
    return {"term": canonical, "aliases": aliases or [], "path": str(path)}


def learn_terms_from_text(
    text: str,
    *,
    source: str = "text",
    min_count: int = 1,
    path: Path = MEMORY_TERMS_PATH,
) -> dict[str, Any]:
    counts = Counter(extract_terms(text))
    return _learn_terms_from_counts(
        counts,
        source=source,
        min_count=min_count,
        path=path,
        contextual_terms=_contextual_acronyms(text),
    )


def _learn_terms_from_counts(
    counts: Counter[str],
    *,
    source: str,
    min_count: int,
    path: Path,
    contextual_terms: set[str] | None = None,
    blocked_terms: set[str] | None = None,
) -> dict[str, Any]:
    contextual = {_normalize_term(term).lower() for term in contextual_terms or set()}
    blocked = {_normalize_term(term).lower() for term in blocked_terms or set()}
    promoted = {}
    for term, count in counts.items():
        key = _normalize_term(term).lower()
        if key in blocked:
            continue
        if source == "history" and not _safe_passive_history_term(term):
            continue
        if count >= min_count or key in contextual:
            promoted[term] = count
    payload = _load_payload(path)
    before = len(payload.get("terms", []))
    payload["terms"] = _merge_terms(payload.get("terms", []), promoted, source=source, status="auto")
    _write_payload(path, payload)
    return {
        "source": source,
        "candidates": len(counts),
        "learned": len(promoted),
        "before": before,
        "after": len(payload["terms"]),
        "terms": sorted(promoted),
        "path": str(path),
    }


def learn_terms_from_history(
    *,
    history_path: Path = Path("logs/history.jsonl"),
    limit: int = 300,
    min_count: int = 2,
    path: Path = MEMORY_TERMS_PATH,
    learn_corrections: bool = True,
    phrase_path: Path = Path("config/phrase_fixes.json"),
) -> dict[str, Any]:
    rows = _read_recent_history_rows(history_path, limit=limit)
    counts: Counter[str] = Counter()
    contextual_terms: set[str] = set()
    blocked_terms: set[str] = set()
    for row in rows:
        text = _history_text(row)
        if not text:
            continue
        counts.update(set(extract_terms(text)))
        contextual_terms.update(_contextual_acronyms(text))
        for source_term, _ in extract_explicit_correction_pairs(text):
            blocked_terms.add(source_term)
    result = _learn_terms_from_counts(
        counts,
        source="history",
        min_count=min_count,
        path=path,
        contextual_terms=contextual_terms,
        blocked_terms=blocked_terms,
    )
    result["history_rows"] = len(rows)
    if learn_corrections:
        result["corrections"] = learn_explicit_corrections_from_rows(rows, phrase_path=phrase_path)
    return result


def learn_explicit_corrections_from_rows(
    rows: list[dict[str, Any]],
    *,
    phrase_path: Path = Path("config/phrase_fixes.json"),
) -> dict[str, Any]:
    from ramblefix.glossary import add_phrase_fix

    learned: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    previous_text = ""
    for row in rows:
        text = _history_text(row)
        corrections = extract_explicit_correction_pairs(text)
        for source, replacement in corrections:
            if not _safe_global_correction_pair(source, replacement):
                contextual = _contextual_correction_pair(previous_text, source, replacement)
                if contextual is None:
                    continue
                source, replacement = contextual
            key = (source.lower(), replacement.lower())
            if key in seen:
                continue
            seen.add(key)
            result = add_phrase_fix(
                source,
                replacement,
                note="Approved from explicit local correction in transcript history.",
                path=phrase_path,
            )
            learned.append(result)
        if text and not corrections:
            previous_text = text
    return {
        "learned": sum(1 for item in learned if not item.get("updated")),
        "updated": sum(1 for item in learned if item.get("updated")),
        "pairs": learned,
    }


def extract_explicit_correction_pairs(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = _normalize_correction_line(raw_line)
        if not line or len(line) > 90 or line.count(" not ") != 1:
            continue
        match = re.fullmatch(
            r"(?P<right>[A-Za-z0-9.+#-]+(?: [A-Za-z0-9.+#-]+){0,2}) not (?P<wrong>[A-Za-z0-9.+#-]+(?: [A-Za-z0-9.+#-]+){0,3})",
            line,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        right = _trim_correction_right_side(match.group("right"))
        wrong = _trim_correction_wrong_side(match.group("wrong"))
        if _valid_correction_pair(right, wrong):
            pairs.append((wrong, _canonical_correction_replacement(right, source=wrong)))
    return pairs


def extract_terms(text: str) -> list[str]:
    cleaned = re.sub(r"\[(?:BLANK_AUDIO|MUSIC|NOISE|SILENCE|INAUDIBLE)\]", " ", text, flags=re.IGNORECASE)
    terms: set[str] = set()

    for match in re.finditer(r"\b[A-Z]{2,8}\b", cleaned):
        _add_if_valid(terms, match.group(0))

    for match in re.finditer(r"\b[A-Za-z]*[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b", cleaned):
        _add_if_valid(terms, match.group(0))

    # Product/report phrases often contain one acronym plus ordinary words:
    # "Stanford AI report", "OpenAI API docs", "MCP server".
    acronym_phrase = r"\b(?:[A-Z]{2,8}|[A-Z][A-Za-z0-9.+#-]+)(?:\s+(?:[A-Z]{2,8}|[A-Z][A-Za-z0-9.+#-]+|[a-z][a-z0-9.+#-]+)){1,3}\b"
    for match in re.finditer(acronym_phrase, cleaned):
        phrase = match.group(0)
        if re.search(r"\b[A-Z]{2,8}\b", phrase):
            _add_if_valid(terms, _strip_edge_stopwords(phrase))

    title_word = r"(?:[A-Z][a-z0-9]+|[A-Z]{2,8})"
    title_phrase = rf"\b{title_word}(?:\s+{title_word}){{1,5}}\b"
    for match in re.finditer(title_phrase, cleaned):
        _add_if_valid(terms, match.group(0))

    for match in re.finditer(r"\b[A-Z][a-zA-Z0-9.+#-]{2,}\b", cleaned):
        _add_if_valid(terms, match.group(0))

    context_word = r"(?:term|terms|tool|tools|model|models|app|apps|project|projects|report|reports|library|libraries|framework|stack|skill|skills)"
    contextual_term = rf"\b{context_word}\s+(?:called\s+|named\s+|like\s+)?([A-Za-z][A-Za-z0-9.+#-]{{2,}})\b"
    for match in re.finditer(contextual_term, cleaned, flags=re.IGNORECASE):
        _add_if_valid(terms, match.group(1))

    return sorted(terms)


def _merge_terms(
    existing_items: Any,
    counts: dict[str, int],
    *,
    source: str,
    status: str,
    aliases: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    by_key: dict[str, dict[str, Any]] = {}
    for item in existing_items if isinstance(existing_items, list) else []:
        if not isinstance(item, dict):
            continue
        canonical = _normalize_term(str(item.get("canonical") or ""))
        if not canonical:
            continue
        by_key[canonical.lower()] = dict(item)

    for term, count in counts.items():
        canonical = _normalize_term(term)
        if not _valid_term(canonical):
            continue
        key = canonical.lower()
        item = by_key.get(key, {"canonical": canonical, "aliases": [], "count": 0, "status": status})
        item["canonical"] = canonical
        item["count"] = int(item.get("count") or 0) + int(count)
        item["last_seen"] = now
        item["enabled"] = item.get("enabled", True)
        item["status"] = "approved" if item.get("status") == "approved" else status
        item_aliases = {_normalize_term(str(alias)) for alias in item.get("aliases", []) if str(alias).strip()}
        item_aliases.add(canonical.lower())
        for alias in (aliases or {}).get(canonical, []):
            normalized_alias = _normalize_term(str(alias))
            if normalized_alias:
                item_aliases.add(normalized_alias)
        item["aliases"] = sorted(item_aliases)
        source_counts = item.get("source_counts") if isinstance(item.get("source_counts"), dict) else {}
        source_counts[source] = int(source_counts.get(source, 0)) + int(count)
        item["source_counts"] = source_counts
        by_key[key] = item

    return sorted(
        by_key.values(),
        key=lambda item: (item.get("status") == "approved", int(item.get("count") or 0), str(item.get("last_seen") or "")),
        reverse=True,
    )[:MAX_MEMORY_TERMS]


def _read_recent_history_rows(history_path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not history_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in history_path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _history_text(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "").strip().lower()
    learnable_statuses = {
        "paste_attempted",
        "copy_fallback",
        "finalizer_replaced",
        "finalizer_saved",
        "fallback_rescue_pasted",
        "fallback_rescue_replaced",
        "fallback_rescue_saved",
        "friendly_rewrite_replaced",
        "friendly_rewrite_saved",
        "light_polish_replaced",
        "light_polish_saved",
        "process_second_pass_replaced",
        "process_second_pass_saved",
        "term_polish_replaced",
        "term_polish_saved",
        "hindi_polish_replaced",
        "hindi_polish_saved",
        "meeting_transcribed",
    }
    if status and status not in learnable_statuses:
        return ""
    fields = [
        row.get("corrected_text"),
        row.get("pasted_text"),
        row.get("raw_text"),
    ]
    unique_fields: list[str] = []
    seen: set[str] = set()
    for field in fields:
        if not isinstance(field, str):
            continue
        normalized = _normalize_term(field).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_fields.append(field)
    text = "\n".join(unique_fields)
    if "asr failure detected" in text.lower():
        return ""
    return text


def _load_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": "local.memory.v1", "terms": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid memory terms shape: {path}")
    payload.setdefault("version", "local.memory.v1")
    payload.setdefault("terms", [])
    return payload


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _add_if_valid(terms: set[str], value: str) -> None:
    term = _normalize_term(value)
    if _valid_term(term):
        terms.add(term)


def _normalize_term(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n.,:;!?()[]{}\"'")


def _strip_edge_stopwords(value: str) -> str:
    words = _normalize_term(value).split()
    while words and words[0].lower() in _TERM_STOPWORDS:
        words.pop(0)
    while words and words[-1].lower() in _TERM_STOPWORDS:
        words.pop()
    return " ".join(words)


def _normalize_correction_line(value: str) -> str:
    line = _normalize_term(value)
    line = re.sub(r"\bit['’]?s\b", "it is", line, flags=re.IGNORECASE)
    line = re.sub(r"\s+", " ", line)
    line = re.sub(r"[,;:]+", " ", line)
    line = re.sub(r"\s+", " ", line).strip()
    line = re.sub(r"^(?:i said|i mean|it is|that is)\s+", "", line, flags=re.IGNORECASE)
    return line


def _trim_correction_wrong_side(value: str) -> str:
    words = _normalize_term(value).split()
    while words and words[-1].lower() in _CORRECTION_TRAILING_FILLER:
        words.pop()
    return " ".join(words)


def _trim_correction_right_side(value: str) -> str:
    words = _normalize_term(value).split()
    lowered = [word.lower() for word in words]
    for prefix in (["i", "said"], ["i", "mean"], ["it", "is"], ["that", "is"]):
        if lowered[: len(prefix)] == prefix:
            words = words[len(prefix) :]
            break
    while words and words[-1].lower() in _CORRECTION_TRAILING_FILLER:
        words.pop()
    return " ".join(words)


def _valid_correction_pair(right: str, wrong: str) -> bool:
    if not right or not wrong or right.lower() == wrong.lower():
        return False
    right_words = right.split()
    wrong_words = wrong.split()
    if not (1 <= len(right_words) <= 3 and 1 <= len(wrong_words) <= 3):
        return False
    all_words = [word.lower() for word in [*right_words, *wrong_words]]
    if any(word in _CORRECTION_TRAILING_FILLER for word in all_words):
        return False
    if any(word in _CORRECTION_BLOCKED_WORDS for word in all_words):
        return False
    if all(word in _TERM_STOPWORDS for word in right_words):
        return False
    if all(word in _TERM_STOPWORDS for word in wrong_words):
        return False
    if len(right) > 50 or len(wrong) > 50:
        return False
    return True


def _safe_global_correction_pair(source: str, replacement: str) -> bool:
    source_words = source.split()
    replacement_words = replacement.split()
    if len(source_words) == 1 and len(replacement_words) == 1:
        if _looks_domainish(source) or _looks_domainish(replacement):
            return True
        return _edit_distance(source.lower(), replacement.lower()) <= 2
    return True


def _looks_domainish(value: str) -> bool:
    if re.search(r"[0-9.+#-]", value):
        return True
    if re.fullmatch(r"[A-Z]{2,8}", value):
        return True
    if re.search(r"[A-Z].*[A-Z]", value):
        return True
    return False


def _contextual_correction_pair(previous_text: str, source: str, replacement: str) -> tuple[str, str] | None:
    source_words = re.findall(r"[A-Za-z0-9.+#-]+", source)
    if not source_words:
        return None
    words = re.findall(r"[A-Za-z0-9.+#-]+", previous_text)
    lowered = [word.lower() for word in words]
    needle = [word.lower() for word in source_words]
    width = len(needle)
    for index in range(0, len(words) - width + 1):
        if lowered[index : index + width] != needle:
            continue
        start = max(0, index - 2)
        end = min(len(words), index + width + 3)
        context = words[start:end]
        replacement_words = replacement.split()
        replaced = context[: index - start] + replacement_words + context[index - start + width :]
        source_phrase = " ".join(context)
        replacement_phrase = " ".join(replaced)
        if source_phrase.lower() != replacement_phrase.lower():
            return source_phrase, replacement_phrase
    return None


def _edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[j - 1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (0 if left_char == right_char else 1),
                )
            )
        previous = current
    return previous[-1]


def _canonical_correction_replacement(value: str, *, source: str = "") -> str:
    normalized = _normalize_term(value)
    if re.fullmatch(r"[A-Z][a-z]+", normalized):
        from ramblefix.glossary import apply_glossary

        lower = normalized.lower()
        glossary_form = apply_glossary(lower)
        if glossary_form != lower:
            return glossary_form
        if len(_normalize_term(source).split()) > 1:
            return normalized
        return lower
    return normalized


def _valid_term(term: str) -> bool:
    if len(term) < 3 or len(term) > 80:
        return False
    words = re.findall(r"[A-Za-z0-9]+", term)
    if not words:
        return False
    if all(word.lower() in _TERM_STOPWORDS for word in words):
        return False
    if any(word.lower() in _BLOCKED_TERM_WORDS for word in words):
        return False
    if words[0].lower() in _TERM_STOPWORDS and len(words) <= 2:
        return False
    return True


def _high_signal_acronym(term: str) -> bool:
    normalized = _normalize_term(term)
    if not re.fullmatch(r"[A-Z]{3,8}", normalized):
        return False
    lower = normalized.lower()
    return lower not in _TERM_STOPWORDS and lower not in _BLOCKED_TERM_WORDS


def _safe_passive_history_term(term: str) -> bool:
    normalized = _normalize_term(term)
    if _high_signal_acronym(normalized):
        return True
    words = normalized.split()
    if len(words) > 1:
        return any(_productish_word(word) for word in words)
    return _productish_word(normalized)


def _productish_word(word: str) -> bool:
    if _high_signal_acronym(word):
        return True
    if re.fullmatch(r"[A-Z]{2,8}s", word):
        return True
    if re.search(r"[0-9.+#-]", word):
        return True
    letters = [char for char in word if char.isalpha()]
    if not letters:
        return False
    has_lower = any(char.islower() for char in letters)
    has_internal_upper = any(char.isupper() for char in word[1:])
    return has_lower and has_internal_upper


def _contextual_acronyms(text: str) -> set[str]:
    tokens = re.findall(r"[A-Za-z0-9.+#-]+", text)
    contextual: set[str] = set()
    context_words = {
        "acronym",
        "acronyms",
        "keyword",
        "keywords",
        "term",
        "terms",
        "transcript",
        "transcription",
    }
    for index, token in enumerate(tokens):
        if not _high_signal_acronym(token):
            continue
        window = tokens[max(0, index - 12) : min(len(tokens), index + 13)]
        if any(item.lower() in context_words for item in window):
            contextual.add(_normalize_term(token))
    return contextual
