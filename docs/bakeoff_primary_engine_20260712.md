# Primary-Engine Bakeoff — 2026-07-12 (running log)

Goal: pick the best local engine (or minimal router) for fast English and
relatively fast Hindi+English with maximum meaning/term preservation, as a
PRIMARY paste path (not a repair pass). Feeds the pre-paste LID router decision.

Corpus: `eval_runs/bakeoff-primary-engine-20260712/corpus.json` — n=70 real
retained clips (13 Hindi+English incl. 12 gold-seed rows, 57 English,
49 rows ≥30s). Wave 1 ran the 31-clip subset in `wave1_ids.txt`
(all 13 HI+EN + 18 stratified EN). 46 corpus rows still carry weak/draft
references and are queued for cloud re-gold (blocked on Gemini key).

Scoring: `scripts/rescore_transliteration_normalized.py` — Devanagari spans
transliterated to roman (ITRANS + schwa-strip), symmetric spelling collapse on
both gold and hypothesis, then the existing meaning/term metrics. Raw scores
kept alongside. Rows with empty gold are excluded from means.

## Wave 1 — normalized meaning by language (31 clips × 7 backends)

| Backend | EN meaning | EN terms | EN p50 | HI+EN meaning | HI+EN terms | HI+EN p50 | Read |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| qwen3_asr_mlx_auto | **0.890** | 0.897 | 6.5s | 0.702 | 0.551 | 3.9s | best all-rounder |
| mlx_whisper_large_v3_turbo_q4 | 0.873 | 0.883 | 4.1s | 0.661 | 0.409 | 3.3s | current-path baseline; translates Hindi |
| parakeet_mlx | 0.802 | 0.833 | **2.5s** | 0.673 | 0.361 | 2.0s | fastest; mangles Hindi terms |
| srota_qwen3_hinglish_mlx | 0.779 | 0.808 | 5.8s | 0.671 | 0.533 | 3.9s | no edge over qwen3 auto |
| oriserve_apex_transformers_mps | invalid (9/18 timeouts) | — | — | **0.919** | **0.752** | 7.6s | HI+EN specialist; runtime too slow |
| trelis_whisper_hinglish_preview_mps | invalid (9/18 timeouts) | — | — | 0.632 | 0.494 | 20.3s | FAILED PATH — see below |
| shunya_zero_stt_hinglish | invalid (9/18 timeouts) | — | — | 0.475 | 0.318 | 14.7s | FAILED PATH — see below |

Latency caveat: p50s include per-process model load; warm-serving latencies
will be measured for finalists in the router prototype.

## Failed / deferred paths (do not re-try without new evidence)

1. **Trelis/whisper-hinglish-preview** — model-card claims (13.7% conversational
   CS WER, "best open Hinglish") did not transfer to real DictaHue audio:
   0.632 normalized meaning on HI+EN (vs Apex 0.919), 20.3s p50 via
   transformers/MPS fp32, 600s timeouts on 60–120s clips. Might improve
   via MLX 4-bit conversion, but quality — not just speed — trails Apex, so
   conversion is not queued.
