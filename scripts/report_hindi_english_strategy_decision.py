from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = (
    ROOT
    / "eval_runs/goal-stt-optimization-20260703-expanded-v5/current_goal_state/goal_current_state.json"
)
DEFAULT_LONG_SCORECARD = (
    ROOT
    / "eval_runs/goal-stt-optimization-20260703-expanded-v5/local_frontier_segmented_long_top12_confirmed_20260703/scorecard.json"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "eval_runs/goal-stt-optimization-20260703-expanded-v5/hindi_english_strategy_decision"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Hindi+English model-frontier decision from measured artifacts. "
            "This is a product strategy ledger, not a runtime benchmark."
        )
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--long-scorecard", type=Path, default=DEFAULT_LONG_SCORECARD)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    state_path = _resolve(args.state)
    output_dir = _resolve(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    state = _load_json(state_path)
    long_scorecard = _optional_json(_resolve(args.long_scorecard))

    payload = _build_payload(state, state_path, long_scorecard, _resolve(args.long_scorecard))
    (output_dir / "strategy_decision.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "strategy_decision.md").write_text(_markdown(payload), encoding="utf-8")

    print(_console(payload))
    print(f"wrote {output_dir / 'strategy_decision.md'}")


def _build_payload(
    state: dict[str, Any],
    state_path: Path,
    long_scorecard: dict[str, Any] | None,
    long_scorecard_path: Path,
) -> dict[str, Any]:
    split = {
        (str(row.get("backend")), str(row.get("bucket"))): row
        for row in state.get("split_metrics", [])
    }
    english_fast = split.get(("policy_fast_only", "english_only"), {})
    hindi_fast = split.get(("policy_fast_only", "hindi_english"), {})
    hindi_staged = split.get(("policy_safety_all", "hindi_english"), {})
    english_staged = split.get(("policy_safety_all", "english_only"), {})
    english_oriserve = split.get(("policy_oriserve_only", "english_only"), {})
    hindi_oriserve = split.get(("policy_oriserve_only", "hindi_english"), {})

    accelerator = state.get("accelerator", {})
    accelerator_ok = bool(accelerator.get("accelerator_ok"))
    selector = state.get("selector", {})

    payload: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "objective": {
            "product": "local-only English and Hindi+English dictation",
            "english_bar": "Keep English fast path >= 0.90 useful and under 2s p95.",
            "hindi_english_bar": "Preserve Hindi+English meaning within 4-5s polished output.",
            "ux_bar": "Fast first output, async overwrite only when safer/better.",
            "single_model_replacement_bar": (
                "A blind replacement must beat staged Hindi+English useful score, keep p95 <= 4.5s, "
                "keep English useful >= 0.90, and create zero blind English regressions."
            ),
        },
        "inputs": {
            "state": _rel(state_path),
            "long_scorecard": _rel(long_scorecard_path) if long_scorecard else "",
        },
        "current_metrics": {
            "english_fast": _compact_metric(english_fast),
            "english_staged": _compact_metric(english_staged),
            "hindi_fast": _compact_metric(hindi_fast),
            "hindi_staged": _compact_metric(hindi_staged),
            "oriserve_english_only": _compact_metric(english_oriserve),
            "oriserve_hindi_english": _compact_metric(hindi_oriserve),
            "safe_updates": {
                "accepted": selector.get("accepted") or selector.get("accepted_count"),
                "hindi_english": selector.get("hindi_english_accepted"),
                "english_only": selector.get("english_only_accepted"),
                "reject_reasons": selector.get("reject_reasons", {}),
            },
        },
        "long_clip_probe": _long_clip_probe(long_scorecard),
        "strategy_principles": [
            "Search the local model frontier first when Hindi+English quality is the bottleneck.",
            "Treat model and runtime as separate candidates; a good model on CPU is not a product path.",
            "If no single local model clears the bar, ship fast English first plus async multilingual repair.",
            "Use multilingual UX only as honest feedback; it must not block the first usable transcript.",
        ],
        "accelerator": {
            "ok": accelerator_ok,
            "status": accelerator.get("status", "unknown"),
            "path": accelerator.get("path", ""),
            "blocked_runtime_family": [] if accelerator_ok else ["MLX", "MPS", "Metal"],
        },
    }
    payload["candidate_ledger"] = _candidate_ledger(payload)
    payload["decision"] = _decision(payload)
    payload["next_actions"] = _next_actions(payload)
    return payload


def _candidate_ledger(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = payload["current_metrics"]
    accelerator_ok = bool(payload["accelerator"]["ok"])

    return [
        {
            "candidate": "Fast first output",
            "model": "whisper.cpp small translate server",
            "runtime": "resident local whisper.cpp server",
            "device_path": "local server; current server is healthy",
            "local_availability": "installed and measured",
            "quality": {
                "english_useful": metrics["english_fast"].get("useful"),
                "hindi_english_useful": metrics["hindi_fast"].get("useful"),
            },
            "latency": {
                "english_p95_seconds": metrics["english_fast"].get("p95_seconds"),
                "hindi_english_p95_seconds": metrics["hindi_fast"].get("p95_seconds"),
            },
            "failure_mode": "Fast and good for English, but drops Hindi/Hinglish meaning.",
            "decision": "promote as first surface only",
            "source": "local eval artifacts",
        },
        {
            "candidate": "Current staged product path",
            "model": "fast server + Oriserve Swift GGML repair + safety selector",
            "runtime": "resident whisper.cpp first pass; local GGML repair",
            "device_path": "local-only",
            "local_availability": "installed and measured",
            "quality": {
                "english_useful": metrics["english_staged"].get("useful"),
                "hindi_english_useful": metrics["hindi_staged"].get("useful"),
            },
            "latency": {
                "english_p95_seconds": metrics["english_staged"].get("p95_seconds"),
                "hindi_english_p95_seconds": metrics["hindi_staged"].get("p95_seconds"),
            },
            "failure_mode": "Meets current bar, but still has misses and only 14 Hindi+English rows.",
            "decision": "current default architecture",
            "source": "local eval artifacts",
        },
        {
            "candidate": "Blind Oriserve replacement",
            "model": "Oriserve/Whisper-Hindi2Hinglish-Swift",
            "runtime": "GGML/whisper.cpp-style local repair",
            "device_path": "local-only",
            "local_availability": "installed and measured",
            "quality": {
                "english_useful": metrics["oriserve_english_only"].get("useful"),
                "hindi_english_useful": metrics["oriserve_hindi_english"].get("useful"),
            },
            "latency": {
                "english_p95_seconds": metrics["oriserve_english_only"].get("p95_seconds"),
                "hindi_english_p95_seconds": metrics["oriserve_hindi_english"].get("p95_seconds"),
            },
            "failure_mode": "Hurts clean English and can hallucinate/warp terms.",
            "decision": "reject as blind default; keep as guarded repair",
            "source": "https://huggingface.co/Oriserve/Whisper-Hindi2Hinglish-Swift",
        },
        {
            "candidate": "Srota/Qwen3 Hinglish",
            "model": "moorlee/qwen3-asr-0.6b-hinglish variants",
            "runtime": "MLX Qwen3 ASR",
            "device_path": "MLX/Metal required",
            "local_availability": "installed path exists; current accelerator gate blocks live judgement",
            "quality": {
                "prior_public50_useful": 0.835,
                "model_card_hiacc_wer": "15.85%",
                "model_card_openslr104_wer": "35.06%",
            },
            "latency": {"prior_public50_p95_seconds": 3.894},
            "failure_mode": (
                "Most relevant public Hinglish candidate, but cannot be promoted/rejected today "
                "because MLX/Metal is unhealthy on this Mac."
            ),
            "decision": "block until accelerator recovery, then retest on union38",
            "source": "https://huggingface.co/moorlee/qwen3-asr-0.6b-hinglish",
        },
        {
            "candidate": "Base Qwen3-ASR MLX",
            "model": "Qwen/Qwen3-ASR-0.6B and 1.7B",
            "runtime": "mlx-qwen3-asr or mlx-audio",
            "device_path": "MLX/Metal required",
            "local_availability": "downloadable; frontier script prepared; current accelerator gate blocks judgement",
            "quality": {
                "public_english_quality": "strong",
                "public_hindi_quality": "weaker than best multilingual lanes",
            },
            "latency": {
                "public_0_6b_10s_m4pro_fp16_seconds": 0.83,
                "public_1_7b_multilingual_mean_seconds": 4.12,
            },
            "failure_mode": "Generic ASR may not preserve natural Hindi+English code-switching as well as Srota.",
            "decision": "test after Metal recovery; do not assume Hinglish quality from English speed",
            "source": "https://github.com/moona3k/mlx-qwen3-asr",
        },
        {
            "candidate": "Oriserve Apex",
            "model": "Oriserve/Whisper-Hindi2Hinglish-Apex",
            "runtime": "MLX or Metal-required; CPU probe too slow",
            "device_path": "accelerated path needed",
            "local_availability": "model artifact prepared; accelerated judgement blocked",
            "quality": {"cpu_probe_useful": 0.711},
            "latency": {"cpu_probe_p95_seconds": 9.695},
            "failure_mode": "CPU is too slow; accelerated path blocked by Metal.",
            "decision": "block until accelerator recovery, then retest",
            "source": "https://huggingface.co/Oriserve/Whisper-Hindi2Hinglish-Apex",
        },
        {
            "candidate": "Trelis Hinglish Preview",
            "model": "Trelis/whisper-hinglish-preview",
            "runtime": "Transformers/Whisper large-v3 class unless converted",
            "device_path": "likely needs accelerated runtime",
            "local_availability": "downloadable; not cached/tested locally",
            "quality": {"public_model_card_claim": "strong code-switch, Hindi, English"},
            "latency": {},
            "failure_mode": "Large/heavy; no local same-WAV result yet.",
            "decision": "defer as quality-mode candidate, not launch default",
            "source": "https://huggingface.co/Trelis/whisper-hinglish-preview",
        },
        {
            "candidate": "Shunya Zero STT Hinglish",
            "model": "shunyalabs/zero-stt-hinglish",
            "runtime": "Whisper-medium class",
            "device_path": "needs accelerated path",
            "local_availability": "public model; local CPU probe too slow",
            "quality": {"short_cpu_probe_useful": 0.5},
            "latency": {"short_cpu_probe_seconds": 68.978},
            "failure_mode": "CPU path unusable for product latency.",
            "decision": "reject CPU path; revisit only with viable acceleration",
            "source": "https://huggingface.co/shunyalabs/zero-stt-hinglish",
        },
        {
            "candidate": "Hindi Conformer CTC",
            "model": "NVIDIA/NeMo STT Hi Conformer CTC",
            "runtime": "NeMo/ONNX possible",
            "device_path": "not proven on Apple Silicon product path",
            "local_availability": "public model; not currently product-integrated",
            "quality": {},
            "latency": {},
            "failure_mode": "Hindi/Devanagari-first, not proven urban Hindi+English meaning path.",
            "decision": "research candidate, not default",
            "source": "https://catalog.ngc.nvidia.com/orgs/nvidia/nemo/models/stt_hi_conformer_ctc_medium",
        },
        {
            "candidate": "AI4Bharat IndicConformer",
            "model": "AI4Bharat IndicConformer Hindi / multilingual",
            "runtime": "NeMo or converted ONNX if available",
            "device_path": "not proven on this Mac",
            "local_availability": "public project; not product-integrated",
            "quality": {"public_positioning": "Indic/Hindi ASR, not Hinglish-work-dictation specific"},
            "latency": {},
            "failure_mode": "Likely useful for Hindi transcript mode, but not proven for urban Hindi+English with English work terms.",
            "decision": "defer unless Srota/Apex fail after accelerator recovery",
            "source": "https://github.com/AI4Bharat/IndicConformerASR",
        },
        {
            "candidate": "Accelerated frontier run",
            "model": "Srota/Qwen3/Apex MLX family",
            "runtime": "MLX/MPS/Metal",
            "device_path": "blocked" if not accelerator_ok else "available",
            "local_availability": "script ready",
            "quality": {},
            "latency": {},
            "failure_mode": "MTLCompilerService failure" if not accelerator_ok else "",
            "decision": "run now" if accelerator_ok else "do not run until Metal recovers",
            "source": "scripts/run_accelerated_frontier_after_metal_recovery.py",
        },
    ]


def _decision(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload["current_metrics"]
    english_ok = _gte(metrics["english_staged"].get("useful"), 0.90)
    hindi_ok = _gte(metrics["hindi_staged"].get("useful"), 0.85)
    hindi_latency_ok = _lte(metrics["hindi_staged"].get("p95_seconds"), 4.5)
    no_english_overwrite = (metrics["safe_updates"].get("english_only") or 0) == 0
    accelerator_ok = bool(payload["accelerator"]["ok"])
    return {
        "conclusion": (
            "Use staged product route now; do not search for a blind single-model default "
            "until accelerated candidates can be measured."
        ),
        "english_ok": english_ok,
        "hindi_english_quality_ok": hindi_ok,
        "hindi_english_latency_ok": hindi_latency_ok,
        "no_blind_english_overwrite": no_english_overwrite,
        "accelerated_frontier_ready": accelerator_ok,
        "bottleneck": "quality/routing, not latency",
        "ship_architecture": [
            "fast local English/meaning first output",
            "mixed-language detected state in the HUD",
            "local async Hindi/Hinglish repair",
            "overwrite only if safety selector accepts",
            "copy/update toast if target text is gone or paste target is missing",
        ],
        "single_model_replacement": (
            "not justified" if not accelerator_ok else "unproven until accelerated same-WAV bakeoff completes"
        ),
    }


def _next_actions(payload: dict[str, Any]) -> list[str]:
    if payload["accelerator"]["ok"]:
        return [
            "Run `.venv/bin/python scripts/run_accelerated_frontier_after_metal_recovery.py`.",
            "Promote only if Hindi+English useful beats 0.850 and p95 stays under 4.5s.",
            "Keep English-only overwrite count at zero.",
        ]
    return [
        "Do not run long MLX/MPS bakeoffs while Metal is unhealthy.",
        "Keep current staged Oriserve repair path for Hindi+English.",
        "Improve selector/learning only with same-WAV eval proof; do not broaden hidden lexicons.",
        "After reboot/Metal recovery, run `.venv/bin/python scripts/run_accelerated_frontier_after_metal_recovery.py`.",
    ]


def _long_clip_probe(scorecard: dict[str, Any] | None) -> dict[str, Any]:
    if not scorecard:
        return {}
    return {
        str(row.get("backend")): {
            "clips": row.get("clips"),
            "useful": row.get("avg_useful_score"),
            "p95_seconds": row.get("p95_seconds"),
        }
        for row in scorecard.get("summary", [])
    }


def _compact_metric(row: dict[str, Any]) -> dict[str, Any]:
    keys = ("clips", "useful", "coverage", "term_coverage", "p50_seconds", "p95_seconds", "usable_rate", "fast_rate")
    return {key: row.get(key) for key in keys if key in row}


def _markdown(payload: dict[str, Any]) -> str:
    decision = payload["decision"]
    metrics = payload["current_metrics"]
    lines = [
        "# Hindi+English Strategy Decision",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        "## Conclusion",
        "",
        decision["conclusion"],
        "",
        f"- Bottleneck: `{decision['bottleneck']}`",
        f"- English OK: `{decision['english_ok']}`",
        f"- Hindi+English quality OK: `{decision['hindi_english_quality_ok']}`",
        f"- Hindi+English latency OK: `{decision['hindi_english_latency_ok']}`",
        f"- No blind English overwrites: `{decision['no_blind_english_overwrite']}`",
        f"- Accelerated frontier ready: `{decision['accelerated_frontier_ready']}`",
        f"- Single-model replacement: `{decision['single_model_replacement']}`",
        "",
        "## Strategy Principles",
        "",
        *[f"- {item}" for item in payload["strategy_principles"]],
        "",
        "## Current Metrics",
        "",
        "| Path | English useful | English p95 | Hindi+English useful | Hindi+English p95 |",
        "| --- | ---: | ---: | ---: | ---: |",
        (
            "| Fast only | "
            f"{_fmt(metrics['english_fast'].get('useful'))} | "
            f"{_fmt_s(metrics['english_fast'].get('p95_seconds'))} | "
            f"{_fmt(metrics['hindi_fast'].get('useful'))} | "
            f"{_fmt_s(metrics['hindi_fast'].get('p95_seconds'))} |"
        ),
        (
            "| Staged product | "
            f"{_fmt(metrics['english_staged'].get('useful'))} | "
            f"{_fmt_s(metrics['english_staged'].get('p95_seconds'))} | "
            f"{_fmt(metrics['hindi_staged'].get('useful'))} | "
            f"{_fmt_s(metrics['hindi_staged'].get('p95_seconds'))} |"
        ),
        (
            "| Oriserve only | "
            f"{_fmt(metrics['oriserve_english_only'].get('useful'))} | "
            f"{_fmt_s(metrics['oriserve_english_only'].get('p95_seconds'))} | "
            f"{_fmt(metrics['oriserve_hindi_english'].get('useful'))} | "
            f"{_fmt_s(metrics['oriserve_hindi_english'].get('p95_seconds'))} |"
        ),
        "",
        "Safe updates:",
        "",
        f"- accepted: `{metrics['safe_updates'].get('accepted')}`",
        f"- Hindi+English: `{metrics['safe_updates'].get('hindi_english')}`",
        f"- English-only: `{metrics['safe_updates'].get('english_only')}`",
        f"- reject reasons: `{metrics['safe_updates'].get('reject_reasons')}`",
        "",
        "## Candidate Ledger",
        "",
        "| Candidate | Runtime | Device | Quality/latency read | Decision |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in payload["candidate_ledger"]:
        read = _candidate_read(item)
        lines.append(
            "| "
            + " | ".join(
                [
                    _esc(str(item["candidate"])),
                    _esc(str(item["runtime"])),
                    _esc(str(item["device_path"])),
                    _esc(read),
                    _esc(str(item["decision"])),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Ship Architecture",
            "",
            *[f"- {step}" for step in decision["ship_architecture"]],
            "",
            "## Next Actions",
            "",
            *[f"- {step}" for step in payload["next_actions"]],
            "",
            "## Inputs",
            "",
            f"- state: `{payload['inputs']['state']}`",
            f"- long scorecard: `{payload['inputs']['long_scorecard']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _candidate_read(item: dict[str, Any]) -> str:
    parts = []
    quality = item.get("quality") or {}
    latency = item.get("latency") or {}
    if quality:
        parts.append("quality " + ", ".join(f"{key}={value}" for key, value in quality.items()))
    if latency:
        parts.append("latency " + ", ".join(f"{key}={value}" for key, value in latency.items()))
    if item.get("failure_mode"):
        parts.append(str(item["failure_mode"]))
    return "; ".join(parts) or "not measured"


def _console(payload: dict[str, Any]) -> str:
    decision = payload["decision"]
    metrics = payload["current_metrics"]
    return "\n".join(
        [
            "Hindi+English strategy decision",
            f"- decision: {decision['conclusion']}",
            f"- english staged: useful={metrics['english_staged'].get('useful')} p95={metrics['english_staged'].get('p95_seconds')}s",
            f"- hindi+english staged: useful={metrics['hindi_staged'].get('useful')} p95={metrics['hindi_staged'].get('p95_seconds')}s",
            f"- accelerated frontier ready: {decision['accelerated_frontier_ready']}",
            f"- next: {payload['next_actions'][0]}",
        ]
    )


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"missing required artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _gte(value: Any, threshold: float) -> bool:
    try:
        return float(value) >= threshold
    except (TypeError, ValueError):
        return False


def _lte(value: Any, threshold: float) -> bool:
    try:
        return float(value) <= threshold
    except (TypeError, ValueError):
        return False


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_s(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.3f}s"
    except (TypeError, ValueError):
        return str(value)


def _esc(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    main()
