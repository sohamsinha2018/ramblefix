from __future__ import annotations

import re
import threading
import time
import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ramblefix.config import DEFAULT_QWEN3_ASR_MLX_HINGLISH_MODEL
from ramblefix.hindi_polish import HindiRiskResult, detect_hindi_risk


_QWEN3_CHUNK_SESSIONS: dict[tuple[str, int], object] = {}


@dataclass(frozen=True)
class ChunkPolishResult:
    text: str
    engine: str
    seconds: float
    release_tail_seconds: float
    safe_update: bool
    reject_reasons: list[str]
    chunks: list[dict[str, Any]] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkHindiPolishResult:
    text: str
    raw_text: str
    engine: str
    route: str
    seconds: float
    risk: HindiRiskResult
    safe_update: bool
    reject_reasons: list[str]
    release_tail_seconds: float
    chunks: list[dict[str, Any]] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)
    error: str = ""


COMMON_ENGLISH_TOKENS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "being",
    "could",
    "does",
    "done",
    "from",
    "have",
    "into",
    "like",
    "maybe",
    "need",
    "okay",
    "only",
    "right",
    "same",
    "should",
    "some",
    "that",
    "then",
    "there",
    "thing",
    "this",
    "through",
    "what",
    "when",
    "where",
    "which",
    "will",
    "with",
    "work",
    "would",
    "your",
}

ROMAN_HINDI_TOKENS = {
    "aaj",
    "aajkal",
    "aap",
    "aapko",
    "aata",
    "aati",
    "aakar",
    "aaya",
    "abhi",
    "achcha",
    "achchha",
    "agar",
    "aisa",
    "aise",
    "alag",
    "alaava",
    "alawa",
    "aur",
    "baad",
    "baar",
    "baat",
    "baatein",
    "baaten",
    "bahan",
    "bangaal",
    "baithenge",
    "bata",
    "bhai",
    "bhi",
    "bol",
    "bola",
    "bolna",
    "bolte",
    "chaahie",
    "chahiye",
    "chal",
    "chala",
    "chale",
    "chalta",
    "chalti",
    "chhod",
    "de",
    "dekh",
    "den",
    "diya",
    "dikha",
    "dil",
    "din",
    "doosra",
    "dusra",
    "dusara",
    "eki",
    "factor",
    "ghi",
    "gaali",
    "godi",
    "haan",
    "hai",
    "hain",
    "har",
    "hamara",
    "hamaara",
    "hame",
    "hamen",
    "hi",
    "hoga",
    "hona",
    "hone",
    "hongi",
    "honge",
    "honi",
    "hoon",
    "hota",
    "hote",
    "hoti",
    "hua",
    "hui",
    "humein",
    "hamein",
    "isake",
    "ismein",
    "iske",
    "ismen",
    "ka",
    "kaam",
    "kaaphi",
    "kaise",
    "kar",
    "kara",
    "karake",
    "karen",
    "karega",
    "karke",
    "karna",
    "karne",
    "ki",
    "kuch",
    "kuchh",
    "kafi",
    "kabhi",
    "kaatna",
    "kaali",
    "kare",
    "karegi",
    "kisne",
    "kya",
    "karta",
    "karte",
    "karti",
    "khelna",
    "kyon",
    "laudi",
    "laga",
    "lagta",
    "likhane",
    "main",
    "matlab",
    "mein",
    "milega",
    "meri",
    "mere",
    "mujhe",
    "nahin",
    "nahi",
    "na",
    "naukri",
    "pae",
    "paega",
    "paega",
    "paengi",
    "paap",
    "paapi",
    "paayega",
    "paaye",
    "payega",
    "payengi",
    "pata",
    "pashchim",
    "par",
    "pichhali",
    "pichli",
    "raha",
    "rahe",
    "rahi",
    "roz",
    "sabka",
    "sab",
    "saara",
    "saare",
    "saari",
    "sare",
    "sari",
    "sakate",
    "sakta",
    "sakte",
    "samajh",
    "samajha",
    "samajhna",
    "samajho",
    "samksipta",
    "saath",
    "sankshipt",
    "taaki",
    "taki",
    "teesra",
    "tera",
    "tere",
    "teri",
    "tha",
    "thik",
    "theek",
    "tisra",
    "tisara",
    "to",
    "toone",
    "tu",
    "tum",
    "tune",
    "uttar",
    "uske",
    "usmen",
    "usmein",
    "vagairah",
    "vah",
    "vahi",
    "vaise",
    "vaisa",
    "vicara",
    "vichar",
    "vichaar",
    "vimarsa",
    "vimarsh",
    "vo",
    "woh",
    "ya",
    "yaar",
    "yah",
    "yaha",
    "jahaan",
    "jahan",
    "ye",
    "yeh",
    "uttara",
    "hawabaazi",
    "hawaabaazi",
    "havaavaaji",
}