2. **shunyalabs/zero-stt-hinglish** — 0.475 HI+EN, 0.318 terms, slow. Out.
3. **ai4bharat/indic-conformer-600m-multilingual** — DEFERRED, not failed:
   gated HF repo (needs Soham's access request), Hindi-only .nemo variant
   needs NeMo toolkit, Devanagari-only output needs an extra transliteration
   stage in-product. Revisit only if Apex/Qwen3 Hinglish quality plateaus
   below release bar.
4. **transformers/MPS runtime for any Whisper-family model on long clips** —
   9/18 English rows hit the 600s timeout. Product path must use
   MLX or GGML runtimes; transformers pipeline is eval-only, short clips only.
5. (Wave 1 confirms prior ledger entries: whisper-turbo silently TRANSLATES
   Hindi segments to English; Parakeet v3 has no Hindi — both unusable as a
   solo engine for HI+EN.)

## Open questions for wave 1b (running)

- `oriserve_apex_mlx` / `oriserve_hindi2hinglish_apex_ggml_q5`: does Apex keep
  its 0.919 HI+EN meaning on a fast runtime, and what is real p50/p95 incl.
  long clips?
- `qwen3_asr_mlx_hindi` (forced-Hindi decode): does it close the gap to Apex
  as the HI+EN route while staying one model family in product?

## Wave 1b — Apex fast runtimes + forced-Hindi Qwen3 (same 31 clips)

| Backend | EN meaning | HI+EN meaning | HI+EN terms | HI+EN p50/p95 | Read |
| --- | ---: | ---: | ---: | --- | --- |
| oriserve_apex_mlx | 0.000 (degenerate) | 0.842 | 0.693 | 2.6s / 4.8s | KEEP for HI-risk route only |
| oriserve_hindi2hinglish_apex_ggml_q5 | timeout | timeout | — | 600s | FAILED PATH — all 31 rows timed out |
| qwen3_asr_mlx_hindi (forced hi) | 0.612 | 0.660 | 0.545 | 4.1s / 13.8s | FAILED PATH — strictly worse than auto |

More failed paths:

6. **Apex (any runtime) on pure-English audio** — decodes to degenerate
   repeated Devanagari (`वह वह वह…`) on 18/18 English clips, zero meaning.
   Not graceful degradation: total garbage. Consequence: any router that can
   send English audio to Apex MUST pair it with a degeneracy guard
   (repetition score / Devanagari-on-English-probe) and an English fallback.
   Apex-MLX keeps 0.842 HI+EN meaning at p50 2.6s — it stays, but only behind
   a Hindi-risk gate.
7. **Apex GGML q5 via whisper-cli** — worse than a timeout: every invocation
   wedges instantly in uninterruptible kernel wait (`U` state, 0 CPU),
   unkillable even with SIGKILL until reboot. The 31-clip wave-1b run leaked
   30 stuck processes, degraded the resident server's `/inference` endpoint,
   and invalidated the 2026-07-12 quality-gate run. Backend now hard-blocked
   in the bakeoff harness behind `RAMBLEFIX_ALLOW_APEX_GGML=1`. Reboot
   required to clear; rerun quality gate only after reboot.
8. **qwen3_asr_mlx_hindi (forced Hindi decode)** — worse than auto-detect on
   both buckets. Forcing language hurts; Qwen3's internal LID is better.

## Router prototype result (31 clips) — DECISION

`scripts/prototype_pre_paste_router.py`: whisper-tiny probe on first 10s
(p50 0.16s) → Hindi-risk gate → Apex-MLX with degeneracy guard, else turbo-q4.
One engine choice BEFORE paste, one paste, no repair chain.

| Path | EN meaning/terms (n=18) | HI+EN meaning/terms (n=13) |
| --- | --- | --- |
| **Pre-paste router** | **0.873 / 0.883** | **0.885 / 0.701** |
| turbo-q4 (current fast path) | 0.873 / 0.883 | 0.661 / 0.409 |
| qwen3_asr_mlx_auto (best single) | 0.890 / 0.897 | 0.702 / 0.551 |
| oriserve_apex_mlx solo | 0.000 (degenerate) | 0.842 / 0.693 |
| live Hindi-repair path (07-11 gate) | — | 0.772 / 0.744 (n=3 sentinels) |

Routing confusion: tp=7 fn=6 fp=0 tn=18. Zero false positives is the safety
headline (English audio never reaches Apex). The 6 false negatives are
light-Hindi clips that fell through to the English engine and still scored
well — graceful failure direction. The prototype probe sees only the first
10s; the product probes every hold-time chunk, so live recall should beat
the prototype.

**Decision: adopt the pre-paste router as the product architecture.**
- English route: keep turbo-q4 initially (identical scores, no product churn);
  Qwen3-auto is a later candidate (+0.017 meaning) once warm-served.
- Hindi-risk route: Apex-MLX behind risk gate + degeneracy guard + English
  fallback text always available.
- Retire the post-paste Hindi repair/replace chain (R-033..R-041 surface).
- The existing `hindi_preflight_oriserve_apex` route (R-033/R-034) is the
  implementation seed: extend it from "preflight for sentinel-like clips"
  to "the only Hindi path", gate the repair chain off behind a config flag,
  and keep the guard + fallback semantics from this prototype.

Caveats: 46/70 corpus rows still carry weak/draft gold (re-gold blocked on
Gemini key); latencies are cold-process, warm resident numbers pending;
staged-path comparison row is from a different (sentinel) corpus.

## Term-restore bakeoff (task follow-up, 2026-07-12) — FAILED PATH

Hypothesis (from competitor research): multi-pass fuzzy dictionary restore
(exact → Levenshtein → phonetic) on router output lifts term coverage.
Measured on the final-gold 31-clip router outputs
(`scripts/bakeoff_fuzzy_term_restore.py`):

| Config | EN terms | HI+EN terms | Meaning regressions | Read |
| --- | --- | --- | --- | --- |
| baseline (glossary only) | 0.880 | 0.701 | — | — |
| fuzzy (lev + phonetic) | 0.875 | 0.660 | 0, but FP rewrites | WORSE: `such→SOC2`, `site→STT`, `lete→Ludo`, `code→Codex` |
| strict (lev-1, first-char, ≥6) | 0.880 | 0.701 | 0 | zero restores fired — safe but useless |

Verdict: do NOT wire. DictaHue's existing glossary + approved memory-terms
pass already captures the recoverable near-miss class; the remaining term
misses are dropped/translated words in the ASR output itself, which post-hoc
matching cannot restore (and phonetic matching actively corrupts common
words into work terms). The research recommendation applies to tools with no
term system at all. Next real lever for HI+EN terms is model-side
(Apex glossary-conditioning or a better Hinglish checkpoint), not text-side.

## Packaging decision (Soham, 2026-07-12)

Hybrid bundle: ship English-complete (~3 GB: turbo-q4 + detector + trimmed
venv — first dictation works offline out of the box), and download Apex-MLX
(~1.5 GB, checksum-verified) on first launch or when the user enables
Hindi+English. Router returns English-only results until Apex lands.
Matches the "experimental Hindi+English" claim boundary and keeps the DMG
near normal-Mac-app size. Venv trim (drop eval-only deps like torch from the
shipped runtime) is a packaging work item.

## Translate-chip bakeoff (task 8, 2026-07-13) — FAILED PATH (deferred)

Soham's ask: paste roman Hinglish, offer a transient "→ English" chip.
Ship rule: local translation meaning >=0.85 vs English gold, terms hold.
Measured (n=12, Gemini-2.5-pro English gold, translit-normalized):
hinglish_as_is 0.517/0.462 · qwen2.5-3B translate 0.521/0.523 (3/12 guard
rejections, p50 0.93s) · turbo-translate 0.423/0.423. Verdict: 3B local
translation adds nothing over raw Hinglish; chip DEFERRED until a stronger
local translator (7-14B class) clears the bar. Chip UX design retained in
task notes. Evidence: eval_runs/bakeoff-primary-engine-20260712/translate-mode/.

## builderr STT submission review (task 11, 2026-07-13)

Seven entrant repos reviewed (Meet, Vishwas, Harsimran, Sham, Sankeerth,
Arnav, Darshan); none beat the RambleFix baseline on the hidden set. Three
overfit/gamed (hardcoded sample phrases, call-stack clip-id sniffing,
guess-ahead autocomplete). Verdicts: drop x5, interesting x2 (Vishwas
tail-window incremental decode — relevant only if live partials ever ship;
Sankeerth dictionary-as-decode-context — streaming-native upgrade path).
ADOPT: Arnav vocab module — initial_prompt term bias + deterministic
spelled-out-tech repair; queued as its own gated bakeoff (terms gap).
Full table in the task log.

## Popular-engine sweep close-out (task 10, 2026-07-13)

Handy (23k-star FOSS leader) engines on our final-gold corpus:
parakeet_mlx EN 0.806/0.833 (no Hindi) — already in wave 1;
nemotron35 (their new streaming engine) EN 0.301/0.327, HI+EN 0.509/0.417,
p50 36s via NeMo — FAILED PATH: emits everything (incl. English) in
Devanagari; unusable for this product even normalized. Verdict: the router
(EN 0.897/0.900, HI+EN 0.885/0.701) beats every engine shipped by the most
popular free tools on our real-usage corpus. Sweep learnings: nothing to
adopt from Handy's pipeline (full-file-on-release + CPU engines); the field's
useful ideas came from the builderr submissions review (Arnav vocab module,
task 16) instead. Not run: Apple SpeechAnalyzer (needs macOS-26 Swift API
harness — candidate for a later zero-download fallback), Moonshine
(English-only tiny, addresses no open gap).

## Structure-strength bakeoff (task 14, 2026-07-13) — FAILED PATH

Qwen2.5-3B edit-constrained cleanup (filler strip + self-correction +
punctuation) vs raw router output, n=58 final-gold rows:
meaning 0.895→0.818 (24/58 rows REGRESSED), terms 0.858→0.788, fillers only
7/22 removed, 9/58 guard rejections, +1.9s p50. Self-correction fixtures
looked 3/3 green but manual inspection shows 2/3 actually wrong (one
correction REVERSED: "seventy five → fifty"; one name mangled) — the
automated fixture check was too weak and is noted as such. The existing
Swift safety policy would have accepted 32/58 of these harmful rewrites,
so the policy alone is not a sufficient guard for generative structure.
VERDICT: keep current rules-only structure. Generative cleanup at 3B scale
destroys meaning wholesale; retry only at 7-14B with a much stricter
verifier, and only if Wispr-level polish becomes a launch blocker.
Evidence: eval_runs/bakeoff-primary-engine-20260712/structure-strength/.

## Vocab-module bakeoff (task 16, 2026-07-13) — mechanism ADOPTABLE, needs our data

Arnav's normalize_tech_words + repair_common_asr_errors applied post-hoc to
router outputs (n=58 final-gold): ZERO meaning regressions (safe, unlike the
failed fuzzy restore), but gains ~0 (EN 0.897→0.898, HI+EN unchanged) — his
dictionary targets his vocabulary, not Soham's. Next step (daytime, with
Soham): populate the module from config/memory_terms.json + observed miss
patterns (e.g. SQQQ→SQQ class from the 07-12 live session), then re-measure.
Evidence: eval_runs/bakeoff-primary-engine-20260712/vocab-module/.

