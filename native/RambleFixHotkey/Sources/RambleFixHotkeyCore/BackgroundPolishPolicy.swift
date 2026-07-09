import Foundation

public enum BackgroundPolishAction: String, Equatable {
    case fallbackRescue
    case processSecondPass
    case termPolish
    case structure
    case finalizer
    case hindiPolish
}

public enum BackgroundPolishPolicy {
    public static let defaultOrder: [BackgroundPolishAction] = [
        .fallbackRescue,
        .structure,
        .termPolish,
        .hindiPolish,
        .processSecondPass,
        .finalizer,
    ]
}

public enum BackgroundPolishToastPolicy {
    public static func shouldShowCopyFallback(draftWasPasted: Bool, replacementSucceeded: Bool) -> Bool {
        !draftWasPasted && !replacementSucceeded
    }
}