HINDI_DISCOURSE_ONLY_TOKENS = {
    "aur",
    "bhai",
    "bhi",
    "de",
    "dekh",
    "haan",
    "hai",
    "hain",
    "hi",
    "matlab",
    "na",
    "par",
    "raha",
    "rahe",
    "rahi",
    "theek",
    "tha",
    "thik",
    "to",
    "vah",
    "vo",
    "woh",
    "ya",
    "yaar",
    "yah",
    "yaha",
    "ye",
}

ROMAN_HINDI_SPELLING_FIXES = {
    "chaahie": "chahiye",
    "chaahiye": "chahiye",
    "hamaara": "hamara",
    "hamein": "hame",
    "hamen": "hame",
    "havaavaaji": "hawabaazi",
    "hawaabaazi": "hawabaazi",
    "kuchh": "kuch",
    "paega": "payega",
    "thik": "theek",
    "vichaar": "vichar",
}

MEANING_FIRST_STOP_TOKENS = COMMON_ENGLISH_TOKENS | HINDI_DISCOURSE_ONLY_TOKENS | {
    "aap",
    "aapko",
    "agar",
    "aisa",
    "alag",
    "bhi",
    "hame",
    "hoga",
    "hona",
    "humein",
    "karna",
    "karne",
    "kuch",
    "kya",
    "nahi",
    "par",
    "raha",
    "rahe",
    "rahi",
    "saath",
    "sab",
    "uske",
    "vaisa",
    "vagairah",
}

STRICT_ALLOWED_NEW_ENGLISH_TOKENS = {
    "and",
    "answer",
    "answered",
    "are",
    "api",
    "but",
    "can",
    "cannot",
    "do",
    "etc",
    "exactly",
    "for",
    "forth",
    "from",
    "have",
    "in",
    "into",
    "is",
    "it",
    "job",
    "like",
    "maybe",
    "mcp",
    "not",
    "okay",
    "of",
    "on",
    "one",
    "or",
    "question",
    "right",
    "same",
    "see",
    "so",
    "that",
    "tell",
    "thanks",
    "the",
    "then",
    "there",
    "this",
    "to",
    "what",
    "will",
    "with",
    "you",
}


def normalize_roman_hindi_spelling(text: str) -> str:
    if not text:
        return text

    pattern = r"\b(" + "|".join(
        re.escape(key) for key in sorted(ROMAN_HINDI_SPELLING_FIXES, key=len, reverse=True)
    ) + r")\b"

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        replacement = ROMAN_HINDI_SPELLING_FIXES.get(raw.lower())
        if replacement is None:
            return raw
        if raw[:1].isupper():
            return replacement[:1].upper() + replacement[1:]
        return replacement

    return re.sub(pattern, replace, text, flags=re.IGNORECASE)


WITNESS_STOP_TOKENS = COMMON_ENGLISH_TOKENS | STRICT_ALLOWED_NEW_ENGLISH_TOKENS | {
    "actually",
    "always",
    "anything",
    "basically",
    "best",
    "better",
    "cool",
    "f" + "ucker",
    "going",
    "good",
    "great",
    "hard",
    "know",
    "look",
    "lower",
    "mother",
    "really",
    "searching",
    "something",
    "things",
    "whatever",
}


