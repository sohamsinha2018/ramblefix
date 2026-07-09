from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ramblefix.eval import meaning_coverage, meaning_loss, repeated_substring_score, term_coverage_report, word_error_rate


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate native-style fast paste plus background Hindi polish.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ids", default="", help="Comma-separated row IDs. Empty means all rows.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--mode", choices=["meaning", "verbatim"], default="meaning")
    parser.add_argument("--first-pass", choices=["python-cli", "native-direct"], default="python-cli")
    parser.add_argument(
        "--polish-mode",
        choices=["detected", "force"],
        default="detected",
        help="detected mirrors default product routing; force measures explicit Hinglish quality mode.",
    )
    parser.add_argument(
        "--audio-risk-max-seconds",
        type=float,
        default=90.0,
        help="In detected mode, run the local Hindi-risk detector only for fast-translate clips up to this duration.",
    )
    args = parser.parse_args()

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    id_filter = {value.strip() for value in args.ids.split(",") if value.strip()}
    items = [item for item in corpus if not id_filter or str(item.get("id")) in id_filter]
    if args.limit > 0:
        items = items[: args.limit]
    if not items:
        raise SystemExit("no corpus rows selected")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    drafts_dir = args.output_dir / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    policy_requests: list[dict[str, str]] = []
    pending_policy: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    for item in items:
        first_payload, first_error, first_wall = _run_first_pass(item, args.timeout_seconds, backend=args.first_pass)
        first_text = str((first_payload or {}).get("text") or "")
        first_row = _scoreable_row(item, first_text, first_wall, "two_phase_first_output", "fast_first", first_payload, first_error)
        rows.append(first_row)

        polish_payload: dict[str, Any] | None = None
        polish_error = ""
        polish_wall = 0.0
        polish_policy = _hindi_polish_policy(
            first_payload or {},
            first_text,
            item,
            args.polish_mode,
            audio_risk_max_seconds=args.audio_risk_max_seconds,
        )
        should_polish = (
            _word_count(first_text) >= 3
            and not first_error
            and polish_policy["should"]
        )
        if should_polish:
            draft_path = drafts_dir / f"{_safe_filename(str(item.get('id') or 'row'))}.txt"
            draft_path.write_text(first_text, encoding="utf-8")
            polish_payload, polish_error, polish_wall = _run_hindi_polish(
                item,
                draft_path,
                args.timeout_seconds,
                force=polish_policy["force"],
            )
            final_text = str((polish_payload or {}).get("text") or "")
            policy_requests.append({"id": str(item.get("id") or ""), "draft": first_text, "final": final_text})
            pending_policy[str(item.get("id") or "")] = (item, first_row, _polish_row(polish_payload, polish_error, polish_wall))
        else:
            rows.append(_final_row_from_first(item, first_row, first_payload, reason="polish_skipped"))
        print(
            f"{item.get('id')} first={first_wall:.3f}s polish={polish_wall:.3f}s "
            f"first='{_short(first_text)}' polish_error='{polish_error[:80]}'",
            flush=True,
        )

    policy_by_id = _run_policy_tool(policy_requests, args.output_dir) if policy_requests else {}
    for row_id, (item, first_row, polish_row) in pending_policy.items():
        policy = policy_by_id.get(row_id) or {"accepted": False, "policyOK": False, "droppedProtectedTerms": []}
        rows.append(_selected_final_row(item, first_row, polish_row, policy))

    _write_outputs(args.output_dir, rows, args.mode)


def _run_first_pass(item: dict[str, Any], timeout_seconds: float, *, backend: str) -> tuple[dict[str, Any] | None, str, float]:
    audio = _audio_path(item)
    if backend == "native-direct":
        cmd = [
            str(ROOT / "native/RambleFixHotkey/.build/release/RambleFixHotkeyASRTool"),
            "--audio",
            str(audio),
            "--timeout",
            str(timeout_seconds),
        ]
        payload, error, wall = _run_json(cmd, timeout_seconds + 5)
        if payload is not None and isinstance(payload.get("seconds"), (int, float)):
            return payload, error, float(payload["seconds"])
        return payload, error, wall
    cmd = [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "ramblefix.cli",
        "dictate-audio",
        str(audio),
        "--json",
        "--no-cleanup",
        "--skip-process-fallback",
    ]
    return _run_json(cmd, timeout_seconds)


