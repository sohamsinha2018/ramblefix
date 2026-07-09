# RambleFix Current Regression Readout - 2026-07-09

## Goal

Do not ship regressions. Known simple misses must become fixtures. App behavior should stay fast for English, strong for Hindi+English, and safe when structure updates land.

## What Was Tested

- 95 public short clips: 45 English, 50 Hindi+English.
- 20 public long clips: 10 English, 10 Hindi+English, about 60-120 seconds.
- 6 actual-user WAVs that still exist locally. The older 11 actual-user rows reference missing WAV files, so they cannot be rerun honestly.
- Regression sentinel: 14 trusted current product rows plus 1 known-failure fixture.
- Latest resident-server expanded check: `eval_runs/resident-expanded-continuation-20260709-193809`, 19 real saved WAVs, including 10 recent English, 5 Hindi+English, and 4 long English rows.
- Latest final launch eval: `eval_runs/final-launch-eval-20260709-195158`, run in dirty-machine override mode because stale kernel-state whisper processes remain.
- Latest same-WAV local MLX/Parakeet bakeoff: `eval_runs/expanded-real-local-mlx-bakeoff-20260709-200156`, 19 real saved WAVs.
- Disputed / human-review-needed Hindi seed rows are no longer release-blocking sentinel rows.

## Regression Gates

- Quality regression gate: passed.
- Native hotkey regression gate: passed.
- V0 release-scope gate: passed.
- Public source-surface gate: passed. It blocks generated binaries, models, recordings, eval outputs, personal memory/phrase config, large artifacts, personal absolute paths, and real API-key-shaped strings from the GitHub publish surface.
- Public staging surface: 210 audited files. Rough internal research notes are ignored for the first public push.
- Release security gate: passed in local mode; fails correctly in public mode until Developer ID signing/notarization.
- Known failure `end-to-end / split flow`: now passes with no term misses.
- Structure safety: 86 accepted structure updates, 0 unsafe accepted rows, 0 protected-term drops.

## Current Performance

Latest resident-server path on real saved WAVs from the final local launch gate:

| Bucket | First-paste useful | Meaning | Terms | p50 | p95 | Read |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Recent English, under 60s | 0.877 | 0.935 | 0.820 | 2.568s | 5.274s | Strong quality; speed is not claim-ready on dirty machine. |
| Hindi+English, under 60s | 0.906 | 0.932 | 0.986 | 3.853s | 5.088s | Best current wedge versus local baselines. |
| Long English, 60-120s | 0.814 | 0.920 | 0.936 | 8.043s | 13.833s | Useful but not a V0 speed claim. |
| Overall expanded corpus | 0.872 | 0.931 | - | 2.609s | 9.006s | Best aggregate useful score on the expanded real-use MLX/Parakeet bakeoff, but p95 is dirty and long clips are not solved. |

Same-WAV expanded local bakeoff on the same 19 real WAVs:

| Backend | Useful | Meaning | p50 | p95 | Read |
| --- | ---: | ---: | ---: | ---: | --- |
| Resident product first paste | 0.872 | 0.931 | 2.609s | 9.006s | Best aggregate quality; dirty latency. |
| Parakeet MLX | 0.867 | 0.913 | 2.743s | 4.615s | Faster and promising, but has unsafe misses on short/long rows. |
| MLX Whisper large-v3-turbo q4 transcribe | 0.861 | 0.934 | 4.164s | 7.764s | Strong meaning, slower outside resident server path. |
| MLX Whisper large-v3-turbo q4 translate | 0.812 | 0.889 | 4.092s | 7.347s | Rejected as default: drops mixed/long meaning. |
| MLX Whisper tiny | 0.799 | 0.818 | 1.893s | 2.757s | Rejected as default: fast but loses too much meaning. |

Category read from this bakeoff:

- Recent English: resident product is strongest on useful score (`0.878`) versus Parakeet (`0.864`), raw MLX turbo (`0.856`), and tiny (`0.831`).
- Hindi+English under 60s: Parakeet is slightly higher on useful score (`0.913`) than resident product (`0.906`), but product has slightly better term preservation (`0.986` vs `0.979`) and safer known behavior.
- Long English: raw MLX turbo has the best useful score (`0.829`), Parakeet is close (`0.816`), resident product is `0.814`; all are not V0 speed claims.
- Product decision: do not switch default. Parakeet is a candidate for a guarded second route, not a blind replacement.

Structure result on the same run:

