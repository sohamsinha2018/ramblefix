from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]

NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
}


@dataclass
class GeminiResult:
    model: str
    ok: bool
    seconds: float
    language_class: str = ""
    transcript: str = ""
    confidence: float | None = None
    reason: str = ""
    error: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Cloud-classify and gold-check goal STT corpus rows with Gemini audio models.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--output-corpus", type=Path, required=True)
    parser.add_argument("--models", default="gemini-2.5-flash,gemini-2.5-pro")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--dns-server",
        default=os.environ.get("RAMBLEFIX_GEMINI_DNS_SERVER", ""),
        help="Optional DNS server for Gemini calls when macOS system DNS is broken, e.g. 8.8.8.8.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse already confirmed/review-needed rows from --output and only process unfinished rows.",
    )
    parser.add_argument(
        "--retry-review-needed",
        action="store_true",
        help="With --resume, retry rows currently marked needs_human_review; confirmed rows are still reused.",
    )
    parser.add_argument(
        "--network-fail-fast",
        type=int,
        default=3,
        help="Stop after this many consecutive rows fail only with network/DNS errors. Use 0 to disable.",
    )
    parser.add_argument(
        "--transcript-style",
        choices=["exact", "roman-hinglish"],
        default="exact",
        help="Use roman-hinglish for product gold where Hindi should be romanized instead of Devanagari.",
    )
    parser.add_argument(
        "--min-confirming-models",
        type=int,
        default=2,
        help="Minimum successful cloud model transcripts required before a row can become cloud_confirmed.",
    )
    parser.add_argument(
        "--recombine-existing",
        action="store_true",
        help="Do not call Gemini; recompute status/output-corpus from an existing --output file.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if args.recombine_existing:
        existing_rows = list(_load_existing_by_id(args.output).values())
        if not existing_rows:
            raise SystemExit(f"No existing rows to recombine in {args.output}")
        recombined = [_recombine_stored_row(row, min_confirming_models=args.min_confirming_models) for row in existing_rows]
        _write_outputs(args.output, args.output_corpus, recombined)
        print(f"recombined {len(recombined)} rows with min_confirming_models={args.min_confirming_models}")
        print(f"wrote {args.output}")
        print(f"wrote {args.output_corpus}")
        return

    if not api_key:
        raise SystemExit("Set GEMINI_API_KEY or GOOGLE_API_KEY.")

    rows = json.loads(args.corpus.read_text(encoding="utf-8"))
    if args.limit:
        rows = rows[: args.limit]
    models = [part.strip() for part in args.models.split(",") if part.strip()]
    existing_by_id = _load_existing_by_id(args.output) if args.resume else {}
    out_rows: list[dict[str, Any]] = []
    consecutive_network_failures = 0
    for index, row in enumerate(rows, 1):
        row_id = str(row.get("id") or "")
        existing = existing_by_id.get(row_id)
        if existing is not None and _resume_row_is_done(existing, retry_review_needed=args.retry_review_needed):
            out_rows.append(existing)
            print(f"[{index}/{len(rows)}] {row.get('id')} SKIP resume {existing.get('cloud_status')}", flush=True)
            _write_outputs(args.output, args.output_corpus, out_rows)
            continue

        audio = _resolve_audio(row.get("audio"))
        print(f"[{index}/{len(rows)}] {row.get('id')} {audio.name}", flush=True)
        results: list[GeminiResult] = []
        for model in models:
            started = time.perf_counter()
            try:
                payload = transcribe_and_classify(
                    audio,
                    model=model,
                    api_key=api_key,
                    timeout_seconds=args.timeout_seconds,
                    transcript_style=args.transcript_style,
                    dns_server=args.dns_server.strip() or None,
                )
                results.append(
                    GeminiResult(
                        model=model,
                        ok=True,
                        seconds=round(time.perf_counter() - started, 3),
                        language_class=str(payload.get("language_class") or "").strip(),
                        transcript=normalize_spaces(str(payload.get("transcript") or "")),
                        confidence=_float_or_none(payload.get("confidence")),
                        reason=str(payload.get("reason") or "").strip(),
                    )
                )
                print(f"  {model}: {results[-1].seconds:.2f}s {results[-1].language_class} {short(results[-1].transcript)}", flush=True)
            except Exception as exc:  # noqa: BLE001 - keep batch moving and record failures.
                results.append(
                    GeminiResult(
                        model=model,
                        ok=False,
                        seconds=round(time.perf_counter() - started, 3),
                        error=f"{type(exc).__name__}: {str(exc)[:500]}",
                    )
                )
                print(f"  {model}: ERR {results[-1].error[:180]}", flush=True)
        combined = _combine_row(row, results, min_confirming_models=args.min_confirming_models)
        out_rows.append(combined)
        _write_outputs(args.output, args.output_corpus, out_rows)
        if _all_network_errors(results):
            consecutive_network_failures += 1
            if args.network_fail_fast and consecutive_network_failures >= args.network_fail_fast:
                print(
                    f"Stopping after {consecutive_network_failures} consecutive network/DNS-only failed rows. "
                    "Fix network/DNS and rerun with --resume.",
                    flush=True,
                )
                break
        else:
            consecutive_network_failures = 0

    _write_outputs(args.output, args.output_corpus, out_rows)
    print(f"wrote {args.output}")
    print(f"wrote {args.output_corpus}")


def transcribe_and_classify(
    audio: Path,
    *,
    model: str,
    api_key: str,
    timeout_seconds: float,
    transcript_style: str = "exact",
    dns_server: str | None = None,
) -> dict[str, Any]:
    data = base64.b64encode(audio.read_bytes()).decode("ascii")
    style_instruction = ""
    if transcript_style == "roman-hinglish":
        style_instruction = (
            "For Hindi/Hinglish speech, write Hindi words in Roman Hinglish, not Devanagari. "
            "Keep English words, acronyms, product names, numbers, negation, and profanity exactly as heard. "
            "Do not translate Hindi meaning away; romanize it.\n"
        )
    request_payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "You are building gold labels for an offline Indian work-dictation STT benchmark.\n"
                                "Listen to the audio and return ONLY valid JSON with these keys:\n"
                                "- language_class: one of english_only, hindi_english, uncertain.\n"
                                "- confidence: number from 0 to 1.\n"
                                "- transcript: exact transcript of what was said. Preserve English, Hindi, Hinglish/code-switching, acronyms, product names, numbers, negation, and profanity. Do not summarize.\n"
                                "- reason: one short reason.\n\n"
                                f"{style_instruction}"
                                "Classification rule: english_only means at least 98% of the spoken words are English and there is no meaningful Hindi/Hinglish code-switch. "
                                "If there is any meaningful Hindi/Hinglish content, classify hindi_english. If audio is unclear, classify uncertain."
                            )
                        },
                        {"inline_data": {"mime_type": "audio/wav", "data": data}},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "response_mime_type": "application/json",
            },
        }
    if dns_server:
        status_code, response_text = _post_gemini_with_explicit_dns(
            model=model,
            api_key=api_key,
            payload=request_payload,
            timeout_seconds=timeout_seconds,
            dns_server=dns_server,
        )
        if status_code >= 400:
            raise RuntimeError(f"Gemini {status_code}: {response_text[:500]}")
        payload = json.loads(response_text)
    else:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key},
            json=request_payload,
            timeout=timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Gemini {response.status_code}: {response.text[:500]}")
        payload = response.json()
    parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = " ".join(str(part.get("text") or "") for part in parts).strip()
    return _parse_json_text(text)


