import Foundation

public enum HistoryMenuPolicy {
    public static func usableTranscript(_ text: String?) -> String? {
        guard let text else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : text
    }

    public static func copyLatestEnabled(memoryTranscript: String?, cachedHistoryTranscript: String?) -> Bool {
        usableTranscript(memoryTranscript) != nil || usableTranscript(cachedHistoryTranscript) != nil
    }
}
