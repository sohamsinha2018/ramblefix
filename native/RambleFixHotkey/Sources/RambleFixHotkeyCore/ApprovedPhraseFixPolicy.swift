import Foundation

public struct ApprovedPhraseFixEntry: Equatable {
    public let source: String
    public let replacement: String

    public init(source: String, replacement: String) {
        self.source = source
        self.replacement = replacement
    }
}

public struct ApprovedPhraseFixResult: Equatable {
    public let text: String
    public let changed: Bool
}

public enum ApprovedPhraseFixPolicy {
    public static func apply(text: String, fixes: [ApprovedPhraseFixEntry]) -> ApprovedPhraseFixResult {
        var corrected = text
        for phraseFix in fixes.sorted(by: { $0.source.count > $1.source.count }) {
            let source = phraseFix.source.trimmingCharacters(in: .whitespacesAndNewlines)
            let replacement = phraseFix.replacement.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !source.isEmpty, !replacement.isEmpty else { continue }
            let escaped = NSRegularExpression.escapedPattern(for: source)
            let pattern = #"(?i)(?<![A-Za-z0-9])"# + escaped + #"(?![A-Za-z0-9])"#
            corrected = corrected.replacingOccurrences(
                of: pattern,
                with: replacement,
                options: .regularExpression
            )
        }
        return ApprovedPhraseFixResult(text: corrected, changed: corrected != text)
    }
}
