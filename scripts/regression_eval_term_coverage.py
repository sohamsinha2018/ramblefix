from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import term_coverage_report  # noqa: E402


def assert_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def main() -> None:
    hindi_variant_report = term_coverage_report(
        "",
        "Vaise aaj West Bengal, pashchim bangaal jahaan se main aata hoon election hua, kaaphi interesting tha.",
        ["waise", "bangal", "jahan", "kaafi"],
    )
    assert_equal(hindi_variant_report["coverage"], 1.0, "Roman Hindi spelling variants should count as term hits")
    assert_equal(hindi_variant_report["misses"], [], "Roman Hindi spelling variants should not be marked missing")

    english_report = term_coverage_report("", "I heard call and coil but not the target word.", ["goal"])
    assert_equal(english_report["hits"], [], "English/product terms must not get fuzzy Hindi matching")
    assert_equal(english_report["misses"], ["goal"], "Missing English/product terms should remain misses")

    acronym_report = term_coverage_report("", "The text says ASR but not the other acronym.", ["asr", "mcp"])
    assert_equal(acronym_report["hits"], ["asr"], "Exact acronym terms should still work")
    assert_equal(acronym_report["misses"], ["mcp"], "Acronym misses should still be preserved")

    auto_anchor_report = term_coverage_report(
        "Waise aaj West Bengal, Pashchim Bangal jahan se main aata hoon, wahan election hua. Kaafi interesting tha.",
        "Vaise aaj West Bengal, pashchim bangaal jahaan se main aata hoon election hua, kaaphi interesting tha.",
    )
    assert_equal(
        auto_anchor_report["misses"],
        [],
        "Auto anchors should ignore Roman Hindi grammar and accept harmless spelling variants",
    )

    print("eval term coverage regression passed")


if __name__ == "__main__":
    main()