def chunk_polish_hindi_if_needed(
    audio_path: str | Path,
    *,
    draft_text: str,
    low_confidence_threshold: float = 0.50,
    target_seconds: float = 8.0,
    min_seconds: float = 5.0,
    max_seconds: float = 9.0,
    lookaround_seconds: float = 1.5,
    max_release_tail_seconds: float = 3.0,
) -> ChunkHindiPolishResult:
    started = time.perf_counter()
    try:
        risk = detect_hindi_risk(
            audio_path,
            draft_text=draft_text,
            low_confidence_threshold=low_confidence_threshold,
        )
    except Exception as exc:  # noqa: BLE001
        risk = HindiRiskResult(
            risk=False,
            language=None,
            probability=None,
            seconds=round(time.perf_counter() - started, 3),
            engine="faster-whisper.detect-language:tiny:int8",
            reasons=[],
        )
        return ChunkHindiPolishResult(
            text=draft_text,
            raw_text="",
            engine="hindi-chunk-polish",
            route="hindi_chunk_polish_detector_error",
            seconds=round(time.perf_counter() - started, 3),
            risk=risk,
            safe_update=False,
            reject_reasons=["detector-error"],
            release_tail_seconds=0.0,
            quality={"error": f"{type(exc).__name__}: {exc}"},
            error=f"{type(exc).__name__}: {exc}",
        )

    if not risk.risk:
        return ChunkHindiPolishResult(
            text=draft_text,
            raw_text="",
            engine="hindi-chunk-polish",
            route="hindi_chunk_polish_skipped",
            seconds=round(time.perf_counter() - started, 3),
            risk=risk,
            safe_update=False,
            reject_reasons=[],
            release_tail_seconds=0.0,
            quality={
                "hindi_risk": False,
                "risk_reasons": [],
                "detector_seconds": risk.seconds,
                "detector_language": risk.language,
                "detector_probability": risk.probability,
            },
        )

    chunk = chunk_polish_audio(
        audio_path,
        draft_text=draft_text,
        target_seconds=target_seconds,
        min_seconds=min_seconds,
        max_seconds=max_seconds,
        lookaround_seconds=lookaround_seconds,
        max_release_tail_seconds=max_release_tail_seconds,
    )
    quality = {
        **chunk.quality,
        "hindi_risk": True,
        "risk_reasons": risk.reasons,
        "detector_seconds": risk.seconds,
        "detector_language": risk.language,
        "detector_probability": risk.probability,
        "safe_update": chunk.safe_update,
        "reject_reasons": chunk.reject_reasons,
        "release_tail_seconds": chunk.release_tail_seconds,
        "chunk_wall_seconds": chunk.seconds,
    }
    return ChunkHindiPolishResult(
        text=chunk.text if chunk.safe_update else draft_text,
        raw_text=chunk.text,
        engine=f"hindi_chunk_polish:{chunk.engine}",
        route="hindi_chunk_polish_safe" if chunk.safe_update else "hindi_chunk_polish_rejected",
        seconds=round(time.perf_counter() - started, 3),
        risk=risk,
        safe_update=chunk.safe_update,
        reject_reasons=chunk.reject_reasons,
        release_tail_seconds=chunk.release_tail_seconds,
        chunks=chunk.chunks,
        quality=quality,
    )


