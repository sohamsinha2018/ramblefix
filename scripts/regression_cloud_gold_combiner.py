from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/crosscheck_goal_corpus_with_gemini.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("crosscheck_goal_corpus_with_gemini", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["crosscheck_goal_corpus_with_gemini"] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    module = _load_module()
    row = {
        "id": "regression",
        "bucket": "unknown_cloud_classify",
        "gold": "",
        "critical": [],
        "meta": {},
    }

    one_success = [
        module.GeminiResult(
            model="gemini-2.5-flash",
            ok=True,
            seconds=1.0,
            language_class="english_only",
            transcript="This is an English transcript.",
            confidence=1.0,
        ),
        module.GeminiResult(
            model="gemini-2.5-pro",
            ok=False,
            seconds=1.0,
            error="RuntimeError: DNS failed",
        ),
    ]
    combined = module._combine_row(row, one_success, min_confirming_models=2)
    assert combined["cloud_status"] == "needs_human_review", combined
    assert "only 1 successful" in combined["classification_reason"], combined

    assert module.similarity(
        "Maybe the constraint could be like within three seconds, slightly less than three seconds.",
        "Maybe the constraint could be like within 3 seconds, slightly less than 3 seconds and see.",
    ) >= 0.88

    two_success = [
        module.GeminiResult(
            model="gemini-2.5-flash",
            ok=True,
            seconds=1.0,
            language_class="english_only",
            transcript="This is an English transcript.",
            confidence=0.9,
        ),
        module.GeminiResult(
            model="gemini-2.5-pro",
            ok=True,
            seconds=1.0,
            language_class="english_only",
            transcript="This is an English transcript.",
            confidence=1.0,
        ),
    ]
    confirmed = module._combine_row(row, two_success, min_confirming_models=2)
    assert confirmed["cloud_status"] == "cloud_confirmed", confirmed
    assert confirmed["bucket"] == "english_only", confirmed

    two_of_three = [
        module.GeminiResult(
            model="gemini-2.5-flash",
            ok=True,
            seconds=1.0,
            language_class="hindi_english",
            transcript="And even for Hindi, right, the local one might update something.",
            confidence=0.9,
        ),
        module.GeminiResult(
            model="gemini-2.5-pro",
            ok=True,
            seconds=1.0,
            language_class="hindi_english",
            transcript="And even for Hindi, right, the local one might update something.",
            confidence=0.95,
        ),
        module.GeminiResult(
            model="gemini-3.5-flash",
            ok=True,
            seconds=1.0,
            language_class="hindi_english",
            transcript="This is a totally different outlier transcript.",
            confidence=0.99,
        ),
    ]
    majority = module._combine_row(row, two_of_three, min_confirming_models=2)
    assert majority["cloud_status"] == "cloud_confirmed", majority
    assert majority["bucket"] == "hindi_english", majority
    assert majority["gold"] == "And even for Hindi, right, the local one might update something.", majority

    language_mismatch = [
        module.GeminiResult(
            model="gemini-2.5-flash",
            ok=True,
            seconds=1.0,
            language_class="english_only",
            transcript="This is the same transcript.",
            confidence=0.9,
        ),
        module.GeminiResult(
            model="gemini-2.5-pro",
            ok=True,
            seconds=1.0,
            language_class="hindi_english",
            transcript="This is the same transcript.",
            confidence=0.95,
        ),
    ]
    mismatch = module._combine_row(row, language_mismatch, min_confirming_models=2)
    assert mismatch["cloud_status"] == "needs_human_review", mismatch

    assert module._resume_row_is_done({"cloud_status": "cloud_confirmed"})
    assert module._resume_row_is_done({"cloud_status": "needs_human_review"})
    assert not module._resume_row_is_done(
        {"cloud_status": "needs_human_review"},
        retry_review_needed=True,
    )
    assert not module._resume_row_is_done({"cloud_status": "cloud_failed"})

    print("cloud gold combiner regression passed")


if __name__ == "__main__":
    main()
