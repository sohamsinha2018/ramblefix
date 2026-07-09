import Foundation
import RambleFixHotkeyCore

func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        FileHandle.standardError.write(Data("FAIL: \(message)\n".utf8))
        exit(1)
    }
}

func expectPaste(_ role: String?, selectedRange: Bool = false, valueSettable: Bool = false, _ message: String) {
    expect(
        PasteFocusHeuristics.shouldAttemptPaste(
            role: role,
            selectedRangeAvailable: selectedRange,
            valueIsSettable: valueSettable
        ),
        message
    )
}

func expectCopyFallback(_ role: String, _ message: String) {
    expect(
        !PasteFocusHeuristics.shouldAttemptPaste(
            role: role,
            selectedRangeAvailable: false,
            valueIsSettable: false
        ),
        message
    )
}

expectPaste("AXButton", selectedRange: true, "editable selected range must override control role")
expectPaste("AXWindow", valueSettable: true, "settable value must override chrome role")
expectPaste("AXWebArea", "Cursor/Electron web editor surfaces must stay on paste path")
expectPaste("AXScrollArea", "scroll-backed editors must stay on paste path")
expectPaste("AXStaticText", "ambiguous static text inside editors must stay on paste path")
expectPaste(nil, "unknown focused role must be permissive")
expect(
    PasteFocusHeuristics.confidence(role: "AXTextArea", selectedRangeAvailable: true, valueIsSettable: false) == .editable,
    "selected range should classify target as editable"
)
expect(
    PasteFocusHeuristics.confidence(role: "AXWebArea", selectedRangeAvailable: false, valueIsSettable: false) == .ambiguous,
    "web-backed editors should classify as ambiguous, not blocked"
)
expect(
    PasteFocusHeuristics.shouldOfferCopyFallback(confidence: .ambiguous, verification: .unverified),
    "ambiguous unverified paste must show copy fallback"
)
expect(
    PasteFocusHeuristics.shouldOfferCopyFallback(confidence: .editable, verification: .failed),
    "failed editable paste must show copy fallback"
)
expect(
    !PasteFocusHeuristics.shouldOfferCopyFallback(confidence: .editable, verification: .unverified),
    "unverified editable paste should be trusted after long-text verification wait"
)
expect(
    !PasteFocusHeuristics.shouldOfferCopyFallback(confidence: .ambiguous, verification: .unverified, targetBundleID: "com.openai.codex"),
    "Codex web-editor paste is often unverified by Accessibility but should not show false copy fallback"
)
expect(
    PasteFocusHeuristics.trustsUnverifiedPaste(targetBundleID: "com.openai.codex"),
    "Codex web-editor should use the fast trusted-unverified paste path"
)
expect(
    !PasteFocusHeuristics.trustsUnverifiedPaste(targetBundleID: "com.apple.Preview"),
    "generic apps must not use the trusted-unverified paste path"
)
expect(
    PasteFocusHeuristics.shouldOfferCopyFallback(confidence: .ambiguous, verification: .unverified, targetBundleID: "com.apple.Preview"),
    "generic ambiguous targets should still show copy fallback"
)
expect(
    !PasteFocusHeuristics.shouldOfferCopyFallback(confidence: .ambiguous, verification: .verified),
    "verified paste should not show copy fallback"
)
expect(
    !TranscriptQualityPolicy.isDegenerateTranscript("There is no clear this by looking at the leaderboard etc should be clear that for each competition what is the end date and maybe some call to action takes you to the measurement criteria. Right that way it is very clear. The third is some score right so that even if somebody loses and we keep adding scores right because there is no platform and some some some text repeats naturally."),
    "natural long ramble with a small local repetition must not be blocked as degenerate"
)
expect(
    TranscriptQualityPolicy.isDegenerateTranscript("I'm I'm I'm I'm I'm I'm I'm I'm I'm I'm I'm I'm I'm I'm I'm I'm"),
    "dominant repeated token loops must still be blocked"
)
let approvedPhraseFix = ApprovedPhraseFixPolicy.apply(
    text: "What is the end-of-the-long safe replacement? It cannot split floor.",
    fixes: [
        ApprovedPhraseFixEntry(source: "end-of-the-long safe replacement", replacement: "end-to-end safe replacement"),
        ApprovedPhraseFixEntry(source: "split floor", replacement: "split flow")
    ]
)
expect(
    approvedPhraseFix.text == "What is the end-to-end safe replacement? It cannot split flow.",
    "approved phrase fixes must repair retained user term failures before first paste"
)
expect(approvedPhraseFix.changed, "approved phrase fixes should report changed output")
do {
    _ = try LocalWhisperServerClient.transcribe(
        audioURL: URL(fileURLWithPath: "/tmp/ramblefix-nonexistent.wav"),
        endpoint: URL(string: "https://example.com/inference")!,
        timeout: 0.1
    )
    expect(false, "native ASR client must reject non-loopback endpoints")
} catch let error as LocalWhisperServerError {
    expect(String(describing: error) == "whisper.cpp server endpoint must be loopback", "native ASR client should reject remote endpoints before any request")
} catch {
    expect(false, "native ASR client should fail with nonLoopbackEndpoint for remote endpoints, got \(error)")
}

for role in ["AXButton", "AXCheckBox", "AXImage", "AXMenuBar", "AXMenuItem", "AXPopUpButton", "AXRadioButton", "AXToolbar", "AXWindow"] {
    expectCopyFallback(role, "obvious non-editable role should use copy fallback: \(role)")
}

