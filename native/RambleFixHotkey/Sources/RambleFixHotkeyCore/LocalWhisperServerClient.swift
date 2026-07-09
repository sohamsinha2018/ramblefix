import AVFoundation
import Foundation

public struct LocalWhisperServerTranscript: Encodable {
    public let text: String
    public let rawText: String
    public let engine: String
    public let processor: String
    public let fallbackReason: String
    public let route: String
    public let seconds: Double
    public let quality: [String: JSONValue]
}

public enum JSONValue: Encodable {
    case string(String)
    case double(Double)
    case bool(Bool)

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value):
            try container.encode(value)
        case .double(let value):
            try container.encode(value)
        case .bool(let value):
            try container.encode(value)
        }
    }
}

public enum LocalWhisperServerClient {
    public static func transcribe(
        audioURL: URL,
        endpoint: URL = URL(string: "http://127.0.0.1:8188/inference")!,
        timeout: TimeInterval = 20.0
    ) throws -> LocalWhisperServerTranscript {
        guard isLoopbackHost(endpoint.host) else {
            throw LocalWhisperServerError.nonLoopbackEndpoint
        }
        let started = Date()
        let boundary = "RambleFixBoundary-\(UUID().uuidString)"
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.httpBody = try multipartBody(audioURL: audioURL, boundary: boundary)

        let semaphore = DispatchSemaphore(value: 0)
        var resultData: Data?
        var resultResponse: URLResponse?
        var resultError: Error?
        let task = URLSession.shared.dataTask(with: request) { data, response, error in
            resultData = data
            resultResponse = response
            resultError = error
            semaphore.signal()
        }
        task.resume()
        if semaphore.wait(timeout: .now() + timeout + 0.5) == .timedOut {
            task.cancel()
            throw LocalWhisperServerError.requestTimedOut
        }
        if let resultError {
            throw resultError
        }
        if let http = resultResponse as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw LocalWhisperServerError.httpStatus(http.statusCode)
        }
        guard let resultData,
              let payload = try JSONSerialization.jsonObject(with: resultData) as? [String: Any] else {
            throw LocalWhisperServerError.invalidJSON
        }
        let text = String(describing: payload["text"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let elapsed = Date().timeIntervalSince(started)
        let fallbackReason = serverCompletenessFallbackReason(audioURL: audioURL, text: text)
        let serverRoute = (payload["route"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines)
        let route = fallbackReason.isEmpty
            ? (serverRoute?.isEmpty == false ? serverRoute! : "fast_server_native")
            : "\(serverRoute?.isEmpty == false ? serverRoute! : "fast_server_native")_process_fallback_skipped"
        var quality = qualityFields(audioURL: audioURL, text: text, fallbackReason: fallbackReason)
        if let serverQuality = payload["quality"] as? [String: Any] {
            for (key, value) in jsonValueMap(from: serverQuality) {
                quality[key] = value
            }
        }
        return LocalWhisperServerTranscript(
            text: text,
            rawText: (payload["raw_text"] as? String) ?? text,
            engine: serverEngine(from: payload, fallbackReason: fallbackReason),
            processor: (payload["processor"] as? String) ?? "none",
            fallbackReason: fallbackReason,
            route: route,
            seconds: roundedSeconds(elapsed),
            quality: quality
        )
    }

    private static func serverEngine(from payload: [String: Any], fallbackReason: String) -> String {
        let engine = (payload["engine"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines)
        let base = engine?.isEmpty == false ? engine! : "local.server.native"
        if fallbackReason.isEmpty {
            return base
        }
        return "\(base)|fallback_reason=\(fallbackReason)|process_fallback_skipped"
    }

    private static func multipartBody(audioURL: URL, boundary: String) throws -> Data {
        var body = Data()
        appendFormField(name: "response_format", value: "json", boundary: boundary, to: &body)
        appendFormField(name: "temperature", value: "0.0", boundary: boundary, to: &body)
        appendFormField(name: "translate", value: "true", boundary: boundary, to: &body)
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(audioURL.lastPathComponent)\"\r\n")
        body.append("Content-Type: audio/wav\r\n\r\n")
        body.append(try Data(contentsOf: audioURL))
        body.append("\r\n--\(boundary)--\r\n")
        return body
    }

    private static func appendFormField(name: String, value: String, boundary: String, to body: inout Data) {
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n")
        body.append("\(value)\r\n")
    }

    private static func serverCompletenessFallbackReason(audioURL: URL, text: String) -> String {
        let stripped = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if stripped.isEmpty { return "" }
        guard let duration = audioDurationSeconds(audioURL), duration >= 18.0 else { return "" }
        let chars = stripped.count
        let words = tokenCount(stripped)
        let veryShortChars = chars < max(120, Int(duration * 4.0))
        let veryShortWords = words < max(20, Int(duration * 0.45))
        let prefixLike = chars < Int(duration * 8.0) && words < 55
        if veryShortChars || veryShortWords || prefixLike {
            return "suspected_truncated_server_output:duration=\(String(format: "%.1f", duration)),chars=\(chars),words=\(words)"
        }
        return ""
    }

    private static func qualityFields(audioURL: URL, text: String, fallbackReason: String) -> [String: JSONValue] {
        var quality: [String: JSONValue] = [
            "blank_or_no_speech": .bool(isNoSpeechText(text)),
            "degenerate": .bool(isDegenerateText(text)),
            "char_count": .double(Double(text.count)),
            "route": .string(fallbackReason.isEmpty ? "fast_server_native" : "fast_server_native_process_fallback_skipped")
        ]
        if let duration = audioDurationSeconds(audioURL) {
            quality["audio_duration_seconds"] = .double(roundedSeconds(duration))
        }
        return quality
    }

    private static func jsonValueMap(from payload: [String: Any]) -> [String: JSONValue] {
        var result: [String: JSONValue] = [:]
        for (key, value) in payload {
            if let numberValue = value as? NSNumber {
                if CFGetTypeID(numberValue) == CFBooleanGetTypeID() {
                    result[key] = .bool(numberValue.boolValue)
                } else {
                    result[key] = .double(numberValue.doubleValue)
                }
            } else if let boolValue = value as? Bool {
                result[key] = .bool(boolValue)
            } else if let stringValue = value as? String {
                result[key] = .string(stringValue)
            }
        }
        return result
    }

    private static func isDegenerateText(_ text: String) -> Bool {
        TranscriptQualityPolicy.isDegenerateTranscript(text)
    }

    private static func isNoSpeechText(_ text: String) -> Bool {
        let stripped = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if stripped.isEmpty { return true }
        let lower = stripped.lowercased()
        if lower == "<|nospeech|>" { return true }
        var markerTrimSet = CharacterSet(charactersIn: "[](){}<>._- ")
        markerTrimSet.formUnion(.whitespacesAndNewlines)
        let marker = lower
            .trimmingCharacters(in: markerTrimSet)
            .replacingOccurrences(of: "_", with: " ")
            .split { $0.isWhitespace }
            .joined(separator: " ")
        return [
            "blank audio",
            "blank",
            "silence",
            "silent audio",
            "no speech",
            "no speech detected",
            "inaudible",
            "music",
            "noise"
        ].contains(marker)
    }

    private static func normalizedTokens(_ text: String) -> [String] {
        text.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init)
    }

    private static func tokenCount(_ text: String) -> Int {
        normalizedTokens(text).count
    }

    private static func audioDurationSeconds(_ url: URL) -> Double? {
        guard let file = try? AVAudioFile(forReading: url) else { return nil }
        let sampleRate = file.fileFormat.sampleRate
        guard sampleRate > 0 else { return nil }
        return Double(file.length) / sampleRate
    }

    private static func roundedSeconds(_ value: Double) -> Double {
        (value * 1000).rounded() / 1000
    }

    private static func isLoopbackHost(_ host: String?) -> Bool {
        guard let host = host?.lowercased() else { return false }
        return host == "localhost" || host == "127.0.0.1" || host == "::1"
    }
}

public enum LocalWhisperServerError: Error, CustomStringConvertible {
    case httpStatus(Int)
    case invalidJSON
    case nonLoopbackEndpoint
    case requestTimedOut

    public var description: String {
        switch self {
        case .httpStatus(let status):
            return "whisper.cpp server returned HTTP \(status)"
        case .invalidJSON:
            return "whisper.cpp server returned invalid JSON"
        case .nonLoopbackEndpoint:
            return "whisper.cpp server endpoint must be loopback"
        case .requestTimedOut:
            return "whisper.cpp server request timed out"
        }
    }
}

private extension Data {
    mutating func append(_ value: String) {
        append(Data(value.utf8))
    }
}
