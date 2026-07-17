#!/usr/bin/env python3
"""Verify public benchmark claims against measured scorecard artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "docs/benchmark_claims_20260713.json"
SITE = ROOT / "site/index.html"
RELEASE_NOTES = ROOT / "docs/github_release_v0_0_1.md"
READOUT = ROOT / "docs/current_regression_readout_20260709.md"


def fail(message: str) -> None:
    print(f"benchmark claim audit failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"could not read JSON {path}: {exc}")


def assert_contains(path: Path, snippets: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    for snippet in snippets:
        if snippet not in text:
            fail(f"{path.relative_to(ROOT)} missing snippet: {snippet!r}")


def assert_not_contains(path: Path, snippets: list[str]) -> None:
    text = path.read_text(encoding="utf-8").lower()
    for snippet in snippets:
        if snippet.lower() in text:
            fail(f"{path.relative_to(ROOT)} contains forbidden claim: {snippet!r}")


def find_row(rows: list[dict[str, Any]], selector: dict[str, str], source_file: str) -> dict[str, Any]:
    matches = []
    for row in rows:
        if all(str(row.get(key)) == str(value) for key, value in selector.items()):
            matches.append(row)
    if len(matches) != 1:
        fail(f"{source_file} selector {selector} matched {len(matches)} rows")
    return matches[0]


def assert_close(actual: Any, expected: float, *, context: str) -> None:
    try:
        value = float(actual)
    except (TypeError, ValueError):
        fail(f"{context} is not numeric: {actual!r}")
    if abs(value - expected) > 0.0005:
        fail(f"{context} expected {expected}, got {value}")


def main() -> None:
    claims = load_json(CLAIMS)
    for rel in claims.get("evidence_files", []):
        path = ROOT / rel
        if not path.exists():
            fail(f"evidence file missing: {rel}")

    for card in claims.get("claim_cards", []):
        source_file = str(card["source_file"])
        source_path = ROOT / source_file
        data = load_json(source_path)
        source_section = str(card.get("source_section") or "$")
        if source_section == "$":
            rows = [data]
        else:
            rows = data.get(source_section)
            if not isinstance(rows, list):
                fail(f"{source_file} missing section {source_section}")
        row = find_row(rows, dict(card["selector"]), source_file)
        for metric_name, metric in dict(card["metrics"]).items():
            assert_close(
                row.get(str(metric["source_key"])),
                float(metric["expected"]),
                context=f"{card['id']}.{metric_name}",
            )
        assert_contains(SITE, list(card.get("site_must_contain", [])))

    assert_contains(SITE, list(claims.get("site_must_contain", [])))
    assert_contains(RELEASE_NOTES, list(claims.get("release_notes_must_contain", [])))
    assert_contains(READOUT, list(claims.get("readout_must_contain", [])))

    forbidden = list((claims.get("claim_boundary") or {}).get("forbidden", []))
    for path in [SITE, RELEASE_NOTES, READOUT]:
        assert_not_contains(path, forbidden)

    print("benchmark claim audit passed")


if __name__ == "__main__":
    main()
