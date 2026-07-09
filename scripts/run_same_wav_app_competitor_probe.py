#!/usr/bin/env python3
"""Probe same-WAV app-level competitor measurability and run available adapters."""

from __future__ import annotations

import argparse
import json
import socket
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from ramblefix.config import DEFAULT_WHISPER_CPP_BASE_MODEL, DEFAULT_WHISPER_CPP_SMALL_MODEL
from ramblefix.engine_router import transcribe_ramblefix_hinglish_v1
from ramblefix.eval import (
    _corpus_terms,
    _corpus_category,
    meaning_coverage,
    meaning_loss,
    term_coverage_report,
    word_error_rate,
)
from ramblefix.external_asr import transcribe_whisper_cpp_server_translate
from ramblefix.quality import repeated_substring_score
from ramblefix.sidecar import ensure_ready as ensure_ramblefix_sidecar


ROOT = Path(__file__).resolve().parent.parent
TYPEWHISPER_CLI = (
    ROOT
    / "eval_runs/competitor-apps-20260614/apps/typewhisper/TypeWhisper.app/Contents/MacOS/typewhisper-cli"
)
TYPEWHISPER_APP = ROOT / "eval_runs/competitor-apps-20260614/apps/typewhisper/TypeWhisper.app"
OPENWHISPR_APP = ROOT / "eval_runs/competitor-apps-20260614/apps/openwhispr/OpenWhispr.app"
OPENWHISPR_SERVER = OPENWHISPR_APP / "Contents/Resources/bin/whisper-server-darwin-arm64"
OPENWHISPR_MODEL_PATHS = {
    "base": Path(DEFAULT_WHISPER_CPP_BASE_MODEL),
    "small": Path(DEFAULT_WHISPER_CPP_SMALL_MODEL),
}

ENGLISH_CATEGORIES = {"fleurs_english", "youtube_english", "real_use_english_dictation"}
HINGLISH_CATEGORIES = {
    "openslr104_hinglish",
    "real_use_hindi_hinglish_probe",
    "real_use_hindi_hinglish_dictation",
    "real_use_hinglish_dictation",
}


@dataclass
class ProbeStatus:
    tool: str
    status: str
    evidence: str
    detail: str
    path: str | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="eval_corpus/public_launch_dictation_pool_20260613.json", type=Path)
    parser.add_argument("--output-dir", default="eval_runs/same-wav-app-competitor-probe-20260614", type=Path)
    parser.add_argument("--english", type=int, default=5)
    parser.add_argument("--hinglish", type=int, default=5)
    parser.add_argument("--run-typewhisper", action="store_true")
    parser.add_argument("--run-openwhispr-bundle-engine", action="store_true")
    parser.add_argument("--openwhispr-model", action="append", choices=sorted(OPENWHISPR_MODEL_PATHS), default=[])
    parser.add_argument("--run-ramblefix-launch-engine", action="store_true")
    args = parser.parse_args()
    openwhispr_models = args.openwhispr_model or ["base"]

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    selected = select_cases(corpus, english=args.english, hinglish=args.hinglish)
    (output_dir / "selected_cases.json").write_text(json.dumps(selected, indent=2, ensure_ascii=False), encoding="utf-8")

    statuses = probe_statuses()
    rows: list[dict[str, Any]] = []
    persist_outputs(output_dir, statuses, rows)

    if args.run_typewhisper:
        typewhisper_status = next((s for s in statuses if s.tool == "TypeWhisper"), None)
        if typewhisper_status and typewhisper_status.status == "ready":
            rows.extend(run_typewhisper(selected, args.corpus))
        else:
            detail = typewhisper_status.detail if typewhisper_status else "not found"
            statuses.append(
                ProbeStatus(
                    tool="TypeWhisper same-WAV run",
                    status="blocked",
                    evidence="cli_probe",
                    detail=f"Skipped transcription because TypeWhisper is not ready: {detail}",
                )
            )
        persist_outputs(output_dir, statuses, rows)

    if args.run_openwhispr_bundle_engine:
        for model_name in openwhispr_models:
            model_path = OPENWHISPR_MODEL_PATHS[model_name]
            if OPENWHISPR_SERVER.exists() and model_path.exists():
                rows.extend(run_openwhispr_bundle_engine(selected, args.corpus, model_name, model_path))
            else:
                statuses.append(
                    ProbeStatus(
                        tool=f"OpenWhispr bundle engine ({model_name})",
                        status="blocked",
                        evidence="bundle_engine_probe",
                        detail=f"Missing server or model. server={OPENWHISPR_SERVER.exists()} model={model_path}",
                    )
                )
            persist_outputs(output_dir, statuses, rows)

    if args.run_ramblefix_launch_engine:
        rows.extend(run_ramblefix_launch_engine(selected, args.corpus))
        persist_outputs(output_dir, statuses, rows)

    persist_outputs(output_dir, statuses, rows, selected=selected)
    print(output_dir / "summary.md")


