# DictaHue Launch Eval - Resident Local Server Path - 2026-07-09

## What Changed

The app no longer depends on the `whisper.cpp` server on port `8178` for the hot path.

Current V0 hot path:

1. Native app records local audio.
2. Native app posts the WAV to the resident local Srota server at `http://127.0.0.1:8188/inference`.
3. Srota runs local `mlx-community/whisper-large-v3-turbo-q4`.
4. Glossary/approved phrase fixes run locally.
5. Native app pastes the first usable transcript, then structure can run safely after paste.

This is still local-only. No product path cloud call is used.

## Gates Run

| Gate | Result |
| --- | --- |
| `scripts/regression_srota_inference_endpoint.py` | Passed |
| `script/regression_ramblefix_hotkey.sh` | Passed |
| `script/install_ramblefix_app.sh` | Passed, app installed and relaunched |
| `script/package_macos_release.sh` | Passed local package build |
| `script/audit_macos_release_artifact.sh dist/release/DictaHue.app` | Passed, ad-hoc signed |
| `script/audit_release_security.sh dist/release/DictaHue.app --local` | Passed, local-only/security checks clean |
| `script/audit_release_security.sh dist/release/DictaHue.app --public` | Fails correctly until Developer ID signing |
| `script/audit_release_checksums.sh dist/release/DictaHue-0.1.0.SHA256SUMS` | Passed |
| `script/audit_public_launch_readiness.sh --allow-placeholders` | Passed with warnings |
| Direct ASR probe on known failure `20260709-004744-F45AE3.wav` | Passed: `1.714s`, fixed `end-to-end` and `split flow` |
| Post-restart Srota endpoint check | Passed: `scripts/regression_srota_inference_endpoint.py` |
| `script/run_final_launch_eval.sh --allow-dirty-machine --allow-placeholders` | Passed local launch gate |

Machine caveat:

- `script/audit_eval_machine_health.sh --strict` still fails because old `whisper-cli` / `whisper-server` jobs are stuck in `UEs`.
- The current app no longer uses or starts that path, but public latency charts should still be rerun after a full reboot.
- `MLX` and `MPS` accelerator checks pass; Srota server on `8188` is listening.
- `kill` and `kill -9` did not clear those stale jobs. They require a real macOS reboot before final public latency claims.

Current local package:

```text
9e651807da15205c078518cd9ffae4593250300c83817246c90850ae7008e2b7  DictaHue-0.1.0.dmg
0966bf04f57e6bb2c1426e30d946caaa858eb6de103533953cf0e09cae545b5a  DictaHue-0.1.0.zip
```

Distribution status:

- Local package exists and passes bundle/runtime/checksum audits.
- Packaged runtime now includes `script/start_srota_server.sh`, an embedded `.venv`, and a managed Python base under `RambleFixRuntime/python`.
- Current embedded artifact sizes: app `1.5G`, DMG `889M`, ZIP `785M`.
- Release security audit passes in local mode: required permissions only, no Screen Recording, runtime local-only, no cloud endpoint or secret marker in app binary.
- Signing identity installed: `RambleFix Local Dev` only.
- `codesign` shows `Signature=adhoc`.
- `spctl` rejects `dist/release/DictaHue.app`.
- Public release still needs `Developer ID Application`, notarization, and stapling.

## Expanded Same-Corpus Result

Corpus: `eval_corpus/launch_real_use_expanded_20260709_checked.json`

Rows:

- 19 total valid clips
- 15 clips under 60 seconds
- 4 clips from 60-120 seconds
- 10 recent English/user regression rows
- 5 Hindi+English probe rows
- 4 long English rows
- 1 stale Hindi+English row was removed because the referenced WAV is missing.

Current resident-server app result from the final local launch gate:

| Path | Clips | Useful | Meaning | p50 | p95 | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| DictaHue resident local server, first paste | 19 | 0.873 | 0.934 | 2.502s | 9.361s | Current app path, dirty-machine caveat |
| DictaHue resident local server, structured if unchanged | 19 | 0.870 | 0.930 | 2.506s | 9.365s | Structure slightly hurts aggregate score |
| Earlier valid-row resident-server run | 19 | 0.873 | 0.934 | 2.483s | 9.383s | Same valid corpus, comparable latency |
| Earlier resident-server run | 20 | 0.885 | 0.925 | 1.802s | 6.869s | Same corpus, comparable latency |
| Old Python CLI fallback path, first paste | 20 | 0.859 | 0.924 | 3.729s | 7.552s | Older path, no longer the intended hot path |

Release sentinel from the same final local launch gate:

