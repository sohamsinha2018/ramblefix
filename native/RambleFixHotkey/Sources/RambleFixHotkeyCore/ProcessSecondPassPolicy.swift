import Foundation

public enum ProcessSecondPassPolicy {
    public static func shouldUse(draft: String, final: String, requireHindiSignal: Bool) -> Bool {
        let draftText = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let finalText = final.trimmingCharacters(in: .whitespacesAndNewlines)
        if finalText.isEmpty || finalText == draftText { return false }
        if requireHindiSignal && !hasMeaningfulHinglishSignal(finalText) { return false }
        if draftText.isEmpty { return true }

        let draftWords = wordCount(draftText)
        let finalWords = wordCount(finalText)
        if draftWords >= 6, finalWords < max(3, draftWords / 2) { return false }
        if finalText.count < max(20, draftText.count / 2) { return false }
        return true
    }

    private static func wordCount(_ text: String) -> Int {
        text.split { !$0.isLetter && !$0.isNumber }.count
    }

    private static func hasMeaningfulHinglishSignal(_ text: String) -> Bool {
        if text.unicodeScalars.contains(where: { (0x0900...0x097F).contains(Int($0.value)) }) {
            return true
        }
        let lower = text.lowercased()
        let markers = [
            "aap", "aapko", "agar", "aisa", "aur", "bhai", "chahiye", "dekh",
            "haan", "hai", "hain", "hamara", "hoga", "karna", "karne",
            "karke", "kuch", "kya", "matlab", "nahi", "nahin", "saath",
            "sakta", "sakte", "theek", "toh", "yaar", "yeh"
        ]
        return markers.contains { marker in
            lower.range(of: #"\b\#(marker)\b"#, options: .regularExpression) != nil
        }
    }
}
