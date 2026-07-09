from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.meeting_engine import transcribe_meeting_audio


SHORT_REMOTE_TEXT = "The company says the renewal blocker is SOC two evidence and Hindi support."
SHORT_MIC_TEXT = "My mic response is please assign Arjun the API action item by Friday."

LONG_REMOTE_TEXT = """
Welcome everyone. The Acme procurement team says the renewal blocker is SOC two evidence,
data residency, and Hindi support. Please confirm the legal review, update the shared workspace,
and send Kubernetes migration risks before Wednesday. Piyush also said that when Hindi and English
are mixed, the transcript must still keep the decision, owner, deadline, and action items.
""".strip()

LONG_MIC_TEXT = """
My mic response is that I will send the API evidence, ask Priya for the FMS numbers,
and create the MCP checklist. If they ask in Hindi, bolo ki hum Friday tak pricing
aur security note bhej denge. Also remind the team that the open claw prototype
and the Stanford AI report are only context, not customer commitments.
""".strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate local dual-source meeting transcript retention.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "logs/dual_source_meeting_smoke")
    parser.add_argument("--chunk-seconds", type=float, default=5.0)
    parser.add_argument("--scenario", choices=["short", "long"], default="short")
    args = parser.parse_args()

    out_dir = args.output_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    remote_wav = out_dir / "meeting_audio.wav"
    mic_wav = out_dir / "my_mic.wav"
    remote_dir = out_dir / "remote_transcript"
    mic_dir = out_dir / "mic_transcript"
    remote_dir.mkdir(parents=True, exist_ok=True)
    mic_dir.mkdir(parents=True, exist_ok=True)

    remote_text = LONG_REMOTE_TEXT if args.scenario == "long" else SHORT_REMOTE_TEXT
    mic_text = LONG_MIC_TEXT if args.scenario == "long" else SHORT_MIC_TEXT

    synthesize(remote_text, remote_wav)
    synthesize(mic_text, mic_wav)

    remote = transcribe_meeting_audio(remote_wav, output_dir=remote_dir, chunk_seconds=args.chunk_seconds, mode="fast")
    mic = transcribe_meeting_audio(mic_wav, output_dir=mic_dir, chunk_seconds=args.chunk_seconds, mode="fast")
    combined = f"[Meeting audio]\n{remote.text.strip()}\n\n[My mic]\n{mic.text.strip()}".strip()

    checks = base_checks(combined)
    if args.scenario == "long":
        checks.extend(long_checks(combined, remote, mic))
    else:
        checks.extend(short_checks(combined))
    payload: dict[str, Any] = {
        "ok": all(check["passed"] for check in checks if check.get("required", True)),
        "scenario": args.scenario,
        "output_dir": str(out_dir),
        "remote_audio": str(remote_wav),
        "mic_audio": str(mic_wav),
        "remote_seconds": remote.seconds,
        "mic_seconds": mic.seconds,
        "remote_audio_seconds": remote.audio_seconds,
        "mic_audio_seconds": mic.audio_seconds,
        "remote_segment_count": len(remote.segments),
        "mic_segment_count": len(mic.segments),
        "remote_text": remote.text,
        "mic_text": mic.text,
        "combined_text": combined,
        "checks": checks,
    }
    (out_dir / "dual_source_meeting.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "dual_source_meeting.txt").write_text(combined + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    raise SystemExit(0 if payload["ok"] else 1)


def synthesize(text: str, wav_path: Path) -> None:
    aiff_path = wav_path.with_suffix(".aiff")
    subprocess.run(["say", "-o", str(aiff_path), text], check=True, cwd=ROOT)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", str(aiff_path), str(wav_path)], check=True, cwd=ROOT)


def base_checks(combined: str) -> list[dict[str, Any]]:
    return [
        check_contains("remote label", combined, "[Meeting audio]"),
        check_contains("mic label", combined, "[My mic]"),
    ]


def short_checks(combined: str) -> list[dict[str, Any]]:
    return [
        check_contains_any("remote SOC2 evidence meaning", combined, ["soc two evidence", "soc 2 evidence", "soc2 evidence"]),
        check_contains("remote Hindi support", combined, "hindi support"),
        check_contains("mic API term", combined, "api"),
        check_contains("mic Friday action timing", combined, "friday"),
    ]


def long_checks(combined: str, remote: Any, mic: Any) -> list[dict[str, Any]]:
    term_checks = [
        check_contains_any(
            "remote SOC2 evidence meaning",
            combined,
            ["soc two evidence", "soc 2 evidence", "soc2 evidence"],
            required=False,
        ),
        check_contains("remote data residency", combined, "data residency", required=False),
        check_contains("remote Hindi support", combined, "hindi support", required=False),
        check_contains("remote Kubernetes term", combined, "kubernetes", required=False),
        check_contains("remote Wednesday deadline", combined, "wednesday", required=False),
        check_contains("mic API term", combined, "api", required=False),
        check_contains("mic FMS term", combined, "fms", required=False),
        check_contains_any("mic MCP term", combined, ["mcp", "m c p"], required=False),
        check_contains("mic Friday deadline", combined, "friday", required=False),
        check_contains("mic pricing term", combined, "pricing", required=False),
        check_contains("mic Stanford AI report term", combined, "stanford ai report", required=False),
    ]
    passed_terms = sum(1 for check in term_checks if check["passed"])
    return [
        check_min("remote has multiple chunks", len(remote.segments), 2),
        check_min("mic has multiple chunks", len(mic.segments), 2),
        *term_checks,
        check_min("long meeting term recall", passed_terms, 8),
    ]


def check_contains(name: str, text: str, needle: str, *, required: bool = True) -> dict[str, Any]:
    passed = contains_term_or_phrase(text, needle)
    return {"name": name, "passed": passed, "expected": needle, "required": required}


def check_contains_any(name: str, text: str, needles: list[str], *, required: bool = True) -> dict[str, Any]:
    passed = any(contains_term_or_phrase(text, needle) for needle in needles)
    return {"name": name, "passed": passed, "expected_any": needles, "required": required}


def check_min(name: str, value: int | float, minimum: int | float, *, required: bool = True) -> dict[str, Any]:
    return {"name": name, "passed": value >= minimum, "value": value, "minimum": minimum, "required": required}


def contains_term_or_phrase(text: str, needle: str) -> bool:
    if not re.search(r"[A-Za-z0-9]", needle):
        return needle.lower() in text.lower()
    pattern = r"(?<![A-Za-z0-9])" + r"\s+".join(re.escape(part) for part in needle.split()) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


if __name__ == "__main__":
    main()
