import Foundation

public enum HUDSignalStylePolicy {
    public static let audioBarCount = 15
    public static let audioBarWidth = 2.4
    public static let audioBarGap = 3.2
    public static let englishMotionVariant = 0
    public static let normalMotionVariantCount = 4
    public static let recordingSquiggleVariant = 0
    public static let motionVariantCount = 9
    public static let hindiMotionVariant = 8
    public static let visualOnlyPillWidth = 96.0
    public static let visualOnlyPillHeight = 26.0
    public static let statusPillWidth = 236.0
    public static let copyPillWidth = 284.0
    public static let textPillHeight = 30.0
    public static let toastHorizontalPadding = 14.0
    public static let toastActionWidth = 48.0
    public static let toastActionGap = 8.0
    public static let toastTextFontSize = 11.5
    public static let toastActionFontSize = 11.5
    public static let englishProcessingLaneCount = 1
    public static let hindiProcessingLaneCount = 2
    public static let adaptiveSignalContrastEnabled = true
    public static let signalShadowAlpha = 0.38
    public static let signalShadowBlurRadius = 4.0
    public static let loadedSystemLoadRatio = 0.75
    public static let busySystemLoadRatio = 1.15
    public static let healthyHueBase = 0.52
    public static let warningHueBase = 0.12
    public static let dangerHueBase = 0.98

    public static var audioWaveVisualWidth: Double {
        Double(audioBarCount) * audioBarWidth + Double(audioBarCount - 1) * audioBarGap
    }

    public static var workPrimaryStrokeWidth: Double {
        audioBarWidth
    }

    public static func isVisualOnlyState(_ state: String) -> Bool {
        state == "REC" || state == "WORK"
    }

    public static func usesGlassBackground(state: String) -> Bool {
        !isVisualOnlyState(state)
    }
}
