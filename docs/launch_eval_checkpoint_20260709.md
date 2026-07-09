# DictaHue Launch Eval Checkpoint - 2026-07-09

## Goal

Prove the V0 launch claim without hiding regressions:

- English under 60s should be at par with the best measured local path.
- Hindi+English under 60s and 60-120s should be materially better than measured local baselines.
- Structure should make text cleaner without dropping meaning, terms, numbers, or negations.
- Package should stay local-only and release-safe.

## Current Fresh Checks

These were run after the latest timeout/process-cleanup, legacy-sidecar guard, and embedded-runtime patch:

| Check | Result |
| --- | --- |
| `script/regression_ramblefix_hotkey.sh` | Passed |
| `scripts/eval_native_friendly_rewrite.py ...` | Passed: 179 rows, 81 accepted updates, 0 unsafe accepted |
| `script/smoke_site_visual.sh` | Passed: desktop, tablet, and mobile screenshots captured |
| `RAMBLEFIX_PACKAGE_EMBED_VENV=1 script/package_macos_release.sh` | Passed local packaging with embedded Python/MLX runtime |
| `script/audit_macos_release_artifact.sh dist/release/DictaHue.app` | Passed, ad-hoc signed |
| `script/audit_release_checksums.sh dist/release/DictaHue-0.1.0.SHA256SUMS` | Passed |
| `script/audit_public_launch_readiness.sh --allow-placeholders` | Passed with warnings |

Latest dirty-mode product-path launch run:

- `script/run_final_launch_eval.sh --allow-dirty-machine --allow-placeholders`
- Summary: `eval_runs/final-launch-eval-20260709-185302/final_launch_eval_summary.md`
- Quality gate passed.
- Resident expanded first paste: useful `0.889`, meaning `0.928`, p50 `1.924s`, p95 `5.714s`.
- Short product rows p95 `1.993s`.
- Hindi+English first paste: useful `0.920`, meaning `0.932`, terms `0.986`, p50 `2.888s`, p95 `3.713s`.
- This is still not public latency evidence because dirty-machine mode was required.

Latest continuation rerun:

- Full final gate attempted at `eval_runs/final-launch-eval-20260709-193430`.
- It failed inside the quality gate because one long sentinel row hit `6.108s`, just over the `6.000s` p95 ceiling.
- Expanded resident eval after restoring missing WAVs: `eval_runs/resident-expanded-continuation-20260709-193809`.
- First paste: useful `0.872`, meaning `0.931`, p50 `2.540s`, p95 `8.452s`.
- Structured-if-unchanged: useful `0.868`, meaning `0.927`, p50 `2.544s`, p95 `8.456s`.
- Hindi+English under 60s: useful `0.906`, meaning `0.932`, terms `0.986`, p50 `3.595s`, p95 `4.763s`.
- Long English 60-120s: useful `0.814`, meaning `0.920`, terms `0.936`, p50 `7.538s`, p95 `12.974s`.
- Read: short/Hinglish quality holds, structure remains safe, long-dictation latency is not V0-ready under current machine load.

Latest restarted-machine attempt:

- Full final gate passed in explicit dirty-machine mode at `eval_runs/final-launch-eval-20260709-195158`.
- Strict machine health still failed first: stale `whisper-server` / `whisper-cli` jobs remain in `UEs` state, so latency is not public-claim-grade.
- Quality gate: p50 `2.540s`, p95 `3.814s`, max `5.312s`.
- Short product rows: p95 `2.712s`.
- Trusted Hindi+English: terms `1.000`, meaning `0.932`.
- Known failure replay: term coverage `1.000`, misses `0`.
- Structure safety: `86` accepted updates, `0` unsafe accepted rows.
- Expanded resident eval first paste: useful `0.872`, meaning `0.931`, p50 `2.609s`, p95 `9.006s`.
- Hindi+English under 60s: useful `0.906`, meaning `0.932`, terms `0.986`, p50 `3.853s`, p95 `5.088s`.
- Long English 60-120s: useful `0.814`, meaning `0.920`, terms `0.936`, p50 `8.043s`, p95 `13.833s`.
- Read: quality is launch-plausible, short/Hinglish remains the wedge, long dictation remains not V0 speed-ready.

