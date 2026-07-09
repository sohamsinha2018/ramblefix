import Foundation

public enum HindiPolishPolicy {
    public static func shouldCheckDraft(draft: String, qualityHindiRisk: Bool, audioRiskAll: Bool) -> Bool {
        if audioRiskAll { return true }
        if qualityHindiRisk { return true }
        let draftText = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        if draftText.isEmpty { return false }
        return hasHindiSignal(draftText)
    }

    public static func shouldProbeAudioRisk(
        draft: String,
        route: String,
        audioSeconds: Double?,
        audioRiskDetector: Bool,
        maxAudioSeconds: Double
    ) -> Bool {
        guard audioRiskDetector else { return false }
        let draftText = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard wordCount(draftText) >= 3 else { return false }
        if hasHindiSignal(draftText) { return false }
        if let audioSeconds, audioSeconds > maxAudioSeconds { return false }
        return isFastServerRoute(route)
    }

    public static func shouldUse(draft: String, final: String) -> Bool {
        let draftText = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let finalText = final.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !draftText.isEmpty, !finalText.isEmpty, draftText != finalText else { return false }
        guard hasHindiSignal(finalText) else { return false }
        guard !hasCorruptionSignal(finalText) else { return false }
        guard !isHindiStyleOnlyChange(draft: draftText, final: finalText) else { return false }
        guard !hasSuspiciousNewEnglishPhrase(draft: draftText, final: finalText) else { return false }
        guard !hasTooManyNewEnglishTokens(draft: draftText, final: finalText) else { return false }
        return passesLengthEnvelope(draft: draftText, final: finalText)
    }

    public static func shouldUseServerSafeUpdate(draft: String, final: String) -> Bool {
        let draftText = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let finalText = final.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !draftText.isEmpty, !finalText.isEmpty, draftText != finalText else { return false }
        guard !hasCorruptionSignal(finalText) else { return false }
        return passesLengthEnvelope(draft: draftText, final: finalText)
    }

    public static func shouldUseAudioRiskUpdate(draft: String, final: String) -> Bool {
        let draftText = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let finalText = final.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !draftText.isEmpty, !finalText.isEmpty, draftText != finalText else { return false }
        guard hasSubstantiveHindiSignal(finalText) else { return false }
        guard !hasCorruptionSignal(finalText) else { return false }
        guard !isHindiStyleOnlyChange(draft: draftText, final: finalText) else { return false }
        let minimumRetention = hasStrongSubstantiveHindiSignal(finalText) ? 0.35 : 0.55
        guard contentRetentionRatio(from: draftText, to: finalText) >= minimumRetention else { return false }
        return passesLengthEnvelope(draft: draftText, final: finalText)
    }

    private static func passesLengthEnvelope(draft: String, final: String) -> Bool {
        let draftWords = wordCount(draft)
        let finalWords = wordCount(final)
        if draftWords >= 6, finalWords < max(3, draftWords / 3) {
            return false
        }
        if draftWords >= 6, finalWords > max(draftWords + 45, Int(Double(draftWords) * 2.4)) {
            return false
        }
        if draft.count >= 40, final.count < draft.count / 3 {
            return false
        }
        return true
    }

