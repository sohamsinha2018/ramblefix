import Foundation
import RambleFixHotkeyCore

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(2)
}

var audioPath: String?
var endpoint = URL(string: "http://127.0.0.1:8188/inference")!
var timeout: TimeInterval = 20.0
var args = Array(CommandLine.arguments.dropFirst())
while !args.isEmpty {
    let arg = args.removeFirst()
    switch arg {
    case "--audio":
        guard !args.isEmpty else { fail("--audio requires a path") }
        audioPath = args.removeFirst()
    case "--endpoint":
        guard !args.isEmpty, let url = URL(string: args.removeFirst()) else { fail("--endpoint requires a URL") }
        endpoint = url
    case "--timeout":
        guard !args.isEmpty, let value = Double(args.removeFirst()) else { fail("--timeout requires seconds") }
        timeout = value
    default:
        fail("unknown argument: \(arg)")
    }
}
guard let audioPath else { fail("--audio is required") }

do {
    let transcript = try LocalWhisperServerClient.transcribe(
        audioURL: URL(fileURLWithPath: audioPath),
        endpoint: endpoint,
        timeout: timeout
    )
    var quality: [String: Any] = [:]
    for (key, value) in transcript.quality {
        switch value {
        case .string(let string):
            quality[key] = string
        case .double(let double):
            quality[key] = double
        case .bool(let bool):
            quality[key] = bool
        }
    }
    let payload: [String: Any] = [
        "raw_text": transcript.rawText,
        "text": transcript.text,
        "engine": transcript.engine,
        "processor": transcript.processor,
        "fallback_reason": transcript.fallbackReason,
        "route": transcript.route,
        "seconds": transcript.seconds,
        "quality": quality
    ]
    let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    fail(String(describing: error))
}