def _run_hindi_polish(
    item: dict[str, Any],
    draft_path: Path,
    timeout_seconds: float,
    *,
    force: bool,
) -> tuple[dict[str, Any] | None, str, float]:
    audio = _audio_path(item)
    cmd = [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "ramblefix.cli",
        "hindi-polish-audio",
        str(audio),
        "--draft-file",
        str(draft_path),
        "--json",
    ]
    if force:
        cmd.append("--force")
    return _run_json(
        cmd,
        timeout_seconds,
        env_overrides={
            "RAMBLEFIX_HINDI_POLISH_SERVER_URL": "http://127.0.0.1:8188",
            "RAMBLEFIX_SROTA_SERVER_URL": "http://127.0.0.1:8188",
        },
    )


def _hindi_polish_policy(
    payload: dict[str, Any],
    text: str,
    item: dict[str, Any],
    polish_mode: str,
    *,
    audio_risk_max_seconds: float,
) -> dict[str, bool]:
    if polish_mode == "force":
        return {"should": True, "force": True}
    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
    if quality.get("hindi_risk") is True:
        return {"should": True, "force": True}
    if _has_hindi_signal(text):
        return {"should": True, "force": True}
    if not _is_fast_server_route(str(payload.get("route") or "")):
        return {"should": False, "force": False}
    audio_seconds = _audio_seconds(item, payload)
    should_probe = audio_seconds is None or audio_seconds <= audio_risk_max_seconds
    return {"should": should_probe, "force": False}


def _is_fast_server_route(route: str) -> bool:
    return route.strip().lower() in {
        "fast_server_translate",
        "fast_server_native",
        "fast_server_native_process_fallback_skipped",
    }


def _has_hindi_signal(text: str) -> bool:
    if any(0x0900 <= ord(ch) <= 0x097F for ch in text):
        return True
    tokens = set(re.findall(r"[a-z]+", text.lower()))
    markers = {
        "aap", "agar", "bhai", "haan", "hai", "hain", "hindi", "hinglish",
        "kaise", "kya", "matlab", "nahi", "nahin", "theek", "toh", "yaar", "yeh",
    }
    return bool(tokens.intersection(markers))


def _audio_seconds(item: dict[str, Any], payload: dict[str, Any] | None = None) -> float | None:
    quality = payload.get("quality") if isinstance(payload, dict) and isinstance(payload.get("quality"), dict) else {}
    for key in ("audio_duration_seconds", "audio_seconds", "duration_seconds", "duration"):
        value = quality.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    for source in (item, meta):
        for key in ("audio_seconds", "duration_seconds", "duration"):
            value = source.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    return None


def _run_json(
    cmd: list[str],
    timeout_seconds: float,
    *,
    env_overrides: dict[str, str] | None = None,
) -> tuple[dict[str, Any] | None, str, float]:
    started = time.perf_counter()
    try:
        env = None
        if env_overrides:
            import os

            env = os.environ.copy()
            env.update(env_overrides)
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        wall = round(time.perf_counter() - started, 3)
        if proc.returncode != 0:
            return None, (proc.stderr or proc.stdout).strip(), wall
        return json.loads(proc.stdout), "", wall
    except Exception as exc:  # noqa: BLE001 - eval rows should capture all failures.
        return None, f"{type(exc).__name__}: {exc}", round(time.perf_counter() - started, 3)


