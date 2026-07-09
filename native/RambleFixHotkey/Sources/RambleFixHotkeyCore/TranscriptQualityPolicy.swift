import Foundation

public enum TranscriptQualityPolicy {
    public static func isDegenerateTranscript(_ text: String) -> Bool {
        let tokens = normalizedTokens(text)
        if tokens.isEmpty { return true }
        return repeatedTokenScore(tokens) >= 0.25
    }

    public static func repeatedTokenScore(_ text: String) -> Double {
        repeatedTokenScore(normalizedTokens(text))
    }

    private static func repeatedTokenScore(_ tokens: [String]) -> Double {
        if tokens.count < 8 { return 0 }
        var worst = 0.0
        let maxWidth = min(8, max(1, tokens.count / 3))
        for width in 1...maxWidth {
            var index = 0
            while index + (width * 3) <= tokens.count {
                let chunk = Array(tokens[index..<(index + width)])
                var repeats = 1
                var cursor = index + width
                while cursor + width <= tokens.count,
                      Array(tokens[cursor..<(cursor + width)]) == chunk {
                    repeats += 1
                    cursor += width
                }
                if repeats >= 3 {
                    worst = max(worst, Double(width * repeats) / Double(tokens.count))
                    index = cursor
                } else {
                    index += 1
                }
            }
        }
        return (worst * 1000).rounded() / 1000
    }

    private static func normalizedTokens(_ text: String) -> [String] {
        text.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init)
    }
}
