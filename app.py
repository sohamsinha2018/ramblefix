from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import streamlit as st
import numpy as np
import soundfile as sf

from ramblefix.audio import record_microphone_monitored, recorder_backend
from ramblefix.corpus import DEFAULT_CORPUS_PATH, append_corpus_item, load_corpus, set_corpus_benchmark, set_corpus_gold
from ramblefix.debug import RunLogger, inspect_audio
from ramblefix.gemini_asr import transcribe_gemini_audio
from ramblefix.glossary import dictionary_version
from ramblefix.history import append_history_record, compact_sidecar_state, extract_fallback_reason
from ramblefix.ludo_asr import transcribe_hybrid_ludo
from ramblefix.external_asr import (
    transcribe_local_meaning_server_with_fallback,
    transcribe_whisper_cpp_server_translate,
    transcribe_whisper_cpp_translate,
    transcribe_whisper_cpp_translate_base,
)
from ramblefix.meaning_router import transcribe_meaning_router
from ramblefix.processing import process_transcript
from ramblefix.quality import is_degenerate_transcript, repeated_substring_score
from ramblefix.asr import ACCURATE_MLX_MODEL, BALANCED_MLX_MODEL, FAST_MLX_MODEL, transcribe_audio
from ramblefix.subprocess_asr import transcribe_with_timeout
from ramblefix.sidecar import as_dict as sidecar_as_dict
from ramblefix.sidecar import status as sidecar_status


st.set_page_config(page_title="RambleFix", layout="wide")

st.title("RambleFix")
st.caption("Local mixed-language voice capture for builders")


def _latest_eval_drafts() -> dict[str, list[dict[str, object]]]:
    path = Path("eval_runs/corpus/corpus_results.json")
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        text = str(row.get("actual", "")).strip()
        if not text:
            continue
        grouped.setdefault(str(row["id"]), []).append(row)
    return grouped


def _best_draft(rows: list[dict[str, object]]) -> str:
    for row in rows:
        text = str(row.get("actual", "")).strip()
        if text and text.lower() != "unclear" and float(row.get("repeat") or 0.0) < 0.2:
            return text
    return ""


def _tail_audio_summary(path: Path) -> str:
    try:
        audio, samplerate = sf.read(path)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio[:, 0]
        tail = audio[-5 * samplerate :] if len(audio) > 5 * samplerate else audio
        rms = float(np.sqrt(np.mean(tail**2))) if len(tail) else 0.0
        peak = float(np.max(np.abs(tail))) if len(tail) else 0.0
        dbfs = 20 * np.log10(max(rms, 1e-12))
        return f"last 5s rms={rms:.4f}, peak={peak:.4f}, dbFS={dbfs:.1f}"
    except Exception:
        return ""


def _audio_path_for_item(item: dict[str, object]) -> Path:
    audio_path = Path(str(item["audio"]))
    if not audio_path.is_absolute():
        audio_path = DEFAULT_CORPUS_PATH.parent.parent / audio_path
    return audio_path


def _ludo_review_items() -> list[dict[str, object]]:
    return [
        item
        for item in load_corpus(DEFAULT_CORPUS_PATH)
        if str(item.get("category", "")).strip().lower() == "ludo_hinglish"
    ]


