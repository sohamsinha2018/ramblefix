import Foundation

public enum RecordingDurationPolicy {
    public static let defaultDictationMaxSeconds: TimeInterval = 900
    public static let defaultMeetingMaxSeconds: TimeInterval = 7200

    public static func normalizedMaxSeconds(from value: String?, defaultSeconds: TimeInterval) -> TimeInterval {
        guard let value,
              let seconds = TimeInterval(value),
              seconds > 0 else {
            return defaultSeconds
        }
        return seconds
    }
}
