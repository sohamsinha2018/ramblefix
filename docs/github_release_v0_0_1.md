# DictaHue v0.1.0 Release Notes

DictaHue is a free local Mac dictation app.

## What It Does

- Hold Fn or Control, speak, release.
- Pastes into the focused app.
- Runs the product dictation path locally.
- Handles English, Indian English, work terms, and light Hindi+English.
- Keeps structure updates conservative: it only replaces text when the original pasted text is still present.

## Privacy

- No signup.
- No cloud product path.
- No screen recording permission in V0.
- Diagnostics and retained audio stay local unless the user chooses to share them.

## Install

1. Download `DictaHue-0.1.0.dmg`.
2. Drag `DictaHue` to Applications.
3. Grant Microphone, Accessibility, and Input Monitoring when macOS asks.
4. Hold Fn or Control to dictate. Fallback hotkey: Control-Option-Space.

## Current Benchmark Boundary

Same-WAV local benchmark from `docs/current_regression_readout_20260709.md`:

- Short clean English: score `0.935`, p95 `0.767s`.
- Short Hindi+English: score `0.674`, terms `0.772`, p95 `0.705s`.
- Long Hindi+English, 60-120s: score `0.641`, terms `0.783`, p95 `3.666s`.

Do not claim long English as best-in-class. The current long-English result is at-par/behind the strongest measured local baseline.

## Known Limits

- Hindi+English is useful and ahead of measured local baselines, but not solved.
- A few acoustic/model confusions still need user learning or phrase repair.
- Wispr Flow comparison is directional unless rerun as a clean same-WAV benchmark.

## Integrity

Publish `DictaHue-0.1.0.SHA256SUMS` beside the DMG and ZIP. Users can verify with:

```bash
shasum -a 256 -c DictaHue-0.1.0.SHA256SUMS
```