def render_ludo_labeler() -> None:
    st.subheader("Ludo Gold Labeler")
    st.caption("Click play, type what you actually hear, save, move to the next clip.")

    all_items = _ludo_review_items()
    if not all_items:
        st.info("No Ludo review clips found. Run `python -m ramblefix.cli ludo-review-set` first.")
        return

    missing_items = [item for item in all_items if not str(item.get("gold", "")).strip()]
    done_count = len(all_items) - len(missing_items)
    st.progress(done_count / len(all_items), text=f"{done_count}/{len(all_items)} labeled")

    bucket_names = sorted({str(item.get("review_bucket", "")).strip() for item in all_items if item.get("review_bucket")})
    bucket = st.selectbox("Bucket", ["All"] + bucket_names, key="label_bucket")
    candidates = missing_items
    if bucket != "All":
        candidates = [item for item in candidates if str(item.get("review_bucket", "")) == bucket]

    if not candidates:
        st.success("No missing-gold clips in this bucket.")
        return

    if "ludo_label_index" not in st.session_state:
        st.session_state.ludo_label_index = 0
    if st.session_state.ludo_label_index >= len(candidates):
        st.session_state.ludo_label_index = 0

    item = candidates[st.session_state.ludo_label_index]
    item_id = str(item["id"])
    audio_path = _audio_path_for_item(item)
    st.markdown(f"**{item_id}**")
    st.caption(f"Bucket: `{item.get('review_bucket', 'unknown')}` · {st.session_state.ludo_label_index + 1}/{len(candidates)} remaining in view")

    if audio_path.exists():
        st.audio(str(audio_path))
    else:
        st.error(f"Missing audio: {audio_path}")

    text_key = f"label-gold-{item_id}"
    if text_key not in st.session_state:
        st.session_state[text_key] = str(item.get("gold", ""))
    gold = st.text_area(
        "What did you hear?",
        key=text_key,
        height=140,
        placeholder="Type the exact spoken words. Roman Hindi/Hinglish is fine.",
    )

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        if st.button("Save & Next", type="primary", disabled=not gold.strip()):
            set_corpus_gold(item_id, gold.strip(), DEFAULT_CORPUS_PATH)
            st.session_state.ludo_label_index = min(st.session_state.ludo_label_index, max(len(candidates) - 2, 0))
            st.success(f"Saved {item_id}")
            st.rerun()
    with col2:
        if st.button("Skip"):
            st.session_state.ludo_label_index = (st.session_state.ludo_label_index + 1) % len(candidates)
            st.rerun()

    benchmarks = item.get("benchmarks") if isinstance(item.get("benchmarks"), dict) else {}
    with st.expander("Hints, not truth", expanded=False):
        st.caption("Use your ears. These are only hints.")
        st.text_area("Old Ludo reference", value=str(benchmarks.get("ludo_old_reference", "")), height=80, disabled=True)
        st.text_area("Local accurate Hindi output", value=str(benchmarks.get("local_accurate_hi", "")), height=100, disabled=True)


