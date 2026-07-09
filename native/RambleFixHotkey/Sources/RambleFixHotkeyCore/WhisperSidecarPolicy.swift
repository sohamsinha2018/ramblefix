import Foundation

public enum WhisperSidecarPolicy {
    public static let nativeASRPort = 8188
    public static let legacyWhisperPort = 8178
    public static let defaultAutostartEnabled = false
    public static let defaultStartupWaitSeconds = 4.0
    public static let defaultStartCooldownSeconds = 12.0

    public static func shouldAutostartLegacySidecar(
        endpointPort: Int?,
        autostartEnabled: Bool
    ) -> Bool {
        guard autostartEnabled else { return false }
        return (endpointPort ?? legacyWhisperPort) != nativeASRPort
    }

    public static func shouldThrottleStartAttempt(
        lastAttemptAt: Date?,
        now: Date,
        cooldownSeconds: TimeInterval = defaultStartCooldownSeconds
    ) -> Bool {
        guard let lastAttemptAt else { return false }
        return now.timeIntervalSince(lastAttemptAt) < cooldownSeconds
    }
}
