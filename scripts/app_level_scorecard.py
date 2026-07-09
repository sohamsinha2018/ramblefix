from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

from ramblefix.eval import meaning_coverage, term_coverage_report
from product_scorecard import has_critical_semantic_error, latency_score


REQUIRED_RESULT_FIELDS = {"tool", "tool_version", "capture_method", "actual", "timestamps", "cloud_disabled"}
REQUIRED_TIMESTAMPS = {"hotkey_down", "hotkey_up", "paste_done"}
LOCAL_CAPTURE_METHODS = {"file_api", "virtual_mic", "manual_virtual_mic", "hotkey_live"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default="eval_corpus/app_hinglish_adversarial_20260612.json")
    parser.add_argument("--runs", required=True, help="JSONL app-level result rows")
    parser.add_argument("--output", default="")
    parser.add_argument("--allow-draft", action="store_true", help="Allow non-gold reference rows. Output is non-claim-grade.")
    parser.add_argument("--require-full-coverage", action="store_true")
    args = parser.parse_args()

    corpus_rows = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    corpus = build_corpus_index(corpus_rows)
    run_rows = read_jsonl(args.runs)
    validate_run_set(run_rows, corpus, allow_draft=args.allow_draft, require_full_coverage=args.require_full_coverage)
    rows = [score_run(row, corpus, allow_draft=args.allow_draft) for row in run_rows]
    payload = {"claim_grade": not args.allow_draft, "summary": summarize(rows), "trap_summary": summarize_by_trap(rows), "rows": rows}
    text = markdown(payload)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        out.with_suffix(".json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        print(text)


def build_corpus_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    corpus: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = str(row.get("id") or "")
        if not row_id:
            raise ValueError("corpus row missing id")
        if row_id in corpus:
            raise ValueError(f"duplicate corpus id: {row_id}")
        if not str(row.get("gold") or "").strip():
            raise ValueError(f"corpus row missing gold: {row_id}")
        if not row.get("critical_facts"):
            raise ValueError(f"corpus row missing critical_facts: {row_id}")
        if polarity_guard_required(row) and not row.get("forbidden_assertions") and not row.get("semantic_checks"):
            raise ValueError(f"polarity row missing forbidden_assertions or semantic_checks: {row_id}")
        corpus[row_id] = row
    return corpus


def polarity_guard_required(row: dict[str, Any]) -> bool:
    text = normalize_text(
        " ".join(
            [
                str(row.get("gold") or ""),
                str(row.get("script") or ""),
                " ".join(str(fact) for fact in row.get("critical_facts", [])),
                " ".join(str(trap) for trap in row.get("failure_traps", [])),
            ]
        )
    )
    padded = f" {text} "
    markers = [
        " do not ",
        " not ",
        " no ",
        " never ",
        " without ",
        " avoid ",
        " skip ",
        " disabled ",
        " mat ",
        " nahi ",
        " nahin ",
    ]
    return any(marker in padded for marker in markers)


def validate_run_set(
    rows: list[dict[str, Any]],
    corpus: dict[str, dict[str, Any]],
    *,
    allow_draft: bool,
    require_full_coverage: bool,
) -> None:
    if not rows:
        raise ValueError("no app result rows")
    seen: set[tuple[str, str]] = set()
    for row in rows:
        missing = sorted(field for field in REQUIRED_RESULT_FIELDS if field not in row)
        if missing:
            raise ValueError(f"result row missing required fields {missing}: {row.get('corpus_id') or row.get('id')}")
        corpus_id = str(row.get("corpus_id") or "")
        if corpus_id not in corpus:
            raise ValueError(f"unknown corpus_id: {corpus_id}")
        item = corpus[corpus_id]
        if item.get("reference_level") != "gold" and not allow_draft:
            raise ValueError(f"{corpus_id} is {item.get('reference_level')}; pass --allow-draft for non-claim-grade scoring")
        if row.get("cloud_disabled") is not True:
            raise ValueError(f"{corpus_id} / {row.get('tool')} is not marked cloud_disabled=true")
        capture_method = str(row.get("capture_method") or "")
        if capture_method not in LOCAL_CAPTURE_METHODS:
            raise ValueError(f"{corpus_id} / {row.get('tool')} has unsupported capture_method={capture_method!r}")
        validate_timestamps(row)
        key = (str(row.get("tool")), corpus_id)
        if key in seen:
            raise ValueError(f"duplicate result for tool/corpus_id: {key[0]} {key[1]}")
        seen.add(key)
    if require_full_coverage:
        covered = {str(row.get("corpus_id")) for row in rows}
        missing_ids = sorted(set(corpus) - covered)
        if missing_ids:
            raise ValueError(f"missing result rows for corpus ids: {', '.join(missing_ids[:10])}")


def validate_timestamps(row: dict[str, Any]) -> None:
    timestamps = row.get("timestamps")
    if not isinstance(timestamps, dict):
        raise ValueError(f"timestamps must be an object: {row.get('corpus_id')}")
    missing = sorted(REQUIRED_TIMESTAMPS - set(timestamps))
    if missing:
        raise ValueError(f"timestamps missing {missing}: {row.get('corpus_id')}")
    ordered_names = ["hotkey_down", "playback_start", "speech_end", "hotkey_up", "final_visible", "paste_done"]
    previous: float | None = None
    previous_name = ""
    for name in ordered_names:
        if name not in timestamps:
            continue
        try:
            value = float(timestamps[name])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"timestamp {name} must be numeric: {row.get('corpus_id')}") from exc
        if previous is not None and value < previous:
            raise ValueError(f"timestamps out of order for {row.get('corpus_id')}: {name} < {previous_name}")
        previous = value
        previous_name = name