def render_corpus_panel() -> None:
    with st.expander("Saved audio clips / Eval corpus", expanded=True):
        st.caption("Play a clip, correct the draft into Roman Hinglish/English, then save gold. You do not need Devanagari.")
        latest_drafts = _latest_eval_drafts()
        corpus_items = load_corpus(DEFAULT_CORPUS_PATH)
        if not corpus_items:
            st.info("No saved clips yet. Click Record and process once; the WAV will be saved here automatically.")
            return

        missing_gold = sum(1 for item in corpus_items if not str(item.get("gold", "")).strip())
        st.markdown(f"**{len(corpus_items)} saved clips** · **{missing_gold} need gold transcript**")
        for item in reversed(corpus_items[-8:]):
            item_id = str(item["id"])
            audio_path = Path(str(item["audio"]))
            if not audio_path.is_absolute():
                audio_path = DEFAULT_CORPUS_PATH.parent.parent / audio_path
            st.markdown(f"**{item_id}** `{item.get('source', '')}` `{item.get('workflow', '')}`")
            if audio_path.exists():
                st.audio(str(audio_path))
                tail_summary = _tail_audio_summary(audio_path)
                if tail_summary:
                    st.caption(tail_summary)
            else:
                st.warning(f"Missing audio: {audio_path}")

            drafts = latest_drafts.get(item_id, [])
            draft_text = _best_draft(drafts)
            editor_key = f"gold-{item_id}"
            if editor_key not in st.session_state:
                st.session_state[editor_key] = str(item.get("gold", "") or draft_text)

            gold = st.text_area(
                "Editable gold transcript",
                key=editor_key,
                height=120,
                placeholder="Roman Hinglish is fine, e.g. mujhe samajhna hai ki how can I move from idea to execution fast",
            )
            if st.button("Save gold", key=f"save-gold-{item_id}"):
                set_corpus_gold(item_id, gold, DEFAULT_CORPUS_PATH)
                st.success(f"Saved gold for {item_id}")

            benchmarks = item.get("benchmarks") if isinstance(item.get("benchmarks"), dict) else {}
            wispr_key = f"wispr-{item_id}"
            if wispr_key not in st.session_state:
                st.session_state[wispr_key] = str(benchmarks.get("wispr", ""))
            wispr_text = st.text_area(
                "Wispr Flow transcript benchmark",
                key=wispr_key,
                height=90,
                placeholder="Paste or dictate Wispr's output for this same test here",
            )
            if st.button("Save Wispr benchmark", key=f"save-wispr-{item_id}"):
                set_corpus_benchmark(item_id, "wispr", wispr_text, DEFAULT_CORPUS_PATH)
                st.success(f"Saved Wispr benchmark for {item_id}")

            if drafts:
                with st.expander("ASR drafts to correct", expanded=not bool(item.get("gold"))):
                    for row in drafts:
                        backend = row.get("backend")
                        repeat = float(row.get("repeat") or 0.0)
                        seconds = float(row.get("seconds") or 0.0)
                        actual = str(row.get("actual", "")).strip()
                        st.caption(f"{backend} | repeat={repeat:.3f} | {seconds:.2f}s")
                        st.text_area(
                            "Draft",
                            value=actual or "(empty)",
                            key=f"draft-{item_id}-{backend}",
                            height=90,
                            disabled=True,
                            label_visibility="collapsed",
                        )
                        if actual and st.button("Use this as editable gold", key=f"use-draft-{item_id}-{backend}"):
                            st.session_state[editor_key] = actual
                            st.rerun()
            else:
                st.caption("Run `python -m ramblefix.cli eval-corpus --output-dir eval_runs/corpus` to generate editable drafts.")

page = st.sidebar.radio("Page", ["Ludo Labeler", "RambleFix App"], index=0)
if page == "Ludo Labeler":
    render_ludo_labeler()
    st.stop()

MODE_CONFIG = {
    "Real-time Prompt": {
        "seconds": 10,
        "model": ACCURATE_MLX_MODEL,
        "ollama": False,
        "language": "English/Hinglish",
        "description": "Short thinking-out-loud snippets. Stable mode uses accurate ASR; fast is experimental.",
    },
    "Dictation": {
        "seconds": 45,
        "model": BALANCED_MLX_MODEL,
        "ollama": False,
        "language": "English/Hinglish",
        "description": "Short-to-medium speech where transcript quality matters.",
    },
    "Meeting": {
        "seconds": 300,
        "model": ACCURATE_MLX_MODEL,
        "ollama": True,
        "language": "Auto",
        "description": "Long recordings. Slower transcription and cleanup are acceptable.",
    },
}

