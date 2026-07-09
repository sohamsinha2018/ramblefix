# DictaHue Public Launch Audit - 2026-07-09

## Proven

- Quality regression gate passed earlier on the sentinel and known-failure corpus in `eval_runs/regression-quality-20260709-023240-focus-fix`.
- Latest ASR quality rerun passed only in explicit dirty mode: `eval_runs/final-launch-eval-20260709-195158`.
- Strict ASR latency gating is still blocked until stale `whisper-cli` / `whisper-server` processes are gone.
- Native hotkey regression gate passes.
- Current app installs and launches locally.
- Packaged runtime is local-only.
- Packaged runtime includes an executable Srota launcher script.
- Packaged runtime embeds a self-contained Python/MLX runtime for one-click local smoke.
- Packaged runtime strips local learned memory and approved phrase fixes.
- Packaged runtime has no absolute symlinks and no Python bytecode/cache files after packaging.
- Native app now guards against accidentally autostarting legacy whisper.cpp for the Srota `8188` endpoint.
- Native app and Srota launcher set `PYTHONDONTWRITEBYTECODE=1`, so Python imports should not mutate the signed bundle at runtime.
- V0 bundle does not declare Screen Recording permission.
- V0 binary does not link ScreenCaptureKit.
- Static site renders without external analytics or signup.
- Public GitHub repo exists: `https://github.com/sohamsinha2018/dictahue`.
- GitHub Discussions is enabled for feedback.
- GitHub Pages workflow deploys the static site successfully.
- GitHub Pages URL is live: `https://sohamsinha2018.github.io/dictahue/`.
- GitHub Pages custom domain is not active yet because `dictahue.app` DNS still points to GoDaddy.
- Public benchmark claim numbers are machine-checked against scorecard JSON.
- Latency-sensitive evals now refuse dirty machine state unless explicitly overridden.
- Current structure-only safety rerun passed: 187 rows, 86 accepted updates, 0 unsafe accepted rows.

## Evidence Commands

```bash
script/run_final_launch_eval.sh --allow-placeholders
script/regression_ramblefix_hotkey.sh
script/regression_ramblefix_quality.sh
script/audit_eval_machine_health.sh --strict
RAMBLEFIX_PACKAGE_EMBED_VENV=1 script/package_macos_release.sh
script/audit_macos_release_artifact.sh dist/release/DictaHue.app
scripts/audit_benchmark_claims.py
script/audit_release_checksums.sh dist/release/DictaHue-0.1.0.SHA256SUMS
script/audit_public_launch_readiness.sh --allow-placeholders
script/smoke_site_visual.sh
gh repo view sohamsinha2018/dictahue --json hasDiscussionsEnabled,url,homepageUrl
gh api repos/sohamsinha2018/dictahue/pages
```

## Still Blocked For Public Launch

- Strict ASR eval is required before using today's package as final benchmark evidence.
- Developer ID Application certificate is not available in this environment.
- Notary profile is not configured.
- The DMG is not notarized/stapled.
- GitHub release asset is not published yet because the DMG still needs Developer ID signing and notarization.
- `dictahue.app` DNS still points to GoDaddy, not GitHub Pages. Update DNS, then add the custom domain back in GitHub Pages:
  - `A @ 185.199.108.153`
  - `A @ 185.199.109.153`
  - `A @ 185.199.110.153`
  - `A @ 185.199.111.153`
  - optional `CNAME www sohamsinha2018.github.io`
- Wispr Flow comparison is still directional, not a same-WAV public benchmark.

## Release Decision

Local smoke build: acceptable.

Public launch: not ready until Developer ID signing, notarization, checksum-published GitHub release, DNS cutover, custom-domain re-enable, and a strict clean-machine latency eval are done.
