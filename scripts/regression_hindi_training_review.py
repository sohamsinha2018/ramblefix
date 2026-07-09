from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCORE_SCRIPT = ROOT / "scripts/score_hindi_training_review.py"
SUGGEST_SCRIPT = ROOT / "scripts/prepare_hindi_review_suggestions.py"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ramblefix-hindi-review-regression-") as tmp:
        tmp_path = Path(tmp)
        review_path = tmp_path / "review_set.json"
        score_path = tmp_path / "scorecard.json"
        review_path.write_text(json.dumps(_fixture(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        _run([sys.executable, str(SUGGEST_SCRIPT), "--review-json", str(review_path)])
        suggested = json.loads(review_path.read_text(encoding="utf-8"))
        assert suggested["rows"][0]["suggested_gold_intent"], "suggestion should be added"
        assert not suggested["rows"][0]["gold_intent"], "suggestion must not fill gold_intent"

        _run([sys.executable, str(SCORE_SCRIPT), "--review-json", str(review_path), "--output", str(score_path)])
        unlabelled_score = json.loads(score_path.read_text(encoding="utf-8"))
        assert unlabelled_score["status"] == "needs_labels", unlabelled_score
        assert unlabelled_score["labeled_rows"] == 0, unlabelled_score
        assert unlabelled_score["summary"] == [], unlabelled_score

        suggested["rows"][0]["gold_intent"] = suggested["rows"][0]["suggested_gold_intent"]
        review_path.write_text(json.dumps(suggested, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _run([sys.executable, str(SCORE_SCRIPT), "--review-json", str(review_path), "--output", str(score_path)])
        labelled_score = json.loads(score_path.read_text(encoding="utf-8"))
        assert labelled_score["status"] == "scored", labelled_score
        assert labelled_score["labeled_rows"] == 1, labelled_score
        assert {row["candidate"] for row in labelled_score["summary"]} == {
            "current_final",
            "fast_text",
            "srota_raw",
            "vosk_hi_large",
        }, labelled_score["summary"]

    print("hindi training review regression passed")


def _run(cmd: list[str]) -> None:
    completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=30)
    if completed.returncode != 0:
        raise AssertionError(
            "command failed: {}\nstdout:\n{}\nstderr:\n{}".format(
                " ".join(cmd),
                completed.stdout,
                completed.stderr,
            )
        )


def _fixture() -> dict:
    return {
        "summary": {
            "rows": 1,
            "risk_count": 1,
            "safe_update_count": 1,
            "safe_hindi_value_count": 1,
            "hindi_stream_tail_p95": 1.2,
        },
        "rows": [
            {
                "run_id": "fixture-001",
                "audio": "/tmp/fixture.wav",
                "audio_seconds": 6.0,
                "risk": True,
                "route": "hindi_stream_safe",
                "safe_update": True,
                "tail_seconds": 1.2,
                "reject_reasons": [],
                "fast_text": "Our tool cannot beat others on one core problem.",
                "srota_raw": "haan bhai ye sab karne se kuch nahi hoga agar hamara tool cannot beat others on one core problem.",
                "current_final": "haan bhai ye sab karne se kuch nahi hoga agar hamara tool cannot beat others on one core problem.",
                "vosk_hi_large": "ये सब करने से कुछ नहीं होगा अगर हमारा टूल कोर प्रॉब्लम",
                "gold_intent": "",
                "notes": "",
            }
        ],
    }


if __name__ == "__main__":
    main()
