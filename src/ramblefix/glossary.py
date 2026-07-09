from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_DIR = Path("config")
DICTIONARY_PATH = CONFIG_DIR / "dictionary.json"
PHRASE_FIXES_PATH = CONFIG_DIR / "phrase_fixes.json"
MEMORY_TERMS_PATH = CONFIG_DIR / "memory_terms.json"
PROFILE_PATH = CONFIG_DIR / "profile.json"


DEFAULT_TERMS = {
    "api": "API",
    "asr": "ASR",
    "bcom": "BCom",
    "bpd": "BPD",
    "codex": "Codex",
    "cursor": "Cursor",
    "fee admin": "Fee Admin",
    "fiatman": "Fee Admin",
    "mcp": "MCP",
    "partner center": "Partner Center",
    "pci": "PCI",
    "pii": "PII",
    "prd": "PRD",
    "riskified": "Riskified",
    "riscfied": "Riskified",
    "risk ified": "Riskified",
    "sdk": "SDK",
    "sox": "SOX",
    "stt": "STT",
}


@dataclass(frozen=True)
class GlossaryVersion:
    dictionary: str
    phrase_fixes: str
    memory_terms: str
    profile: str
    builtins_used: bool

    def compact(self) -> str:
        builtins = "builtins:on" if self.builtins_used else "builtins:off"
        return (
            f"dictionary:{self.dictionary}|phrase_fixes:{self.phrase_fixes}|"
            f"memory_terms:{self.memory_terms}|profile:{self.profile}|{builtins}"
        )


def apply_glossary(text: str) -> str:
    corrected = text
    for wrong, right in sorted(_load_phrase_fixes().items(), key=lambda item: len(item[0]), reverse=True):
        corrected = re.sub(rf"(?<!\w){re.escape(wrong)}(?!\w)", right, corrected, flags=re.IGNORECASE)

    terms = _load_terms()
    for wrong, right in sorted(terms.items(), key=lambda item: len(item[0]), reverse=True):
        corrected = re.sub(rf"\b{re.escape(wrong)}\b", right, corrected, flags=re.IGNORECASE)

    return _repair_spelled_acronym_sequences(corrected, terms)


def add_phrase_fix(
    source: str,
    replacement: str,
    *,
    note: str,
    path: Path = PHRASE_FIXES_PATH,
) -> dict[str, Any]:
    source = _normalize_phrase(source)
    replacement = _normalize_phrase(replacement)
    note = note.strip()
    if not source or not replacement:
        raise ValueError("source and replacement are required")
    if source.lower() == replacement.lower():
        raise ValueError("source and replacement must differ")
    if len(source) > 120 or len(replacement) > 120:
        raise ValueError("source and replacement must be 120 chars or fewer")
    if not note:
        raise ValueError("approved phrase fixes require a note")

    payload = _read_json_config(path) if path.exists() else {"version": "local.learned", "phrase_fixes": []}
    raw = payload.get("phrase_fixes")
    if isinstance(raw, dict):
        raise RuntimeError("phrase_fixes must be a list of approved objects, not a shorthand dict")
    fixes = _as_list(raw)
    next_items: list[Any] = []
    updated = False
    for item in fixes:
        if not isinstance(item, dict):
            next_items.append(item)
            continue
        if str(item.get("source") or "").strip().lower() == source.lower():
            new_item = dict(item)
            new_item.update(
                {
                    "source": source,
                    "replacement": replacement,
                    "enabled": True,
                    "approved": True,
                    "note": note,
                }
            )
            next_items.append(new_item)
            updated = True
        else:
            next_items.append(item)

    if not updated:
        next_items.append(
            {
                "source": source,
                "replacement": replacement,
                "enabled": True,
                "approved": True,
                "note": note,
            }
        )

    payload["phrase_fixes"] = next_items
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "source": source,
        "replacement": replacement,
        "updated": updated,
        "path": str(path),
    }


def dictionary_version() -> str:
    return glossary_version().compact()


def known_glossary_terms() -> dict[str, str]:
    return dict(_load_terms())


def glossary_version() -> GlossaryVersion:
    return GlossaryVersion(
        dictionary=_file_fingerprint(DICTIONARY_PATH) if DICTIONARY_PATH.exists() else "missing",
        phrase_fixes=_file_fingerprint(PHRASE_FIXES_PATH) if PHRASE_FIXES_PATH.exists() else "missing",
        memory_terms=_file_fingerprint(MEMORY_TERMS_PATH) if MEMORY_TERMS_PATH.exists() else "missing",
        profile=_file_fingerprint(PROFILE_PATH) if PROFILE_PATH.exists() else "missing",
        builtins_used=not DICTIONARY_PATH.exists(),
    )