## Decode-bias follow-up (task 16 part 2, 2026-07-13) — ADOPTED on Apex route

Soham asked whether the decode-time initial_prompt half was tested. Results:
- English route: contaminated first run (14 ffmpeg load errors scored 0);
  clean rerun n=31: meaning 0.892→0.901 mean but 10/31 rows regress —
  NOT adopted for the English hot path (fails zero-regression bar).
- Apex Hindi route, production-shaped prompt (global memory_terms+glossary
  only, no per-clip oracle), n=7 Apex-routed clips: terms 0.872→0.929,
  meaning flat, ZERO regressions — ADOPTED.
Shipped: `_apex_term_prompt()` in external_asr.py feeds initial_prompt to
Apex MLX (flag RAMBLEFIX_APEX_TERM_PROMPT, default on; capped at 20 terms).
As Soham approves more memory terms, the bias strengthens automatically.
Caveat: n=7 is small; watch live Hindi runs. Evidence:
eval_runs/bakeoff-primary-engine-20260712/decode-bias/.

## 2026-07-14 — p95 gate fail root-caused: eval warm-up artifact, not product latency

Two consecutive quality-gate runs failed `short product p95 <= 3.0s` (3.251s, then
3.480s on a cooled machine — so NOT thermal). Per-row analysis: the p95 driver was
the FIRST replayed row both times (English clip 100544, mlx-whisper route), while
near-identical sibling clips ran 2.0-2.5s. Warm-server replay of the same clip:
3.43s first hit, then 2.74s / 2.82s steady-state — under the bar. The Apex term
prompt was exonerated (slow row is on the English route, which never sees it).
Fix: `scripts/eval_dictate_audio_product_path.py` now fires one untimed warm-up
request before timed rows (`--no-warmup` to disable), since prod keeps the model
resident and first-request page-in is a harness artifact at sentinel n=7 where
p95 == max. Note kept honest: steady-state short p95 ~2.8s is real and close to
the 3.0s bar — headroom is thin, watch it.