- First paste: useful `0.872`, meaning `0.931`, p50 `2.609s`, p95 `9.006s`.
- Structured-if-unchanged: useful `0.867`, meaning `0.927`, p50 `2.613s`, p95 `9.010s`.
- Read: structure is safe but not improving aggregate score yet. Keep it conservative.

Continuation dirty-machine finding:

- The final launch gate run `eval_runs/final-launch-eval-20260709-193430` failed narrowly because one long sentinel row hit `6.108s` versus the `6.000s` p95 limit.
- The restarted-machine attempt `eval_runs/final-launch-eval-20260709-195158` passed in dirty-machine override mode, but strict health still failed because stale `UEs` whisper processes remained.
- A direct repeat probe on the 81.6s long English clip took `31.704s` then `20.542s` while load average was above `4`, confirming current long-latency evidence is not publishable.

Latest trusted sentinel gate:

- Product rows: `13`.
- p50 `2.540s`, p95 `3.814s`, max `5.312s`.
- Short rows: p95 `2.712s`, max `2.712s`.
- Long rows: p95 `5.312s`, max `5.312s`.
- Trusted Hindi+English terms `1.000`, meaning `0.932`.
- Recent English/user rows terms `0.820`, meaning `0.935`.
- Known failure `end-to-end / split flow`: term coverage `1.000`, misses `0`.
- Structure safety: `86` accepted, `0` unsafe accepted.

Machine caveat:

- Strict health still fails because old `whisper-cli` / `whisper-server` jobs are stuck in `UEs`.
- They show `0.0% CPU`, but `kill -9` cannot clear them.
- The installer no longer starts the legacy `8178` whisper.cpp server; the product route is local Srota on `8188`, and a hotkey regression guard prevents the legacy autostart from returning.
- The latest user restart did not clear these jobs; `uptime` still showed the same Mac boot session. Current live app launch uses `8188`; `8178` is not required by the product route.
- A guard now prevents legacy whisper.cpp autostart for the Srota `8188` endpoint even if a debug flag is accidentally enabled.
- Final public latency numbers need a strict machine-health pass with no stuck legacy processes.

Older public-benchmark readout:

| Bucket | Current RambleFix | Local Baseline | Read |
| --- | ---: | ---: | --- |
| Short clean English | score 0.935, p95 0.767s | OpenWhispr small score 0.939, p95 0.537s | At-par quality, slower. |
| Short YouTube/Indian English | score 0.795, p95 0.940s | OpenWhispr small score 0.757, p95 0.766s | Better quality, slower. |
| Short Hindi+English | score 0.674, terms 0.772, p95 0.705s | OpenWhispr base 0.455, small 0.339 | Clearly better than local baselines. Slightly below older RambleFix Hinglish route: 0.693. |
| Long English | score 0.545, p95 3.204s | OpenWhispr small 0.561, p95 2.400s | Not a strong claim. Do not market long English as beaten. |
| Long Hindi+English | score 0.641, terms 0.783, p95 3.666s | OpenWhispr base 0.285, small 0.122 | Clearly better than local baselines. Lower quality than older Hinglish route: 0.700, but faster than its 4.138s p95. |
| Actual-user existing 6 | score 0.998, meaning 1.000, terms 1.000, p95 1.546s | Wispr directional 0.706, OpenWhispr small 0.998 | Strong but small sample. |

## Why The Miss Happened

The flagged miss was mostly model limitation/acoustic ambiguity. The fast Whisper path heard:

- `end-to-end` as `end-of-the-long`
- `split flow` as `split floor`

That was not caused by heat or structure. The large/turbo local model also missed those terms, so it was not fixed by just using a larger generic model.

There was also a product bug: approved phrase repair was not applied before first paste. That is now fixed and covered by the known-failure regression fixture.

## Current Release Decision

V0-safe path:

- Keep fast local first paste.
- Keep approved phrase repair before first paste.
- Keep safe structure update only if original pasted text is still present.
- Keep Hindi+English conservative: useful and better than local baselines, but do not let a slow/wedged finalizer block foreground UX.

Not V0-safe:

- Forced direct Hindi finalizer path. One direct forced probe wedged beyond 60 seconds on a tiny clip.
- Marketing long English as best-in-class.
- Switching to tiny or translate mode for speed. Same-WAV expanded bakeoff showed clear meaning drops.
- Blind Parakeet default. It is faster and close, but still has bad misses without a router/guard.

## Next Bottleneck

Hindi+English remaining gap is routing/model quality, not safe replacement. The background Hindi pass ran, but on the public probe it returned the same text and policy correctly kept the first paste.

Next work should target selecting a genuinely better Hindi candidate without making English slower or risking bad replacements.
