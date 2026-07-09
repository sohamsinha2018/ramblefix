import Foundation
import RambleFixHotkeyCore

struct PolicyRequest: Decodable {
    let id: String
    let draft: String
    let final: String
}

struct PolicyResponse: Encodable {
    let id: String
    let accepted: Bool
    let policyOK: Bool
    let droppedProtectedTerms: [String]
    let final: String?
    let changed: Bool?
    let rules: [String]?
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(2)
}

var inputPath: String?
var projectRoot = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
var policy = "audio-risk"
var args = Array(CommandLine.arguments.dropFirst())
while !args.isEmpty {
    let arg = args.removeFirst()
    switch arg {
    case "--input":
        guard !args.isEmpty else { fail("--input requires a path") }
        inputPath = args.removeFirst()
    case "--project-root":
        guard !args.isEmpty else { fail("--project-root requires a path") }
        projectRoot = URL(fileURLWithPath: args.removeFirst())
    case "--policy":
        guard !args.isEmpty else { fail("--policy requires a value") }
        policy = args.removeFirst()
    default:
        fail("unknown argument: \(arg)")
    }
}

let data: Data
if let inputPath {
    data = (try? Data(contentsOf: URL(fileURLWithPath: inputPath))) ?? Data()
} else {
    data = FileHandle.standardInput.readDataToEndOfFile()
}
guard !data.isEmpty else { fail("empty policy input") }

let requests: [PolicyRequest]
do {
    requests = try JSONDecoder().decode([PolicyRequest].self, from: data)
} catch {
    fail("could not decode policy input: \(error)")
}

