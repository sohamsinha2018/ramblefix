from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


FAST_MLX_MODEL = "mlx-community/whisper-tiny"
BALANCED_MLX_MODEL = "mlx-community/whisper-base-mlx"
ACCURATE_MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_MLX_MODEL = FAST_MLX_MODEL
DEFAULT_ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_ELEVENLABS_MODEL = "eleven_multilingual_v2"
DEFAULT_WISPR_DB = Path.home() / "Library" / "Application Support" / "Wispr Flow" / "flow.sqlite"
TEXT_FIELDS = {
    "formatted",
    "asr",
    "edited",
    "pasted",
    "default_asr",
    "fallback_asr",
    "default_formatted",
    "fallback_formatted",
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="ramblefix")
    sub = parser.add_subparsers(dest="command", required=True)

    audio = sub.add_parser("audio")
    audio.add_argument("path")
    audio.add_argument("--language")
    audio.add_argument("--model", default=DEFAULT_MLX_MODEL)
    audio.add_argument("--preset", choices=["fast", "balanced", "accurate"])
    audio.add_argument("--mode", choices=["clean", "prompt", "both"], default="both")
    audio.add_argument("--ollama-model", default="llama3.1:8b")
    audio.add_argument("--json", action="store_true")

    text = sub.add_parser("text")
    text.add_argument("path")
    text.add_argument("--mode", choices=["clean", "prompt", "both"], default="both")
    text.add_argument("--ollama-model", default="llama3.1:8b")

    record = sub.add_parser("record")
    record.add_argument("--seconds", type=int, default=30)
    record.add_argument("--output", default="recordings/recording.wav")
    record.add_argument("--language")
    record.add_argument("--model", default=DEFAULT_MLX_MODEL)
    record.add_argument("--preset", choices=["fast", "balanced", "accurate"])
    record.add_argument("--mode", choices=["clean", "prompt", "both"], default="both")
    record.add_argument("--ollama-model", default="llama3.1:8b")

    eval_parser = sub.add_parser("eval")
    eval_parser.add_argument("--output-dir", default="eval_runs/latest")
    eval_parser.add_argument("--provider", choices=["say", "elevenlabs"], default="say")
    eval_parser.add_argument("--case-set", choices=["english", "mixed"], default="english")
    eval_parser.add_argument(
        "--external-backends",
        help="Comma-separated optional local engines to include: meaning_router,ramblefix_engine_v1,ramblefix_hinglish_v1,ramblefix_multilingual_lab_v0,sensevoice_small,oriserve_hindi2hinglish_ggml,whisper_cpp_server_translate,whisper_cpp_translate_base,whisper_cpp_translate,whisper_cpp,whisperkit_cli,faster_whisper,faster_whisper_auto,parakeet_mlx,qwen3_asr_mlx,qwen3_asr_mlx_hinglish,mlx_whisper_large_v3_turbo_q4_transcribe,mlx_whisper_large_v3_turbo_q4_translate,mlx_whisper_large_v3_turbo_4bit_transcribe,mlx_whisper_large_v3_turbo_4bit_translate,mlx_whisper_large_v3_turbo_8bit_transcribe",
    )

    sweep = sub.add_parser("sweep-audio")
    sweep.add_argument("path")
    sweep.add_argument("--output-dir", default="eval_runs/audio-sweep")

    ludo_local = sub.add_parser("ludo-local-eval")
    ludo_local.add_argument("--metrics-dir", default="~/Downloads")
    ludo_local.add_argument("--output-dir", default="eval_runs/ludo-local")
    ludo_local.add_argument("--limit", type=int, default=10)
    ludo_local.add_argument("--since", default="")
    ludo_local.add_argument("--preset", default="accurate")
    ludo_local.add_argument("--language")
    ludo_local.add_argument("--quiet", action="store_true")
    ludo_local.add_argument("--no-resume", action="store_true")

    ludo_review = sub.add_parser("ludo-review-set")
    ludo_review.add_argument("--results", default="eval_runs/ludo-local-all/results.json")
    ludo_review.add_argument("--output-dir", default="eval_runs/ludo-review-set")
    ludo_review.add_argument("--corpus", default="eval_corpus/ramblefix_corpus.json")
    ludo_review.add_argument("--recordings-dir", default="recordings/ludo-review")
    ludo_review.add_argument("--count", type=int, default=50)
    ludo_review.add_argument("--no-corpus", action="store_true")

    corpus = sub.add_parser("eval-corpus")
    corpus.add_argument("--corpus", default="eval_corpus/ramblefix_corpus.json")
    corpus.add_argument("--output-dir", default="eval_runs/corpus")
    corpus.add_argument("--include-gemini", action="store_true")
    corpus.add_argument(
        "--external-backends",
        help="Comma-separated optional local engines to include: meaning_router,ramblefix_engine_v1,ramblefix_hinglish_v1,ramblefix_multilingual_lab_v0,sensevoice_small,oriserve_hindi2hinglish_ggml,whisper_cpp_server_translate,whisper_cpp_translate_base,whisper_cpp_translate,whisper_cpp,whisperkit_cli,faster_whisper,faster_whisper_auto,parakeet_mlx,qwen3_asr_mlx,qwen3_asr_mlx_hinglish,mlx_whisper_large_v3_turbo_q4_transcribe,mlx_whisper_large_v3_turbo_q4_translate,mlx_whisper_large_v3_turbo_4bit_transcribe,mlx_whisper_large_v3_turbo_4bit_translate,mlx_whisper_large_v3_turbo_8bit_transcribe",
    )
    corpus.add_argument(
        "--base-backends",
        default="hybrid_ludo,accurate_en,accurate_auto",
        help="Comma-separated built-in corpus backends to run first. Use 'none' to run only --external-backends.",
    )
    corpus.add_argument("--ids", help="Comma-separated corpus item ids to evaluate")
    corpus.add_argument(
        "--row-timeout-seconds",
        type=float,
        default=0.0,
        help="Abort one corpus item/backend row after this many seconds and write an error row. 0 disables the harness timeout.",
    )

    list_corpus = sub.add_parser("list-corpus")
    list_corpus.add_argument("--corpus", default="eval_corpus/ramblefix_corpus.json")

    set_gold = sub.add_parser("set-gold")
    set_gold.add_argument("id")
    set_gold.add_argument("gold")
    set_gold.add_argument("--corpus", default="eval_corpus/ramblefix_corpus.json")

    set_benchmark = sub.add_parser("set-benchmark")
    set_benchmark.add_argument("id")
    set_benchmark.add_argument("name")
    set_benchmark.add_argument("text")
    set_benchmark.add_argument("--corpus", default="eval_corpus/ramblefix_corpus.json")

    wispr_latest = sub.add_parser("wispr-latest")
    wispr_latest.add_argument("--db", default=str(DEFAULT_WISPR_DB))
    wispr_latest.add_argument("--limit", type=int, default=10)
    wispr_latest.add_argument("--field", choices=sorted(TEXT_FIELDS), default="formatted")
    wispr_latest.add_argument("--show-text", action="store_true")

    wispr_import = sub.add_parser("import-wispr")
    wispr_import.add_argument("id")
    wispr_import.add_argument("--row-id")
    wispr_import.add_argument("--name", default="wispr")
    wispr_import.add_argument("--field", choices=sorted(TEXT_FIELDS), default="formatted")
    wispr_import.add_argument("--db", default=str(DEFAULT_WISPR_DB))
    wispr_import.add_argument("--corpus", default="eval_corpus/ramblefix_corpus.json")

    if _env_truthy("RAMBLEFIX_ENABLE_CLOUD_EVAL_CLI", default=False):
        tts_elevenlabs = sub.add_parser("tts-elevenlabs")
        tts_elevenlabs.add_argument("text")
        tts_elevenlabs.add_argument("--output", default="eval_runs/tts/sample.mp3")
        tts_elevenlabs.add_argument("--voice-id", default=DEFAULT_ELEVENLABS_VOICE_ID)
        tts_elevenlabs.add_argument("--model-id", default=DEFAULT_ELEVENLABS_MODEL)
        tts_elevenlabs.add_argument("--wav", action="store_true")

    sidecar = sub.add_parser("sidecar")
    sidecar_sub = sidecar.add_subparsers(dest="sidecar_command", required=True)
    sidecar_status_parser = sidecar_sub.add_parser("status")
    sidecar_status_parser.add_argument("--json", action="store_true")
    sidecar_start_parser = sidecar_sub.add_parser("start")
    sidecar_start_parser.add_argument("--no-warm", action="store_true")
    sidecar_start_parser.add_argument("--timeout-seconds", type=float, default=20.0)
    sidecar_restart_parser = sidecar_sub.add_parser("restart")
    sidecar_restart_parser.add_argument("--no-warm", action="store_true")
    sidecar_restart_parser.add_argument("--timeout-seconds", type=float, default=20.0)
    sidecar_sub.add_parser("stop")
    sidecar_bench = sidecar_sub.add_parser("bench")
    sidecar_bench.add_argument("--audio", default="recordings/latest.wav")
    sidecar_bench.add_argument("--json", action="store_true")

    dictate_audio = sub.add_parser("dictate-audio")
    dictate_audio.add_argument("audio")
    dictate_audio.add_argument("--json", action="store_true")
    dictate_audio.add_argument("--no-cleanup", action="store_true")
    dictate_audio.add_argument("--skip-process-fallback", action="store_true")
    dictate_audio.add_argument("--ollama-model", default="llama3.1:8b")
    dictate_audio.add_argument("--history", action="store_true", help=argparse.SUPPRESS)

    meeting_transcribe_audio = sub.add_parser("meeting-transcribe-audio")
    meeting_transcribe_audio.add_argument("audio")
    meeting_transcribe_audio.add_argument("--json", action="store_true")
    meeting_transcribe_audio.add_argument("--output-dir", default="")
    meeting_transcribe_audio.add_argument("--chunk-seconds", type=float, default=30.0)
    meeting_transcribe_audio.add_argument("--mode", choices=["fast", "hinglish", "auto"], default="auto")
    meeting_transcribe_audio.add_argument("--skip-process-fallback", action="store_true")

    process_second_pass_audio = sub.add_parser("process-second-pass-audio")
    process_second_pass_audio.add_argument("audio")
    process_second_pass_audio.add_argument("--json", action="store_true")
    process_second_pass_audio.add_argument("--no-cleanup", action="store_true")
    process_second_pass_audio.add_argument("--ollama-model", default="llama3.1:8b")
    process_second_pass_audio.add_argument(
        "--backend",
        default=os.environ.get("RAMBLEFIX_PROCESS_SECOND_PASS_BACKEND", "accurate_en"),
        choices=["accurate_en", "accurate_auto", "whisper_cpp_translate"],
        help="Local backend for async second-pass repair.",
    )
    process_second_pass_audio.add_argument("--history", action="store_true", help=argparse.SUPPRESS)

    finalize_audio = sub.add_parser("finalize-audio")
    finalize_audio.add_argument("audio")
    finalize_audio.add_argument("--json", action="store_true")
    finalize_audio.add_argument("--no-cleanup", action="store_true")
    finalize_audio.add_argument("--ollama-model", default="llama3.1:8b")

    term_polish_audio = sub.add_parser("term-polish-audio")
    term_polish_audio.add_argument("audio")
    term_polish_audio.add_argument("--draft", default="")
    term_polish_audio.add_argument("--draft-file")
    term_polish_audio.add_argument("--timeout-seconds", type=float, default=45.0)
    term_polish_audio.add_argument("--json", action="store_true")

    hindi_risk_audio = sub.add_parser("hindi-risk-audio")
    hindi_risk_audio.add_argument("audio")
    hindi_risk_audio.add_argument("--draft", default="")
    hindi_risk_audio.add_argument("--draft-file")
    hindi_risk_audio.add_argument("--low-confidence-threshold", type=float, default=0.50)
    hindi_risk_audio.add_argument("--json", action="store_true")

    hindi_polish_audio = sub.add_parser("hindi-polish-audio")
    hindi_polish_audio.add_argument("audio")
    hindi_polish_audio.add_argument("--draft", default="")
    hindi_polish_audio.add_argument("--draft-file")
    hindi_polish_audio.add_argument("--low-confidence-threshold", type=float, default=0.50)
    hindi_polish_audio.add_argument("--force", action="store_true")
    hindi_polish_audio.add_argument("--json", action="store_true")

    chinese_polish_audio = sub.add_parser("chinese-polish-audio")
    chinese_polish_audio.add_argument("audio")
    chinese_polish_audio.add_argument("--draft", default="")
    chinese_polish_audio.add_argument("--draft-file")
    chinese_polish_audio.add_argument("--force", action="store_true")
    chinese_polish_audio.add_argument("--json", action="store_true")

    hindi_chunk_polish_audio = sub.add_parser("hindi-chunk-polish-audio")
    hindi_chunk_polish_audio.add_argument("audio")
    hindi_chunk_polish_audio.add_argument("--draft", default="")
    hindi_chunk_polish_audio.add_argument("--draft-file")
    hindi_chunk_polish_audio.add_argument("--target-seconds", type=float, default=8.0)
    hindi_chunk_polish_audio.add_argument("--min-seconds", type=float, default=5.0)
    hindi_chunk_polish_audio.add_argument("--max-seconds", type=float, default=9.0)
    hindi_chunk_polish_audio.add_argument("--lookaround-seconds", type=float, default=1.5)
    hindi_chunk_polish_audio.add_argument("--max-release-tail-seconds", type=float, default=3.0)
    hindi_chunk_polish_audio.add_argument("--json", action="store_true")

    hindi_async_polish_audio = sub.add_parser("hindi-async-polish-audio")
    hindi_async_polish_audio.add_argument("audio")
    hindi_async_polish_audio.add_argument("--draft", default="")
    hindi_async_polish_audio.add_argument("--draft-file")
    hindi_async_polish_audio.add_argument("--low-confidence-threshold", type=float, default=0.50)
    hindi_async_polish_audio.add_argument("--target-seconds", type=float, default=8.0)
    hindi_async_polish_audio.add_argument("--min-seconds", type=float, default=5.0)
    hindi_async_polish_audio.add_argument("--max-seconds", type=float, default=9.0)
    hindi_async_polish_audio.add_argument("--lookaround-seconds", type=float, default=1.5)
    hindi_async_polish_audio.add_argument("--max-release-tail-seconds", type=float, default=3.0)
    hindi_async_polish_audio.add_argument("--json", action="store_true")

    learn_phrase = sub.add_parser("learn-phrase")
    learn_phrase.add_argument("source")
    learn_phrase.add_argument("replacement")
    learn_phrase.add_argument("--note", default="User-approved local correction")
    learn_phrase.add_argument("--json", action="store_true")

    learn_term = sub.add_parser("learn-term")
    learn_term.add_argument("term")
    learn_term.add_argument("--alias", action="append", default=[])
    learn_term.add_argument("--source", default="manual")
    learn_term.add_argument("--json", action="store_true")

    learn_text = sub.add_parser("learn-from-text")
    learn_text.add_argument("text")
    learn_text.add_argument("--source", default="text")
    learn_text.add_argument("--min-count", type=int, default=1)
    learn_text.add_argument("--json", action="store_true")

    learn_history = sub.add_parser("learn-from-history")
    learn_history.add_argument("--history", default="logs/history.jsonl")
    learn_history.add_argument("--limit", type=int, default=300)
    learn_history.add_argument("--min-count", type=int, default=2)
    learn_history.add_argument("--skip-if-busy", action="store_true")
    learn_history.add_argument("--max-load-ratio", type=float, default=0.75)
    learn_history.add_argument("--no-corrections", action="store_true")
    learn_history.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.command == "audio":
        from ramblefix.asr import transcribe_audio

        transcript = transcribe_audio(args.path, model=_resolve_model(args), language=args.language)
        if args.json:
            print(json.dumps({"text": transcript.text, "engine": transcript.engine, "language": transcript.language}))
        else:
            _print_processed(transcript.text, args.mode, args.ollama_model)
    elif args.command == "text":
        raw = Path(args.path).read_text(encoding="utf-8")
        _print_processed(raw, args.mode, args.ollama_model)
    elif args.command == "learn-phrase":
        from ramblefix.glossary import add_phrase_fix, apply_glossary, dictionary_version

        learned = add_phrase_fix(args.source, args.replacement, note=args.note)
        payload = {
            **learned,
            "test_output": apply_glossary(args.source),
            "dictionary_version": dictionary_version(),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            action = "updated" if learned["updated"] else "learned"
            print(f"{action}: {learned['source']} -> {learned['replacement']}")
    elif args.command == "learn-term":
        from ramblefix.learning_memory import add_memory_term

        learned = add_memory_term(args.term, aliases=args.alias, source=args.source)
        if args.json:
            print(json.dumps(learned, ensure_ascii=False, indent=2))
        else:
            print(f"learned term: {learned['term']}")
    elif args.command == "learn-from-text":
        from ramblefix.learning_memory import learn_terms_from_text

        payload = learn_terms_from_text(args.text, source=args.source, min_count=args.min_count)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"learned {payload['learned']} terms from text")
    elif args.command == "learn-from-history":
        from ramblefix.learning_memory import learn_terms_from_history

        if args.skip_if_busy and _system_load_ratio() > args.max_load_ratio:
            payload = {"skipped": True, "reason": "system_busy", "load_ratio": round(_system_load_ratio(), 3)}
        else:
            payload = learn_terms_from_history(
                history_path=Path(args.history),
                limit=args.limit,
                min_count=args.min_count,
                learn_corrections=not args.no_corrections,
            )
            payload["skipped"] = False
            payload["load_ratio"] = round(_system_load_ratio(), 3)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if payload.get("skipped"):
                print(f"skipped: {payload['reason']}")
            else:
                print(f"learned {payload['learned']} terms from {payload['history_rows']} rows")
    elif args.command == "record":
        from ramblefix.asr import transcribe_audio
        from ramblefix.audio import record_microphone

        path = record_microphone(args.output, seconds=args.seconds)
        transcript = transcribe_audio(path, model=_resolve_model(args), language=args.language)
        _print_processed(transcript.text, args.mode, args.ollama_model)
    elif args.command == "eval":
        from ramblefix.eval import MIXED_LANGUAGE_CASES, run_eval

        cases = MIXED_LANGUAGE_CASES if args.case_set == "mixed" else None
        external_backends = (
            {item.strip() for item in args.external_backends.split(",") if item.strip()}
            if args.external_backends
            else None
        )
        results = run_eval(args.output_dir, provider=args.provider, cases=cases, external_backends=external_backends)
        for result in results:
            print(
                f"{result.case} {result.config}: "
                f"raw_WER={result.wer:.3f} corrected_WER={result.corrected_wer:.3f} "
                f"repeat={result.repeated_token_ratio:.3f} "
                f"time={result.seconds:.3f}s"
            )
    elif args.command == "sweep-audio":
        from ramblefix.eval import run_audio_sweep

        rows = run_audio_sweep(args.path, args.output_dir)
        for row in rows:
            print(
                f"{row['config']}: detected={row['detected_language']} "
                f"repeat={float(row['repeat']):.3f} time={float(row['seconds']):.3f}s"
            )
    elif args.command == "ludo-local-eval":
        from ramblefix.ludo_metrics import run_ludo_local_eval

        rows = run_ludo_local_eval(
            metrics_dir=args.metrics_dir,
            output_dir=args.output_dir,
            limit=args.limit,
            since=args.since,
            preset=args.preset,
            language=args.language,
            progress=not args.quiet,
            resume=not args.no_resume,
        )
        print(f"wrote {len(rows)} rows to {args.output_dir}")
    elif args.command == "ludo-review-set":
        from ramblefix.ludo_metrics import build_ludo_review_set

        rows = build_ludo_review_set(
            results_path=args.results,
            output_dir=args.output_dir,
            corpus_path=args.corpus,
            recordings_dir=args.recordings_dir,
            count=args.count,
            write_corpus=not args.no_corpus,
        )
        buckets: dict[str, int] = {}
        for row in rows:
            bucket = str(row["review_bucket"])
            buckets[bucket] = buckets.get(bucket, 0) + 1
        print(f"selected {len(rows)} rows into {args.output_dir}")
        print(json.dumps(buckets, indent=2))
    elif args.command == "eval-corpus":
        from ramblefix.eval import run_corpus_eval

        ids = {item.strip() for item in args.ids.split(",") if item.strip()} if args.ids else None
        external_backends = (
            {item.strip() for item in args.external_backends.split(",") if item.strip()}
            if args.external_backends
            else None
        )
        base_backends = None if args.base_backends == "default" else _parse_base_backends(args.base_backends)
        rows = run_corpus_eval(
            args.corpus,
            args.output_dir,
            include_gemini=args.include_gemini,
            external_backends=external_backends,
            base_backends=base_backends,
            ids=ids,
            row_timeout_seconds=args.row_timeout_seconds if args.row_timeout_seconds > 0 else None,
        )
        for row in rows:
            wer = "n/a" if row["wer"] is None else f"{float(row['wer']):.3f}"
            print(f"{row['id']} {row['backend']}: WER={wer} repeat={float(row['repeat']):.3f} time={float(row['seconds']):.3f}s")
    elif args.command == "list-corpus":
        from ramblefix.corpus import load_corpus

        for item in load_corpus(args.corpus):
            gold = "gold" if str(item.get("gold", "")).strip() else "missing-gold"
            print(f"{item['id']} [{gold}] {item['audio']}")
    elif args.command == "set-gold":
        from ramblefix.corpus import set_corpus_gold

        item = set_corpus_gold(args.id, args.gold, args.corpus)
        print(f"updated {item['id']}")
    elif args.command == "set-benchmark":
        from ramblefix.corpus import set_corpus_benchmark

        item = set_corpus_benchmark(args.id, args.name, args.text, args.corpus)
        print(f"updated {item['id']} benchmark {args.name}")
    elif args.command == "wispr-latest":
        from ramblefix.wispr import list_wispr_rows

        rows = list_wispr_rows(db_path=args.db, limit=args.limit, field=args.field)
        for row in rows:
            print(
                f"{row.transcript_id} | {row.timestamp} | status={row.status} "
                f"duration={row.duration}s words={row.num_words} "
                f"lang={row.detected_language or 'unknown'} latency={row.e2e_latency}ms app={row.app}"
            )
            if args.show_text:
                print(row.text)
                print()
    elif args.command == "import-wispr":
        from ramblefix.wispr import import_wispr_benchmark

        item, row = import_wispr_benchmark(
            item_id=args.id,
            transcript_id=args.row_id,
            benchmark_name=args.name,
            field=args.field,
            db_path=args.db,
            corpus_path=args.corpus,
        )
        print(
            f"updated {item['id']} benchmark {args.name} from Wispr row "
            f"{row.transcript_id} ({row.timestamp}, {len(row.text.split())} words)"
        )
    elif args.command == "tts-elevenlabs":
        from ramblefix.tts import synthesize_with_elevenlabs

        mp3 = synthesize_with_elevenlabs(
            args.text,
            args.output,
            voice_id=args.voice_id,
            model_id=args.model_id,
        )
        print(str(mp3))
        if args.wav:
            wav = mp3.with_suffix(".wav")
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "16000", "-ac", "1", str(wav)],
                check=True,
            )
            print(str(wav))
    elif args.command == "sidecar":
        from ramblefix.sidecar import restart as sidecar_restart
        from ramblefix.sidecar import start as sidecar_start
        from ramblefix.sidecar import status as sidecar_status
        from ramblefix.sidecar import stop as sidecar_stop

        if args.sidecar_command == "status":
            state = sidecar_status()
            _print_sidecar_state(state, as_json=args.json)
        elif args.sidecar_command == "start":
            state = sidecar_start(warm=not args.no_warm, timeout_seconds=args.timeout_seconds)
            _print_sidecar_state(state, as_json=False)
        elif args.sidecar_command == "restart":
            state = sidecar_restart(warm=not args.no_warm, timeout_seconds=args.timeout_seconds)
            _print_sidecar_state(state, as_json=False)
        elif args.sidecar_command == "stop":
            state = sidecar_stop()
            _print_sidecar_state(state, as_json=False)
        elif args.sidecar_command == "bench":
            from ramblefix.external_asr import transcribe_local_meaning_server_with_fallback

            started = time.perf_counter()
            transcript = transcribe_local_meaning_server_with_fallback(args.audio)
            payload = {
                "audio": args.audio,
                "engine": transcript.engine,
                "language": transcript.language,
                "text": transcript.text,
                "seconds": transcript.seconds,
                "wall_seconds": round(time.perf_counter() - started, 3),
            }
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"{payload['engine']} {payload['seconds']:.3f}s")
                print(payload["text"])
    elif args.command == "dictate-audio":
        from ramblefix.glossary import apply_glossary, dictionary_version
        from ramblefix.history import append_history_record, extract_fallback_reason
        from ramblefix.external_asr import transcribe_local_meaning_server_with_fallback
        from ramblefix.processing import process_transcript
        from ramblefix.quality import (
            is_blank_or_no_speech_transcript,
            is_degenerate_transcript,
            repeated_substring_score,
            wav_silence_metrics,
        )

        run_id = time.strftime("%Y%m%d-%H%M%S")
        started = time.perf_counter()
        transcript = transcribe_local_meaning_server_with_fallback(
            args.audio,
            skip_process_fallback=True if args.skip_process_fallback else None,
        )
        asr_seconds = round(time.perf_counter() - started, 3)
        if args.no_cleanup:
            text = apply_glossary(transcript.text)
            processor = "glossary" if text != transcript.text else "none"
            prompt_mode = transcript.text
        else:
            output = process_transcript(transcript.text, use_ollama=False, model=args.ollama_model)
            text = output.clean_transcript
            prompt_mode = output.prompt_mode
            processor = output.processor
        audio_quality = wav_silence_metrics(args.audio)
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
        route = _route_from_engine(transcript.engine)
        payload = {
            "run_id": run_id,
            "audio": args.audio,
            "raw_text": transcript.text,
            "text": text,
            "prompt_mode": prompt_mode,
            "engine": transcript.engine,
            "language": transcript.language,
            "processor": processor,
            "seconds": asr_seconds,
            "quality": quality,
            "fallback_reason": extract_fallback_reason(transcript.engine),
            "route": route,
        }
        if args.history:
            print("warning: --history is deprecated for dictate-audio; paste-aware history is written by the hotkey app", file=sys.stderr)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(text)
    elif args.command == "meeting-transcribe-audio":
        from ramblefix.meeting_engine import transcribe_meeting_audio

        output_dir = Path(args.output_dir) if args.output_dir else None
        transcript = transcribe_meeting_audio(
            args.audio,
            output_dir=output_dir,
            chunk_seconds=args.chunk_seconds,
            mode=args.mode,
            skip_process_fallback=args.skip_process_fallback,
        )
        payload = transcript.to_json()
        payload.update(
            {
                "raw_text": transcript.text,
                "text": transcript.text,
                "prompt_mode": transcript.text,
                "processor": "meeting-engine",
                "language": None,
                "quality": {
                    "audio_seconds": transcript.audio_seconds,
                    "chunk_seconds": transcript.chunk_seconds,
                    "chunk_count": len(transcript.segments),
                    "segment_routes": [segment.route for segment in transcript.segments],
                },
                "fallback_reason": "",
                "route": f"meeting_{transcript.mode}",
            }
        )
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(transcript.text)
    elif args.command == "process-second-pass-audio":
        from ramblefix.external_asr import transcribe_whisper_cpp_translate
        from ramblefix.glossary import dictionary_version
        from ramblefix.history import append_history_record
        from ramblefix.processing import process_transcript
        from ramblefix.quality import (
            is_blank_or_no_speech_transcript,
            is_degenerate_transcript,
            repeated_substring_score,
            wav_silence_metrics,
        )

        run_id = time.strftime("%Y%m%d-%H%M%S")
        started = time.perf_counter()
        if args.backend == "whisper_cpp_translate":
            transcript = transcribe_whisper_cpp_translate(args.audio)
        else:
            from ramblefix.asr import ACCURATE_MLX_MODEL, transcribe_audio

            language = "en" if args.backend == "accurate_en" else None
            transcript = transcribe_audio(args.audio, model=ACCURATE_MLX_MODEL, language=language)
        asr_seconds = round(time.perf_counter() - started, 3)
        if args.no_cleanup:
            text = transcript.text
            processor = "none"
            prompt_mode = transcript.text
        else:
            output = process_transcript(transcript.text, use_ollama=False, model=args.ollama_model)
            text = output.clean_transcript
            prompt_mode = output.prompt_mode
            processor = output.processor
        audio_quality = wav_silence_metrics(args.audio)
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
        payload = {
            "run_id": run_id,
            "audio": args.audio,
            "raw_text": transcript.text,
            "text": text,
            "prompt_mode": prompt_mode,
            "engine": transcript.engine,
            "language": transcript.language,
            "processor": processor,
            "seconds": asr_seconds,
            "quality": quality,
            "fallback_reason": "",
            "route": f"process_second_pass:{args.backend}",
        }
        if args.history:
            append_history_record(
                source="process_second_pass",
                audio=args.audio,
                raw_text=transcript.text,
                text=text,
                prompt_mode=prompt_mode,
                engine=transcript.engine,
                seconds=asr_seconds,
                processor=processor,
                metadata={"quality": quality, "dictionary_version": dictionary_version()},
            )
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(text)
    elif args.command == "finalize-audio":
        from ramblefix.engine_router import transcribe_ramblefix_hinglish_v1
        from ramblefix.processing import process_transcript
        from ramblefix.quality import is_degenerate_transcript, repeated_substring_score

        run_id = time.strftime("%Y%m%d-%H%M%S")
        started = time.perf_counter()
        routed = transcribe_ramblefix_hinglish_v1(args.audio)
        finalizer_seconds = round(time.perf_counter() - started, 3)
        if args.no_cleanup:
            text = routed.text
            processor = "none"
            prompt_mode = routed.text
        else:
            output = process_transcript(routed.text, use_ollama=False, model=args.ollama_model)
            text = output.clean_transcript
            prompt_mode = output.prompt_mode
            processor = output.processor
        quality = {
            "repeated_substring_score": repeated_substring_score(routed.text),
            "degenerate": is_degenerate_transcript(routed.text),
            "char_count": len(routed.text),
            "risk_reasons": routed.risk_reasons,
            "route": routed.route,
        }
        payload = {
            "run_id": run_id,
            "audio": args.audio,
            "raw_text": routed.text,
            "text": text,
            "prompt_mode": prompt_mode,
            "engine": routed.engine,
            "language": None,
            "processor": processor,
            "seconds": finalizer_seconds,
            "quality": quality,
            "fallback_reason": "",
            "route": routed.route,
            "risk_reasons": routed.risk_reasons,
            "candidates": [
                {
                    "source": candidate.source,
                    "text": candidate.text,
                    "seconds": candidate.seconds,
                    "engine": candidate.engine,
                    "error": candidate.error,
                    "language": candidate.language,
                    "language_probability": candidate.language_probability,
                    "risk": candidate.risk,
                }
                for candidate in routed.candidates
            ],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(text)
    elif args.command == "term-polish-audio":
        from ramblefix.term_polish import polish_terms_with_auto

        draft = args.draft
        if args.draft_file:
            draft = Path(args.draft_file).read_text(encoding="utf-8")
        started = time.perf_counter()
        result = polish_terms_with_auto(
            args.audio,
            draft_text=draft,
            timeout_seconds=args.timeout_seconds,
        )
        payload = {
            "run_id": time.strftime("%Y%m%d-%H%M%S"),
            "audio": args.audio,
            "raw_text": result.raw_auto_text,
            "text": result.text,
            "prompt_mode": result.text,
            "engine": result.engine,
            "language": None,
            "processor": "term-polish",
            "seconds": result.seconds or round(time.perf_counter() - started, 3),
            "quality": {
                "term_polish_risk": result.risk,
                "risk_reasons": result.risk_reasons,
                "merge_rules": result.merge_rules,
                "changed": result.changed,
                "auto_seconds": result.auto_seconds,
                "error": result.error,
            },
            "fallback_reason": "",
            "route": result.route,
            "risk_reasons": result.risk_reasons,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(result.text)
    elif args.command == "hindi-risk-audio":
        from ramblefix.hindi_polish import detect_hindi_risk

        draft = args.draft
        if args.draft_file:
            draft = Path(args.draft_file).read_text(encoding="utf-8")
        result = detect_hindi_risk(
            args.audio,
            draft_text=draft,
            low_confidence_threshold=args.low_confidence_threshold,
        )
        payload = {
            "audio": args.audio,
            "risk": result.risk,
            "language": result.language,
            "language_probability": result.probability,
            "engine": result.engine,
            "seconds": result.seconds,
            "risk_reasons": result.reasons,
            "route": "hindi_risk" if result.risk else "hindi_not_detected",
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("risk" if result.risk else "no-risk")
    elif args.command == "hindi-polish-audio":
        from ramblefix.hindi_polish import polish_hindi_if_needed

        draft = args.draft
        if args.draft_file:
            draft = Path(args.draft_file).read_text(encoding="utf-8")
        server_url = os.environ.get("RAMBLEFIX_HINDI_POLISH_SERVER_URL", "").strip()
        payload = None
        if server_url:
            try:
                payload = _hindi_polish_server_payload(
                    server_url=server_url,
                    audio=args.audio,
                    draft_text=draft,
                    low_confidence_threshold=args.low_confidence_threshold,
                    force=args.force,
                )
            except Exception:
                if _env_truthy("RAMBLEFIX_HINDI_POLISH_SERVER_REQUIRED", default=False):
                    raise
        if payload is None:
            result = polish_hindi_if_needed(
                args.audio,
                draft_text=draft,
                low_confidence_threshold=args.low_confidence_threshold,
                force=args.force,
            )
            payload = _hindi_polish_payload(args.audio, result)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(payload.get("text", ""))
    elif args.command == "chinese-polish-audio":
        from ramblefix.chinese_polish import polish_chinese_if_needed

        draft = args.draft
        if args.draft_file:
            draft = Path(args.draft_file).read_text(encoding="utf-8")
        result = polish_chinese_if_needed(
            args.audio,
            draft_text=draft,
            force=args.force,
        )
        payload = _chinese_polish_payload(args.audio, result)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(payload.get("text", ""))
    elif args.command == "hindi-chunk-polish-audio":
        from ramblefix.hindi_chunk_polish import chunk_polish_audio

        draft = args.draft
        if args.draft_file:
            draft = Path(args.draft_file).read_text(encoding="utf-8")
        result = chunk_polish_audio(
            args.audio,
            draft_text=draft,
            target_seconds=args.target_seconds,
            min_seconds=args.min_seconds,
            max_seconds=args.max_seconds,
            lookaround_seconds=args.lookaround_seconds,
            max_release_tail_seconds=args.max_release_tail_seconds,
        )
        payload = {
            "run_id": time.strftime("%Y%m%d-%H%M%S"),
            "audio": args.audio,
            "raw_text": result.text,
            "text": result.text,
            "prompt_mode": result.text,
            "engine": result.engine,
            "language": None,
            "language_probability": None,
            "processor": "hindi-chunk-polish",
            "seconds": result.seconds,
            "quality": result.quality,
            "fallback_reason": "",
            "route": "hindi_chunk_polish_safe" if result.safe_update else "hindi_chunk_polish_saved",
            "safe_update": result.safe_update,
            "release_tail_seconds": result.release_tail_seconds,
            "reject_reasons": result.reject_reasons,
            "chunks": result.chunks,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(result.text)
    elif args.command == "hindi-async-polish-audio":
        from ramblefix.hindi_chunk_polish import chunk_polish_hindi_if_needed

        draft = args.draft
        if args.draft_file:
            draft = Path(args.draft_file).read_text(encoding="utf-8")
        server_url = os.environ.get("RAMBLEFIX_HINDI_POLISH_SERVER_URL", "").strip()
        payload = None
        if server_url:
            try:
                payload = _hindi_async_polish_server_payload(
                    server_url=server_url,
                    audio=args.audio,
                    draft_text=draft,
                    low_confidence_threshold=args.low_confidence_threshold,
                    target_seconds=args.target_seconds,
                    min_seconds=args.min_seconds,
                    max_seconds=args.max_seconds,
                    lookaround_seconds=args.lookaround_seconds,
                    max_release_tail_seconds=args.max_release_tail_seconds,
                )
            except Exception:
                if _env_truthy("RAMBLEFIX_HINDI_POLISH_SERVER_REQUIRED", default=False):
                    raise
        if payload is None:
            result = chunk_polish_hindi_if_needed(
                args.audio,
                draft_text=draft,
                low_confidence_threshold=args.low_confidence_threshold,
                target_seconds=args.target_seconds,
                min_seconds=args.min_seconds,
                max_seconds=args.max_seconds,
                lookaround_seconds=args.lookaround_seconds,
                max_release_tail_seconds=args.max_release_tail_seconds,
            )
            payload = _hindi_async_polish_payload(args.audio, result)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(payload.get("text", ""))


def _resolve_model(args: argparse.Namespace) -> str:
    if args.preset == "fast":
        return FAST_MLX_MODEL
    if args.preset == "balanced":
        return BALANCED_MLX_MODEL
    if args.preset == "accurate":
        return ACCURATE_MLX_MODEL
    return args.model


def _hindi_polish_payload(audio: str, result: object) -> dict[str, object]:
    return {
        "run_id": time.strftime("%Y%m%d-%H%M%S"),
        "audio": audio,
        "raw_text": result.raw_text,
        "text": result.text,
        "prompt_mode": result.text,
        "engine": result.engine,
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


def _chinese_polish_payload(audio: str, result: object) -> dict[str, object]:
    return {
        "run_id": time.strftime("%Y%m%d-%H%M%S"),
        "audio": audio,
        "raw_text": result.raw_text,
        "text": result.text,
        "prompt_mode": result.text,
        "engine": result.engine,
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


def _hindi_polish_server_payload(
    *,
    server_url: str,
    audio: str,
    draft_text: str,
    low_confidence_threshold: float,
    force: bool,
) -> dict[str, object]:
    import requests

    started = time.perf_counter()
    response = requests.post(
        server_url.rstrip("/") + "/hindi-polish",
        json={
            "audio_path": str(Path(audio).expanduser().resolve()),
            "draft_text": draft_text,
            "low_confidence_threshold": low_confidence_threshold,
            "force": force,
        },
        timeout=float(os.environ.get("RAMBLEFIX_HINDI_POLISH_SERVER_TIMEOUT_SECONDS", "90")),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Hindi polish server returned non-object JSON")
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    quality = payload.setdefault("quality", {})
    if isinstance(quality, dict):
        quality["server_roundtrip_seconds"] = round(time.perf_counter() - started, 3)
    return payload


def _hindi_async_polish_payload(audio: str, result: object) -> dict[str, object]:
    return {
        "run_id": time.strftime("%Y%m%d-%H%M%S"),
        "audio": audio,
        "raw_text": result.raw_text,
        "text": result.text,
        "prompt_mode": result.text,
        "engine": result.engine,
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


def _hindi_async_polish_server_payload(
    *,
    server_url: str,
    audio: str,
    draft_text: str,
    low_confidence_threshold: float,
    target_seconds: float,
    min_seconds: float,
    max_seconds: float,
    lookaround_seconds: float,
    max_release_tail_seconds: float,
) -> dict[str, object]:
    import requests

    started = time.perf_counter()
    response = requests.post(
        server_url.rstrip("/") + "/hindi-async-polish",
        json={
            "audio_path": str(Path(audio).expanduser().resolve()),
            "draft_text": draft_text,
            "low_confidence_threshold": low_confidence_threshold,
            "target_seconds": target_seconds,
            "min_seconds": min_seconds,
            "max_seconds": max_seconds,
            "lookaround_seconds": lookaround_seconds,
            "max_release_tail_seconds": max_release_tail_seconds,
        },
        timeout=float(os.environ.get("RAMBLEFIX_HINDI_POLISH_SERVER_TIMEOUT_SECONDS", "90")),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Hindi async polish server returned non-object JSON")
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    quality = payload.setdefault("quality", {})
    if isinstance(quality, dict):
        quality["server_roundtrip_seconds"] = round(time.perf_counter() - started, 3)
    return payload


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_base_backends(value: str) -> list[str]:
    if value.strip().lower() in {"", "none"}:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _route_from_engine(engine: str) -> str:
    if "process_fallback_skipped" in engine:
        return "fast_server_process_fallback_skipped"
    if "fallback_reason=" in engine:
        return "process_fallback"
    if engine == "whisper.cpp.server.translate":
        return "fast_server_translate"
    if engine.startswith("whisper.cpp"):
        return "local_whisper_cpp"
    return engine or "unknown"


def _system_load_ratio() -> float:
    try:
        import os

        cpus = os.cpu_count() or 1
        return float(os.getloadavg()[0]) / float(cpus)
    except Exception:
        return 0.0


def _print_processed(raw_text: str, mode: str, ollama_model: str) -> None:
    from ramblefix.processing import process_transcript

    output = process_transcript(raw_text, model=ollama_model)
    if mode in {"clean", "both"}:
        print("\n# Clean Transcript\n")
        print(output.clean_transcript)
    if mode in {"prompt", "both"}:
        print("\n# Prompt Mode\n")
        print(output.prompt_mode)
    print(f"\nProcessor: {output.processor}")


def _print_sidecar_state(state: object, *, as_json: bool) -> None:
    from ramblefix.sidecar import as_dict as sidecar_as_dict
    from ramblefix.sidecar import state_to_json as sidecar_state_to_json

    if as_json:
        print(sidecar_state_to_json(state))
        return
    payload = sidecar_as_dict(state)
    print(
        f"{payload['status']} ready={payload['ready']} port_open={payload['port_open']} "
        f"owned={payload['owned']} warmed={payload['warmed']} pid={payload['pid'] or '-'} url={payload['url']}"
    )
    if payload.get("error"):
        print(f"error={payload['error']}")


if __name__ == "__main__":
    main()