def score_run(row: dict[str, Any], corpus: dict[str, dict[str, Any]], *, allow_draft: bool) -> dict[str, Any]:
    corpus_id = str(row["corpus_id"])
    item = corpus[corpus_id]
    actual = str(row["actual"])
    gold = str(item["gold"])
    critical_facts = [str(fact) for fact in item.get("critical_facts", []) if str(fact).strip()]
    fact_scores = [critical_fact_coverage(fact, actual) for fact in critical_facts]
    fact_coverage = statistics.mean(fact_scores)
    term_report = adjusted_term_coverage_report(gold, actual, item)
    term_coverage = term_report["coverage"]
    term_score = float(term_coverage) if term_coverage is not None else fact_coverage
    seconds = release_to_paste_seconds(row)
    speed = latency_score(seconds)
    reliability = 0.0 if row.get("error") else 1.0
    human = row.get("human_pasteable")
    human_score = 1.0 if human is True else 0.0 if human is False else 0.5
    critical_error = has_critical_semantic_error(row) or semantic_trap_failed(actual, item)
    term_miss_gate = bool(item.get("critical_terms")) and bool(term_report["misses"])
    if term_miss_gate:
        critical_error = True
    score = 0.40 * fact_coverage + 0.20 * term_score + 0.15 * human_score + 0.15 * speed + 0.10 * reliability
    if critical_error:
        score = min(score, 0.50)
    out = dict(row)
    out.update(
        {
            "corpus_id": corpus_id,
            "reference_level": item.get("reference_level"),
            "claim_grade": item.get("reference_level") == "gold" and not allow_draft,
            "gold": gold,
            "failure_traps": item.get("failure_traps", []),
            "critical_terms": item.get("critical_terms", []),
            "critical_facts": critical_facts,
            "fact_coverage": round(float(fact_coverage), 3),
            "term_coverage": term_coverage,
            "term_misses": term_report["misses"],
            "release_to_paste_seconds": round(seconds, 3),
            "hotkey_down_to_paste_seconds": round(hotkey_down_to_paste_seconds(row), 3),
            "app_useful_score": round(score, 3),
            "critical_semantic_error": critical_error,
            "usable": score >= 0.75 and not row.get("error") and not critical_error,
        }
    )
    return out


def critical_fact_coverage(fact: str, actual: str) -> float:
    score = meaning_coverage(fact, actual)
    if privacy_cloud_disabled_equivalent(fact, actual):
        return max(score, 1.0)
    if "should not hang" in normalize_text(fact) and stability_requirement_satisfied(actual):
        return max(score, 1.0)
    return score


def adjusted_term_coverage_report(gold: str, actual: str, item: dict[str, Any]) -> dict[str, Any]:
    terms = [str(term) for term in item.get("critical_terms", []) if str(term).strip()]
    if "critical_terms" in item and not terms:
        return {"coverage": None, "misses": []}
    report = term_coverage_report(gold, actual, terms)
    misses = [
        str(miss)
        for miss in report["misses"]
        if not semantic_term_covered(str(miss), actual, item)
    ]
    coverage = None if not terms else (len(terms) - len(misses)) / len(terms)
    return {"coverage": coverage, "misses": misses}


def semantic_term_covered(term: str, actual: str, item: dict[str, Any]) -> bool:
    if normalize_text(term) != "claude":
        return False
    facts = [str(fact) for fact in item.get("critical_facts", [])]
    return any(privacy_cloud_disabled_equivalent(fact, actual) for fact in facts)


def privacy_cloud_disabled_equivalent(fact: str, actual: str) -> bool:
    fact_norm = normalize_text(fact)
    actual_norm = normalize_text(actual)
    if "company data" not in fact_norm and "data" not in fact_norm:
        return False
    if "claude" not in fact_norm and "cloud" not in fact_norm:
        return False
    if "cloud" not in actual_norm:
        return False
    return has_affirmative_cloud_control(actual_norm)


def has_affirmative_cloud_control(normalized_actual: str) -> bool:
    words = normalized_actual.split()
    cloud_indices = [index for index, word in enumerate(words) if word == "cloud"]
    if not cloud_indices:
        return False
    control_terms = {"disabled", "disable", "blocked", "block", "offline", "local", "avoid", "prevent", "prohibit", "off"}
    for index, word in enumerate(words):
        if word not in control_terms:
            continue
        if all(abs(index - cloud_index) > 3 for cloud_index in cloud_indices):
            continue
        if occurrence_is_negated(words, index, 1):
            continue
        return True
    return False


def semantic_trap_failed(actual: str, item: dict[str, Any]) -> bool:
    if semantic_checks_failed(actual, item.get("semantic_checks")):
        return True
    forbidden = [str(value) for value in item.get("forbidden_assertions", []) if str(value).strip()]
    return any(forbidden_assertion_present(actual, value) for value in forbidden)