let responses = requests.map { request -> PolicyResponse in
    if policy == "structure" || policy == "friendly-rewrite" {
        let rewrite = FriendlyRewritePolicy.rewrite(text: request.draft)
        let dropped = rewrite.changed
            ? Array(droppedProtectedWorkTerms(from: request.draft, to: rewrite.text, projectRoot: projectRoot)).sorted()
            : []
        return PolicyResponse(
            id: request.id,
            accepted: rewrite.changed && dropped.isEmpty,
            policyOK: rewrite.changed,
            droppedProtectedTerms: dropped,
            final: rewrite.text,
            changed: rewrite.changed,
            rules: rewrite.rules
        )
    }

    let policyOK: Bool
    switch policy {
    case "audio-risk":
        policyOK = HindiPolishPolicy.shouldUseAudioRiskUpdate(draft: request.draft, final: request.final)
    case "standard":
        policyOK = HindiPolishPolicy.shouldUse(draft: request.draft, final: request.final)
    case "server-safe":
        policyOK = HindiPolishPolicy.shouldUseServerSafeUpdate(draft: request.draft, final: request.final)
    default:
        fail("unknown policy: \(policy)")
    }
    let dropped = policyOK ? Array(droppedProtectedWorkTerms(from: request.draft, to: request.final, projectRoot: projectRoot)).sorted() : []
    return PolicyResponse(
        id: request.id,
        accepted: policyOK && dropped.isEmpty,
        policyOK: policyOK,
        droppedProtectedTerms: dropped,
        final: nil,
        changed: nil,
        rules: nil
    )
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
do {
    let output = try encoder.encode(responses)
    FileHandle.standardOutput.write(output)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    fail("could not encode policy output: \(error)")
}

func droppedProtectedWorkTerms(from draft: String, to final: String, projectRoot: URL) -> Set<String> {
    let draftTerms = protectedWorkTerms(in: draft, projectRoot: projectRoot)
    if draftTerms.isEmpty { return [] }
    return draftTerms.subtracting(protectedWorkTerms(in: final, projectRoot: projectRoot))
}

func protectedWorkTerms(in text: String, projectRoot: URL) -> Set<String> {
    let normalized = text
        .lowercased()
        .replacingOccurrences(of: "[^a-z0-9]+", with: " ", options: .regularExpression)
    let tokens = Set(normalized.split(separator: " ").map(String.init))
    let compact = normalized.replacingOccurrences(of: " ", with: "")
    let aliases = protectedTermAliases(projectRoot: projectRoot)
    var terms = Set(aliases.compactMap { alias, canonical in
        protectedAliasAppears(alias, normalized: " \(normalized) ", tokens: tokens, compact: compact) ? canonical : nil
    })
    terms.formUnion(patternProtectedTerms(in: text))
    return terms
}

func protectedAliasAppears(_ alias: String, normalized: String, tokens: Set<String>, compact: String) -> Bool {
    guard !alias.isEmpty else { return false }
    if alias.contains(" ") {
        return normalized.contains(" \(alias) ")
    }
    return tokens.contains(alias) || (alias.count >= 5 && compact.contains(alias))
}

func protectedTermAliases(projectRoot: URL) -> [String: String] {
    let paths = [
        projectRoot.appendingPathComponent("config/dictionary.json"),
        projectRoot.appendingPathComponent("config/memory_terms.json")
    ]
    var aliases: [String: String] = [:]
    for path in paths {
        loadProtectedTerms(from: path, into: &aliases)
    }
    return aliases
}

func patternProtectedTerms(in text: String) -> Set<String> {
    var terms: Set<String> = []
    let patterns = [
        #"\b[A-Z][A-Z0-9]{1,9}\b"#,
        #"\b[A-Za-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b"#,
        #"\b[A-Za-z]+[0-9][A-Za-z0-9]*\b"#,
        #"(?:\b[A-Za-z]\.){2,}[A-Za-z]?\.?"#
    ]
    for pattern in patterns {
        guard let regex = try? NSRegularExpression(pattern: pattern) else { continue }
        let nsText = text as NSString
        for match in regex.matches(in: text, range: NSRange(location: 0, length: nsText.length)) {
            let raw = nsText.substring(with: match.range)
            let normalized = normalizeProtectedTerm(raw)
            if shouldProtectPatternTerm(raw: raw, normalized: normalized) {
                terms.insert(canonicalPatternProtectedTerm(raw: raw, normalized: normalized))
            }
        }
    }
    return terms
}

func canonicalPatternProtectedTerm(raw: String, normalized: String) -> String {
    if raw.range(of: #"^[A-Z0-9]{2,}s$"#, options: .regularExpression) != nil,
       normalized.count > 2 {
        return String(normalized.dropLast())
    }
    return normalized
}

func shouldProtectPatternTerm(raw: String, normalized: String) -> Bool {
    guard normalized.count >= 2 else { return false }
    let rejected = ["am", "pm", "ok", "yes", "no"]
    if rejected.contains(normalized) { return false }
    if raw.contains(".") { return normalized.count >= 2 }
    let hasDigit = raw.rangeOfCharacter(from: .decimalDigits) != nil
    let letters = raw.filter { $0.isLetter }
    let uppercaseLetters = letters.filter { $0.isUppercase }
    let lowercaseLetters = letters.filter { $0.isLowercase }
    if hasDigit, !letters.isEmpty { return true }
    if letters.count >= 2, uppercaseLetters.count == letters.count { return true }
    return uppercaseLetters.count >= 2 && !lowercaseLetters.isEmpty
}

func loadProtectedTerms(from path: URL, into aliases: inout [String: String]) {
    guard let data = try? Data(contentsOf: path),
          let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let items = payload["terms"] as? [[String: Any]] else {
        return
    }
    for item in items {
        guard item["enabled"] as? Bool != false else { continue }
        if let status = item["status"] as? String,
           !["auto", "approved"].contains(status.lowercased()) {
            continue
        }
        guard let canonicalRaw = item["canonical"] as? String else { continue }
        let canonical = normalizeProtectedTerm(canonicalRaw)
        guard !canonical.isEmpty else { continue }
        aliases[canonical] = canonical
        for aliasRaw in item["aliases"] as? [String] ?? [] {
            let alias = normalizeProtectedTerm(aliasRaw)
            if !alias.isEmpty {
                aliases[alias] = canonical
            }
        }
    }
}

func normalizeProtectedTerm(_ value: String) -> String {
    value
        .lowercased()
        .replacingOccurrences(of: "[^a-z0-9]+", with: "", options: .regularExpression)
}