| Gate | Rows | p50 | p95 | Max | Terms / meaning |
| --- | ---: | ---: | ---: | ---: | --- |
| Trusted native product path | 13 | 2.434s | 3.608s | 4.997s | Hinglish terms `1.000`, Hinglish meaning `0.932`, recent meaning `0.935` |
| Short native product rows | 11 | - | 2.511s | 2.511s | Short rows split out to avoid long-clip false failures |
| Long native product rows | 2 | - | 4.997s | 4.997s | Long rows tracked separately |
| Known failure replay | 1 | 2.344s | 2.344s | 2.344s | Terms `1.000`, misses `0` |
| Structure safety | 181 history rows | - | - | - | 82 accepted, 0 unsafe accepted |

## Local Tool Comparison On Same Corpus

| Local path | Clips | Useful | Meaning | p50 | p95 | Read |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| DictaHue resident local server, final first paste | 19 | 0.873 | 0.934 | 2.502s | 9.361s | Best current score on valid local corpus, but p95 is not clean |
| MLX large-v3-turbo q4 transcribe | 20 | 0.872 | 0.925 | 3.079s | 5.694s | Similar meaning, lower score |
| Parakeet MLX | 20 | 0.850 | 0.875 | 1.749s | 4.491s | Fast, worse meaning/Hinglish |
| MLX large-v3-turbo q4 translate | 20 | 0.838 | 0.882 | 2.272s | 4.737s | Worse terms/Hinglish |
| MLX Whisper tiny | 20 | 0.798 | 0.790 | 0.698s | 1.724s | Fast but not launch-quality |

By category:

| Category | DictaHue useful | Meaning | Terms | p50 | p95 | Best local comparison |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Recent English, under 60s | 0.877 | 0.935 | 0.820 | 2.439s | 5.047s | Good quality; speed is not claim-ready on dirty machine |
| Hindi+English, under 60s | 0.906 | 0.932 | 0.986 | 3.623s | 4.761s | Strongest current wedge versus local baselines |
| Long English, 60-120s | 0.821 | 0.933 | 0.936 | 8.010s | 14.788s | Not launch-claim ready |

## Historical Wispr Flow And Muesli Context

Different corpus, so this is directional only.

| Tool | Bucket | Rows | Useful | Meaning | Terms | p50 | p95 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Wispr Flow | English | 11 | 0.872 | 0.896 | 0.843 | 0.659s | 1.296s |
| Wispr Flow | Hinglish | 8 | 0.766 | 0.750 | 0.912 | 0.716s | 1.270s |
| Muesli/WhisperKit | English | 2 | 0.980 | 1.000 | 1.000 | 6.511s | 6.722s |
| Muesli/WhisperKit | Hinglish | 2 | 0.827 | 0.900 | 0.800 | 6.402s | 6.467s |

Read:

- Wispr Flow is still much faster in this historical run, but it is cloud-backed and not a local-only comparator.
- Muesli/WhisperKit had strong quality on a tiny sample but was slow.
- DictaHue's wedge remains local-only work dictation with Indian/Hinglish robustness and learned terms.

## Hits And Misses

Hits:

- Known failure fixed: `end-all safe replacement` -> `end-to-end safe replacement`; `respect flow` -> `split flow`.
- Hindi+English under-60 score is now better than measured local baselines.
- English under-60 quality is still strong, but dirty-machine latency is slower than earlier clean-ish runs.
- The app no longer spawns the broken `whisper.cpp` server by default.
- Release security audit now proves the packaged runtime is local-only and contains no obvious cloud endpoint/secret marker.

Misses / caveats:

- Long clips are still slower: latest p95 `14.788s` on 60-120s English.
- One short clip still misses `shops` as `sharps`.
- Short latency is worse on the dirty machine; final publishable latency needs a reboot and strict rerun.
- Long-row references are indicative, not final claim-grade.
- There is no long Hindi+English claim-grade set yet.
- Strict machine health still fails until the old `UEs` whisper jobs are cleared by a full reboot.

## Current Launch Claim Status

Safe claim after the latest rerun:

- Local-only dictation.
- Strong short English and Indian-English work dictation.
- Better than measured local baselines on Hindi+English under 60 seconds.
- Better overall useful score than measured local baselines on the current expanded corpus.
- Short-dictation first output is around `2.2-2.5s` in the latest dirty-machine gate; earlier clean-ish runs were faster.

Do not claim yet:

- "Hundreds of samples" - not proven here.
- "Always faster than Wispr Flow" - false on current evidence.
- "Long meeting mode is ready" - not in V0.
- "Long dictation latency is solved" - false on current evidence.
- "Public-install ready" - Developer ID signing and notarization still missing.

## Next

1. Full reboot, then rerun `script/audit_eval_machine_health.sh --strict`.
2. Rerun the resident-server expanded eval and save as the final public table.
3. Build notarized Developer ID package.
4. Run release security/local-only audit.
5. Publish site with honest benchmark table and feedback link.
