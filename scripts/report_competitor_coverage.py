from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS = (
    ROOT / "eval_runs/same-wav-app-competitor-probe-20260703-status/app_probe_status.json"
)
DEFAULT_SCORECARD = (
    ROOT
    / "eval_runs/same-wav-app-competitor-probe-20260614/public95-openwhispr-ramblefix-current-score/scorecard.json"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "eval_runs/goal-stt-optimization-20260703-expanded-v5/competitor_coverage"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report same-WAV competitor proof coverage for the RambleFix goal."
    )
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    status_path = _resolve(args.status)
    scorecard_path = _resolve(args.scorecard)
    output_dir = _resolve(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    statuses = _load_json(status_path)
    scorecard = _load_json(scorecard_path)
    payload = _build_payload(statuses, scorecard, status_path, scorecard_path)

    (output_dir / "competitor_coverage.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "competitor_coverage.md").write_text(_markdown(payload), encoding="utf-8")
    print(_console(payload))
    print(f"wrote {output_dir / 'competitor_coverage.md'}")


def _build_payload(
    statuses: list[dict[str, Any]],
    scorecard: dict[str, Any],
    status_path: Path,
    scorecard_path: Path,
) -> dict[str, Any]:
    measured = _measured(scorecard)
    tools = [
        _tool_row("OpenWhispr bundled Whisper engine", "measured_engine", measured.get("openwhispr_bundle_whisper_server_small") or measured.get("openwhispr_bundle_whisper_server_base")),
        _tool_row("TypeWhisper app/API", _status_for(statuses, "TypeWhisper"), None),
        _tool_row("Wispr Flow app", _status_for(statuses, "Wispr Flow"), None),
        _tool_row("Handy app", _status_for(statuses, "Handy"), None),
        _tool_row("OpenWhispr Parakeet/app UX", "blocked", None),
        _tool_row("VoiceInk app", _status_for(statuses, "VoiceInk"), None),
        _tool_row("Apple Dictation", _status_for(statuses, "Apple Dictation"), None),
    ]
    return {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "inputs": {
            "status": _rel(status_path),
            "scorecard": _rel(scorecard_path),
        },
        "measured_summary": measured,
        "tools": tools,
        "claim_boundary": _claim_boundary(tools),
        "next_actions": _next_actions(tools),
    }


def _measured(scorecard: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in scorecard.get("summary", []):
        backend = str(row.get("backend"))
        bucket = str(row.get("bucket"))
        out.setdefault(backend, {})[bucket] = {
            "rows": row.get("rows"),
            "useful": row.get("useful"),
            "p95_seconds": row.get("p95_seconds"),
            "coverage": row.get("coverage"),
            "term_coverage": row.get("term_coverage"),
        }
    return out


def _tool_row(name: str, status: Any, measured: Any) -> dict[str, Any]:
    if isinstance(status, dict):
        return {
            "tool": name,
            "coverage": status.get("status", "unknown"),
            "evidence": status.get("evidence", ""),
            "detail": status.get("detail", ""),
            "measured": measured or {},
        }
    return {
        "tool": name,
        "coverage": str(status),
        "evidence": "scorecard" if measured else "",
        "detail": "",
        "measured": measured or {},
    }


def _status_for(statuses: list[dict[str, Any]], tool: str) -> dict[str, Any]:
    for status in statuses:
        if status.get("tool") == tool:
            return status
    return {"tool": tool, "status": "missing", "evidence": "status_probe", "detail": "No probe status row found."}


def _claim_boundary(tools: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [tool for tool in tools if tool["coverage"] == "measured_engine"]
    unmeasured = [tool for tool in tools if tool["coverage"] != "measured_engine"]
    return {
        "defensible_now": [
            "RambleFix beats OpenWhispr's bundled local Whisper engine on the public95 same-WAV engine slice."
        ]
        if measured
        else [],
        "not_defensible_yet": [
            "RambleFix beats every local/free dictation app.",
            "RambleFix beats Wispr Flow, TypeWhisper, Handy, VoiceInk, Apple Dictation, or OpenWhispr Parakeet at app level.",
        ],
        "unmeasured_tools": [tool["tool"] for tool in unmeasured],
    }


def _next_actions(tools: list[dict[str, Any]]) -> list[str]:
    actions = [
        "Enable TypeWhisper API server and install/select a local model, then rerun same-WAV CLI transcribe.",
        "Build or script a virtual-audio/global-hotkey path for Wispr Flow, Handy, and Apple Dictation; status-only probes are not quality evidence.",
        "Find or expose OpenWhispr Parakeet server/file adapter before claiming against Parakeet.",
        "Install VoiceInk or mark it out of scope; missing app cannot be scored.",
    ]
    if any(tool["coverage"] == "measured_engine" for tool in tools):
        actions.append("Keep OpenWhispr bundled Whisper engine as measured baseline, but label it engine-level only.")
    return actions


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Competitor Coverage Report",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        "## Direct Answer",
        "",
        "We do not yet have enough app-level same-WAV evidence to claim RambleFix beats every local/free dictation app.",
        "",
        "What we do have: same-WAV engine-level evidence against OpenWhispr's bundled Whisper server.",
        "",
        "## Coverage",
        "",
        "| Tool | Coverage | Evidence | Measured read / blocker |",
        "| --- | --- | --- | --- |",
    ]
    for tool in payload["tools"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _esc(tool["tool"]),
                    _esc(tool["coverage"]),
                    _esc(tool["evidence"]),
                    _esc(_tool_detail(tool)),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Claim Boundary",
            "",
            "Defensible now:",
            "",
            *[f"- {claim}" for claim in payload["claim_boundary"]["defensible_now"]],
            "",
            "Not defensible yet:",
            "",
            *[f"- {claim}" for claim in payload["claim_boundary"]["not_defensible_yet"]],
            "",
            "## Next Actions",
            "",
            *[f"- {action}" for action in payload["next_actions"]],
            "",
            "## Inputs",
            "",
            f"- status: `{payload['inputs']['status']}`",
            f"- scorecard: `{payload['inputs']['scorecard']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _tool_detail(tool: dict[str, Any]) -> str:
    measured = tool.get("measured") or {}
    if measured:
        parts = []
        for bucket, row in measured.items():
            parts.append(
                f"{bucket}: useful={row.get('useful')} p95={row.get('p95_seconds')}s rows={row.get('rows')}"
            )
        return "; ".join(parts)
    return str(tool.get("detail") or "")


def _console(payload: dict[str, Any]) -> str:
    unmeasured = ", ".join(payload["claim_boundary"]["unmeasured_tools"])
    return "\n".join(
        [
            "Competitor coverage",
            "- measured: OpenWhispr bundled Whisper engine only",
            f"- missing app-level proof: {unmeasured}",
            "- claim: do not say 'beats everyone' yet",
        ]
    )


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"missing required artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _esc(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    main()