with st.sidebar:
    st.header("Settings")
    workflow = st.radio("Workflow", list(MODE_CONFIG), horizontal=False)
    config = MODE_CONFIG[workflow]
    st.caption(config["description"])
    input_type = st.radio("Input", ["Speak", "Audio file", "Transcript text"], horizontal=False)
    asr_backend = st.selectbox(
        "ASR backend",
        [
            "Local Meaning Server (fast)",
            "Local Meaning (whisper.cpp)",
            "Local Meaning Fast (base experimental)",
            "Meaning Router (experimental)",
            "Local Whisper",
            "Hybrid Ludo-style",
            "Gemini audio",
        ],
        index=0,
    )
    if asr_backend == "Local Meaning Server (fast)":
        state = sidecar_status()
        if state.ready:
            st.caption(f"Sidecar ready on {state.url}.")
        else:
            st.caption("Sidecar will auto-start on first dictation; process whisper.cpp fallback is used if startup fails.")
    language_options = ["Auto", "English/Hinglish", "Hindi"]
    language_label = st.selectbox(
        "ASR language",
        language_options,
        index=language_options.index(config["language"]),
    )
    language_map = {"Auto": None, "English/Hinglish": "en", "Hindi": "hi"}
    language = language_map[language_label]
    model_options = {
        "Fast experimental": FAST_MLX_MODEL,
        "Balanced": BALANCED_MLX_MODEL,
        "Stable": ACCURATE_MLX_MODEL,
        "Custom": "",
    }
    default_label = next(label for label, value in model_options.items() if value == config["model"])
    model_label = st.selectbox("ASR model", list(model_options), index=list(model_options).index(default_label))
    if model_label == "Custom":
        model = st.text_input("Custom MLX Whisper model", value=config["model"])
    else:
        model = model_options[model_label]
    use_ollama = st.checkbox("Use local Ollama cleanup", value=config["ollama"])
    ollama_model = st.text_input("Ollama model", value="llama3.1:8b")
    use_hymt = st.checkbox("Use Tencent HY-MT normalization", value=False)
    hymt_model = st.text_input("HY-MT Ollama model", value="hymt-1.8b")
    gemini_key = st.text_input("Gemini API key", value="", type="password")
    debug_mode = st.checkbox("Debug mode", value=False)
    audit_history = st.checkbox("Save transcript audit history locally", value=True)
    retain_audio = st.checkbox("Retain audio for labeling/evals", value=True)
    if not audit_history:
        st.caption("Transcript text will not be written to history or run logs.")
    if not retain_audio:
        st.caption("Audio is temporary for this run and will not be added to the eval corpus.")
    if asr_backend == "Gemini audio":
        st.warning("Gemini audio sends audio to a cloud API. Use local backends for company data.")
    if use_hymt:
        st.warning("HY-MT is experimental here. The compact GGUF currently fails in this Ollama build.")

raw_text = ""
transcript_meta = ""
timings: list[str] = []
debug_rows: list[tuple[str, object]] = []
log_path: Path | None = None
active_audio_path: Path | None = None
active_asr_engine = ""
active_quality: dict[str, object] = {}
active_sidecar_state: dict[str, object] = {}

