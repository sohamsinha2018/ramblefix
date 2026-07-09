# DictaHue Launch Plan - 2026-07-08

## Scope

V0 is a free, local Mac dictation app:

- hold Fn or Control
- speak
- release
- local text is pasted into the focused app
- copy fallback appears only when paste is unavailable
- history keeps recoverable local transcripts
- English first; Hindi+English improves when detected
- no signup, no cloud product path, no meeting recorder in V0

## Packaging

Public artifact:

- app name: `DictaHue`
- bundle id: `app.dictahue.DictaHue`
- executable: `DictaHue`
- distribution: Developer ID signed and notarized DMG
- domain: `dictahue.app`

Build command after the Developer ID certificate and notary profile exist:

```bash
RAMBLEFIX_CODESIGN_IDENTITY="Developer ID Application: <name> (<TEAMID>)" \
RAMBLEFIX_PUBLIC_RELEASE=1 \
RAMBLEFIX_PACKAGE_EMBED_RUNTIME=1 \
RAMBLEFIX_PACKAGE_EMBED_VENV=1 \
RAMBLEFIX_NOTARIZE=1 \
RAMBLEFIX_NOTARY_PROFILE=dictahue-notary \
script/package_macos_release.sh
```

Validation:

```bash
script/audit_eval_machine_health.sh --strict
script/release_gate_ramblefix.sh
script/audit_macos_release_artifact.sh "dist/release/DictaHue.app"
script/audit_public_launch_readiness.sh --public
codesign --verify --deep --strict --verbose=2 "dist/release/DictaHue.app"
spctl -a -vv "dist/release/DictaHue.app"
xcrun stapler validate "dist/release/DictaHue-0.1.0.dmg"
spctl -a -vv -t open "dist/release/DictaHue-0.1.0.dmg"
```

## Local-Only Gate

The public runtime gate fails if the packaged runtime contains:

- non-loopback URLs
- cloud API key markers
- cloud API endpoint hostnames
- personal absolute paths from the dev machine

Command:

```bash
script/validate_public_runtime_local_only.sh "dist/release/DictaHue.app/Contents/Resources/RambleFixRuntime"
```

## Site

Static files live in `site/`.

Current state:

- Public repo: `https://github.com/sohamsinha2018/dictahue`
- GitHub Pages: `https://sohamsinha2018.github.io/dictahue/`
- GitHub Discussions: `https://github.com/sohamsinha2018/dictahue/discussions`
- Custom domain: pending. Keep `dictahue.app` off GitHub Pages until DNS moves from GoDaddy parking/site builder to GitHub Pages.

If links need to be regenerated, use:

```bash
DICTAHUE_DOWNLOAD_URL="https://github.com/sohamsinha2018/dictahue/releases/latest/download/DictaHue-0.1.0.dmg" \
DICTAHUE_GITHUB_URL="https://github.com/sohamsinha2018/dictahue" \
DICTAHUE_DISCUSSIONS_URL="https://github.com/sohamsinha2018/dictahue/discussions" \
script/configure_site_links.sh
```

DNS records for `dictahue.app`:

```text
A     @     185.199.108.153
A     @     185.199.109.153
A     @     185.199.110.153
A     @     185.199.111.153
CNAME www   sohamsinha2018.github.io
```

GitHub's custom-domain docs say DNS changes can take up to 24 hours to propagate.

After DNS resolves to GitHub Pages, set the custom domain in GitHub Pages to `dictahue.app`.

Recommended launch proof:

- short demo video
- benchmark table from `docs/benchmark_publish_readout_20260708.md`
- privacy promise: local by default, no signup
- visible GitHub stars and release downloads

## Current Blockers

- Developer ID Application certificate is not installed in Keychain yet.
- Notary profile `dictahue-notary` is not stored yet.
- Public GitHub release asset does not exist yet because the app is not signed/notarized.
- `script/audit_public_launch_readiness.sh --public` will intentionally fail until signing/notarization and clean-machine eval are real.
- Public one-click packaging must embed the Python runtime or ship a signed first-run bootstrap. `RAMBLEFIX_PUBLIC_RELEASE=1` now fails unless this is explicit.
- The current benchmark supports the Hindi+English/local-tool wedge, but the Wispr Flow comparison remains directional rather than a clean loopback same-WAV benchmark.
- This Mac currently has stale uninterruptible `whisper-cli` eval processes. Reboot before rerunning or publishing fresh latency benchmarks.