def _run_policy_tool(requests: list[dict[str, str]], output_dir: Path) -> dict[str, dict[str, Any]]:
    request_path = output_dir / "policy_requests.json"
    response_path = output_dir / "policy_responses.json"
    request_path.write_text(json.dumps(requests, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    cmd = [
        "swift",
        "run",
        "-c",
        "release",
        "RambleFixHotkeyPolicyTool",
        "--input",
        str(request_path.resolve()),
        "--project-root",
        str(ROOT.resolve()),
        "--policy",
        "audio-risk",
    ]
    proc = subprocess.run(
        cmd,
        cwd=ROOT / "native/RambleFixHotkey",
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    response_path.write_text(proc.stdout, encoding="utf-8")
    responses = json.loads(proc.stdout)
    return {str(row["id"]): row for row in responses}


def _scoreable_row(
    item: dict[str, Any],
    text: str,
    seconds: float,
    backend: str,
    route: str,
    payload: dict[str, Any] | None,
    error: str,
) -> dict[str, Any]:
    gold = str(item.get("gold") or item.get("text") or item.get("reference") or "").strip()
    term_report = term_coverage_report(gold, text, item.get("critical") or item.get("critical_terms") or item.get("terms") or item.get("anchors"))
    return {
        "id": str(item.get("id") or ""),
        "category": str(item.get("category") or item.get("bucket") or "product"),
        "backend": backend,
        "route": route,
        "audio": str(_audio_path(item)),
        "gold": gold,
        "actual": text,
        "wer": word_error_rate(gold, text) if gold else None,
        "meaning_loss": meaning_loss(gold, text) if gold else None,
        "meaning_coverage": meaning_coverage(gold, text) if gold else None,
        "term_coverage": term_report["coverage"],
        "term_hits": term_report["hits"],
        "term_misses": term_report["misses"],
        "term_terms": term_report["terms"],
        "repeat": repeated_substring_score(text),
        "seconds": round(seconds, 3),
        "meta": payload or {},
        "error": error or None,
    }


def _polish_row(payload: dict[str, Any] | None, error: str, wall: float) -> dict[str, Any]:
    return {
        "text": str((payload or {}).get("text") or ""),
        "seconds": round(wall, 3),
        "payload": payload or {},
        "error": error,
    }


def _final_row_from_first(
    item: dict[str, Any],
    first_row: dict[str, Any],
    payload: dict[str, Any] | None,
    *,
    reason: str,
) -> dict[str, Any]:
    row = dict(first_row)
    row["backend"] = "two_phase_final_selected"
    row["route"] = "fast_kept"
    row["meta"] = {"first": payload or {}, "selected": "first", "reason": reason}
    return row


def _selected_final_row(
    item: dict[str, Any],
    first_row: dict[str, Any],
    polish_row: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    accepted = bool(policy.get("accepted")) and not polish_row.get("error")
    text = str(polish_row["text"] if accepted else first_row["actual"])
    seconds = float(first_row["seconds"]) + (float(polish_row["seconds"]) if accepted else 0.0)
    row = _scoreable_row(
        item,
        text,
        seconds,
        "two_phase_final_selected",
        "hindi_polish_replaced" if accepted else "fast_kept",
        {
            "first_seconds": first_row["seconds"],
            "polish_seconds": polish_row["seconds"],
            "policy": policy,
            "polish": polish_row["payload"],
        },
        polish_row.get("error", "") if not accepted else "",
    )
    row["selected_from"] = "polish" if accepted else "first"
    return row


def _write_outputs(output_dir: Path, rows: list[dict[str, Any]], mode: str) -> None:
    (output_dir / "corpus_results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    product_scorecard = _load_product_scorecard()
    scored = [product_scorecard.score_row(row, mode=mode) for row in rows]
    payload = {"mode": mode, "summary": product_scorecard.summarize(scored), "rows": scored}
    (output_dir / "scorecard.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_dir / "scorecard.md").write_text(product_scorecard.markdown(payload), encoding="utf-8")
    (output_dir / "summary.json").write_text(json.dumps(_summary(scored), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(_summary(scored), indent=2, ensure_ascii=False))


def _summary(scored: list[dict[str, Any]]) -> dict[str, Any]:
    by_backend: dict[str, list[dict[str, Any]]] = {}
    for row in scored:
        by_backend.setdefault(str(row["backend"]), []).append(row)
    summary: dict[str, Any] = {}
    for backend, rows in by_backend.items():
        selected_from_polish = sum(1 for row in rows if row.get("selected_from") == "polish")
        summary[backend] = {
            "clips": len(rows),
            "avg_score": round(sum(float(row.get("useful_dictation_score") or 0.0) for row in rows) / max(1, len(rows)), 3),
            "p50_seconds": _percentile([float(row.get("seconds") or 0.0) for row in rows], 0.50),
            "p95_seconds": _percentile([float(row.get("seconds") or 0.0) for row in rows], 0.95),
            "selected_from_polish": selected_from_polish,
        }
    return summary


def _load_product_scorecard() -> Any:
    path = ROOT / "scripts/product_scorecard.py"
    spec = importlib.util.spec_from_file_location("product_scorecard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["product_scorecard"] = module
    spec.loader.exec_module(module)
    return module


def _audio_path(item: dict[str, Any]) -> Path:
    path = Path(str(item.get("audio") or "")).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", text))


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:120] or "row"


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * q))
    return round(ordered[max(0, min(index, len(ordered) - 1))], 3)


def _short(text: str, limit: int = 90) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


if __name__ == "__main__":
    main()
