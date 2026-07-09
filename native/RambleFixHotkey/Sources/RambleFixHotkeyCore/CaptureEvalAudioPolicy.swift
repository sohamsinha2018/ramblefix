import Foundation

public enum CaptureEvalAudioPolicy {
    public static let defaultEnabled = true

    public static func isEnabled(storedValue: Any?) -> Bool {
        guard let storedValue else {
            return defaultEnabled
        }
        if let value = storedValue as? Bool {
            return value
        }
        if let value = storedValue as? NSNumber {
            return value.boolValue
        }
        if let value = storedValue as? String {
            let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            if ["1", "true", "yes", "on"].contains(normalized) {
                return true
            }
            if ["0", "false", "no", "off"].contains(normalized) {
                return false
            }
        }
        return defaultEnabled
    }
}
