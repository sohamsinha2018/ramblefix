import Foundation

public enum StreamingCaptureDefaults {
    public static let dictationEnabled = false
    public static let targetChunkSeconds = 4.0
    public static let silenceChunkingEnabled = true
    public static let minChunkSeconds = 2.0
    public static let maxChunkSeconds = 5.0
    public static let silenceLookaroundSeconds = 1.5
    public static let silenceLevelThreshold = 0.08
}
