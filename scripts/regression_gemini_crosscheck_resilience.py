from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import requests

import crosscheck_goal_corpus_with_gemini as gemini


def main() -> None:
    _test_network_fail_fast()
    _test_resume_skips_done_rows()
    print("regression_gemini_crosscheck_resilience: PASS")


def _test_network_fail_fast() -> None:
    with tempfile.TemporaryDirectory(prefix="ramblefix-gemini-regression-") as tmp:
        root = Path(tmp)
        corpus = root / "corpus.json"
        output = root / "out.json"
        output_corpus = root / "confirmed.json"
        corpus.write_text(
            json.dumps(
                [
                    _row("a", root / "a.wav"),
                    _row("b", root / "b.wav"),
                    _row("c", root / "c.wav"),
                ]
            ),
            encoding="utf-8",
        )
        _run_main(
            [
                "--corpus",
                str(corpus),
                "--output",
                str(output),
                "--output-corpus",
                str(output_corpus),
                "--models",
                "gemini-2.5-flash,gemini-2.5-pro",
                "--network-fail-fast",
                "2",
            ],
            fake_transcribe=_raise_network,
        )
        rows = json.loads(output.read_text(encoding="utf-8"))
        assert len(rows) == 2, rows
        assert all(row["cloud_status"] == "cloud_failed" for row in rows), rows
        confirmed = json.loads(output_corpus.read_text(encoding="utf-8"))
        assert confirmed == [], confirmed


def _test_resume_skips_done_rows() -> None:
    with tempfile.TemporaryDirectory(prefix="ramblefix-gemini-regression-") as tmp:
        root = Path(tmp)
        corpus = root / "corpus.json"
        output = root / "out.json"
        output_corpus = root / "confirmed.json"
        corpus.write_text(json.dumps([_row("done", root / "done.wav"), _row("new", root / "new.wav")]), encoding="utf-8")
        output.write_text(
            json.dumps(
                [
                    {
                        **_row("done", root / "done.wav"),
                        "gold": "already confirmed",
                        "cloud_status": "cloud_confirmed",
                        "classification_status": "trusted",
                    }
                ]
            ),
            encoding="utf-8",
        )
        calls: list[Path] = []

        def fake_transcribe(audio: Path, **_: object) -> dict[str, object]:
            calls.append(audio)
            return {
                "language_class": "english_only",
                "confidence": 0.99,
                "transcript": "new confirmed",
                "reason": "test",
            }

        _run_main(
            [
                "--corpus",
                str(corpus),
                "--output",
                str(output),
                "--output-corpus",
                str(output_corpus),
                "--models",
                "gemini-2.5-flash,gemini-2.5-pro",
                "--resume",
            ],
            fake_transcribe=fake_transcribe,
        )
        rows = json.loads(output.read_text(encoding="utf-8"))
        assert [row["id"] for row in rows] == ["done", "new"], rows
        assert rows[0]["gold"] == "already confirmed", rows
        assert rows[1]["gold"] == "new confirmed", rows
        assert len(calls) == 2, calls
        assert all(call.name == "new.wav" for call in calls), calls


def _run_main(argv: list[str], *, fake_transcribe: object) -> None:
    old_argv = sys.argv[:]
    old_key = os.environ.get("GEMINI_API_KEY")
    old_transcribe = gemini.transcribe_and_classify
    try:
        os.environ["GEMINI_API_KEY"] = "test-key"
        sys.argv = ["crosscheck_goal_corpus_with_gemini.py", *argv]
        gemini.transcribe_and_classify = fake_transcribe  # type: ignore[assignment]
        gemini.main()
    finally:
        sys.argv = old_argv
        gemini.transcribe_and_classify = old_transcribe
        if old_key is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = old_key


def _raise_network(audio: Path, **_: object) -> dict[str, object]:
    raise requests.exceptions.ConnectionError(f"Failed to resolve generativelanguage.googleapis.com for {audio.name}")


def _row(row_id: str, audio: Path) -> dict[str, object]:
    return {
        "id": row_id,
        "bucket": "hindi_english",
        "category": "regression",
        "audio": str(audio),
        "gold": "",
        "critical": [],
        "source": "regression",
        "cloud_status": "needs_cloud_gold",
        "classification_status": "needs_cloud_classification",
        "classification_reason": "regression",
        "meta": {},
    }


if __name__ == "__main__":
    main()