## 2026-07-14 — orb release gates: final verdict (loop close)

Quality gate (warm-up fix): short p95 2.542s PASS; only remaining FAIL is the
known experimental-Hindi bar (meaning 0.834 / term 0.750 — identical to pre-orb,
so the orb/toast/R-049 work introduced no quality regression). Council --full:
all automated gates PASS (behavior contract, security, claims audit, native
regression suite incl. orb tests, builds, resident endpoint, site smoke, local
packaging + checksums). FAILs are all known/human-gated: headless live smoke
(66s silence -> whisper "I'm sorry" loop was correctly BLOCKED as
blocked_low_quality / degenerate=true, paste_success=false — the guard chain
did its job; test just needs a human), rolling live health 0.900<0.950 (window
includes pre-fix R-049 runs; recovers with usage on fixed build), dirty eval
machine (reboot before publishable latency numbers), Apple cert + notary +
embed-venv (public build only; local package green). Task 19 closed.

## Disfluency-tagger bakeoff (task 20, 2026-07-14) — mechanism WINS, license blocks shipping

Follow-up to docs/research_structure_sota_20260714.md. Candidates on the 52
English structure rows (6 mixed-language rows skipped, as prod rules do):
raw / swift_rules / fdt-disfluency-distilbert-66m-v2 / tiny-4m / tagger+rules
/ tagger+ALLOWLIST (accept DELETE only for words in a filler set — model
proposes, allowlist disposes).

