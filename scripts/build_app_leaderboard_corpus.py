from __future__ import annotations

import argparse
import json
from pathlib import Path


BUCKETS: dict[str, list[dict[str, object]]] = {
    "negation": [
        {
            "script": "Isko rollback mat karna. Sirf Riskified timeout ko thirty seconds se ten seconds karo.",
            "gold": "Do not roll this back. Only change the Riskified timeout from 30 seconds to 10 seconds.",
            "terms": ["Riskified"],
            "facts": ["do not roll this back", "change Riskified timeout", "from 30 seconds to 10 seconds"],
        },
        {
            "script": "Do not send company data to Claude. Bas local transcript banao aur summary offline rakho.",
            "gold": "Do not send company data to Claude. Create the local transcript and keep the summary offline.",
            "terms": ["Claude"],
            "facts": ["do not send company data to Claude", "create local transcript", "keep summary offline"],
        },
        {
            "script": "Fee Admin flag delete mat karna, bas beta users ke liye hide karna.",
            "gold": "Do not delete the Fee Admin flag. Only hide it for beta users.",
            "terms": ["Fee Admin"],
            "facts": ["do not delete Fee Admin flag", "hide only for beta users"],
        },
        {
            "script": "Amit ko mat bolna ki launch done hai. Usko bolo SSO mapping abhi blocked hai.",
            "gold": "Do not tell Amit the launch is done. Tell him SSO mapping is still blocked.",
            "terms": ["Amit", "SSO"],
            "facts": ["do not say launch is done", "tell Amit SSO mapping is blocked"],
        },
        {
            "script": "Cursor prompt mein billing logic change karne ko mat bolna, sirf latency investigate karna.",
            "gold": "In the Cursor prompt, do not ask to change billing logic. Only investigate latency.",
            "terms": ["Cursor"],
            "facts": ["do not change billing logic", "only investigate latency"],
        },
    ],
    "ordering": [
        {
            "script": "PCI validation ke baad SOX review start karna, pehle nahi.",
            "gold": "Start the SOX review after PCI validation, not before.",
            "terms": ["PCI", "SOX"],
            "facts": ["SOX review after PCI validation", "not before PCI validation"],
        },
        {
            "script": "Partner Center deploy se pehle Riskified check run karna.",
            "gold": "Run the Riskified check before the Partner Center deploy.",
            "terms": ["Partner Center", "Riskified"],
            "facts": ["Riskified check before Partner Center deploy"],
        },
        {
            "script": "PR merge tab karna jab smoke test pass ho jaye.",
            "gold": "Merge the PR only after the smoke test passes.",
            "terms": ["PR"],
            "facts": ["merge PR only after smoke test passes"],
        },
        {
            "script": "Okta mapping complete hone tak SSO announcement hold karo.",
            "gold": "Hold the SSO announcement until the Okta mapping is complete.",
            "terms": ["Okta", "SSO"],
            "facts": ["hold SSO announcement", "until Okta mapping complete"],
        },
        {
            "script": "Pehle logs check karo, phir retry logic add karo.",
            "gold": "Check the logs first, then add retry logic.",
            "terms": [],
            "facts": ["check logs first", "add retry logic after logs"],
        },
    ],
    "numbers_money_dates": [
        {
            "script": "Friday tak p95 two hundred milliseconds ke neeche lana hai.",
            "gold": "Bring p95 below 200 milliseconds by Friday.",
            "terms": ["p95"],
            "facts": ["p95 below 200 milliseconds", "deadline Friday"],
        },
        {
            "script": "Ticket BPD dash one two three ko Q3 launch se pehle close karna.",
            "gold": "Close ticket BPD-123 before the Q3 launch.",
            "terms": ["BPD-123", "Q3"],
            "facts": ["close BPD-123", "before Q3 launch"],
        },
        {
            "script": "Budget twenty lakh INR se upar nahi jaana chahiye.",
            "gold": "The budget should not exceed 20 lakh INR.",
            "terms": ["INR"],
            "facts": ["budget not above 20 lakh INR"],
        },
        {
            "script": "API timeout ko ten seconds se three seconds karo.",
            "gold": "Change the API timeout from 10 seconds to 3 seconds.",
            "terms": ["API"],
            "facts": ["API timeout from 10 seconds to 3 seconds"],
        },
        {
            "script": "Launch June twenty four ko hai, June twenty one nahi.",
            "gold": "The launch is on June 24, not June 21.",
            "terms": [],
            "facts": ["launch date June 24", "not June 21"],
        },
    ],
    "acronyms": [
        {
            "script": "PRD mein likho ki SSO, SLA, aur API contract blocked hain.",
            "gold": "Write in the PRD that SSO, SLA, and the API contract are blocked.",
            "terms": ["PRD", "SSO", "SLA", "API"],
            "facts": ["PRD says SSO SLA API contract blocked"],
        },
        {
            "script": "PCI aur SOX dono ke liye audit trail chahiye.",
            "gold": "We need an audit trail for both PCI and SOX.",
            "terms": ["PCI", "SOX"],
            "facts": ["audit trail for PCI", "audit trail for SOX"],
        },
        {
            "script": "SDK release se pehle API docs update karo.",
            "gold": "Update the API docs before the SDK release.",
            "terms": ["SDK", "API"],
            "facts": ["API docs before SDK release"],
        },
        {
            "script": "OKR mein p95 latency aur NPS dono include karo.",
            "gold": "Include both p95 latency and NPS in the OKR.",
            "terms": ["OKR", "p95", "NPS"],
            "facts": ["include p95 latency in OKR", "include NPS in OKR"],
        },
        {
            "script": "PII logs ko BQ table se remove karo.",
            "gold": "Remove PII logs from the BQ table.",
            "terms": ["PII", "BQ"],
            "facts": ["remove PII logs from BQ table"],
        },
    ],
    "proper_names": [
        {
            "script": "Amit aur Gauri ko bolo ki Riskified owner clarify karein.",
            "gold": "Tell Amit and Gauri to clarify the Riskified owner.",
            "terms": ["Amit", "Gauri", "Riskified"],
            "facts": ["tell Amit and Gauri", "clarify Riskified owner"],
        },
        {
            "script": "Soham ke Cursor prompt mein Agoda aur Priceline dono mention karo.",
            "gold": "Mention both Agoda and Priceline in Soham's Cursor prompt.",
            "terms": ["Soham", "Cursor", "Agoda", "Priceline"],
            "facts": ["mention Agoda", "mention Priceline", "in Soham Cursor prompt"],
        },
        {
            "script": "Neha se pucho Partner Center rollout ka owner kaun hai.",
            "gold": "Ask Neha who owns the Partner Center rollout.",
            "terms": ["Neha", "Partner Center"],
            "facts": ["ask Neha", "who owns Partner Center rollout"],
        },
        {
            "script": "Rahul ko Slack update bhejo ki Fee Admin delayed hai.",
            "gold": "Send Rahul a Slack update that Fee Admin is delayed.",
            "terms": ["Rahul", "Slack", "Fee Admin"],
            "facts": ["send Rahul Slack update", "Fee Admin delayed"],
        },
        {
            "script": "Priya ko bolo Okta mapping ke bina SSO mat launch kare.",
            "gold": "Tell Priya not to launch SSO without Okta mapping.",
            "terms": ["Priya", "Okta", "SSO"],
            "facts": ["tell Priya", "do not launch SSO without Okta mapping"],
        },
    ],
    "code_switch_boundary": [
        {
            "script": "Yeh local tool company data ke liye safe hona chahiye because Claude allowed nahi hai.",
            "gold": "This local tool should be safe for company data because Claude is not allowed.",
            "terms": ["Claude"],
            "facts": ["local tool safe for company data", "Claude not allowed"],
        },
        {
            "script": "Mujhe Cursor ke liye clean prompt chahiye but meaning lose mat karna.",
            "gold": "I need a clean prompt for Cursor, but do not lose the meaning.",
            "terms": ["Cursor"],
            "facts": ["clean prompt for Cursor", "do not lose meaning"],
        },
        {
            "script": "Is summary ko English mein rakho, Hindi sirf meaning ke liye use karo.",
            "gold": "Keep this summary in English and use Hindi only for meaning.",
            "terms": [],
            "facts": ["summary in English", "Hindi only for meaning"],
        },
        {
            "script": "Local ASR fast hona chahiye, cloud call bilkul nahi.",
            "gold": "The local ASR should be fast, with absolutely no cloud call.",
            "terms": ["ASR"],
            "facts": ["local ASR fast", "no cloud call"],
        },
        {
            "script": "RambleFix ko raw transcript aur cleaned output dono dikhana chahiye.",
            "gold": "RambleFix should show both the raw transcript and the cleaned output.",
            "terms": ["RambleFix"],
            "facts": ["show raw transcript", "show cleaned output"],
        },
    ],
    "translation_trap": [
        {
            "script": "Hindi bola hai, par output English mein usable hona chahiye.",
            "gold": "The speech is in Hindi, but the output should be usable in English.",
            "terms": [],
            "facts": ["speech in Hindi", "output usable in English"],
        },
        {
            "script": "Devanagari default nahi chahiye, meaning clear chahiye.",
            "gold": "Devanagari should not be the default; the meaning should be clear.",
            "terms": ["Devanagari"],
            "facts": ["Devanagari not default", "meaning clear"],
        },
        {
            "script": "Raw ASR audit ke liye rakho, final output clean English ho sakta hai.",
            "gold": "Keep raw ASR for audit; the final output can be clean English.",
            "terms": ["ASR"],
            "facts": ["keep raw ASR for audit", "final output can be clean English"],
        },
        {
            "script": "Roman Hinglish acceptable hai agar meaning better preserve hota hai.",
            "gold": "Roman Hinglish is acceptable if it preserves meaning better.",
            "terms": ["Roman Hinglish"],
            "facts": ["Roman Hinglish acceptable", "if meaning better preserved"],
        },
        {
            "script": "Translate kar sakte ho, but numbers aur names exact rakhna.",
            "gold": "Translation is allowed, but numbers and names must be kept exact.",
            "terms": [],
            "facts": ["translation allowed", "numbers exact", "names exact"],
        },
    ],
    "cleanup_instruction": [
        {
            "script": "Isko concise Slack update bana do: SSO blocked hai because Okta mapping incomplete hai.",
            "gold": "Create a concise Slack update: SSO is blocked because Okta mapping is incomplete.",
            "terms": ["Slack", "SSO", "Okta"],
            "facts": ["concise Slack update", "SSO blocked", "Okta mapping incomplete"],
        },
        {
            "script": "Cursor prompt banao: API latency investigate karo without billing logic change.",
            "gold": "Create a Cursor prompt: investigate API latency without changing billing logic.",
            "terms": ["Cursor", "API"],
            "facts": ["create Cursor prompt", "investigate API latency", "do not change billing logic"],
        },
        {
            "script": "Isko Jira comment mein convert karo aur Amit ko owner rakho.",
            "gold": "Convert this into a Jira comment and keep Amit as the owner.",
            "terms": ["Jira", "Amit"],
            "facts": ["convert to Jira comment", "Amit is owner"],
        },
        {
            "script": "Meeting note summarize karo: SOX review delayed because evidence missing hai.",
            "gold": "Summarize the meeting note: the SOX review is delayed because evidence is missing.",
            "terms": ["SOX"],
            "facts": ["summarize meeting note", "SOX review delayed", "evidence missing"],
        },
        {
            "script": "Email draft karo but blame mat daalna, sirf blocker explain karna.",
            "gold": "Draft an email, but do not assign blame; only explain the blocker.",
            "terms": [],
            "facts": ["draft an email", "do not assign blame", "explain blocker only"],
        },
    ],
    "noise_fast_speech": [
        {
            "script": "Fast bolo: p95 latency, no cloud, raw transcript, cleaned output, paste target, sab measure karo.",
            "gold": "Measure p95 latency, no cloud, raw transcript, cleaned output, and paste target.",
            "terms": ["p95"],
            "facts": ["measure p95 latency", "measure no cloud", "measure raw transcript", "measure cleaned output", "measure paste target"],
        },
        {
            "script": "Keyboard noise ke saath bolo: deploy hold hai jab tak PCI signoff nahi milta.",
            "gold": "With keyboard noise, the deploy is on hold until PCI signoff is received.",
            "terms": ["PCI"],
            "facts": ["deploy on hold", "until PCI signoff"],
        },
        {
            "script": "Background fan ke saath: local model hang nahi hona chahiye.",
            "gold": "With background fan noise, the local model should not hang.",
            "terms": [],
            "facts": ["local model should not hang"],
        },
        {
            "script": "Very quick: Riskified, Partner Center, Fee Admin, SOX, PCI terms exact rakho.",
            "gold": "Keep the terms Riskified, Partner Center, Fee Admin, SOX, and PCI exact.",
            "terms": ["Riskified", "Partner Center", "Fee Admin", "SOX", "PCI"],
            "facts": ["keep listed terms exact"],
        },
        {
            "script": "Interrupted sentence: SSO launch wait karo, actually cancel nahi, hold karo.",
            "gold": "Hold the SSO launch; do not cancel it.",
            "terms": ["SSO"],
            "facts": ["hold SSO launch", "do not cancel SSO launch"],
        },
    ],
    "homophones_false_friends": [
        {
            "script": "Rollback nahi, rollout plan update karo.",
            "gold": "Do not roll back. Update the rollout plan.",
            "terms": [],
            "facts": ["do not roll back", "update rollout plan"],
        },
        {
            "script": "Cache issue hai, cash issue nahi.",
            "gold": "It is a cache issue, not a cash issue.",
            "terms": ["cache"],
            "facts": ["cache issue", "not cash issue"],
        },
        {
            "script": "Ship mat karo, skip bhi mat karo, bas review hold karo.",
            "gold": "Do not ship it and do not skip it; just hold the review.",
            "terms": [],
            "facts": ["do not ship", "do not skip", "hold review"],
        },
        {
            "script": "Queue depth check karo, cue card nahi.",
            "gold": "Check the queue depth, not the cue card.",
            "terms": ["queue depth"],
            "facts": ["check queue depth", "not cue card"],
        },
        {
            "script": "Partner Center mein flag hide karo, flight hide nahi.",
            "gold": "Hide the flag in Partner Center, not the flight.",
            "terms": ["Partner Center"],
            "facts": ["hide flag in Partner Center", "not flight"],
        },
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="eval_corpus/app_hinglish_adversarial_20260612.json")
    args = parser.parse_args()

    rows = []
    index = 1
    for bucket, items in BUCKETS.items():
        for local_index, item in enumerate(items, start=1):
            row_id = f"app_hinglish_{bucket}_{local_index:03d}"
            rows.append(
                {
                    "id": row_id,
                    "audio": f"recordings/app-leaderboard/{row_id}.wav",
                    "duration_seconds": None,
                    "category": "app_hinglish_adversarial",
                    "language_mix": "hi-en",
                    "accent_region": "india",
                    "task_type": "work_prompt",
                    "reference_level": "scripted_pending_recording",
                    "script": item["script"],
                    "gold": item["gold"],
                    "spoken_form_notes": "Record this naturally. Meaning-mode English or Roman Hinglish output is acceptable.",
                    "critical_terms": item["terms"],
                    "critical_facts": item["facts"],
                    "forbidden_assertions": infer_forbidden_assertions(str(item["gold"]), str(item["script"])),
                    "semantic_checks": infer_semantic_checks(str(item["gold"])),
                    "failure_traps": [bucket],
                    "privacy_class": "synthetic_work_script",
                    "source": "script_seed",
                    "license": "private_eval_internal",
                    "row_number": index,
                }
            )
            index += 1
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} rows to {out}")


