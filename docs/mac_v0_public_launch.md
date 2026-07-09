# DictaHue Mac V0 Public Launch

## Goal

Drive adoption and GitHub proof with a small polished Mac app, not a broad productivity suite.

Public positioning:

> Free, local dictation for Mac. Fast English, built for Indian accents and light Hinglish.

## Adoption Strategy

Use two surfaces:

- GitHub for proof: stars, issues, release download counts, transparent benchmarks, public roadmap.
- Signed DMG for adoption: non-technical users should download and run without cloning or building.

## V0 Product

V0 is only dictation:

- menu-bar app
- hold Fn or Control
- speak
- release
- fast local transcript appears in the focused app
- copy fallback if paste target is missing
- transcript history for recovery
- automatic lightweight cleanup after paste when it is safe
- local logs and user-controlled diagnostics

Explicitly out of V0:

- meeting mode
- system audio capture
- screen recording permission
- signup
- cloud transcription in the product path

## Public Readiness Bar

- Developer ID signed and notarized DMG.
- Public repo staged only through `script/stage_public_source.sh --apply`; never use `git add .`.
- README says exactly what the app does in the first viewport.
- Install path works for a non-technical Mac user.
- Permissions match the dictation promise: Microphone plus Accessibility/Input Monitoring only.
- English dictation is measured on a fixed corpus before release.
- Cleanup is automatic, conservative, local, and must not drop terms or overwrite after the user has submitted/edited the original paste.
- Hinglish is positioned as improving, not fully solved.
- Every failure leaves a local log row and recoverable transcript/audio when retention is enabled.

## Proof To Show Publicly

- short demo video or GIF
- p50/p95 release-to-paste latency
- English term examples
- Hinglish examples where the safe second pass helps
- privacy statement: local by default, no signup
- release download count
- GitHub stars

## Public Repo Staging

Run this before creating the first public commit:

```bash
script/report_public_launch_blockers.sh
script/stage_public_source.sh --dry-run
```

This runs the public source-surface audit and lists the exact files that are safe to publish. It excludes local models, packaged apps, recordings, retained audio, eval runs, personal learned memory, phrase fixes, and generated binaries.

Only after reviewing the dry run:

```bash
script/stage_public_source.sh --apply
git commit -m "Launch DictaHue Mac V0"
git remote add origin git@github.com:<owner>/<repo>.git
git push -u origin main
```

Then configure the site links:

```bash
DICTAHUE_DOWNLOAD_URL="https://github.com/<owner>/<repo>/releases/download/v0.1.0/DictaHue-0.1.0.dmg" \
DICTAHUE_GITHUB_URL="https://github.com/<owner>/<repo>" \
DICTAHUE_DISCUSSIONS_URL="https://github.com/<owner>/<repo>/discussions" \
script/configure_site_links.sh
```

`DICTAHUE_DISCORD_URL` is optional. If it is omitted, the secondary feedback button points to GitHub Discussions.

## Windows Strategy

Do not include Windows in V0 stable. Keep the repo cross-platform in intent:

- Mac stable first
- Windows preview later
- Windows stack: WinUI 3 + Windows App SDK + native hotkey/mic/text insertion APIs
- no Electron unless the native path becomes blocked

The Windows preview should ship only after it passes install, hotkey, paste, mic, local model, and security checks on multiple Windows machines.
