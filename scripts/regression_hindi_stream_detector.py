from __future__ import annotations

import os
import tempfile
import time
import wave
from pathlib import Path

import ramblefix.external_asr as external_asr
from ramblefix.external_asr import ExternalTranscript
from ramblefix.glossary import apply_glossary
from ramblefix.hindi_stream_session import (
    DetectorChunk,
    HindiStreamSession,
    StreamChunk,
    _chunk_file_is_stable,
    _candidate_covers_draft,
    _detector_chunk_risk,
    _detector_risk_reason,
    _merge_ready_hindi_with_draft_tail,
    _merge_tail_redecode_with_draft,
    _oriserve_rejected_candidate,
    _process_final_chunks_on_release,
    _ready_chunk_finish_grace_seconds,
    _repair_leading_unknown_english_before_hindi,
    _should_prefer_clean_tail_merge,
    _tail_redecode_enabled,
    _tail_redecode_seconds,
    _work_acronyms,
)
from ramblefix.hindi_chunk_polish import romanize_devanagari_for_hinglish


def main() -> None:
    assert apply_glossary("API ho ya M C P ho same kaam karna chahiye") == (
        "API ho ya MCP ho same kaam karna chahiye"
    )

    assert _detector_chunk_risk(language="hi", probability=0.95, low_confidence_threshold=0.50)
    assert _detector_risk_reason(language="hi", probability=0.95, low_confidence_threshold=0.50) == "language:hi"

    assert _detector_chunk_risk(language="ur", probability=0.95, low_confidence_threshold=0.50)
    assert _detector_risk_reason(language="ur", probability=0.95, low_confidence_threshold=0.50) == "language:ur"

    assert not _detector_chunk_risk(language="en", probability=0.79, low_confidence_threshold=0.50)
    assert _detector_risk_reason(language="en", probability=0.79, low_confidence_threshold=0.50) == "unknown"

    assert _detector_chunk_risk(
        chunk_index=0,
        language="en",
        probability=0.79,
        low_confidence_threshold=0.50,
        early_low_confidence_threshold=0.80,
    )
    assert (
        _detector_risk_reason(
            chunk_index=0,
            language="en",
            probability=0.79,
            low_confidence_threshold=0.50,
            early_low_confidence_threshold=0.80,
        )
        == "english_early_low_confidence"
    )

    assert not _detector_chunk_risk(
        chunk_index=1,
        language="en",
        probability=0.79,
        low_confidence_threshold=0.50,
        early_low_confidence_threshold=0.80,
    )

    assert _detector_chunk_risk(language="en", probability=0.49, low_confidence_threshold=0.50)
    assert _detector_risk_reason(language="en", probability=0.49, low_confidence_threshold=0.50) == "english_low_confidence"

    assert _detector_chunk_risk(language="ar", probability=0.30, low_confidence_threshold=0.50)
    assert _detector_risk_reason(language="ar", probability=0.30, low_confidence_threshold=0.50) == "non_english_low_confidence:ar"

    assert not _detector_chunk_risk(language="ar", probability=0.75, low_confidence_threshold=0.50)

    assert not _detector_chunk_risk(language=None, probability=None, low_confidence_threshold=0.50)

    os.environ.pop("RAMBLEFIX_HINDI_STREAM_READY_GRACE_SECONDS", None)
    assert _ready_chunk_finish_grace_seconds() == 0.35
    os.environ["RAMBLEFIX_HINDI_STREAM_READY_GRACE_SECONDS"] = "1.25"
    assert _ready_chunk_finish_grace_seconds() == 1.25
    os.environ["RAMBLEFIX_HINDI_STREAM_READY_GRACE_SECONDS"] = "bad"
    assert _ready_chunk_finish_grace_seconds() == 0.35
    os.environ.pop("RAMBLEFIX_HINDI_STREAM_READY_GRACE_SECONDS", None)

    os.environ.pop("RAMBLEFIX_HINDI_STREAM_PROCESS_FINAL_CHUNKS", None)
    assert _process_final_chunks_on_release()
    os.environ["RAMBLEFIX_HINDI_STREAM_PROCESS_FINAL_CHUNKS"] = "0"
    assert not _process_final_chunks_on_release()
    os.environ["RAMBLEFIX_HINDI_STREAM_PROCESS_FINAL_CHUNKS"] = "true"
    assert _process_final_chunks_on_release()
    os.environ.pop("RAMBLEFIX_HINDI_STREAM_PROCESS_FINAL_CHUNKS", None)

    os.environ.pop("RAMBLEFIX_HINDI_STREAM_TAIL_REDECODE", None)
    assert _tail_redecode_enabled()
    os.environ["RAMBLEFIX_HINDI_STREAM_TAIL_REDECODE"] = "0"
    assert not _tail_redecode_enabled()
    os.environ["RAMBLEFIX_HINDI_STREAM_TAIL_REDECODE"] = "true"
    assert _tail_redecode_enabled()
    os.environ.pop("RAMBLEFIX_HINDI_STREAM_TAIL_REDECODE", None)

    os.environ.pop("RAMBLEFIX_HINDI_STREAM_TAIL_SECONDS", None)
    assert _tail_redecode_seconds() == 8.0
    os.environ["RAMBLEFIX_HINDI_STREAM_TAIL_SECONDS"] = "8"
    assert _tail_redecode_seconds() == 8.0
    os.environ["RAMBLEFIX_HINDI_STREAM_TAIL_SECONDS"] = "bad"
    assert _tail_redecode_seconds() == 8.0
    os.environ.pop("RAMBLEFIX_HINDI_STREAM_TAIL_SECONDS", None)

    with tempfile.TemporaryDirectory(prefix="ramblefix-stream-detector-regression-") as tmp:
        path = Path(tmp) / "chunk-000.wav"
        path.write_bytes(b"0" * 256)
        now = time.time()
        os.utime(path, (now - 1.0, now - 1.0))
        assert _chunk_file_is_stable(path, min_age_seconds=0.25, now=now)
        assert (
            romanize_devanagari_for_hinglish("हाँ भाई देख ये सब करने से कुछ नहीं होगा")
            == "haan bhai dekh ye sab karne se kuch nahi hoga"
        )
        assert romanize_devanagari_for_hinglish("मतलब आपको ऐसा करना चाहिए") == "matlab aapko aisa karna chahiye"

        os.utime(path, (now, now))
        assert not _chunk_file_is_stable(path, min_age_seconds=0.25, now=now)

        empty = Path(tmp) / "chunk-001.wav"
        empty.write_bytes(b"0" * 16)
        os.utime(empty, (now - 1.0, now - 1.0))
        assert not _chunk_file_is_stable(empty, min_age_seconds=0.25, now=now)

        draft = (
            "The way MCPs work is that there is an API layer and there are guidelines as well. "
            "So, the guidelines and documentation are all there. So, the API layer functions well."
        )
        raw_prefix = "see the way mcp's work is that there's a api layer but उसके साथ ना guidelines वगैरह भी होती है"
        merged = _merge_ready_hindi_with_draft_tail(draft_text=draft, raw_text=raw_prefix, pending_count=1)
        assert "guidelines वगैरह" in merged
        assert "documentation are all there" in merged
        no_pending_merged = _merge_ready_hindi_with_draft_tail(draft_text=draft, raw_text=raw_prefix, pending_count=0)
        assert "guidelines वगैरह" in no_pending_merged
        assert "documentation are all there" in no_pending_merged

        wedge_draft = "Yes, look, nothing will happen if our tool cannot beat others on one core problem, then there is no wedge."
        wedge_prefix = "हाँ भई देख ये सब करने से कुछ नहीं होगा अगर हमारा tool cannot"
        wedge_merged = _merge_ready_hindi_with_draft_tail(draft_text=wedge_draft, raw_text=wedge_prefix, pending_count=1)
        assert "हमारा tool cannot" in wedge_merged
        assert "beat others on one core problem" in wedge_merged

        hallucinated_tail_draft = (
            "What is it that you have to do? What I am trying to say here is that in our legal profession "
            "this is not how it works. So you have to factor in this and improve it or else it will not be possible. "
            "This will not work. So think through and critique and think and answer in the direction of the sun."
        )
        complete_hindi_answer = (
            "हाँ पर एकी करने से क्या होता है मतलब What I am trying to say here is that in our legal profession "
            "this is not how it works. right so आपको यह factor करके improve करना होगा नहीं तो यह होनी पायेगा "
            "मतलब this will not work right so think through critique और आप मतलब विचार विमर्श करके संक्षिप्त में उत्तर दें"
        )
        clean_hindi_answer = _merge_ready_hindi_with_draft_tail(
            draft_text=hallucinated_tail_draft,
            raw_text=complete_hindi_answer,
            pending_count=0,
        )
        assert "उत्तर दें" in clean_hindi_answer
        assert "direction of the sun" not in clean_hindi_answer

        complete_draft = "So in order for this to work, it should not be that. It should be direct and brief."
        complete_candidate = (
            "हाँ वह so in order for this to work ऐसा हो ही नहीं सकता कि इसमें वो मतलब वो बातें नहीं हो पायेंगी "
            "मतलब कैसे समझो मैं आपको it should not be like that right it should not it should be direct and brief"
        )
        assert _candidate_covers_draft(draft_text=complete_draft, candidate_text=complete_candidate)

        tail_merge, tail_reason, tail_meta = _merge_tail_redecode_with_draft(
            draft_text=complete_draft,
            tail_text=(
                "I mean, you can't talk about it. How do I explain it? It should not be like that. "
                "It should be direct and brief. Whether it's an API or MCP, all of them should do the same."
            ),
        )
        assert tail_reason == "merged"
        assert tail_merge is not None
        assert "API or MCP" in tail_merge
        assert tail_meta["new_acronyms"] == ["api", "mcp"]
        rough_safe_candidate = (
            "so in order for this aisa ho hi nahi sakta hai ta ki ismein vo matlab vo baatein nahi ho "
            "matlab kaise samajho aapko it should not be like that right it should not it should be direct "
            "and brief. API ho ya M C P ho same kaam karna chahiye"
        )
        rough_hindi_value = {
            "substantive_new_roman_hindi_tokens": ["aapko", "aisa", "baatein", "chahiye", "ismein"]
        }
        assert _work_acronyms(rough_safe_candidate) == {"api", "mcp"}
        assert not _should_prefer_clean_tail_merge(
            draft_text=complete_draft,
            candidate_text=rough_safe_candidate,
            hindi_value=rough_hindi_value,
        )
        weak_hindi_value = {"substantive_new_roman_hindi_tokens": ["aapko"]}
        assert _should_prefer_clean_tail_merge(
            draft_text=complete_draft,
            candidate_text=rough_safe_candidate,
            hindi_value=weak_hindi_value,
        )
        short_tail_merge, short_tail_reason, _ = _merge_tail_redecode_with_draft(
            draft_text=complete_draft,
            tail_text="like that right it should know it should be direct and brief API or MCP all the same work should be done",
        )
        assert short_tail_reason == "merged"
        assert short_tail_merge is not None
        assert short_tail_merge.endswith("API or MCP should do the same work.")
        assert not _should_prefer_clean_tail_merge(
            draft_text=(
                "The way MCPs work is that there is an API layer and there are guidelines as well. "
                "So, the guidelines and documentation are all there. So, the API layer functions well."
            ),
            candidate_text=(
                "See the way MCPs work is that there's an API layer par uske saath na guidelines "
                "vagairah bhi hota hai."
            ),
            hindi_value=rough_hindi_value,
        )

        duplicate_tail_merge, duplicate_tail_reason, _ = _merge_tail_redecode_with_draft(
            draft_text=(
                "The way MCPs work is that there is an API layer and there are guidelines as well. "
                "So, the guidelines and documentation are all there. So, the API layer functions well."
            ),
            tail_text=(
                "and also the guidelines. So, the guidelines and documentation are all there, "
                "because the API layer functions well."
            ),
        )
        assert duplicate_tail_merge is None
        assert duplicate_tail_reason == "no-new-work-content"

        rescue_audio = Path(tmp) / "oriserve-rescue.wav"
        with wave.open(str(rescue_audio), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(b"\0\0" * 16000)

        original_oriserve = external_asr.transcribe_oriserve_hindi2hinglish

        def fake_good_oriserve(_: Path) -> ExternalTranscript:
            return ExternalTranscript(
                text=(
                    "Haan bhai, dekh, what I need now is a quick answer and be tight and brief and skeptical. "
                    "Thik hai, havaavaaji nahin chaahie."
                ),
                engine="fake-oriserve",
                seconds=0.1,
            )

        def fake_bad_oriserve(_: Path) -> ExternalTranscript:
            return ExternalTranscript(
                text=(
                    "Haan bhai dekh, yah sab karne se kuchh nahin hoga, agar hamaara tool cannot "
                    "beat others on a core one core problem, then there is no veg."
                ),
                engine="fake-oriserve",
                seconds=0.1,
            )

        try:
            external_asr.transcribe_oriserve_hindi2hinglish = fake_good_oriserve
            good_rescue = _oriserve_rejected_candidate(
                audio_path=rescue_audio,
                draft_text="What I need now is a quick answer and be tight and brief and skeptical.",
                risk_reasons=["language:ur"],
                started_at=time.perf_counter(),
                deadline=time.perf_counter() + 5.0,
                max_release_tail_seconds=3.0,
            )
            assert good_rescue["accepted"] is True
            assert "hawabaazi nahin chahiye" in good_rescue["text"]

            external_asr.transcribe_oriserve_hindi2hinglish = fake_bad_oriserve
            bad_rescue = _oriserve_rejected_candidate(
                audio_path=rescue_audio,
                draft_text=(
                    "Yes, look, nothing will happen if our tool cannot beat others on one core problem, "
                    "then there is no wedge."
                ),
                risk_reasons=["language:hi"],
                started_at=time.perf_counter(),
                deadline=time.perf_counter() + 5.0,
                max_release_tail_seconds=3.0,
            )
            assert bad_rescue["accepted"] is True
            assert "there is no wedge" in bad_rescue["text"]
            assert "veg" not in bad_rescue["text"]
            assert bad_rescue["sanitize"]["repair_rules"] == ["draft-backed-tail-completion"]

            weak_risk_rescue = _oriserve_rejected_candidate(
                audio_path=rescue_audio,
                draft_text="Okay, I think now somehow terms are getting resolved better. MCP, QRS.",
                risk_reasons=["english_early_low_confidence"],
                started_at=time.perf_counter(),
                deadline=time.perf_counter() + 5.0,
                max_release_tail_seconds=3.0,
            )
            assert weak_risk_rescue["ran"] is False
            assert weak_risk_rescue["reason"] == "weak-hindi-risk"
        finally:
            external_asr.transcribe_oriserve_hindi2hinglish = original_oriserve

        incomplete_tail = complete_candidate.rsplit(" and brief", 1)[0]
        assert not _candidate_covers_draft(draft_text=complete_draft, candidate_text=incomplete_tail)
        repaired_tail = _merge_ready_hindi_with_draft_tail(
            draft_text=complete_draft,
            raw_text=incomplete_tail,
            pending_count=1,
        )
        assert repaired_tail.endswith("and brief.")

        subagent_draft = (
            "Hi, in order for this to be solved, you have to structure it into four sub-agents. "
            "Each agent will have a different task."
        )
        subagent_candidate = (
            "Higher पर in order for this to be solved आपको वैसा करना होगा कि you have to structure it "
            "into four sub agents हर agent का काम अलगअलग होगा will have a different task."
        )
        repaired_candidate, repair_reason = _repair_leading_unknown_english_before_hindi(
            draft_text=subagent_draft,
            candidate_text=subagent_candidate,
        )
        assert repair_reason == "drop-leading-unknown-english:Higher"
        assert repaired_candidate.startswith("पर in order")

        dense_bad_candidate = (
            "Okay now for this goal think through what a best design would be. "
            "There can be some super light thing and that attracts if centre if it has any Hindi."
        )
        dense_repaired, dense_reason = _repair_leading_unknown_english_before_hindi(
            draft_text="Now for this goal, think through what the best design would be.",
            candidate_text=dense_bad_candidate,
        )
        assert dense_repaired == dense_bad_candidate
        assert dense_reason == ""

        pending = Path(tmp) / "chunk-002.wav"
        pending.write_bytes(b"1" * 256)
        os.utime(pending, (now - 1.0, now - 1.0))
        session = HindiStreamSession(run_id="regression", chunk_dir=tmp)
        session._risk = True
        session._risk_reasons.append("language:hi")
        session._detected[0] = DetectorChunk(
            index=0,
            path=str(path),
            language="hi",
            language_probability=0.90,
            seconds=0.1,
            risk=True,
        )
        session._chunks[0] = StreamChunk(
            index=0,
            path=str(path),
            duration_seconds=9.9,
            compute_seconds=1.0,
            text=raw_prefix,
        )
        result = session.finish(draft_text=draft, wait_timeout_seconds=0.01)
        assert not result.safe_update
        assert result.route == "hindi_stream_rejected"
        assert "no-default-meaning-gain" in result.reject_reasons
        assert "pending-chunks" not in result.reject_reasons
        assert result.quality["partial_merge"] is True
        assert result.quality["hindi_value"]["has_hindi_value"] is True
        assert result.quality["romanized_output"] is False
        assert "documentation are all there" in result.text
        assert "guidelines vagairah" not in result.text

        english_only_dir = Path(tmp) / "english-only"
        english_only_dir.mkdir()
        english_only_path = english_only_dir / "chunk-000.wav"
        english_only_path.write_bytes(b"3" * 256)
        os.utime(english_only_path, (now - 1.0, now - 1.0))
        english_only_session = HindiStreamSession(run_id="english-only-regression", chunk_dir=english_only_dir)
        english_only_session._risk = True
        english_only_session._risk_reasons.append("language:hi")
        english_only_session._detected[0] = DetectorChunk(
            index=0,
            path=str(english_only_path),
            language="hi",
            language_probability=0.90,
            seconds=0.1,
            risk=True,
        )
        english_only_session._chunks[0] = StreamChunk(
            index=0,
            path=str(english_only_path),
            duration_seconds=5.0,
            compute_seconds=0.5,
            text="I have added some clips right now. Most of them have Hindi in it.",
        )
        english_only_result = english_only_session.finish(
            draft_text="I have added some clips now. Most of them have Hindi in it.",
            wait_timeout_seconds=0.01,
        )
        assert not english_only_result.safe_update
        assert "no-hindi-value" in english_only_result.reject_reasons
        assert english_only_result.quality["hindi_value"]["has_hindi_value"] is False

        style_only_dir = Path(tmp) / "style-only"
        style_only_dir.mkdir()
        style_only_path = style_only_dir / "chunk-000.wav"
        style_only_path.write_bytes(b"4" * 256)
        os.utime(style_only_path, (now - 1.0, now - 1.0))
        style_only_session = HindiStreamSession(run_id="style-only-regression", chunk_dir=style_only_dir)
        style_only_session._risk = True
        style_only_session._risk_reasons.append("language:hi")
        style_only_session._detected[0] = DetectorChunk(
            index=0,
            path=str(style_only_path),
            language="hi",
            language_probability=0.90,
            seconds=0.1,
            risk=True,
        )
        style_only_session._chunks[0] = StreamChunk(
            index=0,
            path=str(style_only_path),
            duration_seconds=5.0,
            compute_seconds=0.5,
            text="हाँ वह देख what I need now is a quick answer and be tight and brief and skeptical.",
        )
        style_only_result = style_only_session.finish(
            draft_text="What I need now is a quick answer and be tight and brief and skeptical.",
            wait_timeout_seconds=0.01,
        )
        assert not style_only_result.safe_update
        assert "no-hindi-value" in style_only_result.reject_reasons
        assert style_only_result.quality["hindi_value"]["has_hindi_value"] is False

        mostly_verbatim_dir = Path(tmp) / "mostly-verbatim"
        mostly_verbatim_dir.mkdir()
        mostly_verbatim_path = mostly_verbatim_dir / "chunk-000.wav"
        mostly_verbatim_path.write_bytes(b"5" * 256)
        os.utime(mostly_verbatim_path, (now - 1.0, now - 1.0))
        mostly_verbatim_session = HindiStreamSession(run_id="mostly-verbatim-regression", chunk_dir=mostly_verbatim_dir)
        mostly_verbatim_session._risk = True
        mostly_verbatim_session._risk_reasons.append("language:hi")
        mostly_verbatim_session._detected[0] = DetectorChunk(
            index=0,
            path=str(mostly_verbatim_path),
            language="hi",
            language_probability=0.90,
            seconds=0.1,
            risk=True,
        )
        mostly_verbatim_session._chunks[0] = StreamChunk(
            index=0,
            path=str(mostly_verbatim_path),
            duration_seconds=8.0,
            compute_seconds=0.5,
            text=(
                "See the way MCPs work is that there's an API layer par uske saath na "
                "guidelines vagairah bhi hota hai as well. So, the guidelines and "
                "documentation are all there. So, the API layer functions well."
            ),
        )
        mostly_verbatim_result = mostly_verbatim_session.finish(
            draft_text=(
                "The way MCPs work is that there is an API layer and there are guidelines "
                "as well. So, the guidelines and documentation are all there. So, the API layer functions well."
            ),
            wait_timeout_seconds=0.01,
        )
        assert not mostly_verbatim_result.safe_update
        assert "no-default-meaning-gain" in mostly_verbatim_result.reject_reasons

        romanized_garbage_dir = Path(tmp) / "romanized-garbage"
        romanized_garbage_dir.mkdir()
        romanized_garbage_path = romanized_garbage_dir / "chunk-000.wav"
        romanized_garbage_path.write_bytes(b"7" * 256)
        os.utime(romanized_garbage_path, (now - 1.0, now - 1.0))
        romanized_garbage_session = HindiStreamSession(
            run_id="romanized-garbage-regression",
            chunk_dir=romanized_garbage_dir,
        )
        romanized_garbage_session._risk = True
        romanized_garbage_session._risk_reasons.append("language:hi")
        romanized_garbage_session._detected[0] = DetectorChunk(
            index=0,
            path=str(romanized_garbage_path),
            language="hi",
            language_probability=0.90,
            seconds=0.1,
            risk=True,
        )
        romanized_garbage_session._chunks[0] = StreamChunk(
            index=0,
            path=str(romanized_garbage_path),
            duration_seconds=8.0,
            compute_seconds=0.5,
            text=(
                "See the way MCPs work is that there's a API layer पर उसके साथ ना "
                "guidelines वगैरह भी होता है सुकाई लाईन documentation ही सब होता है "
                "ताकि तथा API layer functions वे"
            ),
        )
        romanized_garbage_result = romanized_garbage_session.finish(
            draft_text=(
                "The way MCPs work is that there is an API layer and there are guidelines "
                "as well. So, the guidelines and documentation are all there. So, the API layer functions well."
            ),
            wait_timeout_seconds=0.01,
        )
        assert romanized_garbage_result.safe_update
        assert romanized_garbage_result.route == "hindi_stream_safe"
        assert "sukai" not in romanized_garbage_result.text
        assert "laina" not in romanized_garbage_result.text
        assert "API layer functions well" in romanized_garbage_result.text
        assert romanized_garbage_result.quality["candidate_sanitize"]["accepted"] is True
        assert romanized_garbage_result.quality["candidate_sanitize"]["removed_tokens"] == ["sukai", "laina", "tatha"]

        subagent_sanitize_dir = Path(tmp) / "subagent-sanitize"
        subagent_sanitize_dir.mkdir()
        subagent_sanitize_path = subagent_sanitize_dir / "chunk-000.wav"
        subagent_sanitize_path.write_bytes(b"8" * 256)
        os.utime(subagent_sanitize_path, (now - 1.0, now - 1.0))
        subagent_sanitize_session = HindiStreamSession(
            run_id="subagent-sanitize-regression",
            chunk_dir=subagent_sanitize_dir,
        )
        subagent_sanitize_session._risk = True
        subagent_sanitize_session._risk_reasons.append("language:hi")
        subagent_sanitize_session._detected[0] = DetectorChunk(
            index=0,
            path=str(subagent_sanitize_path),
            language="hi",
            language_probability=0.90,
            seconds=0.1,
            risk=True,
        )
        subagent_sanitize_session._chunks[0] = StreamChunk(
            index=0,
            path=str(subagent_sanitize_path),
            duration_seconds=8.0,
            compute_seconds=0.5,
            text=(
                "Higher पर in order for this to be solved आपको वैसा करना होगा कि you have to structure it "
                "into four sub agents हर agent का काम अलगअलग होगा तो one region will do something "
                "दूसरा region कुछ और करेगा तीसरा कुछ और and so on and so forth"
            ),
        )
        subagent_sanitize_result = subagent_sanitize_session.finish(
            draft_text=(
                "Hi, in order for this to be solved, you have to structure it into four sub-agents. "
                "Each agent will have a different task. One agent will do something, the other will do something else, etc."
            ),
            wait_timeout_seconds=0.01,
        )
        assert not subagent_sanitize_result.safe_update
        assert subagent_sanitize_result.text.startswith("Hi, in order for this to be solved")
        assert subagent_sanitize_result.quality["candidate_sanitize"]["accepted"] is False
        assert subagent_sanitize_result.quality["raw_tail"]["accepted"] is False
        assert "dangling-transition" in subagent_sanitize_result.quality["candidate_sanitize"]["reject_reasons"]

        short_tail_dir = Path(tmp) / "short-tail"
        short_tail_dir.mkdir()
        short_tail_path = short_tail_dir / "chunk-000.wav"
        short_tail_path.write_bytes(b"8" * 256)
        os.utime(short_tail_path, (now - 1.0, now - 1.0))
        short_tail_session = HindiStreamSession(
            run_id="short-tail-regression",
            chunk_dir=short_tail_dir,
        )
        short_tail_session._risk = True
        short_tail_session._risk_reasons.append("language:hi")
        short_tail_session._detected[0] = DetectorChunk(
            index=0,
            path=str(short_tail_path),
            language="hi",
            language_probability=0.90,
            seconds=0.1,
            risk=True,
        )
        short_tail_session._chunks[0] = StreamChunk(
            index=0,
            path=str(short_tail_path),
            duration_seconds=4.0,
            compute_seconds=0.5,
            text="हाँ यार क्या करें बता What should I do? You tell me. You have to answer my question.",
        )
        old_raw_tail_env = os.environ.get("RAMBLEFIX_HINDI_STREAM_RAW_TAIL_RESCUE")
        os.environ.pop("RAMBLEFIX_HINDI_STREAM_RAW_TAIL_RESCUE", None)
        try:
            short_tail_result = short_tail_session.finish(
                draft_text="What should I do now?",
                wait_timeout_seconds=0.01,
            )
        finally:
            if old_raw_tail_env is None:
                os.environ.pop("RAMBLEFIX_HINDI_STREAM_RAW_TAIL_RESCUE", None)
            else:
                os.environ["RAMBLEFIX_HINDI_STREAM_RAW_TAIL_RESCUE"] = old_raw_tail_env
        assert short_tail_result.safe_update
        assert short_tail_result.route == "hindi_stream_safe"
        assert "haan kya bata" in short_tail_result.text
        assert "answer my question" in short_tail_result.text

        unsafe_sanitize_dir = Path(tmp) / "unsafe-sanitize"
        unsafe_sanitize_dir.mkdir()
        unsafe_sanitize_path = unsafe_sanitize_dir / "chunk-000.wav"
        unsafe_sanitize_path.write_bytes(b"9" * 256)
        os.utime(unsafe_sanitize_path, (now - 1.0, now - 1.0))
        unsafe_sanitize_session = HindiStreamSession(
            run_id="unsafe-sanitize-regression",
            chunk_dir=unsafe_sanitize_dir,
        )
        unsafe_sanitize_session._risk = True
        unsafe_sanitize_session._risk_reasons.append("language:hi")
        unsafe_sanitize_session._detected[0] = DetectorChunk(
            index=0,
            path=str(unsafe_sanitize_path),
            language="hi",
            language_probability=0.90,
            seconds=0.1,
            risk=True,
        )
        unsafe_sanitize_session._chunks[0] = StreamChunk(
            index=0,
            path=str(unsafe_sanitize_path),
            duration_seconds=8.0,
            compute_seconds=0.5,
            text=(
                "Okay now for this goal think through what a best design would be to design some model that can fix Hindi. "
                "There can be some super light thing and that attracts if centre if it has any Hindi in it and if it does "
                "then it reveals like a different path, likely less than three seconds."
            ),
        )
        unsafe_sanitize_result = unsafe_sanitize_session.finish(
            draft_text=(
                "Now for this goal, think through what the best design would be to design some model that can fix Hindi."
            ),
            wait_timeout_seconds=0.01,
        )
        assert not unsafe_sanitize_result.safe_update
        assert unsafe_sanitize_result.quality["candidate_sanitize"]["accepted"] is False

        richer_dir = Path(tmp) / "richer"
        richer_dir.mkdir()
        richer_path = richer_dir / "chunk-000.wav"
        richer_path.write_bytes(b"6" * 256)
        os.utime(richer_path, (now - 1.0, now - 1.0))
        richer_session = HindiStreamSession(run_id="richer-regression", chunk_dir=richer_dir)
        richer_session._risk = True
        richer_session._risk_reasons.append("language:hi")
        richer_session._detected[0] = DetectorChunk(
            index=0,
            path=str(richer_path),
            language="hi",
            language_probability=0.90,
            seconds=0.1,
            risk=True,
        )
        richer_session._chunks[0] = StreamChunk(
            index=0,
            path=str(richer_path),
            duration_seconds=8.0,
            compute_seconds=0.5,
            text=(
                "haan vahi so in order for this to work aisa ho hi nahi sakta ki ismein "
                "vo matlab vo baatein nahi ho payengi matlab kaise samajho mein aapko "
                "it should not be like that right it should be direct and brief."
            ),
        )
        richer_result = richer_session.finish(
            draft_text="So in order for this to work, it should not be that. It should be direct and brief.",
            wait_timeout_seconds=0.01,
        )
        assert richer_result.safe_update
        assert richer_result.reject_reasons == []

        short_dir = Path(tmp) / "short"
        short_dir.mkdir()
        short_path = short_dir / "chunk-000.wav"
        short_path.write_bytes(b"2" * 256)
        os.utime(short_path, (now, now))
        short_session = HindiStreamSession(run_id="short-regression", chunk_dir=short_dir)
        short_session._risk = True
        short_session._risk_reasons.append("language:hi")
        short_session._detected[0] = DetectorChunk(
            index=0,
            path=str(short_path),
            language="hi",
            language_probability=0.90,
            seconds=0.1,
            risk=True,
        )
        short_result = short_session.finish(draft_text=draft, wait_timeout_seconds=0.01)
        assert not short_result.safe_update
        assert "pending-chunks" not in short_result.reject_reasons
        assert "empty-final" in short_result.reject_reasons


if __name__ == "__main__":
    main()