def infer_forbidden_assertions(gold: str, script: str) -> list[str]:
    text = f"{gold} {script}".lower()
    forbidden: list[str] = []
    if "do not launch sso" in text or "sso mat launch" in text:
        forbidden.extend(["launch SSO without Okta mapping", "launch SSO"])
    if "claude is not allowed" in text or "claude allowed nahi" in text:
        forbidden.append("Claude allowed")
    if "do not lose" in text or "meaning lose mat" in text:
        forbidden.extend(["lose meaning", "losing meaning is acceptable"])
    if "no cloud call" in text or "cloud call bilkul nahi" in text:
        forbidden.extend([
            "cloud call allowed",
            "cloud calls allowed",
            "cloud call enabled",
            "cloud calls enabled",
            "cloud call is enabled",
            "cloud calls are enabled",
            "cloud call is not disabled",
            "cloud calls are not disabled",
            "cloud is not off",
            "cloud is not blocked",
        ])
    if "no cloud" in text:
        forbidden.extend([
            "cloud enabled",
            "cloud is enabled",
            "cloud call enabled",
            "cloud calls enabled",
            "cloud call is enabled",
            "cloud calls are enabled",
            "cloud call is not disabled",
            "cloud calls are not disabled",
            "cloud is not off",
            "cloud is not blocked",
        ])
    if "not be the default" in text or "default nahi" in text:
        forbidden.extend(["Devanagari should be the default", "Devanagari default"])
    if "do not roll" in text or "rollback mat" in text or "rollback nahi" in text:
        forbidden.extend(["roll this back", "rollback this", "roll back this"])
    if "do not send company data" in text:
        forbidden.append("send company data to Claude")
    if "do not delete" in text:
        forbidden.append("delete the Fee Admin flag")
    if "not tell" in text or "mat bolna" in text:
        forbidden.append("tell Amit the launch is done")
    if "do not ask to change billing" in text or "without changing billing logic" in text or "billing logic change karne ko mat" in text:
        forbidden.append("change billing logic")
    if "do not assign blame" in text or "blame mat" in text:
        forbidden.append("assign blame")
    if "do not cancel" in text or "cancel nahi" in text:
        forbidden.extend(["cancel SSO launch", "cancel the SSO launch", "cancel it"])
    if "until pci signoff" in text or "pci signoff nahi" in text:
        forbidden.extend(["deploy before PCI signoff", "deploy without PCI signoff"])
    if "not before" in text or "pehle nahi" in text:
        forbidden.append("start SOX review before PCI validation")
    if "before the partner center deploy" in text:
        forbidden.append("run Riskified check after Partner Center deploy")
    if "only after" in text:
        forbidden.append("merge the PR before the smoke test passes")
    if "not june 21" in text:
        forbidden.append("launch is on June 21")
    if "not exceed 20 lakh" in text:
        forbidden.append("budget exceeds 20 lakh INR")
    if "not the cash issue" in text or "not a cash issue" in text:
        forbidden.append("cash issue")
    if "not the cue card" in text:
        forbidden.append("cue card")
    if "not the flight" in text:
        forbidden.append("flight")
    if "do not ship" in text:
        forbidden.append("ship it")
    if "do not skip" in text:
        forbidden.append("skip it")
    return forbidden