def persist_outputs(
    output_dir: Path,
    statuses: list[ProbeStatus],
    rows: list[dict[str, Any]],
    *,
    selected: list[dict[str, Any]] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "app_probe_status.json").write_text(
        json.dumps([asdict(status) for status in statuses], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "app_competitor_rows.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    if selected is not None:
        (output_dir / "summary.md").write_text(render_markdown(statuses, rows, selected), encoding="utf-8")


def select_cases(corpus: list[dict[str, Any]], *, english: int, hinglish: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected.extend([row for row in corpus if row.get("category") in ENGLISH_CATEGORIES][:english])
    selected.extend([row for row in corpus if row.get("category") in HINGLISH_CATEGORIES][:hinglish])
    return selected


def probe_statuses() -> list[ProbeStatus]:
    statuses: list[ProbeStatus] = []

    if TYPEWHISPER_CLI.exists():
        launch_detail = _typewhisper_launchability_detail()
        proc = subprocess.run([str(TYPEWHISPER_CLI), "status", "--json"], text=True, capture_output=True, timeout=10)
        raw = (proc.stdout or proc.stderr).strip()
        status = "blocked"
        detail = raw
        try:
            payload = json.loads(raw)
            if payload.get("status") == "ready":
                status = "ready"
            elif payload.get("status") == "no_model":
                detail = "API server reachable, but no local model is installed."
        except json.JSONDecodeError:
            pass
        if launch_detail:
            if status != "ready":
                detail = f"{detail} Launchability: {launch_detail}"
            elif "invalid signature" in launch_detail.lower():
                status = "blocked"
                detail = f"CLI reports ready, but app launchability is invalid: {launch_detail}"
        statuses.append(ProbeStatus("TypeWhisper", status, "cli_status+launch_probe", detail, str(TYPEWHISPER_CLI)))
    else:
        statuses.append(ProbeStatus("TypeWhisper", "missing", "filesystem", "CLI not found", str(TYPEWHISPER_CLI)))

    wispr_app = Path("/Applications/Wispr Flow.app")
    wispr_db = Path.home() / "Library/Application Support/Wispr Flow/flow.sqlite"
    if wispr_app.exists() and wispr_db.exists():
        statuses.append(
            ProbeStatus(
                "Wispr Flow",
                "blocked",
                "app_db_probe",
                "Installed and DB-readable, but synthetic PTT key did not create a new row. Needs UI/global-hotkey automation or manual same-WAV capture.",
                str(wispr_app),
            )
        )
    else:
        statuses.append(ProbeStatus("Wispr Flow", "missing", "filesystem", "App or DB not found", str(wispr_app)))

    handy_app = ROOT / "eval_runs/competitor-apps-20260614/apps/handy/Handy.app"
    if handy_app.exists():
        statuses.append(
            ProbeStatus(
                "Handy",
                "blocked",
                "bundle_probe",
                "Downloaded app bundle exists, but no file-transcription CLI/API adapter found yet. GUI hotkey/virtual-audio automation still needed.",
                str(handy_app),
            )
        )
    else:
        statuses.append(ProbeStatus("Handy", "missing", "filesystem", "Downloaded app bundle not found", str(handy_app)))

    openwhispr_app = ROOT / "eval_runs/competitor-apps-20260614/apps/openwhispr/OpenWhispr.app"
    openwhispr_zip = ROOT / "eval_runs/competitor-apps-20260614/downloads/OpenWhispr-1.7.2-arm64-mac.zip"
    if openwhispr_app.exists():
        model_bits = []
        for model_name, model_path in OPENWHISPR_MODEL_PATHS.items():
            model_bits.append(f"{model_name}={'present' if model_path.exists() else 'missing'}")
        statuses.append(
            ProbeStatus(
                "OpenWhispr",
                "blocked",
                "bundle_probe",
                "App bundle exists. App IPC/file adapter is GUI-bound, but its bundled whisper-server can be measured separately as bundle-engine evidence. "
                + ", ".join(model_bits),
                str(openwhispr_app),
            )
        )
    elif openwhispr_zip.exists():
        statuses.append(
            ProbeStatus(
                "OpenWhispr",
                "downloaded",
                "download_probe",
                "Release zip is downloading or downloaded but not unpacked/inspected yet.",
                str(openwhispr_zip),
            )
        )
    else:
        statuses.append(ProbeStatus("OpenWhispr", "missing", "filesystem", "Release asset not found locally", str(openwhispr_zip)))

    voiceink_app = Path("/Applications/VoiceInk.app")
    statuses.append(
        ProbeStatus(
            "VoiceInk",
            "missing" if not voiceink_app.exists() else "blocked",
            "filesystem",
            "No installed VoiceInk app found." if not voiceink_app.exists() else "Installed but adapter not implemented.",
            str(voiceink_app),
        )
    )

    statuses.append(
        ProbeStatus(
            "Apple Dictation",
            "blocked",
            "platform_probe",
            "No same-WAV automation yet. Requires virtual input plus Dictation hotkey/control path.",
        )
    )
    return statuses


def _typewhisper_launchability_detail() -> str:
    if not TYPEWHISPER_APP.exists():
        return "TypeWhisper.app not found."
    proc = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(TYPEWHISPER_APP)],
        text=True,
        capture_output=True,
        timeout=15,
    )
    if proc.returncode != 0:
        return (proc.stdout or proc.stderr or f"codesign returned {proc.returncode}").strip().replace("\n", " ")
    return "codesign verification passed."


def run_typewhisper(items: list[dict[str, Any]], corpus_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = corpus_path.resolve().parent.parent
    for item in items:
        audio = Path(str(item["audio"]))
        if not audio.is_absolute():
            audio = root / audio
        started = time.perf_counter()
        proc = subprocess.run(
            [str(TYPEWHISPER_CLI), "transcribe", str(audio), "--json"],
            text=True,
            capture_output=True,
            timeout=120,
        )
        seconds = round(time.perf_counter() - started, 3)
        text = parse_typewhisper_text(proc.stdout)
        error = None if proc.returncode == 0 and text else (proc.stderr or proc.stdout or f"returncode={proc.returncode}").strip()
        rows.append(score_competitor_row(item, audio, "typewhisper_cli", text, seconds, error=error))
        print(f"{item['id']} typewhisper_cli return={proc.returncode} seconds={seconds}", flush=True)
    return rows


def run_openwhispr_bundle_engine(
    items: list[dict[str, Any]],
    corpus_path: Path,
    model_name: str,
    model_path: Path,
) -> list[dict[str, Any]]:
    """Run OpenWhispr's shipped whisper-server binary on the selected WAVs.

    This is engine evidence, not full app UX evidence. It mirrors the local
    OpenWhispr code path: whisper-server, selected ggml model, auto language,
    no translate flag.
    """
    rows: list[dict[str, Any]] = []
    root = corpus_path.resolve().parent.parent
    port = _choose_port(8191, 8199)
    log_path = ROOT / f"eval_runs/competitor-apps-20260614/openwhispr-{model_name}-server.log"
    proc = _start_openwhispr_server(model_path=model_path, port=port, log_path=log_path)
    try:
        for item in items:
            audio = Path(str(item["audio"]))
            if not audio.is_absolute():
                audio = root / audio
            started = time.perf_counter()
            text = ""
            error = None
            try:
                text = _post_whisper_server(audio, port=port)
            except Exception as exc:  # noqa: BLE001 - probe output should capture exact failure
                error = repr(exc)
            seconds = round(time.perf_counter() - started, 3)
            row = score_competitor_row(
                item,
                audio,
                f"openwhispr_bundle_whisper_server_{model_name}",
                text,
                seconds,
                error=error,
            )
            row["meta"] = {
                "source": "openwhispr_bundle_engine",
                "evidence": "same_wav_bundle_server",
                "app": str(OPENWHISPR_APP),
                "server_binary": str(OPENWHISPR_SERVER),
                "model": model_name,
                "model_path": str(model_path),
                "language": "auto",
                "translate": False,
                "port": port,
            }
            rows.append(row)
            print(f"{item['id']} openwhispr_bundle_{model_name} seconds={seconds} error={bool(error)}", flush=True)
    finally:
        _stop_process(proc)
    return rows


def run_ramblefix_launch_engine(items: list[dict[str, Any]], corpus_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = corpus_path.resolve().parent.parent
    state = ensure_ramblefix_sidecar(timeout_seconds=20.0)
    if not state.ready:
        raise RuntimeError(f"RambleFix sidecar not ready: {state.status} {state.error}")

    for item in items:
        audio = Path(str(item["audio"]))
        if not audio.is_absolute():
            audio = root / audio
        category = _corpus_category(item)
        started = time.perf_counter()
        error = None
        try:
            if category in HINGLISH_CATEGORIES:
                tr = transcribe_ramblefix_hinglish_v1(audio)
                text = tr.text
                backend = "ramblefix_launch_engine_v1_hinglish"
                meta = {
                    "source": "ramblefix_launch_engine_v1",
                    "evidence": "same_wav_local_engine",
                    "route": tr.route,
                    "risk_reasons": tr.risk_reasons,
                    "engine": tr.engine,
                }
            else:
                tr = transcribe_whisper_cpp_server_translate(audio)
                text = tr.text
                backend = "ramblefix_launch_engine_v1_fast"
                meta = {
                    "source": "ramblefix_launch_engine_v1",
                    "evidence": "same_wav_local_engine",
                    "engine": tr.engine,
                    "route": "fast_server_translate",
                }
        except Exception as exc:  # noqa: BLE001 - probe output should capture exact failure
            text = ""
            backend = "ramblefix_launch_engine_v1"
            meta = {"source": "ramblefix_launch_engine_v1", "evidence": "same_wav_local_engine"}
            error = repr(exc)
        seconds = round(time.perf_counter() - started, 3)
        row = score_competitor_row(item, audio, backend, text, seconds, error=error)
        row["meta"] = meta
        rows.append(row)
        print(f"{item['id']} {backend} seconds={seconds} error={bool(error)}", flush=True)
    return rows


def _choose_port(start: int, end: int) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"no free port in {start}-{end}")


def _start_openwhispr_server(*, model_path: Path, port: int, log_path: Path) -> subprocess.Popen[bytes]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(OPENWHISPR_SERVER),
        "--model",
        str(model_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--language",
        "auto",
        "--no-timestamps",
    ]
    log_file = log_path.open("wb")
    try:
        proc = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
    finally:
        log_file.close()

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            detail = log_path.read_text(errors="replace")[-2000:] if log_path.exists() else ""
            raise RuntimeError(f"OpenWhispr whisper-server exited with {proc.returncode}: {detail}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                time.sleep(0.2)
                return proc
        time.sleep(0.25)
    _stop_process(proc)
    detail = log_path.read_text(errors="replace")[-2000:] if log_path.exists() else ""
    raise RuntimeError(f"OpenWhispr whisper-server did not open port {port}: {detail}")


def _post_whisper_server(audio: Path, *, port: int) -> str:
    with audio.open("rb") as audio_file:
        response = requests.post(
            f"http://127.0.0.1:{port}/inference",
            files={"file": (audio.name, audio_file, "audio/wav")},
            data={"language": "auto", "response_format": "json"},
            timeout=300,
        )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload.get("text"), str):
        return payload["text"].strip()
    if isinstance(payload.get("transcription"), list):
        return " ".join(str(segment.get("text", "")).strip() for segment in payload["transcription"]).strip()
    return str(payload).strip()


def _stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def parse_typewhisper_text(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    for key in ("text", "transcript", "transcription", "formattedText"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return raw.strip()


def score_competitor_row(
    item: dict[str, Any],
    audio: Path,
    backend: str,
    text: str,
    seconds: float,
    *,
    error: str | None,
) -> dict[str, Any]:
    gold = str(item.get("gold", ""))
    term_report = term_coverage_report(gold, text, _corpus_terms(item))
    return {
        "id": item["id"],
        "category": _corpus_category(item),
        "backend": backend,
        "audio": str(audio),
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
        "seconds": seconds,
        "meta": {"source": backend, "evidence": "same_wav_cli"},
        "error": error,
    }


def render_markdown(statuses: list[ProbeStatus], rows: list[dict[str, Any]], selected: list[dict[str, Any]]) -> str:
    lines = [
        "# Same-WAV App Competitor Probe",
        "",
        f"- Selected clips: `{len(selected)}`",
        f"- Measured competitor rows: `{len(rows)}`",
        "",
        "## App Status",
        "",
        "| Tool | Status | Evidence | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for status in statuses:
        detail = status.detail.replace("\n", " ")[:220]
        lines.append(f"| {status.tool} | {status.status} | {status.evidence} | {detail} |")
    lines.append("")
    lines.append("## Measured Rows")
    lines.append("")
    if not rows:
        lines.append("No competitor app rows measured yet.")
    else:
        lines.append("### Aggregate")
        lines.append("")
        lines.append("| Backend | Rows | Avg WER | Avg Coverage | Avg Terms | p50 sec | p95 sec | Errors |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for backend, backend_rows in sorted(_rows_by_backend(rows).items()):
            lines.append(_aggregate_line(backend, backend_rows))
        lines.append("")
        lines.append("### Detail")
        lines.append("")
        lines.append("| ID | Backend | WER | Coverage | Terms | Seconds | Error |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- |")
        for row in rows:
            terms = "" if row.get("term_coverage") is None else f"{float(row['term_coverage']):.3f}"
            err = "yes" if row.get("error") else ""
            lines.append(
                f"| {row['id']} | {row['backend']} | {float(row['wer']):.3f} | "
                f"{float(row['meaning_coverage']):.3f} | {terms} | {float(row['seconds']):.3f} | {err} |"
            )
    lines.append("")
    return "\n".join(lines)


def _rows_by_backend(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["backend"]), []).append(row)
    return grouped


def _mean_number(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def _fmt(value: float | None) -> str:
    return "" if value is None else f"{value:.3f}"


def _aggregate_line(backend: str, rows: list[dict[str, Any]]) -> str:
    seconds = [float(row["seconds"]) for row in rows if row.get("seconds") is not None]
    errors = sum(1 for row in rows if row.get("error"))
    return (
        f"| {backend} | {len(rows)} | {_fmt(_mean_number(rows, 'wer'))} | "
        f"{_fmt(_mean_number(rows, 'meaning_coverage'))} | {_fmt(_mean_number(rows, 'term_coverage'))} | "
        f"{_fmt(_percentile(seconds, 0.50))} | {_fmt(_percentile(seconds, 0.95))} | {errors} |"
    )


if __name__ == "__main__":
    main()
