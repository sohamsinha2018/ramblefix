from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from ramblefix.asr import ACCURATE_MLX_MODEL, transcribe_audio
from ramblefix.gemini_asr import GeminiTranscript, transcribe_gemini_audio
from ramblefix.glossary import apply_glossary
from ramblefix.quality import repeated_substring_score


DEFAULT_LUDO_ASR_URL = "http://127.0.0.1:8001/asr"
COMMON_ROMAN = set(
    "aaj ab abhi acha achha acchi accha aage aaake aake aur baat bada badh badhe badh sake bahar bata batao bahiya bhai bolo bolu bolna kya kyu kyun kaise kaisse kaisa kaisi main mein mai me tu tum tera teri tumhara meri mera mere haan haa han nahi nahin na ho hai hain hum tha thi the theek thik ok okay kar kare karo karke karna chal chalo chalte cheez cheezhe cheeze yaar soham ludo board dice roll goti gotiya paasa pasa chhe six kaat kaatna capture home move bahar andar gaya gayi liya liye nikal nikala do de dena sun suna sunao ga gana gaana gaane gaao shayari cheating setting please ipl match market ai startup weather delhi bangkok food khana pyaar voice cut election elections poll vote voting result results west bengal pashchim bangal interesting mujhe banana samajhna samajna samajh local tool prompt english hindi hinglish transcript taki thaak"
    .split()
)
COMMON_ENGLISH = set(
    "a an agenda and app apps are as ask be build building bunch but can check correctly convert cursor do does for from get go goal good have how i if in into is it its main me mode monetize not of on only or other output personally pre preserve problem prompt quickly real really results right should skill skills solve some something still take tell that the them there this time to tool transcript use very want waste we what when whether which with without you your"
    .split()
)
CLEAR_INTENT_RE = re.compile(
    r"\b(ipl|match|market|startup|ai|tool|prompt|transcript|english|hindi|hinglish|gemini|whisper|asr|chatgpt|cursor|codex|teams|song|gana|sunao|shayari|khana|food|weather|election|goti|kaat|kaatna|dice|roll|six|mujhe|banana|samajhna|samajna|problem|solve|cursor|skill|skills|execution|agenda|monetize|kya|hai)\b|"
    r"(गाना|सुनाओ|शायरी|खाना|मौसम|चुनाव|गोटी|काट|क्या|है|मुझे|समझ|बताओ|समस्या|आगे)",
    re.I,
)
GARBLE_RE = re.compile(r"\b(aamunin|yurimot|kujwaas|twijveen|dupin|jibti|klay|pagnaug|jaaini|hekkis|burata|bakw\w*)\b", re.I)


@dataclass(frozen=True)
class Candidate:
    source: str
    text: str
    score: float
    ms: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class HybridTranscript:
    text: str
    language: str | None
    engine: str
    candidates: list[Candidate]


def transcribe_ludo_local(audio_path: str | Path, *, url: str = DEFAULT_LUDO_ASR_URL, timeout_seconds: float = 5.0) -> Candidate:
    path = Path(audio_path).expanduser().resolve()
    try:
        response = requests.post(
            url,
            # Ludo's sidecar converts input -> input.wav. If Content-Type is
            # audio/wav it writes input.wav and ffmpeg fails in-place.
            # Let ffmpeg detect the real container from bytes instead.
            headers={"Content-Type": "application/octet-stream"},
            data=path.read_bytes(),
            timeout=timeout_seconds,
        )
        payload = response.json()
        if not response.ok or not payload.get("ok"):
            return Candidate(source="local_whispercpp", text="", score=-10, ms=payload.get("elapsed_ms"), error=payload.get("error") or f"HTTP {response.status_code}")
        text = repair_transcript(str(payload.get("text", "")).strip())
        return Candidate(source="local_whispercpp", text=text, score=score_candidate(text), ms=payload.get("elapsed_ms"))
    except Exception as exc:
        return Candidate(source="local_whispercpp", text="", score=-10, error=repr(exc))


def transcribe_hybrid_ludo(
    audio_path: str | Path,
    *,
    gemini_key: str | None = None,
    ludo_asr_url: str = DEFAULT_LUDO_ASR_URL,
) -> HybridTranscript:
    candidates: list[Candidate] = []

    local = transcribe_ludo_local(audio_path, url=ludo_asr_url)
    candidates.append(local)

    if local.score < 0.55 or looks_bad(local.text) or should_probe_mlx_auto(local.text):
        started = time.perf_counter()
        try:
            mlx = transcribe_audio(audio_path, model=ACCURATE_MLX_MODEL, language=None)
            text = repair_transcript(mlx.text)
            candidates.append(
                Candidate(
                    source="mlx_accurate_auto",
                    text=text,
                    score=score_candidate(text),
                    ms=int((time.perf_counter() - started) * 1000),
                )
            )
        except Exception as exc:
            candidates.append(
                Candidate(
                    source="mlx_accurate_auto",
                    text="",
                    score=-10,
                    ms=int((time.perf_counter() - started) * 1000),
                    error=repr(exc),
                )
            )

    should_run_gemini = local.error is not None or local.score < 0.25 or looks_bad(local.text)
    if gemini_key or should_run_gemini:
        try:
            gemini: GeminiTranscript = transcribe_gemini_audio(audio_path, api_key=gemini_key)
            text = repair_transcript(gemini.text)
            candidates.append(Candidate(source="gemini_audio", text=text, score=score_candidate(text)))
        except Exception as exc:
            candidates.append(Candidate(source="gemini_audio", text="", score=-10, error=repr(exc)))

    best = max(candidates, key=lambda c: c.score)
    if best.score < 0.2 or looks_bad(best.text):
        return HybridTranscript(
            text="unclear",
            language=None,
            engine="hybrid_ludo:unclear",
            candidates=candidates,
        )
    return HybridTranscript(
        text=best.text or "unclear",
        language=None,
        engine=f"hybrid_ludo:{best.source}",
        candidates=candidates,
    )


