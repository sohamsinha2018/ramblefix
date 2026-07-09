import AVFoundation
import Foundation

public final class MonoPCM16WAVWriter {
    private let url: URL
    private let sampleRate: UInt32
    private let handle: FileHandle
    private var dataBytes: UInt32 = 0
    private var closed = false

    public init(url: URL, sampleRate: UInt32 = 16_000) throws {
        self.url = url
        self.sampleRate = sampleRate
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: url.path, contents: nil)
        handle = try FileHandle(forWritingTo: url)
        try handle.write(contentsOf: Self.header(sampleRate: sampleRate, dataBytes: 0))
    }

    deinit {
        try? close()
    }

    public func write(samples: [Int16]) throws {
        guard !closed, !samples.isEmpty else { return }
        var data = Data(capacity: samples.count * 2)
        for sample in samples {
            var littleEndian = sample.littleEndian
            withUnsafeBytes(of: &littleEndian) { data.append(contentsOf: $0) }
        }
        try handle.write(contentsOf: data)
        dataBytes += UInt32(data.count)
    }

    public func close() throws {
        guard !closed else { return }
        closed = true
        try handle.seek(toOffset: 0)
        try handle.write(contentsOf: Self.header(sampleRate: sampleRate, dataBytes: dataBytes))
        try handle.close()
    }

    private static func header(sampleRate: UInt32, dataBytes: UInt32) -> Data {
        var data = Data()
        data.append(contentsOf: [0x52, 0x49, 0x46, 0x46])
        appendUInt32(36 + dataBytes, to: &data)
        data.append(contentsOf: [0x57, 0x41, 0x56, 0x45])
        data.append(contentsOf: [0x66, 0x6d, 0x74, 0x20])
        appendUInt32(16, to: &data)
        appendUInt16(1, to: &data)
        appendUInt16(1, to: &data)
        appendUInt32(sampleRate, to: &data)
        appendUInt32(sampleRate * 2, to: &data)
        appendUInt16(2, to: &data)
        appendUInt16(16, to: &data)
        data.append(contentsOf: [0x64, 0x61, 0x74, 0x61])
        appendUInt32(dataBytes, to: &data)
        return data
    }

    private static func appendUInt16(_ value: UInt16, to data: inout Data) {
        var littleEndian = value.littleEndian
        withUnsafeBytes(of: &littleEndian) { data.append(contentsOf: $0) }
    }

    private static func appendUInt32(_ value: UInt32, to data: inout Data) {
        var littleEndian = value.littleEndian
        withUnsafeBytes(of: &littleEndian) { data.append(contentsOf: $0) }
    }
}

public enum MonoPCM16Downsampler {
    public static func samples(
        from buffer: AVAudioPCMBuffer,
        outputSampleRate: Double = 16_000,
        cursor: inout Double
    ) -> [Int16] {
        let frameCount = Int(buffer.frameLength)
        guard frameCount > 0, buffer.format.sampleRate > 0 else { return [] }
        let channelCount = max(1, Int(buffer.format.channelCount))
        let step = buffer.format.sampleRate / outputSampleRate
        var output: [Int16] = []
        var position = cursor
        while position < Double(frameCount) {
            let frame = min(frameCount - 1, max(0, Int(position)))
            let sample = monoSample(buffer: buffer, frame: frame, channelCount: channelCount)
            output.append(floatToInt16(sample))
            position += step
        }
        cursor = max(0, position - Double(frameCount))
        return output
    }

    private static func monoSample(buffer: AVAudioPCMBuffer, frame: Int, channelCount: Int) -> Float {
        var sum: Float = 0
        for channel in 0..<channelCount {
            sum += sample(buffer: buffer, frame: frame, channel: channel, channelCount: channelCount)
        }
        return sum / Float(channelCount)
    }

    private static func sample(buffer: AVAudioPCMBuffer, frame: Int, channel: Int, channelCount: Int) -> Float {
        switch buffer.format.commonFormat {
        case .pcmFormatFloat32:
            if buffer.format.isInterleaved,
               let pointer = buffer.audioBufferList.pointee.mBuffers.mData?.assumingMemoryBound(to: Float.self) {
                return pointer[(frame * channelCount) + channel]
            }
            guard let data = buffer.floatChannelData else { return 0 }
            return data[min(channel, Int(buffer.format.channelCount) - 1)][frame]
        case .pcmFormatInt16:
            if buffer.format.isInterleaved,
               let pointer = buffer.audioBufferList.pointee.mBuffers.mData?.assumingMemoryBound(to: Int16.self) {
                return Float(pointer[(frame * channelCount) + channel]) / 32768.0
            }
            guard let data = buffer.int16ChannelData else { return 0 }
            return Float(data[min(channel, Int(buffer.format.channelCount) - 1)][frame]) / 32768.0
        default:
            return 0
        }
    }

    private static func floatToInt16(_ sample: Float) -> Int16 {
        let clamped = max(-1.0, min(1.0, sample))
        return Int16(clamped * 32767.0)
    }
}
