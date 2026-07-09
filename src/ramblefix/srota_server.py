from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_STREAM_SESSIONS: Any | None = None


def _stream_sessions() -> Any:
    global _STREAM_SESSIONS
    if _STREAM_SESSIONS is None:
        from ramblefix.hindi_stream_session import HindiStreamSessionManager

        _STREAM_SESSIONS = HindiStreamSessionManager()
    return _STREAM_SESSIONS


class SrotaHandler(BaseHTTPRequestHandler):
    server_version = "RambleFixSrota/0.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        if self.path.rstrip("/") == "/health":
            self._send_json(
                {
                    "ok": True,
                    "engine": "srota",
                    "hinglish_finalizer_backend": os.environ.get("RAMBLEFIX_HINGLISH_FINALIZER_BACKEND", "oriserve"),
                    "oriserve_backend": os.environ.get("RAMBLEFIX_ORISERVE_BACKEND", "auto"),
                    "srota_backend": os.environ.get("RAMBLEFIX_SROTA_BACKEND", "auto"),
                }
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
        if self.path.rstrip("/") == "/inference":
            self._handle_local_mlx_inference()
            return
        if self.path.rstrip("/") != "/transcribe":
            if self.path.rstrip("/") == "/hindi-polish":
                self._handle_hindi_polish()
                return
            if self.path.rstrip("/") == "/chinese-polish":
                self._handle_chinese_polish()
                return
            if self.path.rstrip("/") == "/hindi-risk":
                self._handle_hindi_risk()
                return
            if self.path.rstrip("/") == "/process-second-pass":
                self._handle_process_second_pass()
                return
            if self.path.rstrip("/") == "/hindi-async-polish":
                self._handle_hindi_async_polish()
                return
            if self.path.rstrip("/") == "/hindi-stream/start":
                self._handle_hindi_stream_start()
                return
            if self.path.rstrip("/") == "/hindi-stream/warm":
                self._handle_hindi_stream_warm()
                return
            if self.path.rstrip("/") == "/chinese-polish/warm":
                self._handle_chinese_polish_warm()
                return
            if self.path.rstrip("/") == "/hindi-stream/finish":
                self._handle_hindi_stream_finish()
                return
            if self.path.rstrip("/") == "/hindi-stream/cancel":
                self._handle_hindi_stream_cancel()
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            payload = self._read_json()
            audio_path = Path(str(payload.get("audio_path", ""))).expanduser().resolve()
            if not audio_path.exists():
                raise FileNotFoundError(f"missing audio_path: {audio_path}")
            started = time.perf_counter()
            from ramblefix.external_asr import transcribe_srota_hinglish

            transcript = transcribe_srota_hinglish(audio_path)
            self._send_json(
                {
                    "text": transcript.text,
                    "engine": f"srota.server:{transcript.engine}",
                    "seconds": round(time.perf_counter() - started, 3),
                    "language": transcript.language,
                    "language_probability": transcript.language_probability,
                }
            )
        except Exception as exc:  # noqa: BLE001 - HTTP API should return JSON errors.
            self._send_json({"error": repr(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_local_mlx_inference(self) -> None:
        audio_path: Path | None = None
        delete_audio = False
        try:
            audio_path, delete_audio = self._read_audio_request()
            started = time.perf_counter()
            from ramblefix.asr import transcribe_audio
            from ramblefix.glossary import apply_glossary
            from ramblefix.quality import (
                is_blank_or_no_speech_transcript,
                is_degenerate_transcript,
                repeated_substring_score,
                wav_silence_metrics,
            )

            model = os.environ.get("RAMBLEFIX_FAST_ASR_MODEL", "mlx-community/whisper-large-v3-turbo-q4")
            language = os.environ.get("RAMBLEFIX_FAST_ASR_LANGUAGE", "").strip() or None
            transcript = transcribe_audio(audio_path, model=model, language=language)
            text = apply_glossary(transcript.text)
            seconds = round(time.perf_counter() - started, 3)
            audio_quality = wav_silence_metrics(audio_path)
            blank_or_no_speech = (
                is_blank_or_no_speech_transcript(transcript.text)
                or bool(audio_quality.get("audio_probably_silent"))
            )
            self._send_json(
                {
                    "text": text,
                    "raw_text": transcript.text,
                    "engine": f"srota.local_mlx:{transcript.engine}",
                    "processor": "glossary" if text != transcript.text else "none",
                    "seconds": seconds,
                    "language": transcript.language,
                    "route": "srota_local_mlx_inference",
                    "fallback_reason": "",
                    "quality": {
                        "blank_or_no_speech": blank_or_no_speech,
                        "degenerate": is_degenerate_transcript(transcript.text),
                        "repeated_substring_score": repeated_substring_score(transcript.text),
                        "char_count": len(transcript.text),
                        **audio_quality,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001 - HTTP API should return JSON errors.
            self._send_json({"error": repr(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
        finally:
            if delete_audio and audio_path is not None:
                audio_path.unlink(missing_ok=True)

    def _handle_hindi_stream_start(self) -> None:
        try:
            payload = self._read_json()
            run_id = str(payload.get("run_id", "")).strip()
            if not run_id:
                raise ValueError("missing run_id")
            chunk_dir = Path(str(payload.get("chunk_dir", ""))).expanduser().resolve()
            session = _stream_sessions().start(
                run_id=run_id,
                chunk_dir=chunk_dir,
                low_confidence_threshold=float(payload.get("low_confidence_threshold", 0.50)),
                early_low_confidence_threshold=float(payload.get("early_low_confidence_threshold", 0.80)),
                poll_interval_seconds=float(payload.get("poll_interval_seconds", 0.20)),
            )
            self._send_json(
                {
                    "ok": True,
                    "run_id": run_id,
                    "chunk_dir": str(session.chunk_dir),
                    "route": "hindi_stream_started",
                }
            )
        except Exception as exc:  # noqa: BLE001 - HTTP API should return JSON errors.
            self._send_json({"error": repr(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_hindi_stream_warm(self) -> None:
        try:
            from ramblefix.hindi_stream_session import (
                warm_detector_worker,
                warm_oriserve_worker,
                warm_qwen_worker,
            )

            warm_qwen_worker()
            warm_detector_worker()
            warm_oriserve_worker()
            self._send_json({"ok": True, "route": "hindi_stream_warm_started", "oriserve": "warming"})
        except Exception as exc:  # noqa: BLE001 - HTTP API should return JSON errors.
            self._send_json({"error": repr(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_chinese_polish_warm(self) -> None:
        try:
            from ramblefix.external_asr import warm_sensevoice_small

            threading.Thread(target=warm_sensevoice_small, daemon=True).start()
            self._send_json({"ok": True, "route": "chinese_polish_warm_started", "sensevoice": "warming"})
        except Exception as exc:  # noqa: BLE001 - HTTP API should return JSON errors.
            self._send_json({"error": repr(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_hindi_stream_finish(self) -> None:
        try:
            payload = self._read_json()
            run_id = str(payload.get("run_id", "")).strip()
            if not run_id:
                raise ValueError("missing run_id")
            result = _stream_sessions().finish(
                run_id=run_id,
                draft_text=str(payload.get("draft_text", "")),
                max_release_tail_seconds=float(payload.get("max_release_tail_seconds", 3.0)),
                wait_timeout_seconds=float(payload.get("wait_timeout_seconds", 3.0)),
                witness_audio_path=str(payload.get("audio_path") or "") or None,
                witness_timeout_seconds=float(payload.get("witness_timeout_seconds", 3.5)),
            )
            self._send_json(_hindi_stream_payload(result))
        except Exception as exc:  # noqa: BLE001 - HTTP API should return JSON errors.
            self._send_json({"error": repr(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_hindi_stream_cancel(self) -> None:
        try:
            payload = self._read_json()
            run_id = str(payload.get("run_id", "")).strip()
            if not run_id:
                raise ValueError("missing run_id")
            cancelled = _stream_sessions().cancel(run_id=run_id)
            self._send_json(
                {
                    "ok": True,
                    "run_id": run_id,
                    "cancelled": cancelled,
                    "route": "hindi_stream_cancelled" if cancelled else "hindi_stream_missing",
                }
            )
        except Exception as exc:  # noqa: BLE001 - HTTP API should return JSON errors.
            self._send_json({"error": repr(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_hindi_polish(self) -> None:
        try:
            from ramblefix.hindi_polish import polish_hindi_if_needed

            payload = self._read_json()
            audio_path = Path(str(payload.get("audio_path", ""))).expanduser().resolve()
            if not audio_path.exists():
                raise FileNotFoundError(f"missing audio_path: {audio_path}")
            draft_text = str(payload.get("draft_text", ""))
            low_confidence_threshold = float(payload.get("low_confidence_threshold", 0.50))
            force = bool(payload.get("force", False))
            result = polish_hindi_if_needed(
                audio_path,
                draft_text=draft_text,
                low_confidence_threshold=low_confidence_threshold,
                force=force,
            )
            self._send_json(_hindi_polish_payload(result, audio_path))
        except Exception as exc:  # noqa: BLE001 - HTTP API should return JSON errors.
            self._send_json({"error": repr(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_hindi_risk(self) -> None:
        try:
            from ramblefix.hindi_polish import detect_hindi_risk

            payload = self._read_json()
            audio_path = Path(str(payload.get("audio_path", ""))).expanduser().resolve()
            if not audio_path.exists():
                raise FileNotFoundError(f"missing audio_path: {audio_path}")
            draft_text = str(payload.get("draft_text", ""))
            low_confidence_threshold = float(payload.get("low_confidence_threshold", 0.50))
            result = detect_hindi_risk(
                audio_path,
                draft_text=draft_text,
                low_confidence_threshold=low_confidence_threshold,
            )
            self._send_json(_hindi_risk_payload(result, audio_path))
        except Exception as exc:  # noqa: BLE001 - HTTP API should return JSON errors.
            self._send_json({"error": repr(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_chinese_polish(self) -> None:
        try:
            from ramblefix.chinese_polish import polish_chinese_if_needed

            payload = self._read_json()
            audio_path = Path(str(payload.get("audio_path", ""))).expanduser().resolve()
            if not audio_path.exists():
                raise FileNotFoundError(f"missing audio_path: {audio_path}")
            draft_text = str(payload.get("draft_text", ""))
            force = bool(payload.get("force", False))
            result = polish_chinese_if_needed(
                audio_path,
                draft_text=draft_text,
                force=force,
            )
            self._send_json(_chinese_polish_payload(result, audio_path))
        except Exception as exc:  # noqa: BLE001 - HTTP API should return JSON errors.
            self._send_json({"error": repr(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_process_second_pass(self) -> None:
        try:
            payload = self._read_json()
            audio_path = Path(str(payload.get("audio_path", ""))).expanduser().resolve()
            if not audio_path.exists():
                raise FileNotFoundError(f"missing audio_path: {audio_path}")
            backend = str(payload.get("backend") or os.environ.get("RAMBLEFIX_PROCESS_SECOND_PASS_BACKEND") or "accurate_en")
            self._send_json(_process_second_pass_payload(audio_path, backend=backend))
        except Exception as exc:  # noqa: BLE001 - HTTP API should return JSON errors.
            self._send_json({"error": repr(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_hindi_async_polish(self) -> None:
        try:
            from ramblefix.hindi_chunk_polish import chunk_polish_hindi_if_needed

            payload = self._read_json()
            audio_path = Path(str(payload.get("audio_path", ""))).expanduser().resolve()
            if not audio_path.exists():
                raise FileNotFoundError(f"missing audio_path: {audio_path}")
            draft_text = str(payload.get("draft_text", ""))
            result = chunk_polish_hindi_if_needed(
                audio_path,
                draft_text=draft_text,
                low_confidence_threshold=float(payload.get("low_confidence_threshold", 0.50)),
                target_seconds=float(payload.get("target_seconds", 8.0)),
                min_seconds=float(payload.get("min_seconds", 5.0)),
                max_seconds=float(payload.get("max_seconds", 9.0)),
                lookaround_seconds=float(payload.get("lookaround_seconds", 1.5)),
                max_release_tail_seconds=float(payload.get("max_release_tail_seconds", 3.0)),
            )
            self._send_json(_hindi_async_polish_payload(result, audio_path))
        except Exception as exc:  # noqa: BLE001 - HTTP API should return JSON errors.
            self._send_json({"error": repr(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("RAMBLEFIX_SROTA_SERVER_QUIET", "1").strip().lower() in {"1", "true", "yes", "on"}:
            return
        super().log_message(fmt, *args)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object")
        return payload

    def _read_audio_request(self) -> tuple[Path, bool]:
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data"):
            import cgi

            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": content_type,
                    "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
                },
            )
            file_item = form["file"] if "file" in form else None
            if file_item is None or not getattr(file_item, "file", None):
                raise ValueError("multipart request missing file field")
            suffix = Path(str(getattr(file_item, "filename", "") or "audio.wav")).suffix or ".wav"
            with tempfile.NamedTemporaryFile(prefix="ramblefix-srota-inference-", suffix=suffix, delete=False) as tmp:
                tmp.write(file_item.file.read())
                return Path(tmp.name), True

        payload = self._read_json()
        audio_path = Path(str(payload.get("audio_path", ""))).expanduser().resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"missing audio_path: {audio_path}")
        return audio_path, False

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _hindi_polish_payload(result: Any, audio_path: Path) -> dict[str, Any]:
    return {
        "run_id": time.strftime("%Y%m%d-%H%M%S"),
        "audio": str(audio_path),
        "raw_text": result.raw_text,
        "text": result.text,
        "prompt_mode": result.text,
        "engine": f"hindi_polish_server:{result.engine}",
        "language": result.risk.language,
        "language_probability": result.risk.probability,
        "processor": "hindi-polish",
        "seconds": result.seconds,
        "quality": result.quality,
        "fallback_reason": "",
        "route": result.route,
        "risk": result.risk.risk,
        "risk_reasons": result.risk.reasons,
        "candidates": result.candidates,
        "error": result.error,
    }


def _hindi_risk_payload(result: Any, audio_path: Path) -> dict[str, Any]:
    return {
        "run_id": time.strftime("%Y%m%d-%H%M%S"),
        "audio": str(audio_path),
        "risk": result.risk,
        "language": result.language,
        "language_probability": result.probability,
        "engine": result.engine,
        "seconds": result.seconds,
        "risk_reasons": result.reasons,
        "route": "hindi_risk" if result.risk else "hindi_not_detected",
    }


def _chinese_polish_payload(result: Any, audio_path: Path) -> dict[str, Any]:
    return {
        "run_id": time.strftime("%Y%m%d-%H%M%S"),
        "audio": str(audio_path),
        "raw_text": result.raw_text,
        "text": result.text,
        "prompt_mode": result.text,
        "engine": f"chinese_polish_server:{result.engine}",
        "language": result.risk.language,
        "language_probability": result.risk.probability,
        "processor": "chinese-polish",
        "seconds": result.seconds,
        "quality": result.quality,
        "fallback_reason": "",
        "route": result.route,
        "risk": result.risk.risk,
        "chinese_risk": result.risk.risk,
        "risk_reasons": result.risk.reasons,
        "safe_update": result.safe_update,
        "reject_reasons": result.reject_reasons,
        "candidates": result.candidates,
        "error": result.error,
    }


def _process_second_pass_payload(audio_path: Path, *, backend: str) -> dict[str, Any]:
    from ramblefix.processing import process_transcript
    from ramblefix.quality import (
        is_blank_or_no_speech_transcript,
        is_degenerate_transcript,
        repeated_substring_score,
        wav_silence_metrics,
    )

    started = time.perf_counter()
    if backend == "whisper_cpp_translate":
        from ramblefix.external_asr import transcribe_whisper_cpp_translate

        transcript = transcribe_whisper_cpp_translate(audio_path)
    elif backend in {"accurate_en", "accurate_auto"}:
        from ramblefix.asr import ACCURATE_MLX_MODEL, transcribe_audio

        transcript = transcribe_audio(
            audio_path,
            model=ACCURATE_MLX_MODEL,
            language="en" if backend == "accurate_en" else None,
        )
    else:
        raise ValueError(f"unsupported process second-pass backend: {backend}")

    seconds = round(time.perf_counter() - started, 3)
    output = process_transcript(transcript.text, use_ollama=False)
    audio_quality = wav_silence_metrics(audio_path)
    blank_or_no_speech = (
        is_blank_or_no_speech_transcript(transcript.text)
        or bool(audio_quality.get("audio_probably_silent"))
    )
    quality = {
        "repeated_substring_score": repeated_substring_score(transcript.text),
        "degenerate": is_degenerate_transcript(transcript.text),
        "blank_or_no_speech": blank_or_no_speech,
        "char_count": len(transcript.text),
        **audio_quality,
    }
    return {
        "run_id": time.strftime("%Y%m%d-%H%M%S"),
        "audio": str(audio_path),
        "raw_text": transcript.text,
        "text": output.clean_transcript,
        "prompt_mode": output.prompt_mode,
        "engine": f"process_second_pass_server:{transcript.engine}",
        "language": transcript.language,
        "language_probability": None,
        "processor": output.processor,
        "seconds": seconds,
        "quality": quality,
        "fallback_reason": "",
        "route": f"process_second_pass:{backend}",
    }


def _hindi_async_polish_payload(result: Any, audio_path: Path) -> dict[str, Any]:
    return {
        "run_id": time.strftime("%Y%m%d-%H%M%S"),
        "audio": str(audio_path),
        "raw_text": result.raw_text,
        "text": result.text,
        "prompt_mode": result.text,
        "engine": f"hindi_async_polish_server:{result.engine}",
        "language": result.risk.language,
        "language_probability": result.risk.probability,
        "processor": "hindi-async-polish",
        "seconds": result.seconds,
        "quality": result.quality,
        "fallback_reason": "",
        "route": result.route,
        "risk": result.risk.risk,
        "risk_reasons": result.risk.reasons,
        "safe_update": result.safe_update,
        "release_tail_seconds": result.release_tail_seconds,
        "reject_reasons": result.reject_reasons,
        "chunks": result.chunks,
        "error": result.error,
    }


def _hindi_stream_payload(result: Any) -> dict[str, Any]:
    from ramblefix.glossary import apply_glossary

    text = apply_glossary(result.text)
    return {
        "run_id": result.run_id,
        "raw_text": result.raw_text,
        "text": text,
        "prompt_mode": text,
        "engine": "hindi_stream_server:mlx-qwen3-asr-chunked",
        "processor": "hindi-stream",
        "seconds": result.finish_wait_seconds,
        "quality": result.quality,
        "fallback_reason": "",
        "route": result.route,
        "risk": result.quality.get("hindi_risk", False),
        "risk_reasons": result.quality.get("risk_reasons", []),
        "safe_update": result.safe_update,
        "release_tail_seconds": result.release_tail_seconds,
        "finish_wait_seconds": result.finish_wait_seconds,
        "reject_reasons": result.reject_reasons,
        "chunks": result.chunks,
        "detector_chunks": result.detector_chunks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Srota/Qwen Hinglish sidecar.")
    parser.add_argument("--host", default=os.environ.get("RAMBLEFIX_SROTA_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("RAMBLEFIX_SROTA_SERVER_PORT", "8188")))
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(f"refusing non-loopback Srota server host: {args.host}")

    # Avoid client->server recursion when the sidecar inherits the caller env.
    os.environ.pop("RAMBLEFIX_SROTA_SERVER_URL", None)
    server = ThreadingHTTPServer((args.host, args.port), SrotaHandler)
    print(f"Srota server listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
