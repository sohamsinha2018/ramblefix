# RambleFix

Free, local, open-source dictation for Mac.

RambleFix is a tiny menu-bar app for fast work dictation. Hold the hotkey, speak, release, and the text lands in the focused app. It is built for Indian English, work terms, and light Hinglish without sending your audio to the cloud.

## Why Use It

- Local by default: no account, no cloud transcription in the shipped product path.
- Fast English dictation for everyday work.
- Better handling of Indian English, acronyms, product names, and builder vocabulary over time.
- Automatic lightweight cleanup after paste: trims obvious filler, adds punctuation, and slightly structures text when safe.
- Light Hinglish support as a safe second pass when it helps.
- Copy fallback and transcript history when paste cannot be verified.

## V0 Scope

RambleFix V0 is dictation only:

- hold Fn or Control in any text box
- speak
- release
- local speech-to-text runs
- text is pasted into the focused app
- if paste is unsafe or no text box is focused, RambleFix shows a copy fallback

No signup. No meeting recorder. No screen recording permission.

## Permissions

RambleFix V0 asks only for the permissions needed for dictation:

- Microphone: record your voice locally.
- Accessibility / Input Monitoring: listen for the hotkey and paste text into the focused app.

## Install

Public installs should use the signed and notarized DMG linked from `https://ramblefix.app/`. If the public download buttons are not live, the one-click installer is not ready.

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
RAMBLEFIX_NOTARY_PROFILE=ramblefix-notary \
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

Security and release checklist: [docs/org_security_approval_and_mac_release_checklist.md](docs/org_security_approval_and_mac_release_checklist.md)

## Built With Builders

RambleFix was shaped through a public Builderr.ai speech-to-text challenge: keep English fast and accurate while adding useful Hindi + English, all locally.

- Challenge and final results: [builderr.ai/speech-to-text](https://builderr.ai/speech-to-text)
- Starter/reference challenge repo: [builderr-ai/builderr-speech-to-text](https://github.com/builderr-ai/builderr-speech-to-text)
- Sponsor: [Amit Kumar](https://in.linkedin.com/in/urbansanyasi)

Public submissions and profiles worth studying:

| Builder | Builderr profile | Public submission |
| --- | --- | --- |
| Sankeerth | [builderr.ai/builders/sankeerth](https://www.builderr.ai/builders/sankeerth) | [San245o/builderr-speech-to-text](https://github.com/San245o/builderr-speech-to-text) |
| Arnav Chauhan | [builderr.ai/builders/arnav](https://www.builderr.ai/builders/arnav) | [arnav-chauhan-kgpian/hindi-english-raaaa](https://github.com/arnav-chauhan-kgpian/hindi-english-raaaa) |
| Vishwas | [builderr.ai/builders/vishwas](https://www.builderr.ai/builders/vishwas) | [vishwasvoc/builderr-speech-to-text](https://github.com/vishwasvoc/builderr-speech-to-text) |
| Rishchith | [builderr.ai/builders/rishchith](https://www.builderr.ai/builders/rishchith) | [Rishchith/builderr-speech-to-text](https://github.com/Rishchith/builderr-speech-to-text) |
| Sham | [builderr.ai/builders/sham](https://www.builderr.ai/builders/sham) | [sham-1912/builderr-speech-to-text](https://github.com/sham-1912/builderr-speech-to-text) |
| Harsimran | [builderr.ai/builders/harsimran](https://www.builderr.ai/builders/harsimran) | [Harsimran-Dalal/speech-to-text](https://github.com/Harsimran-Dalal/speech-to-text) |
| Meet | [builderr.ai/builders/meet](https://www.builderr.ai/builders/meet) | [meet252501/speech-rebo](https://github.com/meet252501/speech-rebo) |

Builder profiles without a live public STT repo link verified in this audit: [Darshan](https://www.builderr.ai/builders/darshan), [Vishal](https://www.builderr.ai/builders/vishal).

If you want to add a new language route, fork RambleFix, open a PR, and add your Builderr profile or public experiment here.

## License

RambleFix source code is MIT licensed. Public release artifacts that embed third-party models or native binaries must include their third-party notices before release.

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
