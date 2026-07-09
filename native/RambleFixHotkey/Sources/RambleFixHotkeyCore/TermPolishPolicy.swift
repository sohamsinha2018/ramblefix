import Foundation

public enum TermPolishPolicy {
    public static func shouldRun(text: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }
        return hasSpelledLetterSequence(trimmed) || hasSplitKnownProductTerm(trimmed)
    }

    public static func shouldUse(draft: String, final: String) -> Bool {
        let draftText = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let finalText = final.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !draftText.isEmpty, !finalText.isEmpty, draftText != finalText else { return false }

        let draftWords = wordCount(draftText)
        let finalWords = wordCount(finalText)
        let reducedSpelledLetters = spelledLetterSequenceCount(finalText) < spelledLetterSequenceCount(draftText)

        if draftWords >= 8, finalWords < max(4, draftWords / 2) {
            return false
        }
        if finalWords > max(6, Int(Double(max(draftWords, 1)) * 1.45)) {
            return false
        }

        let delta = abs(finalText.count - draftText.count)
        if delta > max(32, draftText.count / 3), !reducedSpelledLetters {
            return false
        }

        if !shouldRun(text: draftText), !hasSplitKnownProductTerm(finalText) {
            return false
        }
        return true
    }

    public static func hasSpelledLetterSequence(_ text: String) -> Bool {
        spelledLetterSequenceCount(text) > 0
    }

    private static func spelledLetterSequenceCount(_ text: String) -> Int {
        let pattern = #"(?i)(?<![A-Za-z])(?:[A-Z](?:\s*,\s*|\s+)){1,7}[A-Z](?![A-Za-z])"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return 0 }
        let nsText = text as NSString
        return regex.numberOfMatches(in: text, range: NSRange(location: 0, length: nsText.length))
    }

    private static func hasSplitKnownProductTerm(_ text: String) -> Bool {
        text.range(of: #"\b(?:Ramble|Rumble)\s+Fix\b"#, options: [.regularExpression, .caseInsensitive]) != nil
    }

    private static func wordCount(_ text: String) -> Int {
        text.split { !$0.isLetter && !$0.isNumber }.count
    }
}
