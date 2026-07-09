import Foundation

public enum PasteTargetConfidence {
    case editable
    case ambiguous
    case blocked
}

public enum PasteVerificationStatus {
    case verified
    case failed
    case unverified
}

public enum PasteFocusHeuristics {
    public static func confidence(
        role: String?,
        selectedRangeAvailable: Bool,
        valueIsSettable: Bool
    ) -> PasteTargetConfidence {
        if selectedRangeAvailable || valueIsSettable {
            return .editable
        }
        guard let role = role?.trimmingCharacters(in: .whitespacesAndNewlines),
              !role.isEmpty else {
            return .ambiguous
        }
        return obviouslyNonEditableRoles.contains(role) ? .blocked : .ambiguous
    }

    public static func shouldAttemptPaste(
        role: String?,
        selectedRangeAvailable: Bool,
        valueIsSettable: Bool
    ) -> Bool {
        confidence(
            role: role,
            selectedRangeAvailable: selectedRangeAvailable,
            valueIsSettable: valueIsSettable
        ) != .blocked
    }

    public static func shouldOfferCopyFallback(
        confidence: PasteTargetConfidence,
        verification: PasteVerificationStatus
    ) -> Bool {
        shouldOfferCopyFallback(
            confidence: confidence,
            verification: verification,
            targetBundleID: ""
        )
    }

    public static func shouldOfferCopyFallback(
        confidence: PasteTargetConfidence,
        verification: PasteVerificationStatus,
        targetBundleID: String
    ) -> Bool {
        switch verification {
        case .verified:
            return false
        case .failed:
            return true
        case .unverified:
            if confidence == .ambiguous,
               trustedUnverifiableEditorBundleIDs.contains(targetBundleID) {
                return false
            }
            return confidence != .editable
        }
    }

    public static func trustsUnverifiedPaste(targetBundleID: String) -> Bool {
        trustedUnverifiableEditorBundleIDs.contains(targetBundleID)
    }

    private static let trustedUnverifiableEditorBundleIDs: Set<String> = [
        "com.openai.codex",
        "com.anthropic.claudefordesktop"
    ]

    private static let obviouslyNonEditableRoles: Set<String> = [
        "AXButton",
        "AXCheckBox",
        "AXImage",
        "AXMenuBar",
        "AXMenuItem",
        "AXPopUpButton",
        "AXRadioButton",
        "AXToolbar",
        "AXWindow"
    ]
}