    public static func hasHindiSignal(_ text: String) -> Bool {
        if text.unicodeScalars.contains(where: { (0x0900...0x097F).contains(Int($0.value)) }) {
            return true
        }
        let lower = text.lowercased()
        let markers = [
            "aap", "agar", "bhai", "haan", "hai", "hain",
            "kaise", "kya", "matlab", "nahi", "nahin", "theek", "toh", "yaar", "yeh"
        ]
        return markers.contains { marker in
            lower.range(of: #"\b\#(marker)\b"#, options: .regularExpression) != nil
        }
    }

    public static func hasSubstantiveHindiSignal(_ text: String) -> Bool {
        if text.unicodeScalars.contains(where: { (0x0900...0x097F).contains(Int($0.value)) }) {
            return true
        }
        let hits = Set(normalizedTokens(text).filter { substantiveRomanHindiTokens.contains($0) })
        return hits.count >= 2
    }

    private static func hasStrongSubstantiveHindiSignal(_ text: String) -> Bool {
        let hits = Set(normalizedTokens(text).filter { substantiveRomanHindiTokens.contains($0) })
        return hits.count >= 4
    }

    private static func wordCount(_ text: String) -> Int {
        text.split { !$0.isLetter && !$0.isNumber }.count
    }

    private static func isFastServerRoute(_ route: String) -> Bool {
        let normalized = route.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return normalized == "fast_server_translate"
            || normalized == "fast_server_native"
            || normalized == "fast_server_native_process_fallback_skipped"
    }

    private static func hasCorruptionSignal(_ text: String) -> Bool {
        if text.contains("\u{FFFD}") { return true }
        return hasRunawayRepetition(text)
    }

    private static func hasRunawayRepetition(_ text: String) -> Bool {
        let tokens = normalizedTokens(text)
        guard tokens.count >= 3 else { return false }
        for width in 1...min(4, tokens.count / 3) {
            let limit = tokens.count - (width * 3)
            if limit < 0 { continue }
            for start in 0...limit {
                let first = Array(tokens[start..<(start + width)])
                let second = Array(tokens[(start + width)..<(start + (2 * width))])
                let third = Array(tokens[(start + (2 * width))..<(start + (3 * width))])
                if first == second, second == third {
                    return true
                }
            }
        }
        return false
    }

    private static func hasSuspiciousNewEnglishPhrase(draft: String, final: String) -> Bool {
        let draftTokens = Set(englishContentTokens(draft))
        var unknownRun = 0
        for token in englishContentTokens(final) {
            if isKnownCandidateToken(token, draftTokens: draftTokens) {
                unknownRun = 0
            } else {
                unknownRun += 1
                if unknownRun >= 2 { return true }
            }
        }
        return false
    }

    private static func hasTooManyNewEnglishTokens(draft: String, final: String) -> Bool {
        let draftTokens = Set(englishContentTokens(draft))
        let finalTokens = englishContentTokens(final)
        var unknown = Set<String>()
        for token in finalTokens {
            if isKnownCandidateToken(token, draftTokens: draftTokens) {
                continue
            } else {
                unknown.insert(token)
                if unknown.count >= 2 { return true }
            }
        }
        return false
    }

    private static func isHindiStyleOnlyChange(draft: String, final: String) -> Bool {
        let draftTokens = normalizedTokens(draft)
        guard draftTokens.count >= 4 else { return false }
        let finalMeaningTokens = normalizedTokens(final).filter { !hindiStyleOnlyTokens.contains($0) }
        guard finalMeaningTokens.count >= 4 else { return false }
        return finalMeaningTokens == draftTokens
    }

    private static func contentRetentionRatio(from draft: String, to final: String) -> Double {
        let draftTokens = Set(normalizedTokens(draft).filter { $0.count >= 3 })
        if draftTokens.isEmpty { return 1.0 }
        let finalTokens = Set(normalizedTokens(final).filter { $0.count >= 3 })
        return Double(draftTokens.intersection(finalTokens).count) / Double(max(1, draftTokens.count))
    }

    private static func englishContentTokens(_ text: String) -> [String] {
        normalizedTokens(text).filter { token in
            token.count >= 4 && token.unicodeScalars.allSatisfy { scalar in
                (65...90).contains(Int(scalar.value)) || (97...122).contains(Int(scalar.value))
            }
        }
    }

    private static func isKnownCandidateToken(_ token: String, draftTokens: Set<String>) -> Bool {
        draftTokens.contains(token) || commonEnglishTokens.contains(token) || romanHindiTokens.contains(token)
    }

    private static func normalizedTokens(_ text: String) -> [String] {
        text.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init)
    }

    private static let commonEnglishTokens: Set<String> = [
        "about", "after", "again", "also", "because", "before", "being", "could",
        "does", "done", "from", "have", "into", "like", "maybe", "need",
        "okay", "only", "right", "same", "should", "some", "that", "then",
        "there", "thing", "this", "through", "what", "when", "where", "which",
        "will", "with", "work", "would", "your", "forth"
    ]

    private static let romanHindiTokens: Set<String> = [
        "aap", "aapko", "agar", "aisa", "alag", "aur", "baat", "baatein",
        "bhai", "chaahie", "chahiye", "dekh", "doosra", "dusra", "haan", "hai",
        "hain", "hamara", "hamaara", "hamein", "hamen", "har", "hoga", "honi",
        "hota", "humein", "ismein", "ismen", "kaam", "kaise", "karega", "karenge",
        "karna", "karne", "karke", "karen", "kuch", "kuchh", "kya", "matlab",
        "mein", "nahi", "nahin", "paega", "payega", "payengi", "par", "saath",
        "sakta", "samajho", "teesra", "theek", "thik", "tisra", "uske", "vagairah",
        "vahi", "vah", "vo", "woh", "yaar", "yah", "yeh"
    ]

    private static let hindiStyleOnlyTokens: Set<String> = [
        "bhai", "dekh", "haan", "matlab", "na", "theek", "thik", "toh",
        "vah", "vahi", "vo", "woh", "ya", "yaar", "ye", "yeh"
    ]

    private static let substantiveRomanHindiTokens: Set<String> = romanHindiTokens.subtracting(hindiStyleOnlyTokens).union([
        "bata", "hamein", "hamen", "havaavaaji", "hawabaazi", "paengi", "samjhaun"
    ])
}