def chunk_polish_audio(
    audio_path: str | Path,
    *,
    draft_text: str,
    model: str = DEFAULT_QWEN3_ASR_MLX_HINGLISH_MODEL,
    target_seconds: float = 8.0,
    min_seconds: float = 5.0,
    max_seconds: float = 9.0,
    lookaround_seconds: float = 1.5,
    max_release_tail_seconds: float = 3.0,
) -> ChunkPolishResult:
    """Simulate streaming Hindi polish by transcribing silence-aware chunks.

    This is an offline architecture lab. It estimates release-to-polish time
    assuming closed chunks are processed while the user is still speaking.
    """
    started = time.perf_counter()
    try:
        from mlx_qwen3_asr import load_audio
    except ImportError as exc:
        raise RuntimeError("mlx-qwen3-asr is not installed") from exc

    sample_rate = 16000
    audio = np.asarray(load_audio(str(Path(audio_path).expanduser().resolve()), sr=sample_rate), dtype=np.float32)
    session = _session_for_model(model)
    chunks = split_silence_chunks(
        audio,
        sample_rate=sample_rate,
        target_seconds=target_seconds,
        min_seconds=min_seconds,
        max_seconds=max_seconds,
        lookaround_seconds=lookaround_seconds,
    )

    chunk_payloads: list[dict[str, Any]] = []
    text_parts: list[str] = []
    compute_done = 0.0
    duration = len(audio) / sample_rate if sample_rate else 0.0
    for index, (start, end) in enumerate(chunks):
        chunk = audio[start:end]
        if len(chunk) < int(0.35 * sample_rate):
            continue
        chunk_started = time.perf_counter()
        result = session.transcribe(chunk)
        compute_seconds = round(time.perf_counter() - chunk_started, 3)
        chunk_text = _extract_text(result)
        text_parts.append(chunk_text)
        end_seconds = end / sample_rate
        compute_done = max(compute_done, end_seconds) + compute_seconds
        chunk_payloads.append(
            {
                "index": index,
                "start_seconds": round(start / sample_rate, 3),
                "end_seconds": round(end_seconds, 3),
                "duration_seconds": round((end - start) / sample_rate, 3),
                "compute_seconds": compute_seconds,
                "text": chunk_text,
            }
        )

    text = stitch_chunk_texts(text_parts)
    release_tail = round(max(0.0, compute_done - duration), 3)
    reject_reasons = update_reject_reasons(
        draft_text=draft_text,
        final_text=text,
        release_tail_seconds=release_tail,
        max_release_tail_seconds=max_release_tail_seconds,
        allow_roman_hindi=True,
        strict_new_english=True,
    )
    chunk_durations = [float(chunk["duration_seconds"]) for chunk in chunk_payloads]
    compute_times = [float(chunk["compute_seconds"]) for chunk in chunk_payloads]
    return ChunkPolishResult(
        text=text,
        engine=f"mlx-qwen3-asr-chunked:{model}",
        seconds=round(time.perf_counter() - started, 3),
        release_tail_seconds=release_tail,
        safe_update=not reject_reasons,
        reject_reasons=reject_reasons,
        chunks=chunk_payloads,
        quality={
            "chunk_count": len(chunk_payloads),
            "target_seconds": target_seconds,
            "min_seconds": min_seconds,
            "max_seconds": max_seconds,
            "lookaround_seconds": lookaround_seconds,
            "max_chunk_compute_seconds": round(max(compute_times), 3) if compute_times else 0.0,
            "keeps_up": all(compute <= duration for compute, duration in zip(compute_times, chunk_durations, strict=False)),
        },
    )


def split_silence_chunks(
    audio: np.ndarray,
    *,
    sample_rate: int,
    target_seconds: float,
    min_seconds: float,
    max_seconds: float,
    lookaround_seconds: float,
) -> list[tuple[int, int]]:
    frame = max(1, int(0.020 * sample_rate))
    hop = max(1, int(0.010 * sample_rate))
    rms = _frame_rms(audio, frame=frame, hop=hop)
    median_rms = float(np.median(rms)) if rms.size else 1.0
    total = int(len(audio))
    start = 0
    chunks: list[tuple[int, int]] = []
    min_samples = max(1, int(min_seconds * sample_rate))
    max_samples = max(min_samples, int(max_seconds * sample_rate))
    target_samples = max(min_samples, int(target_seconds * sample_rate))
    look_samples = max(0, int(lookaround_seconds * sample_rate))

    while total - start > max_samples:
        ideal = start + target_samples
        lower = max(start + min_samples, ideal - look_samples)
        upper = min(start + max_samples, ideal + look_samples, total - min_samples)
        if upper <= lower:
            boundary = min(start + target_samples, total)
        else:
            boundary = _quietest_boundary(
                rms,
                median_rms=median_rms,
                hop=hop,
                frame=frame,
                lower=lower,
                upper=upper,
                ideal=ideal,
                sample_rate=sample_rate,
            )
        chunks.append((start, boundary))
        start = boundary
    if start < total:
        chunks.append((start, total))
    return chunks


def stitch_chunk_texts(parts: list[str]) -> str:
    output = ""
    for part in parts:
        text = part.strip()
        if not text:
            continue
        if not output:
            output = text
            continue
        output = _append_with_overlap(output, text)
    return output.strip()


