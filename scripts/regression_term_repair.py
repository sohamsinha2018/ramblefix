from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.glossary import apply_glossary
from ramblefix.term_polish import polish_terms_with_auto


@contextlib.contextmanager
def deterministic_glossary_config():
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="ramblefix-term-repair-") as raw_dir:
        config_dir = Path(raw_dir) / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "dictionary.json").write_text(
            json.dumps(
                {
                    "version": "regression.fixture",
                    "terms": [
                        {"canonical": "ASR", "aliases": ["asr"]},
                        {"canonical": "MCP", "aliases": ["mcp"]},
                        {"canonical": "SOC2", "aliases": ["soc2", "soc 2", "soc two", "sock two"]},
                        {"canonical": "Kubernetes", "aliases": ["kubernetes", "cuban eats"]},
                        {"canonical": "Stanford AI Report", "aliases": ["stanford ai report", "stanford ai reporter"]},
                        {"canonical": "RambleFix", "aliases": ["ramble fix", "rumble fix", "ramblefix"]},
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (config_dir / "phrase_fixes.json").write_text(
            json.dumps(
                {
                    "version": "regression.fixture",
                    "phrase_fixes": [
                        {
                            "source": "socked to evidence",
                            "replacement": "SOC2 evidence",
                            "enabled": True,
                            "approved": True,
                            "note": "Deterministic regression fixture.",
                        },
                        {
                            "source": "end-of-the-long safe replacement",
                            "replacement": "end-to-end safe replacement",
                            "enabled": True,
                            "approved": True,
                            "note": "Deterministic regression fixture.",
                        },
                        {
                            "source": "end-all safe replacement",
                            "replacement": "end-to-end safe replacement",
                            "enabled": True,
                            "approved": True,
                            "note": "Deterministic regression fixture.",
                        },
                        {
                            "source": "split floor",
                            "replacement": "split flow",
                            "enabled": True,
                            "approved": True,
                            "note": "Deterministic regression fixture.",
                        },
                        {
                            "source": "respect flow",
                            "replacement": "split flow",
                            "enabled": True,
                            "approved": True,
                            "note": "Deterministic regression fixture.",
                        },
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (config_dir / "memory_terms.json").write_text(
            json.dumps({"version": "regression.fixture", "terms": []}, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chdir(raw_dir)
        try:
            yield
        finally:
            os.chdir(original_cwd)


def assert_eq(actual: str, expected: str) -> None:
    if actual != expected:
        raise AssertionError(f"\nexpected: {expected!r}\nactual:   {actual!r}")


def main() -> None:
    with deterministic_glossary_config():
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
