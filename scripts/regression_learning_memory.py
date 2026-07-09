from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ramblefix.learning_memory import (
    extract_explicit_correction_pairs,
    learn_explicit_corrections_from_rows,
    learn_terms_from_history,
    learn_terms_from_text,
)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    expect(
        extract_explicit_correction_pairs("Skill not skid please") == [("skid", "skill")],
        "short explicit correction should learn wrong -> right",
    )
    expect(
        extract_explicit_correction_pairs("I said score, not school, please.") == [("school", "score")],
        "spoken correction prefix should not become part of the replacement",
    )
    expect(
        extract_explicit_correction_pairs("Codex not codecs") == [("codecs", "Codex")],
        "known product casing should survive explicit correction",
    )
    expect(
        extract_explicit_correction_pairs("Dubai, not tool buy") == [("tool buy", "Dubai")],
        "proper-name correction should preserve user-provided casing",
    )
    expect(
        extract_explicit_correction_pairs("It's STT bro, not STD") == [("STD", "STT")],
        "spoken correction with filler should learn the wrong acronym, not the filler",
    )
    expect(
        extract_explicit_correction_pairs("There should not be any fallback because protected words are hard coded") == [],
        "ordinary should-not sentence must not be learned",
    )
    expect(
        extract_explicit_correction_pairs("Search for exotic sites where I can find, not exotic sites platforms") == [],
        "ordinary contrast sentence must not be learned",
    )

    rows = [
        {
            "status": "paste_attempted",
            "corrected_text": "By the way, is the school really risk adjusted for the trading thing?",
            "pasted_text": "",
            "raw_text": "",
        },
        {
            "status": "paste_attempted",
            "corrected_text": "I said score, not school, please.",
            "pasted_text": "",
            "raw_text": "",
        },
        {
            "status": "paste_attempted",
            "corrected_text": "Skill not skid please",
            "pasted_text": "",
            "raw_text": "",
        }
    ]
    with tempfile.TemporaryDirectory() as tmp:
        phrase_path = Path(tmp) / "phrase_fixes.json"
        payload = learn_explicit_corrections_from_rows(rows, phrase_path=phrase_path)
        expect(payload["learned"] == 2, "explicit corrections should be learned")
        config = json.loads(phrase_path.read_text(encoding="utf-8"))
        fixes = config["phrase_fixes"]
        by_source = {item["source"]: item for item in fixes}
        expect(
            by_source["is the school really risk adjusted"]["replacement"] == "is the score really risk adjusted",
            "ambiguous single-word correction should become contextual",
        )
        expect(by_source["skid"]["replacement"] == "skill", "close single-word ASR confusion can be global")
        expect(all(item["approved"] is True for item in fixes), "explicit corrections should be approved")

    with tempfile.TemporaryDirectory() as tmp:
        memory_path = Path(tmp) / "memory_terms.json"
        payload = learn_terms_from_text(
            "Okay yes, preserve terms FRN and FMS in the transcript.",
            source="test",
            min_count=2,
            path=memory_path,
        )
        expect(payload["learned"] == 2, "single clean acronyms with term context should be learned")
        expect(payload["terms"] == ["FMS", "FRN"], "only contextual clean acronyms should be promoted below min-count")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        history_path = root / "history.jsonl"
        memory_path = root / "memory_terms.json"
        phrase_path = root / "phrase_fixes.json"
        rows = [
            {"status": "paste_attempted", "pasted_text": "Hey Think Hindi English"},
            {"status": "paste_attempted", "pasted_text": "Hey Think Hindi English"},
            {"status": "paste_attempted", "pasted_text": "It is STT bro, not STD"},
            {"status": "paste_attempted", "pasted_text": "Can it preserve terms FRN and FMS?"},
            {"status": "copy_fallback", "pasted_text": "Use MCP and POC here."},
            {"status": "term_polish_replaced", "pasted_text": "Use MCP and POC again."},
        ]
        history_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        payload = learn_terms_from_history(
            history_path=history_path,
            limit=20,
            min_count=2,
            path=memory_path,
            phrase_path=phrase_path,
        )
        expect("FRN" in payload["terms"] and "FMS" in payload["terms"], "contextual acronyms should learn from history")
        expect("MCP" in payload["terms"] and "POC" in payload["terms"], "repeated acronyms should learn from history")
        expect("STD" not in payload["terms"], "wrong side of correction must not become a memory term")
        expect("Hey" not in payload["terms"] and "Think" not in payload["terms"], "ordinary title-case words must not be learned")

    print("regression_learning_memory passed")


if __name__ == "__main__":
    main()