def infer_semantic_checks(gold: str) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    lower = gold.lower()
    if "from 30 seconds to 10 seconds" in lower:
        checks.append({"type": "ordered_values", "name": "riskified_timeout", "from": "30 seconds", "to": "10 seconds"})
    if "from 10 seconds to 3 seconds" in lower:
        checks.append({"type": "ordered_values", "name": "api_timeout", "from": "10 seconds", "to": "3 seconds"})
    if "june 24" in lower and "not june 21" in lower:
        checks.append({"type": "required_and_forbidden", "name": "launch_date", "required": ["June 24"], "forbidden": ["June 21"]})
    if "after pci validation" in lower:
        checks.append({"type": "ordered_terms", "name": "sox_after_pci", "first": "PCI validation", "second": "SOX review"})
    if "before the partner center deploy" in lower:
        checks.append({"type": "ordered_terms", "name": "riskified_before_deploy", "first": "Riskified check", "second": "Partner Center deploy"})
    if "before the q3 launch" in lower:
        checks.append({"type": "ordered_terms", "name": "ticket_before_q3", "first": "BPD-123", "second": "Q3 launch"})
    if "not exceed 20 lakh inr" in lower:
        checks.append({"type": "required_and_forbidden", "name": "budget_cap", "required": ["20 lakh INR"], "forbidden": ["above 20 lakh", "exceed 20 lakh"]})
    if "should not hang" in lower:
        checks.append({"type": "stability_failure_possible", "name": "local_model_stability"})
    return checks


if __name__ == "__main__":
    import argparse

    main()
