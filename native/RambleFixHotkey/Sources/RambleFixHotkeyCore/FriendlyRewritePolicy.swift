import Foundation

public struct FriendlyRewriteResult: Equatable {
    public let text: String
    public let changed: Bool
    public let rules: [String]
}

public enum FriendlyRewritePolicy {
    private static let fillerTokens: Set<String> = [
        "okay", "ok", "so", "uh", "um", "basically"
    ]
    private static let repeatedFillerTokens: Set<String> = [
        "okay", "ok", "yeah", "yes", "right", "like", "basically"
    ]
    private static let repeatedFillerPhrases: [[String]] = [
        ["you", "know"],
        ["i", "mean"]
    ]
    private static let stopTokens: Set<String> = [
        "and", "the", "for", "with", "that", "this", "then", "but"
    ]

    public static func rewrite(text: String) -> FriendlyRewriteResult {
        let original = normalizedWhitespace(text)
        guard !original.isEmpty else {
            return FriendlyRewriteResult(text: "", changed: false, rules: [])
        }
        guard !looksMixedLanguage(original) else {
            return FriendlyRewriteResult(text: original, changed: false, rules: [])
        }

        var output = original
        var rules: [String] = []

        let leadingTrimmed = trimLeadingFillers(output)
        if leadingTrimmed != output {
            output = leadingTrimmed
            rules.append("trim_leading_filler")
        }

        let pronounFixed = replaceRegex(
            output,
            pattern: #"(?i)\bi\b"#,
            replacement: "I"
        )
        if pronounFixed != output {
            output = pronounFixed
            rules.append("capitalize_i")
        }

        let repeatedFillersCollapsed = collapseRepeatedFillers(output)
        if repeatedFillersCollapsed != output {
            output = repeatedFillersCollapsed
            rules.append("collapse_repeated_fillers")
        }

        let splitThen = splitAndThen(output)
        if splitThen != output {
            output = splitThen
            rules.append("split_and_then")
        }

        let splitDiscourse = splitDiscourseBoundaries(output)
        if splitDiscourse != output {
            output = splitDiscourse
            rules.append("split_discourse_boundary")
        }

        let punctuationSpaced = normalizePunctuationSpacing(output)
        if punctuationSpaced != output {
            output = punctuationSpaced
            rules.append("punctuation_spacing")
        }

        let punctuationArtifactsFixed = normalizePunctuationArtifacts(output)
        if punctuationArtifactsFixed != output {
            output = punctuationArtifactsFixed
            rules.append("punctuation_artifacts")
        }

        let capitalized = capitalizeSentenceStarts(output)
        if capitalized != output {
            output = capitalized
            rules.append("capitalize_sentence")
        }

        let punctuated = addTerminalPunctuation(output, original: original)
        if punctuated != output {
            output = punctuated
            rules.append("terminal_punctuation")
        }

        let preservedTerms = restoreCaseSensitiveTokens(from: original, in: output)
        if preservedTerms != output {
            output = preservedTerms
            rules.append("preserve_case_sensitive_tokens")
        }

        output = normalizedWhitespace(output)
        guard shouldUse(draft: original, final: output) else {
            return FriendlyRewriteResult(text: original, changed: false, rules: [])
        }
        return FriendlyRewriteResult(text: output, changed: output != original, rules: rules)
    }

    public static func shouldUse(draft: String, final: String) -> Bool {
        let draftText = normalizedWhitespace(draft)
        let finalText = normalizedWhitespace(final)
        guard !draftText.isEmpty, !finalText.isEmpty, draftText != finalText else { return false }
        guard finalText.count <= max(draftText.count + 24, Int(Double(draftText.count) * 1.18)) else { return false }
        guard finalText.count >= max(2, Int(Double(draftText.count) * 0.75)) else { return false }
        guard conservativeStructureTokens(from: draftText) == conservativeStructureTokens(from: finalText, droppingLeadingFillers: false) else {
            return false
        }
        guard importantTokenRetentionRatio(from: draftText, to: finalText) >= 1.0 else { return false }
        guard finalText.split(whereSeparator: { $0.isWhitespace }).count >= max(2, min(4, draftText.split(whereSeparator: { $0.isWhitespace }).count)) else {
            return false
        }
        guard !hasAwkwardSplitArtifacts(finalText) else { return false }
        return true
    }

    public static func importantTokenRetentionRatio(from draft: String, to final: String) -> Double {
        let draftTokens = Set(importantTokens(draft))
        if draftTokens.isEmpty { return 1.0 }
        let finalTokens = Set(importantTokens(final))
        return Double(draftTokens.intersection(finalTokens).count) / Double(draftTokens.count)
    }

    private static func importantTokens(_ text: String) -> [String] {
        text
            .lowercased()
            .split { !$0.isLetter && !$0.isNumber }
            .map(String.init)
            .filter { token in
                token.count >= 3 && !fillerTokens.contains(token)
                    && !stopTokens.contains(token)
            }
    }

