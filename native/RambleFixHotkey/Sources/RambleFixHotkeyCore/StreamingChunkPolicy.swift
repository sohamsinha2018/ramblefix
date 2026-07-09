import Foundation

public enum StreamingChunkPolicy {
    public static func shouldCloseChunk(
        elapsedSeconds: Double,
        targetSeconds: Double,
        silenceChunkingEnabled: Bool,
        minSeconds: Double,
        maxSeconds: Double,
        silenceLookaroundSeconds: Double,
        normalizedLevel: Double,
        silenceLevelThreshold: Double
    ) -> Bool {
        if !silenceChunkingEnabled {
            return elapsedSeconds >= targetSeconds
        }
        if elapsedSeconds >= maxSeconds {
            return true
        }
        let earliestQuietBoundary = max(minSeconds, targetSeconds - silenceLookaroundSeconds)
        return elapsedSeconds >= earliestQuietBoundary && normalizedLevel <= silenceLevelThreshold
    }
}