if input_type == "Speak":
    max_seconds = 180 if workflow != "Meeting" else 900
    seconds = st.slider("Recording length", min_value=5, max_value=max_seconds, value=min(config["seconds"], max_seconds), step=5)
    st.caption("Uses your default microphone. macOS may ask for microphone permission on first run.")
    st.caption(f"Recorder backend: {recorder_backend()}")
    if st.button("Record and process", type="primary"):
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        logger = RunLogger(run_id)
        log_path = logger.path
        output_dir = Path("recordings") if retain_audio else Path("logs/tmp_audio")
        audio_path = output_dir / f"{run_id}.wav"
        logger.event(
            "config",
            workflow=workflow,
            input_type=input_type,
            seconds=seconds,
            language_label=language_label,
            language=language,
            model_label=model_label,
            model=model,
            asr_backend=asr_backend,
            recorder_backend=recorder_backend(),
            use_ollama=use_ollama,
            use_hymt=use_hymt,
        )
        logger.event(
            "recording_start",
            audio_path=str(audio_path),
            seconds=seconds,
        )
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            active_audio_path = audio_path
        except Exception as exc:
            logger.event("setup_error", error=repr(exc))
            if audit_history:
                try:
                    append_history_record(
                        run_id=run_id,
                        mode=workflow,
                        audio_path=audio_path,
                        raw_text="",
                        corrected_text="",
                        dictionary_version=dictionary_version(),
                        timings=timings,
                        offline_mode=asr_backend != "Gemini audio",
                        status="failed",
                        error_type="setup_error",
                    )
                except Exception as history_exc:
                    logger.event("history_error", error=repr(history_exc))
            st.error(f"Setup failed: {exc}")
            st.stop()

        progress = st.progress(0.0, text="Recording: 0.0s")
        level_slot = st.empty()

        def update_recording_progress(elapsed: float, rms: float, peak: float) -> None:
            progress.progress(
                min(elapsed / seconds, 1.0),
                text=f"Recording: {elapsed:.1f}s / {seconds}s",
            )
            level_slot.caption(f"mic rms={rms:.4f} peak={peak:.4f}")

        with st.status(f"Recording {seconds} seconds from microphone...", expanded=True):
            started = time.perf_counter()
            try:
                record_microphone_monitored(
                    audio_path,
                    seconds=seconds,
                    on_progress=update_recording_progress,
                )
                progress.progress(1.0, text="Recording complete")
                timings.append(f"record={time.perf_counter() - started:.2f}s")
                stats = inspect_audio(audio_path)
                logger.event("audio_stats", audio_stats=stats)
                debug_rows.append(("audio_stats", stats))
                if retain_audio:
                    latest_path = output_dir / "latest.wav"
                    latest_path.write_bytes(audio_path.read_bytes())
                    corpus_item = append_corpus_item(
                        item_id=run_id,
                        audio_path=audio_path,
                        source="mic",
                        workflow=workflow,
                        notes="Auto-captured from Streamlit mic recording. Fill gold once this clip is understood.",
                    )
                    logger.event("corpus_item", corpus_item=corpus_item)
                    debug_rows.append(("corpus_item", corpus_item))
            except Exception as exc:
                logger.event("capture_error", error=repr(exc))
                if audit_history:
                    try:
                        append_history_record(
                            run_id=run_id,
                            mode=workflow,
                            audio_path=active_audio_path,
                            raw_text="",
                            corrected_text="",
                            dictionary_version=dictionary_version(),
                            timings=timings,
                            offline_mode=asr_backend != "Gemini audio",
                            status="failed",
                            error_type="capture_error",
                        )
                    except Exception as history_exc:
                        logger.event("history_error", error=repr(history_exc))
                if not retain_audio and audio_path.exists():
                    audio_path.unlink(missing_ok=True)
                st.error(f"Capture failed: {exc}")
                st.stop()

        logger.event(
            "recording_complete",
            audio_path=str(audio_path),
        )

        with st.status("Transcribing locally...", expanded=True):
            started = time.perf_counter()
            try:
                if asr_backend == "Local Meaning (whisper.cpp)":
                    transcript = transcribe_whisper_cpp_translate(audio_path)
                elif asr_backend == "Local Meaning Server (fast)":
                    transcript = transcribe_local_meaning_server_with_fallback(audio_path)
                elif asr_backend == "Local Meaning Fast (base experimental)":
                    transcript = transcribe_whisper_cpp_translate_base(audio_path)
                elif asr_backend == "Meaning Router (experimental)":
                    transcript = transcribe_meaning_router(audio_path)
                    debug_rows.append(("asr_candidates", [c.__dict__ for c in transcript.candidates]))
                    logger.event("asr_candidates", candidates=[c.__dict__ for c in transcript.candidates] if audit_history else [], route=transcript.route)
                elif asr_backend == "Hybrid Ludo-style":
                    transcript = transcribe_hybrid_ludo(audio_path, gemini_key=gemini_key or None)
                    debug_rows.append(("asr_candidates", [c.__dict__ for c in transcript.candidates]))
                    logger.event("asr_candidates", candidates=[c.__dict__ for c in transcript.candidates] if audit_history else [])
                elif asr_backend == "Gemini audio":
                    transcript = transcribe_gemini_audio(audio_path, api_key=gemini_key or None)
                elif workflow == "Real-time Prompt":
                    transcript = transcribe_with_timeout(audio_path, model=model, language=language, timeout_seconds=45)
                else:
                    transcript = transcribe_audio(audio_path, model=model, language=language)
            except Exception as exc:
                logger.event("asr_error", error=repr(exc))
                if audit_history:
                    try:
                        append_history_record(
                            run_id=run_id,
                            mode=workflow,
                            audio_path=active_audio_path,
                            raw_text="",
                            corrected_text="",
                            asr_engine=active_asr_engine,
                            sidecar_state=compact_sidecar_state(active_sidecar_state),
                            dictionary_version=dictionary_version(),
                            timings=timings,
                            quality_flags=active_quality,
                            offline_mode=asr_backend != "Gemini audio",
                            status="failed",
                            error_type="asr_error",
                        )
                    except Exception as history_exc:
                        logger.event("history_error", error=repr(history_exc))
                if not retain_audio and active_audio_path and active_audio_path.exists():
                    active_audio_path.unlink(missing_ok=True)
                st.error(f"ASR failed: {exc}")
                st.stop()
            timings.append(f"asr={time.perf_counter() - started:.2f}s")
            raw_text = transcript.text
            active_asr_engine = transcript.engine
            transcript_meta = f"{transcript.engine}, language={transcript.language or 'unknown'}, file={audio_path}"
            quality = {
                "repeated_substring_score": repeated_substring_score(raw_text),
                "degenerate": is_degenerate_transcript(raw_text),
                "char_count": len(raw_text),
            }
            active_quality = quality
            if asr_backend == "Local Meaning Server (fast)":
                active_sidecar_state = sidecar_as_dict(sidecar_status())
            logger.event(
                "asr_result",
                engine=transcript.engine,
                language=transcript.language,
                raw_text=raw_text if audit_history else "",
                quality=quality,
            )
            debug_rows.append(("asr_raw", raw_text))
            debug_rows.append(("asr_quality", quality))