def forbidden_assertion_present(actual: str, forbidden: str) -> bool:
    normalized = normalize_text(actual)
    value = normalize_text(forbidden)
    if not value or value not in normalized:
        return False
    words = normalized.split()
    phrase_words = value.split()
    return any(
        not occurrence_is_negated(words, index, len(phrase_words))
        for index in phrase_occurrence_starts(words, phrase_words)
    )


def negated_phrase_present(normalized_text: str, normalized_phrase: str) -> bool:
    words = normalized_text.split()
    phrase_words = normalized_phrase.split()
    return any(
        occurrence_is_negated(words, index, len(phrase_words))
        for index in phrase_occurrence_starts(words, phrase_words)
    )


def phrase_occurrence_starts(words: list[str], phrase_words: list[str]) -> list[int]:
    if not phrase_words:
        return []
    phrase_len = len(phrase_words)
    return [
        index
        for index in range(0, len(words) - phrase_len + 1)
        if words[index : index + phrase_len] == phrase_words
    ]


def occurrence_is_negated(words: list[str], index: int, phrase_len: int) -> bool:
    before = words[max(0, index - 6) : index]
    after = words[index + phrase_len : index + phrase_len + 25]
    before = strip_trailing_negation_adverbs(before)
    if negated_reporting_before_phrase(before):
        return True
    if local_prefix_has_negation(words, index):
        return True
    if len(before) >= 2 and before[-2:] in (["no", "longer"], ["no", "more"]):
        return True
    if before[-1:] in (["not"], ["no"], ["never"], ["without"], ["mat"], ["nahi"], ["nahin"]):
        return True
    if len(before) >= 2 and before[-2:] in (
        ["do", "not"],
        ["does", "not"],
        ["did", "not"],
        ["should", "not"],
        ["must", "not"],
        ["can", "not"],
        ["could", "not"],
        ["will", "not"],
        ["would", "not"],
        ["is", "not"],
        ["are", "not"],
        ["was", "not"],
        ["were", "not"],
        ["not", "a"],
        ["not", "an"],
        ["not", "the"],
        ["not", "to"],
        ["not", "be"],
        ["not", "get"],
    ):
        return True
    if len(before) >= 4 and before[-1:] == ["to"] and before[-4:-2] in (["do", "not"], ["does", "not"], ["did", "not"]):
        return True
    return after_phrase_negates_assertion(after)


def after_phrase_negates_assertion(after: list[str]) -> bool:
    if len(after) < 3 or after[:2] not in (["is", "not"], ["was", "not"], ["are", "not"]):
        return False
    safe_predicates = {"date", "default", "issue", "problem", "case", "cause", "reason"}
    tail = after[2:]
    if tail[0] in safe_predicates:
        return True
    if len(tail) >= 2 and tail[0] in {"a", "an", "the"} and tail[1] in safe_predicates:
        return True
    return False


def negated_reporting_before_phrase(words: list[str]) -> bool:
    reporters = {"call", "say", "label", "describe", "mark", "treat"}
    references = {"it", "this", "that", "them", "as"}
    articles = {"a", "an", "the", "as", "is"}
    for offset, word in enumerate(words):
        if word != "not" or offset + 1 >= len(words):
            continue
        tail = words[offset + 1 :]
        while tail and tail[0] in NEGATION_ADVERBS:
            tail = tail[1:]
        if not tail:
            continue
        if (
            2 <= len(tail) <= 5
            and tail[0] in reporters
            and any(token in articles for token in tail[1:])
            and all(token in reporters | references | articles for token in tail)
        ):
            return True
    return False


def strip_trailing_negation_adverbs(words: list[str]) -> list[str]:
    trimmed = list(words)
    while trimmed and trimmed[-1] in NEGATION_ADVERBS:
        trimmed.pop()
    return trimmed


NEGATION_ADVERBS = {"ever", "really", "actually", "just", "please"}


def semantic_checks_failed(actual: str, checks: object) -> bool:
    if not isinstance(checks, list):
        return False
    for check in checks:
        if not isinstance(check, dict):
            continue
        kind = str(check.get("type") or "")
        if kind == "ordered_values" and ordered_values_failed(actual, str(check.get("from") or ""), str(check.get("to") or "")):
            return True
        if kind == "ordered_terms" and ordered_terms_failed(actual, str(check.get("first") or ""), str(check.get("second") or "")):
            return True
        if kind == "required_and_forbidden" and required_forbidden_failed(actual, check.get("required"), check.get("forbidden")):
            return True
        if kind == "stability_failure_possible" and stability_requirement_failed(actual):
            return True
    return False


def stability_requirement_failed(actual: str) -> bool:
    return stability_failure_possible(actual) or not stability_requirement_satisfied(actual)


def stability_requirement_satisfied(actual: str) -> bool:
    words = normalize_text(actual).split()
    return direct_stability_no_failure_assertion(words) or valid_stability_control_present(words)


def valid_stability_control_present(words: list[str]) -> bool:
    for phrase in STABILITY_FAILURE_PHRASES:
        phrase_words = phrase.split()
        for index in phrase_occurrence_starts(words, phrase_words):
            if stability_prevention_relation_context(words, index, len(phrase_words)):
                return True
    for index, word in enumerate(words):
        if word not in CONTROL_NOUNS:
            continue
        before = words[max(0, index - 3) : index]
        after = words[index + 1 : index + 4]
        after_clause = words[index + 1 : index + 25]
        if control_window_failed(before, after_clause):
            continue
        if (
            control_targets_local_model(words, index)
            and any(token in STABILITY_CONTROL_ANCHORS for token in before + after)
            and any(token in CONTROL_SUCCESS_MARKERS for token in before + after)
        ):
            return True
    return False


