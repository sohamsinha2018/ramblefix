from __future__ import annotations

from ramblefix.hindi_chunk_polish import (
    hindi_value_delta,
    meaning_first_update_reject_reasons,
    romanize_devanagari_for_hinglish,
    update_reject_reasons,
    witness_can_accept_rejected_candidate,
)
from ramblefix.hindi_stream_session import _sanitize_rejected_new_english_candidate


def main() -> None:
    draft = "Yes, look, nothing will happen if our tool cannot beat others on one core problem, then there is no wedge."
    clean_hinglish = (
        "Haan bhai dekh, yah sab karne se kuchh nahin hoga, agar hamaara tool "
        "cannot beat others on one core problem, then there is no wedge."
    )
    assert _reasons(draft, clean_hinglish) == []

    bench_hallucination = clean_hinglish.replace("wedge", "bench")
    assert _has_reason(_reasons(draft, bench_hallucination), "new-english-token:bench")

    veg_hallucination = clean_hinglish.replace("wedge", "veg")
    assert _has_reason(_reasons(draft, veg_hallucination), "new-english-token:veg")

    subagent_draft = "Each agent will do something, the other will do something else, and so on."
    gujraat_hallucination = (
        "Har agent ka kaam alag alag hoga. So one agent will do something, "
        "doosra agent gujraat karega, tisra kuchh aur karega."
    )
    assert _has_reason(_reasons(subagent_draft, gujraat_hallucination), "new-english-token:gujraat")

    code_draft = "I cannot solve this through this code."
    code_candidate = "Hamen aisa karna hoga, which I cannot solve through this court."
    reasons = _reasons(code_draft, code_candidate)
    assert _has_reason(reasons, "new-english-token:")
    assert any("court" in reason for reason in reasons)

    skeptical_draft = "What I need now is a quick answer and be tight and brief and skeptical."
    skeptical_candidate = (
        "Haan bhai dekh what I need now is a quick answer and be tight and brief "
        "friends kept it like, thik hai."
    )
    reasons = _reasons(skeptical_draft, skeptical_candidate)
    assert any("friends" in reason for reason in reasons)
    assert any("kept" in reason for reason in reasons)

    subagent_draft = "You have to structure it into four sub agents."
    subagent_candidate = "You have to structure it into four subagents."
    assert _reasons(subagent_draft, subagent_candidate) == []

    term_draft = "Are you able to get terms better MCP, QSR, API, and FMS?"
    term_clean = "Haan bhai, are you able to get terms better mcp, qsr, api, and fms?"
    assert _reasons(term_draft, term_clean) == []
    assert _default_reasons(term_draft, term_clean) == []

    term_drift = "Haan bhai, are you able to get terms better MCB, QSR, API, and FMS?"
    reasons = _default_reasons(term_draft, term_drift)
    assert any(reason.startswith("protected-term-missing:") and "mcp" in reason for reason in reasons)
    assert any(reason.startswith("protected-term-new:") and "mcb" in reason for reason in reasons)

    missing_api = "Haan bhai, are you able to get terms better MCP and QSR?"
    reasons = _default_reasons(term_draft, missing_api)
    assert any(reason.startswith("protected-term-missing:") and "api" in reason for reason in reasons)

    possessive_draft = "The way MCPs work is that there is an API layer."
    possessive_candidate = "The way mcp's work is that there is an api layer."
    assert _default_reasons(possessive_draft, possessive_candidate) == []

    plural_candidate = "See the way MCPs work is that there's an API layer."
    assert _default_reasons(possessive_draft, plural_candidate) == []

    recovered_acronym_draft = "It should be direct and brief."
    recovered_acronym_candidate = "It should be direct and brief, API ho ya MCP ho."
    assert _default_reasons(recovered_acronym_draft, recovered_acronym_candidate) == []

    known_glossary_draft = "This needs routing update."
    known_glossary_candidate = "This needs routing update Riskified."
    assert _reasons(known_glossary_draft, known_glossary_candidate) == []

    unknown_glossary_candidate = "This needs routing update madeupterm."
    assert _has_reason(_reasons(known_glossary_draft, unknown_glossary_candidate), "new-english-token:madeupterm")

    multiword_piece_candidate = "This needs routing update report."
    assert _has_reason(_reasons(known_glossary_draft, multiword_piece_candidate), "new-english-token:report")

    filler_draft = "This needs to work and preserve meaning."
    filler_candidate = "Haan bhai, okay, this needs to work, like, and preserve meaning, right?"
    assert _reasons(filler_draft, filler_candidate) == []
    assert hindi_value_delta(filler_draft, filler_candidate)["has_hindi_value"] is False

    style_only_candidate = "haan woh dekh what I need now is a quick answer and be tight and brief and skeptical."
    assert hindi_value_delta(skeptical_draft, style_only_candidate)["has_hindi_value"] is False

    substantive_hindi_candidate = (
        "haan bhai dekh ye sab karne se kuch nahi hoga agar hamara tool "
        "cannot beat others on one core problem, then there is no wedge."
    )
    assert hindi_value_delta(draft, substantive_hindi_candidate)["has_hindi_value"] is True
    assert meaning_first_update_reject_reasons(draft, substantive_hindi_candidate) == []

    common_roman_hindi_draft = "Tell me what is going on today?"
    common_roman_hindi_candidate = (
        "Aur kya chal raha hai tera din? Kuch bata aajkal kya chal raha hai tera?"
    )
    assert _reasons(common_roman_hindi_draft, common_roman_hindi_candidate) == []
    assert meaning_first_update_reject_reasons(
        common_roman_hindi_draft,
        common_roman_hindi_candidate,
    ) == []

    gaali_draft = "I don't understand black, I was just talking about my sister's fight."
    gaali_candidate = "Achchha yah kaali samajh bhi nahin aati kya pichhali baar main bahan ki laudi bol raha tha."
    assert _reasons(gaali_draft, gaali_candidate) == []

    paap_draft = (
        "I am not saying anything wrong. I am saying something different in your writing. "
        "Cutting is not allowed. Why is it allowed? Who said that? Anyway, what I was "
        "saying is... Do you know about the election?"
    )
    paap_candidate = (
        "Are kuch paap aap nahin bol raha main kabhi kabhi kuch bol raha hoon tere "
        "likhane mein kuch alag dikha rahe hain kaatna paapi milega kyon hota hai? "
        "Yah kisne bola? Anyway what I was saying is yaar abhi election election ka pata tha."
    )
    assert _reasons(paap_draft, paap_candidate) == []

    hallucinated_work_candidate = "Yaar AI PhD samajha nahin kya kar rahe hain do aajkal kya naukri hai sir exactly job kya"
    hallucinated_work_reasons = _reasons("AI is a business, what are you doing these days?", hallucinated_work_candidate)
    assert _has_reason(hallucinated_work_reasons, "new-english-token:phd")
    hallucinated_work_sanitized = _sanitize_rejected_new_english_candidate(
        draft_text="AI is a business, what are you doing these days?",
        candidate_text=hallucinated_work_candidate,
        reject_reasons=hallucinated_work_reasons,
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
    )
    assert hallucinated_work_sanitized["accepted"] is True
    assert "phd" not in hallucinated_work_sanitized["text"].lower()
    assert " sir " not in f" {hallucinated_work_sanitized['text'].lower()} "
    assert "exactly job" in hallucinated_work_sanitized["text"].lower()

    supported_hinglish_draft = "What should I do now?"
    supported_hinglish_candidate = (
        "Haan yaar kya karen ab bata? What should I do? You tell me."
    )
    assert _reasons(supported_hinglish_draft, supported_hinglish_candidate) == []

    compressed_draft = "So in order for this to work, it should not be that. It should be direct and brief."
    richer_candidate = (
        "haan vahi so in order for this to work aisa ho hi nahi sakta ki ismein "
        "vo matlab vo baatein nahi ho payengi matlab kaise samajho mein aapko "
        "it should not be like that right it should be direct and brief."
    )
    assert meaning_first_update_reject_reasons(compressed_draft, richer_candidate) == []

    mostly_verbatim_candidate = (
        "See the way MCPs work is that there's an API layer par uske saath na "
        "guidelines vagairah bhi hota hai as well. So, the guidelines and "
        "documentation are all there. So, the API layer functions well."
    )
    mostly_verbatim_draft = (
        "The way MCPs work is that there is an API layer and there are guidelines "
        "as well. So, the guidelines and documentation are all there. So, the API layer functions well."
    )
    assert meaning_first_update_reject_reasons(mostly_verbatim_draft, mostly_verbatim_candidate) == [
        "no-default-meaning-gain"
    ]

    legal_tradeoff_draft = (
        "What is it that you have to do? What I am trying to say here is that in our legal profession "
        "this is not how it works. So you have to factor in this and improve it or else it will not be possible. "
        "This will not work. So think through and critique and think and answer in the direction of the sun."
    )
    legal_tradeoff_candidate = (
        "haan par eki karne se kya hota hai matlab What I am trying to say here is that in our legal profession "
        "this is not how it works. right so aapko yeh factor karke improve karna hoga nahi to yeh honi payega "
        "matlab this will not work right so think through critique aur aap matlab and think and answer in the direction of the sun."
    )
    assert meaning_first_update_reject_reasons(legal_tradeoff_draft, legal_tradeoff_candidate) == []

    legal_devanagari_candidate = (
        "हाँ पर एकी करने से क्या होता है मतलब What I am trying to say here is that in our legal profession "
        "this is not how it works. right so आपको यह factor करके improve करना होगा नहीं तो यह होनी पायेगा "
        "मतलब this will not work right so think through critique और आप मतलब विचार विमर्श करके संक्षिप्त में उत्तर दें"
    )
    legal_romanized = romanize_devanagari_for_hinglish(legal_devanagari_candidate)
    assert "uttara den" in legal_romanized
    assert _reasons(legal_tradeoff_draft, legal_romanized) == []
    assert meaning_first_update_reject_reasons(legal_tradeoff_draft, legal_romanized) == []

    incomplete_hinglish_candidate = (
        "See, the way MCP's work is that there is a API layer par uske saath na "
        "guidelines vagairah bhi hota hai. So guideline documentation hi sab hota hai "
        "taaki the API layer functions mein"
    )
    assert meaning_first_update_reject_reasons(mostly_verbatim_draft, incomplete_hinglish_candidate) == [
        "incomplete-tail"
    ]

    hindi_draft = "I have added some clips now. Most of them have tons of a bunch of Hindi in it."
    hindi_candidate = "I've added some clips. Maybe coffee is added. Theek hai."
    reasons = _reasons(hindi_draft, hindi_candidate)
    assert any("coffee" in reason for reason in reasons)

    tetris_fast = "Think of a very simple arcade game, maybe, at a race or something fun."
    tetris_candidate = "Think of a very simple arcade game, maybe, a Tetris or mind sweeper or something fun."
    tetris_witness = "Think of a very simple arcade game, maybe a Tetris or Minesweeper or something fun."
    tetris_rejects = _reasons(tetris_fast, tetris_candidate)
    assert any(reason.startswith("new-english-token:") for reason in tetris_rejects)
    tetris_decision = witness_can_accept_rejected_candidate(
        draft_text=tetris_fast,
        candidate_text=tetris_candidate,
        witness_text=tetris_witness,
        reject_reasons=tetris_rejects,
    )
    assert tetris_decision["accepted"] is True
    assert "tetris" in tetris_decision["supported_new_terms"]
    assert "sweeper" in tetris_decision["supported_new_terms"]

    drift_fast = "Can't you then open Google search on my browser?"
    drift_candidate = "Continue then open Google search in my browser as a free user."
    drift_witness = "Can't you then open Google search in my browser?"
    drift_decision = witness_can_accept_rejected_candidate(
        draft_text=drift_fast,
        candidate_text=drift_candidate,
        witness_text=drift_witness,
        reject_reasons=_reasons(drift_fast, drift_candidate),
    )
    assert drift_decision["accepted"] is False

    common_fast = "Use a skill for my problem."
    common_candidate = "I know it is a good skill that I can use for my problem."
    common_witness = "I know it is a good skill that I can use for my problem."
    common_decision = witness_can_accept_rejected_candidate(
        draft_text=common_fast,
        candidate_text=common_candidate,
        witness_text=common_witness,
        reject_reasons=_reasons(common_fast, common_candidate),
    )
    assert common_decision["accepted"] is False
    assert "know" not in common_decision["supported_new_terms"]

    unrelated_support_fast = "The last message I sent to Rambo Fix should tell me to copy."
    unrelated_support_candidate = "The last message I sent to Rambofix should tell me to copy a and b all se."
    unrelated_support_witness = "The last message I sent to Rambo Fix should tell me to copy."
    unrelated_decision = witness_can_accept_rejected_candidate(
        draft_text=unrelated_support_fast,
        candidate_text=unrelated_support_candidate,
        witness_text=unrelated_support_witness,
        reject_reasons=["new-english-token:all"],
    )
    assert unrelated_decision["accepted"] is False
    assert unrelated_decision["unsupported_rejected_new_terms"] == ["all"]

    mcp_chunk4_candidate = (
        "See the way MCPPs work is that There's a API layer पर उसके साथ ना "
        "guidelines वगैरह भी होता है सुखाई लाइन डॉक्युमेंटेशन ही सब होता है "
        "that API layer functions were"
    )
    mcp_sanitized = _sanitize_for_test(mostly_verbatim_draft, mcp_chunk4_candidate)
    assert mcp_sanitized["accepted"] is True
    assert "MCPs" in mcp_sanitized["text"]
    assert "MCPP" not in mcp_sanitized["text"]
    assert any(rule.startswith("draft-near-match:MCPPs->MCPs") for rule in mcp_sanitized["repair_rules"])
    mcp_session_sanitized = _sanitize_rejected_new_english_candidate(
        draft_text=mostly_verbatim_draft,
        candidate_text=romanize_devanagari_for_hinglish(mcp_chunk4_candidate),
        reject_reasons=[
            "protected-term-missing:mcp",
            "protected-term-new:mcpp",
            "new-english-token:mcpps,were",
        ],
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
    )
    assert mcp_session_sanitized["accepted"] is True
    assert mcp_session_sanitized["removed_tokens"] == ["were", "sukhai", "laina", "dokyumemtesana"]

    legal_chunk4_candidate = (
        "हाँ पर एकी करने से क्या होता है मतलब What I am trying to say here is that "
        "In our legal profession this is not how it works. right so आपको ये factor करके "
        "improve करना होगा नहीं तो यह होनी पायेगा मतलब this will not work right so "
        "think through it critic और आप मतलब विचार विमर्श करके संक्षिप्त में उत्तर दें"
    )
    legal_sanitized = _sanitize_for_test(legal_tradeoff_draft, legal_chunk4_candidate)
    assert legal_sanitized["accepted"] is True
    assert "critique" in legal_sanitized["text"]
    assert any(rule.startswith("draft-near-match:critic->critique") for rule in legal_sanitized["repair_rules"])

    subagent_bad_candidate = (
        "haan par In order for this to be solved. aapko aisa karna hoga ki you have to "
        "structure it into four subagents har agent ka kaam alag alag hoga hai so one "
        "agent will do something dusara action kuch aur karega tisara kuch aur and so on and so forth"
    )
    subagent_sanitized = _sanitize_rejected_new_english_candidate(
        draft_text=(
            "Hi, in order for this to be solved, you have to structure it into four sub-agents. "
            "Each agent will have a different task. One agent will do something, the other will do something else, etc."
        ),
        candidate_text=subagent_bad_candidate,
        reject_reasons=["new-english-token:action"],
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
    )
    assert subagent_sanitized["accepted"] is True
    assert "action" not in subagent_sanitized["text"].lower()
    assert "and so forth" in subagent_sanitized["text"].lower()
    assert not any("forth->for" in rule for rule in subagent_sanitized.get("repair_rules", []))
    subagent_gujvaar_sanitized = _sanitize_rejected_new_english_candidate(
        draft_text=(
            "Hi, in order for this to be solved, you have to structure it into four sub-agents. "
            "Each agent will have a different task. One agent will do something, the other will do something else, etc."
        ),
        candidate_text=(
            "Haan yaar, par in order for this to be solved, aapko aisa karna hoga ki you have to "
            "structure it to 4 sub agents. Har agent ka kaam alag alag hoga, thik hai? So, "
            "1 agent will do something, doosra agent gujvaar karega, tisra kuchh aur in so on and so forth"
        ),
        reject_reasons=["new-english-token:gujvaar"],
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
    )
    assert subagent_gujvaar_sanitized["accepted"] is True
    assert "gujvaar" not in subagent_gujvaar_sanitized["text"].lower()
    assert "and so forth" in subagent_gujvaar_sanitized["text"].lower()
    subagent_dangling_sanitized = _sanitize_rejected_new_english_candidate(
        draft_text=(
            "Hi, in order for this to be solved, you have to structure it into four sub-agents. "
            "Each agent will have a different task. One agent will do something, the other will do something else, etc."
        ),
        candidate_text=(
            "haan par In order for this to be solved. aapko aisa karna hoga ki you have to "
            "structure it into four subagents har agent ka kaam alag alag hoga hai so one "
            "agent will do something dusara action kuch aur karega tisara kuch aur and so on "
            "and so forth the other will do something else, etc."
        ),
        reject_reasons=["new-english-token:action,forth"],
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
    )
    assert subagent_dangling_sanitized["accepted"] is False
    assert "dangling-transition" in subagent_dangling_sanitized["reject_reasons"]

    cursor_drift_draft = (
        "The last message I sent to Rambo Fix, maybe the Cursor was not focused somewhere, "
        "it didn't tell me to copy."
    )
    cursor_drift_candidate = (
        "The last message I sent to Rambofix maybe the car service not so good somewhere "
        "It didn't tell me to copy."
    )
    cursor_sanitized = _sanitize_for_test(cursor_drift_draft, cursor_drift_candidate)
    assert cursor_sanitized["accepted"] is False

    hindi_drift_candidate = (
        "I have some clips right now Most of them have tons of like bunch of Hinden it. "
        "उसमें भी काफी सारे इंदी होंगी ठीक है तो यह try and see"
    )
    hindi_sanitized = _sanitize_for_test(hindi_draft, hindi_drift_candidate)
    assert hindi_sanitized["accepted"] is False
    hindi_session_sanitized = _sanitize_rejected_new_english_candidate(
        draft_text=hindi_draft,
        candidate_text=romanize_devanagari_for_hinglish(hindi_drift_candidate),
        reject_reasons=["new-english-token:hinden"],
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
    )
    assert hindi_session_sanitized["accepted"] is False
    assert hindi_session_sanitized["reject_reasons"]

    wedge_sanitized = _sanitize_rejected_new_english_candidate(
        draft_text=draft,
        candidate_text=bench_hallucination,
        reject_reasons=["new-english-token:bench"],
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
    )
    assert wedge_sanitized["accepted"] is True
    assert "wedge" in wedge_sanitized["text"]

    short_fragment_sanitized = _sanitize_rejected_new_english_candidate(
        draft_text="Hello, how's it going friend?",
        candidate_text="Hello, how is it going, maalapha ka?",
        reject_reasons=["new-english-token:maalapha"],
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
    )
    assert short_fragment_sanitized["accepted"] is False
    assert "over-sanitized-low-content" in short_fragment_sanitized["reject_reasons"]

    sparse_fragment_sanitized = _sanitize_rejected_new_english_candidate(
        draft_text="By the way, you got the term wrong, I said go, you got it correct.",
        candidate_text="By the way new word term wrong aise goli ko record.",
        reject_reasons=["new-english-token:new,word,goli,record"],
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
    )
    assert sparse_fragment_sanitized["accepted"] is False
    assert "unsupervised-multi-token-removal" in sparse_fragment_sanitized["reject_reasons"]

    unsupervised_public_fragment = _sanitize_rejected_new_english_candidate(
        draft_text="In the middle we see empty space which is the workspace where we can store our work.",
        candidate_text="Madhy mein ham khaali jagah dekhate hain jo ki work space hai jahaan ham.",
        reject_reasons=["new-english-token:madhy,ham,khaali,jagah,dekhate"],
        release_tail_seconds=1.8,
        max_release_tail_seconds=4.5,
    )
    assert unsupervised_public_fragment["accepted"] is False
    assert "unsupervised-multi-token-removal" in unsupervised_public_fragment["reject_reasons"]


def _reasons(draft: str, final: str) -> list[str]:
    return update_reject_reasons(
        draft_text=draft,
        final_text=final,
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
        allow_roman_hindi=True,
        strict_new_english=True,
    )


def _default_reasons(draft: str, final: str) -> list[str]:
    return update_reject_reasons(
        draft_text=draft,
        final_text=final,
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
        allow_roman_hindi=True,
    )


def _has_reason(reasons: list[str], prefix: str) -> bool:
    return any(reason.startswith(prefix) for reason in reasons)


def _sanitize_for_test(draft: str, candidate: str) -> dict:
    romanized = romanize_devanagari_for_hinglish(candidate)
    reasons = update_reject_reasons(
        draft_text=draft,
        final_text=romanized,
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
        allow_roman_hindi=True,
        strict_new_english=True,
    )
    return _sanitize_rejected_new_english_candidate(
        draft_text=draft,
        candidate_text=romanized,
        reject_reasons=reasons,
        release_tail_seconds=1.0,
        max_release_tail_seconds=3.0,
    )


if __name__ == "__main__":
    main()
