from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from textwrap import dedent

from ramblefix.glossary import apply_glossary
from ramblefix.hymt import normalize_with_hymt
from ramblefix.work_polish import polish_meaning_first_work_text


@dataclass(frozen=True)
class ProcessedOutput:
    clean_transcript: str
    prompt_mode: str
    processor: str


DOMAIN_HINTS = """
Preserve workplace and builder terms exactly when likely:
Cursor, Codex, Claude, ChatGPT, LLM, PRD, API, SDK, BCom, BPD, Partner Center,
Fee Admin, Cupcake, DPS, PCI, PII, SOX, Grafana, Agoda, Priceline, Riskified,
SharePoint, Teams, MCP, PR, MR, OKR, Q2, Q3, H1, H2.
"""


def process_transcript(
    text: str,
    *,
    use_ollama: bool = True,
    model: str = "llama3.1:8b",
    use_hymt: bool = False,
    hymt_model: str = "hymt-1.8b",
) -> ProcessedOutput:
    normalized = apply_glossary(_normalize_spacing(text))
    if not normalized:
        return ProcessedOutput(clean_transcript="", prompt_mode="", processor="blank-guard")

    if use_hymt:
        try:
            translated = normalize_with_hymt(normalized, model=hymt_model, target="english")
            normalized = apply_glossary(translated.text)
        except Exception:
            # HY-MT GGUF support is runtime-dependent; keep the rest of the pipeline usable.
            pass

    if use_ollama and shutil.which("ollama"):
        try:
            return _process_with_ollama(normalized, model=model)
        except Exception:
            pass

    return ProcessedOutput(
        clean_transcript=_fallback_clean(normalized),
        prompt_mode=_fallback_prompt(normalized),
        processor="fallback-rules",
    )


def _process_with_ollama(text: str, *, model: str) -> ProcessedOutput:
    prompt = dedent(
        f"""
        You clean up mixed-language builder speech. The speaker may mix English
        with Hindi/Hinglish or another language. Preserve the speaker's meaning.
        Do not invent facts. Preserve names, product terms, acronyms, timelines,
        and technical terms.

        Domain hints:
        {DOMAIN_HINTS}

        Return exactly this format:

        CLEAN_TRANSCRIPT:
        <cleaned transcript, readable but faithful>

        PROMPT_MODE:
        Goal:
        Context:
        Requirements:
        Constraints:
        Open questions:

        Raw transcript:
        {text}
        """
    ).strip()

    completed = subprocess.run(
        ["ollama", "run", model, prompt],
        check=True,
        text=True,
        capture_output=True,
        timeout=180,
    )
    raw = completed.stdout.strip()
    clean, prompt_mode = _split_ollama_output(raw)
    return ProcessedOutput(clean_transcript=clean, prompt_mode=prompt_mode, processor=f"ollama:{model}")


def _split_ollama_output(raw: str) -> tuple[str, str]:
    clean_marker = "CLEAN_TRANSCRIPT:"
    prompt_marker = "PROMPT_MODE:"
    if clean_marker in raw and prompt_marker in raw:
        clean = raw.split(clean_marker, 1)[1].split(prompt_marker, 1)[0].strip()
        prompt = raw.split(prompt_marker, 1)[1].strip()
        return clean, prompt
    return raw, raw


def _normalize_spacing(text: str) -> str:
    text = re.sub(r"\[(?:BLANK_AUDIO|MUSIC|NOISE|SILENCE|INAUDIBLE)\]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<v[^>]*>", "", text)
    text = re.sub(r"</v>", "", text)
    text = re.sub(r"\d\d:\d\d:\d\d\.\d+\s+-->\s+\d\d:\d\d:\d\d\.\d+", " ", text)
    text = re.sub(r"\bWEBVTT\b", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fallback_clean(text: str) -> str:
    replacements = {
        "risk if I'd": "Riskified",
        "risk if I": "Riskified",
        "price line": "Priceline",
        "share point": "SharePoint",
        "go guide": "Agoda",
        "a go guide": "Agoda",
        "partner enter": "Partner Center",
        "PPT partner center": "BPD Partner Center",
        "IDE4": "IPv4",
        "headquote": "headcount",
    }
    cleaned = apply_glossary(text)
    for old, new in replacements.items():
        cleaned = re.sub(re.escape(old), new, cleaned, flags=re.IGNORECASE)
    cleaned = polish_meaning_first_work_text(cleaned).text
    cleaned = re.sub(r"\b(you know|like|right)\b[, ]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _fallback_prompt(text: str) -> str:
    cleaned = _fallback_clean(text)
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    useful = [
        sentence.strip()
        for sentence in sentences
        if any(
            keyword in sentence.lower()
            for keyword in [
                "need",
                "want",
                "goal",
                "problem",
                "build",
                "clarify",
                "check",
                "action",
                "decision",
                "constraint",
                "should",
                "must",
            ]
        )
    ]
    compact = " ".join(useful[:12]) or cleaned[:1200]
    return dedent(
        f"""
        Goal:
        Convert the spoken notes into a useful work artifact.

        Context:
        {compact}

        Requirements:
        - Preserve concrete names, dates, acronyms, owners, and constraints.
        - Remove filler and repetition.
        - Do not invent facts.

        Open questions:
        - Confirm any unclear names or domain terms from the original audio/transcript.
        """
    ).strip()
