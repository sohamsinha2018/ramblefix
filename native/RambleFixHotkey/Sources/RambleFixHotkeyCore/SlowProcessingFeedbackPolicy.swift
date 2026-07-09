import Foundation

public enum SlowProcessingFeedbackPolicy {
    public static let defaultDelaySeconds: TimeInterval = 5.0

    public static func shouldShowFeedback(startedAt: Date, now: Date, delaySeconds: TimeInterval = defaultDelaySeconds) -> Bool {
        now.timeIntervalSince(startedAt) >= delaySeconds
    }
}
