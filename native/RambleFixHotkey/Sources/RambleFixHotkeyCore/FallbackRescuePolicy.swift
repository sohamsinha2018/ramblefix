import Foundation

public enum FallbackRescuePolicy {
    public static func isProcessFallbackSkippedRoute(_ route: String) -> Bool {
        route == "fast_server_process_fallback_skipped"
            || route == "fast_server_native_process_fallback_skipped"
    }

    public static func shouldRun(route: String, fallbackReason: String, audioProbablySilent: Bool) -> Bool {
        isProcessFallbackSkippedRoute(route)
            && !fallbackReason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !audioProbablySilent
    }
}