def stability_prevention_relation_context(words: list[str], index: int, phrase_len: int) -> bool:
    wide_before = words[max(0, index - 8) : index]
    before = words[max(0, index - 3) : index]
    control_before = {"prevent", "prevents", "preventing", "avoid", "avoids", "avoiding", "guard", "guards", "disable", "disables"}
    preventers = {"prevent", "prevents", "preventing", "guard", "guards"}
    if before and before[-1] in control_before:
        return local_model_before(words, index)
    if local_model_before(words, index) and any(token in preventers for token in wide_before):
        for offset in range(len(wide_before) - 1, -1, -1):
            if wide_before[offset] not in preventers:
                continue
            prefix = wide_before[max(0, offset - 3) : offset]
            suffix = wide_before[offset:index]
            if any(token in CONTROL_FAILURE_MARKERS for token in prefix + suffix):
                return False
            return True
    if before and before[-1] == "from":
        for offset in range(len(wide_before) - 1, -1, -1):
            if wide_before[offset] not in preventers:
                continue
            prefix = wide_before[max(0, offset - 3) : offset]
            suffix = wide_before[offset:index]
            if any(token in {"not", "failed", "fails", "absent", "missing"} for token in prefix + suffix):
                return False
            return local_model_before_from(wide_before)
    return False


STABILITY_FAILURE_PHRASES = [
    "hang",
    "hangs",
    "hanging",
    "freeze",
    "freezes",
    "freezing",
    "stall",
    "stalls",
    "stalling",
    "stuck",
    "unresponsive",
    "lock up",
    "lockup",
    "lockups",
    "locks up",
    "locked up",
    "time out",
    "times out",
    "timed out",
    "timeout",
    "timeouts",
    "crash",
    "crashes",
    "crashing",
    "deadlock",
    "deadlocks",
    "deadlocked",
    "stop responding",
    "stops responding",
    "stopped responding",
    "not respond",
    "no response",
    "nonresponsive",
]


STABILITY_CONTROL_ANCHORS = {
    "hang",
    "hangs",
    "hanging",
    "freeze",
    "freezes",
    "freezing",
    "stall",
    "stalls",
    "stalling",
    "stuck",
    "unresponsive",
    "lockup",
    "lockups",
    "timeout",
    "timeouts",
    "crash",
    "crashes",
    "crashing",
    "deadlock",
    "deadlocked",
}


CONTROL_SUCCESS_MARKERS = {"active", "enabled", "on", "working", "effective", "present", "available", "configured"}


def stability_failure_possible(actual: str) -> bool:
    normalized = normalize_text(actual)
    words = normalized.split()
    if failed_stability_control_present(words):
        return True
    for phrase in STABILITY_FAILURE_PHRASES:
        phrase_words = phrase.split()
        for index in phrase_occurrence_starts(words, phrase_words):
            if stability_control_context(words, index, len(phrase_words)) or stability_prevention_relation_context(words, index, len(phrase_words)):
                continue
            if not occurrence_is_negated(words, index, len(phrase_words)):
                return True
    return False


CONTROL_NOUNS = {"guard", "guards", "prevention", "protection", "mitigation", "handler", "handlers", "limit", "control", "controls"}
CONTROL_FAILURE_MARKERS = {
    "fail",
    "fails",
    "failed",
    "failure",
    "failing",
    "break",
    "breaks",
    "breaking",
    "breakage",
    "broken",
    "malfunction",
    "malfunctions",
    "faulty",
    "unstable",
    "issue",
    "issues",
    "problem",
    "problems",
    "defective",
    "flaky",
    "degraded",
    "brittle",
    "inoperative",
    "nonfunctional",
    "unusable",
    "busted",
    "disabled",
    "off",
    "down",
    "unreliable",
    "inactive",
    "malfunctioning",
    "unavailable",
    "absent",
    "missing",
    "lack",
    "lacking",
    "lacks",
}


def failed_stability_control_present(words: list[str]) -> bool:
    for index, word in enumerate(words):
        if word not in CONTROL_NOUNS:
            continue
        before = words[max(0, index - 3) : index]
        after = words[index + 1 : index + 25]
        if not_needed_control_exception(before, [word] + after, words):
            continue
        if control_window_failed(before, after):
            return True
    return False


def control_window_failed(before: list[str], after: list[str]) -> bool:
    window = before + after
    if control_failure_marker_present(window):
        return True
    if contains_negated_success_marker(after):
        return True
    return any(
        after[offset : offset + 2]
        in (
            ["can", "fail"],
            ["may", "fail"],
            ["might", "fail"],
            ["could", "fail"],
            ["can", "break"],
            ["may", "break"],
            ["might", "break"],
            ["could", "break"],
            ["will", "break"],
            ["would", "break"],
            ["shall", "break"],
            ["can", "malfunction"],
            ["may", "malfunction"],
            ["might", "malfunction"],
            ["could", "malfunction"],
            ["will", "malfunction"],
            ["would", "malfunction"],
            ["shall", "malfunction"],
            ["not", "working"],
            ["not", "active"],
            ["not", "enabled"],
            ["not", "available"],
            ["not", "configured"],
            ["not", "effective"],
            ["not", "present"],
        )
        for offset in range(len(after) - 1)
    )