    private static func conservativeStructureTokens(from text: String, droppingLeadingFillers: Bool = true) -> [String] {
        var tokens = text
            .lowercased()
            .split { !$0.isLetter && !$0.isNumber }
            .map(String.init)
        if droppingLeadingFillers {
            while let first = tokens.first, fillerTokens.contains(first) {
                tokens.removeFirst()
            }
        }
        return tokensWithoutRepeatedFillers(tokensWithoutSplitConjunctions(tokens))
    }

    private static func tokensWithoutSplitConjunctions(_ tokens: [String]) -> [String] {
        var output: [String] = []
        var index = 0
        while index < tokens.count {
            if tokens[index] == "and",
               index + 1 < tokens.count,
               tokens[index + 1] == "then" {
                index += 1
                continue
            }
            output.append(tokens[index])
            index += 1
        }
        return output
    }

    private static func tokensWithoutRepeatedFillers(_ tokens: [String]) -> [String] {
        var output: [String] = []
        var index = 0
        while index < tokens.count {
            var skippedPhrase = false
            for phrase in repeatedFillerPhrases where phrase.count > 0 && index + phrase.count <= tokens.count {
                let current = Array(tokens[index..<index + phrase.count])
                if current == phrase,
                   output.count >= phrase.count,
                   Array(output.suffix(phrase.count)) == phrase {
                    index += phrase.count
                    skippedPhrase = true
                    break
                }
            }
            if skippedPhrase { continue }

            let token = tokens[index]
            if repeatedFillerTokens.contains(token), output.last == token {
                index += 1
                continue
            }
            output.append(token)
            index += 1
        }
        return output
    }

    private static func trimLeadingFillers(_ text: String) -> String {
        var output = text
        var changed = true
        while changed {
            changed = false
            for filler in fillerTokens.sorted(by: { $0.count > $1.count }) {
                let next = replaceRegex(
                    output,
                    pattern: #"(?i)^\s*\#(NSRegularExpression.escapedPattern(for: filler))[\s,]+"#,
                    replacement: ""
                )
                if next != output {
                    output = next
                    changed = true
                    break
                }
            }
        }
        return normalizedWhitespace(output)
    }

    private static func normalizePunctuationSpacing(_ text: String) -> String {
        replaceRegex(
            text,
            pattern: #"\s+([,?.!])"#,
            replacement: "$1"
        )
    }

    private static func normalizePunctuationArtifacts(_ text: String) -> String {
        let commaBeforeTerminal = replaceRegex(
            text,
            pattern: #",([?.!])"#,
            replacement: "$1"
        )
        return replaceRegex(
            commaBeforeTerminal,
            pattern: #"([?.!]),"#,
            replacement: "$1"
        )
    }

    private static func collapseRepeatedFillers(_ text: String) -> String {
        var output = text
        var changed = true
        while changed {
            changed = false
            for phrase in ["you know", "I mean", "i mean"] {
                let next = replaceRegex(
                    output,
                    pattern: #"(?i)\b\#(NSRegularExpression.escapedPattern(for: phrase))[\s,]+\#(NSRegularExpression.escapedPattern(for: phrase))\b"#,
                    replacement: phrase
                )
                if next != output {
                    output = next
                    changed = true
                }
            }
            for filler in repeatedFillerTokens.sorted(by: { $0.count > $1.count }) {
                let next = replaceRegex(
                    output,
                    pattern: #"(?i)\b\#(NSRegularExpression.escapedPattern(for: filler))[\s,]+\#(NSRegularExpression.escapedPattern(for: filler))\b"#,
                    replacement: filler
                )
                if next != output {
                    output = next
                    changed = true
                }
            }
        }
        return normalizedWhitespace(output)
    }

    private static func splitAndThen(_ text: String) -> String {
        let wordCount = text.split(whereSeparator: { $0.isWhitespace }).count
        guard wordCount >= 5 else { return text }
        return replaceRegex(
            text,
            pattern: #"(?i)(?<![.?!:]),?\s+and then\s+"#,
            replacement: ". Then "
        )
    }

