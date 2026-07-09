from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from textwrap import dedent


DEFAULT_HYMT_MODEL = "hymt-1.8b"


@dataclass(frozen=True)
class TranslationOutput:
    text: str
    processor: str


def normalize_with_hymt(
    text: str,
    *,
    model: str = DEFAULT_HYMT_MODEL,
    target: str = "english",
    timeout_seconds: int = 120,
) -> TranslationOutput:
    if not shutil.which("ollama"):
        raise RuntimeError("Ollama is not installed")

    target_instruction = {
        "english": "Convert the input into clean, concise English.",
        "hinglish": "Convert the input into clean Hinglish, preserving natural Hindi-English mixing.",
    }.get(target, target)

    prompt = dedent(
        f"""
        You are a translation and normalization engine for mixed-language speech transcripts.

        Task:
        {target_instruction}

        Rules:
        - Preserve intent.
        - Preserve product names, acronyms, company names, and technical terms.
        - Do not add new facts.
        - Fix obvious ASR artifacts only when the intended term is clear.
        - Output only the normalized text.

        Important terms:
        Cursor, Codex, Claude, ChatGPT, Teams, SharePoint, MCP, Agoda, Booking Holdings,
        Partner Center, Fee Admin, BCom, BPD, PCI, PII, SOX, Riskified, API, SDK, PRD.

        Input:
        {text}
        """
    ).strip()

    completed = subprocess.run(
        ["ollama", "run", model, prompt],
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    return TranslationOutput(text=completed.stdout.strip(), processor=f"hymt:{model}")
