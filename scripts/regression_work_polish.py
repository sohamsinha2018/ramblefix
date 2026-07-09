from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.processing import process_transcript
from ramblefix.work_polish import polish_meaning_first_work_text


def check(name: str, actual: str, expected: str) -> None:
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")


def main() -> None:
    product = polish_meaning_first_work_text(
        "I am thinking about how to solve for a Cursor ChatGPT. I want you to tell me which skill I use."
    )
    check(
        "product problem repair",
        product.text,
        "I am thinking about how to solve a Cursor ChatGPT problem. I want you to tell me which skill I use.",
    )

    local = polish_meaning_first_work_text("It should be a solution because company data cannot go to the cloud.")
    check(
        "local tool cloud repair",
        local.text,
        "This tool should be local because company data cannot go to the cloud.",
    )

    no_cursor_hallucination = polish_meaning_first_work_text("Make a clean prompt for Codex but don't lose the meaning.")
    check(
        "no cursor hallucination",
        no_cursor_hallucination.text,
        "Make a clean prompt for Codex but don't lose the meaning.",
    )

    processed = process_transcript(
        "It should be a solution because company data cannot go to the cloud.",
        use_ollama=False,
    )
    check(
        "processing cleanup integration",
        processed.clean_transcript,
        "This tool should be local because company data cannot go to the cloud.",
    )
    print("work polish regression passed")


if __name__ == "__main__":
    main()