def should_probe_mlx_auto(text: str) -> bool:
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    if not words:
        return True
    devanagari_words = re.findall(r"[\u0900-\u097F]+", text)
    roman_words = re.findall(r"[A-Za-z]+", text)
    # Pure Hindi is where MLX auto often beats the Ludo sidecar. Hinglish
    # should stay on the faster local candidate unless it looks bad.
    return len(devanagari_words) >= 4 and len(roman_words) <= 1


def score_candidate(text: str) -> float:
    t = text.strip()
    if not t:
        return -10
    score = 0.0
    words = re.findall(r"\w+", t, flags=re.UNICODE)
    roman = [w.lower() for w in re.findall(r"[a-z]+", t)]
    if len(words) >= 3:
        score += 0.25
    if len(words) >= 7:
        score += 0.2
    has_devanagari = bool(re.search(r"[\u0900-\u097F]", t))
    has_clear_intent = bool(CLEAR_INTENT_RE.search(t))
    if has_clear_intent:
        score += 0.45
    if has_devanagari:
        score += 0.25
    if re.search(r"\b(aaj|kya|hai|haan|nahi|yaar|bhai|tu|tum|mere|meri|goti|dice|match|shayari|khana|kaisa|kyun|abhi|mat|kaatna|banana|samajhna|mujhe)\b", t, re.I):
        score += 0.25
    if re.search(r"\b(Cursor|Codex|ChatGPT|Gemini|Teams|Whisper|ASR|LLM)\b", t, re.I):
        score += 0.15
    if re.search(r"\b(problem solve|solve kar|solve karke|aage badh|main agenda|pre[- ]?(made|baked) skills?|cursor|chat ?gpt|monetize|good use of my time)\b", t, re.I):
        score += 0.45
    if re.search(r"(problem solve|आगे बढ़|कैसे करें|बताओ)", t, re.I):
        score += 0.45
    if re.search(r"\b(haa|haan|bahiya|bhaiya)\b", t, re.I):
        score += 0.15
    english_hits = [w.lower() for w in roman if w.lower() in COMMON_ENGLISH]
    if len(english_hits) >= 4:
        score += 0.45
    elif len(english_hits) >= 2:
        score += 0.2
    score -= min(repeated_substring_score(t), 1.0) * 0.8
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", t):
        score -= 0.5
    if re.search(r"\b(thank you|thanks)\b\.?$", t, re.I) and len(words) <= 4:
        score -= 0.8
    if GARBLE_RE.search(t):
        score -= 0.75
    if repeated_phrase_score(words) > 0:
        score -= 0.9
    unknown = [w for w in roman if len(w) > 2 and w not in COMMON_ROMAN and w not in COMMON_ENGLISH]
    unknown_ratio = len(unknown) / len(roman) if roman else 0.0
    tiny_ratio = len([w for w in words if len(w) <= 2]) / len(words) if words else 0.0
    if unknown_ratio > 0.38 and len(english_hits) < 4:
        score -= 0.8
    elif unknown_ratio > 0.28 and len(english_hits) < 3 and not has_clear_intent and not has_devanagari:
        score -= 0.55
    if tiny_ratio > 0.45:
        score -= 0.35
    if len(words) >= 5 and not has_clear_intent and not has_devanagari:
        score -= 0.35
    return round(score, 3)


def repair_transcript(text: str) -> str:
    repaired = apply_glossary(text)
    repaired = re.sub(r"\bkiyaar\b", "ki yaar", repaired, flags=re.I)
    repaired = re.sub(r"\bthaak ki\b", "taki", repaired, flags=re.I)
    repaired = re.sub(r"\btha ki\b", "taki", repaired, flags=re.I)
    repaired = re.sub(r"\bcheezhe\b", "cheezein", repaired, flags=re.I)
    repaired = re.sub(r"\bsakhe\b", "sake", repaired, flags=re.I)
    repaired = re.sub(r"\bshaasakti\b", "jaa sakte ho", repaired, flags=re.I)
    repaired = re.sub(r"\bbhar sakhe\b", "badh sake", repaired, flags=re.I)
    repaired = re.sub(r"\baage bhar\b", "aage badh", repaired, flags=re.I)
    repaired = re.sub(r"\baake badh sake\b", "aage badh sake", repaired, flags=re.I)
    repaired = re.sub(r"\bbuild a this tool\b", "build this tool", repaired, flags=re.I)
    repaired = re.sub(
        r"\bOkay, one of the questions that I need to ask myself\b",
        "The only question that I need to ask myself",
        repaired,
        flags=re.I,
    )
    repaired = re.sub(r"\s+", " ", repaired)
    return repaired.strip()


def looks_bad(text: str) -> bool:
    if not text.strip():
        return True
    if repeated_substring_score(text) > 0.2:
        return True
    words = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
    if repeated_phrase_score(words) > 0:
        return True
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text):
        return True
    if score_candidate(text) < 0.2:
        return True
    return len(words) <= 2


def repeated_phrase_score(words: list[str], *, size: int = 6) -> float:
    if len(words) < size * 2:
        return 0.0
    shingles = [" ".join(words[i : i + size]) for i in range(len(words) - size + 1)]
    return 1.0 if len(shingles) != len(set(shingles)) else 0.0