    private static func splitDiscourseBoundaries(_ text: String) -> String {
        var output = normalizeFillerButComma(text)
        let wordCount = text.split(whereSeparator: { $0.isWhitespace }).count
        guard wordCount >= 10 else { return normalizedWhitespace(output) }
        let boundaries: [(String, String)] = [
            (#"(?i)(?<![.?!:]),?\s+(but)\s+"#, ". But "),
            (#"(?i)(?<![.?!:]),?\s+(so\s+the\s+question\s+is(?:\s+that)?)\s+"#, ". So the question is "),
            (#"(?i)(?<![.?!:]),?\s+(the\s+first\s+thing\s+is(?:\s+that)?)\s+"#, ". The first thing is "),
            (#"(?i)(?<![.?!:]),?\s+(the\s+second\s+thing\s+is(?:\s+that)?)\s+"#, ". The second thing is "),
            (#"(?i)(?<![.?!:]),?\s+(another\s+thing\s+is(?:\s+that)?)\s+"#, ". Another thing is "),
            (#"(?i)(?<![.?!:]),?\s+(for\s+example)\s+"#, ". For example ")
        ]
        for (pattern, replacement) in boundaries {
            output = replaceRegex(output, pattern: pattern, replacement: replacement)
        }
        return normalizedWhitespace(normalizeFillerButComma(output))
    }

    private static func normalizeFillerButComma(_ text: String) -> String {
        let periodFixed = replaceRegex(
            text,
            pattern: #"(?i)\b(like|right|okay|ok|yeah)\.\s+But\s+"#,
            replacement: "$1, but "
        )
        return replaceRegex(
            periodFixed,
            pattern: #"(?i)\b(like|right|okay|ok|yeah)[,.]?\s+but\s+"#,
            replacement: "$1, but "
        )
    }

    private static func addTerminalPunctuation(_ text: String, original: String) -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let last = trimmed.last else { return text }
        if ".?!:".contains(last) { return text }
        if last == "," {
            return "\(trimmed.dropLast())."
        }
        if text.contains(".") {
            return "\(text)."
        }
        return "\(text)."
    }

    private static func hasAwkwardSplitArtifacts(_ text: String) -> Bool {
        if text.range(of: #"(?i)\b(like|right|okay|ok|yeah)\.\s+But\b"#, options: .regularExpression) != nil {
            return true
        }
        if text.range(of: #"(?i)\bnot\s+only\b.*\.\s+But\b"#, options: .regularExpression) != nil {
            return true
        }
        return false
    }

    private static func looksMixedLanguage(_ text: String) -> Bool {
        if text.unicodeScalars.contains(where: { scalar in
            (0x0900...0x097F).contains(Int(scalar.value))
                || (0x0600...0x06FF).contains(Int(scalar.value))
                || (0x4E00...0x9FFF).contains(Int(scalar.value))
        }) {
            return true
        }
        let markers: Set<String> = [
            "haan", "han", "kya", "hai", "hain", "nahi", "nahin", "matlab",
            "yaar", "mujhe", "mera", "meri", "kaise", "hoga", "hogi", "karo",
            "karna", "karenge", "achcha", "acha", "thik", "theek", "phir",
            "agar", "aisa", "waise"
        ]
        let tokens = normalizedWhitespace(text)
            .lowercased()
            .split { !$0.isLetter && !$0.isNumber }
            .map(String.init)
        return tokens.contains { markers.contains($0) }
    }

    private static func capitalizeSentenceStarts(_ text: String) -> String {
        var output = ""
        var shouldCapitalize = true
        for scalar in text.unicodeScalars {
            let character = Character(scalar)
            if shouldCapitalize, String(character).rangeOfCharacter(from: .letters) != nil {
                output.append(String(character).uppercased())
                shouldCapitalize = false
            } else {
                output.append(character)
            }
            if ".?!".unicodeScalars.contains(scalar) {
                shouldCapitalize = true
            } else if !CharacterSet.whitespacesAndNewlines.contains(scalar) {
                if String(character).rangeOfCharacter(from: .letters) == nil || !shouldCapitalize {
                    shouldCapitalize = false
                }
            }
        }
        return output
    }

    private static func restoreCaseSensitiveTokens(from original: String, in candidate: String) -> String {
        var output = candidate
        for token in caseSensitiveTokens(original) {
            let pattern = #"(?i)(?<![A-Za-z0-9])\#(NSRegularExpression.escapedPattern(for: token))(?![A-Za-z0-9])"#
            output = replaceRegex(output, pattern: pattern, replacement: token)
        }
        return output
    }

    private static func caseSensitiveTokens(_ text: String) -> [String] {
        let patterns = [
            #"\b[A-Za-z0-9][A-Za-z0-9-]*\.[A-Za-z]{2,}\b"#,
            #"\b[A-Za-z]+[0-9][A-Za-z0-9]*\b"#,
            #"\b[A-Za-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b"#,
            #"\b[A-Z][A-Z0-9]{1,9}s?\b"#
        ]
        var ordered: [String] = []
        var seen: Set<String> = []
        for pattern in patterns {
            guard let regex = try? NSRegularExpression(pattern: pattern) else { continue }
            let nsText = text as NSString
            let range = NSRange(location: 0, length: nsText.length)
            for match in regex.matches(in: text, range: range) {
                let token = nsText.substring(with: match.range)
                let key = token.lowercased()
                if !seen.contains(key) {
                    seen.insert(key)
                    ordered.append(token)
                }
            }
        }
        return ordered
    }

    private static func normalizedWhitespace(_ text: String) -> String {
        replaceRegex(
            text,
            pattern: #"\s+"#,
            replacement: " "
        )
        .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func replaceRegex(_ text: String, pattern: String, replacement: String) -> String {
        text.replacingOccurrences(
            of: pattern,
            with: replacement,
            options: [.regularExpression]
        )
    }
}