def _load_terms() -> dict[str, str]:
    if not DICTIONARY_PATH.exists():
        terms = dict(DEFAULT_TERMS)
        terms.update(_load_memory_terms())
        return terms
    payload = _read_json_config(DICTIONARY_PATH)
    terms: dict[str, str] = {}
    for item in _as_list(payload.get("terms") if isinstance(payload, dict) else None):
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical") or "").strip()
        if not canonical:
            continue
        aliases = [canonical, *_as_list(item.get("aliases"))]
        for alias in aliases:
            value = str(alias).strip()
            if value:
                terms[value.lower()] = canonical
    terms.update(_load_memory_terms())
    return terms


def _load_memory_terms() -> dict[str, str]:
    if not MEMORY_TERMS_PATH.exists():
        return {}
    payload = _read_json_config(MEMORY_TERMS_PATH)
    terms: dict[str, str] = {}
    for item in _as_list(payload.get("terms") if isinstance(payload, dict) else None):
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        enabled = item.get("enabled", True)
        if not canonical or enabled is False or status not in {"auto", "approved"}:
            continue
        aliases = [canonical, *_as_list(item.get("aliases"))]
        for alias in aliases:
            value = str(alias).strip()
            if value:
                terms[value.lower()] = canonical
    return terms


def _load_phrase_fixes() -> dict[str, str]:
    if not PHRASE_FIXES_PATH.exists():
        return {}
    payload = _read_json_config(PHRASE_FIXES_PATH)
    fixes: dict[str, str] = {}
    raw = payload.get("phrase_fixes") if isinstance(payload, dict) else None
    if isinstance(raw, dict):
        raise RuntimeError("phrase_fixes must be a list of approved objects, not a shorthand dict")
    for item in _as_list(raw):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        replacement = str(item.get("replacement") or "").strip()
        enabled = item.get("enabled", True)
        approved = bool(item.get("approved", False))
        note = str(item.get("note") or "").strip()
        if source and replacement and enabled is not False and approved and note:
            fixes[source] = replacement
    return fixes


def _read_json_config(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid RambleFix config JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid RambleFix config shape: {path} must contain a JSON object")
    return payload


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        version = str(payload.get("version") or "unversioned") if isinstance(payload, dict) else "invalid-shape"
    except json.JSONDecodeError:
        version = "invalid-json"
    return f"{version}:{digest}"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _repair_spelled_acronym_sequences(text: str, terms: dict[str, str]) -> str:
    acronyms = _known_acronyms(terms)
    if not acronyms:
        return text
    pattern = re.compile(r"(?<![A-Za-z])(?:[A-Z](?:\s*,\s*|\s+)){1,7}[A-Z](?![A-Za-z])")
    repaired = text
    for match in reversed(list(pattern.finditer(text))):
        letters = re.findall(r"[A-Z]", match.group(0))
        replacement = _best_spelled_acronym_replacement(letters, acronyms)
        if replacement:
            repaired = repaired[: match.start()] + replacement + repaired[match.end() :]
    return repaired


def _known_acronyms(terms: dict[str, str]) -> list[str]:
    values = set(terms.values()) | set(DEFAULT_TERMS.values())
    acronyms = [
        value
        for value in values
        if re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", value) and value.upper() not in {"OK"}
    ]
    return sorted(acronyms, key=lambda value: (-len(value), value))


def _best_spelled_acronym_replacement(letters: list[str], acronyms: list[str]) -> str | None:
    spelled = "".join(letters).upper()
    exact = [term for term in acronyms if term == spelled]
    if exact:
        return exact[0]

    result: list[str] = []
    index = 0
    while index < len(spelled):
        best: tuple[int, str] | None = None
        for term in acronyms:
            size = len(term)
            segment = spelled[index : index + size]
            if len(segment) != size:
                continue
            distance = _levenshtein(segment, term)
            first_two_match = size >= 3 and segment[:2] == term[:2]
            if distance == 0 or (distance == 1 and first_two_match):
                score = (distance, -size)
                if best is None or score < (best[0], -len(best[1])):
                    best = (distance, term)
        if best is None:
            return None
        result.append(best[1])
        index += len(best[1])

    return ", ".join(result) if len(result) >= 2 else None


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for row, char_a in enumerate(a, 1):
        current = [row]
        for col, char_b in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[col] + 1, previous[col - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]