def contains_negated_success_marker(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens):
        if token != "not":
            continue
        if any(candidate in CONTROL_SUCCESS_MARKERS for candidate in tokens[index + 1 : index + 6]):
            return True
    return False


def control_failure_marker_present(tokens: list[str]) -> bool:
    soft_markers = {"issue", "issues", "problem", "problems", "failure"}
    for index, token in enumerate(tokens):
        if token not in CONTROL_FAILURE_MARKERS:
            continue
        if token in soft_markers:
            if soft_failure_marker_is_exempted(tokens, index):
                continue
        return True
    return False


SOFT_FAILURE_MODIFIERS = {
    "known",
    "open",
    "current",
    "remaining",
    "stability",
    "active",
    "new",
    "reported",
    "pending",
    "currently",
    "unresolved",
    "outstanding",
    "residual",
}


def soft_failure_marker_is_exempted(tokens: list[str], index: int) -> bool:
    previous_token = tokens[index - 1] if index > 0 else ""
    next_token = tokens[index + 1] if index + 1 < len(tokens) else ""
    if quantified_subject_resolution_failure(tokens, index):
        return False
    if previous_token in {"no", "zero"} and no_zero_marker_is_unnegated(tokens, index - 1) and not local_suffix_has_postposed_negation(tokens, index):
        return True
    if next_token == "free" and free_marker_is_unnegated(tokens, index, index + 1) and not local_suffix_has_postposed_negation(tokens, index + 1):
        return True
    if soft_marker_has_no_zero_modifier(tokens, index):
        return not local_suffix_has_postposed_negation(tokens, index)
    if soft_marker_has_resolution_after(tokens, index):
        return True

    for offset in range(max(0, index - 5), index):
        if tokens[offset] != "without":
            continue
        if local_prefix_has_negation(tokens, offset):
            continue
        between = tokens[offset + 1 : index]
        if not between or all(token in {"any"} | SOFT_FAILURE_MODIFIERS for token in between):
            return not local_suffix_has_postposed_negation(tokens, index)

    for offset in range(max(0, index - 5), index):
        if tokens[offset] != "free":
            continue
        if not free_marker_is_unnegated(tokens, index, offset):
            continue
        between = tokens[offset + 1 : index]
        if not between or all(token in {"of", "from", "any"} | SOFT_FAILURE_MODIFIERS for token in between):
            return not local_suffix_has_postposed_negation(tokens, index)

    return False


def soft_marker_has_no_zero_modifier(tokens: list[str], index: int) -> bool:
    for offset in range(max(0, index - 4), index):
        if tokens[offset] not in {"no", "zero"}:
            continue
        if not no_zero_marker_is_unnegated(tokens, offset):
            continue
        between = tokens[offset + 1 : index]
        if between and all(token in SOFT_FAILURE_MODIFIERS for token in between):
            return not local_suffix_has_postposed_negation(tokens, index)
    return False


def quantified_subject_resolution_failure(tokens: list[str], index: int) -> bool:
    if not quantified_subject_before_marker(tokens, index):
        return False

    resolution_terms = {"fixed", "resolved", "closed", "cleared", "addressed", "solved"}
    after = tokens[index + 1 : index + 6]
    if after and after[0] in resolution_terms:
        return True
    auxiliaries = {"is", "are", "was", "were", "have", "has", "had", "get", "gets", "got", "will", "can", "may", "should", "would", "could"}
    fillers = {
        "be",
        "been",
        "being",
        "getting",
        "gotten",
        "got10",
        "already",
        "currently",
        "still",
        "actively",
        "ever",
        "really",
        "actually",
        "fully",
        "completely",
        "truly",
        "entirely",
        "totally",
    }
    saw_auxiliary = False
    for token in after:
        if token in resolution_terms:
            return saw_auxiliary
        if token in auxiliaries:
            saw_auxiliary = True
            continue
        if saw_auxiliary and token in fillers:
            continue
        return False
    return False


def quantified_subject_before_marker(tokens: list[str], index: int) -> bool:
    for offset in range(max(0, index - 7), index):
        token = tokens[offset]
        between = tokens[offset + 1 : index]
        if token in {"no", "zero"} and all(candidate in SOFT_FAILURE_MODIFIERS for candidate in between):
            return True
        if token == "none" and none_of_subject_bridge(between):
            return True
        if token == "not" and not_single_subject_bridge(between):
            return True
        if token == "no" and no_single_subject_bridge(between):
            return True
    return False


def none_of_subject_bridge(tokens: list[str]) -> bool:
    if not tokens or tokens[0] != "of":
        return False
    rest = tokens[1:]
    while rest and rest[0] in {"the", "these", "those", "this", "that", "any"}:
        rest = rest[1:]
    return all(token in SOFT_FAILURE_MODIFIERS for token in rest)


def not_single_subject_bridge(tokens: list[str]) -> bool:
    rest = list(tokens)
    if rest and rest[0] == "even":
        rest = rest[1:]
    if rest[:1] == ["one"]:
        rest = rest[1:]
    elif rest[:1] == ["single"]:
        rest = rest[1:]
    elif rest[:2] == ["a", "single"]:
        rest = rest[2:]
    else:
        return False
    return all(token in SOFT_FAILURE_MODIFIERS for token in rest)