def update_reject_reasons(
    *,
    draft_text: str,
    final_text: str,
    release_tail_seconds: float,
    max_release_tail_seconds: float,
    allow_roman_hindi: bool = False,
    strict_new_english: bool = False,
) -> list[str]:
    reasons: list[str] = []
    if not final_text.strip():
        reasons.append("empty-final")
    if release_tail_seconds > max_release_tail_seconds:
        reasons.append("tail>threshold")
    if "\ufffd" in final_text or "\uFFFD" in final_text or "�" in final_text:
        reasons.append("replacement-char")
    if _has_runaway_repetition(final_text):
        reasons.append("repetition")
    reasons.extend(_protected_term_reject_reasons(draft_text, final_text))
    suspicious, tokens, reason = _has_suspicious_new_english(
        draft_text,
        final_text,
        allow_roman_hindi=allow_roman_hindi,
        strict_new_english=strict_new_english,
    )
    if suspicious:
        reasons.append(reason + ":" + ",".join(tokens[:5]))
    return reasons


def witness_supported_new_terms(
    *,
    draft_text: str,
    candidate_text: str,
    witness_text: str,
) -> dict[str, list[str]]:
    """Find substantive candidate terms supported by an independent local ASR.

    This is deliberately token-level. Whole-transcript replacement is risky,
    but repeated local evidence for a new content term can justify a background
    update when the fast transcript likely misheard a noun or technical word.
    """
    draft_tokens = set(_english_content_tokens(draft_text, min_length=3))
    witness_tokens = set(_english_content_tokens(witness_text, min_length=3))
    candidate_new: list[str] = []
    for token in _english_content_tokens(candidate_text, min_length=4):
        if token in draft_tokens or _is_plural_or_singular_match(token, draft_tokens):
            continue
        if token in WITNESS_STOP_TOKENS or token in ROMAN_HINDI_TOKENS:
            continue
        if token not in candidate_new:
            candidate_new.append(token)

    supported = [
        token
        for token in candidate_new
        if _token_supported_by_witness(token, witness_tokens)
    ]
    unsupported = [token for token in candidate_new if token not in supported]
    return {"supported": supported, "unsupported": unsupported}


def witness_can_accept_rejected_candidate(
    *,
    draft_text: str,
    candidate_text: str,
    witness_text: str,
    reject_reasons: list[str],
) -> dict[str, Any]:
    allowed_reject_prefixes = ("new-english-", "no-hindi-value")
    blocking = [
        reason
        for reason in reject_reasons
        if not reason.startswith(allowed_reject_prefixes)
    ]
    support = witness_supported_new_terms(
        draft_text=draft_text,
        candidate_text=candidate_text,
        witness_text=witness_text,
    )
    supported = support["supported"]
    unsupported = support["unsupported"]
    rejected_tokens = _rejected_new_english_tokens(reject_reasons)
    witness_tokens = set(_english_content_tokens(witness_text, min_length=3))
    unsupported_rejected_tokens = [
        token
        for index, token in enumerate(rejected_tokens)
        if token not in supported
        and not _token_supported_by_witness(token, witness_tokens)
        and not _split_compound_supported_by_witness(index, rejected_tokens, witness_tokens)
    ]
    max_unsupported = max(4, len(supported) * 3)
    accepted = bool(
        reject_reasons
        and not blocking
        and supported
        and not unsupported_rejected_tokens
        and len(unsupported) <= max_unsupported
    )
    return {
        "accepted": accepted,
        "supported_new_terms": supported,
        "unsupported_new_terms": unsupported,
        "rejected_new_terms": rejected_tokens,
        "unsupported_rejected_new_terms": unsupported_rejected_tokens,
        "blocking_reject_reasons": blocking,
        "max_unsupported_new_terms": max_unsupported,
    }


def _rejected_new_english_tokens(reject_reasons: list[str]) -> list[str]:
    tokens: list[str] = []
    for reason in reject_reasons:
        if not reason.startswith(("new-english-token:", "new-english-run:", "new-english-count:")):
            continue
        _, raw = reason.split(":", 1)
        for token in raw.split(","):
            clean = token.strip().lower()
            if clean and clean not in tokens:
                tokens.append(clean)
    return tokens


def _split_compound_supported_by_witness(
    index: int,
    tokens: list[str],
    witness_tokens: set[str],
) -> bool:
    token = tokens[index]
    compounds: list[str] = []
    if index > 0:
        compounds.append(tokens[index - 1] + token)
    if index + 1 < len(tokens):
        compounds.append(token + tokens[index + 1])
    for compound in compounds:
        if len(compound) < 8:
            continue
        if _token_supported_by_witness(compound, witness_tokens):
            return True
        for witness in witness_tokens:
            if len(witness) >= 8 and difflib.SequenceMatcher(None, compound, witness).ratio() >= 0.82:
                return True
    return False


