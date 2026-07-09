import Foundation

public struct MeetingTranscriptSection {
    public let label: String
    public let text: String

    public init(label: String, text: String) {
        self.label = label
        self.text = text
    }
}

public enum MeetingTranscriptCombiner {
    public static func combinedText(sections: [MeetingTranscriptSection]) -> String {
        sections
            .map { section in
                let text = section.text.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !text.isEmpty else { return "" }
                return "[\(section.label)]\n\(text)"
            }
            .filter { !$0.isEmpty }
            .joined(separator: "\n\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