def no_single_subject_bridge(tokens: list[str]) -> bool:
    if tokens[:1] in (["one"], ["single"]):
        return all(token in SOFT_FAILURE_MODIFIERS for token in tokens[1:])
    return False


def no_zero_marker_is_unnegated(tokens: list[str], index: int) -> bool:
    return not local_prefix_has_negation(tokens, index)


def free_marker_is_unnegated(tokens: list[str], marker_index: int, free_index: int) -> bool:
    return not local_prefix_has_negation(tokens, marker_index) and not local_prefix_has_negation(tokens, free_index)


def local_prefix_has_negation(tokens: list[str], index: int) -> bool:
    fillers = {
        "really",
        "actually",
        "fully",
        "completely",
        "quite",
        "exactly",
        "currently",
        "still",
        "even",
        "truly",
        "entirely",
        "totally",
        "anymore",
    }
    prefix = tokens[max(0, index - 10) : index]
    for offset in range(len(prefix) - 1, -1, -1):
        token = prefix[offset]
        tail = prefix[offset + 1 :]
        if token in {"not", "never"} and all(candidate in fillers for candidate in tail):
            return True
        if token == "no" and offset + 1 < len(prefix) and prefix[offset + 1] in {"longer", "more"}:
            if all(candidate in fillers for candidate in prefix[offset + 2 :]):
                return True
    return False


def local_suffix_has_postposed_negation(tokens: list[str], index: int) -> bool:
    fillers = {
        "really",
        "actually",
        "fully",
        "completely",
        "quite",
        "exactly",
        "currently",
        "still",
        "even",
        "truly",
        "entirely",
        "totally",
    }
    suffix = tokens[index + 1 : index + 8]
    for offset, token in enumerate(suffix):
        if token == "anymore" and all(candidate in fillers for candidate in suffix[:offset]):
            return True
        if token == "no" and offset + 1 < len(suffix) and suffix[offset + 1] in {"longer", "more"}:
            return all(candidate in fillers for candidate in suffix[:offset])
        if token not in fillers:
            return False
    if "anymore" in suffix[:3]:
        return True
    return False


def soft_marker_has_resolution_after(tokens: list[str], index: int) -> bool:
    resolution_terms = {"fixed", "resolved", "closed", "cleared", "addressed", "solved"}
    allowed_fillers = {"was", "were", "is", "are", "been", "being", "has", "have", "had", "got", "gets", "that", "now", "already", "previously"}
    window = tokens[index + 1 : index + 7]
    for offset, token in enumerate(window):
        if token not in resolution_terms:
            continue
        before_resolution = window[:offset]
        if any(candidate in {"not", "never", "no"} for candidate in before_resolution):
            return False
        resolution_index = index + 1 + offset
        if all(candidate in allowed_fillers for candidate in before_resolution) and not local_suffix_has_postposed_negation(tokens, resolution_index):
            return True
    return False


def stability_control_context(words: list[str], index: int, phrase_len: int) -> bool:
    wide_before = words[max(0, index - 8) : index]
    before = words[max(0, index - 3) : index]
    after = words[index + phrase_len : index + phrase_len + 25]
    control_before = {"prevent", "prevents", "preventing", "avoid", "avoids", "avoiding", "guard", "guards", "disable", "disables"}
    preventers = {"prevent", "prevents", "preventing", "guard", "guards"}
    if after and after[0] in CONTROL_NOUNS:
        if not_needed_control_exception(before, after, words):
            return True
        if control_window_failed(before, after[1:]):
            return False
        return True
    if before and before[-1] in control_before:
        return True
    if before and before[-1] == "from":
        for offset in range(len(wide_before) - 1, -1, -1):
            if wide_before[offset] not in preventers:
                continue
            prefix = wide_before[max(0, offset - 3) : offset]
            suffix = wide_before[offset:index]
            if any(token in {"not", "failed", "fails", "absent", "missing"} for token in prefix + suffix):
                return False
            return True
    return False


def not_needed_control_exception(before: list[str], after: list[str], words: list[str]) -> bool:
    if not any(token in {"no", "not", "none"} for token in before):
        return False
    if not after or after[0] not in CONTROL_NOUNS:
        return False
    if not any(token in {"needed", "required", "necessary"} for token in after[1:5]):
        return False
    return direct_stability_no_failure_assertion(words)


def direct_stability_no_failure_assertion(words: list[str]) -> bool:
    for phrase in STABILITY_FAILURE_PHRASES:
        if phrase in {"not respond", "no response"}:
            continue
        phrase_words = phrase.split()
        for index in phrase_occurrence_starts(words, phrase_words):
            if index + len(phrase_words) < len(words) and words[index + len(phrase_words)] in CONTROL_NOUNS:
                continue
            if local_model_before(words, index) and occurrence_is_negated(words, index, len(phrase_words)):
                return True
    for index, word in enumerate(words):
        local_window = words[max(0, index - 4) : index + 5]
        control_window = words[max(0, index - 3) : index + 1]
        if (
            word in {"stable", "reliable", "responsive"}
            and local_model_before(words, index)
            and not any(token in CONTROL_NOUNS for token in control_window)
            and not occurrence_is_negated(words, index, 1)
        ):
            return True
    return False