def hindi_value_delta(draft_text: str, final_text: str) -> dict[str, Any]:
    draft_roman = _roman_hindi_token_set(draft_text)
    final_roman = _roman_hindi_token_set(final_text)
    final_devanagari = _devanagari_count(final_text)
    if final_devanagari:
        final_roman |= _roman_hindi_token_set(romanize_devanagari_for_hinglish(final_text))
    new_roman = sorted(final_roman - draft_roman)
    substantive_new_roman = sorted(token for token in new_roman if token not in HINDI_DISCOURSE_ONLY_TOKENS)
    return {
        "devanagari_chars": final_devanagari,
        "new_roman_hindi_tokens": new_roman,
        "substantive_new_roman_hindi_tokens": substantive_new_roman,
        "has_hindi_value": bool(substantive_new_roman),
    }


def meaning_first_update_reject_reasons(draft_text: str, final_text: str) -> list[str]:
    draft_tokens = _meaning_content_tokens(draft_text)
    final_tokens = _meaning_content_tokens(final_text)
    if len(draft_tokens) < 4 or len(final_tokens) < 4:
        return []

    draft_set = set(draft_tokens)
    final_set = set(final_tokens)
    retained_ratio = len(draft_set & final_set) / max(1, len(draft_set))
    new_count = len(final_set - draft_set)
    missing_count = len(draft_set - final_set)
    hindi_value = hindi_value_delta(draft_text, final_text)
    substantive_hindi_count = len(hindi_value["substantive_new_roman_hindi_tokens"])
    if _ends_with_incomplete_connector(final_text):
        return ["incomplete-tail"]
    if substantive_hindi_count >= 5 and len(final_tokens) >= 5:
        return []
    if substantive_hindi_count >= 5 and retained_ratio >= 0.65:
        return []
    if retained_ratio < 0.78 and new_count < 6:
        return [f"default-meaning-drop:{retained_ratio:.2f}"]
    if new_count < 3:
        return ["no-default-meaning-gain"]
    if retained_ratio < 0.85 and missing_count >= 2:
        return [f"default-meaning-tradeoff:{retained_ratio:.2f}"]
    return []


def _meaning_content_tokens(text: str) -> list[str]:
    romanized = romanize_devanagari_for_hinglish(text)
    return [
        token
        for token in _tokens(romanized)
        if len(token) >= 4 and token not in MEANING_FIRST_STOP_TOKENS
    ]


def _roman_hindi_token_set(text: str) -> set[str]:
    return {token for token in _tokens(text) if token in ROMAN_HINDI_TOKENS}


def _ends_with_incomplete_connector(text: str) -> bool:
    tokens = _tokens(romanize_devanagari_for_hinglish(text))
    if not tokens:
        return False
    return tokens[-1] in {
        "aur",
        "but",
        "ki",
        "ke",
        "ka",
        "mein",
        "me",
        "par",
        "to",
        "with",
    }


def _devanagari_count(text: str) -> int:
    return sum(1 for char in text if "\u0900" <= char <= "\u097f")


def romanize_devanagari_for_hinglish(text: str) -> str:
    if not _devanagari_count(text):
        return text
    try:
        from aksharamukha import transliterate
        from text_unidecode import unidecode
    except Exception:  # noqa: BLE001
        return text

    prepared = text.replace("हाँ", "haan").replace("हां", "haan").replace("नहीं", "nahi")
    romanized = unidecode(transliterate.process("Devanagari", "IAST", prepared))
    return _polish_roman_hindi(romanized)


