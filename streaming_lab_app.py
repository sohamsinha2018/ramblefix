from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import streamlit as st

from ramblefix.engine_router import transcribe_ramblefix_hinglish_v1
from ramblefix.cloud_asr import transcribe_elevenlabs_scribe
from ramblefix.external_asr import (
    ExternalTranscript,
    transcribe_faster_whisper,
    transcribe_qwen3_asr_mlx,
    transcribe_whisper_cpp_server_translate,
)
from ramblefix.sidecar import as_dict as sidecar_as_dict
from ramblefix.sidecar import ensure_ready, status as sidecar_status
from ramblefix.streaming import stream_wav_file


ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "eval_runs" / "streaming-lab-manual"


st.set_page_config(page_title="RambleFix Streaming Lab", layout="wide")
st.title("RambleFix Streaming Lab")
st.caption("Record once, then compare fast draft, async finalizer, and reference STT.")


def main() -> None:
    with st.sidebar:
        st.subheader("Settings")
        finalizer = st.selectbox("Streaming finalizer", ["always", "auto", "none"], index=0)
        draft_min = st.slider("First draft after seconds", 0.5, 3.0, 0.75, 0.25)
        draft_every = st.slider("Draft cadence seconds", 0.5, 3.0, 1.0, 0.25)
        realtime = st.checkbox("Replay in real time", value=True)
        language_code = st.selectbox("Reference language hint", ["auto", "en", "hi"], index=0)
        eleven_key = st.text_input("ElevenLabs key override", value="", type="password")
        run_scribe = st.checkbox("Run ElevenLabs Scribe reference", value=bool(os.environ.get("ELEVENLABS_API_KEY") or eleven_key))
        run_qwen = st.checkbox("Run Qwen English reference", value=True)
        run_faster = st.checkbox("Run faster-whisper small reference", value=False)
        if st.button("Warm local sidecar"):
            state = ensure_ready(warm=True, timeout_seconds=20.0)
            st.json(sidecar_as_dict(state))

    sidecar = sidecar_as_dict(sidecar_status())
    st.info(f"Local whisper sidecar: {sidecar.get('status')} at {sidecar.get('url')}")

    recorded = st.audio_input("Record from browser")
    uploaded = st.file_uploader("Or upload audio", type=["wav", "mp3", "m4a", "webm", "ogg"])
    audio_file = recorded or uploaded
    if not audio_file:
        st.stop()

    source_path, wav_path = _save_audio(audio_file)
    st.audio(str(wav_path))
    st.caption(f"Saved normalized WAV: `{wav_path}`")

    if not st.button("Run streaming + comparison", type="primary"):
        st.stop()

    run_dir = RUNS / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    st.write("Running local streaming lab...")
    streaming = stream_wav_file(
        wav_path,
        real_time=realtime,
        draft_min_seconds=draft_min,
        draft_every_seconds=draft_every,
        finalizer=finalizer,
    )
    _write_json(run_dir / "streaming.json", streaming.to_json())

    st.subheader("Streaming UX")
    metrics = streaming.timings_ms
    cols = st.columns(4)
    cols[0].metric("First partial", _fmt_ms(metrics.get("time_to_first_partial")))
    cols[1].metric("Release to paste", _fmt_ms(metrics.get("release_to_paste")))
    cols[2].metric("Release to final", _fmt_ms(metrics.get("release_to_final")))
    cols[3].metric("Churn WER", "n/a" if streaming.churn_wer is None else f"{streaming.churn_wer:.3f}")

    paste_col, final_col = st.columns(2)
    paste_col.text_area("Pasted draft", streaming.paste_text, height=140)
    final_col.text_area("Async final", streaming.final_text, height=140)

    with st.expander("Timeline events", expanded=True):
        st.dataframe([event.__dict__ for event in streaming.events], use_container_width=True)

    st.subheader("Reference STT Compare")
    refs: list[dict[str, Any]] = []
    refs.append(_run_ref("fast_server_translate", lambda: transcribe_whisper_cpp_server_translate(wav_path)))
    refs.append(_run_ref("srota_hinglish", lambda: _srota_external(wav_path)))
    if run_qwen:
        refs.append(_run_ref("qwen_english_mlx", lambda: transcribe_qwen3_asr_mlx(wav_path, language="English")))
    if run_faster:
        refs.append(_run_ref("faster_whisper_small", lambda: transcribe_faster_whisper(wav_path, model="small", language=None)))
    if run_scribe:
        refs.append(
            _run_ref(
                "elevenlabs_scribe_v2",
                lambda: transcribe_elevenlabs_scribe(
                    wav_path,
                    api_key=eleven_key or None,
                    language_code=None if language_code == "auto" else language_code,
                ),
            )
        )
    _write_json(run_dir / "references.json", refs)
    st.dataframe(refs, use_container_width=True)
    for ref in refs:
        st.text_area(ref["name"], ref.get("text", ""), height=110)

    _write_json(
        run_dir / "run.json",
        {
            "source_audio": str(source_path),
            "wav": str(wav_path),
            "streaming": streaming.to_json(),
            "references": refs,
        },
    )
    st.success(f"Wrote run artifacts to `{run_dir}`")


def _save_audio(uploaded: Any) -> tuple[Path, Path]:
    RUNS.mkdir(parents=True, exist_ok=True)
    raw_dir = RUNS / "incoming"
    raw_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(uploaded.name or "audio.wav").suffix or ".wav"
    source = raw_dir / f"{time.strftime('%Y%m%d-%H%M%S')}{suffix}"
    source.write_bytes(uploaded.getvalue())
    wav = source.with_suffix(".16k.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-ar", "16000", "-ac", "1", str(wav)],
        check=True,
    )
    return source, wav


def _run_ref(name: str, fn: Callable[[], ExternalTranscript]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        transcript = fn()
        return {
            "name": name,
            "engine": transcript.engine,
            "seconds": transcript.seconds,
            "wall_seconds": round(time.perf_counter() - started, 3),
            "language": transcript.language,
            "language_probability": transcript.language_probability,
            "text": transcript.text,
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001 - show comparator failures in the UI.
        return {
            "name": name,
            "engine": "",
            "seconds": round(time.perf_counter() - started, 3),
            "wall_seconds": round(time.perf_counter() - started, 3),
            "language": None,
            "language_probability": None,
            "text": "",
            "error": repr(exc),
        }


def _srota_external(path: Path) -> ExternalTranscript:
    routed = transcribe_ramblefix_hinglish_v1(path)
    return ExternalTranscript(text=routed.text, engine=routed.engine, seconds=routed.seconds)


def _fmt_ms(value: object) -> str:
    return "n/a" if value is None else f"{value} ms"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