def control_targets_local_model(words: list[str], control_index: int) -> bool:
    before = words[max(0, control_index - 5) : control_index]
    if has_local_model_phrase(before):
        return True
    after = words[control_index + 1 : control_index + 9]
    for index, token in enumerate(after):
        if token not in {"for", "on"}:
            continue
        tail = after[index + 1 :]
        if tail[:2] == ["local", "model"] or tail[:3] == ["the", "local", "model"]:
            return True
    return False


def local_model_before(words: list[str], index: int, window: int = 8) -> bool:
    return has_local_model_phrase(words[max(0, index - window) : index])


def local_model_before_from(words_before_failure: list[str]) -> bool:
    if not words_before_failure or words_before_failure[-1] != "from":
        return False
    prefix = words_before_failure[:-1]
    return prefix[-2:] == ["local", "model"] or prefix[-3:] == ["the", "local", "model"]


def has_local_model_phrase(words: list[str]) -> bool:
    for index, (first, second) in enumerate(zip(words, words[1:])):
        if first != "local" or second != "model":
            continue
        prefix = words[max(0, index - 2) : index]
        if prefix[-1:] in (["not"], ["no"], ["without"], ["never"]):
            continue
        if len(prefix) >= 2 and prefix[-2] in {"not", "no", "without", "never"} and prefix[-1] in {"a", "an", "the"}:
            continue
        return True
    return False


def ordered_values_failed(actual: str, from_value: str, to_value: str) -> bool:
    normalized = normalize_text(actual)
    from_norm = normalize_text(from_value)
    to_norm = normalize_text(to_value)
    if from_norm not in normalized or to_norm not in normalized:
        return True
    correct_patterns = [
        f"from {from_norm} to {to_norm}",
        f"{from_norm} to {to_norm}",
    ]
    reversed_patterns = [
        f"from {to_norm} to {from_norm}",
        f"{to_norm} to {from_norm}",
    ]
    if any(pattern in normalized for pattern in reversed_patterns):
        return True
    return not any(pattern in normalized for pattern in correct_patterns)


def ordered_terms_failed(actual: str, first: str, second: str) -> bool:
    normalized = normalize_text(actual)
    first_norm = normalize_text(first)
    second_norm = normalize_text(second)
    if first_norm not in normalized or second_norm not in normalized:
        return True
    if f"{second_norm} before {first_norm}" in normalized or f"{first_norm} after {second_norm}" in normalized:
        return True
    return False


def required_forbidden_failed(actual: str, required: object, forbidden: object) -> bool:
    normalized = normalize_text(actual)
    required_values = [normalize_text(str(value)) for value in required if str(value).strip()] if isinstance(required, list) else []
    forbidden_values = [normalize_text(str(value)) for value in forbidden if str(value).strip()] if isinstance(forbidden, list) else []
    if any(value not in normalized for value in required_values):
        return True
    return any(forbidden_assertion_present(normalized, value) for value in forbidden_values)


def normalize_text(text: str) -> str:
    value = text.lower()
    value = re.sub(r"\bdo\s*n\s*['’]?\s*t\b", "do not", value)
    value = re.sub(r"\bdon\s*['’]?\s*t\b", "do not", value)
    value = re.sub(r"\bdoes\s*n\s*['’]?\s*t\b", "does not", value)
    value = re.sub(r"\bdoesn\s*['’]?\s*t\b", "does not", value)
    value = re.sub(r"\bis\s*n\s*['’]?\s*t\b", "is not", value)
    value = re.sub(r"\bisn\s*['’]?\s*t\b", "is not", value)
    value = re.sub(r"\bwas\s*n\s*['’]?\s*t\b", "was not", value)
    value = re.sub(r"\bwasn\s*['’]?\s*t\b", "was not", value)
    value = re.sub(r"\bare\s*n\s*['’]?\s*t\b", "are not", value)
    value = re.sub(r"\baren\s*['’]?\s*t\b", "are not", value)
    value = re.sub(r"\bwere\s*n\s*['’]?\s*t\b", "were not", value)
    value = re.sub(r"\bweren\s*['’]?\s*t\b", "were not", value)
    value = re.sub(r"\bca\s*n\s*['’]?\s*t\b", "can not", value)
    value = re.sub(r"\bcannot\b", "can not", value)
    value = re.sub(r"\bcould\s*n\s*['’]?\s*t\b", "could not", value)
    value = re.sub(r"\bcouldn\s*['’]?\s*t\b", "could not", value)
    value = re.sub(r"\bwould\s*n\s*['’]?\s*t\b", "would not", value)
    value = re.sub(r"\bwouldn\s*['’]?\s*t\b", "would not", value)
    value = re.sub(r"\bshould\s*n\s*['’]?\s*t\b", "should not", value)
    value = re.sub(r"\bshouldn\s*['’]?\s*t\b", "should not", value)
    value = re.sub(r"\bmust\s*n\s*['’]?\s*t\b", "must not", value)
    value = re.sub(r"\bmustn\s*['’]?\s*t\b", "must not", value)
    value = re.sub(r"\bwon\s*['’]?\s*t\b", "will not", value)
    value = re.sub(r"\bai\s*n\s*['’]?\s*t\b", "is not", value)
    value = re.sub(r"\bain\s*['’]?\s*t\b", "is not", value)
    replacements = {
        "thirty": "30",
        "ten": "10",
        "three": "3",
        "twenty one": "21",
        "twenty four": "24",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def release_to_paste_seconds(row: dict[str, Any]) -> float:
    timestamps = row["timestamps"]
    return float(timestamps["paste_done"]) - float(timestamps["hotkey_up"])


def hotkey_down_to_paste_seconds(row: dict[str, Any]) -> float:
    timestamps = row["timestamps"]
    return float(timestamps["paste_done"]) - float(timestamps["hotkey_down"])


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["tool"]), []).append(row)
    summary = []
    for tool, bucket in sorted(grouped.items()):
        scores = [float(row["app_useful_score"]) for row in bucket]
        latencies = [float(row["release_to_paste_seconds"]) for row in bucket]
        trap_scores = summarize_by_trap(bucket)
        worst_trap = min((float(row["avg_score"]) for row in trap_scores), default=0.0)
        summary.append(
            {
                "tool": tool,
                "clips": len(bucket),
                "avg_score": round(statistics.mean(scores), 3),
                "worst_trap_score": round(worst_trap, 3),
                "usable_rate": round(sum(1 for row in bucket if row["usable"]) / len(bucket), 3),
                "critical_error_rate": round(sum(1 for row in bucket if row["critical_semantic_error"]) / len(bucket), 3),
                "term_miss_rate": round(sum(1 for row in bucket if row.get("term_misses")) / len(bucket), 3),
                "avg_fact_coverage": round(statistics.mean(float(row["fact_coverage"]) for row in bucket), 3),
                "avg_term_coverage": avg_optional([row.get("term_coverage") for row in bucket]),
                "p50_release_to_paste": round(statistics.median(latencies), 3),
                "error_rate": round(sum(1 for row in bucket if row.get("error")) / len(bucket), 3),
            }
        )
    return sorted(summary, key=lambda row: (-float(row["avg_score"]), str(row["tool"])))