Current package checksums:

```text
c3f2f7f89c49b27b1d286ebf7b871ee04c2b9b0b8e9d89d7275ee3f55868eafa  DictaHue-0.1.0.dmg
cf6d0d8bd2bd5c5f1482d0f34f5c27943f1e0ad4ec5d51d4f7ea024b4fc20518  DictaHue-0.1.0.zip
```

Current embedded package size:

- App: `1.5G`
- DMG: `889M`
- ZIP: `781M`

The package now includes a self-contained `RambleFixRuntime/.venv` and managed Python base under `RambleFixRuntime/python`. The release audits reject absolute symlinks and Python bytecode/cache files inside the sealed runtime.

## Historical Benchmark Evidence

Latest completed benchmark evidence is from `eval_runs/current-product-structure-20260709/` and `eval_runs/regression-quality-20260709-023240-focus-fix/`.

| Bucket | Current structured app | Best measured local baseline | Read |
| --- | ---: | ---: | --- |
| English public under 60s | 0.935 useful, p95 0.767s | OpenWhispr small 0.939 useful, p95 0.537s | At-par quality, slower |
| Indian/YouTube English under 60s | 0.795 useful, p95 0.940s | OpenWhispr small 0.757 useful, p95 0.766s | Better quality, slower |
| Hindi+English under 60s | 0.674 useful, terms 0.772, p95 0.705s | OpenWhispr base 0.455, small 0.339 | Clearly better |
| Long English 60-120s | 0.545 useful, p95 3.204s | OpenWhispr small 0.561, p95 2.400s | Not a launch claim |
| Long Hindi+English 60-120s | 0.641 useful, terms 0.783, p95 3.666s | OpenWhispr base 0.285, small 0.122 | Clearly better |
| Actual user existing 6 | 0.998 useful, terms 1.000, p95 1.546s | Wispr directional 0.706, OpenWhispr small 0.998 | Strong but small |

Structure safety in the last full gate:

- 81 accepted structure updates.
- 0 unsafe accepted rows.
- 0 protected-term drops.
- Known failure `end-to-end / split flow` replayed with no tracked term misses.

## Current Blocker

Fresh ASR benchmark reruns are not trustworthy until the stuck legacy processes are gone. The Mac was rebooted, but older app/eval code restarted legacy `8178` before the latest install. The current machine still has stale repo `whisper-cli` / `whisper-server` processes in `UE` state that survive `kill -9`. The strict gate fails early by design:

```bash
script/audit_eval_machine_health.sh --strict
script/regression_ramblefix_quality.sh
```

This is not a product-quality result. It is an eval-machine health failure. Current live app launch is Srota-only: `8188` is listening, `8178` is not listening, and latest app logs show `native_asr_server_ready`.

## Post-Reboot Eval Sequence

Run this before final public claims:

```bash
cd <repo>

script/run_final_launch_eval.sh --allow-placeholders
```

For the real public release, after links, Developer ID signing, and notarization are configured:

```bash
script/run_final_launch_eval.sh --public
```

Public launch still also needs:

- real GitHub/download/discussion/community links,
- Developer ID signing,
- notarization and stapling,
- one final strict public launch audit without `--allow-placeholders`.

## Signing Check

Current machine state:

- `security find-identity -p codesigning -v` shows only `RambleFix Local Dev`.
- No `Developer ID Application:` identity is installed.
- `xcrun notarytool history` fails because no notary credentials/profile are configured.
- `codesign -dvvv dist/release/DictaHue.app` shows `Signature=adhoc`.
- `spctl -a -vv dist/release/DictaHue.app` rejects the app.

So the current package is valid for local smoke only, not public distribution.
