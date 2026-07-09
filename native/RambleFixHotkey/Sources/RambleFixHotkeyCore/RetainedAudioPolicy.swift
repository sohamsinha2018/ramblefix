import Foundation

public enum RetainedAudioPolicy {
    public static let defaultMaxSuccessfulHotkeyClips = 200

    public static func normalizedLimit(from raw: String?, defaultValue: Int = defaultMaxSuccessfulHotkeyClips) -> Int {
        guard let raw,
              let parsed = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)),
              parsed > 0 else {
            return max(1, defaultValue)
        }
        return parsed
    }

    public static func filesToPrune(
        in directory: URL,
        keepingMax limit: Int,
        fileManager: FileManager = .default
    ) -> [URL] {
        let keep = max(1, limit)
        guard let urls = try? fileManager.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.contentModificationDateKey, .isRegularFileKey],
            options: [.skipsHiddenFiles]
        ) else {
            return []
        }
        let wavs = urls.filter { url in
            guard url.pathExtension.lowercased() == "wav",
                  let values = try? url.resourceValues(forKeys: [.isRegularFileKey]),
                  values.isRegularFile == true else {
                return false
            }
            return true
        }
        guard wavs.count > keep else { return [] }
        return wavs
            .sorted { lhs, rhs in
                let leftDate = (try? lhs.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                let rightDate = (try? rhs.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
                if leftDate == rightDate {
                    return lhs.lastPathComponent < rhs.lastPathComponent
                }
                return leftDate < rightDate
            }
            .prefix(wavs.count - keep)
            .map { $0 }
    }
}
