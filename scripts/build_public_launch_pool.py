#!/usr/bin/env python3
"""Combine public benchmark corpora into one launch dictation pool."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON list")
    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(data):
        if not isinstance(row, dict):
            raise ValueError(f"{path} row {idx} is not an object")
        if not row.get("audio") or not row.get("gold"):
            raise ValueError(f"{path} row {idx} missing audio or gold")
        rows.append(row)
    return rows


def pool_bucket(row: dict[str, Any]) -> str:
    category = str(row.get("category", "")).lower()
    language = str(row.get("language", "")).lower()
    region = str(row.get("region", "")).lower()
    if (
        "zh" in language
        or "cmn" in language
        or "mandarin" in language
        or "chinese" in language
        or "fleurs_cmn" in category
        or "chinese" in category
    ):
        if "english" in category or "en" in language:
            return "chinese_english_code_switch"
        return "chinese_public"
    if "hinglish" in category or "hi-en" in language or ("hindi" in category and "english" in category):
        return "hinglish_code_switch"
    if "hindi" in category or language in {"hi", "hi_in", "hindi"}:
        return "hindi_public"
    if "youtube" in category and region:
        return f"english_youtube_{region.replace(' ', '_')}"
    return "english_public"


def merge_corpora(inputs: list[Path]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: Counter[str] = Counter()

    for path in inputs:
        for row in load_rows(path):
            item = dict(row)
            base_id = str(item.get("id") or f"row_{len(merged):05d}")
            seen[base_id] += 1
            if seen[base_id] > 1:
                item["id"] = f"{base_id}__dup{seen[base_id]}"
            else:
                item["id"] = base_id
            item["pool"] = "public_launch_dictation_20260613"
            item["pool_bucket"] = pool_bucket(item)
            item["source_corpus"] = str(path)
            item.setdefault("reference_trust", "silver")
            item.setdefault("reference_source", "public_transcript")
            merged.append(item)

    return merged


def counts(rows: list[dict[str, Any]], key: str) -> Counter[str]:
    return Counter(str(row.get(key, "missing")) for row in rows)


def render_counter(title: str, counter: Counter[str]) -> list[str]:
    lines = [f"## {title}", ""]
    for name, value in sorted(counter.items(), key=lambda pair: (-pair[1], pair[0])):
        lines.append(f"- {name}: {value}")
    lines.append("")
    return lines


def write_manifest(path: Path, output: Path, inputs: list[Path], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Public Launch Dictation Pool - 2026-06-13",
        "",
        f"- Output corpus: `{output}`",
        f"- Total clips: {len(rows)}",
        f"- Input corpora: {len(inputs)}",
        "",
        "## Inputs",
        "",
    ]
    for input_path in inputs:
        lines.append(f"- `{input_path}`")
    lines.append("")
    lines.extend(render_counter("Pool Buckets", counts(rows, "pool_bucket")))
    lines.extend(render_counter("Categories", counts(rows, "category")))
    lines.extend(render_counter("Sources", counts(rows, "source")))
    lines.extend(render_counter("Reference Trust", counts(rows, "reference_trust")))
    lines.extend(
        [
            "## How To Use",
            "",
            "Run model evals against this corpus first, then split results by `pool_bucket` before making product claims.",
            "",
            "```bash",
            "python -m ramblefix.cli eval-corpus \\",
            f"  --corpus {output} \\",
            "  --base-backends none \\",
            "  --external-backends whisper_cpp_server_translate \\",
            "  --output-dir eval_runs/public-launch-dictation-pool-eval-20260613/server-translate",
            "```",
            "",
            "## Caveats",
            "",
            "- These are public/silver references, not manually reviewed launch claims.",
            "- YouTube rows use public captions as gold; good for broad accent coverage, not final marketing proof.",
            "- FLEURS is clean benchmark speech; useful for sanity, less representative of messy dictation.",
            "- OpenSLR 104 is the strongest public proxy here for Hindi-English technical code-switching.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = merge_corpora(args.inputs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    write_manifest(args.manifest, args.output, args.inputs, rows)
    print(f"wrote {len(rows)} rows to {args.output}")
    print(f"wrote manifest to {args.manifest}")


if __name__ == "__main__":
    main()