elif input_type == "Audio file":
    uploaded = st.file_uploader("Upload audio", type=["wav", "mp3", "m4a", "mp4", "aac", "flac"])
    if uploaded and st.button("Transcribe and clean", type="primary"):
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        logger = RunLogger(run_id)
        log_path = logger.path
        logger.event(
            "config",
            workflow=workflow,
            input_type=input_type,
            language_label=language_label,
            language=language,
            model_label=model_label,
            model=model,
            asr_backend=asr_backend,
            use_ollama=use_ollama,
            use_hymt=use_hymt,
        )
        suffix = Path(uploaded.name).suffix or ".audio"
        output_dir = Path("recordings") if retain_audio else Path("logs/tmp_audio")
        saved_upload_path = output_dir / f"{run_id}{suffix}"
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            saved_upload_path.write_bytes(uploaded.read())
            tmp_path = saved_upload_path
            active_audio_path = tmp_path
            if retain_audio:
                corpus_item = append_corpus_item(
                    item_id=run_id,
                    audio_path=saved_upload_path,
                    source="upload",
                    workflow=workflow,
                    notes=f"Uploaded file: {uploaded.name}. Fill gold once this clip is understood.",
                )
                logger.event("corpus_item", corpus_item=corpus_item)
                debug_rows.append(("corpus_item", corpus_item))
        except Exception as exc:
            logger.event("upload_save_error", error=repr(exc))
            if audit_history:
                try:
                    append_history_record(
                        run_id=run_id,
                        mode=workflow,
                        audio_path=saved_upload_path,
                        raw_text="",
                        corrected_text="",
                        dictionary_version=dictionary_version(),
                        timings=timings,
                        offline_mode=asr_backend != "Gemini audio",
                        status="failed",
                        error_type="upload_save_error",
                    )
                except Exception as history_exc:
                    logger.event("history_error", error=repr(history_exc))
            if not retain_audio and saved_upload_path.exists():
                saved_upload_path.unlink(missing_ok=True)
            st.error(f"Upload save failed: {exc}")
            st.stop()

        with st.status("Transcribing locally...", expanded=True):
            started = time.perf_counter()
            try:
                stats = inspect_audio(tmp_path)
                logger.event("audio_stats", audio_stats=stats)
                debug_rows.append(("audio_stats", stats))
            except Exception as exc:
                logger.event("audio_stats_error", error=repr(exc))
            try:
                if asr_backend == "Local Meaning (whisper.cpp)":
                    transcript = transcribe_whisper_cpp_translate(tmp_path)
                elif asr_backend == "Local Meaning Server (fast)":
                    transcript = transcribe_local_meaning_server_with_fallback(tmp_path)
                elif asr_backend == "Local Meaning Fast (base experimental)":
                    transcript = transcribe_whisper_cpp_translate_base(tmp_path)
                elif asr_backend == "Meaning Router (experimental)":
                    transcript = transcribe_meaning_router(tmp_path)
                    debug_rows.append(("asr_candidates", [c.__dict__ for c in transcript.candidates]))
                    logger.event("asr_candidates", candidates=[c.__dict__ for c in transcript.candidates] if audit_history else [], route=transcript.route)
                elif asr_backend == "Hybrid Ludo-style":
                    transcript = transcribe_hybrid_ludo(tmp_path, gemini_key=gemini_key or None)
                    debug_rows.append(("asr_candidates", [c.__dict__ for c in transcript.candidates]))
                    logger.event("asr_candidates", candidates=[c.__dict__ for c in transcript.candidates] if audit_history else [])
                elif asr_backend == "Gemini audio":
                    transcript = transcribe_gemini_audio(tmp_path, api_key=gemini_key or None)
                else:
                    transcript = transcribe_audio(tmp_path, model=model, language=language)
            except Exception as exc:
                logger.event("asr_error", error=repr(exc))
                if audit_history:
                    try:
                        append_history_record(
                            run_id=run_id,
                            mode=workflow,
                            audio_path=active_audio_path,
                            raw_text="",
                            corrected_text="",
                            asr_engine=active_asr_engine,
                            sidecar_state=compact_sidecar_state(active_sidecar_state),
                            dictionary_version=dictionary_version(),
                            timings=timings,
                            quality_flags=active_quality,
                            offline_mode=asr_backend != "Gemini audio",
                            status="failed",
                            error_type="asr_error",
                        )
                    except Exception as history_exc:
                        logger.event("history_error", error=repr(history_exc))
                if not retain_audio and active_audio_path and active_audio_path.exists():
                    active_audio_path.unlink(missing_ok=True)
                st.error(f"ASR failed: {exc}")
                st.stop()
            timings.append(f"asr={time.perf_counter() - started:.2f}s")
            raw_text = transcript.text
            active_asr_engine = transcript.engine
            transcript_meta = f"{transcript.engine}, language={transcript.language or 'unknown'}"
            quality = {
                "repeated_substring_score": repeated_substring_score(raw_text),
                "degenerate": is_degenerate_transcript(raw_text),
                "char_count": len(raw_text),
            }
            active_quality = quality
            if asr_backend == "Local Meaning Server (fast)":
                active_sidecar_state = sidecar_as_dict(sidecar_status())
            logger.event("asr_result", engine=transcript.engine, language=transcript.language, raw_text=raw_text if audit_history else "", quality=quality)
            debug_rows.append(("asr_raw", raw_text))
            debug_rows.append(("asr_quality", quality))
