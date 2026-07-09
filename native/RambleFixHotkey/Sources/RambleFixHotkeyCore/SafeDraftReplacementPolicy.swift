import Foundation

public struct SafeDraftReplacementDecision: Equatable {
    public let range: NSRange?
    public let reason: String

    public init(range: NSRange?, reason: String) {
        self.range = range
        self.reason = reason
    }
}

public enum SafeDraftReplacementPolicy {
    public static func replacementRange(
        value: String,
        selectedLocation: Int,
        selectedLength: Int,
        draft: String
    ) -> NSRange? {
        replacementDecision(
            value: value,
            selectedLocation: selectedLocation,
            selectedLength: selectedLength,
            draft: draft
        ).range
    }

    public static func replacementDecision(
        value: String,
        selectedLocation: Int,
        selectedLength: Int,
        draft: String
    ) -> SafeDraftReplacementDecision {
        guard !draft.isEmpty else {
            return SafeDraftReplacementDecision(range: nil, reason: "empty_draft")
        }

        let valueNSString = value as NSString
        let draftNSString = draft as NSString
        let draftLength = draftNSString.length
        let valueLength = valueNSString.length

        guard selectedLocation >= 0,
              selectedLength >= 0,
              selectedLocation + selectedLength <= valueLength else {
            return SafeDraftReplacementDecision(range: nil, reason: "invalid_selection")
        }

        if selectedLength > 0 {
            let selectedRange = NSRange(location: selectedLocation, length: selectedLength)
            let selectedText = valueNSString.substring(with: selectedRange)
            if selectedText == draft {
                return SafeDraftReplacementDecision(range: selectedRange, reason: "selected_draft_match")
            }
            return SafeDraftReplacementDecision(range: nil, reason: "selection_not_draft")
        }

        if selectedLocation >= draftLength {
            let draftStart = selectedLocation - draftLength
            if draftStart >= 0, draftStart + draftLength <= valueLength {
                let currentSuffix = valueNSString.substring(with: NSRange(location: draftStart, length: draftLength))
                if currentSuffix == draft {
                    return SafeDraftReplacementDecision(
                        range: NSRange(location: draftStart, length: draftLength),
                        reason: "cursor_suffix_match"
                    )
                }
            }
        }

        let first = valueNSString.range(of: draft)
        guard first.location != NSNotFound else {
            return SafeDraftReplacementDecision(range: nil, reason: "draft_not_found")
        }
        let afterFirst = first.location + first.length
        let remainingLength = max(0, valueLength - afterFirst)
        if remainingLength > 0 {
            let second = valueNSString.range(of: draft, options: [], range: NSRange(location: afterFirst, length: remainingLength))
            if second.location != NSNotFound {
                return SafeDraftReplacementDecision(range: nil, reason: "multiple_draft_matches")
            }
        }
        return SafeDraftReplacementDecision(range: first, reason: "unique_draft_match")
    }
}