expect(RetainedAudioPolicy.normalizedLimit(from: nil, defaultValue: 25) == 25, "missing retain limit should use default")
expect(RetainedAudioPolicy.normalizedLimit(from: "3", defaultValue: 25) == 3, "valid retain limit should be honored")
expect(RetainedAudioPolicy.normalizedLimit(from: "0", defaultValue: 25) == 25, "zero retain limit should fall back to default")
expect(RetainedAudioPolicy.normalizedLimit(from: "bad", defaultValue: 25) == 25, "invalid retain limit should fall back to default")
expect(CaptureEvalAudioPolicy.isEnabled(storedValue: nil), "missing capture preference should retain eval audio by default")
expect(CaptureEvalAudioPolicy.isEnabled(storedValue: true), "true capture preference should retain eval audio")
expect(!CaptureEvalAudioPolicy.isEnabled(storedValue: false), "false capture preference should disable eval audio")
expect(CaptureEvalAudioPolicy.isEnabled(storedValue: "on"), "string on capture preference should retain eval audio")
expect(!CaptureEvalAudioPolicy.isEnabled(storedValue: "off"), "string off capture preference should disable eval audio")
expect(!HistoryMenuPolicy.copyLatestEnabled(memoryTranscript: nil, cachedHistoryTranscript: nil), "copy latest should be disabled without memory or cached history")
expect(!HistoryMenuPolicy.copyLatestEnabled(memoryTranscript: "  ", cachedHistoryTranscript: "\n"), "copy latest should ignore blank memory and blank cached history")
expect(HistoryMenuPolicy.copyLatestEnabled(memoryTranscript: "latest run", cachedHistoryTranscript: nil), "copy latest should enable from in-memory latest transcript")
expect(HistoryMenuPolicy.copyLatestEnabled(memoryTranscript: nil, cachedHistoryTranscript: "cached history"), "copy latest should enable from cached history without forcing a disk read")
expect(SlowProcessingFeedbackPolicy.defaultDelaySeconds == 5.0, "slow processing feedback should default to 5 seconds")
let slowFeedbackStartedAt = Date(timeIntervalSince1970: 1_000)
expect(!SlowProcessingFeedbackPolicy.shouldShowFeedback(startedAt: slowFeedbackStartedAt, now: slowFeedbackStartedAt.addingTimeInterval(4.99)), "slow processing feedback should not show before threshold")
expect(SlowProcessingFeedbackPolicy.shouldShowFeedback(startedAt: slowFeedbackStartedAt, now: slowFeedbackStartedAt.addingTimeInterval(5.0)), "slow processing feedback should show at threshold")
expect(HUDSignalStylePolicy.englishMotionVariant == 0, "English processing should use the stable single-lane wave variant")
expect(HUDSignalStylePolicy.normalMotionVariantCount == 4, "V0 recording HUD should stay limited to cheap mic visualizer variants")
expect(HUDSignalStylePolicy.recordingSquiggleVariant == 0, "recording HUD should surface the thin squiggle variant first, then rotate through other variants")
expect(HUDSignalStylePolicy.motionVariantCount == 9, "HUD work animation should include neutral and Hindi variants only for the MVP")
expect(HUDSignalStylePolicy.hindiMotionVariant != HUDSignalStylePolicy.englishMotionVariant, "Hindi+English processing HUD must stay visually distinct from English processing")
expect(HUDSignalStylePolicy.englishProcessingLaneCount == 1, "English processing should remain a single thinking lane")
expect(HUDSignalStylePolicy.hindiProcessingLaneCount == 2, "Hindi+English processing should remain visibly dual-lane")
expect(HUDSignalStylePolicy.adaptiveSignalContrastEnabled, "visual-only waves should use adaptive contrast without drawing a background")
expect(HUDSignalStylePolicy.signalShadowAlpha <= 0.40, "adaptive contrast should stay subtle and not become a visible pill")
expect(HUDSignalStylePolicy.signalShadowBlurRadius <= 4.0, "adaptive contrast should stay cheap to draw")
expect(HUDSignalStylePolicy.loadedSystemLoadRatio == 0.75, "HUD should warn when per-core system load is high")
expect(HUDSignalStylePolicy.busySystemLoadRatio == 1.15, "HUD should switch to danger when per-core system load is very high")
expect(HUDSignalStylePolicy.healthyHueBase == 0.52, "healthy HUD color should stay in the cyan family")
expect(HUDSignalStylePolicy.warningHueBase == 0.12, "warm/loaded HUD color should stay in the amber family")
expect(HUDSignalStylePolicy.dangerHueBase == 0.98, "hot/busy HUD color should stay in the red-magenta family")
expect(HUDSignalStylePolicy.isVisualOnlyState("REC"), "recording HUD should be visual-only")
expect(HUDSignalStylePolicy.isVisualOnlyState("WORK"), "processing HUD should be visual-only")
expect(!HUDSignalStylePolicy.usesGlassBackground(state: "REC"), "recording wave must not draw a glass background")
expect(!HUDSignalStylePolicy.usesGlassBackground(state: "WORK"), "processing wave must not draw a glass background")
expect(HUDSignalStylePolicy.usesGlassBackground(state: "COPY"), "copy fallback HUD should keep the glass background")
expect(HUDSignalStylePolicy.usesGlassBackground(state: "FAIL"), "error HUD should keep the glass background")
expect(abs(HUDSignalStylePolicy.audioWaveVisualWidth - 80.8) < 0.001, "HUD visual width should match the speaking wave width")
expect(HUDSignalStylePolicy.workPrimaryStrokeWidth == HUDSignalStylePolicy.audioBarWidth, "HUD working stroke should match speaking bar thickness")
expect(HUDSignalStylePolicy.visualOnlyPillWidth == 96.0, "visual-only HUD pill should stay compact like Wispr-style capture feedback")
expect(HUDSignalStylePolicy.visualOnlyPillHeight == 26.0, "visual-only HUD pill should stay slim")
expect(HUDSignalStylePolicy.statusPillWidth == 236.0, "status/error toast should stay tighter than the old large pill")
expect(HUDSignalStylePolicy.copyPillWidth == 284.0, "copy fallback toast should stay compact while leaving room for preview and action")
expect(HUDSignalStylePolicy.statusPillWidth < HUDSignalStylePolicy.copyPillWidth, "copy fallback pill should leave room for transcript preview and action")
expect(HUDSignalStylePolicy.textPillHeight == 30.0, "text/error HUD should stay a slim translucent toast")
expect(HUDSignalStylePolicy.toastHorizontalPadding == 14.0, "toast text should use tight but readable horizontal padding")
expect(HUDSignalStylePolicy.toastActionWidth == 48.0, "copy action should stay compact and right aligned")
expect(HUDSignalStylePolicy.toastTextFontSize == 11.5, "toast font should stay compact but legible")
expect(
    !HindiPolishPolicy.shouldCheckDraft(
        draft: "Also I just noticed that every time I am speaking in English it shows the Hindi pattern.",
        qualityHindiRisk: false,
        audioRiskAll: false
    ),
    "plain English draft must not be treated as confirmed Hindi for the HUD"
)
expect(
    HindiPolishPolicy.shouldProbeAudioRisk(
        draft: "Also I just noticed that every time I am speaking in English it shows the Hindi pattern.",
        route: "fast_server_native",
        audioSeconds: 8.0,
        audioRiskDetector: true,
        maxAudioSeconds: 90.0
    ),
    "plain English can still run a hidden speculative Hindi-risk probe"
)
expect(!WhisperSidecarPolicy.defaultAutostartEnabled, "native app should not autostart whisper.cpp sidecar by default")
expect(
    !WhisperSidecarPolicy.shouldAutostartLegacySidecar(endpointPort: 8188, autostartEnabled: true),
    "native app must never autostart legacy whisper.cpp for the Srota 8188 endpoint"
)
expect(
    WhisperSidecarPolicy.shouldAutostartLegacySidecar(endpointPort: 8178, autostartEnabled: true),
    "legacy whisper.cpp autostart remains available only for explicit non-Srota debugging"
)
expect(WhisperSidecarPolicy.defaultStartupWaitSeconds == 4.0, "pre-transcribe sidecar warm wait should stay short")
let sidecarAttempt = Date(timeIntervalSince1970: 1_000)
expect(
    WhisperSidecarPolicy.shouldThrottleStartAttempt(lastAttemptAt: sidecarAttempt, now: sidecarAttempt.addingTimeInterval(4.0)),
    "sidecar autostart should throttle duplicate launch attempts"
)
expect(
    !WhisperSidecarPolicy.shouldThrottleStartAttempt(lastAttemptAt: sidecarAttempt, now: sidecarAttempt.addingTimeInterval(20.0)),
    "sidecar autostart should allow retry after cooldown"
)
expect(RecordingDurationPolicy.defaultDictationMaxSeconds == 900, "dictation max should default to 15 minutes")
expect(RecordingDurationPolicy.defaultMeetingMaxSeconds == 7200, "meeting max should default to 2 hours")
expect(RecordingDurationPolicy.normalizedMaxSeconds(from: nil, defaultSeconds: 900) == 900, "missing max duration should use default")
expect(RecordingDurationPolicy.normalizedMaxSeconds(from: "120", defaultSeconds: 900) == 120, "valid max duration override should be honored")
expect(RecordingDurationPolicy.normalizedMaxSeconds(from: "0", defaultSeconds: 900) == 900, "zero max duration should fall back to default")
expect(RecordingDurationPolicy.normalizedMaxSeconds(from: "bad", defaultSeconds: 900) == 900, "invalid max duration should fall back to default")
let combinedMeetingText = MeetingTranscriptCombiner.combinedText(sections: [
    MeetingTranscriptSection(label: "Meeting audio", text: "The company uses Teams for the vendor call."),
    MeetingTranscriptSection(label: "My mic", text: "I asked about SOC2 and Hindi support.")
])
expect(combinedMeetingText.contains("[Meeting audio]"), "meeting transcript must keep the system-audio section")
expect(combinedMeetingText.contains("[My mic]"), "meeting transcript must keep the mic section")
expect(combinedMeetingText.contains("SOC2"), "meeting transcript combiner must preserve terms from mic text")
expect(!StreamingCaptureDefaults.dictationEnabled, "dictation should default to stable AVAudioRecorder capture; streaming capture is opt-in")
expect(StreamingCaptureDefaults.silenceChunkingEnabled, "streaming capture should use silence-aware chunks by default")
expect(StreamingCaptureDefaults.targetChunkSeconds == 4.0, "streaming target should use measured 4s Hindi chunk policy")
expect(StreamingCaptureDefaults.minChunkSeconds == 2.0, "streaming min should use measured 2s Hindi chunk policy")
expect(StreamingCaptureDefaults.maxChunkSeconds == 5.0, "streaming max should use measured 5s Hindi chunk policy")
expect(StreamingCaptureDefaults.silenceLookaroundSeconds == 1.5, "streaming lookaround should use measured 1.5s window")
expect(StreamingCaptureDefaults.silenceLevelThreshold == 0.08, "streaming silence threshold should match measured policy")
expect(
    !StreamingChunkPolicy.shouldCloseChunk(
        elapsedSeconds: 3.9,
        targetSeconds: 4.0,
        silenceChunkingEnabled: false,
        minSeconds: 4.0,
        maxSeconds: 4.0,
        silenceLookaroundSeconds: 1.5,
        normalizedLevel: 0.01,
        silenceLevelThreshold: 0.08
    ),
    "fixed chunking should not close before target"
)
expect(
    StreamingChunkPolicy.shouldCloseChunk(
        elapsedSeconds: 4.0,
        targetSeconds: 4.0,
        silenceChunkingEnabled: false,
        minSeconds: 4.0,
        maxSeconds: 4.0,
        silenceLookaroundSeconds: 1.5,
        normalizedLevel: 1.0,
        silenceLevelThreshold: 0.08
    ),
    "fixed chunking should close at target even when loud"
)
expect(
    StreamingChunkPolicy.shouldCloseChunk(
        elapsedSeconds: 8.6,
        targetSeconds: 10.0,
        silenceChunkingEnabled: true,
        minSeconds: 6.0,
        maxSeconds: 12.0,
        silenceLookaroundSeconds: 1.5,
        normalizedLevel: 0.02,
        silenceLevelThreshold: 0.08
    ),
    "silence chunking should close on quiet boundary near target"
)
expect(
    !StreamingChunkPolicy.shouldCloseChunk(
        elapsedSeconds: 8.6,
        targetSeconds: 10.0,
        silenceChunkingEnabled: true,
        minSeconds: 6.0,
        maxSeconds: 12.0,
        silenceLookaroundSeconds: 1.5,
        normalizedLevel: 0.40,
        silenceLevelThreshold: 0.08
    ),
    "silence chunking should keep recording through loud audio before max"
)
expect(
    FallbackRescuePolicy.shouldRun(
        route: "fast_server_process_fallback_skipped",
        fallbackReason: "suspected_truncated_server_output:duration=42.2,chars=301,words=54",
        audioProbablySilent: false
    ),
    "process fallback skipped route should trigger local rescue"
)
expect(
    FallbackRescuePolicy.shouldRun(
        route: "fast_server_native_process_fallback_skipped",
        fallbackReason: "suspected_truncated_server_output:duration=42.2,chars=301,words=54",
        audioProbablySilent: false
    ),
    "native process fallback skipped route must also trigger local rescue"
)
expect(
    !FallbackRescuePolicy.shouldRun(
        route: "fast_server_native",
        fallbackReason: "",
        audioProbablySilent: false
    ),
    "normal fast server route should not trigger rescue"
)
expect(
    !FallbackRescuePolicy.shouldRun(
        route: "fast_server_native_process_fallback_skipped",
        fallbackReason: "suspected_truncated_server_output:duration=42.2,chars=301,words=54",
        audioProbablySilent: true
    ),
    "probably silent clips should not waste a rescue pass"
)
expect(
    StreamingChunkPolicy.shouldCloseChunk(
        elapsedSeconds: 12.0,
        targetSeconds: 10.0,
        silenceChunkingEnabled: true,
        minSeconds: 6.0,
        maxSeconds: 12.0,
        silenceLookaroundSeconds: 1.5,
        normalizedLevel: 0.40,
        silenceLevelThreshold: 0.08
    ),
    "silence chunking should force close at max even when loud"
)
expect(
    BackgroundPolishPolicy.defaultOrder == [.fallbackRescue, .structure, .termPolish, .hindiPolish, .processSecondPass, .finalizer],
    "MVP background polish should run structure as a post-paste action, with no Chinese route"
)
expect(
    !BackgroundPolishToastPolicy.shouldShowCopyFallback(draftWasPasted: true, replacementSucceeded: false),
    "background polish must not show a transcript toast after the main paste already succeeded"
)
expect(
    BackgroundPolishToastPolicy.shouldShowCopyFallback(draftWasPasted: false, replacementSucceeded: false),
    "copy fallback must still show when no text was pasted into the target"
)
expect(
    !BackgroundPolishToastPolicy.shouldShowCopyFallback(draftWasPasted: false, replacementSucceeded: true),
    "copy fallback must not show after successful background replacement"
)
let friendlyRewrite = FriendlyRewritePolicy.rewrite(text: "okay so can you check MCP and then send it to Claude")
expect(
    friendlyRewrite.text == "Can you check MCP. Then send it to Claude.",
    "light polish should lightly structure text without dropping terms; got \(friendlyRewrite)"
)
expect(
    friendlyRewrite.rules.contains("trim_leading_filler") && friendlyRewrite.rules.contains("split_and_then"),
    "light polish should report the local rules it used"
)
expect(
    FriendlyRewritePolicy.shouldUse(draft: "Please keep MCP and Claude in the sentence", final: "Please keep MCP in the sentence.") == false,
    "light polish must reject dropped important tokens"
)
expect(
    FriendlyRewritePolicy.shouldUse(draft: "okay send the PR to Claude", final: "Send the PR to Claude."),
    "light polish should allow harmless filler trim and capitalization"
)
let friendlyShortTermRewrite = FriendlyRewritePolicy.rewrite(text: "okay please send PR 2 to Claude and then update API")
expect(
    friendlyShortTermRewrite.text == "Please send PR 2 to Claude. Then update API.",
    "light polish should preserve short terms, numbers, and acronyms; got \(friendlyShortTermRewrite)"
)
let friendlyQuestionRewrite = FriendlyRewritePolicy.rewrite(text: "okay how do we improve polish without dropping MCP")
expect(
    friendlyQuestionRewrite.text == "How do we improve polish without dropping MCP.",
    "structure must not invent question punctuation while preserving protected terms; got \(friendlyQuestionRewrite)"
)
let friendlyPunctuationSpacingRewrite = FriendlyRewritePolicy.rewrite(text: "how does this work ?")
expect(
    friendlyPunctuationSpacingRewrite.text == "How does this work?",
    "light polish should remove stray spaces before punctuation; got \(friendlyPunctuationSpacingRewrite)"
)
let friendlyPunctuationArtifactRewrite = FriendlyRewritePolicy.rewrite(text: "what I was saying is this should land,")
expect(
    friendlyPunctuationArtifactRewrite.text == "What I was saying is this should land.",
    "structure must avoid wrong question punctuation and comma-period artifacts; got \(friendlyPunctuationArtifactRewrite)"
)
let friendlyGreetingRewrite = FriendlyRewritePolicy.rewrite(text: "Hello, how is it going?")
expect(
    !friendlyGreetingRewrite.changed,
    "structure must not drop a possible greeting; got \(friendlyGreetingRewrite)"
)
let friendlyWhenStatementRewrite = FriendlyRewritePolicy.rewrite(text: "when you replace make sure that the old text is still there")
expect(
    friendlyWhenStatementRewrite.text == "When you replace make sure that the old text is still there.",
    "structure must not turn when-statements into questions; got \(friendlyWhenStatementRewrite)"
)
let friendlyDomainRewrite = FriendlyRewritePolicy.rewrite(text: "please check dictahue.app and then publish API notes")
expect(
    friendlyDomainRewrite.text == "Please check dictahue.app. Then publish API notes.",
    "light polish must preserve domain casing and acronyms; got \(friendlyDomainRewrite)"
)
let friendlyRepeatedFillerRewrite = FriendlyRewritePolicy.rewrite(text: "please check MCP right right and then update API")
expect(
    friendlyRepeatedFillerRewrite.text == "Please check MCP right. Then update API.",
    "light polish should collapse duplicated filler while preserving one copy and work terms; got \(friendlyRepeatedFillerRewrite)"
)
let friendlyDiscourseRewrite = FriendlyRewritePolicy.rewrite(text: "please check MCP but do not drop API because the terms are important")
expect(
    friendlyDiscourseRewrite.text == "Please check MCP. But do not drop API because the terms are important.",
    "light polish should add light structure without changing negation or terms; got \(friendlyDiscourseRewrite)"
)
let friendlyRightButRewrite = FriendlyRewritePolicy.rewrite(text: "okay so this should work right but do not change MCP")
expect(
    friendlyRightButRewrite.text == "This should work right, but do not change MCP.",
    "structure should handle filler-plus-but with a comma instead of rejecting the update; got \(friendlyRightButRewrite)"
)
let friendlyCommaButRewrite = FriendlyRewritePolicy.rewrite(text: "all of this is fine, but is there a problem worth solving")
expect(
    friendlyCommaButRewrite.text == "All of this is fine. But is there a problem worth solving.",
    "light polish should not create comma-period artifacts before But; got \(friendlyCommaButRewrite)"
)
let friendlyExistingSentenceRewrite = FriendlyRewritePolicy.rewrite(text: "Find their email IDs. And then draft an email.")
expect(
    friendlyExistingSentenceRewrite.text == "Find their email IDs. And then draft an email.",
    "light polish should not create double periods after existing sentence boundaries; got \(friendlyExistingSentenceRewrite)"
)
expect(
    !FriendlyRewritePolicy.shouldUse(
        draft: "This is both for competitors as well as like, but posters.",
        final: "This is both for competitors as well as like. But posters."
    ),
    "light polish must reject awkward filler-to-But sentence splits"
)
expect(
    !FriendlyRewritePolicy.shouldUse(
        draft: "Because not only do I need to build it, but it should be simple.",
        final: "Because not only do I need to build it. But it should be simple."
    ),
    "light polish must reject not-only/but split artifacts"
)
expect(
    !FriendlyRewritePolicy.rewrite(text: "okay haan yaar kaise hoga MCP").changed,
    "structure must leave Hindi/Hinglish-looking text to the Hindi polish path"
)
expect(
    !FriendlyRewritePolicy.rewrite(text: "okay 这个 API should work").changed,
    "structure must leave Chinese/multilingual-looking text alone"
)
expect(
    !FriendlyRewritePolicy.shouldUse(draft: "Please send PR 2 to Claude", final: "Please send to Claude."),
    "light polish must reject dropped short terms and numbers"
)
expect(
    !FriendlyRewritePolicy.shouldUse(draft: "Send the PR to Claude", final: "Do not send the PR to Claude."),
    "light polish must reject meaning-changing additions even when original terms remain"
)
expect(
    !FriendlyRewritePolicy.shouldUse(draft: "Send Claude the PR", final: "Claude, send the PR."),
    "light polish must reject reordered same-token rewrites"
)
expect(
    !FriendlyRewritePolicy.shouldUse(draft: "Wrong window, sorry.", final: "Rong window saare."),
    "light polish must reject ASR-style wrong-text replacements"
)
let suffixReplacement = SafeDraftReplacementPolicy.replacementDecision(
    value: "Please check MCP",
    selectedLocation: ("Please check MCP" as NSString).length,
    selectedLength: 0,
    draft: "Please check MCP"
)
expect(
    suffixReplacement.range == NSRange(location: 0, length: ("Please check MCP" as NSString).length)
        && suffixReplacement.reason == "cursor_suffix_match",
    "safe replacement should still accept exact draft before cursor; got \(suffixReplacement)"
)
let trailingNewlineReplacement = SafeDraftReplacementPolicy.replacementDecision(
    value: "Please check MCP\n",
    selectedLocation: ("Please check MCP\n" as NSString).length,
    selectedLength: 0,
    draft: "Please check MCP"
)
expect(
    trailingNewlineReplacement.range == NSRange(location: 0, length: ("Please check MCP" as NSString).length)
        && trailingNewlineReplacement.reason == "unique_draft_match",
    "safe replacement should accept a unique draft even when the field adds trailing text/newline; got \(trailingNewlineReplacement)"
)
let selectedReplacement = SafeDraftReplacementPolicy.replacementDecision(
    value: "Please check MCP",
    selectedLocation: 0,
    selectedLength: ("Please check MCP" as NSString).length,
    draft: "Please check MCP"
)
expect(
    selectedReplacement.range == NSRange(location: 0, length: ("Please check MCP" as NSString).length)
        && selectedReplacement.reason == "selected_draft_match",
    "safe replacement should accept an explicitly selected draft; got \(selectedReplacement)"
)
let duplicateReplacement = SafeDraftReplacementPolicy.replacementDecision(
    value: "Please check MCP\nPlease check MCP\n",
    selectedLocation: ("Please check MCP\nPlease check MCP\n" as NSString).length,
    selectedLength: 0,
    draft: "Please check MCP"
)
expect(
    duplicateReplacement.range == nil && duplicateReplacement.reason == "multiple_draft_matches",
    "safe replacement must reject duplicate draft matches; got \(duplicateReplacement)"
)
expect(TermPolishPolicy.shouldRun(text: "It wrote A, S, R, M, C, B badly."), "spelled acronym output should trigger term polish")
expect(TermPolishPolicy.shouldRun(text: "Use Rumble Fix tools for dictation."), "Rumble Fix product-name miss should trigger term polish")
expect(!TermPolishPolicy.shouldRun(text: "ASR and MCP are already preserved."), "already-preserved terms should not trigger term polish")
expect(!TermPolishPolicy.shouldRun(text: "The UX works in this editor."), "normal uppercase terms should not trigger a slow polish pass")
expect(TermPolishPolicy.shouldUse(draft: "It wrote A, S, R, M, C, B badly.", final: "It wrote ASR, MCP badly."), "small acronym repair should be usable")
expect(!TermPolishPolicy.shouldUse(draft: "It wrote A, S, R, M, C, B badly.", final: "ASR"), "truncated term-polish output must be rejected")
expect(
    HindiPolishPolicy.shouldUse(
        draft: "Yes, nothing will happen if our tool cannot beat others.",
        final: "हाँ भई देख ये सब करने से कुछ नहीं होगा अगर हमारा tool cannot beat others."
    ),
    "Hindi polish should allow a mixed-script faithful update"
)
expect(
    HindiPolishPolicy.shouldUse(
        draft: "Yes, look, nothing will happen if our tool cannot beat others on one core problem, then there is no wedge.",
        final: "haan bhai dekh ye sab karne se kuch nahi hoga agar hamara tool cannot beat others on one core problem, then there is no wedge."
    ),
    "Hindi polish should allow a roman Hinglish faithful update"
)
expect(
    !HindiPolishPolicy.shouldUse(
        draft: "What I need now is a quick answer and be tight and brief and skeptical.",
        final: "haan woh dekh what I need now is a quick answer and be tight and brief and skeptical."
    ),
    "Hindi polish should reject Hindi discourse-only replacements"
)
expect(
    !HindiPolishPolicy.shouldUse(
        draft: "Yes, nothing will happen if our tool cannot beat others.",
        final: "Yes, nothing will happen if our tool cannot beat others."
    ),
    "Hindi polish should reject unchanged English"
)
expect(
    !HindiPolishPolicy.shouldUse(
        draft: "Please preserve the full legal profession point and improve it.",
        final: "हाँ"
    ),
    "Hindi polish should reject truncated updates"
)
expect(
    !HindiPolishPolicy.shouldUse(
        draft: "What I need now is a quick answer and be tight and brief and skeptical.",
        final: "हाँ वह देख what I need now is a quick answer and be tight and brief friends kept it ठीक है"
    ),
    "Hindi polish should reject suspicious new English hallucinations"
)
expect(
    !HindiPolishPolicy.shouldUse(
        draft: "Design a model that can fix Hindi and use a different path if it has Hindi in it.",
        final: "Design a model that can fix Hindi and use Santa path in strange circumstances."
    ),
    "Hindi polish should reject multiple invented English tokens even when not consecutive"
)
expect(
    !HindiPolishPolicy.shouldUse(
        draft: "Now for this goal, think through what the best design would be to design some model that can fix Hindi.",
        final: "Now for this goal think through what a best design would be and that attracts if centre reveals a different path."
    ),
    "Hindi polish should reject roman-script hallucinations without Hindi value"
)
expect(
    !HindiPolishPolicy.shouldUse(
        draft: "It should be direct and brief, API or MCP should work the same.",
        final: "यह शुरू करना और यह शुरू करना और यह शुरू करना �"
    ),
    "Hindi polish should reject corrupted or runaway repeated text"
)
expect(
    HindiPolishPolicy.shouldUseServerSafeUpdate(
        draft: "What should I do now?",
        final: "Haan yaar kya karen aap bata? What should I do? You tell me. You have to answer my question."
    ),
    "server-safe Hindi stream update should be trusted when it restores missing meaning"
)
expect(
    HindiPolishPolicy.shouldUseServerSafeUpdate(
        draft: "The way MCPs work is that there is an API layer and there are guidelines as well. So, the guidelines and documentation are all there. So, the API layer functions well.",
        final: "See, MCP's work is that there is a API layer par uske saath na guidelines vagairah bhi hota hai. guidelines, documentation hi sab hota hai taaki the API layer functions well."
    ),
    "server-safe Hindi stream update should allow corpus-backed Hinglish work speech"
)
expect(
    !HindiPolishPolicy.shouldUseServerSafeUpdate(
        draft: "Please preserve the full legal profession point and improve it.",
        final: "हाँ"
    ),
    "server-safe Hindi stream update should still reject truncation"
)
expect(
    !HindiPolishPolicy.shouldUseServerSafeUpdate(
        draft: "It should be direct and brief, API or MCP should work the same.",
        final: "यह शुरू करना और यह शुरू करना और यह शुरू करना �"
    ),
    "server-safe Hindi stream update should still reject corruption"
)
expect(
    HindiPolishPolicy.shouldUseAudioRiskUpdate(
        draft: "What should I do now?",
        final: "Haan yaar kya karen aap bata? What should I do? You tell me. You have to answer my question."
    ),
    "audio-risk Hindi polish should allow Oriserve to restore missing Hinglish meaning"
)
expect(
    HindiPolishPolicy.shouldUseAudioRiskUpdate(
        draft: "What I need now is a quick answer and be tight and brief and skeptical.",
        final: "Haan bhai, dekh, what I need now is a quick answer and be tight and brief and skeptical. Thik hai, havaavaaji nahin chaahie."
    ),
    "audio-risk Hindi polish should allow substantive roman-Hinglish additions"
)
expect(
    HindiPolishPolicy.shouldUseAudioRiskUpdate(
        draft: "Yes, look, nothing will happen if our tool cannot beat others on one core problem.",
        final: "Haan bhai dekh, yah sab karne se kuchh nahin hoga, agar hamaara tool cannot beat others on a core one core problem."
    ),
    "audio-risk Hindi polish should accept common roman-Hindi spellings, not treat them as suspicious English"
)
expect(
    HindiPolishPolicy.shouldUseAudioRiskUpdate(
        draft: "You have to structure it into four sub-agents. Each agent will have a different task.",
        final: "Aapko aisa karna hoga ki you have to structure it to 4 sub agents. Har agent ka kaam alag alag hoga, doosra agent kuchh aur karega, tisra kuchh aur."
    ),
    "audio-risk Hindi polish should accept common ordinal and work-action Hinglish words"
)
expect(
    !HindiPolishPolicy.shouldUseAudioRiskUpdate(
        draft: "What I need now is a quick answer and be tight and brief and skeptical.",
        final: "Haan woh dekh what I need now is a quick answer and be tight and brief and skeptical."
    ),
    "audio-risk Hindi polish should reject style-only Hindi decoration"
)
expect(
    !HindiPolishPolicy.shouldUseAudioRiskUpdate(
        draft: "Find a useful scale that I can use for my problem.",
        final: "Find a useful scale. Kuchh scale that I can use for my problem."
    ),
    "audio-risk Hindi polish should reject one-token accidental Hinglish on English"
)
expect(
    !HindiPolishPolicy.shouldCheckDraft(
        draft: "What should I do now?",
        qualityHindiRisk: false,
        audioRiskAll: false
    ),
    "Hindi polish should not run for clean English by default"
)
expect(
    HindiPolishPolicy.shouldCheckDraft(
        draft: "Haan bhai, what should I do now?",
        qualityHindiRisk: false,
        audioRiskAll: false
    ),
    "Hindi polish should run when the fast draft still has Hinglish signal"
)
expect(
    HindiPolishPolicy.shouldCheckDraft(
        draft: "What should I do now?",
        qualityHindiRisk: true,
        audioRiskAll: false
    ),
    "Hindi polish should run when the ASR payload flags Hindi risk"
)
expect(
    HindiPolishPolicy.shouldCheckDraft(
        draft: "What should I do now?",
        qualityHindiRisk: false,
        audioRiskAll: true
    ),
    "Hindi polish audio-risk-all override should remain available for experiments"
)
expect(
    HindiPolishPolicy.shouldProbeAudioRisk(
        draft: "What should I do now?",
        route: "fast_server_translate",
        audioSeconds: 8.0,
        audioRiskDetector: true,
        maxAudioSeconds: 90.0
    ),
    "Hindi polish should run cheap audio-risk detection when fast translate may have erased Hinglish"
)
expect(
    HindiPolishPolicy.shouldProbeAudioRisk(
        draft: "What should I do now?",
        route: "fast_server_native",
        audioSeconds: 8.0,
        audioRiskDetector: true,
        maxAudioSeconds: 90.0
    ),
    "native fast-server dictation should also run cheap audio-risk detection"
)
expect(
    HindiPolishPolicy.shouldProbeAudioRisk(
        draft: "What should I do now?",
        route: "fast_server_native_process_fallback_skipped",
        audioSeconds: 8.0,
        audioRiskDetector: true,
        maxAudioSeconds: 90.0
    ),
    "native fast-server fallback-risk route should still allow Hindi audio-risk detection"
)
expect(
    !HindiPolishPolicy.shouldProbeAudioRisk(
        draft: "Haan bhai, what should I do now?",
        route: "fast_server_translate",
        audioSeconds: 8.0,
        audioRiskDetector: true,
        maxAudioSeconds: 90.0
    ),
    "audio-risk detector should not duplicate marker-based Hindi polish"
)
expect(
    !HindiPolishPolicy.shouldProbeAudioRisk(
        draft: "What should I do now?",
        route: "fast_server_translate",
        audioSeconds: 120.0,
        audioRiskDetector: true,
        maxAudioSeconds: 90.0
    ),
    "audio-risk detector should skip very long dictation by default"
)
expect(
    !HindiPolishPolicy.shouldProbeAudioRisk(
        draft: "What should I do now?",
        route: "local_whisper_cpp",
        audioSeconds: 8.0,
        audioRiskDetector: true,
        maxAudioSeconds: 90.0
    ),
    "audio-risk detector should stay scoped to the fast translate path"
)
expect(
    ProcessSecondPassPolicy.shouldUse(
        draft: "What should I do now?",
        final: "haan yaar kya kare ab bata? What should I do? You tell me.",
        requireHindiSignal: true
    ),
    "process second pass should allow a Hinglish rescue with retained draft meaning"
)
expect(
    !ProcessSecondPassPolicy.shouldUse(
        draft: "Yes, look, nothing will happen if our tool cannot beat others on one core problem, then there is no wedge.",
        final: "Yeah, see, nothing to do all this. If our tool cannot beat others on one core problem, then there is no wedge.",
        requireHindiSignal: true
    ),
    "process second pass must reject pure-English rewrites even when Hindi risk fired"
)
expect(
    !ProcessSecondPassPolicy.shouldUse(
        draft: "I added some clips with a bunch of Hindi in it.",
        final: "I added some clips and there are also many Hindi words.",
        requireHindiSignal: true
    ),
    "process second pass must not treat the English word Hindi as enough Hinglish signal"
)
expect(
    !ProcessSecondPassPolicy.shouldUse(
        draft: "In order for this to work right, we have to solve all the problems which I cannot through this code.",
        final: "karsakteho",
        requireHindiSignal: true
    ),
    "process second pass must reject truncated Hinglish updates"
)
let draft = "Please update ASR, MCP in the notes."
let fullValue = "Before. \(draft)"
let selectedLocation = (fullValue as NSString).length
let replacement = SafeDraftReplacementPolicy.replacementRange(value: fullValue, selectedLocation: selectedLocation, selectedLength: 0, draft: draft)
expect(replacement?.location == ("Before. " as NSString).length, "safe replacement should target the exact draft before cursor")
expect(SafeDraftReplacementPolicy.replacementRange(value: "Already submitted", selectedLocation: ("Already submitted" as NSString).length, selectedLength: 0, draft: draft) == nil, "safe replacement must skip when old text is gone")
expect(SafeDraftReplacementPolicy.replacementRange(value: fullValue, selectedLocation: selectedLocation, selectedLength: 3, draft: draft) == nil, "safe replacement must skip active text selections")