Results: current swift_rules remove 0/22 regex-filler hits (they only trim
leading/repeated fillers). Raw tagger removes 16/22 at 32ms p50 (4ms for the
4M, but it only removes 9/22) — but made 2 contentful deletions ("slightly",
"what") and 6 rows scored as regressed vs verbatim gold. Adjudication: ALL 6
regressions are scorer artifact — the deleted fillers exist in the cloud gold,
so the metric penalizes desired deletions. Proof: rescoring vs filler-stripped
gold, allowlist variant == raw exactly (0.8832 both), 0 regressed rows, and
the allowlist provably kills the contentful-deletion class. Self-correction
fixtures: untouched (2/3) or wrong-span (1/3, neutered by allowlist) — repair
resolution is NOT this mechanism and stays out of scope.

VERDICT: adopt the mechanism (66M deletion tagger + allowlist gate + existing
Swift guards downstream) pending ONE blocker: the public checkpoint is
trained partly on DailyDialog (CC BY-NC-SA) — not shippable as-is. Path:
retrain on cleared data using the public synthetic-injection recipe
(stillerman/fdt-disfluency-synthetic), or an alternative clean corpus.
English route only; Hinglish rows stay untouched (no code-mixed disfluency
data exists). Evidence: eval_runs/bakeoff-primary-engine-20260712/disfluency-tagger/.
