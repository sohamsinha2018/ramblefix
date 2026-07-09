#!/usr/bin/env python3
"""Regression guard for Chinese+English scope.

The product target is code-switched English+Chinese speech only. Pure Chinese
must not become a routed optimization target.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.language_mix import LanguageMix, classify_language_mix, is_chinese_english_code_switch


CASES = [
    ("Can you check 这个 PR?", LanguageMix.CHINESE_ENGLISH, True),
    ("这个 latency 需要再优化", LanguageMix.CHINESE_ENGLISH, True),
    ("不要 call cloud API", LanguageMix.CHINESE_ENGLISH, True),
    ("For the next sprint, 我们先 ship the local model.", LanguageMix.CHINESE_ENGLISH, True),
    ("这是一个纯中文句子，没有英文。", LanguageMix.CHINESE_ONLY, False),
    ("请帮我检查接口响应是否正确。", LanguageMix.CHINESE_ONLY, False),
    ("Please check the API response.", LanguageMix.ENGLISH_ONLY, False),
    ("API MCP PR latency", LanguageMix.ENGLISH_ONLY, False),
    ("12345 ...", LanguageMix.OTHER_OR_UNCERTAIN, False),
]


def main() -> None:
    for text, expected_class, expected_route in CASES:
        actual_class = classify_language_mix(text)
        actual_route = is_chinese_english_code_switch(text)
        if actual_class != expected_class:
            raise AssertionError(f"classify_language_mix({text!r}) = {actual_class}, expected {expected_class}")
        if actual_route != expected_route:
            raise AssertionError(
                f"is_chinese_english_code_switch({text!r}) = {actual_route}, expected {expected_route}"
            )
    print("chinese_english_scope_ok")


if __name__ == "__main__":
    main()
