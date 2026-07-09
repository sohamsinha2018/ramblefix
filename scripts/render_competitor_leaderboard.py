from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path("eval_runs/competitor-leaderboard-20260613/leaderboard_inputs.json")
DEFAULT_OUTPUT = Path("docs/competitor_leaderboard_20260613.md")
DEFAULT_JSON_OUTPUT = Path("eval_runs/competitor-leaderboard-20260613/leaderboard.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the RambleFix competitor leaderboard.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    payload = build_payload(data)

    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_markdown(payload), encoding="utf-8")
    print(args.output)
    print(args.json_output)


def build_payload(data: dict[str, Any]) -> dict[str, Any]:
    tools = []
    for tool in data["tools"]:
        ratings = tool["ratings"]
        scored = {
            **tool,
            "dictation_score": weighted_score(ratings, data["weights"]["dictation"]),
            "meeting_score": weighted_score(ratings, data["weights"]["meetings"]),
        }
        tools.append(scored)
    return {
        "generated_on": data["generated_on"],
        "scales": data["scales"],
        "weights": data["weights"],
        "tools": sorted(tools, key=lambda row: (-row["dictation_score"], row["tool"].lower())),
        "meeting_tools": sorted(tools, key=lambda row: (-row["meeting_score"], row["tool"].lower())),
    }


def weighted_score(ratings: dict[str, float], weights: dict[str, float]) -> float:
    total = sum(float(ratings[key]) * weight for key, weight in weights.items())
    return round(total, 2)


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# RambleFix Competitor Leaderboard - 2026-06-13",
        "",
        "This is a working leaderboard, not a public superiority claim. Scores mix measured RambleFix data with public-source survey ratings for competitors until same-WAV app rows exist.",
        "",
        "## Verdict",
        "",
        "- Dictation is the right launch wedge: RambleFix already has measured local latency/quality evidence and a plausible Hinglish/company-data advantage.",
        "- Current focus is dictation only. Meeting capture, meeting summaries, and long-audio product work are deferred.",
        "- Meeting mode is valuable but not a clean wedge yet: OpenWhispr, MacWhisper, TypeWhisper, VoiceInk profiles, and local meeting CLIs already cover pieces of it.",
        "- The meeting wedge should be narrower: local multilingual meeting transcript + summary + actions for Indian English/Hinglish company calls, with raw transcript/audit trail and no cloud.",
        "",
        "## Evidence Rule",
        "",
        "- `measured`: same repo/corpus evidence exists.",
        "- `survey`: public docs/source review only.",
        "- `manual`: tiny/manual evidence only.",
        "- `unmeasured`: known competitor, no usable local result yet.",
        "",
        "## Dictation Leaderboard",
        "",
        "| Rank | Tool | Score | Evidence | Local Status | Latency | English | Hinglish | UX | Privacy | Notes |",
        "| ---: | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for rank, tool in enumerate(payload["tools"], start=1):
        ratings = tool["ratings"]
        lines.append(
            "| {rank} | {tool} | {score:.2f} | {evidence} | {local} | {latency:g} | {english:g} | {hinglish:g} | {ux:g} | {privacy:g} | {notes} |".format(
                rank=rank,
                tool=escape(tool["tool"]),
                score=float(tool["dictation_score"]),
                evidence=tool["evidence_level"],
                local=tool["local_status"],
                latency=ratings["latency"],
                english=ratings["english_accuracy"],
                hinglish=ratings["hinglish_accuracy"],
                ux=ratings["ux_polish"],
                privacy=ratings["local_privacy"],
                notes=escape(tool["positioning"]),
            )
        )

    lines.extend(
        [
            "",
            "## Meeting / Long Audio Leaderboard",
            "",
            "| Rank | Tool | Score | Evidence | Capture | Summary | Multilingual | Privacy | UX | Audit | Notes |",
            "| ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for rank, tool in enumerate(payload["meeting_tools"], start=1):
        ratings = tool["ratings"]
        lines.append(
            "| {rank} | {tool} | {score:.2f} | {evidence} | {capture:g} | {summary:g} | {multi:g} | {privacy:g} | {ux:g} | {audit:g} | {notes} |".format(
                rank=rank,
                tool=escape(tool["tool"]),
                score=float(tool["meeting_score"]),
                evidence=tool["evidence_level"],
                capture=ratings["meeting_capture"],
                summary=ratings["meeting_summary"],
                multi=ratings["multilingual_meetings"],
                privacy=ratings["local_privacy"],
                ux=ratings["ux_polish"],
                audit=ratings["auditability"],
                notes=escape(tool["positioning"]),
            )
        )

    lines.extend(["", "## Current Measured RambleFix Numbers", ""])
    ramblefix = next(tool for tool in payload["tools"] if tool["tool"] == "RambleFix")
    measured = ramblefix["measured"]
    lines.extend(
        [
            f"- Mixed public meaning benchmark: useful `{measured['mixed_public_useful_score']}`, p50 `{measured['mixed_public_p50_seconds']}s`, p95 `{measured['mixed_public_p95_seconds']}s`, hang risk `{measured['mixed_public_hang_risk']}`.",
            f"- Public English YouTube benchmark: useful `{measured['youtube_english_useful_score']}`, p50 `{measured['youtube_english_p50_seconds']}s`, p95 `{measured['youtube_english_p95_seconds']}s`, hang risk `{measured['youtube_english_hang_risk']}`.",
            f"- Latest live local-server dictation check: `{measured['latest_live_dictation_seconds']}s`.",
            "",
            "## Product Takeaways",
            "",
            "1. RambleFix should lead with dictation, not meetings.",
            "2. Meeting mode is worth building only as a second track after the dictation loop is reliable.",
            "3. A meeting MVP should not try to beat Zoom/Otter/Granola broadly. It should process local recordings or system audio, preserve mixed-language meaning, extract decisions/actions, and keep everything auditable/offline.",
            "4. The next proof step is same-WAV app rows for TypeWhisper, OpenWhispr, VoiceInk, Handy, Apple Dictation, and Wispr Flow in a separate cloud/aspirational track.",
            "",
            "## Tool Notes",
            "",
        ]
    )

    for tool in payload["tools"]:
        lines.extend(
            [
                f"### {tool['tool']}",
                "",
                f"- Category: `{tool['category']}`",
                f"- Evidence: `{tool['evidence_level']}`",
                f"- Strengths: {'; '.join(tool['strengths'])}",
                f"- Weaknesses: {'; '.join(tool['weaknesses'])}",
                f"- Sources: {', '.join(format_source(source) for source in tool['sources'])}",
                "",
            ]
        )
    return "\n".join(lines)


def escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def format_source(source: str) -> str:
    if source.startswith("http"):
        return f"[source]({source})"
    return f"`{source}`"


if __name__ == "__main__":
    main()
