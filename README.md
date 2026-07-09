# DictaHue

Free, local dictation for Mac.

DictaHue is a tiny menu-bar app for fast work dictation. Hold the hotkey, speak, release, and the text lands in the focused app. It is built for Indian English, work terms, and light Hinglish without sending your audio to the cloud.

## Why Use It

- Local by default: no account, no cloud product path.
- Fast English dictation for everyday work.
- Better handling of Indian English, acronyms, product names, and builder vocabulary over time.
- Automatic lightweight cleanup after paste: trims obvious filler, adds punctuation, and slightly structures text when safe.
- Light Hinglish support as a safe second pass when it helps.
- Copy fallback and transcript history when paste cannot be verified.

## V0 Scope

DictaHue V0 is dictation only:

- hold Fn or Control in any text box
- speak
- release
- local speech-to-text runs
- text is pasted into the focused app
- if paste is unsafe or no text box is focused, RambleFix shows a copy fallback

No signup. No meeting recorder. No screen recording permission.

## Permissions

DictaHue V0 asks only for the permissions needed for dictation:

- Microphone: record your voice locally.
- Accessibility / Input Monitoring: listen for the hotkey and paste text into the focused app.

## Install

Public installs should use a signed and notarized DMG from GitHub Releases.

Local developer install:

```bash
script/install_ramblefix_app.sh
```

Package a public Mac release:

```bash
RAMBLEFIX_CODESIGN_IDENTITY="Developer ID Application: <Team>" \
RAMBLEFIX_PUBLIC_RELEASE=1 \
RAMBLEFIX_PACKAGE_EMBED_RUNTIME=1 \
RAMBLEFIX_PACKAGE_EMBED_VENV=1 \
RAMBLEFIX_NOTARIZE=1 \
RAMBLEFIX_NOTARY_PROFILE=dictahue-notary \
script/package_macos_release.sh
```

Before public launch:

```bash
script/report_public_launch_blockers.sh
```

Run the V0 release scope gate:

```bash
script/validate_v0_release_scope.sh
```

## Quality Bar

The launch bar is simple:

- English p95 release-to-paste under 2 seconds on the checked local corpus.
- Zero pasted `[BLANK_AUDIO]`.
- Paste success or copy fallback is always logged.
- Lightweight cleanup may update the pasted text only when the original text is still present.
- Hinglish can improve output, but must not block or regress English.
- All runtime transcription stays local.

Current release audit: [docs/release_readiness_audit_20260630.md](docs/release_readiness_audit_20260630.md)

## Development

Set up Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the native hotkey regression:

```bash
script/regression_ramblefix_hotkey.sh
```

Build the Mac app:

```bash
script/build_macos_app.sh
```

Run the Streamlit lab UI:

```bash
streamlit run app.py
```

The lab UI is for development and corpus work. The public product is the native menu-bar app.
