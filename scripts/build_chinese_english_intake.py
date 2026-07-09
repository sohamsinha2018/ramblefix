#!/usr/bin/env python3
"""Create a Chinese+English corpus intake scaffold without touching product routes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.language_mix import classify_language_mix, is_chinese_english_code_switch


SOURCES: list[dict[str, Any]] = [
    {
        "id": "talcs",
        "name": "TALCS",
        "language": "zh-en",
        "region": "mainland_china",
        "category": "talcs_zh_en_codeswitch",
        "bucket": "zh_en_public_codeswitch",
        "reference_trust": "silver",
        "url": "https://ai.100tal.com/dataset",
        "paper": "https://arxiv.org/abs/2206.13135",
        "notes": "587h Mandarin-English online teaching code-switch corpus. Strong large public ASR source.",
    },
    {
        "id": "ascend",
        "name": "ASCEND",
        "language": "zh-en",
        "region": "hong_kong",
        "category": "ascend_zh_en_conversation",
        "bucket": "zh_en_public_codeswitch",
        "reference_trust": "silver",
        "url": "https://arxiv.org/abs/2112.06223",
        "paper": "https://arxiv.org/abs/2112.06223",
        "notes": "10.62h spontaneous Chinese-English multi-turn conversational speech.",
    },
    {
        "id": "cs_dialogue",
        "name": "CS-Dialogue",
        "language": "zh-en",
        "region": "mainland_china",
        "category": "cs_dialogue_zh_en_spontaneous",
        "bucket": "zh_en_public_codeswitch",
        "reference_trust": "silver",
        "url": "https://arxiv.org/abs/2502.18913",
        "paper": "https://arxiv.org/abs/2502.18913",
        "notes": "104h spontaneous Mandarin-English dialogue from 200 speakers; availability still needs verification.",
    },
    {
        "id": "merlion_ccs",
        "name": "MERLIon CCS",
        "language": "zh-en",
        "region": "singapore",
        "category": "merlion_ccs_singapore_zh_en",
        "bucket": "zh_en_singapore_codeswitch",
        "reference_trust": "silver",
        "url": "https://doi.org/10.21979/N9/ANXS8Z",
        "paper": "https://arxiv.org/abs/2305.18881",
        "repo": "https://github.com/MERLIon-Challenge/merlion-ccs-2023",
        "access_status": "metadata_checked_dataverse_api",
        "file_summary": {
            "total_files": 495,
            "wav_files": 459,
            "tabular_files": 12,
            "plain_text_files": 6,
            "restricted_files": 4,
        },
        "notes": "30h+ Singapore English/Mandarin Zoom calls. Priority Singapore source. Downloads require accepting dataset terms; labels are language-ID/diarization, not full ASR gold.",
    },
    {
        "id": "fleurs_zh_en_regression",
        "name": "FLEURS zh/en regression",
        "language": "zh,en",
        "region": "benchmark",
        "category": "fleurs_chinese_english_regression",
        "bucket": "zh_en_monolingual_regression",
        "reference_trust": "silver",
        "url": "https://huggingface.co/datasets/google/fleurs",
        "notes": "Use for lab pure Mandarin, pure Hindi, and pure English coverage; not code-switch proof.",
        "scope": "lab_monolingual_coverage",
    },
    {
        "id": "user_singapore_prompts",
        "name": "User Singapore-style prompts",
        "language": "zh-en",
        "region": "singapore",
        "category": "user_singapore_zh_en_work",
        "bucket": "zh_en_singapore_codeswitch",
        "reference_trust": "gold_candidate",
        "url": "",
        "notes": "Record 10-15 work prompts with Singapore English + Mandarin. Promote to gold after human/cloud consensus.",
    },
]


SINGAPORE_PROMPTS = [
    "Can you help me check 这个 PR and make sure the API response is correct?",
    "Later we sync on the roadmap, 然后看一下 customer feedback.",
    "This one can lah, but 这个 latency 需要再优化.",
    "For the next sprint, 我们先 ship the local model and then test onboarding.",
    "The meeting notes are okay, 可是 action items need to be clearer.",
    "Can you compare this with Wispr Flow, 看看哪个 paste latency 更稳定?",
    "I think the English transcript is fine, 但是中文部分不要丢掉意思.",
    "This feature should run local only, 不要 call cloud API.",
    "The UI looks nice lah, but processing wave 要更明显一点.",
    "After release, paste the fast version first, 然后再 update polished text.",
]


def empty_corpus_row(source: dict[str, Any], idx: int, prompt: str = "") -> dict[str, Any]:
    item_id = f"{source['id']}_{idx:03d}"
    return {
        "id": item_id,
        "audio": "",
        "gold": prompt,
        "source": source["id"],
        "workflow": "Chinese+English corpus intake",
        "category": source["category"],
        "reference_trust": source["reference_trust"],
        "reference_source": "pending",
        "language": source["language"],
        "region": source["region"],
        "start": None,
        "end": None,
        "terms": extract_terms(prompt),
        "notes": source["notes"],
        "pool": "chinese_english_codeswitch_20260705",
        "pool_bucket": source["bucket"],
        "language_mix": classify_language_mix(prompt).value,
        "dual_language_target": is_chinese_english_code_switch(prompt),
        "status": "pending_audio",
    }


def extract_terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in ["PR", "API", "latency", "sprint", "Wispr Flow", "paste", "cloud API", "UI"]:
        pattern = rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])"
        if re.search(pattern, text, flags=re.IGNORECASE):
            terms.append(token)
    return terms


def markdown_manifest(sources: list[dict[str, Any]], corpus: Path) -> str:
    lines = [
        "# Chinese+English Corpus Intake - 2026-07-05",
        "",
        f"- Draft corpus: `{corpus}`",
        "- This is an intake scaffold, not a claim-grade gold corpus.",
        "- Production target scope is dual-language English+Chinese only.",
        "- Lab scope also includes pure English, pure Hindi, and pure Chinese coverage rows.",
        "- Pure Hindi/Chinese are lab targets, not foreground production optimization targets yet.",
        "- Singapore bucket is mandatory and uses MERLIon CCS plus user Singapore-style work prompts.",
        "",
        "## Sources",
        "",
        "| Source | Region | Bucket | Trust | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for source in sources:
        notes = str(source["notes"]).replace("|", "/")
        lines.append(
            f"| {source['name']} | {source['region']} | {source['bucket']} | {source['reference_trust']} | {notes} |"
        )
    lines.extend(
        [
            "",
            "## Next Steps",
            "",
            "1. Download/inspect MERLIon CCS from the DR-NTU DOI and convert short utterances to WAV rows.",
            "2. Pull TALCS/ASCEND/CS-Dialogue only if licensing/download format is usable.",
            "3. Record 10-15 Singapore-style work prompts through RambleFix for gold-candidate rows.",
            "4. Run same-WAV bakeoff across current fast server, Hinglish finalizer, SenseVoice, and sherpa-onnx.",
            "",
            "## Product Guardrail",
            "",
            "Do not route production Chinese+English or pure Chinese/Hindi until the eval corpus proves it improves meaning without English regression.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("eval_corpus/chinese_english_codeswitch_draft_20260705.json"))
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=Path("eval_runs/chinese-english-corpus-intake-20260705/source_manifest.json"),
    )
    parser.add_argument(
        "--manifest-md",
        type=Path,
        default=Path("eval_runs/chinese-english-corpus-intake-20260705/manifest.md"),
    )
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    user_source = next(source for source in SOURCES if source["id"] == "user_singapore_prompts")
    for idx, prompt in enumerate(SINGAPORE_PROMPTS):
        row = empty_corpus_row(user_source, idx, prompt)
        if not row["dual_language_target"]:
            raise ValueError(f"Singapore prompt is not dual-language Chinese+English: {prompt}")
        rows.append(row)

    args.corpus.parent.mkdir(parents=True, exist_ok=True)
    args.source_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_md.parent.mkdir(parents=True, exist_ok=True)

    args.corpus.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.source_manifest.write_text(json.dumps(SOURCES, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.manifest_md.write_text(markdown_manifest(SOURCES, args.corpus), encoding="utf-8")

    print(f"wrote {len(rows)} draft gold-candidate prompt rows to {args.corpus}")
    print(f"wrote source manifest to {args.source_manifest}")
    print(f"wrote markdown manifest to {args.manifest_md}")


if __name__ == "__main__":
    main()