let tempRoot = FileManager.default.temporaryDirectory.appendingPathComponent("RambleFixRetainedAudioPolicy-\(UUID().uuidString)", isDirectory: true)
let failureRoot = tempRoot.appendingPathComponent("failures", isDirectory: true)
try FileManager.default.createDirectory(at: failureRoot, withIntermediateDirectories: true)
let now = Date()
var wavs: [URL] = []
for index in 0..<4 {
    let url = tempRoot.appendingPathComponent("clip-\(index).wav")
    try Data("clip-\(index)".utf8).write(to: url)
    try FileManager.default.setAttributes([.modificationDate: now.addingTimeInterval(Double(index))], ofItemAtPath: url.path)
    wavs.append(url)
}
let txt = tempRoot.appendingPathComponent("note.txt")
try Data("ignore".utf8).write(to: txt)
let failureWav = failureRoot.appendingPathComponent("failure.wav")
try Data("failure".utf8).write(to: failureWav)
let pruned = RetainedAudioPolicy.filesToPrune(in: tempRoot, keepingMax: 2).map(\.lastPathComponent)
expect(pruned == ["clip-0.wav", "clip-1.wav"], "retained audio pruning should remove oldest root wavs only")
expect(!pruned.contains("failure.wav"), "retained audio pruning must ignore failure subdirectory")
try? FileManager.default.removeItem(at: tempRoot)

print("RambleFixHotkeyRegressionTests passed")