else:
    raw_text = st.text_area("Paste transcript or rough dictation", height=260)
    run_text = st.button("Clean", type="primary")
    if not run_text:
        raw_text = ""
    else:
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        logger = RunLogger(run_id)
        log_path = logger.path
        logger.event("config", workflow=workflow, input_type=input_type, use_ollama=use_ollama, use_hymt=use_hymt)
        logger.event("text_input", raw_text=raw_text if audit_history else "")
        debug_rows.append(("text_input", raw_text))

if raw_text:
    with st.status("Cleaning and compressing...", expanded=False):
        started = time.perf_counter()
        try:
            output = process_transcript(
                raw_text,
                use_ollama=use_ollama,
                model=ollama_model,
                use_hymt=use_hymt,
                hymt_model=hymt_model,
            )
        except Exception as exc:
            if "logger" in locals():
                logger.event("cleanup_error", error=repr(exc))
                if audit_history:
                    try:
                        append_history_record(
                            run_id=run_id,
                            mode=workflow,
                            audio_path=active_audio_path,
                            raw_text=raw_text,
                            corrected_text="",
                            asr_engine=active_asr_engine,
                            sidecar_state=compact_sidecar_state(active_sidecar_state),
                            fallback_reason=extract_fallback_reason(active_asr_engine),
                            dictionary_version=dictionary_version(),
                            timings=timings,
                            quality_flags=active_quality,
                            offline_mode=asr_backend != "Gemini audio",
                            status="failed",
                            error_type="cleanup_error",
                        )
                    except Exception as history_exc:
                        logger.event("history_error", error=repr(history_exc))
                if not retain_audio and active_audio_path and active_audio_path.exists():
                    active_audio_path.unlink(missing_ok=True)
            st.error(f"Cleanup failed: {exc}")
            st.stop()
        timings.append(f"cleanup={time.perf_counter() - started:.2f}s")
        if "logger" in locals():
            logger.event(
                "cleanup_result",
                processor=output.processor,
                clean_transcript=output.clean_transcript if audit_history else "",
                prompt_mode=output.prompt_mode if audit_history else "",
            )
            if audit_history:
                try:
                    history_row = append_history_record(
                        run_id=run_id,
                        mode=workflow,
                        audio_path=active_audio_path,
                        raw_text=raw_text,
                        corrected_text=output.clean_transcript,
                        pasted_text="",
                        asr_engine=active_asr_engine,
                        sidecar_state=compact_sidecar_state(active_sidecar_state),
                        fallback_reason=extract_fallback_reason(active_asr_engine),
                        dictionary_version=dictionary_version(),
                        timings=timings,
                        quality_flags=active_quality,
                        offline_mode=asr_backend != "Gemini audio",
                        status="completed",
                    )
                    logger.event("history_record", history_path="logs/history.jsonl", fallback_reason=history_row.get("fallback_reason"))
                    debug_rows.append(("history_record", history_row))
                except Exception as exc:
                    logger.event("history_error", error=repr(exc))
                    st.warning(f"Could not write audit history: {exc}")
        if not retain_audio and active_audio_path and active_audio_path.exists():
            try:
                active_audio_path.unlink()
                if "logger" in locals():
                    logger.event("temporary_audio_deleted", audio_path=str(active_audio_path))
            except Exception as exc:
                if "logger" in locals():
                    logger.event("temporary_audio_delete_error", error=repr(exc))
        debug_rows.append(("processor", output.processor))
        debug_rows.append(("clean_transcript", output.clean_transcript))

    if transcript_meta:
        st.caption(transcript_meta)
    if "fallback_reason=" in transcript_meta:
        st.warning("Fast sidecar path degraded to process fallback. Check sidecar status before relying on latency numbers.")
    if timings:
        st.caption(" | ".join(timings))
    if log_path:
        st.caption(f"log={log_path}")
    if debug_mode and debug_rows:
        with st.expander("Debug trace", expanded=True):
            for label, value in debug_rows:
                st.markdown(f"**{label}**")
                if hasattr(value, "__dict__"):
                    st.json(value.__dict__)
                elif isinstance(value, dict):
                    st.json(value)
                else:
                    st.code(str(value))
    if is_degenerate_transcript(raw_text):
        st.error(
            "ASR output looks degenerate/repetitive. Set language to Auto or English, "
            "or try Balanced model."
        )
    if debug_rows and any(label == "audio_stats" and getattr(value, "likely_silence", False) for label, value in debug_rows):
        st.warning("Mic input looks very quiet. Move closer to the mic, raise input gain, or check the selected microphone.")
    elif repeated_substring_score(raw_text) > 0.1:
        st.warning("ASR output has suspicious repetition.")

    tabs = st.tabs(["Prompt Mode", "Clean Transcript", "Raw"])
    with tabs[0]:
        st.text_area("LLM-ready instruction", output.prompt_mode, height=420)
    with tabs[1]:
        st.text_area("Clean transcript", output.clean_transcript, height=420)
    with tabs[2]:
        st.text_area("Raw transcript", raw_text, height=420)

    st.caption(f"Processor: {output.processor}")

st.divider()
render_corpus_panel()