def _polish_roman_hindi(text: str) -> str:
    replacements = {
        r"\bdekha\b": "dekh",
        r"\bvaha\b": "woh",
        r"\bsaba\b": "sab",
        r"\bkarane\b": "karne",
        r"\bkarake\b": "karke",
        r"\bkucha\b": "kuch",
        r"\bnahim\b": "nahi",
        r"\bagara\b": "agar",
        r"\bhamem\b": "humein",
        r"\bpara\b": "par",
        r"\busake\b": "uske",
        r"\bsatha\b": "saath",
        r"\bvagairaha\b": "vagairah",
        r"\bapako\b": "aapko",
        r"\bapa\b": "aap",
        r"\bkarana\b": "karna",
        r"\bhara\b": "har",
        r"\bkama\b": "kaam",
        r"\balagaalaga\b": "alag alag",
        r"\bcahie\b": "chahiye",
        r"\bsakata\b": "sakta",
        r"\bisamem\b": "ismein",
        r"\bmem\b": "mein",
        r"\bmatalaba\b": "matlab",
        r"\bbatem\b": "baatein",
        r"\bpaemgi\b": "payengi",
        r"\baura\b": "aur",
        r"\byaha\b": "yeh",
        r"\bdem\b": "den",
    }
    output = text
    for pattern, replacement in replacements.items():
        output = re.sub(pattern, replacement, output, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", output).strip()


def _quietest_boundary(
    rms: np.ndarray,
    *,
    median_rms: float,
    hop: int,
    frame: int,
    lower: int,
    upper: int,
    ideal: int,
    sample_rate: int,
) -> int:
    first = max(0, lower // hop)
    last = min(len(rms) - 1, max(first, upper // hop))
    best: tuple[float, int] | None = None
    for frame_index in range(first, last + 1):
        position = (frame_index * hop) + (frame // 2)
        if position <= lower or position >= upper:
            continue
        energy = float(rms[frame_index]) / (median_rms + 1e-9)
        distance_seconds = abs(position - ideal) / sample_rate
        score = energy + (0.10 * distance_seconds)
        if best is None or score < best[0]:
            best = (score, position)
    return best[1] if best else ideal


def _frame_rms(audio: np.ndarray, *, frame: int, hop: int) -> np.ndarray:
    if len(audio) < frame:
        return np.array([], dtype=np.float32)
    values = []
    for start in range(0, len(audio) - frame + 1, hop):
        segment = audio[start : start + frame]
        values.append(float(np.sqrt(np.mean(segment * segment) + 1e-12)))
    return np.asarray(values, dtype=np.float32)


def _append_with_overlap(current: str, addition: str) -> str:
    current_tokens = current.split()
    addition_tokens = addition.split()
    max_overlap = min(12, len(current_tokens), len(addition_tokens))
    for width in range(max_overlap, 0, -1):
        if [t.lower() for t in current_tokens[-width:]] == [t.lower() for t in addition_tokens[:width]]:
            return " ".join(current_tokens + addition_tokens[width:])
    return current.rstrip() + " " + addition.lstrip()


def _has_runaway_repetition(text: str) -> bool:
    tokens = _tokens(text)
    if len(tokens) < 3:
        return False
    for width in range(1, min(4, len(tokens) // 3) + 1):
        for start in range(0, len(tokens) - (3 * width) + 1):
            first = tokens[start : start + width]
            second = tokens[start + width : start + (2 * width)]
            third = tokens[start + (2 * width) : start + (3 * width)]
            if first == second == third:
                return True
    return False


def _protected_term_reject_reasons(draft_text: str, final_text: str) -> list[str]:
    draft_terms = _protected_terms(draft_text)
    final_terms = _protected_terms(final_text)
    if not draft_terms and not final_terms:
        return []

    draft_normal = _normal_terms(draft_text)
    final_normal = _normal_terms(final_text)
    reasons: list[str] = []
    missing = sorted(term for term in draft_terms if term not in final_normal)
    if missing:
        reasons.append("protected-term-missing:" + ",".join(missing[:5]))

    introduced = sorted(term for term in final_terms if draft_terms and term not in draft_normal)
    if introduced:
        reasons.append("protected-term-new:" + ",".join(introduced[:5]))
    return reasons


def _protected_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw in re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:'[A-Za-z]+)?\b", text):
        token = raw.split("'", 1)[0]
        if len(token) < 2:
            continue
        if len(token) > 2 and token[-1] == "s" and token[:-1].isupper():
            terms.add(token[:-1].lower())
            continue
        if token.isupper() and any(char.isalpha() for char in token):
            terms.add(token.lower())
            continue
        if _looks_camel_case_term(token):
            terms.add(token.lower())
    return terms


def _normal_terms(text: str) -> set[str]:
    return set(_tokens(text)).union(_protected_terms(text))


def _looks_camel_case_term(token: str) -> bool:
    return (
        len(token) >= 4
        and token[0].isupper()
        and any(char.isupper() for char in token[1:])
        and any(char.islower() for char in token)
    )


def _has_suspicious_new_english(
    draft_text: str,
    final_text: str,
    *,
    allow_roman_hindi: bool = False,
    strict_new_english: bool = False,
) -> tuple[bool, list[str], str]:
    draft_tokens = set(_english_content_tokens(draft_text, min_length=3 if strict_new_english else 4))
    draft_compact = "".join(_tokens(draft_text))
    known_glossary_tokens = _known_glossary_candidate_tokens()
    unknown: list[str] = []
    run = 0
    for token in _english_content_tokens(final_text, min_length=3 if strict_new_english else 4):
        if _is_known_candidate_token(
            token,
            draft_tokens=draft_tokens,
            draft_compact=draft_compact,
            known_glossary_tokens=known_glossary_tokens,
            allow_roman_hindi=allow_roman_hindi,
            strict_new_english=strict_new_english,
        ):
            run = 0
            continue
        unknown.append(token)
        run += 1
        if not strict_new_english and run >= 2:
            return True, unknown, "new-english-run"
    if strict_new_english and unknown:
        return True, unknown, "new-english-token"
    if len(set(unknown)) >= 2:
        return True, unknown, "new-english-count"
    return False, unknown, ""


def _english_content_tokens(text: str, *, min_length: int = 4) -> list[str]:
    return [token for token in _tokens(text) if len(token) >= min_length and token.isascii()]


def _is_known_candidate_token(
    token: str,
    *,
    draft_tokens: set[str],
    draft_compact: str,
    known_glossary_tokens: set[str],
    allow_roman_hindi: bool,
    strict_new_english: bool,
) -> bool:
    if token in draft_tokens:
        return True
    if len(token) >= 6 and token in draft_compact:
        return True
    if _is_plural_or_singular_match(token, draft_tokens):
        return True
    if allow_roman_hindi and token in ROMAN_HINDI_TOKENS:
        return True
    if token in known_glossary_tokens:
        return True
    if strict_new_english:
        return token in STRICT_ALLOWED_NEW_ENGLISH_TOKENS
    return token in COMMON_ENGLISH_TOKENS


def _known_glossary_candidate_tokens() -> set[str]:
    try:
        from ramblefix.glossary import known_glossary_terms
    except Exception:  # noqa: BLE001
        return set()

    tokens: set[str] = set()
    for alias, canonical in known_glossary_terms().items():
        for value in (alias, canonical):
            parts = _tokens(value)
            if len(parts) != 1:
                continue
            token = parts[0]
            if _safe_known_glossary_token(token):
                tokens.add(token)
    return tokens


def _safe_known_glossary_token(token: str) -> bool:
    return (
        len(token) >= 3
        and token.isascii()
        and token not in COMMON_ENGLISH_TOKENS
        and token not in MEANING_FIRST_STOP_TOKENS
        and token not in ROMAN_HINDI_TOKENS
    )


def _is_plural_or_singular_match(token: str, draft_tokens: set[str]) -> bool:
    if token.endswith("s") and token[:-1] in draft_tokens:
        return True
    return f"{token}s" in draft_tokens


def _token_supported_by_witness(token: str, witness_tokens: set[str]) -> bool:
    if token in witness_tokens or _is_plural_or_singular_match(token, witness_tokens):
        return True
    if len(token) < 5:
        return False
    return any(len(witness) >= 5 and (token in witness or witness in token) for witness in witness_tokens)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text.lower())


def _extract_text(result: object) -> str:
    text = getattr(result, "text", None)
    if text is not None:
        return str(text).strip()
    return str(result or "").strip()


def _session_for_model(model: str) -> object:
    key = (model, threading.get_ident())
    session = _QWEN3_CHUNK_SESSIONS.get(key)
    if session is not None:
        return session
    try:
        from mlx_qwen3_asr import Session
    except ImportError as exc:
        raise RuntimeError("mlx-qwen3-asr is not installed") from exc
    session = Session(model=model)
    _QWEN3_CHUNK_SESSIONS[key] = session
    return session