def summarize_by_trap(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        traps = row.get("failure_traps") or ["unknown"]
        for trap in traps:
            grouped.setdefault(str(trap), []).append(row)
    summary = []
    for trap, bucket in sorted(grouped.items()):
        summary.append(
            {
                "trap": trap,
                "clips": len(bucket),
                "avg_score": round(statistics.mean(float(row["app_useful_score"]) for row in bucket), 3),
                "usable_rate": round(sum(1 for row in bucket if row["usable"]) / len(bucket), 3),
                "critical_error_rate": round(sum(1 for row in bucket if row["critical_semantic_error"]) / len(bucket), 3),
                "term_miss_rate": round(sum(1 for row in bucket if row.get("term_misses")) / len(bucket), 3),
            }
        )
    return summary


def avg_optional(values: list[Any]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    return round(statistics.mean(numbers), 3) if numbers else None


def markdown(payload: dict[str, Any]) -> str:
    claim_note = "CLAIM-GRADE: gold references only." if payload["claim_grade"] else "DRAFT ONLY: non-gold references allowed; do not use for public superiority claims."
    lines = [
        "# App-Level Dictation Scorecard",
        "",
        claim_note,
        "",
        "This scorecard is for same-WAV app outputs. It is separate from engine-only bakeoffs.",
        "",
        "| Tool | Clips | Avg Score | Worst Trap | Usable Rate | Critical Error | Term Miss | Fact Coverage | Term Coverage | p50 Release-to-Paste | Error Rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["summary"]:
        term = "" if row["avg_term_coverage"] is None else f"{float(row['avg_term_coverage']):.3f}"
        lines.append(
            f"| {row['tool']} | {row['clips']} | {float(row['avg_score']):.3f} | "
            f"{float(row['worst_trap_score']):.3f} | {float(row['usable_rate']):.3f} | "
            f"{float(row['critical_error_rate']):.3f} | {float(row['term_miss_rate']):.3f} | "
            f"{float(row['avg_fact_coverage']):.3f} | {term} | "
            f"{float(row['p50_release_to_paste']):.3f} | {float(row['error_rate']):.3f} |"
        )
    lines.extend(["", "## Trap Summary", "", "| Trap | Clips | Avg Score | Usable Rate | Critical Error | Term Miss |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for row in payload["trap_summary"]:
        lines.append(
            f"| {row['trap']} | {row['clips']} | {float(row['avg_score']):.3f} | "
            f"{float(row['usable_rate']):.3f} | {float(row['critical_error_rate']):.3f} | {float(row['term_miss_rate']):.3f} |"
        )
    lines.extend(["", "## Rows", "", "| Corpus ID | Tool | Score | Fact | Term | Latency | Usable | Misses | Actual |", "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |"])
    for row in payload["rows"]:
        term = "" if row["term_coverage"] is None else f"{float(row['term_coverage']):.3f}"
        misses = ", ".join(str(item) for item in row.get("term_misses", [])).replace("|", "\\|")
        actual = str(row["actual"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {row['corpus_id']} | {row['tool']} | {float(row['app_useful_score']):.3f} | "
            f"{float(row['fact_coverage']):.3f} | {term} | {float(row['release_to_paste_seconds']):.3f} | "
            f"{row['usable']} | {misses} | {actual[:180]} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
