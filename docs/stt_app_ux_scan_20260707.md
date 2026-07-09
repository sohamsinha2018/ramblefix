# STT App UX Scan - 2026-07-07

## Sources Checked

- Wispr Flow official site: https://wisprflow.ai/
- Wispr Flow media kit: https://wisprflow.ai/media-kit
- Wispr Flow what's new / Scratchpad: https://wisprflow.ai/whats-new
- Wispr Flow Android floating bubble coverage: https://hothardware.com/news/wispr-flow-ai-dictation-app-android
- Wispr Flow review screenshots and feature notes: https://droidcrunch.com/wispr-flow-review/
- Wispr Flow vs Superwhisper comparison: https://clickup.com/blog/wispr-flow-vs-superwhisper/
- Superwhisper official site: https://superwhisper.com/
- Superwhisper screenshots/writeup: https://todayonmac.com/superwhisper/
- Whisper Notes Fn dictation article: https://whispernotes.app/blog/mac-system-wide-dictation-whisper
- Aqua official site: https://aquavoice.com/
- Aqua YC profile: https://www.ycombinator.com/companies/aqua-voice

## What The Best Apps Converge On

1. Active capture UI is tiny.
   - Wispr-style capture is a small floating control, not a full transcript box.
   - Android screenshots show a compact bubble with cancel / waveform / confirm controls.
   - User should know recording is active without being pulled out of the app.

2. The product moment is after release.
   - Hold key, speak, release, clean text appears.
   - While speaking, show waveform / listening state, not live text by default.
   - Processing can show a small working state, then disappear once paste succeeds.

3. History matters.
   - Wispr shows recent activity.
   - Superwhisper has recording history.
   - RambleFix needs local transcript history because paste can fail when focus is wrong.

4. Copy fallback is core, not edge-case UI.
   - If no focused text box is available, keep the transcript in a small toast with Copy.
   - Do not pretend paste worked.

5. Personal dictionary / learning is table stakes.
   - Wispr highlights automatic personal dictionary and snippets.
   - Superwhisper exposes replacements and vocabulary.
   - Whisper Notes exposes custom vocabulary.
   - RambleFix should keep lightweight local learning, but not block the hot path.

6. Auto-polish is valuable if it is conservative.
   - Wispr's value prop is polished writing, filler removal, punctuation, and formatting.
   - The RambleFix version should be light: punctuation, repeated filler cleanup, small structure.
   - Never drop terms, numbers, negation, or mixed-language meaning.

7. Privacy is the wedge.
   - Several reviews call out Wispr's cloud/context tradeoff.
   - RambleFix should not request screen recording in V0 and should not send product audio/text to cloud.
   - Diagnostics should be local by default and user-approved if shared.

## RambleFix UX Decisions

- Keep capture HUD wave-only with no background.
- Keep processing HUD small; use color/system pressure signals only if cheap.
- Use translucent/glass toast only for text, errors, copy fallback, and diagnostics states.
- Add menu/history actions, not intrusive rating prompts.
- Add one-click local "Mark Latest Transcript Bad" for feedback.
- Keep stable/canary split so UX experiments do not overwrite the working dictation app.

## Release Gate Implications

Before a stable build, verify:

- hotkey capture starts and stops
- no `[BLANK_AUDIO]` paste
- copy fallback appears when paste is unsafe
- history contains the transcript and timings
- known long-clip truncation route runs rescue before paste
- friendly rewrite does not drop terms or mixed-language meaning
- no Screen Recording permission is declared in V0
