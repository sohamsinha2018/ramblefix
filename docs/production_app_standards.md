# Production App Standards

## Product Promise

RambleFix is local-first dictation. Production quality means the app is easy to install, asks for minimal permissions, fails recoverably, and gives the user control over diagnostics.

## Permissions

V0 dictation permissions:

- Microphone
- Accessibility / Input Monitoring

Do not request Screen Recording, Contacts, Calendar, Files, or network permissions for V0 dictation.

Future meeting/system-audio features must be feature-gated, separately documented, and never bundled into the V0 permission story.

## Logging

Keep logs local by default.

Required log fields for dictation:

- app version
- run id
- mode
- route/backend
- audio duration
- ASR wall time
- release-to-paste time
- paste success or copy fallback
- blank/no-speech flag
- target app name and bundle id
- system pressure/thermal snapshot

Never log secrets. Do not include raw audio or raw transcripts in exported diagnostics unless the user explicitly chooses to include them.

## Automatic Cleanup

V0 may run one lightweight local cleanup pass after the fast paste.

Allowed:

- trim obvious leading filler
- normalize spacing and capitalization
- add punctuation
- split simple ramble connectors like "and then" when no content is dropped

Not allowed:

- cloud rewrite in the product path
- user-facing mode picker in V0
- replacing text if the original paste is no longer present
- dropping product terms, names, numbers, acronyms, or domain words
- changing Hindi/Hinglish meaning just to make text sound smoother

## Diagnostics

The app should provide a user-controlled Export Diagnostics action.

Default diagnostics bundle:

- app version and build
- OS version
- local model/backend health
- timing summary
- recent native event metadata
- crash/error metadata when available

Excluded by default:

- raw audio
- raw transcript text
- clipboard contents
- API keys
- full filesystem paths beyond the app/runtime paths needed for debugging

## Security

Repository gates:

- dependency scanning
- secret scanning
- CodeQL or equivalent static analysis
- branch protection before public release
- signed release artifacts
- release checksum

Runtime rules:

- loopback-only local servers
- no cloud product path
- no auto-upload of diagnostics
- no hidden benchmark phrase hacks
- background learning skips under load and stays off the dictation hot path

## Release Gates

Before a public Mac release:

```bash
script/validate_v0_release_scope.sh
script/regression_ramblefix_hotkey.sh
script/package_macos_release.sh
```

`RAMBLEFIX_PUBLIC_RELEASE=1` must require Developer ID signing and notarization.
