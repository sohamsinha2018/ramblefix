from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.glossary import apply_glossary
from ramblefix.term_polish import polish_terms_with_auto


def assert_eq(actual: str, expected: str) -> None:
    if actual != expected:
        raise AssertionError(f"\nexpected: {expected!r}\nactual:   {actual!r}")


def main() -> None:
    assert_eq(
        apply_glossary("Terms are A, S, R, M, C, B, and auto correct."),
        "Terms are ASR, MCP, and auto correct.",
    )
    assert_eq(
        apply_glossary("Show me the UX skill and UI flow."),
        "Show me the UX skill and UI flow.",
    )
    assert_eq(
        apply_glossary("Use Ramble Fix tools for MCP and ASR."),
        "Use RambleFix tools for MCP and ASR.",
    )
    assert_eq(
        apply_glossary("The blocker is socked to evidence and Cuban eats migration."),
        "The blocker is SOC2 evidence and Kubernetes migration.",
    )
    assert_eq(
        apply_glossary("Use the Stanford AI reporter as background context."),
        "Use the Stanford AI Report as background context.",
    )
    assert_eq(
        apply_glossary("Use Rumble Fix tools for MCP and ASR."),
        "Use RambleFix tools for MCP and ASR.",
    )
    assert_eq(
        apply_glossary("What is the end-of-the-long safe replacement? It cannot split floor."),
        "What is the end-to-end safe replacement? It cannot split flow.",
    )
    assert_eq(
        apply_glossary("What is the end-all safe replacement? Does that mean that the respect flow or whatever cannot replace as well?"),
        "What is the end-to-end safe replacement? Does that mean that the split flow or whatever cannot replace as well?",
    )
    result = polish_terms_with_auto(
        "/tmp/ramblefix-nonexistent-audio-for-deterministic-term-polish.wav",
        draft_text="Use Rumble Fix tools for A, S, R, M, C, B.",
        timeout_seconds=0.1,
    )
    assert_eq(result.text, "Use RambleFix tools for ASR, MCP.")
    assert_eq(result.route, "term_polish_changed")
    assert_eq(result.engine, "term-polish:glossary")
    print("term repair regression passed")


if __name__ == "__main__":
    main()
