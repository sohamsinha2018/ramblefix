from __future__ import annotations

import re
from enum import Enum


class LanguageMix(str, Enum):
    ENGLISH_ONLY = "english_only"
    CHINESE_ONLY = "chinese_only"
    CHINESE_ENGLISH = "chinese_english"
    OTHER_OR_UNCERTAIN = "other_or_uncertain"


HAN_RE = re.compile(
    "["
    "\u3400-\u4DBF"
    "\u4E00-\u9FFF"
    "\uF900-\uFAFF"
    "\U00020000-\U0002A6DF"
    "\U0002A700-\U0002B73F"
    "\U0002B740-\U0002B81F"
    "\U0002B820-\U0002CEAF"
    "\U0002CEB0-\U0002EBEF"
    "]"
)
LATIN_WORD_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]{2,}|[A-Z])(?:[-'][A-Za-z0-9]+)*(?![A-Za-z0-9])")


def has_chinese_script(text: str) -> bool:
    return bool(HAN_RE.search(text))


def has_english_signal(text: str) -> bool:
    return bool(LATIN_WORD_RE.search(text))


def is_chinese_english_code_switch(text: str) -> bool:
    """Return true only for mixed English + Chinese-script text.

    Pure Chinese is intentionally false. RambleFix's Chinese lane is for
    code-switched dictation, not general Chinese transcription.
    """
    return has_chinese_script(text) and has_english_signal(text)


def classify_language_mix(text: str) -> LanguageMix:
    has_chinese = has_chinese_script(text)
    has_english = has_english_signal(text)
    if has_chinese and has_english:
        return LanguageMix.CHINESE_ENGLISH
    if has_chinese:
        return LanguageMix.CHINESE_ONLY
    if has_english:
        return LanguageMix.ENGLISH_ONLY
    return LanguageMix.OTHER_OR_UNCERTAIN