class _ResolvedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, resolved_ip: str, *, timeout: float) -> None:
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self._resolved_ip = resolved_ip

    def connect(self) -> None:
        sock = socket.create_connection((self._resolved_ip, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _post_gemini_with_explicit_dns(
    *,
    model: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    dns_server: str,
) -> tuple[int, str]:
    host = "generativelanguage.googleapis.com"
    path = f"/v1beta/models/{model}:generateContent"
    resolved_ip = _resolve_with_dig(host, dns_server=dns_server)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    conn = _ResolvedHTTPSConnection(host, resolved_ip, timeout=timeout_seconds)
    try:
        conn.request(
            "POST",
            path,
            body=body,
            headers={
                "Host": host,
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "x-goog-api-key": api_key,
            },
        )
        response = conn.getresponse()
        text = response.read().decode("utf-8", errors="replace")
        return response.status, text
    finally:
        conn.close()


def _resolve_with_dig(host: str, *, dns_server: str) -> str:
    completed = subprocess.run(
        ["dig", f"@{dns_server}", host, "+short", "+time=3", "+tries=1"],
        check=False,
        capture_output=True,
        text=True,
    )
    addresses = [
        line.strip()
        for line in completed.stdout.splitlines()
        if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", line.strip())
    ]
    if not addresses:
        stderr = completed.stderr.strip()
        raise RuntimeError(f"explicit DNS lookup failed for {host} via {dns_server}: {stderr or completed.stdout.strip()}")
    return addresses[0]


def _parse_json_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("Gemini response JSON was not an object")
    return payload


def _combine_row(row: dict[str, Any], results: list[GeminiResult], *, min_confirming_models: int = 2) -> dict[str, Any]:
    good = [result for result in results if result.ok and result.transcript]
    agreement_subset: list[GeminiResult] = []
    if not good:
        status = "cloud_failed"
        language_class = str(row.get("bucket") or "uncertain")
        gold = str(row.get("gold") or "")
        reason = "no successful Gemini result"
    else:
        classes = [result.language_class for result in good if result.language_class in {"english_only", "hindi_english", "uncertain"}]
        language_class = _majority_or_uncertain(classes)
        agreement_subset = _best_agreeing_subset(good, min_confirming_models=min_confirming_models)
        best_pool = agreement_subset or good
        best = max(best_pool, key=lambda result: (result.confidence or 0.0, len(result.transcript)))
        gold = best.transcript
        if len(good) < min_confirming_models:
            status = "needs_human_review"
            reason = f"only {len(good)} successful cloud transcript(s); need {min_confirming_models}"
        elif agreement_subset:
            status = "cloud_confirmed"
            language_class = agreement_subset[0].language_class
            models = ", ".join(result.model for result in agreement_subset)
            reason = f"{len(agreement_subset)} Gemini models agree on transcript and language class: {models}"
        elif language_class != "uncertain":
            status = "needs_human_review"
            reason = "Gemini language class resolved but no confirming transcript subset"
        else:
            status = "needs_human_review"
            reason = "Gemini classification uncertain"

    return {
        **row,
        "bucket": "hindi_english" if language_class == "hindi_english" else "english_only" if language_class == "english_only" else row.get("bucket", "english_only"),
        "gold": gold,
        "critical": _extract_terms(gold),
        "cloud_status": status,
        "classification_status": "trusted" if status == "cloud_confirmed" else "needs_review",
        "classification_reason": reason,
        "meta": {
            **(row.get("meta") if isinstance(row.get("meta"), dict) else {}),
            "cloud_language_class": language_class,
            "cloud_crosscheck_status": status,
            "cloud_crosscheck_reason": reason,
            "cloud_results": [asdict(result) for result in results],
        },
    }


def _best_agreeing_subset(results: list[GeminiResult], *, min_confirming_models: int, threshold: float = 0.88) -> list[GeminiResult]:
    candidates = [
        result
        for result in results
        if result.language_class in {"english_only", "hindi_english"} and result.transcript
    ]
    if len(candidates) < min_confirming_models:
        return []
    best_subset: list[GeminiResult] = []
    best_score = -1.0
    for subset in _combinations(candidates, min_confirming_models):
        language_class = subset[0].language_class
        if any(result.language_class != language_class for result in subset):
            continue
        pair_scores = [
            similarity(left.transcript, right.transcript)
            for index, left in enumerate(subset)
            for right in subset[index + 1 :]
        ]
        if not pair_scores:
            continue
        score = min(pair_scores)
        if score >= threshold and score > best_score:
            best_score = score
            best_subset = subset
    return best_subset


def _combinations(items: list[GeminiResult], size: int) -> list[list[GeminiResult]]:
    if size <= 0:
        return [[]]
    if size > len(items):
        return []
    if size == 1:
        return [[item] for item in items]
    combos: list[list[GeminiResult]] = []
    for index, item in enumerate(items):
        for tail in _combinations(items[index + 1 :], size - 1):
            combos.append([item, *tail])
    return combos


def _recombine_stored_row(row: dict[str, Any], *, min_confirming_models: int) -> dict[str, Any]:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    stored = meta.get("cloud_results") if isinstance(meta.get("cloud_results"), list) else []
    results = []
    for item in stored:
        if not isinstance(item, dict):
            continue
        results.append(
            GeminiResult(
                model=str(item.get("model") or ""),
                ok=bool(item.get("ok")),
                seconds=float(item.get("seconds") or 0.0),
                language_class=str(item.get("language_class") or ""),
                transcript=normalize_spaces(str(item.get("transcript") or "")),
                confidence=_float_or_none(item.get("confidence")),
                reason=str(item.get("reason") or ""),
                error=str(item.get("error") or ""),
            )
        )
    base = {**row}
    original_meta = base.get("meta") if isinstance(base.get("meta"), dict) else {}
    base["meta"] = {k: v for k, v in original_meta.items() if k not in {"cloud_language_class", "cloud_crosscheck_status", "cloud_crosscheck_reason", "cloud_results"}}
    return _combine_row(base, results, min_confirming_models=min_confirming_models)


def _write_outputs(output: Path, output_corpus: Path, rows: list[dict[str, Any]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    eval_ready = [row for row in rows if row.get("cloud_status") == "cloud_confirmed"]
    output_corpus.parent.mkdir(parents=True, exist_ok=True)
    output_corpus.write_text(json.dumps(eval_ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_existing_by_id(output: Path) -> dict[str, dict[str, Any]]:
    if not output.exists():
        return {}
    try:
        rows = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(rows, list):
        return {}
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("id"):
            by_id[str(row["id"])] = row
    return by_id


def _resume_row_is_done(row: dict[str, Any], *, retry_review_needed: bool = False) -> bool:
    if row.get("cloud_status") == "cloud_confirmed":
        return True
    if row.get("cloud_status") == "needs_human_review":
        return not retry_review_needed
    return False


def _all_network_errors(results: list[GeminiResult]) -> bool:
    if not results or any(result.ok for result in results):
        return False
    return all(_is_network_error(result.error) for result in results)


def _is_network_error(error: str) -> bool:
    lowered = error.lower()
    return any(
        marker in lowered
        for marker in [
            "connectionerror",
            "connecttimeout",
            "readtimeout",
            "name resolution",
            "failed to resolve",
            "nodename nor servname",
            "temporary failure in name resolution",
            "max retries exceeded",
            "network is unreachable",
        ]
    )


def _majority_or_uncertain(values: list[str]) -> str:
    if not values:
        return "uncertain"
    counts = {value: values.count(value) for value in set(values)}
    best, count = max(counts.items(), key=lambda item: item[1])
    if count >= 2 or len(values) == 1:
        return best
    return "uncertain"


def _extract_terms(text: str) -> list[str]:
    terms = set()
    for match in re.finditer(r"\b[A-Z][A-Z0-9]{1,9}\b|\b[A-Za-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b", text):
        raw = match.group(0).strip()
        if raw.lower() not in {"ok", "yes", "no"}:
            terms.add(raw)
    for term in ["RambleFix", "MCP", "UX", "ASR", "STT", "Codex", "Gemini", "OpenAI"]:
        if re.search(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE):
            terms.add(term)
    return sorted(terms, key=lambda value: value.lower())


def _resolve_audio(value: Any) -> Path:
    path = Path(str(value or "")).expanduser()
    if path.is_absolute():
        return path
    return (ROOT / path).resolve()


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def similarity(left: str, right: str) -> float:
    left_tokens = normalize_for_compare(left).split()
    right_tokens = normalize_for_compare(right).split()
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    return SequenceMatcher(None, left_tokens, right_tokens, autojunk=False).ratio()


def normalize_for_compare(text: str) -> str:
    tokens = re.sub(r"[^a-z0-9\u0900-\u097f]+", " ", text.lower()).strip().split()
    return " ".join(NUMBER_WORDS.get(token, token) for token in tokens)


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def short(text: str, width: int = 100) -> str:
    clean = normalize_spaces(text)
    return clean if len(clean) <= width else clean[: width - 3] + "..."


if __name__ == "__main__":
    main()
