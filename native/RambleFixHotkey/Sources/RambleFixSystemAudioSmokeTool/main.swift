import AVFoundation
import CoreMedia
import Foundation
import RambleFixHotkeyCore
import ScreenCaptureKit

@available(macOS 13.0, *)
final class SmokeSystemAudioRecorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let audioURL: URL
    private let queue = DispatchQueue(label: "com.ramblefix.local.system-audio-smoke")
    private let lock = NSLock()
    private var stream: SCStream?
    private var wavWriter: MonoPCM16WAVWriter?
    private var resampleCursor: Double = 0
    private var acceptingBuffers = false
    private(set) var lastError: String?

    init(audioURL: URL) {
        self.audioURL = audioURL
        super.init()
    }

    func start() {
        SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) { [weak self] content, error in
            guard let self else { return }
            if let error {
                self.recordError("shareable_content: \(error.localizedDescription)")
                return
            }
            guard let display = content?.displays.first else {
                self.recordError("shareable_content: no display")
                return
            }
            let config = SCStreamConfiguration()
            config.width = 2
            config.height = 2
            config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
            config.capturesAudio = true
            config.excludesCurrentProcessAudio = true
            config.sampleRate = 48_000
            config.channelCount = 2
            let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
            let stream = SCStream(filter: filter, configuration: config, delegate: self)
            do {
                try FileManager.default.createDirectory(at: self.audioURL.deletingLastPathComponent(), withIntermediateDirectories: true)
                try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: self.queue)
                self.lock.lock()
                self.stream = stream
                self.acceptingBuffers = true
                self.lock.unlock()
                stream.startCapture { [weak self] error in
                    if let error {
                        self?.recordError("start_capture: \(error.localizedDescription)")
                    }
                }
            } catch {
                self.recordError("start: \(error.localizedDescription)")
            }
        }
    }

    func stop() {
        lock.lock()
        acceptingBuffers = false
        let stream = self.stream
        self.stream = nil
        try? wavWriter?.close()
        wavWriter = nil
        resampleCursor = 0
        lock.unlock()
        guard let stream else { return }
        let semaphore = DispatchSemaphore(value: 0)
        stream.stopCapture { _ in semaphore.signal() }
        _ = semaphore.wait(timeout: .now() + 2.0)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        guard let buffer = pcmBuffer(from: sampleBuffer) else { return }
        write(buffer)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        recordError("stopped: \(error.localizedDescription)")
    }

    private func pcmBuffer(from sampleBuffer: CMSampleBuffer) -> AVAudioPCMBuffer? {
        guard let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
              let streamDescription = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription),
              let inputFormat = AVAudioFormat(streamDescription: streamDescription) else {
            return nil
        }
        let frameCount = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        guard frameCount > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: inputFormat, frameCapacity: frameCount) else {
            return nil
        }
        buffer.frameLength = frameCount
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sampleBuffer,
            at: 0,
            frameCount: Int32(frameCount),
            into: buffer.mutableAudioBufferList
        )
        return status == noErr ? buffer : nil
    }

    private func write(_ buffer: AVAudioPCMBuffer) {
        var writeError: String?
        lock.lock()
        guard acceptingBuffers else {
            lock.unlock()
            return
        }
        do {
            if wavWriter == nil {
                wavWriter = try MonoPCM16WAVWriter(url: audioURL)
            }
            let samples = MonoPCM16Downsampler.samples(from: buffer, cursor: &resampleCursor)
            try wavWriter?.write(samples: samples)
        } catch {
            writeError = "write: \(error.localizedDescription)"
        }
        lock.unlock()
        if let writeError {
            recordError(writeError)
        }
    }

    private func recordError(_ message: String) {
        lock.lock()
        lastError = message
        lock.unlock()
    }
}

@main
struct SystemAudioSmokeTool {
    static func main() {
        guard #available(macOS 13.0, *) else {
            printJSON(["ok": false, "error": "ScreenCaptureKit requires macOS 13+"])
            Foundation.exit(2)
        }
        run()
    }

    @available(macOS 13.0, *)
    private static func run() {
        let args = CommandLine.arguments
        var seconds = 5.0
        var output = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("logs/system_audio_smoke/capture.wav")
        var index = 1
        while index < args.count {
            let arg = args[index]
            if arg == "--seconds", index + 1 < args.count {
                seconds = max(1.0, Double(args[index + 1]) ?? seconds)
                index += 2
            } else if arg == "--output", index + 1 < args.count {
                output = URL(fileURLWithPath: args[index + 1]).standardizedFileURL
                index += 2
            } else {
                index += 1
            }
        }

        try? FileManager.default.removeItem(at: output)
        let recorder = SmokeSystemAudioRecorder(audioURL: output)
        recorder.start()
        RunLoop.current.run(until: Date().addingTimeInterval(seconds))
        recorder.stop()
        let duration = audioDurationSeconds(output) ?? 0
        let size = (try? FileManager.default.attributesOfItem(atPath: output.path)[.size] as? NSNumber)?.intValue ?? 0
        let ok = FileManager.default.fileExists(atPath: output.path) && duration >= 0.5 && recorder.lastError == nil
        printJSON([
            "ok": ok,
            "audio_path": output.path,
            "duration_seconds": roundedSeconds(duration),
            "size_bytes": size,
            "error": recorder.lastError ?? ""
        ])
        Foundation.exit(ok ? 0 : 1)
    }

    private static func audioDurationSeconds(_ url: URL) -> Double? {
        guard let file = try? AVAudioFile(forReading: url) else { return nil }
        let sampleRate = file.fileFormat.sampleRate
        guard sampleRate > 0 else { return nil }
        return Double(file.length) / sampleRate
    }

    private static func roundedSeconds(_ seconds: Double) -> Double {
        (seconds * 1000).rounded() / 1000
    }

    private static func printJSON(_ payload: [String: Any]) {
        let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
        if let data, let text = String(data: data, encoding: .utf8) {
            print(text)
        }
    }
}
