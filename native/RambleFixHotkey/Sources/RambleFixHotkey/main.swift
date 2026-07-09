import AppKit
import ApplicationServices
import AVFoundation
import Carbon
import CoreMedia
import Darwin
import RambleFixHotkeyCore
#if RAMBLEFIX_MEETING_MODE
import ScreenCaptureKit
#endif

private func resolvedAppName() -> String {
    if let name = Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String,
       !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        return name
    }
    return "RambleFix Local"
}

private let appName = resolvedAppName()
private let menuBarIdleTitle = appName == "DictaHue" ? "DH" : "RF Local"
private let hudDefaultMotionFrameInterval: TimeInterval = 0.06
private let hudPressureMotionFrameInterval: TimeInterval = 0.10
private let hudMotionVariantCount = HUDSignalStylePolicy.motionVariantCount
private let hudHueSpan: CGFloat = 0.24
private let captureEvalAudioDefaultsKey = "ramblefix.captureEvalAudio"
private let hotkeyRetainedAudioLimitEnv = "RAMBLEFIX_HOTKEY_RETAIN_AUDIO_LIMIT"
private let meetingModeEnabled = envFlag("RAMBLEFIX_ENABLE_MEETING_MODE", defaultValue: false)
private let hudScreenRefractionEnabled = runtimeFlag("RAMBLEFIX_HUD_SCREEN_REFRACTION", defaultValue: false)

private func rfRGB(_ red: CGFloat, _ green: CGFloat, _ blue: CGFloat, alpha: CGFloat = 1.0) -> NSColor {
    NSColor(calibratedRed: red / 255.0, green: green / 255.0, blue: blue / 255.0, alpha: alpha)
}

private enum RFTheme {
    static let ink = rfRGB(16, 17, 22)
    static let paper = rfRGB(247, 248, 252)
    static let paperCard = rfRGB(255, 255, 255)
    static let coral = rfRGB(255, 62, 91)
    static let mint = rfRGB(181, 255, 88)
    static let cyan = rfRGB(39, 226, 255)
    static let amber = rfRGB(255, 202, 69)
    static let violet = rfRGB(183, 139, 255)
    static let textOnInk = NSColor.white.withAlphaComponent(0.96)
    static let mutedOnInk = NSColor.white.withAlphaComponent(0.62)
    static let textOnAccent = rfRGB(17, 18, 22, alpha: 0.88)
    static let toastFill = rfRGB(248, 249, 252, alpha: 0.72)
    static let toastStroke = rfRGB(255, 255, 255, alpha: 0.46)
    static let toastText = rfRGB(20, 22, 28, alpha: 0.88)
    static let toastAction = rfRGB(20, 22, 28, alpha: 0.72)
    static let glassFill = rfRGB(255, 255, 255, alpha: 0.035)
    static let glassStroke = rfRGB(255, 255, 255, alpha: 0.18)
    static let glassInnerStroke = rfRGB(255, 255, 255, alpha: 0.12)
}

final class RFLiquidGlassOverlayView: NSView {
    var accent = RFTheme.cyan {
        didSet {
            if !accent.isEqual(oldValue) {
                needsDisplay = true
            }
        }
    }
    var isCompact = true {
        didSet {
            if isCompact != oldValue {
                needsDisplay = true
            }
        }
    }

    override var isFlipped: Bool { false }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let rect = bounds.insetBy(dx: 0.6, dy: 0.6)
        guard rect.width > 4, rect.height > 4 else { return }
        let radius = rect.height / 2
        let capsule = NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
        capsule.addClip()

        let glassGradient = NSGradient(colors: [
            NSColor.white.withAlphaComponent(isCompact ? 0.14 : 0.11),
            accent.withAlphaComponent(isCompact ? 0.032 : 0.026),
            NSColor.black.withAlphaComponent(isCompact ? 0.070 : 0.055)
        ])
        glassGradient?.draw(in: rect, angle: 118)

        let centerLens = NSBezierPath(roundedRect: rect.insetBy(dx: rect.height * 0.28, dy: 3.0), xRadius: max(radius - 3.0, 1), yRadius: max(radius - 3.0, 1))
        let lensGradient = NSGradient(colors: [
            NSColor.white.withAlphaComponent(isCompact ? 0.060 : 0.045),
            NSColor.white.withAlphaComponent(0.0),
            NSColor.black.withAlphaComponent(isCompact ? 0.060 : 0.045)
        ])
        lensGradient?.draw(in: centerLens, angle: 92)

        let sideLens = NSBezierPath(roundedRect: rect.insetBy(dx: 1.6, dy: 1.6), xRadius: max(radius - 1.6, 1), yRadius: max(radius - 1.6, 1))
        accent.withAlphaComponent(isCompact ? 0.020 : 0.016).setFill()
        sideLens.fill()

        let caustic = NSBezierPath()
        caustic.lineWidth = isCompact ? 0.72 : 0.62
        caustic.lineCapStyle = .round
        caustic.lineJoinStyle = .round
        caustic.move(to: NSPoint(x: rect.minX + radius * 0.78, y: rect.maxY - 3.3))
        caustic.curve(
            to: NSPoint(x: rect.maxX - radius * 0.58, y: rect.maxY - 3.9),
            controlPoint1: NSPoint(x: rect.midX - rect.width * 0.20, y: rect.maxY - 1.7),
            controlPoint2: NSPoint(x: rect.midX + rect.width * 0.24, y: rect.maxY - 1.8)
        )
        NSColor.white.withAlphaComponent(isCompact ? 0.46 : 0.34).setStroke()
        caustic.stroke()

        let lowerGlow = NSBezierPath(roundedRect: rect.insetBy(dx: 3, dy: 3), xRadius: max(radius - 3, 1), yRadius: max(radius - 3, 1))
        accent.withAlphaComponent(isCompact ? 0.055 : 0.040).setStroke()
        lowerGlow.lineWidth = 0.55
        lowerGlow.stroke()

        let lowerRefraction = NSBezierPath()
        lowerRefraction.lineWidth = 0.62
        lowerRefraction.lineCapStyle = .round
        lowerRefraction.move(to: NSPoint(x: rect.minX + radius * 0.82, y: rect.minY + 3.0))
        lowerRefraction.curve(
            to: NSPoint(x: rect.maxX - radius * 0.72, y: rect.minY + 2.8),
            controlPoint1: NSPoint(x: rect.midX - rect.width * 0.18, y: rect.minY + 1.0),
            controlPoint2: NSPoint(x: rect.midX + rect.width * 0.18, y: rect.minY + 1.2)
        )
        NSColor.black.withAlphaComponent(isCompact ? 0.18 : 0.13).setStroke()
        lowerRefraction.stroke()

        let border = NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
        border.lineWidth = 0.38
        NSColor.white.withAlphaComponent(isCompact ? 0.32 : 0.24).setStroke()
        border.stroke()
    }
}

final class RFRefractedBackdropGlassView: NSView {
    var accent = RFTheme.cyan {
        didSet { needsDisplay = true }
    }
    var isCompact = true {
        didSet { needsDisplay = true }
    }

    private(set) var hasBackdrop = false
    private var backdropImage: NSImage?
    private var lastRenderCostMs: Double = 0

    override var isFlipped: Bool { false }

    func clearBackdrop() {
        hasBackdrop = false
        backdropImage = nil
        needsDisplay = true
    }

    @discardableResult
    func refreshBackdrop(for panel: NSPanel, accent: NSColor, isCompact: Bool) -> Bool {
        self.accent = accent
        self.isCompact = isCompact
        guard panel.frame.width > 12, panel.frame.height > 12 else {
            clearBackdrop()
            return false
        }

        guard CGPreflightScreenCaptureAccess() else {
            clearBackdrop()
            return false
        }

        let start = DispatchTime.now().uptimeNanoseconds
        guard let capture = captureBackdrop(for: panel),
              let image = refractBackdrop(capture, targetSize: panel.frame.size) else {
            clearBackdrop()
            return false
        }
        backdropImage = image
        hasBackdrop = true
        lastRenderCostMs = Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000.0
        needsDisplay = true
        return true
    }

    private func captureBackdrop(for panel: NSPanel) -> CGImage? {
        let screen = panel.screen ?? NSScreen.main ?? NSScreen.screens.first
        let screenFrame = screen?.frame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let panelFrame = panel.frame
        let captureRect = CGRect(
            x: panelFrame.minX,
            y: screenFrame.maxY - panelFrame.maxY,
            width: panelFrame.width,
            height: panelFrame.height
        )
        guard captureRect.width > 1, captureRect.height > 1 else { return nil }

        if panel.isVisible, panel.windowNumber > 0 {
            return CGWindowListCreateImage(
                captureRect,
                .optionOnScreenBelowWindow,
                CGWindowID(panel.windowNumber),
                [.boundsIgnoreFraming, .bestResolution]
            )
        }

        return CGWindowListCreateImage(
            captureRect,
            .optionOnScreenOnly,
            kCGNullWindowID,
            [.boundsIgnoreFraming, .bestResolution]
        )
    }

    private func refractBackdrop(_ image: CGImage, targetSize: NSSize) -> NSImage? {
        let width = max(image.width, 1)
        let height = max(image.height, 1)
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue)
        var source = [UInt8](repeating: 0, count: height * bytesPerRow)
        let drawn = source.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(
                data: buffer.baseAddress,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: bytesPerRow,
                space: colorSpace,
                bitmapInfo: bitmapInfo.rawValue
            ) else { return false }
            context.interpolationQuality = .high
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { return nil }

        var output = [UInt8](repeating: 0, count: height * bytesPerRow)
        let radius = Double(height) / 2.0
        let halfWidth = Double(width) / 2.0
        let halfHeight = Double(height) / 2.0
        let capsuleHalfLine = max(0.0, halfWidth - radius)
        let rimBand = max(8.0, min(radius * 0.85, 26.0))
        let maxBend = max(3.0, min(Double(height) * 0.18, 10.0))

        for y in 0..<height {
            for x in 0..<width {
                let cx = Double(x) - halfWidth
                let cy = Double(y) - halfHeight
                let clampedX = min(max(cx, -capsuleHalfLine), capsuleHalfLine)
                let normalX = cx - clampedX
                let normalY = cy
                let normalLength = max(0.0001, sqrt(normalX * normalX + normalY * normalY))
                let nx = normalX / normalLength
                let ny = normalY / normalLength
                let inwardDistance = max(0.0, radius - normalLength)
                let rim = max(0.0, min(1.0, 1.0 - inwardDistance / rimBand))
                let centerEase = max(0.0, min(1.0, inwardDistance / max(radius, 1.0)))
                let bend = pow(rim, 2.1) * maxBend - pow(centerEase, 1.8) * 1.2
                let sx = min(max(Int(round(Double(x) + nx * bend)), 0), width - 1)
                let sy = min(max(Int(round(Double(y) + ny * bend)), 0), height - 1)
                let sourceIndex = sy * bytesPerRow + sx * bytesPerPixel
                let outputIndex = y * bytesPerRow + x * bytesPerPixel

                let edgeShade = UInt8(max(0, min(255, 255.0 - rim * 16.0)))
                output[outputIndex] = UInt8((UInt16(source[sourceIndex]) * UInt16(edgeShade)) / 255)
                output[outputIndex + 1] = UInt8((UInt16(source[sourceIndex + 1]) * UInt16(edgeShade)) / 255)
                output[outputIndex + 2] = UInt8((UInt16(source[sourceIndex + 2]) * UInt16(edgeShade)) / 255)
                output[outputIndex + 3] = 255
            }
        }

        guard let provider = CGDataProvider(data: Data(output) as CFData),
              let refracted = CGImage(
                width: width,
                height: height,
                bitsPerComponent: 8,
                bitsPerPixel: 32,
                bytesPerRow: bytesPerRow,
                space: colorSpace,
                bitmapInfo: bitmapInfo,
                provider: provider,
                decode: nil,
                shouldInterpolate: true,
                intent: .defaultIntent
              ) else { return nil }
        return NSImage(cgImage: refracted, size: targetSize)
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let rect = bounds.insetBy(dx: 0.45, dy: 0.45)
        guard rect.width > 4, rect.height > 4 else { return }
        let radius = rect.height / 2.0
        let capsule = NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
        capsule.addClip()

        if let backdropImage {
            backdropImage.draw(in: rect, from: .zero, operation: .sourceOver, fraction: 0.94)
        } else {
            NSColor.white.withAlphaComponent(0.06).setFill()
            capsule.fill()
        }

        let shade = NSGradient(colors: [
            NSColor.white.withAlphaComponent(isCompact ? 0.16 : 0.12),
            accent.withAlphaComponent(isCompact ? 0.045 : 0.035),
            NSColor.black.withAlphaComponent(isCompact ? 0.18 : 0.14)
        ])
        shade?.draw(in: rect, angle: 116)

        let topArc = NSBezierPath()
        topArc.lineWidth = isCompact ? 0.82 : 0.72
        topArc.lineCapStyle = .round
        topArc.move(to: NSPoint(x: rect.minX + radius * 0.72, y: rect.maxY - 3.2))
        topArc.curve(
            to: NSPoint(x: rect.maxX - radius * 0.68, y: rect.maxY - 4.0),
            controlPoint1: NSPoint(x: rect.midX - rect.width * 0.20, y: rect.maxY - 1.1),
            controlPoint2: NSPoint(x: rect.midX + rect.width * 0.22, y: rect.maxY - 1.2)
        )
        NSColor.white.withAlphaComponent(isCompact ? 0.62 : 0.48).setStroke()
        topArc.stroke()

        let lowerArc = NSBezierPath()
        lowerArc.lineWidth = 0.68
        lowerArc.lineCapStyle = .round
        lowerArc.move(to: NSPoint(x: rect.minX + radius * 0.80, y: rect.minY + 3.0))
        lowerArc.curve(
            to: NSPoint(x: rect.maxX - radius * 0.74, y: rect.minY + 2.8),
            controlPoint1: NSPoint(x: rect.midX - rect.width * 0.18, y: rect.minY + 0.8),
            controlPoint2: NSPoint(x: rect.midX + rect.width * 0.18, y: rect.minY + 1.1)
        )
        NSColor.black.withAlphaComponent(isCompact ? 0.24 : 0.18).setStroke()
        lowerArc.stroke()

        let rim = NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
        rim.lineWidth = 0.45
        RFTheme.glassStroke.setStroke()
        rim.stroke()
    }
}

final class RFHUDSignalView: NSView {
    var state = "REC" {
        didSet { needsDisplay = true }
    }
    var accent = RFTheme.coral {
        didSet { needsDisplay = true }
    }
    var level: CGFloat = 0.35 {
        didSet { needsDisplay = true }
    }
    var phase: CGFloat = 0 {
        didSet { needsDisplay = true }
    }
    var colorShift: CGFloat = 0 {
        didSet { needsDisplay = true }
    }
    var variant: Int = 0 {
        didSet { needsDisplay = true }
    }
    var recipeSeed: CGFloat = 0 {
        didSet { needsDisplay = true }
    }

    override var isFlipped: Bool { false }

    private var audioBarCount: Int { HUDSignalStylePolicy.audioBarCount }
    private var audioBarWidth: CGFloat { CGFloat(HUDSignalStylePolicy.audioBarWidth) }
    private var audioBarGap: CGFloat { CGFloat(HUDSignalStylePolicy.audioBarGap) }
    private var signalVisualWidth: CGFloat { CGFloat(HUDSignalStylePolicy.audioWaveVisualWidth) }
    private var workStrokeWidth: CGFloat { CGFloat(HUDSignalStylePolicy.workPrimaryStrokeWidth) }

    private var signalRect: NSRect {
        let width = min(signalVisualWidth, max(bounds.width - 8, 1))
        return NSRect(x: bounds.midX - width / 2, y: bounds.minY, width: width, height: bounds.height)
    }

    private func recipeValue(_ salt: CGFloat) -> CGFloat {
        let raw = sin((recipeSeed + salt) * 12.9898) * 43758.5453
        return raw - floor(raw)
    }

    private func recipeSigned(_ salt: CGFloat) -> CGFloat {
        recipeValue(salt) * 2.0 - 1.0
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        if HUDSignalStylePolicy.adaptiveSignalContrastEnabled {
            drawSignalWithAdaptiveContrast()
        }
        drawSignalArtwork()
    }

    private func drawSignalWithAdaptiveContrast() {
        guard let context = NSGraphicsContext.current else {
            drawSignalArtwork()
            return
        }
        context.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(CGFloat(HUDSignalStylePolicy.signalShadowAlpha))
        shadow.shadowBlurRadius = CGFloat(HUDSignalStylePolicy.signalShadowBlurRadius)
        shadow.shadowOffset = .zero
        shadow.set()
        drawSignalArtwork()
        context.restoreGraphicsState()
    }

    private func drawSignalArtwork() {
        switch state {
        case "WORK":
            drawThinkingWave()
        default:
            drawAudioWave()
        }
    }

    private func drawAudioWave() {
        let rect = signalRect
        let maxHeight = bounds.height - 5
        let normalVariantCount = max(HUDSignalStylePolicy.normalMotionVariantCount, 1)
        let style = max(0, variant) % normalVariantCount
        switch style {
        case 1:
            drawAudioBarWave(in: rect, maxHeight: maxHeight)
        case 2:
            drawAudioMirrorBars(in: rect, maxHeight: maxHeight)
        case HUDSignalStylePolicy.recordingSquiggleVariant:
            drawAudioMirroredSquiggle(in: rect, maxHeight: maxHeight)
        default:
            drawAudioDotWave(in: rect, maxHeight: maxHeight)
        }
    }

    private func drawAudioBarWave(in rect: NSRect, maxHeight: CGFloat) {
        let denominator = CGFloat(max(audioBarCount - 1, 1))
        let frequency = 0.64 + recipeValue(0.11) * 0.24
        let secondary = 1.6 + recipeValue(0.17) * 1.1
        for index in 0..<audioBarCount {
            let progress = CGFloat(index) / denominator
            let wave = 0.50
                + sin(CGFloat(index) * frequency + phase) * 0.34
                + sin(progress * .pi * secondary - phase * 0.42) * 0.16
            let shapedWave = max(0.0, min(1.0, wave))
            let loudness = max(0.18, min(1.0, level))
            let quietLift = 0.10 + 0.04 * (sin(phase * 0.44 + CGFloat(index) * 0.37) + 1.0) / 2.0
            let barHeight = 4 + maxHeight * (quietLift + 0.72 * loudness * shapedWave)
            let x = rect.minX + CGFloat(index) * (audioBarWidth + audioBarGap)
            let rect = NSRect(x: x, y: bounds.midY - barHeight / 2, width: audioBarWidth, height: barHeight)
            dynamicColor(offset: CGFloat(index) * 0.064, alpha: index % 2 == 0 ? 0.95 : 0.72).setFill()
            NSBezierPath(roundedRect: rect, xRadius: audioBarWidth / 2, yRadius: audioBarWidth / 2).fill()
        }
    }

    private func drawAudioRibbonWave(in rect: NSRect, maxHeight: CGFloat) {
        let width = max(rect.width, 1)
        let loudness = max(0.18, min(1.0, level))
        let baseFrequency = 2.35 + recipeValue(0.21) * 0.75
        let detail = 5.2 + recipeValue(0.24) * 2.2
        for layer in 0..<4 {
            let path = NSBezierPath()
            path.lineWidth = max(1.2, audioBarWidth * (layer == 3 ? 0.95 : 0.56))
            path.lineCapStyle = .round
            path.lineJoinStyle = .round
            var x = rect.minX
            let phaseOffset = CGFloat(layer) * 0.72 + recipeSigned(0.28 + CGFloat(layer) * 0.11) * 0.22
            path.move(to: NSPoint(x: x, y: rect.midY))
            while x <= rect.maxX {
                let progress = (x - rect.minX) / width
                let amplitude = (2.6 + maxHeight * 0.18 * loudness) * (0.64 + CGFloat(layer) * 0.13)
                let y = rect.midY
                    + sin(progress * .pi * (baseFrequency + CGFloat(layer) * 0.18) + phase * 0.86 + phaseOffset) * amplitude
                    + sin(progress * .pi * (detail + CGFloat(layer) * 0.55) - phase * 0.34) * 0.7
                path.line(to: NSPoint(x: x, y: y))
                x += 2.2
            }
            let alpha: CGFloat = layer == 3 ? 0.88 : 0.18 + CGFloat(layer) * 0.11
            dynamicColor(offset: 0.12 + CGFloat(layer) * 0.14, alpha: alpha).setStroke()
            path.stroke()
        }
    }

    private func drawAudioDotWave(in rect: NSRect, maxHeight: CGFloat) {
        let count = audioBarCount + 1
        let width = max(rect.width, 1)
        let loudness = max(0.18, min(1.0, level))
        let frequency = 3.0 + recipeValue(0.31) * 1.4
        let drift = 0.78 + recipeValue(0.34) * 0.32
        for index in 0..<count {
            let progress = CGFloat(index) / CGFloat(max(count - 1, 1))
            let energy = (sin(progress * .pi * frequency + phase * 1.1) + 1.0) / 2.0
            let y = rect.midY
                + sin(progress * .pi * 3.5 - phase * drift) * (3.0 + maxHeight * 0.20 * loudness)
            let size = audioBarWidth * (0.9 + energy * 1.05)
            let x = rect.minX + progress * width
            let dot = NSRect(x: x - size / 2, y: y - size / 2, width: size, height: size)
            dynamicColor(offset: 0.10 + progress * 0.65, alpha: 0.42 + energy * 0.48).setFill()
            NSBezierPath(ovalIn: dot).fill()
        }
    }

    private func drawAudioMirrorBars(in rect: NSRect, maxHeight: CGFloat) {
        let denominator = CGFloat(max(audioBarCount - 1, 1))
        let loudness = max(0.18, min(1.0, level))
        let sweep = 0.42 + recipeValue(0.41) * 0.20
        let pulseWidth = 1.8 + recipeValue(0.43) * 0.8
        for index in 0..<audioBarCount {
            let progress = CGFloat(index) / denominator
            let centerPulse = 1.0 - min(abs(progress - ((sin(phase * sweep) + 1.0) / 2.0)) * pulseWidth, 1.0)
            let energy = max(0.0, min(1.0, 0.26 + centerPulse * 0.58 + sin(CGFloat(index) * 0.86 + phase) * 0.16))
            let height = 3.0 + maxHeight * 0.38 * loudness * energy
            let x = rect.minX + CGFloat(index) * (audioBarWidth + audioBarGap)
            let top = NSRect(x: x, y: bounds.midY + 1.0, width: audioBarWidth, height: height)
            let bottom = NSRect(x: x, y: bounds.midY - height - 1.0, width: audioBarWidth, height: height)
            dynamicColor(offset: progress * 0.52, alpha: 0.72 + energy * 0.22).setFill()
            NSBezierPath(roundedRect: top, xRadius: audioBarWidth / 2, yRadius: audioBarWidth / 2).fill()
            NSBezierPath(roundedRect: bottom, xRadius: audioBarWidth / 2, yRadius: audioBarWidth / 2).fill()
        }
    }

    private func drawAudioBreathingBars(in rect: NSRect, maxHeight: CGFloat) {
        let denominator = CGFloat(max(audioBarCount - 1, 1))
        let loudness = max(0.18, min(1.0, level))
        let breath = (sin(phase * 0.42) + 1.0) / 2.0
        for index in 0..<audioBarCount {
            let progress = CGFloat(index) / denominator
            let centerFalloff = 1.0 - min(abs(progress - 0.5) * 1.85, 1.0)
            let ripple = (sin(CGFloat(index) * 0.72 + phase * 0.86) + 1.0) / 2.0
            let energy = 0.16 + centerFalloff * 0.48 + ripple * 0.22 + breath * 0.10
            let height = 4.0 + maxHeight * loudness * min(1.0, energy)
            let x = rect.minX + CGFloat(index) * (audioBarWidth + audioBarGap)
            let capsule = NSRect(x: x, y: bounds.midY - height / 2, width: audioBarWidth, height: height)
            dynamicColor(offset: progress * 0.48, alpha: 0.62 + centerFalloff * 0.26).setFill()
            NSBezierPath(roundedRect: capsule, xRadius: audioBarWidth / 2, yRadius: audioBarWidth / 2).fill()
        }
    }

    private func drawAudioMirroredSquiggle(in rect: NSRect, maxHeight: CGFloat) {
        let width = max(rect.width, 1)
        let loudness = max(0.18, min(1.0, level))
        let amplitude = min(maxHeight * 0.28, 5.2 + loudness * 5.0)
        let frequency = 2.15 + recipeValue(0.91) * 0.55
        for mirror in 0..<2 {
            let path = NSBezierPath()
            path.lineWidth = max(1.2, audioBarWidth * 0.72)
            path.lineCapStyle = .round
            path.lineJoinStyle = .round
            var x = rect.minX
            path.move(to: NSPoint(x: x, y: rect.midY))
            while x <= rect.maxX {
                let progress = (x - rect.minX) / width
                let sign: CGFloat = mirror == 0 ? 1.0 : -1.0
                let y = rect.midY
                    + sign * sin(progress * .pi * frequency + phase * 0.78) * amplitude
                    + sign * sin(progress * .pi * 6.0 - phase * 0.34) * 0.9
                path.line(to: NSPoint(x: x, y: y))
                x += 2.0
            }
            dynamicColor(offset: mirror == 0 ? 0.18 : 0.38, alpha: mirror == 0 ? 0.92 : 0.56).setStroke()
            path.stroke()
        }

        let beadTravel = (phase * 0.060).truncatingRemainder(dividingBy: 1.0)
        let beadX = rect.minX + beadTravel * width
        let beadY = rect.midY + sin(beadTravel * .pi * frequency + phase * 0.78) * amplitude
        let beadSize = max(1.4, audioBarWidth * 0.72)
        NSColor.white.withAlphaComponent(0.58).setFill()
        NSBezierPath(ovalIn: NSRect(x: beadX - beadSize / 2, y: beadY - beadSize / 2, width: beadSize, height: beadSize)).fill()
    }

    private func drawAudioSnakeWave(in rect: NSRect, maxHeight: CGFloat) {
        let count = audioBarCount
        let width = max(rect.width, 1)
        let loudness = max(0.18, min(1.0, level))
        let frequency = 3.2 + recipeValue(0.51) * 1.6
        let skew = recipeSigned(0.54) * 0.18
        for index in 0..<count {
            let progress = CGFloat(index) / CGFloat(max(count - 1, 1))
            let travel = (progress + phase * 0.035).truncatingRemainder(dividingBy: 1.0)
            let y = rect.midY
                + sin((travel + skew * progress) * .pi * frequency + phase * 0.38) * (3.0 + maxHeight * 0.20 * loudness)
            let x = rect.minX + progress * width
            let length = audioBarWidth * (1.5 + ((sin(phase + CGFloat(index) * 0.9) + 1.0) / 2.0) * 1.7)
            let capsule = NSRect(x: x - length / 2, y: y - audioBarWidth / 2, width: length, height: audioBarWidth)
            dynamicColor(offset: 0.36 + progress * 0.46, alpha: index % 2 == 0 ? 0.90 : 0.58).setFill()
            NSBezierPath(roundedRect: capsule, xRadius: audioBarWidth / 2, yRadius: audioBarWidth / 2).fill()
        }
    }

    private func drawAudioOrbitWave(in rect: NSRect, maxHeight: CGFloat) {
        let count = audioBarCount
        let width = max(rect.width, 1)
        let loudness = max(0.18, min(1.0, level))
        let orbit = 2.2 + recipeValue(0.61) * 1.6
        for index in 0..<count {
            let progress = CGFloat(index) / CGFloat(max(count - 1, 1))
            let angle = progress * .pi * orbit + phase * 0.86
            let x = rect.minX + progress * width
            let y = rect.midY + sin(angle) * (2.8 + maxHeight * 0.18 * loudness)
            let size = audioBarWidth * (1.0 + ((cos(angle * 1.7) + 1.0) / 2.0) * 1.2)
            let dot = NSRect(x: x - size / 2, y: y - size / 2, width: size, height: size)
            dynamicColor(offset: 0.08 + progress * 0.72, alpha: 0.54 + ((sin(angle) + 1.0) / 2.0) * 0.38).setFill()
            NSBezierPath(ovalIn: dot).fill()
            if index % 3 == 0 {
                let haloSize = size * 2.2
                let halo = NSBezierPath(ovalIn: NSRect(x: x - haloSize / 2, y: y - haloSize / 2, width: haloSize, height: haloSize))
                halo.lineWidth = max(1.0, audioBarWidth * 0.45)
                dynamicColor(offset: 0.26 + progress * 0.42, alpha: 0.20).setStroke()
                halo.stroke()
            }
        }
    }

    private func drawAudioStepWave(in rect: NSRect, maxHeight: CGFloat) {
        let count = audioBarCount - 1
        let width = max(rect.width, 1)
        let loudness = max(0.18, min(1.0, level))
        let stepWidth = width / CGFloat(max(count, 1))
        let slope = 2.4 + recipeValue(0.71) * 1.6
        for index in 0..<count {
            let progress = CGFloat(index) / CGFloat(max(count - 1, 1))
            let energy = (sin(progress * .pi * slope + phase) + 1.0) / 2.0
            let y = rect.midY + (energy - 0.5) * maxHeight * 0.44 * loudness
            let length = stepWidth * (0.50 + energy * 0.42)
            let x = rect.minX + CGFloat(index) * stepWidth + (stepWidth - length) / 2
            let capsule = NSRect(x: x, y: y - audioBarWidth / 2, width: length, height: audioBarWidth)
            dynamicColor(offset: 0.18 + progress * 0.56, alpha: 0.52 + energy * 0.42).setFill()
            NSBezierPath(roundedRect: capsule, xRadius: audioBarWidth / 2, yRadius: audioBarWidth / 2).fill()
        }
    }

    private func drawAudioBraidedWave(in rect: NSRect, maxHeight: CGFloat) {
        let width = max(rect.width, 1)
        let loudness = max(0.18, min(1.0, level))
        let frequency = 2.4 + recipeValue(0.81) * 1.2
        for strand in 0..<3 {
            let path = NSBezierPath()
            path.lineWidth = max(1.4, audioBarWidth * (strand == 1 ? 0.86 : 0.62))
            path.lineCapStyle = .round
            path.lineJoinStyle = .round
            let offset = CGFloat(strand) * (2.0 * .pi / 3.0)
            var x = rect.minX
            path.move(to: NSPoint(x: x, y: rect.midY))
            while x <= rect.maxX {
                let progress = (x - rect.minX) / width
                let y = rect.midY
                    + sin(progress * .pi * frequency + phase * 0.84 + offset) * (3.2 + maxHeight * 0.16 * loudness)
                path.line(to: NSPoint(x: x, y: y))
                x += 2.4
            }
            dynamicColor(offset: 0.10 + CGFloat(strand) * 0.22, alpha: strand == 1 ? 0.86 : 0.38).setStroke()
            path.stroke()
        }
    }

    private func drawThinkingWave() {
        switch variant % hudMotionVariantCount {
        case HUDSignalStylePolicy.englishMotionVariant:
            drawThinkingRibbon()
        case HUDSignalStylePolicy.hindiMotionVariant:
            drawThinkingHindiTopline()
        default:
            drawThinkingRibbon()
        }
    }

    private func drawThinkingRibbon() {
        let rect = signalRect
        let midY = rect.midY
        let width = max(rect.width, 1)
        let rail = NSBezierPath()
        rail.lineWidth = max(1.0, workStrokeWidth * 0.72)
        rail.lineCapStyle = .round
        rail.move(to: NSPoint(x: rect.minX + 2.0, y: midY))
        rail.line(to: NSPoint(x: rect.maxX - 2.0, y: midY))
        dynamicColor(offset: 0.44, alpha: 0.28).setStroke()
        rail.stroke()

        let wave = NSBezierPath()
        wave.lineWidth = max(1.2, workStrokeWidth * 0.86)
        wave.lineCapStyle = .round
        wave.lineJoinStyle = .round
        var x = rect.minX
        wave.move(to: NSPoint(x: x, y: midY))
        while x <= rect.maxX {
            let progress = (x - rect.minX) / width
            let y = midY
                + sin(progress * .pi * 2.4 + phase * 0.66) * 2.8
                + sin(progress * .pi * 5.4 - phase * 0.22) * 0.45
            wave.line(to: NSPoint(x: x, y: y))
            x += 2.2
        }
        dynamicColor(offset: 0.50, alpha: 0.92).setStroke()
        wave.stroke()

        for index in 0..<3 {
            let travel = (phase * 0.062 + CGFloat(index) / 3.0).truncatingRemainder(dividingBy: 1.0)
            let beadX = rect.minX + travel * width
            let beadY = midY + sin(travel * .pi * 2.4 + phase * 0.66) * 2.8
            let beadWidth = workStrokeWidth * (2.2 + CGFloat(index % 2) * 0.35)
            let bead = NSRect(
                x: beadX - beadWidth / 2,
                y: beadY - workStrokeWidth / 2,
                width: beadWidth,
                height: workStrokeWidth
            )
            dynamicColor(offset: 0.52 + CGFloat(index) * 0.08, alpha: 0.82).setFill()
            NSBezierPath(roundedRect: bead, xRadius: workStrokeWidth / 2, yRadius: workStrokeWidth / 2).fill()
        }
    }

    private func drawThinkingBeads() {
        let rect = signalRect
        let count = 8
        let step = rect.width / CGFloat(count + 1)
        let midY = rect.midY
        for index in 0..<count {
            let x = rect.minX + step * CGFloat(index + 1)
            let energy = (sin(phase + CGFloat(index) * 0.92) + 1.0) / 2.0
            let y = midY + sin(phase * 0.72 + CGFloat(index) * 0.78) * 5.0
            let width = workStrokeWidth * (1.6 + energy * 1.2)
            let rect = NSRect(x: x - width / 2, y: y - workStrokeWidth / 2, width: width, height: workStrokeWidth)
            dynamicColor(offset: CGFloat(index) * 0.11, alpha: 0.42 + energy * 0.52).setFill()
            NSBezierPath(roundedRect: rect, xRadius: workStrokeWidth / 2, yRadius: workStrokeWidth / 2).fill()
        }
    }

    private func drawThinkingHelix() {
        let rect = signalRect
        let midY = rect.midY
        let width = max(rect.width, 1)
        for strand in 0..<2 {
            let path = NSBezierPath()
            path.lineWidth = workStrokeWidth
            var x = rect.minX
            let offset = CGFloat(strand) * .pi
            path.move(to: NSPoint(x: x, y: midY))
            while x <= rect.maxX {
                let progress = (x - rect.minX) / width
                let y = midY
                    + sin(progress * .pi * 5.2 + phase + offset) * 5.0
                    + sin(progress * .pi * 2.0 - phase * 0.35) * 1.2
                path.line(to: NSPoint(x: x, y: y))
                x += 2.5
            }
            dynamicColor(offset: CGFloat(strand) * 0.28 + 0.2, alpha: strand == 0 ? 0.9 : 0.58).setStroke()
            path.stroke()
        }
    }

    private func drawThinkingPulseBars() {
        let count = audioBarCount
        let tickWidth = workStrokeWidth
        let gap = audioBarGap
        let startX = signalRect.minX
        let maxHeight = bounds.height - 6
        for index in 0..<count {
            let energy = (sin(phase * 1.18 + CGFloat(index) * 0.72) + 1.0) / 2.0
            let height = 4.0 + maxHeight * (0.18 + energy * 0.74)
            let x = startX + CGFloat(index) * (tickWidth + gap)
            let rect = NSRect(x: x, y: bounds.midY - height / 2, width: tickWidth, height: height)
            dynamicColor(offset: 0.54 + CGFloat(index) * 0.08, alpha: 0.36 + energy * 0.56).setFill()
            NSBezierPath(roundedRect: rect, xRadius: tickWidth / 2, yRadius: tickWidth / 2).fill()
        }
    }

    private func drawThinkingComet() {
        let rect = signalRect
        let midY = rect.midY
        let travel = (sin(phase * 0.54) + 1.0) / 2.0
        let headX = rect.minX + 6 + travel * max(rect.width - 12, 1)
        for index in 0..<7 {
            let lag = CGFloat(index) * 7.0
            let x = max(rect.minX + 4, min(rect.maxX - 4, headX - lag))
            let y = midY + sin(phase * 0.92 + CGFloat(index) * 0.66) * 4.8
            let width = max(workStrokeWidth * 1.2, workStrokeWidth * (3.6 - CGFloat(index) * 0.28))
            let height = workStrokeWidth
            let rect = NSRect(x: x - width / 2, y: y - height / 2, width: width, height: height)
            dynamicColor(offset: 0.15 + CGFloat(index) * 0.075, alpha: max(0.20, 0.90 - CGFloat(index) * 0.11)).setFill()
            NSBezierPath(roundedRect: rect, xRadius: height / 2, yRadius: height / 2).fill()
        }
    }

    private func drawThinkingOrbit() {
        let rect = signalRect
        let count = 9
        let width = max(rect.width, 1)
        let orbit = 2.8 + recipeValue(1.11) * 1.4
        for index in 0..<count {
            let progress = CGFloat(index) / CGFloat(max(count - 1, 1))
            let angle = progress * .pi * orbit + phase * 0.72
            let x = rect.minX + progress * width
            let y = rect.midY + sin(angle) * 5.2
            let size = workStrokeWidth * (1.0 + ((cos(angle * 1.6) + 1.0) / 2.0) * 1.3)
            let dot = NSRect(x: x - size / 2, y: y - size / 2, width: size, height: size)
            dynamicColor(offset: 0.14 + progress * 0.64, alpha: 0.38 + ((sin(angle) + 1.0) / 2.0) * 0.48).setFill()
            NSBezierPath(ovalIn: dot).fill()
        }
    }

    private func drawThinkingScanline() {
        let rect = signalRect
        let count = 10
        let width = max(rect.width, 1)
        let scan = (phase * 0.075).truncatingRemainder(dividingBy: 1.0)
        for index in 0..<count {
            let progress = CGFloat(index) / CGFloat(max(count - 1, 1))
            let distance = min(abs(progress - scan), 1.0 - abs(progress - scan))
            let energy = max(0.18, 1.0 - distance * 3.4)
            let x = rect.minX + progress * width
            let height = 4.0 + energy * 14.0
            let capsule = NSRect(x: x - workStrokeWidth / 2, y: rect.midY - height / 2, width: workStrokeWidth, height: height)
            dynamicColor(offset: 0.30 + progress * 0.52, alpha: 0.28 + energy * 0.58).setFill()
            NSBezierPath(roundedRect: capsule, xRadius: workStrokeWidth / 2, yRadius: workStrokeWidth / 2).fill()
        }
    }

    private func drawThinkingBraided() {
        let rect = signalRect
        let width = max(rect.width, 1)
        let frequency = 2.6 + recipeValue(1.21) * 1.4
        for strand in 0..<3 {
            let path = NSBezierPath()
            path.lineWidth = max(1.4, workStrokeWidth * (strand == 1 ? 0.95 : 0.64))
            path.lineCapStyle = .round
            path.lineJoinStyle = .round
            let offset = CGFloat(strand) * (2.0 * .pi / 3.0)
            var x = rect.minX
            path.move(to: NSPoint(x: x, y: rect.midY))
            while x <= rect.maxX {
                let progress = (x - rect.minX) / width
                let y = rect.midY
                    + sin(progress * .pi * frequency + phase * 0.68 + offset) * 5.4
                    + sin(progress * .pi * 6.0 - phase * 0.28) * 0.8
                path.line(to: NSPoint(x: x, y: y))
                x += 2.2
            }
            dynamicColor(offset: 0.12 + CGFloat(strand) * 0.22, alpha: strand == 1 ? 0.86 : 0.36).setStroke()
            path.stroke()
        }
    }

    private func drawThinkingHindiTopline() {
        let rect = signalRect
        let width = max(rect.width, 1)
        let midY = rect.midY
        let frequency = 2.15 + recipeValue(1.32) * 0.42
        let detail = 5.6 + recipeValue(1.37) * 0.85

        for lane in 0..<3 {
            let path = NSBezierPath()
            path.lineWidth = max(1.15, workStrokeWidth * (lane == 1 ? 0.92 : 0.68))
            path.lineCapStyle = .round
            path.lineJoinStyle = .round
            let lanePhase = CGFloat(lane) * (2.0 * .pi / 3.0)
            let laneLift = CGFloat(lane - 1) * 1.2
            var x = rect.minX
            path.move(to: NSPoint(x: x, y: midY + laneLift))
            while x <= rect.maxX {
                let progress = (x - rect.minX) / width
                let envelope = 0.62 + sin(progress * .pi) * 0.38
                let y = midY + laneLift
                    + sin(progress * .pi * frequency + phase * 0.56 + lanePhase) * 4.1 * envelope
                    + sin(progress * .pi * detail - phase * 0.20 + lanePhase * 0.45) * 0.55
                path.line(to: NSPoint(x: x, y: y))
                x += 2.1
            }
            dynamicColor(offset: 0.16 + CGFloat(lane) * 0.18, alpha: lane == 1 ? 0.88 : 0.42).setStroke()
            path.stroke()
        }

        for index in 0..<3 {
            let travel = (phase * 0.052 + CGFloat(index) / 3.0).truncatingRemainder(dividingBy: 1.0)
            let envelope = 0.62 + sin(travel * .pi) * 0.38
            let beadX = rect.minX + travel * width
            let beadY = midY + sin(travel * .pi * frequency + phase * 0.56) * 4.1 * envelope
            let beadWidth = max(2.4, workStrokeWidth * 1.55)
            let bead = NSRect(
                x: beadX - beadWidth / 2,
                y: beadY - workStrokeWidth / 2,
                width: beadWidth,
                height: workStrokeWidth
            )
            dynamicColor(offset: 0.56 + CGFloat(index) * 0.075, alpha: 0.78).setFill()
            NSBezierPath(roundedRect: bead, xRadius: workStrokeWidth / 2, yRadius: workStrokeWidth / 2).fill()
        }
    }

    private func dynamicColor(offset: CGFloat, alpha: CGFloat) -> NSColor {
        let offsetUnit = offset - floor(offset)
        let focusedOffset = (offsetUnit - 0.5) * hudHueSpan
        let rawHue = (colorShift + focusedOffset + phase * 0.004).truncatingRemainder(dividingBy: 1.0)
        let hue = rawHue < 0 ? rawHue + 1.0 : rawHue
        return NSColor(calibratedHue: hue, saturation: 0.78, brightness: 1.0, alpha: alpha)
    }
}

struct ProcessResult {
    let exitCode: Int32
    let stdout: String
    let stderr: String
}

struct PasteAttemptResult {
    let attempted: Bool
    let verification: PasteVerificationStatus
    let copyFallbackRecommended: Bool
}

struct SafeReplaceAttempt {
    let replaced: Bool
    let reason: String
    let focusedRole: String
    let focusedSource: String
    let valueLength: Int?
    let selectedLocation: Int?
    let selectedLength: Int?
    let replacementLocation: Int?
    let replacementLength: Int?
}

struct DictationPayload {
    let rawText: String
    let text: String
    let engine: String
    let processor: String
    let fallbackReason: String
    let quality: [String: Any]
    let seconds: Double
    let route: String
    let safeUpdate: Bool
}

enum RunMode: String {
    case dictation
    case meeting
}

struct ActiveRun {
    let runID: String
    let mode: RunMode
    let audioURL: URL
    let streamChunkDirectory: URL?
    let targetPID: pid_t?
    let targetBundleID: String
    let targetName: String
    let focusedElement: AXUIElement?
    let startedAt: Date

    func withFocusedElement(_ element: AXUIElement?) -> ActiveRun {
        ActiveRun(
            runID: runID,
            mode: mode,
            audioURL: audioURL,
            streamChunkDirectory: streamChunkDirectory,
            targetPID: targetPID,
            targetBundleID: targetBundleID,
            targetName: targetName,
            focusedElement: element ?? focusedElement,
            startedAt: startedAt
        )
    }
}

struct MeetingAudioSource {
    let label: String
    let kind: String
    let audioURL: URL
    let durationSeconds: Double
}

struct MeetingSourceTranscript {
    let source: MeetingAudioSource
    let payload: DictationPayload
}

struct HistoryEntry {
    let createdAt: String
    let status: String
    let targetName: String
    let route: String
    let latencySeconds: Double?
    let text: String
}

final class StreamingAudioRecorder {
    let audioURL: URL
    let chunkDirectory: URL
    let targetChunkSeconds: Double
    let silenceChunkingEnabled: Bool
    let minChunkSeconds: Double
    let maxChunkSeconds: Double
    let silenceLookaroundSeconds: Double
    let silenceLevelThreshold: CGFloat
    private let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private var outputFormat: AVAudioFormat?
    private var fullAudioFile: AVAudioFile?
    private var currentChunkFile: AVAudioFile?
    private var currentChunkFrames: AVAudioFramePosition = 0
    private var chunkIndex = 0
    private let fileWriteQueue = DispatchQueue(label: "com.ramblefix.local.streaming-audio-file-write")
    private let fileWriteLock = NSLock()
    private var acceptingBuffers = false
    private(set) var chunkURLs: [URL] = []
    private(set) var latestNormalizedLevel: CGFloat = 0.25

    init(
        audioURL: URL,
        chunkDirectory: URL,
        targetChunkSeconds: Double,
        silenceChunkingEnabled: Bool = false,
        minChunkSeconds: Double? = nil,
        maxChunkSeconds: Double? = nil,
        silenceLookaroundSeconds: Double = 1.5,
        silenceLevelThreshold: CGFloat = 0.08
    ) {
        self.audioURL = audioURL
        self.chunkDirectory = chunkDirectory
        self.targetChunkSeconds = targetChunkSeconds
        self.silenceChunkingEnabled = silenceChunkingEnabled
        self.minChunkSeconds = minChunkSeconds ?? targetChunkSeconds
        self.maxChunkSeconds = maxChunkSeconds ?? targetChunkSeconds
        self.silenceLookaroundSeconds = silenceLookaroundSeconds
        self.silenceLevelThreshold = silenceLevelThreshold
    }

    func start() throws -> Bool {
        try FileManager.default.createDirectory(at: chunkDirectory, withIntermediateDirectories: true)
        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        guard let format = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: 16_000,
            channels: 1,
            interleaved: false
        ), let audioConverter = AVAudioConverter(from: inputFormat, to: format) else {
            return false
        }
        outputFormat = format
        converter = audioConverter
        fullAudioFile = try AVAudioFile(forWriting: audioURL, settings: format.settings)
        try startNextChunkFile()
        acceptingBuffers = true
        input.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { [weak self] buffer, _ in
            self?.consume(buffer)
        }
        engine.prepare()
        try engine.start()
        return true
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        fileWriteQueue.sync {
            fileWriteLock.lock()
            defer { fileWriteLock.unlock() }
            acceptingBuffers = false
            currentChunkFile = nil
            fullAudioFile = nil
            converter = nil
            outputFormat = nil
        }
        engine.stop()
    }

    private func consume(_ buffer: AVAudioPCMBuffer) {
        updateLevel(from: buffer)
        guard let copied = copyBuffer(buffer) else { return }
        fileWriteQueue.async { [weak self] in
            self?.writeConverted(copied)
        }
    }

    private func writeConverted(_ buffer: AVAudioPCMBuffer) {
        fileWriteLock.lock()
        defer { fileWriteLock.unlock() }
        do {
            guard acceptingBuffers else { return }
            guard let fullAudioFile,
                  let chunkAudioFile = currentChunkFile,
                  let converted = convertedBuffer(from: buffer),
                  converted.frameLength > 0 else {
                return
            }
            try fullAudioFile.write(from: converted)
            try chunkAudioFile.write(from: converted)
            currentChunkFrames += AVAudioFramePosition(converted.frameLength)
            if shouldCloseCurrentChunk() {
                currentChunkFile = nil
                currentChunkFrames = 0
                try startNextChunkFile()
            }
        } catch {
            // Keep the capture path alive. The final recorder file is still best-effort.
        }
    }

    private func copyBuffer(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let copied = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength) else {
            return nil
        }
        copied.frameLength = buffer.frameLength
        let frameCount = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        if let source = buffer.floatChannelData, let target = copied.floatChannelData {
            for channel in 0..<channelCount {
                target[channel].update(from: source[channel], count: frameCount)
            }
            return copied
        }
        if let source = buffer.int16ChannelData, let target = copied.int16ChannelData {
            for channel in 0..<channelCount {
                target[channel].update(from: source[channel], count: frameCount)
            }
            return copied
        }
        if let source = buffer.int32ChannelData, let target = copied.int32ChannelData {
            for channel in 0..<channelCount {
                target[channel].update(from: source[channel], count: frameCount)
            }
            return copied
        }
        return nil
    }

    private func shouldCloseCurrentChunk() -> Bool {
        guard let outputFormat else { return false }
        let seconds = Double(currentChunkFrames) / outputFormat.sampleRate
        return StreamingChunkPolicy.shouldCloseChunk(
            elapsedSeconds: seconds,
            targetSeconds: targetChunkSeconds,
            silenceChunkingEnabled: silenceChunkingEnabled,
            minSeconds: minChunkSeconds,
            maxSeconds: maxChunkSeconds,
            silenceLookaroundSeconds: silenceLookaroundSeconds,
            normalizedLevel: Double(latestNormalizedLevel),
            silenceLevelThreshold: Double(silenceLevelThreshold)
        )
    }

    private func convertedBuffer(from buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let converter, let outputFormat else { return nil }
        let ratio = outputFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(max(1, Double(buffer.frameLength) * ratio + 64))
        guard let converted = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else {
            return nil
        }
        var supplied = false
        var conversionError: NSError?
        let status = converter.convert(to: converted, error: &conversionError) { _, outStatus in
            if supplied {
                outStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            outStatus.pointee = .haveData
            return buffer
        }
        if status == .error || conversionError != nil {
            return nil
        }
        return converted
    }

    private func startNextChunkFile() throws {
        guard let outputFormat else { return }
        let url = chunkDirectory.appendingPathComponent(String(format: "chunk-%03d.wav", chunkIndex))
        chunkIndex += 1
        currentChunkFile = try AVAudioFile(forWriting: url, settings: outputFormat.settings)
        chunkURLs.append(url)
    }

    private func updateLevel(from buffer: AVAudioPCMBuffer) {
        guard let channels = buffer.floatChannelData, buffer.frameLength > 0 else { return }
        let frames = Int(buffer.frameLength)
        let samples = channels[0]
        var sum: Float = 0
        for index in 0..<frames {
            let sample = samples[index]
            sum += sample * sample
        }
        let rms = sqrt(max(sum / Float(frames), 0.000_000_01))
        let db = 20 * log10(rms)
        let normalized = max(0.0, min(1.0, (Double(db) + 55.0) / 45.0))
        latestNormalizedLevel = CGFloat(normalized)
    }
}

#if RAMBLEFIX_MEETING_MODE
@available(macOS 13.0, *)
final class SystemAudioRecorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let audioURL: URL
    private let queue = DispatchQueue(label: "com.ramblefix.local.system-audio-capture")
    private let lock = NSLock()
    private var stream: SCStream?
    private var wavWriter: MonoPCM16WAVWriter?
    private var resampleCursor: Double = 0
    private var acceptingBuffers = false
    private(set) var lastError: String?

    init(audioURL: URL) {
        self.audioURL = audioURL
        super.init()
    }

    func start() {
        SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) { [weak self] content, error in
            guard let self else { return }
            if let error {
                self.recordError("shareable_content: \(error.localizedDescription)")
                return
            }
            guard let display = content?.displays.first else {
                self.recordError("shareable_content: no display")
                return
            }
            let config = SCStreamConfiguration()
            config.width = 2
            config.height = 2
            config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
            config.capturesAudio = true
            config.excludesCurrentProcessAudio = true
            config.sampleRate = 48_000
            config.channelCount = 2
            let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
            let stream = SCStream(filter: filter, configuration: config, delegate: self)
            do {
                try FileManager.default.createDirectory(at: self.audioURL.deletingLastPathComponent(), withIntermediateDirectories: true)
                try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: self.queue)
                self.lock.lock()
                self.stream = stream
                self.acceptingBuffers = true
                self.lock.unlock()
                stream.startCapture { [weak self] error in
                    if let error {
                        self?.recordError("start_capture: \(error.localizedDescription)")
                    }
                }
            } catch {
                self.recordError("start: \(error.localizedDescription)")
            }
        }
    }

    func stop() {
        lock.lock()
        acceptingBuffers = false
        let stream = self.stream
        self.stream = nil
        try? wavWriter?.close()
        wavWriter = nil
        resampleCursor = 0
        lock.unlock()
        stream?.stopCapture { _ in }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        guard let buffer = pcmBuffer(from: sampleBuffer) else { return }
        write(buffer)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        recordError("stopped: \(error.localizedDescription)")
    }

    private func pcmBuffer(from sampleBuffer: CMSampleBuffer) -> AVAudioPCMBuffer? {
        guard let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
              let streamDescription = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription),
              let inputFormat = AVAudioFormat(streamDescription: streamDescription) else {
            return nil
        }
        let frameCount = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        guard frameCount > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: inputFormat, frameCapacity: frameCount) else {
            return nil
        }
        buffer.frameLength = frameCount
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sampleBuffer,
            at: 0,
            frameCount: Int32(frameCount),
            into: buffer.mutableAudioBufferList
        )
        return status == noErr ? buffer : nil
    }

    private func write(_ buffer: AVAudioPCMBuffer) {
        var writeError: String?
        lock.lock()
        guard acceptingBuffers else {
            lock.unlock()
            return
        }
        do {
            if wavWriter == nil {
                wavWriter = try MonoPCM16WAVWriter(url: audioURL)
            }
            let samples = MonoPCM16Downsampler.samples(from: buffer, cursor: &resampleCursor)
            try wavWriter?.write(samples: samples)
        } catch {
            writeError = "write: \(error.localizedDescription)"
        }
        lock.unlock()
        if let writeError {
            recordError(writeError)
        }
    }

    private func recordError(_ message: String) {
        lock.lock()
        lastError = message
        lock.unlock()
    }
}
#else
final class SystemAudioRecorder {
    let audioURL: URL
    private(set) var lastError: String?

    init(audioURL: URL) {
        self.audioURL = audioURL
    }

    func start() {
        lastError = "system audio capture is not included in this V0 build"
    }

    func stop() {}
}
#endif

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem?
    private var dictateMenuItem: NSMenuItem?
    private var meetingMenuItem: NSMenuItem?
    private var modeMenuItem: NSMenuItem?
    private var permissionMenuItem: NSMenuItem?
    private var systemHealthMenuItem: NSMenuItem?
    private var copyLatestMenuItem: NSMenuItem?
    private var captureEvalAudioMenuItem: NSMenuItem?
    private var hotKeyRef: EventHotKeyRef?
    private var eventTap: CFMachPort?
    private var eventTapRunLoopSource: CFRunLoopSource?
    private var permissionRetryTimer: Timer?
    private var hudWindow: NSPanel?
    private var hudTitleLabel: NSTextField?
    private var hudSubtitleLabel: NSTextField?
    private var hudStateLabel: NSTextField?
    private var hudSignalView: RFHUDSignalView?
    private var hudBackgroundView: NSVisualEffectView?
    private var hudNativeGlassView: NSView?
    private var hudGlassOverlayView: RFLiquidGlassOverlayView?
    private var hudRefractedGlassView: RFRefractedBackdropGlassView?
    private var hudBackdropCaptureKey: String?
    private var hudActionButton: NSButton?
    private var hudCopyText: String?
    private var hudTimer: Timer?
    private var hudMotionTimer: Timer?
    private var slowProcessingFeedbackTimer: Timer?
    private var slowProcessingRunID: String?
    private var hudColorShift: CGFloat = 0
    private var hudMotionVariant: Int = 0
    private var hudLastMotionVariant: Int = -1
    private var hudRecipeSeed: CGFloat = 0
    private var hudMotionSpeed: CGFloat = 0.28
    private var hudMotionFrameInterval: TimeInterval = hudDefaultMotionFrameInterval
    private var hudPreviewTimer: Timer?
    private var recordingHUDAccent: NSColor = RFTheme.cyan
    private var capturePeakLevel: CGFloat = 0
    private var captureMeanLevel: CGFloat = 0
    private var captureLevelSamples = 0
    private var recordingStartSound: NSSound?
    private var pasteDoneSound: NSSound?
    private var historyWindow: NSWindow?
    private var historyTextView: NSTextView?
    private var latestTranscript: String?
    private var latestHistoryTranscriptCache: String?
    private var recorder: AVAudioRecorder?
    private var streamingRecorder: StreamingAudioRecorder?
    private var systemAudioRecorder: SystemAudioRecorder?
    private var activeRun: ActiveRun?
    private var isTranscribing = false
    private var isFinalizing = false
    private var hotkeyHeld = false
    private var controlHoldActive = false
    private var controlHoldStartedRecording = false
    private var controlHoldToken = 0
    private var permissionRequestPending = false
    private var systemHealthTimer: Timer?
    private var systemHealthSamplingEnabled = true
    private var latestSystemLoadRatio: Double = 0
    private var latestSystemLoadSampleAt: Date?
    private var latestSystemHealthSampleCostMs: Double = 0
    private var latestThermalState = ProcessInfo.ThermalState.nominal
    private var whisperSidecarStartAttemptedAt: Date?
    private var nativeASRServerStartAttemptedAt: Date?
    private var learningTimer: Timer?
    private var isLearningTerms = false
    private var protectedTermAliasCache: [String: String]?
    private var protectedTermAliasCacheSignature = ""
    private var approvedPhraseFixCache: [ApprovedPhraseFixEntry]?
    private var approvedPhraseFixCacheSignature = ""
    private var lastPermissionSignature = ""
    private let projectRoot: URL

    override init() {
        let envRoot = ProcessInfo.processInfo.environment["RAMBLEFIX_ROOT"]
        let root = envRoot.flatMap { validatedProjectRoot(URL(fileURLWithPath: $0, isDirectory: true)) }
            ?? bundledProjectRoot()
            ?? findProjectRoot()
            ?? URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
        self.projectRoot = root
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        ProcessInfo.processInfo.disableAutomaticTermination("\(appName) runs as a resident menu bar hotkey app.")
        ProcessInfo.processInfo.disableSuddenTermination()
        NSApp.setActivationPolicy(.accessory)
        appendNativeEvent("app_launch", fields: [
            "project_root": projectRoot.path,
            "pid": Int(ProcessInfo.processInfo.processIdentifier),
            "capture_eval_audio": isCaptureEvalAudioEnabled()
        ])
        setupMenu()
        if envFlag("RAMBLEFIX_HUD_PREVIEW", defaultValue: false) {
            runHUDPreview()
            return
        }
        warmWhisperSidecarIfNeeded()
        warmNativeASRServerIfNeeded()
        scheduleSystemHealthSampling()
        refreshPermissions(requestKeyboard: true)
        permissionRetryTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.refreshPermissions(requestKeyboard: false)
        }
        registerHotKey()
        scheduleBackgroundLearning()
        warmHindiStreamSidecarIfNeeded()
    }

    private func runHUDPreview() {
        let previewState = ProcessInfo.processInfo.environment["RAMBLEFIX_HUD_PREVIEW_STATE"]?.lowercased() ?? "rec"
        let lifetime = envDouble("RAMBLEFIX_HUD_PREVIEW_SECONDS", defaultValue: 8.0, minValue: 2.0)
        hudRecipeSeed = nextHUDRecipeSeed()
        switch previewState {
        case "work", "english":
            hudColorShift = hueWithJitter(base: CGFloat(HUDSignalStylePolicy.healthyHueBase), jitter: 0.0)
            hudMotionSpeed = 0.32
            hudMotionVariant = HUDSignalStylePolicy.englishMotionVariant
            showHUD(title: "", subtitle: "", state: "WORK", accent: RFTheme.cyan)
        case "hindi", "hinglish":
            hudColorShift = hueWithJitter(base: 0.74, jitter: 0.0)
            hudMotionSpeed = 0.30
            hudMotionVariant = HUDSignalStylePolicy.hindiMotionVariant
            showHUD(title: "", subtitle: "", state: "WORK", accent: RFTheme.violet)
        case "copy", "toast":
            hudColorShift = hueWithJitter(base: CGFloat(HUDSignalStylePolicy.healthyHueBase), jitter: 0.0)
            showHUD(title: "Cursor not focused", subtitle: "", state: "COPY", accent: RFTheme.mint, copyText: "Cursor not focused")
        default:
            hudColorShift = hueWithJitter(base: CGFloat(HUDSignalStylePolicy.healthyHueBase), jitter: 0.0)
            hudMotionVariant = HUDSignalStylePolicy.recordingSquiggleVariant
            var previewLevel: CGFloat = 0.54
            hudPreviewTimer = Timer.scheduledTimer(withTimeInterval: hudDefaultMotionFrameInterval, repeats: true) { [weak self] _ in
                guard let self else { return }
                previewLevel = 0.44 + 0.30 * ((sin(self.hudSignalView?.phase ?? 0) + 1.0) / 2.0)
                self.showHUD(title: "", subtitle: "", state: "REC", accent: RFTheme.cyan, level: previewLevel)
            }
            showHUD(title: "", subtitle: "", state: "REC", accent: RFTheme.cyan, level: previewLevel)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + lifetime) { [weak self] in
            self?.hudPreviewTimer?.invalidate()
            self?.hudPreviewTimer = nil
            NSApp.terminate(nil)
        }
    }

    private func setupMenu() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = menuBarIdleTitle
        item.button?.toolTip = "\(appName) dictation"
        let menu = NSMenu()
        menu.delegate = self
        menu.addItem(NSMenuItem(title: "Hold Fn or Control in any text box", action: nil, keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Fallback: Ctrl-Option-Space", action: nil, keyEquivalent: ""))
        let permissions = NSMenuItem(title: "Permissions: checking...", action: #selector(openInputMonitoringSettings), keyEquivalent: "")
        menu.addItem(permissions)
        let systemHealth = NSMenuItem(title: "System: checking...", action: nil, keyEquivalent: "")
        menu.addItem(systemHealth)
        let dictate = NSMenuItem(title: "Dictate", action: #selector(toggleDictation), keyEquivalent: "d")
        menu.addItem(dictate)
        let meeting = NSMenuItem(title: "Record Meeting", action: #selector(toggleMeetingRecording), keyEquivalent: "r")
        if meetingModeEnabled {
            menu.addItem(meeting)
        }
        menu.addItem(NSMenuItem.separator())
        let mode = NSMenuItem(title: "Mode: English Fast", action: nil, keyEquivalent: "")
        menu.addItem(mode)
        let captureEvalAudio = NSMenuItem(title: "Capture Eval Audio", action: #selector(toggleCaptureEvalAudio), keyEquivalent: "e")
        menu.addItem(captureEvalAudio)
        menu.addItem(NSMenuItem(title: "Show Transcript History", action: #selector(showTranscriptHistory), keyEquivalent: "h"))
        let copyLatest = NSMenuItem(title: "Copy Latest Transcript", action: #selector(copyLatestTranscript), keyEquivalent: "c")
        menu.addItem(copyLatest)
        menu.addItem(NSMenuItem(title: "Mark Latest Transcript Bad", action: #selector(markLatestTranscriptBad), keyEquivalent: "b"))
        menu.addItem(NSMenuItem(title: "Learn Clipboard Correction", action: #selector(learnClipboardCorrection), keyEquivalent: "l"))
        menu.addItem(NSMenuItem(title: "Open History Folder", action: #selector(openHistoryFolder), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Export Diagnostics", action: #selector(exportDiagnostics), keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(quit), keyEquivalent: "q"))
        item.menu = menu
        statusItem = item
        dictateMenuItem = dictate
        meetingMenuItem = meeting
        modeMenuItem = mode
        captureEvalAudioMenuItem = captureEvalAudio
        permissionMenuItem = permissions
        systemHealthMenuItem = systemHealth
        copyLatestMenuItem = copyLatest
        updateMenuState()
    }

    func menuWillOpen(_ menu: NSMenu) {
        refreshPermissions(requestKeyboard: false)
    }

    private func preflightAccessibility(prompt: Bool) -> Bool {
        let options = ["AXTrustedCheckOptionPrompt": prompt] as CFDictionary
        return AXIsProcessTrustedWithOptions(options)
    }

    private func preflightInputMonitoring(prompt: Bool) -> Bool {
        if CGPreflightListenEventAccess() {
            return true
        }
        if prompt {
            return CGRequestListenEventAccess()
        }
        return false
    }

    private func preflightScreenCapture(prompt: Bool) -> Bool {
        if CGPreflightScreenCaptureAccess() {
            return true
        }
        if prompt {
            return CGRequestScreenCaptureAccess()
        }
        return false
    }

    private func refreshPermissions(requestKeyboard: Bool) {
        let accessibility = preflightAccessibility(prompt: requestKeyboard)
        let inputMonitoring = preflightInputMonitoring(prompt: requestKeyboard)
        let screenCapture = meetingModeEnabled ? preflightScreenCapture(prompt: false) : nil
        let screenCaptureSignature = screenCapture.map { $0 ? "true" : "false" } ?? "not_applicable"
        let signature = "accessibility:\(accessibility)|input_monitoring:\(inputMonitoring)|screen_capture:\(screenCaptureSignature)"
        if signature != lastPermissionSignature {
            lastPermissionSignature = signature
            var fields: [String: Any] = [
                "accessibility": accessibility,
                "input_monitoring": inputMonitoring,
                "requested_prompt": requestKeyboard
            ]
            if let screenCapture {
                fields["screen_capture"] = screenCapture
            }
            appendNativeEvent("permission_state", fields: fields)
        }
        if let screenCapture {
            permissionMenuItem?.title = "Permissions: Accessibility \(accessibility ? "OK" : "missing"), Input Monitoring \(inputMonitoring ? "OK" : "missing"), Screen Recording \(screenCapture ? "OK" : "missing")"
        } else {
            permissionMenuItem?.title = "Permissions: Accessibility \(accessibility ? "OK" : "missing"), Input Monitoring \(inputMonitoring ? "OK" : "missing")"
        }
        if inputMonitoring, eventTap == nil {
            installSingleKeyEventTap()
        }
        if !inputMonitoring, activeRun == nil, !isTranscribing, !isFinalizing {
            statusItem?.button?.title = "\(menuBarIdleTitle)!"
        } else if activeRun == nil, !isTranscribing, !isFinalizing {
            statusItem?.button?.title = menuBarIdleTitle
        }
    }

    private func preflightMicrophone(completion: @escaping (Bool) -> Void) {
        let status = AVCaptureDevice.authorizationStatus(for: .audio)
        switch status {
        case .authorized:
            completion(true)
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                DispatchQueue.main.async { completion(granted) }
            }
        default:
            completion(false)
        }
    }

    private func registerHotKey() {
        let hotKeyID = EventHotKeyID(signature: OSType("RMBL".fourCharCode), id: 1)
        let modifiers = UInt32(controlKey | optionKey)
        let keyCode = UInt32(kVK_Space)
        let status = RegisterEventHotKey(keyCode, modifiers, hotKeyID, GetApplicationEventTarget(), 0, &hotKeyRef)
        appendNativeEvent("fallback_hotkey_register", fields: ["status": Int(status)])
        guard status == noErr else {
            notify("\(appName) hotkey unavailable", "Ctrl-Option-Space could not be registered. OSStatus \(status).")
            statusItem?.button?.title = "\(menuBarIdleTitle)!"
            return
        }

        var eventTypes = [
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased))
        ]
        let handlerStatus = InstallEventHandler(GetApplicationEventTarget(), { _, event, userData in
            guard let event, let userData else { return noErr }
            let delegate = Unmanaged<AppDelegate>.fromOpaque(userData).takeUnretainedValue()
            let kind = GetEventKind(event)
            if kind == UInt32(kEventHotKeyPressed) {
                delegate.appendNativeEvent("fallback_hotkey_pressed")
                delegate.startDictation()
            } else if kind == UInt32(kEventHotKeyReleased) {
                delegate.appendNativeEvent("fallback_hotkey_released")
                delegate.stopDictationAndTranscribe()
            }
            return noErr
        }, eventTypes.count, &eventTypes, Unmanaged.passUnretained(self).toOpaque(), nil)
        if handlerStatus != noErr {
            notify("\(appName) hotkey handler failed", "OSStatus \(handlerStatus).")
            statusItem?.button?.title = "\(menuBarIdleTitle)!"
        }
    }

    private func installSingleKeyEventTap() {
        guard envFlag("RAMBLEFIX_SINGLE_KEY_HOTKEY", defaultValue: true) else { return }
        guard eventTap == nil else { return }
        guard preflightInputMonitoring(prompt: false) else {
            appendNativeEvent("single_key_event_tap_missing_input_monitoring")
            notify("\(appName) needs Input Monitoring", "Enable Input Monitoring for \(appName) to use Fn or Control. Ctrl-Option-Space still works.")
            return
        }
        let mask = (1 << CGEventType.flagsChanged.rawValue) | (1 << CGEventType.keyDown.rawValue)
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .listenOnly,
            eventsOfInterest: CGEventMask(mask),
            callback: { _, type, event, userInfo in
                guard let userInfo else { return Unmanaged.passUnretained(event) }
                let delegate = Unmanaged<AppDelegate>.fromOpaque(userInfo).takeUnretainedValue()
                delegate.handleEventTap(type: type, event: event)
                return Unmanaged.passUnretained(event)
            },
            userInfo: Unmanaged.passUnretained(self).toOpaque()
        ) else {
            appendNativeEvent("single_key_event_tap_failed")
            notify("\(appName) single-key hotkey unavailable", "Enable Accessibility/Input Monitoring for \(appName). Ctrl-Option-Space still works.")
            return
        }
        eventTap = tap
        let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        eventTapRunLoopSource = source
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        appendNativeEvent("single_key_event_tap_installed")
    }

    private func handleEventTap(type: CGEventType, event: CGEvent) {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let eventTap {
                CGEvent.tapEnable(tap: eventTap, enable: true)
            }
            return
        }
        if type == .keyDown {
            cancelControlHoldCandidate()
            return
        }
        guard type == .flagsChanged else { return }
        let keyCode = Int(event.getIntegerValueField(.keyboardEventKeycode))
        let isControlKey = keyCode == kVK_Control || keyCode == kVK_RightControl
        let isFunctionKey = keyCode == kVK_Function
        if controlHoldActive, !controlHoldStartedRecording, !isOnlySingleKeyHotkey(event.flags) {
            cancelControlHoldCandidate()
        }
        guard isControlKey || isFunctionKey else { return }
        if isOnlySingleKeyHotkey(event.flags) {
            beginControlHoldCandidate()
        } else {
            endControlHold()
        }
    }

    private func beginControlHoldCandidate() {
        guard !controlHoldActive else { return }
        controlHoldActive = true
        controlHoldStartedRecording = false
        controlHoldToken += 1
        let token = controlHoldToken
        appendNativeEvent("single_key_hold_candidate")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.22) {
            guard self.controlHoldActive, self.controlHoldToken == token, !self.controlHoldStartedRecording else { return }
            self.controlHoldStartedRecording = true
            self.hotkeyHeld = true
            self.appendNativeEvent("single_key_recording_start")
            self.startDictation()
        }
    }

    private func cancelControlHoldCandidate() {
        guard controlHoldActive, !controlHoldStartedRecording else { return }
        controlHoldActive = false
        hotkeyHeld = false
        controlHoldToken += 1
        appendNativeEvent("single_key_hold_cancelled")
    }

    private func endControlHold() {
        let shouldStop = controlHoldActive && controlHoldStartedRecording
        controlHoldActive = false
        controlHoldStartedRecording = false
        hotkeyHeld = false
        controlHoldToken += 1
        appendNativeEvent("single_key_hold_ended", fields: ["will_stop": shouldStop])
        if shouldStop {
            stopDictationAndTranscribe()
        }
    }

    private func isOnlyControl(_ flags: CGEventFlags) -> Bool {
        flags.contains(.maskControl)
            && !flags.contains(.maskShift)
            && !flags.contains(.maskAlternate)
            && !flags.contains(.maskCommand)
            && !flags.contains(.maskSecondaryFn)
    }

    private func isOnlyFunction(_ flags: CGEventFlags) -> Bool {
        flags.contains(.maskSecondaryFn)
            && !flags.contains(.maskControl)
            && !flags.contains(.maskShift)
            && !flags.contains(.maskAlternate)
            && !flags.contains(.maskCommand)
    }

    private func isOnlySingleKeyHotkey(_ flags: CGEventFlags) -> Bool {
        isOnlyControl(flags) || isOnlyFunction(flags)
    }

    @objc private func toggleDictation() {
        if !isRecordingActive() {
            hotkeyHeld = true
            startDictation()
        } else {
            guard activeRun?.mode == .dictation else { return }
            hotkeyHeld = false
            stopDictationAndTranscribe()
        }
    }

    @objc private func toggleMeetingRecording() {
        if !isRecordingActive() {
            hotkeyHeld = false
            startMeetingRecording()
        } else {
            guard activeRun?.mode == .meeting else { return }
            stopMeetingRecordingAndTranscribe()
        }
    }

    @objc private func openHistoryFolder() {
        let logsURL = projectRoot.appendingPathComponent("logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logsURL, withIntermediateDirectories: true)
        NSWorkspace.shared.open(logsURL)
    }

    @objc private func exportDiagnostics() {
        do {
            let archive = try createDiagnosticsArchive()
            appendNativeEvent("diagnostics_exported", fields: [
                "archive_path": archive.path,
                "includes_audio": false,
                "includes_transcripts": false
            ])
            showHUD(title: "Diagnostics exported", subtitle: archive.lastPathComponent, state: "INFO", accent: RFTheme.mint, autoHide: 4.0)
            NSWorkspace.shared.activateFileViewerSelecting([archive])
        } catch {
            appendNativeEvent("diagnostics_export_failed", fields: [
                "error": String(describing: error)
            ])
            notify("\(appName) diagnostics failed", String(describing: error))
            showHUD(title: "Export failed", subtitle: "Could not create diagnostics.", state: "FAIL", accent: RFTheme.coral, autoHide: 4.0)
        }
    }

    @objc private func toggleCaptureEvalAudio() {
        let next = !isCaptureEvalAudioEnabled()
        UserDefaults.standard.set(next, forKey: captureEvalAudioDefaultsKey)
        updateMenuState()
        let subtitle = next
            ? "Successful clips are saved locally for scoring."
            : "Successful clips will be cleaned after dictation."
        showHUD(title: next ? "Eval audio on" : "Eval audio off", subtitle: subtitle, state: "INFO", accent: next ? RFTheme.mint : RFTheme.violet, autoHide: 2.0)
    }

    @objc private func showTranscriptHistory() {
        showOrRefreshHistoryWindow()
    }

    @objc private func copyLatestTranscript() {
        guard let text = latestTranscriptFromMemoryOrHistory() else {
            showHUD(title: "No transcript yet", subtitle: "Dictate once, then copy from here.", state: "INFO", accent: RFTheme.violet, autoHide: 2.0)
            return
        }
        writeTextToPasteboard(text)
        showHUD(title: "Copied latest transcript", subtitle: "Paste it wherever you need.", state: "COPY", accent: RFTheme.mint, autoHide: 2.0)
    }

    @objc private func markLatestTranscriptBad() {
        guard let text = latestTranscriptFromMemoryOrHistory() else {
            showHUD(title: "No transcript yet", subtitle: "Dictate once, then mark it here.", state: "INFO", accent: RFTheme.violet, autoHide: 2.0)
            return
        }
        appendFeedbackEvent(kind: "bad_transcript", text: text)
        showHUD(title: "Marked for review", subtitle: "Saved locally. No data sent.", state: "INFO", accent: RFTheme.amber, autoHide: 2.5)
    }

    @objc private func learnClipboardCorrection() {
        guard let raw = NSPasteboard.general.string(forType: .string),
              let pair = parseClipboardCorrection(raw) else {
            showHUD(title: "Copy a correction first", subtitle: "Use format: wrong -> right", state: "LEARN", accent: RFTheme.amber, autoHide: 3.0)
            return
        }
        let python = ProcessInfo.processInfo.environment["RAMBLEFIX_PYTHON"]
            ?? projectRoot.appendingPathComponent(".venv/bin/python").path
        let result = runProcess(
            python,
            [
                "-m", "ramblefix.cli", "learn-phrase",
                pair.source,
                pair.replacement,
                "--note", "Approved from RambleFix Local clipboard correction"
            ],
            currentDirectory: projectRoot
        )
        if result.exitCode == 0 {
            showHUD(title: "Learned correction", subtitle: "\(pair.source) -> \(pair.replacement)", state: "LEARN", accent: RFTheme.mint, autoHide: 3.0)
        } else {
            let message = (result.stderr.isEmpty ? result.stdout : result.stderr)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            showHUD(title: "Learning failed", subtitle: String(message.prefix(96)), state: "FAIL", accent: RFTheme.coral, autoHide: 4.0)
        }
    }

    private func parseClipboardCorrection(_ raw: String) -> (source: String, replacement: String)? {
        let text = raw
            .replacingOccurrences(of: "\n", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        for separator in ["=>", "->", "="] {
            let parts = text.components(separatedBy: separator)
            guard parts.count == 2 else { continue }
            let source = parts[0].trimmingCharacters(in: .whitespacesAndNewlines)
            let replacement = parts[1].trimmingCharacters(in: .whitespacesAndNewlines)
            if !source.isEmpty,
               !replacement.isEmpty,
               source.localizedCaseInsensitiveCompare(replacement) != .orderedSame,
               source.count <= 120,
               replacement.count <= 120 {
                return (source, replacement)
            }
        }
        return nil
    }

    private func scheduleSystemHealthSampling() {
        guard runtimeFlag("RAMBLEFIX_SYSTEM_HEALTH_SAMPLING", defaultValue: true) else { return }
        sampleSystemHealth()
        systemHealthTimer?.invalidate()
        systemHealthTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            self?.sampleSystemHealthIfIdle()
        }
    }

    private func sampleSystemHealthIfIdle() {
        guard activeRun == nil,
              !isRecordingActive(),
              !isTranscribing,
              !isFinalizing,
              !permissionRequestPending else {
            return
        }
        sampleSystemHealth()
    }

    private func sampleSystemHealth() {
        guard systemHealthSamplingEnabled else { return }
        let started = Date()
        latestThermalState = ProcessInfo.processInfo.thermalState
        latestSystemLoadRatio = currentSystemLoadRatio()
        latestSystemLoadSampleAt = Date()
        latestSystemHealthSampleCostMs = roundedMilliseconds(latestSystemLoadSampleAt?.timeIntervalSince(started) ?? 0)
        if latestSystemHealthSampleCostMs > 20 {
            systemHealthSamplingEnabled = false
            systemHealthTimer?.invalidate()
            systemHealthTimer = nil
        }
        updateSystemHealthMenuItem()
    }

    private func currentSystemLoadRatio() -> Double {
        var averages = [Double](repeating: 0, count: 3)
        let samples = averages.withUnsafeMutableBufferPointer { buffer -> Int32 in
            guard let baseAddress = buffer.baseAddress else { return 0 }
            return getloadavg(baseAddress, Int32(buffer.count))
        }
        guard samples > 0 else { return 0 }
        let cpuCount = max(ProcessInfo.processInfo.processorCount, 1)
        return max(0, averages[0] / Double(cpuCount))
    }

    private func updateSystemHealthMenuItem() {
        let load = String(format: "%.2f", latestSystemLoadRatio)
        if systemHealthSamplingEnabled {
            systemHealthMenuItem?.title = "System: \(systemPressureLabel()) - load \(load) - \(thermalStateName(latestThermalState))"
        } else {
            systemHealthMenuItem?.title = "System: sampling off - last cost \(String(format: "%.1f", latestSystemHealthSampleCostMs))ms"
        }
    }

    private func systemPressureLabel() -> String {
        switch latestThermalState {
        case .serious, .critical:
            return "hot"
        case .fair:
            return "warm"
        default:
            if latestSystemLoadRatio >= HUDSignalStylePolicy.busySystemLoadRatio { return "busy" }
            if latestSystemLoadRatio >= HUDSignalStylePolicy.loadedSystemLoadRatio { return "loaded" }
            return "clear"
        }
    }

    private func thermalStateName(_ state: ProcessInfo.ThermalState) -> String {
        switch state {
        case .nominal:
            return "nominal"
        case .fair:
            return "fair"
        case .serious:
            return "serious"
        case .critical:
            return "critical"
        @unknown default:
            return "unknown"
        }
    }

    private func scheduleBackgroundLearning() {
        guard runtimeFlag("RAMBLEFIX_BACKGROUND_LEARNING", defaultValue: true) else { return }
        learningTimer?.invalidate()
        learningTimer = Timer.scheduledTimer(withTimeInterval: 1200, repeats: true) { [weak self] _ in
            self?.runBackgroundLearningIfIdle()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 90) { [weak self] in
            self?.runBackgroundLearningIfIdle()
        }
    }

    private func runBackgroundLearningIfIdle() {
        guard !isLearningTerms,
              activeRun == nil,
              !isRecordingActive(),
              !isTranscribing,
              !isFinalizing,
              !permissionRequestPending else {
            return
        }
        sampleSystemHealth()
        guard latestThermalState == .nominal, latestSystemLoadRatio < 0.75 else { return }
        isLearningTerms = true
        let python = ProcessInfo.processInfo.environment["RAMBLEFIX_PYTHON"]
            ?? projectRoot.appendingPathComponent(".venv/bin/python").path
        let historyPath = projectRoot.appendingPathComponent("logs/history.jsonl").path
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self else { return }
            _ = self.runProcess(
                python,
                [
                    "-m", "ramblefix.cli", "learn-from-history",
                    "--history", historyPath,
                    "--limit", "300",
                    "--min-count", "2",
                    "--skip-if-busy",
                    "--max-load-ratio", "0.75",
                    "--json"
                ],
                currentDirectory: self.projectRoot
            )
            DispatchQueue.main.async { [weak self] in
                self?.isLearningTerms = false
            }
        }
    }

    @objc private func refreshTranscriptHistory() {
        refreshHistoryTextView()
    }

    @objc private func openHistoryFolderFromWindow() {
        openHistoryFolder()
    }

    @objc private func openInputMonitoringSettings() {
        if meetingModeEnabled, !preflightScreenCapture(prompt: false) {
            openScreenRecordingSettings()
            return
        }
        openPrivacyPane("Privacy_ListenEvent")
    }

    private func openScreenRecordingSettings() {
        openPrivacyPane("Privacy_ScreenCapture")
    }

    private func openPrivacyPane(_ pane: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") {
            NSWorkspace.shared.open(url)
        }
    }

    private func updateMenuState() {
        let mode = activeRun?.mode
        dictateMenuItem?.title = mode == .dictation ? "Stop Dictation" : "Dictate"
        meetingMenuItem?.title = mode == .meeting ? "Stop Meeting Recording" : "Record Meeting"
        dictateMenuItem?.isEnabled = mode == nil || mode == .dictation
        meetingMenuItem?.isEnabled = meetingModeEnabled && (mode == nil || mode == .meeting)
        modeMenuItem?.title = mode == .meeting ? "Mode: Meeting" : "Mode: English Fast"
        captureEvalAudioMenuItem?.state = isCaptureEvalAudioEnabled() ? .on : .off
        captureEvalAudioMenuItem?.isEnabled = mode == nil
        copyLatestMenuItem?.isEnabled = latestTranscriptFromMemoryOrCache() != nil
    }

    private func startDictation() {
        hotkeyHeld = true
        if isRecordingActive() || isTranscribing { return }
        guard preflightAccessibility(prompt: true) else {
            notify("\(appName) needs Accessibility", "Enable Accessibility permission so \(appName) can paste into the active app.")
            return
        }
        permissionRequestPending = true
        preflightMicrophone { granted in
            self.permissionRequestPending = false
            guard granted else {
                self.notify("\(appName) needs Microphone", "Enable Microphone permission to record dictation.")
                return
            }
            guard self.hotkeyHeld else { return }
            self.startRecordingNow(mode: .dictation)
        }
    }

    private func startMeetingRecording() {
        guard meetingModeEnabled else {
            appendNativeEvent("meeting_mode_blocked_v0")
            notify("\(appName) V0 is dictation only", "Meeting recording is not included in the public V0 app.")
            showHUD(title: "Dictation only", subtitle: "Meeting mode is not in V0.", state: "INFO", accent: RFTheme.amber, autoHide: 3.0)
            updateMenuState()
            return
        }
        if isRecordingActive() || isTranscribing { return }
        if meetingSystemAudioEnabled(), !preflightScreenCapture(prompt: true) {
            appendNativeEvent("meeting_screen_recording_missing", fields: [
                "system_audio_enabled": true
            ])
            notify("\(appName) needs Screen Recording", "Enable Screen Recording to capture meeting audio locally.")
            showHUD(title: "Screen Recording needed", subtitle: "Enable it for meeting audio.", state: "FAIL", accent: RFTheme.coral, autoHide: 5.0)
            openScreenRecordingSettings()
            updateMenuState()
            return
        }
        permissionRequestPending = true
        preflightMicrophone { granted in
            self.permissionRequestPending = false
            guard granted else {
                self.notify("\(appName) needs Microphone", "Enable Microphone permission to record meeting audio.")
                return
            }
            self.startRecordingNow(mode: .meeting)
        }
    }

    private func startRecordingNow(mode: RunMode) {
        let runID = timestampRunID()
        let retainAudio = shouldRetainAllHotkeyAudio(mode: mode)
        let audioDir: URL
        if mode == .meeting {
            audioDir = projectRoot.appendingPathComponent("logs/meeting_audio", isDirectory: true)
        } else {
            audioDir = retainAudio
                ? projectRoot.appendingPathComponent("logs/hotkey_audio", isDirectory: true)
                : FileManager.default.temporaryDirectory.appendingPathComponent("RambleFixLocal", isDirectory: true)
        }
        do {
            try FileManager.default.createDirectory(at: audioDir, withIntermediateDirectories: true)
        } catch {
            notify("\(appName) setup failed", String(describing: error))
            return
        }
        let audioURL = audioDir.appendingPathComponent("\(runID).wav")
        let streamChunkDirectory = useStreamingCapture(for: mode)
            ? projectRoot.appendingPathComponent("logs/hindi_stream_chunks/\(runID)", isDirectory: true)
            : nil
        let target = NSWorkspace.shared.frontmostApplication
        let focusedElement = focusedTextElement()
        let run = ActiveRun(
            runID: runID,
            mode: mode,
            audioURL: audioURL,
            streamChunkDirectory: streamChunkDirectory,
            targetPID: target?.processIdentifier,
            targetBundleID: target?.bundleIdentifier ?? "",
            targetName: target?.localizedName ?? "Unknown",
            focusedElement: focusedElement,
            startedAt: Date()
        )
        do {
            if let streamChunkDirectory {
                let streaming = StreamingAudioRecorder(
                    audioURL: audioURL,
                    chunkDirectory: streamChunkDirectory,
                    targetChunkSeconds: streamingCaptureChunkSeconds(),
                    silenceChunkingEnabled: streamingSilenceChunkingEnabled(),
                    minChunkSeconds: streamingMinChunkSeconds(),
                    maxChunkSeconds: streamingMaxChunkSeconds(),
                    silenceLookaroundSeconds: streamingSilenceLookaroundSeconds(),
                    silenceLevelThreshold: streamingSilenceLevelThreshold()
                )
                guard try streaming.start() else {
                    notify("\(appName) capture failed", "Streaming recorder did not start.")
                    return
                }
                streamingRecorder = streaming
            } else {
                let settings: [String: Any] = [
                    AVFormatIDKey: kAudioFormatLinearPCM,
                    AVSampleRateKey: 16_000.0,
                    AVNumberOfChannelsKey: 1,
                    AVLinearPCMBitDepthKey: 16,
                    AVLinearPCMIsFloatKey: false,
                    AVLinearPCMIsBigEndianKey: false
                ]
                let audioRecorder = try AVAudioRecorder(url: audioURL, settings: settings)
                audioRecorder.isMeteringEnabled = true
                guard audioRecorder.prepareToRecord(), audioRecorder.record() else {
                    notify("\(appName) capture failed", "Recorder did not start.")
                    return
                }
                recorder = audioRecorder
            }
            if mode == .meeting, meetingSystemAudioEnabled() {
                let systemURL = meetingSystemAudioURL(for: audioURL)
                let systemRecorder = SystemAudioRecorder(audioURL: systemURL)
                systemAudioRecorder = systemRecorder
                systemRecorder.start()
                appendNativeEvent("meeting_system_audio_started", fields: [
                    "run_id": runID,
                    "audio_path": systemURL.path
                ])
            }
            activeRun = run
            appendNativeEvent("recording_started", fields: [
                "run_id": runID,
                "mode": mode.rawValue,
                "audio_path": audioURL.path,
                "streaming_capture": streamChunkDirectory != nil
            ])
            resetCaptureSignal()
            sampleSystemHealth()
            let recordingAccent = configureSystemHUDSignals(includeWeakCapture: false)
            recordingHUDAccent = recordingAccent
            hudMotionVariant = nextHUDMotionVariant()
            hudRecipeSeed = nextHUDRecipeSeed()
            statusItem?.button?.title = mode == .meeting ? "RF meet" : "RF rec"
            showHUD(title: "", subtitle: "", state: "REC", accent: recordingAccent, level: 0.28)
            playRecordingStartSound()
            startHindiStreamIfNeeded(run: run)
            startRecordingHUDTimer(runID: runID)
            updateMenuState()

            let defaultMax = mode == .meeting
                ? RecordingDurationPolicy.defaultMeetingMaxSeconds
                : RecordingDurationPolicy.defaultDictationMaxSeconds
            let envName = mode == .meeting ? "RAMBLEFIX_MEETING_MAX_SECONDS" : "RAMBLEFIX_HOTKEY_MAX_SECONDS"
            let maxSeconds = RecordingDurationPolicy.normalizedMaxSeconds(
                from: ProcessInfo.processInfo.environment[envName],
                defaultSeconds: defaultMax
            )
            DispatchQueue.main.asyncAfter(deadline: .now() + maxSeconds) {
                if self.activeRun?.runID == runID, self.isRecordingActive() {
                    if mode == .meeting {
                        self.stopMeetingRecordingAndTranscribe()
                    } else {
                        self.stopDictationAndTranscribe()
                    }
                }
            }
        } catch {
            appendNativeEvent("recording_start_failed", fields: [
                "mode": mode.rawValue,
                "error": String(describing: error)
            ])
            notify("\(appName) capture failed", String(describing: error))
        }
    }

    private func isRecordingActive() -> Bool {
        recorder != nil || streamingRecorder != nil || systemAudioRecorder != nil
    }

    private func useStreamingCapture(for mode: RunMode) -> Bool {
        mode == .dictation && envFlag("RAMBLEFIX_HOTKEY_STREAMING_CAPTURE", defaultValue: StreamingCaptureDefaults.dictationEnabled)
    }

    private func meetingSystemAudioEnabled() -> Bool {
        meetingModeEnabled && envFlag("RAMBLEFIX_MEETING_SYSTEM_AUDIO", defaultValue: true)
    }

    private func meetingSystemAudioURL(for audioURL: URL) -> URL {
        audioURL.deletingPathExtension().appendingPathExtension("system.wav")
    }

    private func meetingTranscriptionSources(for run: ActiveRun) -> [MeetingAudioSource] {
        var sources: [MeetingAudioSource] = []
        let systemURL = meetingSystemAudioURL(for: run.audioURL)
        if meetingSystemAudioEnabled(),
           FileManager.default.fileExists(atPath: systemURL.path),
           let duration = audioDurationSeconds(systemURL),
           duration >= 1.0 {
            sources.append(MeetingAudioSource(
                label: "Meeting audio",
                kind: "system",
                audioURL: systemURL,
                durationSeconds: duration
            ))
        }
        let micDuration = audioDurationSeconds(run.audioURL) ?? max(0, Date().timeIntervalSince(run.startedAt))
        sources.append(MeetingAudioSource(
            label: "My mic",
            kind: "mic",
            audioURL: run.audioURL,
            durationSeconds: micDuration
        ))
        return sources
    }

    private func streamingCaptureChunkSeconds() -> Double {
        envDouble("RAMBLEFIX_HOTKEY_STREAMING_CHUNK_SECONDS", defaultValue: StreamingCaptureDefaults.targetChunkSeconds, minValue: 3.0)
    }

    private func streamingSilenceChunkingEnabled() -> Bool {
        envFlag("RAMBLEFIX_HOTKEY_STREAMING_SILENCE_CHUNKS", defaultValue: StreamingCaptureDefaults.silenceChunkingEnabled)
    }

    private func streamingMinChunkSeconds() -> Double? {
        envOptionalDouble("RAMBLEFIX_HOTKEY_STREAMING_MIN_CHUNK_SECONDS", defaultValue: StreamingCaptureDefaults.minChunkSeconds, minValue: 1.0)
    }

    private func streamingMaxChunkSeconds() -> Double? {
        envOptionalDouble("RAMBLEFIX_HOTKEY_STREAMING_MAX_CHUNK_SECONDS", defaultValue: StreamingCaptureDefaults.maxChunkSeconds, minValue: streamingCaptureChunkSeconds())
    }

    private func streamingSilenceLookaroundSeconds() -> Double {
        envDouble("RAMBLEFIX_HOTKEY_STREAMING_SILENCE_LOOKAROUND_SECONDS", defaultValue: StreamingCaptureDefaults.silenceLookaroundSeconds, minValue: 0.0)
    }

    private func streamingSilenceLevelThreshold() -> CGFloat {
        CGFloat(envDouble("RAMBLEFIX_HOTKEY_STREAMING_SILENCE_LEVEL_THRESHOLD", defaultValue: StreamingCaptureDefaults.silenceLevelThreshold, minValue: 0.0))
    }

    private func startHindiStreamIfNeeded(run: ActiveRun) {
        guard runtimeFlag("RAMBLEFIX_HOTKEY_HINDI_STREAM_POLISH", defaultValue: false),
              let streamChunkDirectory = run.streamChunkDirectory else {
            return
        }
        let hindiTargetSeconds = envDouble("RAMBLEFIX_HOTKEY_HINDI_STREAM_TARGET_SECONDS", defaultValue: 4.0, minValue: 2.0)
        let hindiMinSeconds = envDouble("RAMBLEFIX_HOTKEY_HINDI_STREAM_MIN_SECONDS", defaultValue: StreamingCaptureDefaults.minChunkSeconds, minValue: 1.0)
        let hindiMaxSeconds = max(
            hindiMinSeconds,
            envDouble("RAMBLEFIX_HOTKEY_HINDI_STREAM_MAX_SECONDS", defaultValue: 5.0, minValue: 2.0)
        )
        let payload: [String: Any] = [
            "run_id": run.runID,
            "chunk_dir": streamChunkDirectory.path,
            "low_confidence_threshold": 0.50,
            "target_seconds": hindiTargetSeconds,
            "min_seconds": hindiMinSeconds,
            "max_seconds": hindiMaxSeconds,
            "poll_interval_seconds": 0.10
        ]
        DispatchQueue.global(qos: .utility).async {
            _ = self.postSrotaJSON(path: "/hindi-stream/start", payload: payload, timeout: 2.0)
        }
    }

    private func warmHindiStreamSidecarIfNeeded() {
        guard runtimeFlag("RAMBLEFIX_HOTKEY_HINDI_STREAM_POLISH", defaultValue: false),
              runtimeFlag("RAMBLEFIX_HOTKEY_STREAMING_CAPTURE", defaultValue: StreamingCaptureDefaults.dictationEnabled),
              runtimeFlag("RAMBLEFIX_HOTKEY_HINDI_STREAM_WARM_ON_LAUNCH", defaultValue: true) else {
            return
        }
        DispatchQueue.global(qos: .utility).async {
            _ = self.postSrotaJSON(path: "/hindi-stream/warm", payload: [:], timeout: 1.0)
        }
    }

    private func nextHUDMotionVariant() -> Int {
        let variantCount = max(1, min(HUDSignalStylePolicy.normalMotionVariantCount, hudMotionVariantCount))
        let first = min(max(HUDSignalStylePolicy.recordingSquiggleVariant, 0), variantCount - 1)
        let next = hudLastMotionVariant < 0 ? first : (hudLastMotionVariant + 1) % variantCount
        hudLastMotionVariant = next
        return next
    }

    private func nextHUDRecipeSeed() -> CGFloat {
        CGFloat.random(in: 0.05...0.95)
    }

    private func resetCaptureSignal() {
        capturePeakLevel = 0
        captureMeanLevel = 0
        captureLevelSamples = 0
    }

    private func updateCaptureSignal(level: CGFloat) {
        capturePeakLevel = max(capturePeakLevel, level)
        if captureLevelSamples == 0 {
            captureMeanLevel = level
        } else {
            captureMeanLevel = captureMeanLevel * 0.86 + level * 0.14
        }
        captureLevelSamples += 1
    }

    private func isCaptureLikelyWeak() -> Bool {
        captureLevelSamples >= 3 && (capturePeakLevel < 0.22 || captureMeanLevel < 0.09)
    }

    private func configureProcessingHUDSignals() -> NSColor {
        sampleSystemHealth()
        return configureSystemHUDSignals(includeWeakCapture: true)
    }

    private func configureSystemHUDSignals(includeWeakCapture: Bool) -> NSColor {
        switch latestThermalState {
        case .serious, .critical:
            hudColorShift = hueWithJitter(base: CGFloat(HUDSignalStylePolicy.dangerHueBase), jitter: 0.025)
            hudMotionSpeed = CGFloat.random(in: 0.14...0.22)
            hudMotionFrameInterval = hudPressureMotionFrameInterval
            return RFTheme.coral
        case .fair:
            hudColorShift = hueWithJitter(base: CGFloat(HUDSignalStylePolicy.warningHueBase), jitter: 0.035)
            hudMotionSpeed = CGFloat.random(in: 0.16...0.26)
            hudMotionFrameInterval = hudPressureMotionFrameInterval
            return RFTheme.amber
        default:
            if latestSystemLoadRatio >= HUDSignalStylePolicy.busySystemLoadRatio {
                hudColorShift = hueWithJitter(base: CGFloat(HUDSignalStylePolicy.dangerHueBase), jitter: 0.025)
                hudMotionSpeed = CGFloat.random(in: 0.14...0.22)
                hudMotionFrameInterval = hudPressureMotionFrameInterval
                return RFTheme.coral
            }
            if includeWeakCapture, isCaptureLikelyWeak() {
                hudColorShift = hueWithJitter(base: CGFloat(HUDSignalStylePolicy.warningHueBase), jitter: 0.04)
                hudMotionSpeed = CGFloat.random(in: 0.18...0.28)
                hudMotionFrameInterval = hudDefaultMotionFrameInterval
                return RFTheme.amber
            }
            if latestSystemLoadRatio >= HUDSignalStylePolicy.loadedSystemLoadRatio {
                hudColorShift = hueWithJitter(base: CGFloat(HUDSignalStylePolicy.warningHueBase), jitter: 0.035)
                hudMotionSpeed = CGFloat.random(in: 0.16...0.28)
                hudMotionFrameInterval = hudDefaultMotionFrameInterval
                return RFTheme.amber
            }
            hudColorShift = hueWithJitter(base: CGFloat(HUDSignalStylePolicy.healthyHueBase), jitter: 0.035)
            hudMotionSpeed = CGFloat.random(in: 0.22...0.42)
            hudMotionFrameInterval = hudDefaultMotionFrameInterval
            return RFTheme.cyan
        }
    }

    private func hueWithJitter(base: CGFloat, jitter: CGFloat) -> CGFloat {
        normalizeHue(base + CGFloat.random(in: -jitter...jitter))
    }

    private func normalizeHue(_ value: CGFloat) -> CGFloat {
        let hue = value.truncatingRemainder(dividingBy: 1.0)
        return hue < 0 ? hue + 1.0 : hue
    }

    private func stopDictationAndTranscribe() {
        hotkeyHeld = false
        permissionRequestPending = false
        guard let run = activeRun, isRecordingActive() else { return }
        guard run.mode == .dictation else { return }
        recorder?.stop()
        streamingRecorder?.stop()
        recorder = nil
        streamingRecorder = nil
        activeRun = nil
        let heldSeconds = Date().timeIntervalSince(run.startedAt)
        appendNativeEvent("dictation_stopped", fields: [
            "run_id": run.runID,
            "held_seconds": roundedSeconds(heldSeconds),
            "audio_path": run.audioURL.path
        ])
        let minimumSeconds = Double(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_MIN_SECONDS"] ?? "0.9") ?? 0.9
        if heldSeconds < minimumSeconds {
            stopRecordingHUDTimer()
            let audioSavedAt = Date()
            let retainedAudioURL = retainAudioForDebugIfNeeded(run: run, reason: "too_short")
            appendHotkeyHistory(run: run, payload: nil, status: "too_short", errorType: "too_short_capture", audioSavedAt: audioSavedAt, asrStartedAt: audioSavedAt, asrEndedAt: audioSavedAt, pasteStartedAt: nil, pasteEndedAt: nil, retainedAudioURL: retainedAudioURL, blankOrNoSpeech: true, pasteSuccess: false)
            hotkeyHeld = false
            cleanupAudioIfNeeded(run.audioURL)
            statusItem?.button?.title = menuBarIdleTitle
            showHUD(title: "Too short", subtitle: "Hold a little longer. Nothing pasted.", state: "SKIP", accent: RFTheme.amber, autoHide: 2.5)
            updateMenuState()
            return
        }
        stopRecordingHUDTimer()
        isTranscribing = true
        statusItem?.button?.title = "\(menuBarIdleTitle)..."
        let workAccent = configureProcessingHUDSignals()
        hudMotionVariant = HUDSignalStylePolicy.englishMotionVariant
        hudRecipeSeed = nextHUDRecipeSeed()
        showHUD(title: "Transcribing", subtitle: "Running locally. Text will paste after this.", state: "WORK", accent: workAccent)
        startSlowProcessingFeedback(runID: run.runID)
        updateMenuState()
        let audioSavedAt = Date()

        DispatchQueue.global(qos: .userInitiated).async {
            let result = self.transcribe(run: run)
            DispatchQueue.main.async {
                self.finish(run: run, audioSavedAt: audioSavedAt, result: result)
            }
        }
    }

    private func stopMeetingRecordingAndTranscribe() {
        hotkeyHeld = false
        permissionRequestPending = false
        guard let run = activeRun, isRecordingActive() else { return }
        guard run.mode == .meeting else { return }
        recorder?.stop()
        streamingRecorder?.stop()
        let stoppedSystemRecorder = systemAudioRecorder
        stoppedSystemRecorder?.stop()
        recorder = nil
        streamingRecorder = nil
        systemAudioRecorder = nil
        activeRun = nil
        stopRecordingHUDTimer()
        isTranscribing = true
        statusItem?.button?.title = "RF meet..."
        let workAccent = configureProcessingHUDSignals()
        hudMotionVariant = HUDSignalStylePolicy.englishMotionVariant
        hudRecipeSeed = nextHUDRecipeSeed()
        showHUD(title: "Transcribing meeting", subtitle: "Saving local transcript.", state: "WORK", accent: workAccent)
        startSlowProcessingFeedback(runID: run.runID)
        updateMenuState()
        let audioSavedAt = Date()
        if let error = stoppedSystemRecorder?.lastError {
            appendNativeEvent("meeting_system_audio_error", fields: [
                "run_id": run.runID,
                "error": error
            ])
        }

        DispatchQueue.global(qos: .utility).async {
            let result = self.transcribeMeeting(run: run)
            DispatchQueue.main.async {
                self.finishMeeting(run: run, audioSavedAt: audioSavedAt, result: result)
            }
        }
    }

    private func transcribe(run: ActiveRun) -> (payload: DictationPayload?, error: String?, asrStartedAt: Date, asrEndedAt: Date) {
        if envFlag("RAMBLEFIX_HOTKEY_NATIVE_WHISPER_SERVER", defaultValue: true) {
            let directStartedAt = Date()
            do {
                let endpoint = nativeWhisperServerEndpoint()
                if endpoint.port == 8188 {
                    ensureNativeASRServerReady(endpoint: endpoint, runID: run.runID)
                } else {
                    ensureWhisperSidecarReady(endpoint: endpoint, runID: run.runID)
                }
                let timeout = Double(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_NATIVE_WHISPER_TIMEOUT_SECONDS"] ?? "") ?? 20.0
                let transcript = try LocalWhisperServerClient.transcribe(
                    audioURL: run.audioURL,
                    endpoint: endpoint,
                    timeout: timeout
                )
                let directEndedAt = Date()
                appendNativeEvent("native_whisper_server_transcribed", fields: [
                    "run_id": run.runID,
                    "seconds": transcript.seconds,
                    "route": transcript.route,
                    "fallback_reason": transcript.fallbackReason
                ])
                return (payloadFromTranscript(transcript), nil, directStartedAt, directEndedAt)
            } catch {
                appendNativeEvent("native_whisper_server_fallback", fields: [
                    "run_id": run.runID,
                    "error": String(describing: error)
                ])
            }
        }
        let python = ProcessInfo.processInfo.environment["RAMBLEFIX_PYTHON"]
            ?? projectRoot.appendingPathComponent(".venv/bin/python").path
        let asrStartedAt = Date()
        let dictate = runProcess(
            python,
            ["-m", "ramblefix.cli", "dictate-audio", run.audioURL.path, "--json", "--no-cleanup", "--skip-process-fallback"],
            currentDirectory: projectRoot
        )
        let asrEndedAt = Date()
        guard dictate.exitCode == 0 else {
            return (nil, dictate.stderr.isEmpty ? dictate.stdout : dictate.stderr, asrStartedAt, asrEndedAt)
        }
        guard let payload = parsePayload(dictate.stdout), !payload.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return (nil, "\(appName) produced no text.", asrStartedAt, asrEndedAt)
        }
        return (payload, nil, asrStartedAt, asrEndedAt)
    }

    private func nativeWhisperServerEndpoint() -> URL {
        let raw = ProcessInfo.processInfo.environment["RAMBLEFIX_WHISPER_CPP_SERVER_URL"]
            ?? ProcessInfo.processInfo.environment["RAMBLEFIX_NATIVE_WHISPER_SERVER_URL"]
            ?? "http://127.0.0.1:8188/inference"
        let fallback = URL(string: "http://127.0.0.1:8188/inference")!
        guard let url = URL(string: raw), isLoopbackHost(url.host) else {
            return fallback
        }
        return url
    }

    private func isLoopbackHost(_ host: String?) -> Bool {
        guard let host = host?.lowercased() else { return false }
        return host == "localhost" || host == "127.0.0.1" || host == "::1"
    }

    private func payloadFromTranscript(_ transcript: LocalWhisperServerTranscript) -> DictationPayload {
        let corrected = applyApprovedPhraseFixes(to: transcript.text)
        var quality = jsonQualityToAny(transcript.quality)
        if corrected.changed {
            quality["glossary_changed"] = true
            quality["glossary_processor"] = "approved_phrase_fixes"
        }
        return DictationPayload(
            rawText: transcript.rawText,
            text: corrected.text,
            engine: transcript.engine,
            processor: corrected.changed ? "glossary" : transcript.processor,
            fallbackReason: transcript.fallbackReason,
            quality: quality,
            seconds: transcript.seconds,
            route: transcript.route,
            safeUpdate: false
        )
    }

    private func jsonQualityToAny(_ quality: [String: JSONValue]) -> [String: Any] {
        var converted: [String: Any] = [:]
        for (key, value) in quality {
            switch value {
            case .string(let string):
                converted[key] = string
            case .double(let double):
                converted[key] = double
            case .bool(let bool):
                converted[key] = bool
            }
        }
        return converted
    }

    private func transcribeMeeting(run: ActiveRun) -> (payload: DictationPayload?, error: String?, asrStartedAt: Date, asrEndedAt: Date) {
        let python = ProcessInfo.processInfo.environment["RAMBLEFIX_PYTHON"]
            ?? projectRoot.appendingPathComponent(".venv/bin/python").path
        let outputDir = projectRoot.appendingPathComponent("logs/meeting_transcripts/\(run.runID)", isDirectory: true)
        let chunkSeconds = ProcessInfo.processInfo.environment["RAMBLEFIX_MEETING_CHUNK_SECONDS"] ?? "30"
        let mode = ProcessInfo.processInfo.environment["RAMBLEFIX_MEETING_ENGINE_MODE"] ?? "auto"
        let asrStartedAt = Date()
        let sources = meetingTranscriptionSources(for: run)
        appendNativeEvent("meeting_transcription_sources", fields: [
            "run_id": run.runID,
            "sources": sources.map { $0.kind },
            "durations": sources.map { roundedSeconds($0.durationSeconds) }
        ])
        var transcripts: [MeetingSourceTranscript] = []
        var errors: [String] = []
        for source in sources {
            let sourceOutputDir = outputDir.appendingPathComponent(source.kind, isDirectory: true)
            let dictate = runProcess(
                python,
                [
                    "-m", "ramblefix.cli", "meeting-transcribe-audio",
                    source.audioURL.path,
                    "--json",
                    "--output-dir", sourceOutputDir.path,
                    "--chunk-seconds", chunkSeconds,
                    "--mode", mode
                ],
                currentDirectory: projectRoot
            )
            guard dictate.exitCode == 0 else {
                let error = dictate.stderr.isEmpty ? dictate.stdout : dictate.stderr
                errors.append("\(source.kind): \(error)")
                continue
            }
            guard let payload = parsePayload(dictate.stdout),
                  !payload.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                errors.append("\(source.kind): \(appName) produced no meeting transcript.")
                continue
            }
            transcripts.append(MeetingSourceTranscript(source: source, payload: payload))
        }
        let asrEndedAt = Date()
        guard !transcripts.isEmpty else {
            return (nil, errors.isEmpty ? "\(appName) produced no meeting transcript." : errors.joined(separator: "\n"), asrStartedAt, asrEndedAt)
        }
        guard transcripts.count > 1 else {
            return (transcripts[0].payload, nil, asrStartedAt, asrEndedAt)
        }
        let combinedText = MeetingTranscriptCombiner.combinedText(
            sections: transcripts.map {
                MeetingTranscriptSection(label: $0.source.label, text: $0.payload.text)
            }
        )
        let payload = DictationPayload(
            rawText: combinedText,
            text: combinedText,
            engine: "ramblefix_meeting_engine_v1:multi_source",
            processor: "meeting-engine",
            fallbackReason: errors.joined(separator: "\n"),
            quality: [
                "audio_sources": transcripts.map {
                    [
                        "kind": $0.source.kind,
                        "label": $0.source.label,
                        "audio_path": $0.source.audioURL.path,
                        "audio_seconds": roundedSeconds($0.source.durationSeconds),
                        "engine": $0.payload.engine,
                        "route": $0.payload.route,
                        "seconds": $0.payload.seconds
                    ]
                },
                "failed_sources": errors,
                "source_count": transcripts.count
            ],
            seconds: roundedSeconds(asrEndedAt.timeIntervalSince(asrStartedAt)),
            route: "meeting_\(mode)_multi_source",
            safeUpdate: false
        )
        return (payload, nil, asrStartedAt, asrEndedAt)
    }

    private func finish(run: ActiveRun, audioSavedAt: Date, result: (payload: DictationPayload?, error: String?, asrStartedAt: Date, asrEndedAt: Date)) {
        var finalizerStarted = false
        defer {
            isTranscribing = false
            stopSlowProcessingFeedback()
            statusItem?.button?.title = result.error == nil ? menuBarIdleTitle : "\(menuBarIdleTitle)!"
            updateMenuState()
            if !finalizerStarted {
                cleanupAudioIfNeeded(run.audioURL)
            }
        }
        guard let payload = result.payload else {
            notify("\(appName) transcription failed", result.error ?? "Unknown error")
            let retainedAudioURL = retainAudioForDebugIfNeeded(run: run, reason: "asr_error")
            appendHotkeyHistory(run: run, payload: nil, status: "failed", errorType: "asr_error", audioSavedAt: audioSavedAt, asrStartedAt: result.asrStartedAt, asrEndedAt: result.asrEndedAt, pasteStartedAt: nil, pasteEndedAt: nil, retainedAudioURL: retainedAudioURL, pasteSuccess: false)
            showHUD(title: "Transcription failed", subtitle: "Saved failure details in history.", state: "FAIL", accent: RFTheme.coral, autoHide: 3.0)
            refreshHistoryWindowIfVisible()
            return
        }
        if isRetryableEmptyPayload(payload) {
            showHUD(title: "Working", subtitle: "Trying local rescue path.", state: "WORK", accent: RFTheme.amber, autoHide: 8.0)
            finalizerStarted = startBackgroundFallbackRescueIfNeeded(run: run, draftPayload: payload, audioSavedAt: audioSavedAt, pasteEndedAt: result.asrEndedAt, draftWasPasted: false)
            if finalizerStarted {
                return
            }
            finalizerStarted = startBackgroundFinalizerIfNeeded(run: run, draftPayload: payload, audioSavedAt: audioSavedAt, pasteEndedAt: result.asrEndedAt, draftWasPasted: false)
            if finalizerStarted {
                return
            }
        }
        if shouldRunFallbackRescue(for: payload) {
            showHUD(title: "Working", subtitle: "Running full local pass.", state: "WORK", accent: RFTheme.amber, autoHide: 10.0)
            finalizerStarted = startBackgroundFallbackRescueIfNeeded(run: run, draftPayload: payload, audioSavedAt: audioSavedAt, pasteEndedAt: result.asrEndedAt, draftWasPasted: false)
            if finalizerStarted {
                return
            }
            let retainedAudioURL = retainAudioForDebugIfNeeded(run: run, reason: "fallback_rescue_unavailable")
            appendHotkeyHistory(run: run, payload: payload, status: "blocked_low_quality", errorType: "fallback_rescue_unavailable", audioSavedAt: audioSavedAt, asrStartedAt: result.asrStartedAt, asrEndedAt: result.asrEndedAt, pasteStartedAt: nil, pasteEndedAt: nil, retainedAudioURL: retainedAudioURL, pasteSuccess: false)
            showHUD(title: "Low quality capture", subtitle: "Nothing pasted. The clip is in history.", state: "SKIP", accent: RFTheme.amber, autoHide: 2.5)
            refreshHistoryWindowIfVisible()
            return
        }
        if isNoSpeechPayload(payload) {
            let retainedAudioURL = retainAudioForDebugIfNeeded(run: run, reason: "no_speech")
            notify("\(appName) heard no speech", "Nothing pasted.")
            appendHotkeyHistory(run: run, payload: payload, status: "no_speech", errorType: "blank_or_no_speech", audioSavedAt: audioSavedAt, asrStartedAt: result.asrStartedAt, asrEndedAt: result.asrEndedAt, pasteStartedAt: nil, pasteEndedAt: nil, retainedAudioURL: retainedAudioURL, blankOrNoSpeech: true, pasteSuccess: false)
            showHUD(title: "No speech detected", subtitle: "Nothing pasted. The clip is in history.", state: "SKIP", accent: RFTheme.amber, autoHide: 2.5)
            refreshHistoryWindowIfVisible()
            return
        }
        if isBlockedTranscriptionPayload(payload) {
            let retainedAudioURL = retainAudioForDebugIfNeeded(run: run, reason: "low_quality")
            appendHotkeyHistory(run: run, payload: payload, status: "blocked_low_quality", errorType: "low_quality_transcript", audioSavedAt: audioSavedAt, asrStartedAt: result.asrStartedAt, asrEndedAt: result.asrEndedAt, pasteStartedAt: nil, pasteEndedAt: nil, retainedAudioURL: retainedAudioURL, pasteSuccess: false)
            showHUD(title: "Low quality capture", subtitle: "Nothing pasted. The clip is in history.", state: "SKIP", accent: RFTheme.amber, autoHide: 2.5)
            refreshHistoryWindowIfVisible()
            return
        }
        let pastePayload = payload
        let pasteStartedAt = Date()
        let pasteResult = pasteWithResult(pastePayload.text, targetPID: run.targetPID, targetBundleID: run.targetBundleID)
        let pasteEndedAt = Date()
        let pasted = pasteResult.attempted && !pasteResult.copyFallbackRecommended
        let pasteErrorType = pasteResult.attempted ? "paste_unverified" : "paste_target_error"
        let retainedAudioURL = pasted ? nil : retainAudioForDebugIfNeeded(run: run, reason: pasteErrorType)
        appendHotkeyHistory(run: run, payload: pastePayload, status: pasted ? "paste_attempted" : "copy_fallback", errorType: pasted ? "" : pasteErrorType, audioSavedAt: audioSavedAt, asrStartedAt: result.asrStartedAt, asrEndedAt: result.asrEndedAt, pasteStartedAt: pasteStartedAt, pasteEndedAt: pasteEndedAt, retainedAudioURL: retainedAudioURL, pasteSuccess: pasted)
        latestTranscript = pastePayload.text
        updateMenuState()
        if !pasted {
            showHUD(
                title: transcriptPreview(pastePayload.text),
                subtitle: "",
                state: "COPY",
                accent: RFTheme.mint,
                autoHide: 8.0,
                copyText: pastePayload.text
            )
            if !pasteResult.attempted {
                notify("\(appName) paste skipped", "The original target app was no longer available.")
            }
            finalizerStarted = startBackgroundPolishIfNeeded(run: run, draftPayload: pastePayload, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: false)
            return
        }
        hideHUD()
        playPasteDoneSound()
        refreshHistoryWindowIfVisible()
        let postPasteFocusedElement = focusedTextElement()
        appendNativeEvent("post_paste_focus_cached", fields: [
            "run_id": run.runID,
            "available": postPasteFocusedElement != nil
        ])
        let replacementRun = run.withFocusedElement(postPasteFocusedElement)
        finalizerStarted = startBackgroundPolishIfNeeded(run: replacementRun, draftPayload: pastePayload, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: true)
    }

    private func finishMeeting(run: ActiveRun, audioSavedAt: Date, result: (payload: DictationPayload?, error: String?, asrStartedAt: Date, asrEndedAt: Date)) {
        defer {
            isTranscribing = false
            stopSlowProcessingFeedback()
            statusItem?.button?.title = result.error == nil ? menuBarIdleTitle : "\(menuBarIdleTitle)!"
            updateMenuState()
        }
        guard let payload = result.payload else {
            notify("\(appName) meeting transcription failed", result.error ?? "Unknown error")
            appendHotkeyHistory(run: run, payload: nil, status: "failed", errorType: "meeting_asr_error", audioSavedAt: audioSavedAt, asrStartedAt: result.asrStartedAt, asrEndedAt: result.asrEndedAt, pasteStartedAt: nil, pasteEndedAt: nil)
            return
        }
        let transcriptURL = run.audioURL.deletingPathExtension().appendingPathExtension("txt")
        try? payload.text.write(to: transcriptURL, atomically: true, encoding: .utf8)
        appendHotkeyHistory(run: run, payload: payload, status: "meeting_transcribed", errorType: "", audioSavedAt: audioSavedAt, asrStartedAt: result.asrStartedAt, asrEndedAt: result.asrEndedAt, pasteStartedAt: nil, pasteEndedAt: nil)
        latestTranscript = payload.text
        hideHUD()
        refreshHistoryWindowIfVisible()
        notify("\(appName) meeting saved", "Transcript saved to \(transcriptURL.lastPathComponent).")
    }

    private func startBackgroundFinalizerIfNeeded(run: ActiveRun, draftPayload: DictationPayload, audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool = true) -> Bool {
        guard runtimeFlag("RAMBLEFIX_HOTKEY_FINALIZER", defaultValue: true) else { return false }
        guard shouldRunFinalizer(for: draftPayload) else { return false }
        isFinalizing = true
        statusItem?.button?.title = "RF fin"
        showBackgroundPolishHUD(action: .finalizer, run: run)
        DispatchQueue.global(qos: .utility).async {
            let result = self.finalize(run: run)
            DispatchQueue.main.async {
                self.finishFinalizer(run: run, draftPayload: draftPayload, result: result, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: draftWasPasted)
            }
        }
        return true
    }

    private func startBackgroundPolishIfNeeded(run: ActiveRun, draftPayload: DictationPayload, audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool = true, after previousAction: BackgroundPolishAction? = nil) -> Bool {
        let actions: [BackgroundPolishAction]
        if let previousAction,
           let index = BackgroundPolishPolicy.defaultOrder.firstIndex(of: previousAction) {
            actions = Array(BackgroundPolishPolicy.defaultOrder.dropFirst(index + 1))
        } else {
            actions = BackgroundPolishPolicy.defaultOrder
        }
        for action in actions {
            switch action {
            case .fallbackRescue:
                if startBackgroundFallbackRescueIfNeeded(run: run, draftPayload: draftPayload, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: draftWasPasted) {
                    return true
                }
            case .processSecondPass:
                if startBackgroundProcessSecondPassIfNeeded(run: run, draftPayload: draftPayload, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: draftWasPasted) {
                    return true
                }
            case .termPolish:
                if startBackgroundTermPolishIfNeeded(run: run, draftPayload: draftPayload, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: draftWasPasted) {
                    return true
                }
            case .structure:
                if startBackgroundStructureIfNeeded(run: run, draftPayload: draftPayload, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: draftWasPasted) {
                    return true
                }
            case .finalizer:
                if startBackgroundFinalizerIfNeeded(run: run, draftPayload: draftPayload, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: draftWasPasted) {
                    return true
                }
            case .hindiPolish:
                if startBackgroundHindiPolishIfNeeded(run: run, draftPayload: draftPayload, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: draftWasPasted) {
                    return true
                }
            }
        }
        return false
    }

    private func startBackgroundFallbackRescueIfNeeded(run: ActiveRun, draftPayload: DictationPayload, audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool = true) -> Bool {
        guard runtimeFlag("RAMBLEFIX_HOTKEY_FALLBACK_RESCUE", defaultValue: true) else { return false }
        guard shouldRunFallbackRescue(for: draftPayload) else { return false }
        isFinalizing = true
        statusItem?.button?.title = "RF rescue"
        showBackgroundPolishHUD(action: .fallbackRescue, run: run)
        DispatchQueue.global(qos: .utility).async {
            let result = self.fallbackRescue(run: run)
            DispatchQueue.main.async {
                self.finishFallbackRescue(run: run, draftPayload: draftPayload, result: result, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: draftWasPasted)
            }
        }
        return true
    }

    private func fallbackRescue(run: ActiveRun) -> (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date) {
        let python = ProcessInfo.processInfo.environment["RAMBLEFIX_PYTHON"]
            ?? projectRoot.appendingPathComponent(".venv/bin/python").path
        let startedAt = Date()
        let dictate = runProcess(
            python,
            ["-m", "ramblefix.cli", "dictate-audio", run.audioURL.path, "--json", "--no-cleanup", "--skip-process-fallback"],
            currentDirectory: projectRoot
        )
        let endedAt = Date()
        guard dictate.exitCode == 0 else {
            return (nil, dictate.stderr.isEmpty ? dictate.stdout : dictate.stderr, startedAt, endedAt)
        }
        guard let payload = parsePayload(dictate.stdout), !payload.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return (nil, "\(appName) rescue path produced no text.", startedAt, endedAt)
        }
        return (payload, nil, startedAt, endedAt)
    }

    private func finishFallbackRescue(run: ActiveRun, draftPayload: DictationPayload, result: (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date), audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool = true) {
        defer {
            isFinalizing = false
            statusItem?.button?.title = menuBarIdleTitle
            cleanupAudioIfNeeded(run.audioURL)
        }
        guard let finalPayload = result.payload,
              !isNoSpeechPayload(finalPayload),
              shouldUseFallbackRescue(draft: draftPayload.text, final: finalPayload.text) else {
            let blankOrNoSpeech = result.payload.map { isNoSpeechPayload($0) } ?? false
            appendHotkeyHistory(run: run, payload: result.payload ?? draftPayload, status: "fallback_rescue_skipped", errorType: result.error == nil ? (blankOrNoSpeech ? "blank_or_no_speech" : "") : "fallback_rescue_error", audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: nil, pasteEndedAt: nil, blankOrNoSpeech: blankOrNoSpeech, pasteSuccess: nil)
            hideBackgroundPolishHUDIfNeeded()
            return
        }
        if !draftWasPasted || draftPayload.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let pasteStartedAt = Date()
            let pasteResult = pasteWithResult(finalPayload.text, targetPID: run.targetPID, targetBundleID: run.targetBundleID)
            let pasted = pasteResult.attempted && !pasteResult.copyFallbackRecommended
            appendHotkeyHistory(run: run, payload: finalPayload, status: pasted ? "fallback_rescue_pasted" : "fallback_rescue_saved", errorType: pasted ? "" : "safe_paste_unavailable", audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: pasteStartedAt, pasteEndedAt: Date(), pasteSuccess: pasted)
            latestTranscript = finalPayload.text
            refreshHistoryWindowIfVisible()
            if pasted {
                hideHUD()
                playPasteDoneSound()
                return
            }
            showBackgroundCopyFallbackIfNeeded(finalPayload.text, draftWasPasted: draftWasPasted, replacementSucceeded: pasted)
            return
        }
        let replacement = replaceDraftIfUnchanged(draft: draftPayload.text, final: finalPayload.text, targetPID: run.targetPID, targetBundleID: run.targetBundleID, cachedFocusedElement: run.focusedElement, runID: run.runID, action: "fallbackRescue")
        let replaced = replacement.replaced
        appendHotkeyHistory(run: run, payload: finalPayload, status: replaced ? "fallback_rescue_replaced" : "fallback_rescue_saved", errorType: safeReplaceErrorType(replacement), audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: pasteEndedAt, pasteEndedAt: Date(), pasteSuccess: replaced)
        latestTranscript = finalPayload.text
        refreshHistoryWindowIfVisible()
        if replaced {
            hideHUD()
            return
        }
        showBackgroundCopyFallbackIfNeeded(finalPayload.text, draftWasPasted: draftWasPasted, replacementSucceeded: replaced)
    }

    private func startBackgroundStructureIfNeeded(run: ActiveRun, draftPayload: DictationPayload, audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool = true) -> Bool {
        guard runtimeFlag("RAMBLEFIX_HOTKEY_STRUCTURE", defaultValue: runtimeFlag("RAMBLEFIX_HOTKEY_LIGHT_POLISH", defaultValue: true)) else { return false }
        guard shouldRunStructure(for: draftPayload) else { return false }
        isFinalizing = true
        statusItem?.button?.title = "RF struct"
        showBackgroundPolishHUD(action: .structure, run: run)
        DispatchQueue.global(qos: .utility).async {
            let result = self.structure(draftPayload: draftPayload)
            DispatchQueue.main.async {
                self.finishStructure(run: run, draftPayload: draftPayload, result: result, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: draftWasPasted)
            }
        }
        return true
    }

    private func structure(draftPayload: DictationPayload) -> (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date) {
        let startedAt = Date()
        let rewrite = FriendlyRewritePolicy.rewrite(text: draftPayload.text)
        let endedAt = Date()
        guard rewrite.changed else {
            return (nil, nil, startedAt, endedAt)
        }
        var quality = draftPayload.quality
        quality["structure_rules"] = rewrite.rules
        quality["structure_changed"] = true
        quality["structure_input"] = "exact_pasted_text"
        let payload = DictationPayload(
            rawText: draftPayload.rawText,
            text: rewrite.text,
            engine: draftPayload.engine,
            processor: "structure",
            fallbackReason: draftPayload.fallbackReason,
            quality: quality,
            seconds: roundedSeconds(endedAt.timeIntervalSince(startedAt)),
            route: "structure",
            safeUpdate: true
        )
        return (payload, nil, startedAt, endedAt)
    }

    private func finishStructure(run: ActiveRun, draftPayload: DictationPayload, result: (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date), audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool) {
        defer {
            isFinalizing = false
            statusItem?.button?.title = menuBarIdleTitle
            cleanupAudioIfNeeded(run.audioURL)
        }
        guard let finalPayload = result.payload,
              !isNoSpeechPayload(finalPayload),
              shouldUseStructure(draft: draftPayload.text, final: finalPayload.text) else {
            appendHotkeyHistory(run: run, payload: result.payload ?? draftPayload, status: "structure_skipped", errorType: result.error == nil ? "" : "structure_error", audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: nil, pasteEndedAt: nil, pasteSuccess: nil)
            hideBackgroundPolishHUDIfNeeded()
            return
        }
        let replacement = replaceDraftIfUnchanged(draft: draftPayload.text, final: finalPayload.text, targetPID: run.targetPID, targetBundleID: run.targetBundleID, cachedFocusedElement: run.focusedElement, runID: run.runID, action: "structure")
        let replaced = replacement.replaced
        appendHotkeyHistory(run: run, payload: finalPayload, status: replaced ? "structure_replaced" : "structure_saved", errorType: safeReplaceErrorType(replacement), audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: pasteEndedAt, pasteEndedAt: Date(), pasteSuccess: replaced)
        latestTranscript = finalPayload.text
        refreshHistoryWindowIfVisible()
        if replaced {
            hideHUD()
            return
        }
        showBackgroundCopyFallbackIfNeeded(finalPayload.text, draftWasPasted: draftWasPasted, replacementSucceeded: replaced)
    }

    private func startBackgroundProcessSecondPassIfNeeded(run: ActiveRun, draftPayload: DictationPayload, audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool = true) -> Bool {
        guard runtimeFlag("RAMBLEFIX_HOTKEY_PROCESS_SECOND_PASS", defaultValue: true) else { return false }
        guard shouldRunProcessSecondPass(for: draftPayload) else { return false }
        isFinalizing = true
        statusItem?.button?.title = "RF check"
        showBackgroundPolishHUD(action: .processSecondPass, run: run)
        DispatchQueue.global(qos: .utility).async {
            let result = self.processSecondPass(run: run, draftPayload: draftPayload)
            DispatchQueue.main.async {
                self.finishProcessSecondPass(run: run, draftPayload: draftPayload, result: result, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: draftWasPasted)
            }
        }
        return true
    }

    private func processSecondPass(run: ActiveRun, draftPayload: DictationPayload) -> (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date) {
        let startedAt = Date()
        let threshold = Double(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_PROCESS_SECOND_PASS_RISK_THRESHOLD"] ?? "") ?? 0.60
        let riskResponse = postSrotaJSON(
            path: "/hindi-risk",
            payload: [
                "audio_path": run.audioURL.path,
                "draft_text": draftPayload.text,
                "low_confidence_threshold": threshold
            ],
            timeout: 2.0
        )
        guard riskResponse.error == nil else {
            return (nil, riskResponse.error, startedAt, Date())
        }
        guard riskResponse.payload?["risk"] as? Bool == true else {
            return (nil, nil, startedAt, Date())
        }

        let backend = ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_PROCESS_SECOND_PASS_BACKEND"] ?? "accurate_en"
        let processResponse = postSrotaJSON(
            path: "/process-second-pass",
            payload: [
                "audio_path": run.audioURL.path,
                "backend": backend
            ],
            timeout: max(1.0, Double(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_PROCESS_SECOND_PASS_TIMEOUT"] ?? "") ?? 5.5)
        )
        let endedAt = Date()
        guard processResponse.error == nil else {
            return (nil, processResponse.error, startedAt, endedAt)
        }
        guard let rawPayload = processResponse.payload,
              let payload = payloadFromJSON(rawPayload),
              !payload.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return (nil, "\(appName) process second pass produced no text.", startedAt, endedAt)
        }
        let processAccepted = shouldUseProcessSecondPassCandidate(
            draftPayload: draftPayload,
            finalPayload: payload,
            riskPayload: riskResponse.payload,
            run: run
        )
        guard processAccepted else {
            return (nil, nil, startedAt, endedAt)
        }
        if let rescuePayload = shortHinglishRescueIfUseful(
            run: run,
            draftPayload: draftPayload,
            processPayload: payload,
            riskPayload: riskResponse.payload
        ) {
            return (rescuePayload, nil, startedAt, Date())
        }
        return (payload, nil, startedAt, endedAt)
    }

    private func finishProcessSecondPass(run: ActiveRun, draftPayload: DictationPayload, result: (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date), audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool) {
        defer {
            isFinalizing = false
            statusItem?.button?.title = menuBarIdleTitle
            cleanupAudioIfNeeded(run.audioURL)
        }
        guard let finalPayload = result.payload,
              !isNoSpeechPayload(finalPayload),
              shouldUseProcessSecondPass(draft: draftPayload.text, final: finalPayload.text) else {
            let blankOrNoSpeech = result.payload.map { isNoSpeechPayload($0) } ?? false
            let errorType = result.error == nil ? (blankOrNoSpeech ? "blank_or_no_speech" : "no_hindi_risk") : "process_second_pass_error"
            appendHotkeyHistory(run: run, payload: result.payload ?? draftPayload, status: "process_second_pass_skipped", errorType: errorType, audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: nil, pasteEndedAt: nil, blankOrNoSpeech: blankOrNoSpeech, pasteSuccess: nil)
            hideBackgroundPolishHUDIfNeeded()
            return
        }
        let replacement = replaceDraftIfUnchanged(draft: draftPayload.text, final: finalPayload.text, targetPID: run.targetPID, targetBundleID: run.targetBundleID, cachedFocusedElement: run.focusedElement, runID: run.runID, action: "processSecondPass")
        let replaced = replacement.replaced
        appendHotkeyHistory(run: run, payload: finalPayload, status: replaced ? "process_second_pass_replaced" : "process_second_pass_saved", errorType: safeReplaceErrorType(replacement), audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: pasteEndedAt, pasteEndedAt: Date(), pasteSuccess: replaced)
        latestTranscript = finalPayload.text
        refreshHistoryWindowIfVisible()
        if replaced {
            hideHUD()
            return
        }
        showBackgroundCopyFallbackIfNeeded(finalPayload.text, draftWasPasted: draftWasPasted, replacementSucceeded: replaced)
    }

    private func finalize(run: ActiveRun) -> (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date) {
        let python = ProcessInfo.processInfo.environment["RAMBLEFIX_PYTHON"]
            ?? projectRoot.appendingPathComponent(".venv/bin/python").path
        let startedAt = Date()
        let finalize = runProcess(
            python,
            ["-m", "ramblefix.cli", "finalize-audio", run.audioURL.path, "--json"],
            currentDirectory: projectRoot,
            environmentOverrides: ["RAMBLEFIX_SROTA_SERVER_URL": "http://127.0.0.1:8188"]
        )
        let endedAt = Date()
        guard finalize.exitCode == 0 else {
            return (nil, finalize.stderr.isEmpty ? finalize.stdout : finalize.stderr, startedAt, endedAt)
        }
        guard let payload = parsePayload(finalize.stdout), !payload.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return (nil, "\(appName) finalizer produced no text.", startedAt, endedAt)
        }
        return (payload, nil, startedAt, endedAt)
    }

    private func finishFinalizer(run: ActiveRun, draftPayload: DictationPayload, result: (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date), audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool) {
        defer {
            isFinalizing = false
            statusItem?.button?.title = menuBarIdleTitle
            cleanupAudioIfNeeded(run.audioURL)
        }
        guard let finalPayload = result.payload, !isNoSpeechPayload(finalPayload), shouldUseFinalizer(draft: draftPayload.text, final: finalPayload.text) else {
            let blankOrNoSpeech = result.payload.map { isNoSpeechPayload($0) } ?? false
            appendHotkeyHistory(run: run, payload: result.payload ?? draftPayload, status: "finalizer_skipped", errorType: result.error == nil ? (blankOrNoSpeech ? "blank_or_no_speech" : "") : "finalizer_error", audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: nil, pasteEndedAt: nil, blankOrNoSpeech: blankOrNoSpeech, pasteSuccess: nil)
            hideBackgroundPolishHUDIfNeeded()
            return
        }
        let replacement = replaceDraftIfUnchanged(draft: draftPayload.text, final: finalPayload.text, targetPID: run.targetPID, targetBundleID: run.targetBundleID, cachedFocusedElement: run.focusedElement, runID: run.runID, action: "finalizer")
        let replaced = replacement.replaced
        appendHotkeyHistory(run: run, payload: finalPayload, status: replaced ? "finalizer_replaced" : "finalizer_saved", errorType: safeReplaceErrorType(replacement), audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: pasteEndedAt, pasteEndedAt: Date(), pasteSuccess: replaced)
        latestTranscript = finalPayload.text
        refreshHistoryWindowIfVisible()
        if replaced {
            hideHUD()
        }
        if !replaced {
            showBackgroundCopyFallbackIfNeeded(finalPayload.text, draftWasPasted: draftWasPasted, replacementSucceeded: replaced)
            if runtimeFlag("RAMBLEFIX_HOTKEY_NOTIFY_FINALIZER", defaultValue: false) {
                notify("\(appName) final text saved", "A better local finalizer result is in history.")
            }
        }
    }

    private func startBackgroundTermPolishIfNeeded(run: ActiveRun, draftPayload: DictationPayload, audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool = true) -> Bool {
        guard runtimeFlag("RAMBLEFIX_HOTKEY_TERM_POLISH", defaultValue: true) else { return false }
        guard shouldRunTermPolish(for: draftPayload) else { return false }
        isFinalizing = true
        statusItem?.button?.title = "RF polish"
        showBackgroundPolishHUD(action: .termPolish, run: run)
        DispatchQueue.global(qos: .utility).async {
            let result = self.termPolish(run: run, draftPayload: draftPayload)
            DispatchQueue.main.async {
                self.finishTermPolish(run: run, draftPayload: draftPayload, result: result, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: draftWasPasted)
            }
        }
        return true
    }

    private func termPolish(run: ActiveRun, draftPayload: DictationPayload) -> (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date) {
        let python = ProcessInfo.processInfo.environment["RAMBLEFIX_PYTHON"]
            ?? projectRoot.appendingPathComponent(".venv/bin/python").path
        let draftURL = termPolishDraftURL(for: run)
        do {
            try draftPayload.text.write(to: draftURL, atomically: true, encoding: .utf8)
        } catch {
            let now = Date()
            return (nil, "Could not save term polish draft: \(error)", now, now)
        }
        let startedAt = Date()
        let polish = runProcess(
            python,
            ["-m", "ramblefix.cli", "term-polish-audio", run.audioURL.path, "--draft-file", draftURL.path, "--json"],
            currentDirectory: projectRoot
        )
        let endedAt = Date()
        guard polish.exitCode == 0 else {
            return (nil, polish.stderr.isEmpty ? polish.stdout : polish.stderr, startedAt, endedAt)
        }
        guard let payload = parsePayload(polish.stdout), !payload.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return (nil, "\(appName) term polish produced no text.", startedAt, endedAt)
        }
        return (payload, nil, startedAt, endedAt)
    }

    private func finishTermPolish(run: ActiveRun, draftPayload: DictationPayload, result: (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date), audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool) {
        defer {
            isFinalizing = false
            statusItem?.button?.title = menuBarIdleTitle
            try? FileManager.default.removeItem(at: termPolishDraftURL(for: run))
            cleanupAudioIfNeeded(run.audioURL)
        }
        guard let finalPayload = result.payload,
              !isNoSpeechPayload(finalPayload),
              shouldUseTermPolish(draft: draftPayload.text, final: finalPayload.text) else {
            let blankOrNoSpeech = result.payload.map { isNoSpeechPayload($0) } ?? false
            appendHotkeyHistory(run: run, payload: result.payload ?? draftPayload, status: "term_polish_skipped", errorType: result.error == nil ? (blankOrNoSpeech ? "blank_or_no_speech" : "") : "term_polish_error", audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: nil, pasteEndedAt: nil, blankOrNoSpeech: blankOrNoSpeech, pasteSuccess: nil)
            hideBackgroundPolishHUDIfNeeded()
            return
        }
        let replacement = replaceDraftIfUnchanged(draft: draftPayload.text, final: finalPayload.text, targetPID: run.targetPID, targetBundleID: run.targetBundleID, cachedFocusedElement: run.focusedElement, runID: run.runID, action: "termPolish")
        let replaced = replacement.replaced
        appendHotkeyHistory(run: run, payload: finalPayload, status: replaced ? "term_polish_replaced" : "term_polish_saved", errorType: safeReplaceErrorType(replacement), audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: pasteEndedAt, pasteEndedAt: Date(), pasteSuccess: replaced)
        latestTranscript = finalPayload.text
        refreshHistoryWindowIfVisible()
        if replaced {
            hideHUD()
            return
        }
        showBackgroundCopyFallbackIfNeeded(finalPayload.text, draftWasPasted: draftWasPasted, replacementSucceeded: replaced)
    }

    private func termPolishDraftURL(for run: ActiveRun) -> URL {
        run.audioURL.deletingPathExtension().appendingPathExtension("draft.txt")
    }

    private func startBackgroundHindiPolishIfNeeded(run: ActiveRun, draftPayload: DictationPayload, audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool = true) -> Bool {
        guard runtimeFlag("RAMBLEFIX_HOTKEY_HINDI_POLISH", defaultValue: true) else { return false }
        let text = draftPayload.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard wordCount(text) >= 3 else { return false }
        let forcePolish = shouldForceHindiPolish(for: draftPayload)
        guard forcePolish || shouldProbeHindiAudioRisk(for: draftPayload, run: run) else { return false }
        isFinalizing = true
        if forcePolish {
            statusItem?.button?.title = "RF check"
            showBackgroundPolishHUD(action: .hindiPolish, run: run)
        } else {
            appendNativeEvent("background_polish_started", fields: [
                "run_id": run.runID,
                "action": BackgroundPolishAction.hindiPolish.rawValue,
                "visible": false,
                "reason": "speculative_audio_risk_probe"
            ])
        }
        DispatchQueue.global(qos: .utility).async {
            let result = self.hindiPolish(run: run, draftPayload: draftPayload, force: forcePolish)
            DispatchQueue.main.async {
                self.finishHindiPolish(run: run, draftPayload: draftPayload, result: result, audioSavedAt: audioSavedAt, pasteEndedAt: pasteEndedAt, draftWasPasted: draftWasPasted)
            }
        }
        return true
    }

    private func hindiPolish(run: ActiveRun, draftPayload: DictationPayload, force: Bool) -> (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date) {
        if run.streamChunkDirectory != nil,
           runtimeFlag("RAMBLEFIX_HOTKEY_HINDI_STREAM_POLISH", defaultValue: false) {
            let streamResult = hindiStreamPolish(run: run, draftPayload: draftPayload)
            if streamResult.payload != nil || !envFlag("RAMBLEFIX_HOTKEY_HINDI_STREAM_FALLBACK_FULL", defaultValue: true) {
                return streamResult
            }
        }
        let python = ProcessInfo.processInfo.environment["RAMBLEFIX_PYTHON"]
            ?? projectRoot.appendingPathComponent(".venv/bin/python").path
        let draftURL = hindiPolishDraftURL(for: run)
        do {
            try draftPayload.text.write(to: draftURL, atomically: true, encoding: .utf8)
        } catch {
            let now = Date()
            return (nil, "Could not save Hindi polish draft: \(error)", now, now)
        }
        let startedAt = Date()
        var arguments = ["-m", "ramblefix.cli", "hindi-polish-audio", run.audioURL.path, "--draft-file", draftURL.path, "--json"]
        if force && envFlag("RAMBLEFIX_HOTKEY_HINDI_POLISH_FORCE_ORISERVE", defaultValue: true) {
            arguments.append("--force")
        }
        let polish = runProcess(
            python,
            arguments,
            currentDirectory: projectRoot,
            environmentOverrides: hindiPolishEnvironmentOverrides()
        )
        let endedAt = Date()
        guard polish.exitCode == 0 else {
            return (nil, polish.stderr.isEmpty ? polish.stdout : polish.stderr, startedAt, endedAt)
        }
        guard let payload = parsePayload(polish.stdout), !payload.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return (nil, "\(appName) Hindi polish produced no text.", startedAt, endedAt)
        }
        return (payload, nil, startedAt, endedAt)
    }

    private func hindiStreamPolish(run: ActiveRun, draftPayload: DictationPayload) -> (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date) {
        let startedAt = Date()
        let timeout = max(0.5, Double(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_HINDI_STREAM_FINISH_TIMEOUT"] ?? "") ?? 5.5)
        let witnessTimeout = max(0.0, Double(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_HINDI_STREAM_WITNESS_TIMEOUT"] ?? "") ?? 0.0)
        let response = postSrotaJSON(
            path: "/hindi-stream/finish",
            payload: [
                "run_id": run.runID,
                "draft_text": draftPayload.text,
                "max_release_tail_seconds": timeout,
                "wait_timeout_seconds": timeout,
                "audio_path": run.audioURL.path,
                "witness_timeout_seconds": witnessTimeout
            ],
            timeout: timeout + witnessTimeout + 1.0
        )
        let endedAt = Date()
        guard let raw = response.payload else {
            return (nil, response.error ?? "\(appName) Hindi stream polish failed.", startedAt, endedAt)
        }
        guard let payload = payloadFromJSON(raw), !payload.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return (nil, "\(appName) Hindi stream polish produced no text.", startedAt, endedAt)
        }
        return (payload, nil, startedAt, endedAt)
    }

    private func finishHindiPolish(run: ActiveRun, draftPayload: DictationPayload, result: (payload: DictationPayload?, error: String?, startedAt: Date, endedAt: Date), audioSavedAt: Date, pasteEndedAt: Date, draftWasPasted: Bool) {
        defer {
            isFinalizing = false
            statusItem?.button?.title = menuBarIdleTitle
            try? FileManager.default.removeItem(at: hindiPolishDraftURL(for: run))
            cleanupAudioIfNeeded(run.audioURL)
        }
        let sawHindiRisk = result.payload.map { isHindiRiskPayload($0) } ?? false
        guard let finalPayload = result.payload,
              sawHindiRisk,
              !isNoSpeechPayload(finalPayload),
              shouldUseHindiPolish(draft: draftPayload.text, finalPayload: finalPayload) else {
            if sawHindiRisk || result.error != nil {
                let blankOrNoSpeech = result.payload.map { isNoSpeechPayload($0) } ?? false
                appendHotkeyHistory(run: run, payload: result.payload ?? draftPayload, status: "hindi_polish_skipped", errorType: result.error == nil ? (blankOrNoSpeech ? "blank_or_no_speech" : "") : "hindi_polish_error", audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: nil, pasteEndedAt: nil, blankOrNoSpeech: blankOrNoSpeech, pasteSuccess: nil)
                refreshHistoryWindowIfVisible()
            }
            hideBackgroundPolishHUDIfNeeded()
            return
        }
        let replacement = replaceDraftIfUnchanged(draft: draftPayload.text, final: finalPayload.text, targetPID: run.targetPID, targetBundleID: run.targetBundleID, cachedFocusedElement: run.focusedElement, runID: run.runID, action: "hindiPolish")
        let replaced = replacement.replaced
        appendHotkeyHistory(run: run, payload: finalPayload, status: replaced ? "hindi_polish_replaced" : "hindi_polish_saved", errorType: safeReplaceErrorType(replacement), audioSavedAt: audioSavedAt, asrStartedAt: result.startedAt, asrEndedAt: result.endedAt, pasteStartedAt: pasteEndedAt, pasteEndedAt: Date(), pasteSuccess: replaced)
        latestTranscript = finalPayload.text
        refreshHistoryWindowIfVisible()
        if replaced {
            hideHUD()
            return
        }
        showBackgroundCopyFallbackIfNeeded(finalPayload.text, draftWasPasted: draftWasPasted, replacementSucceeded: replaced)
    }

    private func hindiPolishDraftURL(for run: ActiveRun) -> URL {
        run.audioURL.deletingPathExtension().appendingPathExtension("hindi-draft.txt")
    }

    private func shouldRunFinalizer(for payload: DictationPayload) -> Bool {
        let text = payload.text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !envFlag("RAMBLEFIX_HOTKEY_FINALIZER_RISK_ONLY", defaultValue: true) {
            return wordCount(text) >= 3 || text.isEmpty
        }
        if text.isEmpty { return true }
        if payload.quality["degenerate"] as? Bool == true { return true }
        if !payload.fallbackReason.isEmpty { return true }
        if text.range(of: "unclear", options: .caseInsensitive) != nil { return true }
        if text.range(of: "foreign language", options: .caseInsensitive) != nil { return true }
        if wordCount(text) <= 4 { return true }
        if envFlag("RAMBLEFIX_HOTKEY_FINALIZER_ALWAYS", defaultValue: false) { return true }
        return likelyNeedsHinglishFinalizer(text)
    }

    private func shouldUseFinalizer(draft: String, final: String) -> Bool {
        let draftText = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let finalText = final.trimmingCharacters(in: .whitespacesAndNewlines)
        if finalText.isEmpty || finalText == draftText { return false }
        let draftWords = wordCount(draftText)
        let finalWords = wordCount(finalText)
        if draftWords >= 6, finalWords < max(3, draftWords / 3) { return false }
        if draftText.count >= 40, finalText.count < draftText.count / 3 { return false }
        if !droppedProtectedWorkTerms(from: draftText, to: finalText).isEmpty { return false }
        if !draftText.isEmpty,
           hasIndicOrArabicScript(finalText),
           !hasIndicOrArabicScript(draftText),
           !envFlag("RAMBLEFIX_HOTKEY_ALLOW_MIXED_SCRIPT_REPLACE", defaultValue: false) {
            return false
        }
        return true
    }

    private func shouldRunTermPolish(for payload: DictationPayload) -> Bool {
        let text = payload.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return false }
        if likelyNeedsHinglishFinalizer(text) { return false }
        return TermPolishPolicy.shouldRun(text: text) || shouldRunLearnedTermPolish(for: text)
    }

    private func shouldRunLearnedTermPolish(for text: String) -> Bool {
        let normalized = text
            .lowercased()
            .replacingOccurrences(of: "[^a-z0-9]+", with: " ", options: .regularExpression)
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else { return false }
        let tokens = Set(normalized.split(separator: " " ).map(String.init))
        let compact = normalized.replacingOccurrences(of: " ", with: "")
        for (alias, canonical) in protectedTermAliases() where alias != canonical {
            if protectedAliasAppears(alias, normalized: " \(normalized) ", tokens: tokens, compact: compact) {
                return true
            }
        }
        return false
    }

    private func shouldUseTermPolish(draft: String, final: String) -> Bool {
        if !TermPolishPolicy.shouldUse(draft: draft, final: final) { return false }
        return droppedProtectedWorkTerms(from: draft, to: final).isEmpty
    }

    private func shouldUseFallbackRescue(draft: String, final: String) -> Bool {
        let draftText = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let finalText = final.trimmingCharacters(in: .whitespacesAndNewlines)
        if finalText.isEmpty || finalText == draftText { return false }
        if draftText.isEmpty { return true }
        let draftWords = wordCount(draftText)
        let finalWords = wordCount(finalText)
        if draftWords >= 6, finalWords < max(3, draftWords / 2) { return false }
        if finalText.count < max(20, draftText.count / 2) { return false }
        return droppedProtectedWorkTerms(from: draftText, to: finalText).isEmpty
    }

    private func shouldRunProcessSecondPass(for payload: DictationPayload) -> Bool {
        let text = payload.text.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return false }
        if isNoSpeechPayload(payload) { return false }
        if wordCount(text) < 3 { return false }
        if payload.route == "process_second_pass" { return false }
        return true
    }

    private func shouldRunStructure(for payload: DictationPayload) -> Bool {
        let text = payload.text.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return false }
        if isNoSpeechPayload(payload) { return false }
        if wordCount(text) < 3 { return false }
        if payload.processor == "structure" || payload.route == "structure" { return false }
        if payload.processor == "light-polish" || payload.route == "light_polish" { return false }
        if payload.processor == "friendly-rewrite" || payload.route == "friendly_rewrite" { return false }
        if payload.quality["hindi_risk"] as? Bool == true { return false }
        if hasIndicOrArabicScript(text) || likelyNeedsHinglishFinalizer(text) { return false }
        return FriendlyRewritePolicy.rewrite(text: text).changed
    }

    private func shouldUseStructure(draft: String, final: String) -> Bool {
        guard FriendlyRewritePolicy.shouldUse(draft: draft, final: final) else { return false }
        guard droppedProtectedWorkTerms(from: draft, to: final).isEmpty else { return false }
        if hasIndicOrArabicScript(draft) != hasIndicOrArabicScript(final) { return false }
        return true
    }

    private func shouldUseProcessSecondPass(draft: String, final: String) -> Bool {
        ProcessSecondPassPolicy.shouldUse(draft: draft, final: final, requireHindiSignal: true)
            && droppedProtectedWorkTerms(from: draft, to: final).isEmpty
    }

    private func shouldUseProcessSecondPassCandidate(draftPayload: DictationPayload, finalPayload: DictationPayload, riskPayload: [String: Any]?, run: ActiveRun) -> Bool {
        guard shouldUseProcessSecondPass(draft: draftPayload.text, final: finalPayload.text) else { return false }
        if riskLanguage(riskPayload) == "hi" || riskLanguage(riskPayload) == "ur" {
            return true
        }
        if riskReasons(riskPayload).contains(where: { $0 == "language:hi" || $0 == "language:ur" }) {
            return true
        }
        let shortDraftLimit = Int(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_PROCESS_SECOND_PASS_SHORT_DRAFT_WORDS"] ?? "") ?? 5
        let maxShortAudioSeconds = Double(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_PROCESS_SECOND_PASS_SHORT_AUDIO_SECONDS"] ?? "") ?? 6.0
        guard wordCount(draftPayload.text) <= shortDraftLimit else { return false }
        guard let duration = audioDurationSeconds(run.audioURL), duration <= maxShortAudioSeconds else { return false }
        return true
    }

    private func shortHinglishRescueIfUseful(run: ActiveRun, draftPayload: DictationPayload, processPayload: DictationPayload, riskPayload: [String: Any]?) -> DictationPayload? {
        guard envFlag("RAMBLEFIX_HOTKEY_SHORT_HINGLISH_RESCUE", defaultValue: true) else { return nil }
        let shortDraftLimit = Int(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_SHORT_HINGLISH_RESCUE_DRAFT_WORDS"] ?? "") ?? 5
        let maxAudioSeconds = Double(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_SHORT_HINGLISH_RESCUE_AUDIO_SECONDS"] ?? "") ?? 6.0
        guard wordCount(draftPayload.text) <= shortDraftLimit else { return nil }
        guard let duration = audioDurationSeconds(run.audioURL), duration <= maxAudioSeconds else { return nil }
        guard riskPayload?["risk"] as? Bool == true else { return nil }
        guard !hasIndicOrArabicScript(processPayload.text) else { return nil }
        let timeout = max(1.0, Double(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_SHORT_HINGLISH_RESCUE_TIMEOUT"] ?? "") ?? 4.2)
        let response = postSrotaJSON(
            path: "/hindi-polish",
            payload: [
                "audio_path": run.audioURL.path,
                "draft_text": draftPayload.text,
                "low_confidence_threshold": Double(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_PROCESS_SECOND_PASS_RISK_THRESHOLD"] ?? "") ?? 0.60,
                "force": true
            ],
            timeout: timeout
        )
        guard response.error == nil,
              let rawPayload = response.payload,
              let rescuePayload = payloadFromJSON(rawPayload),
              shouldUseShortHinglishRescue(draft: draftPayload.text, current: processPayload.text, rescue: rescuePayload) else {
            return nil
        }
        return rescuePayload
    }

    private func shouldUseShortHinglishRescue(draft: String, current: String, rescue: DictationPayload) -> Bool {
        let rescueText = rescue.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !rescueText.isEmpty, rescueText != current.trimmingCharacters(in: .whitespacesAndNewlines) else { return false }
        guard hasIndicOrArabicScript(rescueText) else { return false }
        let maxSeconds = Double(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_SHORT_HINGLISH_RESCUE_MAX_SECONDS"] ?? "") ?? 4.0
        if rescue.seconds > maxSeconds { return false }
        guard contentRetentionRatio(from: draft, to: rescueText) >= 0.55 else { return false }
        return shouldUseFallbackRescue(draft: draft, final: rescueText)
    }

    private func shouldUseHindiPolish(draft: String, finalPayload: DictationPayload) -> Bool {
        let final = finalPayload.text
        let policyOK: Bool
        if finalPayload.processor == "hindi-stream", finalPayload.safeUpdate {
            policyOK = HindiPolishPolicy.shouldUseServerSafeUpdate(draft: draft, final: final)
        } else if finalPayload.processor == "hindi-polish" || finalPayload.route == "hindi_polish_changed" {
            policyOK = HindiPolishPolicy.shouldUseAudioRiskUpdate(draft: draft, final: final)
        } else {
            policyOK = HindiPolishPolicy.shouldUse(draft: draft, final: final)
        }
        if !policyOK { return false }
        return droppedProtectedWorkTerms(from: draft, to: final).isEmpty
    }

    private func isHindiRiskPayload(_ payload: DictationPayload) -> Bool {
        if payload.quality["hindi_risk"] as? Bool == true { return true }
        if payload.route == "hindi_polish_changed" { return true }
        return false
    }

    private func likelyNeedsHinglishFinalizer(_ text: String) -> Bool {
        let tokens = Set(
            text
                .lowercased()
                .split { !$0.isLetter && !$0.isNumber }
                .map(String.init)
        )
        let markers = ["bhai", "yaar", "kya", "nahi", "nahin", "samajh", "matlab", "maz", "fatafat", "haan", "hai", "hain", "toh"]
        if markers.contains(where: { tokens.contains($0) }) { return true }
        return false
    }

    private func shouldForceHindiPolish(for payload: DictationPayload) -> Bool {
        let text = payload.text.trimmingCharacters(in: .whitespacesAndNewlines)
        return HindiPolishPolicy.shouldCheckDraft(
            draft: text,
            qualityHindiRisk: payload.quality["hindi_risk"] as? Bool == true,
            audioRiskAll: envFlag("RAMBLEFIX_HOTKEY_HINDI_POLISH_AUDIO_RISK_ALL", defaultValue: false)
        )
    }

    private func shouldProbeHindiAudioRisk(for payload: DictationPayload, run: ActiveRun) -> Bool {
        let maxAudioSeconds = Double(ProcessInfo.processInfo.environment["RAMBLEFIX_HOTKEY_HINDI_POLISH_AUDIO_RISK_MAX_SECONDS"] ?? "") ?? 90.0
        return HindiPolishPolicy.shouldProbeAudioRisk(
            draft: payload.text,
            route: payload.route,
            audioSeconds: audioDurationSeconds(run.audioURL),
            audioRiskDetector: envFlag("RAMBLEFIX_HOTKEY_HINDI_POLISH_AUDIO_RISK_DETECTOR", defaultValue: true),
            maxAudioSeconds: maxAudioSeconds
        )
    }

    private func hindiPolishEnvironmentOverrides() -> [String: String] {
        guard envFlag("RAMBLEFIX_HINDI_POLISH_USE_SERVER", defaultValue: true) else { return [:] }
        return [
            "RAMBLEFIX_HINDI_POLISH_SERVER_URL": "http://127.0.0.1:8188",
            "RAMBLEFIX_SROTA_SERVER_URL": "http://127.0.0.1:8188",
            "RAMBLEFIX_HINGLISH_FINALIZER_BACKEND": "oriserve",
            "RAMBLEFIX_ORISERVE_BACKEND": "ggml"
        ]
    }

    private func wordCount(_ text: String) -> Int {
        text.split { !$0.isLetter && !$0.isNumber }.count
    }

    private func audioDurationSeconds(_ url: URL) -> Double? {
        guard let file = try? AVAudioFile(forReading: url) else { return nil }
        let sampleRate = file.fileFormat.sampleRate
        guard sampleRate > 0 else { return nil }
        return Double(file.length) / sampleRate
    }

    private func hasIndicOrArabicScript(_ text: String) -> Bool {
        text.unicodeScalars.contains { scalar in
            (0x0900...0x097F).contains(Int(scalar.value)) || (0x0600...0x06FF).contains(Int(scalar.value))
        }
    }

    private func riskLanguage(_ payload: [String: Any]?) -> String {
        (payload?["language"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private func riskReasons(_ payload: [String: Any]?) -> [String] {
        if let reasons = payload?["risk_reasons"] as? [String] {
            return reasons
        }
        if let reasons = payload?["risk_reasons"] as? [Any] {
            return reasons.compactMap { $0 as? String }
        }
        return []
    }

    private func contentRetentionRatio(from draft: String, to final: String) -> Double {
        let draftTokens = Set(contentTokens(draft))
        if draftTokens.isEmpty { return 1.0 }
        let finalTokens = Set(contentTokens(final))
        let retained = draftTokens.intersection(finalTokens).count
        return Double(retained) / Double(max(1, draftTokens.count))
    }

    private func contentTokens(_ text: String) -> [String] {
        text
            .lowercased()
            .split { !$0.isLetter && !$0.isNumber }
            .map(String.init)
            .filter { $0.count >= 3 }
    }

    private func droppedProtectedWorkTerms(from draft: String, to final: String) -> Set<String> {
        let draftTerms = protectedWorkTerms(in: draft)
        if draftTerms.isEmpty { return [] }
        return draftTerms.subtracting(protectedWorkTerms(in: final))
    }

    private func protectedWorkTerms(in text: String) -> Set<String> {
        let normalized = text
            .lowercased()
            .replacingOccurrences(of: "[^a-z0-9]+", with: " ", options: .regularExpression)
        let tokens = Set(normalized.split(separator: " ").map(String.init))
        let compact = normalized.replacingOccurrences(of: " ", with: "")
        var terms = Set(protectedTermAliases().compactMap { alias, canonical in
            protectedAliasAppears(alias, normalized: " \(normalized) ", tokens: tokens, compact: compact) ? canonical : nil
        })
        terms.formUnion(patternProtectedTerms(in: text))
        return terms
    }

    private func protectedAliasAppears(_ alias: String, normalized: String, tokens: Set<String>, compact: String) -> Bool {
        guard !alias.isEmpty else { return false }
        if alias.contains(" ") {
            return normalized.contains(" \(alias) ")
        }
        return tokens.contains(alias) || (alias.count >= 5 && compact.contains(alias))
    }

    private func protectedTermAliases() -> [String: String] {
        let paths = [
            projectRoot.appendingPathComponent("config/dictionary.json"),
            projectRoot.appendingPathComponent("config/memory_terms.json")
        ]
        let signature = protectedTermsSignature(paths: paths)
        if let cached = protectedTermAliasCache, signature == protectedTermAliasCacheSignature {
            return cached
        }

        var aliases: [String: String] = [:]
        for path in paths {
            loadProtectedTerms(from: path, into: &aliases)
        }
        protectedTermAliasCache = aliases
        protectedTermAliasCacheSignature = signature
        return aliases
    }

    private func patternProtectedTerms(in text: String) -> Set<String> {
        var terms: Set<String> = []
        let patterns = [
            #"\b[A-Z][A-Z0-9]{1,9}\b"#,
            #"\b[A-Za-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b"#,
            #"\b[A-Za-z]+[0-9][A-Za-z0-9]*\b"#,
            #"(?:\b[A-Za-z]\.){2,}[A-Za-z]?\.?"#
        ]
        for pattern in patterns {
            guard let regex = try? NSRegularExpression(pattern: pattern) else { continue }
            let nsText = text as NSString
            for match in regex.matches(in: text, range: NSRange(location: 0, length: nsText.length)) {
                let raw = nsText.substring(with: match.range)
                let normalized = normalizeProtectedTerm(raw)
                if shouldProtectPatternTerm(raw: raw, normalized: normalized) {
                    terms.insert(canonicalPatternProtectedTerm(raw: raw, normalized: normalized))
                }
            }
        }
        return terms
    }

    private func canonicalPatternProtectedTerm(raw: String, normalized: String) -> String {
        if raw.range(of: #"^[A-Z0-9]{2,}s$"#, options: .regularExpression) != nil,
           normalized.count > 2 {
            return String(normalized.dropLast())
        }
        return normalized
    }

    private func shouldProtectPatternTerm(raw: String, normalized: String) -> Bool {
        guard normalized.count >= 2 else { return false }
        let rejected = ["am", "pm", "ok", "yes", "no"]
        if rejected.contains(normalized) { return false }
        if raw.contains(".") { return normalized.count >= 2 }
        let hasDigit = raw.rangeOfCharacter(from: .decimalDigits) != nil
        let letters = raw.filter { $0.isLetter }
        let uppercaseLetters = letters.filter { $0.isUppercase }
        let lowercaseLetters = letters.filter { $0.isLowercase }
        if hasDigit, !letters.isEmpty { return true }
        if letters.count >= 2, uppercaseLetters.count == letters.count { return true }
        return uppercaseLetters.count >= 2 && !lowercaseLetters.isEmpty
    }

    private func protectedTermsSignature(paths: [URL]) -> String {
        paths.map { path in
            let attributes = try? FileManager.default.attributesOfItem(atPath: path.path)
            let modified = (attributes?[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0
            let size = (attributes?[.size] as? NSNumber)?.uint64Value ?? 0
            return "\(path.lastPathComponent):\(modified):\(size)"
        }.joined(separator: "|")
    }

    private func loadProtectedTerms(from path: URL, into aliases: inout [String: String]) {
        guard let data = try? Data(contentsOf: path),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let items = payload["terms"] as? [[String: Any]] else {
            return
        }
        for item in items {
            guard item["enabled"] as? Bool != false else { continue }
            if let status = item["status"] as? String,
               !["auto", "approved"].contains(status.lowercased()) {
                continue
            }
            guard let canonicalRaw = item["canonical"] as? String else { continue }
            let canonical = normalizeProtectedTerm(canonicalRaw)
            guard !canonical.isEmpty else { continue }
            aliases[canonical] = canonical
            for aliasRaw in item["aliases"] as? [String] ?? [] {
                let alias = normalizeProtectedTerm(aliasRaw)
                if !alias.isEmpty {
                    aliases[alias] = canonical
                }
            }
        }
    }

    private func normalizeProtectedTerm(_ value: String) -> String {
        value
            .lowercased()
            .replacingOccurrences(of: "[^a-z0-9]+", with: " ", options: .regularExpression)
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func replaceDraftIfUnchanged(draft: String, final: String, targetPID: pid_t?, targetBundleID: String, cachedFocusedElement: AXUIElement?, runID: String, action: String) -> SafeReplaceAttempt {
        func finish(_ result: SafeReplaceAttempt) -> SafeReplaceAttempt {
            var fields: [String: Any] = [
                "run_id": runID,
                "action": action,
                "replaced": result.replaced,
                "reason": result.reason,
                "draft_chars": (draft as NSString).length,
                "final_chars": (final as NSString).length,
                "focused_role": result.focusedRole,
                "focused_source": result.focusedSource
            ]
            if let valueLength = result.valueLength { fields["value_chars"] = valueLength }
            if let selectedLocation = result.selectedLocation { fields["selected_location"] = selectedLocation }
            if let selectedLength = result.selectedLength { fields["selected_length"] = selectedLength }
            if let replacementLocation = result.replacementLocation { fields["replacement_location"] = replacementLocation }
            if let replacementLength = result.replacementLength { fields["replacement_length"] = replacementLength }
            appendNativeEvent("safe_replace_attempt", fields: fields)
            return result
        }

        func attempt(_ replaced: Bool, _ reason: String, focusedRole: String = "", focusedSource: String = "", valueLength: Int? = nil, selectedLocation: Int? = nil, selectedLength: Int? = nil, replacementLocation: Int? = nil, replacementLength: Int? = nil) -> SafeReplaceAttempt {
            SafeReplaceAttempt(
                replaced: replaced,
                reason: reason,
                focusedRole: focusedRole,
                focusedSource: focusedSource,
                valueLength: valueLength,
                selectedLocation: selectedLocation,
                selectedLength: selectedLength,
                replacementLocation: replacementLocation,
                replacementLength: replacementLength
            )
        }

        guard preflightAccessibility(prompt: true) else {
            return finish(attempt(false, "accessibility_unavailable"))
        }
        guard let targetPID,
              let target = NSRunningApplication(processIdentifier: targetPID) else {
            return finish(attempt(false, "target_missing"))
        }
        if !targetBundleID.isEmpty, target.bundleIdentifier != targetBundleID {
            return finish(attempt(false, "target_bundle_mismatch"))
        }
        target.activate(options: [.activateIgnoringOtherApps])
        Thread.sleep(forTimeInterval: 0.10)
        guard let frontmost = NSWorkspace.shared.frontmostApplication,
              frontmost.processIdentifier == targetPID else {
            return finish(attempt(false, "frontmost_mismatch"))
        }
        if !targetBundleID.isEmpty, frontmost.bundleIdentifier != targetBundleID {
            return finish(attempt(false, "frontmost_bundle_mismatch"))
        }
        let focusedLookup = focusedTextElementWithSource(cachedFallback: cachedFocusedElement)
        guard let focused = focusedLookup.element else {
            return finish(attempt(false, "focused_element_missing"))
        }
        let focusedRole = axStringAttribute(focused, kAXRoleAttribute as CFString) ?? ""
        guard let value = axStringValue(focused) else {
            return finish(attempt(false, "value_unavailable", focusedRole: focusedRole, focusedSource: focusedLookup.source))
        }
        guard let selectedRange = axSelectedRange(focused) else {
            return finish(attempt(false, "selected_range_unavailable", focusedRole: focusedRole, focusedSource: focusedLookup.source, valueLength: (value as NSString).length))
        }
        let decision = SafeDraftReplacementPolicy.replacementDecision(
            value: value,
            selectedLocation: selectedRange.location,
            selectedLength: selectedRange.length,
            draft: draft
        )
        guard let range = decision.range else {
            return finish(attempt(
                false,
                decision.reason,
                focusedRole: focusedRole,
                focusedSource: focusedLookup.source,
                valueLength: (value as NSString).length,
                selectedLocation: selectedRange.location,
                selectedLength: selectedRange.length
            ))
        }

        var replacementRange = CFRange(location: range.location, length: range.length)
        guard let rangeValue = AXValueCreate(.cfRange, &replacementRange) else {
            return finish(attempt(
                false,
                "range_value_create_failed",
                focusedRole: focusedRole,
                focusedSource: focusedLookup.source,
                valueLength: (value as NSString).length,
                selectedLocation: selectedRange.location,
                selectedLength: selectedRange.length,
                replacementLocation: range.location,
                replacementLength: range.length
            ))
        }
        let setResult = AXUIElementSetAttributeValue(focused, kAXSelectedTextRangeAttribute as CFString, rangeValue)
        guard setResult == .success else {
            return finish(attempt(
                false,
                "set_selected_range_failed_\(setResult.rawValue)",
                focusedRole: focusedRole,
                focusedSource: focusedLookup.source,
                valueLength: (value as NSString).length,
                selectedLocation: selectedRange.location,
                selectedLength: selectedRange.length,
                replacementLocation: range.location,
                replacementLength: range.length
            ))
        }
        Thread.sleep(forTimeInterval: 0.05)
        let pasted = paste(final, targetPID: targetPID, targetBundleID: targetBundleID)
        return finish(attempt(
            pasted,
            pasted ? decision.reason : "paste_failed",
            focusedRole: focusedRole,
            focusedSource: focusedLookup.source,
            valueLength: (value as NSString).length,
            selectedLocation: selectedRange.location,
            selectedLength: selectedRange.length,
            replacementLocation: range.location,
            replacementLength: range.length
        ))
    }

    private func safeReplaceErrorType(_ result: SafeReplaceAttempt) -> String {
        result.replaced ? "" : "safe_replace_\(result.reason)"
    }

    private func focusedTextElement() -> AXUIElement? {
        let system = AXUIElementCreateSystemWide()
        var focused: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute as CFString, &focused)
        guard result == .success, let focused else { return nil }
        return (focused as! AXUIElement)
    }

    private func focusedTextElementWithSource(cachedFallback: AXUIElement?) -> (element: AXUIElement?, source: String) {
        for attempt in 0..<3 {
            if let focused = focusedTextElement() {
                return (focused, attempt == 0 ? "system_focused" : "system_focused_retry_\(attempt)")
            }
            if attempt < 2 {
                Thread.sleep(forTimeInterval: 0.05)
            }
        }
        guard let cachedFallback,
              axStringValue(cachedFallback) != nil,
              axSelectedRange(cachedFallback) != nil else {
            return (nil, "")
        }
        return (cachedFallback, "cached_start_focus")
    }

    private func focusedPasteTargetConfidence() -> PasteTargetConfidence {
        guard let focused = focusedTextElement() else { return .ambiguous }
        return PasteFocusHeuristics.confidence(
            role: axStringAttribute(focused, kAXRoleAttribute as CFString),
            selectedRangeAvailable: axSelectedRange(focused) != nil,
            valueIsSettable: axAttributeIsSettable(focused, kAXValueAttribute as CFString)
        )
    }

    private func verifyPasteLanded(_ text: String, targetPID: pid_t, targetBundleID: String) -> PasteVerificationStatus {
        guard let frontmost = NSWorkspace.shared.frontmostApplication,
              frontmost.processIdentifier == targetPID else {
            return .failed
        }
        if !targetBundleID.isEmpty, frontmost.bundleIdentifier != targetBundleID {
            return .failed
        }
        guard let focused = focusedTextElement(),
              let value = axStringValue(focused) else {
            return .unverified
        }
        if value.contains(text) {
            return .verified
        }
        if normalizedPasteVerificationText(value).contains(normalizedPasteVerificationText(text)) {
            return .verified
        }
        if let selectedRange = axSelectedRange(focused) {
            let valueNSString = value as NSString
            let textNSString = text as NSString
            let textLength = textNSString.length
            if selectedRange.location >= textLength {
                let start = selectedRange.location - textLength
                if start >= 0, start + textLength <= valueNSString.length {
                    let suffix = valueNSString.substring(with: NSRange(location: start, length: textLength))
                    if suffix == text {
                        return .verified
                    }
                }
            }
        }
        return .failed
    }

    private func normalizedPasteVerificationText(_ text: String) -> String {
        text
            .lowercased()
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func pasteTargetConfidenceName(_ confidence: PasteTargetConfidence) -> String {
        switch confidence {
        case .editable: return "editable"
        case .ambiguous: return "ambiguous"
        case .blocked: return "blocked"
        }
    }

    private func pasteVerificationName(_ verification: PasteVerificationStatus) -> String {
        switch verification {
        case .verified: return "verified"
        case .failed: return "failed"
        case .unverified: return "unverified"
        }
    }

    private func axAttributeIsSettable(_ element: AXUIElement, _ attribute: CFString) -> Bool {
        var settable = DarwinBoolean(false)
        let result = AXUIElementIsAttributeSettable(element, attribute, &settable)
        return result == .success && settable.boolValue
    }

    private func axStringAttribute(_ element: AXUIElement, _ attribute: CFString) -> String? {
        var value: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(element, attribute, &value)
        guard result == .success else { return nil }
        return value as? String
    }

    private func axStringValue(_ element: AXUIElement) -> String? {
        var value: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(element, kAXValueAttribute as CFString, &value)
        guard result == .success else { return nil }
        return value as? String
    }

    private func axSelectedRange(_ element: AXUIElement) -> CFRange? {
        var value: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(element, kAXSelectedTextRangeAttribute as CFString, &value)
        guard result == .success, let axValue = value else { return nil }
        var range = CFRange()
        guard AXValueGetValue((axValue as! AXValue), .cfRange, &range) else { return nil }
        return range
    }

    private func showHUD(title: String, subtitle: String, state: String, accent: NSColor, level: CGFloat? = nil, autoHide: TimeInterval? = nil, copyText: String? = nil) {
        let visualOnly = HUDSignalStylePolicy.isVisualOnlyState(state)
        let hasAction = copyText != nil
        let width: CGFloat = visualOnly
            ? CGFloat(HUDSignalStylePolicy.visualOnlyPillWidth)
            : CGFloat(hasAction ? HUDSignalStylePolicy.copyPillWidth : HUDSignalStylePolicy.statusPillWidth)
        let height: CGFloat = visualOnly
            ? CGFloat(HUDSignalStylePolicy.visualOnlyPillHeight)
            : CGFloat(HUDSignalStylePolicy.textPillHeight)
        let displayTitle = visualOnly || subtitle.isEmpty ? title : "\(title) · \(subtitle)"
        if hudWindow == nil {
            let panel = NSPanel(
                contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                styleMask: [.borderless, .nonactivatingPanel],
                backing: .buffered,
                defer: false
            )
            panel.isFloatingPanel = true
            panel.level = .statusBar
            panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]
            panel.isOpaque = false
            panel.backgroundColor = .clear
            panel.hasShadow = false
            panel.ignoresMouseEvents = true

            let root = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height))
            root.wantsLayer = true
            root.layer?.backgroundColor = NSColor.clear.cgColor

            let backgroundView = NSVisualEffectView(frame: root.bounds)
            backgroundView.autoresizingMask = [.width, .height]
            backgroundView.material = .hudWindow
            backgroundView.blendingMode = .behindWindow
            backgroundView.state = .active
            backgroundView.wantsLayer = true
            backgroundView.layer?.cornerRadius = height / 2
            backgroundView.layer?.masksToBounds = true
            backgroundView.layer?.backgroundColor = RFTheme.glassFill.cgColor
            backgroundView.layer?.borderWidth = 0
            backgroundView.layer?.borderColor = NSColor.clear.cgColor

            let glassOverlay = RFLiquidGlassOverlayView(frame: root.bounds)
            glassOverlay.autoresizingMask = [.width, .height]
            glassOverlay.wantsLayer = true
            glassOverlay.layer?.masksToBounds = false

            let refractedGlassView = RFRefractedBackdropGlassView(frame: root.bounds)
            refractedGlassView.autoresizingMask = [.width, .height]
            refractedGlassView.isHidden = true

            let nativeGlassView: NSView? = nil

            let signalView = RFHUDSignalView(frame: NSRect(x: 0, y: 2, width: width, height: height - 4))
            signalView.state = state
            signalView.accent = accent
            signalView.level = level ?? 0.35

            let stateLabel = NSTextField(labelWithString: state)
            stateLabel.isHidden = true

            let titleLabel = NSTextField(labelWithString: title)
            titleLabel.frame = NSRect(
                x: CGFloat(HUDSignalStylePolicy.toastHorizontalPadding),
                y: 8,
                width: width - CGFloat(HUDSignalStylePolicy.toastHorizontalPadding * 2),
                height: 14
            )
            titleLabel.font = .systemFont(ofSize: CGFloat(HUDSignalStylePolicy.toastTextFontSize), weight: .regular)
            titleLabel.textColor = RFTheme.toastText
            titleLabel.lineBreakMode = .byTruncatingTail
            titleLabel.cell?.usesSingleLineMode = true
            titleLabel.cell?.truncatesLastVisibleLine = true

            let subtitleLabel = NSTextField(labelWithString: subtitle)
            subtitleLabel.frame = .zero
            subtitleLabel.font = .systemFont(ofSize: CGFloat(HUDSignalStylePolicy.toastTextFontSize), weight: .regular)
            subtitleLabel.textColor = RFTheme.toastAction
            subtitleLabel.lineBreakMode = .byTruncatingTail

            let actionButton = NSButton(title: "Copy", target: self, action: #selector(copyHUDTranscript))
            actionButton.bezelStyle = .shadowlessSquare
            actionButton.font = .systemFont(ofSize: CGFloat(HUDSignalStylePolicy.toastActionFontSize), weight: .semibold)
            actionButton.contentTintColor = RFTheme.toastAction
            actionButton.isBordered = false
            actionButton.focusRingType = .none
            actionButton.wantsLayer = true
            actionButton.layer?.backgroundColor = NSColor.clear.cgColor
            actionButton.frame = NSRect(
                x: width - CGFloat(HUDSignalStylePolicy.toastHorizontalPadding + HUDSignalStylePolicy.toastActionWidth),
                y: 5,
                width: CGFloat(HUDSignalStylePolicy.toastActionWidth),
                height: 20
            )

            root.addSubview(backgroundView)
            root.addSubview(glassOverlay)
            if let nativeGlassView {
                root.addSubview(nativeGlassView)
            }
            root.addSubview(refractedGlassView)
            root.addSubview(signalView)
            root.addSubview(stateLabel)
            root.addSubview(titleLabel)
            root.addSubview(subtitleLabel)
            root.addSubview(actionButton)
            panel.contentView = root
            hudWindow = panel
            hudBackgroundView = backgroundView
            hudNativeGlassView = nativeGlassView
            hudGlassOverlayView = glassOverlay
            hudRefractedGlassView = refractedGlassView
            hudSignalView = signalView
            hudStateLabel = stateLabel
            hudTitleLabel = titleLabel
            hudSubtitleLabel = subtitleLabel
            hudActionButton = actionButton
        }

        if let panel = hudWindow, panel.frame.size != NSSize(width: width, height: height) {
            panel.setFrame(NSRect(origin: panel.frame.origin, size: NSSize(width: width, height: height)), display: true)
            panel.contentView?.frame = NSRect(x: 0, y: 0, width: width, height: height)
            hudBackgroundView?.frame = NSRect(x: 0, y: 0, width: width, height: height)
            hudBackgroundView?.layer?.cornerRadius = height / 2
            hudNativeGlassView?.frame = NSRect(x: 0, y: 0, width: width, height: height)
            if #available(macOS 26.0, *), let nativeGlassView = hudNativeGlassView as? NSGlassEffectView {
                nativeGlassView.cornerRadius = height / 2
            }
            hudGlassOverlayView?.frame = NSRect(x: 0, y: 0, width: width, height: height)
            hudRefractedGlassView?.frame = NSRect(x: 0, y: 0, width: width, height: height)
            hudBackdropCaptureKey = nil
        }
        hudCopyText = copyText
        setHUDActionButtonTitle("Copy")
        hudWindow?.ignoresMouseEvents = copyText == nil
        hudWindow?.hasShadow = !visualOnly
        hudWindow?.contentView?.layer?.backgroundColor = NSColor.clear.cgColor
        configureHUDGlassFallback(visualOnly: visualOnly, height: height, accent: accent)
        hudRefractedGlassView?.accent = accent
        hudRefractedGlassView?.isCompact = visualOnly

        hudSignalView?.isHidden = !visualOnly
        hudSignalView?.frame = visualOnly
            ? NSRect(x: 0, y: 3, width: width, height: height - 6)
            : NSRect(x: 18, y: 8, width: width - 36, height: 28)
        hudSignalView?.state = state
        hudSignalView?.accent = accent
        hudSignalView?.level = level ?? hudSignalView?.level ?? 0.35
        hudSignalView?.colorShift = hudColorShift
        hudSignalView?.variant = hudMotionVariant
        hudSignalView?.recipeSeed = hudRecipeSeed
        hudSignalView?.phase += state == "REC" ? 0.18 : 0

        hudStateLabel?.isHidden = true
        hudStateLabel?.stringValue = state
        hudTitleLabel?.isHidden = visualOnly
        let toastPadding = CGFloat(HUDSignalStylePolicy.toastHorizontalPadding)
        let toastActionWidth = CGFloat(HUDSignalStylePolicy.toastActionWidth)
        let toastActionGap = CGFloat(HUDSignalStylePolicy.toastActionGap)
        let titleRightReserve = hasAction ? toastActionWidth + toastActionGap + toastPadding : toastPadding
        hudTitleLabel?.frame = NSRect(x: toastPadding, y: 8, width: width - toastPadding - titleRightReserve, height: 14)
        hudTitleLabel?.font = .systemFont(ofSize: CGFloat(HUDSignalStylePolicy.toastTextFontSize), weight: .regular)
        hudTitleLabel?.textColor = RFTheme.toastText
        hudSubtitleLabel?.isHidden = true
        hudSubtitleLabel?.frame = .zero
        hudTitleLabel?.stringValue = displayTitle
        hudSubtitleLabel?.stringValue = subtitle
        hudActionButton?.isHidden = !hasAction || visualOnly
        hudActionButton?.frame = NSRect(x: width - toastPadding - toastActionWidth, y: 5, width: toastActionWidth, height: 20)
        hudActionButton?.bezelStyle = .shadowlessSquare
        hudActionButton?.isBordered = false
        hudActionButton?.focusRingType = .none
        hudActionButton?.contentTintColor = RFTheme.toastAction
        if hasAction {
            setHUDActionButtonTitle("Copy")
        }

        if state == "WORK" {
            startHUDMotionTimer()
        } else {
            stopHUDMotionTimer()
        }

        positionHUD()
        refreshHUDBackdropIfNeeded(state: state, width: width, height: height, visualOnly: visualOnly, accent: accent)
        hudWindow?.orderFrontRegardless()
        if let autoHide {
            DispatchQueue.main.asyncAfter(deadline: .now() + autoHide) { [weak self] in
                guard self?.hudTitleLabel?.stringValue == displayTitle else { return }
                self?.hudWindow?.orderOut(nil)
                self?.hudBackdropCaptureKey = nil
                self?.hudRefractedGlassView?.clearBackdrop()
            }
        }
    }

    private func configureHUDGlassFallback(visualOnly: Bool, height: CGFloat, accent: NSColor) {
        if visualOnly {
            clearHUDVisualBackground()
            return
        }
        hudRefractedGlassView?.isHidden = true
        hudNativeGlassView?.isHidden = true
        hudBackgroundView?.isHidden = false
        hudBackgroundView?.material = visualOnly ? .hudWindow : .popover
        hudBackgroundView?.blendingMode = .behindWindow
        hudBackgroundView?.state = .active
        hudBackgroundView?.layer?.cornerRadius = height / 2
        hudBackgroundView?.layer?.backgroundColor = RFTheme.glassFill.cgColor
        hudBackgroundView?.layer?.borderWidth = 0
        hudBackgroundView?.layer?.borderColor = NSColor.clear.cgColor
        hudGlassOverlayView?.isHidden = false
        hudGlassOverlayView?.accent = accent
        hudGlassOverlayView?.isCompact = visualOnly
    }

    private func clearHUDVisualBackground() {
        hudRefractedGlassView?.isHidden = true
        hudRefractedGlassView?.clearBackdrop()
        hudNativeGlassView?.isHidden = true
        hudBackgroundView?.isHidden = true
        hudGlassOverlayView?.isHidden = true
        hudBackdropCaptureKey = nil
    }

    private func refreshHUDBackdropIfNeeded(state: String, width: CGFloat, height: CGFloat, visualOnly: Bool, accent: NSColor) {
        if visualOnly {
            clearHUDVisualBackground()
            return
        }
        guard let panel = hudWindow, let refractedGlassView = hudRefractedGlassView else { return }
        guard hudScreenRefractionEnabled else {
            configureHUDGlassFallback(visualOnly: visualOnly, height: height, accent: accent)
            return
        }
        let frame = panel.frame
        let captureKey = [
            state,
            "\(Int(width.rounded()))x\(Int(height.rounded()))",
            "\(Int(frame.minX.rounded()))",
            "\(Int(frame.minY.rounded()))",
            visualOnly ? "visual" : "toast"
        ].joined(separator: "|")
        if hudBackdropCaptureKey != captureKey {
            hudBackdropCaptureKey = captureKey
            _ = refractedGlassView.refreshBackdrop(for: panel, accent: accent, isCompact: visualOnly)
        }
        if refractedGlassView.hasBackdrop {
            refractedGlassView.isHidden = false
            hudNativeGlassView?.isHidden = true
            hudBackgroundView?.isHidden = true
            hudGlassOverlayView?.isHidden = true
        } else {
            configureHUDGlassFallback(visualOnly: visualOnly, height: height, accent: accent)
        }
    }

    private func setHUDActionButtonTitle(_ title: String) {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center
        hudActionButton?.attributedTitle = NSAttributedString(
            string: title,
            attributes: [
                .font: NSFont.systemFont(ofSize: CGFloat(HUDSignalStylePolicy.toastActionFontSize), weight: .semibold),
                .foregroundColor: RFTheme.toastAction,
                .paragraphStyle: paragraph
            ]
        )
    }

    @objc private func copyHUDTranscript() {
        guard let text = hudCopyText, !text.isEmpty else { return }
        writeTextToPasteboard(text)
        latestTranscript = text
        setHUDActionButtonTitle("Copied")
        playPasteDoneSound()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in
            self?.hudWindow?.orderOut(nil)
            self?.hudBackdropCaptureKey = nil
            self?.hudRefractedGlassView?.clearBackdrop()
        }
    }

    private func transcriptPreview(_ text: String) -> String {
        let compact = text.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if compact.count <= 52 { return compact }
        return "\(compact.prefix(49))..."
    }

    private func positionHUD() {
        guard let panel = hudWindow else { return }
        let screen = NSScreen.main ?? NSScreen.screens.first
        let visible = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let size = panel.frame.size
        panel.setFrameOrigin(NSPoint(x: visible.midX - size.width / 2, y: visible.minY + 26))
    }

    private func hideHUD() {
        stopHUDMotionTimer()
        hudWindow?.orderOut(nil)
        hudCopyText = nil
        hudBackdropCaptureKey = nil
        hudRefractedGlassView?.clearBackdrop()
    }

    private func showBackgroundPolishHUD(action: BackgroundPolishAction, run: ActiveRun) {
        guard hudCopyText == nil else { return }
        let accent = backgroundPolishAccent(action: action)
        hudMotionVariant = backgroundPolishMotionVariant(action: action)
        hudRecipeSeed = nextHUDRecipeSeed()
        appendNativeEvent("background_polish_started", fields: [
            "run_id": run.runID,
            "action": action.rawValue
        ])
        showHUD(title: "", subtitle: "", state: "WORK", accent: accent)
    }

    private func hideBackgroundPolishHUDIfNeeded() {
        guard hudCopyText == nil else { return }
        guard hudStateLabel?.stringValue == "WORK" else { return }
        hideHUD()
    }

    private func showBackgroundCopyFallbackIfNeeded(_ text: String, draftWasPasted: Bool, replacementSucceeded: Bool) {
        guard BackgroundPolishToastPolicy.shouldShowCopyFallback(
            draftWasPasted: draftWasPasted,
            replacementSucceeded: replacementSucceeded
        ) else {
            hideBackgroundPolishHUDIfNeeded()
            return
        }
        showHUD(
            title: transcriptPreview(text),
            subtitle: "",
            state: "COPY",
            accent: RFTheme.mint,
            autoHide: 8.0,
            copyText: text
        )
    }

    private func warmWhisperSidecarIfNeeded() {
        guard envFlag("RAMBLEFIX_HOTKEY_NATIVE_WHISPER_SERVER", defaultValue: true),
              WhisperSidecarPolicy.shouldAutostartLegacySidecar(
                endpointPort: nativeWhisperServerEndpoint().port,
                autostartEnabled: runtimeFlag(
                    "RAMBLEFIX_HOTKEY_AUTOSTART_WHISPER_SERVER",
                    defaultValue: WhisperSidecarPolicy.defaultAutostartEnabled
                )
              ) else {
            return
        }
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self else { return }
            let endpoint = self.nativeWhisperServerEndpoint()
            if self.isWhisperSidecarReachable(endpoint: endpoint) {
                self.appendNativeEvent("whisper_sidecar_ready", fields: ["reason": "app_launch"])
                return
            }
            self.startWhisperSidecarIfNeeded(reason: "app_launch")
        }
    }

    private func warmNativeASRServerIfNeeded() {
        guard envFlag("RAMBLEFIX_HOTKEY_NATIVE_WHISPER_SERVER", defaultValue: true),
              runtimeFlag("RAMBLEFIX_HOTKEY_AUTOSTART_NATIVE_ASR_SERVER", defaultValue: true) else {
            return
        }
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self else { return }
            let endpoint = self.nativeWhisperServerEndpoint()
            guard endpoint.port == 8188 else { return }
            if self.isWhisperSidecarReachable(endpoint: endpoint) {
                self.appendNativeEvent("native_asr_server_ready", fields: ["reason": "app_launch"])
                return
            }
            self.startNativeASRServerIfNeeded(reason: "app_launch")
        }
    }

    private func ensureNativeASRServerReady(endpoint: URL, runID: String) {
        guard runtimeFlag("RAMBLEFIX_HOTKEY_AUTOSTART_NATIVE_ASR_SERVER", defaultValue: true) else {
            return
        }
        if isWhisperSidecarReachable(endpoint: endpoint) {
            return
        }
        startNativeASRServerIfNeeded(reason: "pre_transcribe", runID: runID)
        let waitSeconds = envDouble(
            "RAMBLEFIX_HOTKEY_NATIVE_ASR_STARTUP_WAIT_SECONDS",
            defaultValue: 12.0,
            minValue: 0.0
        )
        let ready = waitForWhisperSidecar(endpoint: endpoint, timeout: waitSeconds)
        appendNativeEvent("native_asr_server_pre_transcribe_wait", fields: [
            "run_id": runID,
            "ready": ready,
            "wait_seconds": roundedSeconds(waitSeconds)
        ])
    }

    private func startNativeASRServerIfNeeded(reason: String, runID: String? = nil) {
        let now = Date()
        if WhisperSidecarPolicy.shouldThrottleStartAttempt(lastAttemptAt: nativeASRServerStartAttemptedAt, now: now) {
            appendNativeEvent("native_asr_server_start_throttled", fields: [
                "reason": reason,
                "run_id": runID ?? ""
            ])
            return
        }
        nativeASRServerStartAttemptedAt = now

        let manager = FileManager.default
        let scriptURL = projectRoot.appendingPathComponent("script/start_srota_server.sh")
        let pythonURL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["RAMBLEFIX_PYTHON"] ?? projectRoot.appendingPathComponent(".venv/bin/python").path)
        let executableURL: URL
        let arguments: [String]
        if manager.fileExists(atPath: scriptURL.path) {
            executableURL = scriptURL
            arguments = []
        } else if manager.fileExists(atPath: pythonURL.path) {
            executableURL = pythonURL
            arguments = ["-m", "ramblefix.srota_server"]
        } else {
            appendNativeEvent("native_asr_server_start_failed", fields: [
                "reason": reason,
                "run_id": runID ?? "",
                "error": "missing script/start_srota_server.sh and .venv/bin/python"
            ])
            return
        }

        let logsURL = projectRoot.appendingPathComponent("logs", isDirectory: true)
        try? manager.createDirectory(at: logsURL, withIntermediateDirectories: true)
        let logURL = logsURL.appendingPathComponent("srota-server-8188.log")
        manager.createFile(atPath: logURL.path, contents: nil)
        let logHandle = try? FileHandle(forWritingTo: logURL)
        _ = try? logHandle?.seekToEnd()

        let process = Process()
        process.executableURL = executableURL
        process.arguments = arguments
        process.currentDirectoryURL = projectRoot
        process.environment = runtimeEnvironment(overrides: [
            "RAMBLEFIX_SROTA_BACKEND": "mlx",
            "RAMBLEFIX_HINGLISH_FINALIZER_BACKEND": "oriserve",
            "RAMBLEFIX_ORISERVE_BACKEND": "ggml",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1"
        ])
        if let logHandle {
            process.standardOutput = logHandle
            process.standardError = logHandle
        }
        do {
            try process.run()
            appendNativeEvent("native_asr_server_start_requested", fields: [
                "reason": reason,
                "run_id": runID ?? "",
                "pid": Int(process.processIdentifier),
                "executable": executableURL.lastPathComponent
            ])
        } catch {
            appendNativeEvent("native_asr_server_start_failed", fields: [
                "reason": reason,
                "run_id": runID ?? "",
                "error": String(describing: error)
            ])
        }
    }

    private func ensureWhisperSidecarReady(endpoint: URL, runID: String) {
        guard WhisperSidecarPolicy.shouldAutostartLegacySidecar(
            endpointPort: endpoint.port,
            autostartEnabled: runtimeFlag(
                "RAMBLEFIX_HOTKEY_AUTOSTART_WHISPER_SERVER",
                defaultValue: WhisperSidecarPolicy.defaultAutostartEnabled
            )
        ) else {
            return
        }
        if isWhisperSidecarReachable(endpoint: endpoint) {
            return
        }
        startWhisperSidecarIfNeeded(reason: "pre_transcribe", runID: runID)
        let waitSeconds = envDouble(
            "RAMBLEFIX_HOTKEY_WHISPER_STARTUP_WAIT_SECONDS",
            defaultValue: WhisperSidecarPolicy.defaultStartupWaitSeconds,
            minValue: 0.0
        )
        let ready = waitForWhisperSidecar(endpoint: endpoint, timeout: waitSeconds)
        appendNativeEvent("whisper_sidecar_pre_transcribe_wait", fields: [
            "run_id": runID,
            "ready": ready,
            "wait_seconds": roundedSeconds(waitSeconds)
        ])
    }

    private func startWhisperSidecarIfNeeded(reason: String, runID: String? = nil) {
        let now = Date()
        if WhisperSidecarPolicy.shouldThrottleStartAttempt(lastAttemptAt: whisperSidecarStartAttemptedAt, now: now) {
            appendNativeEvent("whisper_sidecar_start_throttled", fields: [
                "reason": reason,
                "run_id": runID ?? ""
            ])
            return
        }
        whisperSidecarStartAttemptedAt = now

        let scriptURL = projectRoot.appendingPathComponent("script/start_whisper_server.sh")
        guard FileManager.default.fileExists(atPath: scriptURL.path) else {
            appendNativeEvent("whisper_sidecar_start_failed", fields: [
                "reason": reason,
                "run_id": runID ?? "",
                "error": "missing script/start_whisper_server.sh"
            ])
            return
        }

        let logURL = projectRoot.appendingPathComponent("logs/whisper-server-8178.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let logHandle = try? FileHandle(forWritingTo: logURL)
        _ = try? logHandle?.seekToEnd()

        let process = Process()
        process.executableURL = scriptURL
        process.currentDirectoryURL = projectRoot
        process.environment = runtimeEnvironment(overrides: [:])
        if let logHandle {
            process.standardOutput = logHandle
            process.standardError = logHandle
        }
        do {
            try process.run()
            appendNativeEvent("whisper_sidecar_start_requested", fields: [
                "reason": reason,
                "run_id": runID ?? "",
                "pid": Int(process.processIdentifier)
            ])
        } catch {
            appendNativeEvent("whisper_sidecar_start_failed", fields: [
                "reason": reason,
                "run_id": runID ?? "",
                "error": String(describing: error)
            ])
        }
    }

    private func waitForWhisperSidecar(endpoint: URL, timeout: TimeInterval) -> Bool {
        if timeout <= 0 {
            return isWhisperSidecarReachable(endpoint: endpoint)
        }
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if isWhisperSidecarReachable(endpoint: endpoint) {
                return true
            }
            Thread.sleep(forTimeInterval: 0.25)
        }
        return isWhisperSidecarReachable(endpoint: endpoint)
    }

    private func isWhisperSidecarReachable(endpoint: URL) -> Bool {
        guard isLoopbackHost(endpoint.host) else { return false }
        let host = endpoint.host ?? "127.0.0.1"
        let port = endpoint.port ?? 8178
        let ncPath = FileManager.default.fileExists(atPath: "/usr/bin/nc") ? "/usr/bin/nc" : "/bin/nc"
        guard FileManager.default.fileExists(atPath: ncPath) else { return false }
        let result = runProcess(ncPath, ["-z", host, String(port)], currentDirectory: projectRoot)
        return result.exitCode == 0
    }

    private func backgroundPolishAccent(action: BackgroundPolishAction) -> NSColor {
        let pressureAccent = configureProcessingHUDSignals()
        let pressured = latestThermalState != .nominal || latestSystemLoadRatio >= HUDSignalStylePolicy.loadedSystemLoadRatio || isCaptureLikelyWeak()
        if pressured {
            return pressureAccent
        }
        if action == .hindiPolish || action == .processSecondPass {
            hudColorShift = hueWithJitter(base: 0.74, jitter: 0.035)
            hudMotionSpeed = CGFloat.random(in: 0.20...0.36)
            hudMotionFrameInterval = hudDefaultMotionFrameInterval
            return RFTheme.violet
        }
        if action == .structure {
            hudColorShift = hueWithJitter(base: 0.48, jitter: 0.04)
            hudMotionSpeed = CGFloat.random(in: 0.24...0.42)
            hudMotionFrameInterval = hudDefaultMotionFrameInterval
            return RFTheme.cyan
        }
        return pressureAccent
    }

    private func backgroundPolishMotionVariant(action: BackgroundPolishAction) -> Int {
        switch action {
        case .hindiPolish, .processSecondPass:
            return HUDSignalStylePolicy.hindiMotionVariant
        case .termPolish, .structure:
            return HUDSignalStylePolicy.englishMotionVariant
        default:
            return HUDSignalStylePolicy.englishMotionVariant
        }
    }

    private func playRecordingStartSound() {
        guard envFlag("RAMBLEFIX_HOTKEY_START_SOUND", defaultValue: true) else { return }
        if recordingStartSound == nil {
            recordingStartSound = ["Pop", "Tink", "Glass"]
                .compactMap { NSSound(named: NSSound.Name($0)) }
                .first
            recordingStartSound?.volume = 0.16
        }
        guard let sound = recordingStartSound else { return }
        sound.stop()
        sound.currentTime = 0
        sound.play()
    }

    private func playPasteDoneSound() {
        guard envFlag("RAMBLEFIX_HOTKEY_SUCCESS_SOUND", defaultValue: true) else { return }
        if pasteDoneSound == nil {
            pasteDoneSound = ["Tink", "Pop", "Glass"]
                .compactMap { NSSound(named: NSSound.Name($0)) }
                .first
            pasteDoneSound?.volume = 0.32
        }
        guard let sound = pasteDoneSound else {
            NSSound.beep()
            return
        }
        sound.stop()
        sound.currentTime = 0
        sound.play()
    }

    private func startRecordingHUDTimer(runID: String) {
        stopRecordingHUDTimer()
        hudTimer = Timer.scheduledTimer(withTimeInterval: 0.08, repeats: true) { [weak self] _ in
            guard let self,
                  let run = self.activeRun,
                  run.runID == runID else {
                return
            }
            let level: CGFloat
            if let recorder = self.recorder {
                recorder.updateMeters()
                level = self.normalizedAudioLevel(power: recorder.averagePower(forChannel: 0))
            } else if let streamingRecorder = self.streamingRecorder {
                level = streamingRecorder.latestNormalizedLevel
            } else {
                return
            }
            self.updateCaptureSignal(level: level)
            self.showHUD(
                title: "",
                subtitle: "",
                state: "REC",
                accent: self.recordingHUDAccent,
                level: level
            )
        }
    }

    private func stopRecordingHUDTimer() {
        hudTimer?.invalidate()
        hudTimer = nil
    }

    private func startSlowProcessingFeedback(runID: String) {
        stopSlowProcessingFeedback()
        slowProcessingRunID = runID
        let startedAt = Date()
        slowProcessingFeedbackTimer = Timer.scheduledTimer(withTimeInterval: SlowProcessingFeedbackPolicy.defaultDelaySeconds, repeats: false) { [weak self] _ in
            guard let self,
                  self.slowProcessingRunID == runID,
                  self.isTranscribing,
                  SlowProcessingFeedbackPolicy.shouldShowFeedback(startedAt: startedAt, now: Date()) else {
                return
            }
            let accent = self.configureProcessingHUDSignals()
            self.statusItem?.button?.title = "RF wait"
            self.showHUD(
                title: "Still working",
                subtitle: "Computer busy; keeping audio.",
                state: "WAIT",
                accent: accent
            )
        }
    }

    private func stopSlowProcessingFeedback() {
        slowProcessingFeedbackTimer?.invalidate()
        slowProcessingFeedbackTimer = nil
        slowProcessingRunID = nil
    }

    private func startHUDMotionTimer() {
        guard hudMotionTimer == nil else { return }
        hudMotionTimer = Timer.scheduledTimer(withTimeInterval: hudMotionFrameInterval, repeats: true) { [weak self] _ in
            guard let self else { return }
            self.hudSignalView?.phase += self.hudMotionSpeed
        }
    }

    private func stopHUDMotionTimer() {
        hudMotionTimer?.invalidate()
        hudMotionTimer = nil
    }

    private func normalizedAudioLevel(power: Float) -> CGFloat {
        let normalized = max(0.0, min(1.0, (Double(power) + 55.0) / 45.0))
        return CGFloat(normalized)
    }

    private func showOrRefreshHistoryWindow() {
        if historyWindow == nil {
            let window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 760, height: 560),
                styleMask: [.titled, .closable, .miniaturizable, .resizable],
                backing: .buffered,
                defer: false
            )
            window.title = "\(appName) Transcript History"
            window.minSize = NSSize(width: 560, height: 360)

            let content = NSView(frame: NSRect(x: 0, y: 0, width: 760, height: 560))
            content.autoresizingMask = [.width, .height]
            content.wantsLayer = true
            content.layer?.backgroundColor = RFTheme.paper.cgColor

            let title = NSTextField(labelWithString: "Recent Transcripts")
            title.frame = NSRect(x: 18, y: 518, width: 260, height: 24)
            title.font = .systemFont(ofSize: 18, weight: .semibold)
            title.textColor = RFTheme.ink
            title.autoresizingMask = [.minYMargin]

            let refresh = NSButton(title: "Refresh", target: self, action: #selector(refreshTranscriptHistory))
            refresh.frame = NSRect(x: 548, y: 514, width: 86, height: 30)
            refresh.autoresizingMask = [.minXMargin, .minYMargin]
            refresh.bezelStyle = .rounded

            let copy = NSButton(title: "Copy Latest", target: self, action: #selector(copyLatestTranscript))
            copy.frame = NSRect(x: 638, y: 514, width: 104, height: 30)
            copy.autoresizingMask = [.minXMargin, .minYMargin]
            copy.bezelStyle = .rounded

            let scroll = NSScrollView(frame: NSRect(x: 18, y: 18, width: 724, height: 486))
            scroll.hasVerticalScroller = true
            scroll.autoresizingMask = [.width, .height]
            scroll.wantsLayer = true
            scroll.layer?.cornerRadius = 12
            scroll.layer?.borderWidth = 1
            scroll.layer?.borderColor = NSColor.black.withAlphaComponent(0.08).cgColor

            let textView = NSTextView(frame: scroll.bounds)
            textView.isEditable = false
            textView.isSelectable = true
            textView.drawsBackground = true
            textView.backgroundColor = RFTheme.paperCard
            textView.textColor = RFTheme.ink
            textView.font = .systemFont(ofSize: 13)
            textView.textContainerInset = NSSize(width: 12, height: 12)
            textView.autoresizingMask = [.width, .height]
            scroll.documentView = textView

            content.addSubview(title)
            content.addSubview(refresh)
            content.addSubview(copy)
            content.addSubview(scroll)
            window.contentView = content
            historyWindow = window
            historyTextView = textView
        }
        refreshHistoryTextView()
        historyWindow?.center()
        historyWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func refreshHistoryWindowIfVisible() {
        guard historyWindow?.isVisible == true else { return }
        refreshHistoryTextView()
    }

    private func refreshHistoryTextView() {
        let entries = loadHistoryEntries(limit: 40)
        latestHistoryTranscriptCache = entries.first?.text
        updateMenuState()
        if entries.isEmpty {
            historyTextView?.string = "No transcripts yet."
            return
        }
        let body = entries.map { entry -> String in
            let latency = entry.latencySeconds.map { "\(String(format: "%.2f", $0))s" } ?? "no paste"
            let route = entry.route.isEmpty ? "unknown route" : entry.route
            return "\(entry.createdAt) - \(entry.status) - \(latency) - \(entry.targetName) - \(route)\n\(entry.text)"
        }.joined(separator: "\n\n")
        historyTextView?.string = body
    }

    private func loadHistoryEntries(limit: Int) -> [HistoryEntry] {
        let historyURL = projectRoot.appendingPathComponent("logs/history.jsonl")
        guard let raw = try? String(contentsOf: historyURL, encoding: .utf8) else { return [] }
        var entries: [HistoryEntry] = []
        for line in raw.split(separator: "\n") {
            guard let data = String(line).data(using: .utf8),
                  let row = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                continue
            }
            guard let text = historyText(from: row), !text.isEmpty else { continue }
            let target = (row["target_app"] as? [String: Any])?["name"] as? String ?? "Unknown"
            let timings = row["timings"] as? [String: Any]
            let latency = timings?["release_to_paste_seconds"] as? Double
            entries.append(HistoryEntry(
                createdAt: compactCreatedAt(row["created_at"] as? String ?? ""),
                status: row["status"] as? String ?? "unknown",
                targetName: target,
                route: row["route"] as? String ?? row["asr_engine"] as? String ?? "",
                latencySeconds: latency,
                text: text
            ))
        }
        return Array(entries.suffix(limit).reversed())
    }

    private func latestTranscriptFromMemoryOrHistory() -> String? {
        if let cached = latestTranscriptFromMemoryOrCache() {
            return cached
        }
        let historyTranscript = loadHistoryEntries(limit: 12)
            .first { !isNoSpeechText($0.text) && !$0.text.contains("ASR failure detected") }?
            .text
        latestHistoryTranscriptCache = historyTranscript
        return historyTranscript
    }

    private func latestTranscriptFromMemoryOrCache() -> String? {
        if let memory = HistoryMenuPolicy.usableTranscript(latestTranscript) {
            return memory
        }
        return HistoryMenuPolicy.usableTranscript(latestHistoryTranscriptCache)
    }

    private func historyText(from row: [String: Any]) -> String? {
        let candidates = [
            row["corrected_text"] as? String,
            row["pasted_text"] as? String,
            row["raw_text"] as? String
        ]
        for candidate in candidates {
            let text = candidate?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if !text.isEmpty {
                return text
            }
        }
        return nil
    }

    private func compactCreatedAt(_ value: String) -> String {
        guard let tIndex = value.firstIndex(of: "T") else { return value }
        let afterT = value[value.index(after: tIndex)...]
        return String(afterT.prefix(8)).replacingOccurrences(of: "Z", with: "")
    }

    private func writeTextToPasteboard(_ text: String) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
    }

    @objc private func quit() {
        permissionRetryTimer?.invalidate()
        systemHealthTimer?.invalidate()
        learningTimer?.invalidate()
        stopRecordingHUDTimer()
        stopSlowProcessingFeedback()
        stopHUDMotionTimer()
        if let hotKeyRef {
            UnregisterEventHotKey(hotKeyRef)
        }
        if let eventTapRunLoopSource {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), eventTapRunLoopSource, .commonModes)
        }
        if let eventTap {
            CFMachPortInvalidate(eventTap)
        }
        NSApp.terminate(nil)
    }

    private func runProcess(_ executable: String, _ arguments: [String], currentDirectory: URL? = nil, environmentOverrides: [String: String] = [:]) -> ProcessResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.currentDirectoryURL = currentDirectory ?? projectRoot
        process.environment = runtimeEnvironment(overrides: environmentOverrides)
        let stdout = Pipe()
        let stderr = Pipe()
        process.standardOutput = stdout
        process.standardError = stderr
        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            return ProcessResult(exitCode: 1, stdout: "", stderr: String(describing: error))
        }
        let stdoutText = String(data: stdout.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        let stderrText = String(data: stderr.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        return ProcessResult(exitCode: process.terminationStatus, stdout: stdoutText, stderr: stderrText)
    }

    private func runtimeEnvironment(overrides: [String: String]) -> [String: String] {
        var environment = ProcessInfo.processInfo.environment
        let manager = FileManager.default
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONNOUSERSITE"] = "1"

        let srcPath = projectRoot.appendingPathComponent("src", isDirectory: true).path
        if manager.fileExists(atPath: srcPath) {
            let existing = environment["PYTHONPATH"] ?? ""
            environment["PYTHONPATH"] = existing.isEmpty ? srcPath : "\(srcPath):\(existing)"
        }

        let bundledWhisperServer = projectRoot.appendingPathComponent("bin/whisper-server").path
        if environment["RAMBLEFIX_WHISPER_SERVER_BINARY"] == nil,
           manager.fileExists(atPath: bundledWhisperServer) {
            environment["RAMBLEFIX_WHISPER_SERVER_BINARY"] = bundledWhisperServer
        }

        let bundledWhisperCLI = projectRoot.appendingPathComponent("bin/whisper-cli").path
        if environment["RAMBLEFIX_WHISPER_CPP_BINARY"] == nil,
           manager.fileExists(atPath: bundledWhisperCLI) {
            environment["RAMBLEFIX_WHISPER_CPP_BINARY"] = bundledWhisperCLI
        }

        let bundledWhisperModel = projectRoot.appendingPathComponent("models/ggml-small.bin").path
        if environment["RAMBLEFIX_WHISPER_MODEL"] == nil,
           manager.fileExists(atPath: bundledWhisperModel) {
            environment["RAMBLEFIX_WHISPER_MODEL"] = bundledWhisperModel
        }

        let bundledOriserveModel = projectRoot.appendingPathComponent("models/oriserve-ggml/ggml-oriserve-hinglish-q8_0.bin").path
        if environment["RAMBLEFIX_ORISERVE_GGML_MODEL"] == nil,
           manager.fileExists(atPath: bundledOriserveModel) {
            environment["RAMBLEFIX_ORISERVE_GGML_MODEL"] = bundledOriserveModel
            environment["RAMBLEFIX_ORISERVE_BACKEND"] = environment["RAMBLEFIX_ORISERVE_BACKEND"] ?? "ggml"
        }

        for (key, value) in overrides {
            environment[key] = value
        }
        return environment
    }

    private func parsePayload(_ text: String) -> DictationPayload? {
        guard let start = text.firstIndex(of: "{"), let end = text.lastIndex(of: "}") else {
            return nil
        }
        let json = String(text[start...end])
        guard let data = json.data(using: .utf8),
              let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        return payloadFromJSON(payload)
    }

    private func payloadFromJSON(_ payload: [String: Any]) -> DictationPayload? {
        let rawText = payload["raw_text"] as? String ?? ""
        let originalText = payload["text"] as? String ?? ""
        let corrected = applyApprovedPhraseFixes(to: originalText)
        var quality = payload["quality"] as? [String: Any] ?? [:]
        if corrected.changed {
            quality["glossary_changed"] = true
            quality["glossary_processor"] = "approved_phrase_fixes"
        }
        return DictationPayload(
            rawText: rawText,
            text: corrected.text,
            engine: payload["engine"] as? String ?? "",
            processor: corrected.changed ? "glossary" : (payload["processor"] as? String ?? ""),
            fallbackReason: payload["fallback_reason"] as? String ?? "",
            quality: quality,
            seconds: payload["seconds"] as? Double ?? 0,
            route: payload["route"] as? String ?? "",
            safeUpdate: payload["safe_update"] as? Bool ?? false
        )
    }

    private func applyApprovedPhraseFixes(to text: String) -> (text: String, changed: Bool) {
        let result = ApprovedPhraseFixPolicy.apply(text: text, fixes: approvedPhraseFixes())
        return (result.text, result.changed)
    }

    private func approvedPhraseFixes() -> [ApprovedPhraseFixEntry] {
        let path = projectRoot.appendingPathComponent("config/phrase_fixes.json")
        let signature = protectedTermsSignature(paths: [path])
        if let cached = approvedPhraseFixCache, signature == approvedPhraseFixCacheSignature {
            return cached
        }
        var fixes: [ApprovedPhraseFixEntry] = []
        if let data = try? Data(contentsOf: path),
           let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let items = payload["phrase_fixes"] as? [[String: Any]] {
            for item in items {
                guard item["enabled"] as? Bool != false,
                      item["approved"] as? Bool == true,
                      let source = item["source"] as? String,
                      let replacement = item["replacement"] as? String,
                      !(item["note"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                      !source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                      !replacement.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    continue
                }
                fixes.append(ApprovedPhraseFixEntry(source: source, replacement: replacement))
            }
        }
        fixes.sort { $0.source.count > $1.source.count }
        approvedPhraseFixCache = fixes
        approvedPhraseFixCacheSignature = signature
        return fixes
    }

    private func postSrotaJSON(path: String, payload: [String: Any], timeout: TimeInterval) -> (payload: [String: Any]?, error: String?) {
        guard let url = URL(string: "http://127.0.0.1:8188\(path)") else {
            return (nil, "Invalid Srota URL: \(path)")
        }
        guard let body = try? JSONSerialization.data(withJSONObject: payload) else {
            return (nil, "Could not encode Srota request JSON.")
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body

        let semaphore = DispatchSemaphore(value: 0)
        var responsePayload: [String: Any]?
        var responseError: String?
        let task = URLSession.shared.dataTask(with: request) { data, response, error in
            defer { semaphore.signal() }
            if let error {
                responseError = String(describing: error)
                return
            }
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                responseError = "Srota returned HTTP \(http.statusCode)."
                return
            }
            guard let data,
                  let parsed = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                responseError = "Srota returned invalid JSON."
                return
            }
            if let errorText = parsed["error"] as? String, !errorText.isEmpty {
                responseError = errorText
                return
            }
            responsePayload = parsed
        }
        task.resume()
        if semaphore.wait(timeout: .now() + timeout + 0.5) == .timedOut {
            task.cancel()
            return (nil, "Srota request timed out: \(path)")
        }
        return (responsePayload, responseError)
    }

    private func paste(_ text: String, targetPID: pid_t?, targetBundleID: String) -> Bool {
        let result = pasteWithResult(text, targetPID: targetPID, targetBundleID: targetBundleID)
        return result.attempted && !result.copyFallbackRecommended
    }

    private func pasteWithResult(_ text: String, targetPID: pid_t?, targetBundleID: String) -> PasteAttemptResult {
        let blocked = PasteAttemptResult(attempted: false, verification: .failed, copyFallbackRecommended: true)
        guard preflightAccessibility(prompt: true) else { return blocked }
        guard let targetPID,
              let target = NSRunningApplication(processIdentifier: targetPID) else {
            return blocked
        }
        if !targetBundleID.isEmpty, target.bundleIdentifier != targetBundleID {
            return blocked
        }
        target.activate(options: [.activateIgnoringOtherApps])
        Thread.sleep(forTimeInterval: 0.20)
        guard let frontmost = NSWorkspace.shared.frontmostApplication,
              frontmost.processIdentifier == targetPID else {
            return blocked
        }
        if !targetBundleID.isEmpty, frontmost.bundleIdentifier != targetBundleID {
            return blocked
        }
        let confidence = focusedPasteTargetConfidence()
        guard confidence != .blocked else {
            return blocked
        }

        let pasteboard = NSPasteboard.general
        let oldItems = snapshotPasteboard(pasteboard)
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
        let ramblefixChange = pasteboard.changeCount

        let source = CGEventSource(stateID: .hidSystemState)
        let keyDown = CGEvent(keyboardEventSource: source, virtualKey: 0x09, keyDown: true)
        let keyUp = CGEvent(keyboardEventSource: source, virtualKey: 0x09, keyDown: false)
        keyDown?.flags = .maskCommand
        keyUp?.flags = .maskCommand
        keyDown?.post(tap: .cghidEventTap)
        keyUp?.post(tap: .cghidEventTap)

        let verification = waitForPasteVerification(
            text,
            targetPID: targetPID,
            targetBundleID: targetBundleID,
            timeout: pasteVerificationTimeout(
                forCharacterCount: text.count,
                confidence: confidence,
                targetBundleID: targetBundleID
            )
        )
        let shouldOfferCopy = PasteFocusHeuristics.shouldOfferCopyFallback(
            confidence: confidence,
            verification: verification,
            targetBundleID: targetBundleID
        )
        let restoreDelay = pasteboardRestoreDelay(verification: verification, copyFallbackRecommended: shouldOfferCopy)
        appendNativeEvent("paste_attempt_result", fields: [
            "target_pid": Int(targetPID),
            "target_bundle_id": targetBundleID,
            "target_confidence": pasteTargetConfidenceName(confidence),
            "verification": pasteVerificationName(verification),
            "text_chars": text.count,
            "copy_fallback_recommended": shouldOfferCopy,
            "clipboard_restore_delay_seconds": restoreDelay
        ])

        DispatchQueue.main.asyncAfter(deadline: .now() + restoreDelay) {
            if pasteboard.changeCount == ramblefixChange {
                pasteboard.clearContents()
                if !oldItems.isEmpty {
                    pasteboard.writeObjects(oldItems)
                }
            }
        }
        return PasteAttemptResult(attempted: true, verification: verification, copyFallbackRecommended: shouldOfferCopy)
    }

    private func pasteboardRestoreDelay(verification: PasteVerificationStatus, copyFallbackRecommended: Bool) -> TimeInterval {
        if verification == .unverified && !copyFallbackRecommended {
            return 8.0
        }
        return 0.75
    }

    private func waitForPasteVerification(_ text: String, targetPID: pid_t, targetBundleID: String, timeout: TimeInterval) -> PasteVerificationStatus {
        let deadline = Date().addingTimeInterval(timeout)
        var lastStatus = PasteVerificationStatus.unverified
        while Date() < deadline {
            Thread.sleep(forTimeInterval: 0.08)
            let status = verifyPasteLanded(text, targetPID: targetPID, targetBundleID: targetBundleID)
            if status == .verified {
                return .verified
            }
            lastStatus = status
        }
        return lastStatus
    }

    private func pasteVerificationTimeout(forCharacterCount count: Int, confidence: PasteTargetConfidence, targetBundleID: String) -> TimeInterval {
        if confidence == .ambiguous,
           PasteFocusHeuristics.trustsUnverifiedPaste(targetBundleID: targetBundleID) {
            return 0.24
        }
        if count >= 1200 { return 1.8 }
        if count >= 500 { return 1.2 }
        if count >= 240 { return 0.8 }
        return 0.24
    }

    private func snapshotPasteboard(_ pasteboard: NSPasteboard) -> [NSPasteboardItem] {
        guard let items = pasteboard.pasteboardItems else { return [] }
        return items.map { item in
            let copy = NSPasteboardItem()
            for type in item.types {
                if let data = item.data(forType: type) {
                    copy.setData(data, forType: type)
                }
            }
            return copy
        }
    }

    private func cleanupAudioIfNeeded(_ url: URL) {
        if shouldRetainAllHotkeyAudio(mode: nil) {
            return
        }
        try? FileManager.default.removeItem(at: url)
    }

    private func shouldRetainAllHotkeyAudio(mode: RunMode?) -> Bool {
        return hotkeyAudioRetentionReason(mode: mode) != nil
    }

    private func isCaptureEvalAudioEnabled() -> Bool {
        CaptureEvalAudioPolicy.isEnabled(
            storedValue: UserDefaults.standard.object(forKey: captureEvalAudioDefaultsKey)
        )
    }

    private func hotkeyAudioRetentionReason(mode: RunMode?) -> String? {
        if mode == .meeting { return "meeting" }
        if envFlag("RAMBLEFIX_HOTKEY_RETAIN_AUDIO", defaultValue: false) { return "env_retain_audio" }
        if envFlag("RAMBLEFIX_HOTKEY_TEST_MODE", defaultValue: false) { return "test_mode" }
        if isCaptureEvalAudioEnabled() { return "eval_capture" }
        return nil
    }

    private func hotkeyRetainedAudioLimit() -> Int {
        RetainedAudioPolicy.normalizedLimit(
            from: ProcessInfo.processInfo.environment[hotkeyRetainedAudioLimitEnv]
        )
    }

    private func pruneRetainedHotkeyAudioIfNeeded() {
        let audioDir = projectRoot.appendingPathComponent("logs/hotkey_audio", isDirectory: true)
        let toDelete = RetainedAudioPolicy.filesToPrune(in: audioDir, keepingMax: hotkeyRetainedAudioLimit())
        for url in toDelete {
            try? FileManager.default.removeItem(at: url)
        }
    }

    private func streamChunkCount(in directory: URL) -> Int {
        let urls = (try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: nil
        )) ?? []
        return urls.filter { $0.pathExtension.lowercased() == "wav" }.count
    }

    private func retainAudioForDebugIfNeeded(run: ActiveRun, reason: String) -> URL? {
        if shouldRetainAllHotkeyAudio(mode: run.mode) {
            return run.audioURL
        }
        guard envFlag("RAMBLEFIX_HOTKEY_RETAIN_FAILURE_AUDIO", defaultValue: true) else {
            return nil
        }
        let audioDir = projectRoot.appendingPathComponent("logs/hotkey_audio/failures", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: audioDir, withIntermediateDirectories: true)
            let targetURL = audioDir.appendingPathComponent("\(run.runID)-\(reason).wav")
            if FileManager.default.fileExists(atPath: targetURL.path) {
                try? FileManager.default.removeItem(at: targetURL)
            }
            try FileManager.default.copyItem(at: run.audioURL, to: targetURL)
            return targetURL
        } catch {
            return nil
        }
    }

    private func isNoSpeechPayload(_ payload: DictationPayload) -> Bool {
        if payload.quality["blank_or_no_speech"] as? Bool == true {
            return true
        }
        return isNoSpeechText(payload.text) || isNoSpeechText(payload.rawText)
    }

    private func isRetryableEmptyPayload(_ payload: DictationPayload) -> Bool {
        shouldRunFallbackRescue(for: payload)
            && payload.rawText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func shouldRunFallbackRescue(for payload: DictationPayload) -> Bool {
        FallbackRescuePolicy.shouldRun(
            route: payload.route,
            fallbackReason: payload.fallbackReason,
            audioProbablySilent: payload.quality["audio_probably_silent"] as? Bool == true
        )
    }

    private func isBlockedTranscriptionPayload(_ payload: DictationPayload) -> Bool {
        if payload.quality["degenerate"] as? Bool == true {
            return true
        }
        let text = payload.text.trimmingCharacters(in: .whitespacesAndNewlines)
        return text.range(of: "ASR failure detected", options: [.caseInsensitive, .anchored]) != nil
    }

    private func isNoSpeechText(_ text: String) -> Bool {
        let stripped = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if stripped.isEmpty { return true }
        let lower = stripped.lowercased()
        if lower == "<|nospeech|>" { return true }
        var markerTrimSet = CharacterSet(charactersIn: "[](){}<>._- ")
        markerTrimSet.formUnion(.whitespacesAndNewlines)
        let marker = lower
            .trimmingCharacters(in: markerTrimSet)
            .replacingOccurrences(of: "_", with: " ")
            .split { $0.isWhitespace }
            .joined(separator: " ")
        let noSpeechMarkers: Set<String> = [
            "blank audio",
            "blank",
            "silence",
            "silent audio",
            "no speech",
            "no speech detected",
            "inaudible",
            "music",
            "noise"
        ]
        return noSpeechMarkers.contains(marker)
    }

    private func createDiagnosticsArchive() throws -> URL {
        let manager = FileManager.default
        let exportsDir = projectRoot.appendingPathComponent("logs/diagnostics", isDirectory: true)
        try manager.createDirectory(at: exportsDir, withIntermediateDirectories: true)

        let stamp = timestampRunID()
        let bundleName = "RambleFixDiagnostics-\(stamp)"
        let bundleDir = exportsDir.appendingPathComponent(bundleName, isDirectory: true)
        let archiveURL = exportsDir.appendingPathComponent("\(bundleName).zip")
        try? manager.removeItem(at: bundleDir)
        try? manager.removeItem(at: archiveURL)
        try manager.createDirectory(at: bundleDir, withIntermediateDirectories: true)

        try diagnosticsSummaryText().write(
            to: bundleDir.appendingPathComponent("summary.txt"),
            atomically: true,
            encoding: .utf8
        )
        try historyDiagnosticsSummary().write(
            to: bundleDir.appendingPathComponent("history_summary.txt"),
            atomically: true,
            encoding: .utf8
        )
        try sanitizedJSONLinesTail(
            sourceURL: projectRoot.appendingPathComponent("logs/native_events.jsonl"),
            limit: 300
        ).write(
            to: bundleDir.appendingPathComponent("native_events_sanitized_tail.jsonl"),
            atomically: true,
            encoding: .utf8
        )

        let result = runProcess("/usr/bin/zip", ["-qry", archiveURL.path, bundleName], currentDirectory: exportsDir)
        guard result.exitCode == 0, manager.fileExists(atPath: archiveURL.path) else {
            throw NSError(
                domain: "RambleFixDiagnostics",
                code: Int(result.exitCode),
                userInfo: [NSLocalizedDescriptionKey: result.stderr.isEmpty ? "zip failed" : result.stderr]
            )
        }
        try? manager.removeItem(at: bundleDir)
        return archiveURL
    }

    private func diagnosticsSummaryText() -> String {
        let info = Bundle.main.infoDictionary ?? [:]
        let version = info["CFBundleShortVersionString"] as? String ?? "unknown"
        let build = info["CFBundleVersion"] as? String ?? "unknown"
        let os = ProcessInfo.processInfo.operatingSystemVersionString
        let health = systemHealthSnapshot()
        return [
            "RambleFix diagnostics",
            "generated_at: \(iso(Date()))",
            "app: \(appName)",
            "version: \(version)",
            "build: \(build)",
            "os: \(os)",
            "processor_count: \(ProcessInfo.processInfo.processorCount)",
            "project_root: \(projectRoot.path)",
            "meeting_mode_enabled: \(meetingModeEnabled)",
            "capture_eval_audio_enabled: \(isCaptureEvalAudioEnabled())",
            "includes_raw_audio: false",
            "includes_raw_transcripts: false",
            "system_pressure: \(health["pressure"] ?? "unknown")",
            "thermal_state: \(health["thermal_state"] ?? "unknown")",
            "load_ratio_1m_per_cpu: \(health["load_ratio_1m_per_cpu"] ?? "unknown")"
        ].joined(separator: "\n") + "\n"
    }

    private func historyDiagnosticsSummary() -> String {
        let historyURL = projectRoot.appendingPathComponent("logs/history.jsonl")
        guard let raw = try? String(contentsOf: historyURL, encoding: .utf8) else {
            return "history_rows_scanned: 0\n"
        }
        let lines = raw.split(separator: "\n").suffix(500)
        var rows: [[String: Any]] = []
        for line in lines {
            guard let data = String(line).data(using: .utf8),
                  let row = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                continue
            }
            rows.append(row)
        }

        var statusCounts: [String: Int] = [:]
        var pasteSuccess = 0
        var copyFallback = 0
        var blankOrNoSpeech = 0
        var latencies: [Double] = []
        var recent: [String] = []

        for row in rows {
            let status = row["status"] as? String ?? "unknown"
            statusCounts[status, default: 0] += 1
            if row["paste_success"] as? Bool == true {
                pasteSuccess += 1
            }
            if status == "copy_fallback" {
                copyFallback += 1
            }
            if row["blank_or_no_speech"] as? Bool == true {
                blankOrNoSpeech += 1
            }
            if let timings = row["timings"] as? [String: Any],
               let latency = timings["release_to_paste_seconds"] as? Double {
                latencies.append(latency)
            }
        }

        for row in rows.suffix(30) {
            let runID = row["run_id"] as? String ?? "unknown"
            let status = row["status"] as? String ?? "unknown"
            let route = row["route"] as? String ?? row["asr_engine"] as? String ?? "unknown"
            let errorType = row["error_type"] as? String ?? ""
            let latency = ((row["timings"] as? [String: Any])?["release_to_paste_seconds"] as? Double)
                .map { String(format: "%.3fs", $0) } ?? "n/a"
            recent.append("\(runID) status=\(status) route=\(route) latency=\(latency) error=\(errorType)")
        }

        let sortedStatus = statusCounts.keys.sorted()
            .map { "\($0): \(statusCounts[$0] ?? 0)" }
            .joined(separator: ", ")
        return [
            "history_rows_scanned: \(rows.count)",
            "status_counts: \(sortedStatus)",
            "paste_success_count: \(pasteSuccess)",
            "copy_fallback_count: \(copyFallback)",
            "blank_or_no_speech_count: \(blankOrNoSpeech)",
            "p50_release_to_paste_seconds: \(percentileString(latencies, 0.50))",
            "p95_release_to_paste_seconds: \(percentileString(latencies, 0.95))",
            "",
            "recent_runs:",
            recent.joined(separator: "\n")
        ].joined(separator: "\n") + "\n"
    }

    private func percentileString(_ values: [Double], _ percentile: Double) -> String {
        guard !values.isEmpty else { return "n/a" }
        let sorted = values.sorted()
        let index = min(sorted.count - 1, max(0, Int(ceil(Double(sorted.count) * percentile)) - 1))
        return String(format: "%.3f", sorted[index])
    }

    private func sanitizedJSONLinesTail(sourceURL: URL, limit: Int) -> String {
        guard let raw = try? String(contentsOf: sourceURL, encoding: .utf8) else { return "" }
        let lines = raw.split(separator: "\n").suffix(limit)
        return lines.compactMap { line -> String? in
            guard let data = String(line).data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: data) else {
                return nil
            }
            let sanitized = sanitizeDiagnosticsValue(object, key: "")
            guard JSONSerialization.isValidJSONObject(sanitized),
                  let out = try? JSONSerialization.data(withJSONObject: sanitized, options: [.sortedKeys]),
                  let text = String(data: out, encoding: .utf8) else {
                return nil
            }
            return text
        }.joined(separator: "\n") + "\n"
    }

    private func sanitizeDiagnosticsValue(_ value: Any, key: String) -> Any {
        let sensitiveKeys: Set<String> = [
            "raw_text",
            "corrected_text",
            "pasted_text",
            "selected_text",
            "clipboard",
            "audio_path",
            "history_path",
            "stream_chunk_dir",
            "project_root"
        ]
        if sensitiveKeys.contains(key) {
            return "<redacted>"
        }
        if let dictionary = value as? [String: Any] {
            var sanitized: [String: Any] = [:]
            for (childKey, childValue) in dictionary {
                sanitized[childKey] = sanitizeDiagnosticsValue(childValue, key: childKey)
            }
            return sanitized
        }
        if let array = value as? [Any] {
            return array.map { sanitizeDiagnosticsValue($0, key: key) }
        }
        if value is NSNull || value is String || value is NSNumber {
            return value
        }
        return String(describing: value)
    }

    private func appendNativeEvent(_ event: String, fields: [String: Any] = [:]) {
        var row = fields
        row["created_at"] = iso(Date())
        row["event"] = event
        guard let data = try? JSONSerialization.data(withJSONObject: row),
              let line = String(data: data, encoding: .utf8) else {
            return
        }
        let logURL = projectRoot.appendingPathComponent("logs/native_events.jsonl")
        try? FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if FileManager.default.fileExists(atPath: logURL.path),
           let handle = try? FileHandle(forWritingTo: logURL) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: Data((line + "\n").utf8))
        } else {
            try? (line + "\n").write(to: logURL, atomically: true, encoding: .utf8)
        }
    }

    private func appendFeedbackEvent(kind: String, text: String) {
        let latestEntry = loadHistoryEntries(limit: 1).first
        var row: [String: Any] = [
            "created_at": iso(Date()),
            "kind": kind,
            "source": "menu_latest",
            "text": text,
            "preview": transcriptPreview(text),
            "offline_mode": true,
            "app": appName
        ]
        if let latestEntry {
            row["history_created_at"] = latestEntry.createdAt
            row["history_status"] = latestEntry.status
            row["history_route"] = latestEntry.route
            if let latency = latestEntry.latencySeconds {
                row["history_latency_seconds"] = latency
            }
            row["target_app_name"] = latestEntry.targetName
        }
        guard let data = try? JSONSerialization.data(withJSONObject: row),
              let line = String(data: data, encoding: .utf8) else {
            return
        }
        let feedbackURL = projectRoot.appendingPathComponent("logs/feedback.jsonl")
        try? FileManager.default.createDirectory(at: feedbackURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if FileManager.default.fileExists(atPath: feedbackURL.path),
           let handle = try? FileHandle(forWritingTo: feedbackURL) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: Data((line + "\n").utf8))
        } else {
            try? (line + "\n").write(to: feedbackURL, atomically: true, encoding: .utf8)
        }
        appendNativeEvent("feedback_marked", fields: [
            "kind": kind,
            "feedback_path": feedbackURL.path,
            "preview": transcriptPreview(text)
        ])
    }

    private func appendHotkeyHistory(run: ActiveRun, payload: DictationPayload?, status: String, errorType: String, audioSavedAt: Date, asrStartedAt: Date, asrEndedAt: Date, pasteStartedAt: Date?, pasteEndedAt: Date?, retainedAudioURL: URL? = nil, blankOrNoSpeech: Bool = false, pasteSuccess: Bool? = nil) {
        guard envFlag("RAMBLEFIX_HOTKEY_HISTORY", defaultValue: true) else { return }
        let historyURL = projectRoot.appendingPathComponent("logs/history.jsonl")
        try? FileManager.default.createDirectory(at: historyURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        let retentionReason = hotkeyAudioRetentionReason(mode: run.mode)
        let retainAudio = retentionReason != nil
        let audioPath = retainedAudioURL?.path ?? (retainAudio ? run.audioURL.path : "")
        var timings: [String: Any] = [
            "hotkey_down_at": iso(run.startedAt),
            "audio_saved_at": iso(audioSavedAt),
            "asr_started_at": iso(asrStartedAt),
            "asr_ended_at": iso(asrEndedAt),
            "audio_duration_seconds": roundedSeconds(audioSavedAt.timeIntervalSince(run.startedAt)),
            "asr_wall_seconds": roundedSeconds(asrEndedAt.timeIntervalSince(asrStartedAt)),
            "release_to_asr_ready_seconds": roundedSeconds(asrEndedAt.timeIntervalSince(audioSavedAt))
        ]
        if let pasteStartedAt, let pasteEndedAt {
            timings["paste_started_at"] = iso(pasteStartedAt)
            timings["paste_ended_at"] = iso(pasteEndedAt)
            timings["paste_wall_seconds"] = roundedSeconds(pasteEndedAt.timeIntervalSince(pasteStartedAt))
            timings["release_to_paste_seconds"] = roundedSeconds(pasteEndedAt.timeIntervalSince(audioSavedAt))
            timings["release_to_first_output_seconds"] = roundedSeconds(pasteEndedAt.timeIntervalSince(audioSavedAt))
        }
        var row: [String: Any] = [
            "run_id": run.runID,
            "created_at": iso(Date()),
            "mode": run.mode.rawValue,
            "audio_path": audioPath,
            "raw_text": payload?.rawText ?? "",
            "corrected_text": payload?.text ?? "",
            "pasted_text": payload?.text ?? "",
            "asr_engine": payload?.engine ?? "",
            "processor": payload?.processor ?? "",
            "route": payload?.route ?? "",
            "fallback_reason": payload?.fallbackReason ?? "",
            "dictionary_version": "",
            "quality_flags": payload?.quality ?? [:],
            "offline_mode": true,
            "status": status,
            "error_type": errorType,
            "audio_retained": !audioPath.isEmpty,
            "audio_retention_reason": retainedAudioURL != nil ? "failure_debug" : (retentionReason ?? ""),
            "audio_retention_limit": hotkeyRetainedAudioLimit(),
            "blank_or_no_speech": blankOrNoSpeech || (payload.map { isNoSpeechPayload($0) } ?? false),
            "target_app": [
                "pid": run.targetPID as Any,
                "bundle_id": run.targetBundleID,
                "name": run.targetName
            ],
            "system_health": systemHealthSnapshot(),
            "timings": timings
        ]
        if let streamChunkDirectory = run.streamChunkDirectory {
            row["streaming_capture"] = true
            row["stream_chunk_dir"] = streamChunkDirectory.path
            row["stream_chunk_count"] = streamChunkCount(in: streamChunkDirectory)
        }
        if let pasteStartedAt, let pasteEndedAt {
            row["paste_started_at"] = iso(pasteStartedAt)
            row["paste_ended_at"] = iso(pasteEndedAt)
        }
        if let pasteSuccess = pasteSuccess {
            row["paste_success"] = pasteSuccess
        }
        if let seconds = payload?.seconds {
            row["asr_seconds"] = seconds
        }
        guard let data = try? JSONSerialization.data(withJSONObject: row),
              let line = String(data: data, encoding: .utf8) else {
            return
        }
        var historyWriteSucceeded = false
        if FileManager.default.fileExists(atPath: historyURL.path),
           let handle = try? FileHandle(forWritingTo: historyURL) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            do {
                try handle.write(contentsOf: Data((line + "\n").utf8))
                historyWriteSucceeded = true
            } catch {
                historyWriteSucceeded = false
            }
        } else {
            do {
                try (line + "\n").write(to: historyURL, atomically: true, encoding: .utf8)
                historyWriteSucceeded = true
            } catch {
                historyWriteSucceeded = false
            }
        }
        appendNativeEvent("history_append", fields: [
            "run_id": run.runID,
            "status": status,
            "history_path": historyURL.path,
            "audio_path": audioPath,
            "audio_retained": !audioPath.isEmpty,
            "write_succeeded": historyWriteSucceeded
        ])
        if retainAudio, run.mode == .dictation {
            pruneRetainedHotkeyAudioIfNeeded()
        }
    }

    private func roundedSeconds(_ seconds: TimeInterval) -> Double {
        return (seconds * 1000).rounded() / 1000
    }

    private func roundedMilliseconds(_ seconds: TimeInterval) -> Double {
        return (seconds * 10000).rounded() / 10
    }

    private func systemHealthSnapshot() -> [String: Any] {
        let sampleAge = latestSystemLoadSampleAt.map { roundedSeconds(Date().timeIntervalSince($0)) } ?? -1
        return [
            "pressure": systemPressureLabel(),
            "thermal_state": thermalStateName(latestThermalState),
            "load_ratio_1m_per_cpu": roundedSeconds(latestSystemLoadRatio),
            "cpu_count": ProcessInfo.processInfo.processorCount,
            "sample_age_seconds": sampleAge,
            "sample_cost_ms": latestSystemHealthSampleCostMs,
            "sampling_enabled": systemHealthSamplingEnabled
        ]
    }

    private func notify(_ title: String, _ body: String) {
        let notification = NSUserNotification()
        notification.title = title
        notification.informativeText = body
        NSUserNotificationCenter.default.deliver(notification)
    }
}

private func envFlag(_ name: String, defaultValue: Bool) -> Bool {
    guard let raw = ProcessInfo.processInfo.environment[name]?.lowercased() else {
        return defaultValue
    }
    return ["1", "true", "yes", "on"].contains(raw)
}

private func runtimeFlag(_ name: String, defaultValue: Bool) -> Bool {
    if let raw = ProcessInfo.processInfo.environment[name] {
        return parseBooleanFlag(raw, defaultValue: defaultValue)
    }
    if let value = UserDefaults.standard.object(forKey: name) {
        if let bool = value as? Bool {
            return bool
        }
        if let number = value as? NSNumber {
            return number.boolValue
        }
        if let raw = value as? String {
            return parseBooleanFlag(raw, defaultValue: defaultValue)
        }
    }
    return defaultValue
}

private func parseBooleanFlag(_ raw: String, defaultValue: Bool) -> Bool {
    let normalized = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    if ["1", "true", "yes", "on"].contains(normalized) {
        return true
    }
    if ["0", "false", "no", "off"].contains(normalized) {
        return false
    }
    return defaultValue
}

private func envDouble(_ name: String, defaultValue: Double, minValue: Double) -> Double {
    guard let raw = ProcessInfo.processInfo.environment[name],
          let value = Double(raw) else {
        return max(minValue, defaultValue)
    }
    return max(minValue, value)
}

private func envOptionalDouble(_ name: String, defaultValue: Double? = nil, minValue: Double) -> Double? {
    guard let raw = ProcessInfo.processInfo.environment[name],
          let value = Double(raw) else {
        return defaultValue.map { max(minValue, $0) }
    }
    return max(minValue, value)
}

private func bundledProjectRoot() -> URL? {
    guard let marker = Bundle.main.url(forResource: "ramblefix-root", withExtension: "txt"),
          let raw = try? String(contentsOf: marker, encoding: .utf8) else {
        return nil
    }
    let path = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !path.isEmpty else { return nil }
    let root = path.hasPrefix("/")
        ? URL(fileURLWithPath: path, isDirectory: true)
        : marker.deletingLastPathComponent().appendingPathComponent(path, isDirectory: true)
    return validatedProjectRoot(root)
}

private func validatedProjectRoot(_ url: URL) -> URL? {
    let manager = FileManager.default
    let root = url.standardizedFileURL
    let pyproject = root.appendingPathComponent("pyproject.toml").path
    let package = root.appendingPathComponent("native/RambleFixHotkey/Package.swift").path
    let runtimeCLI = root.appendingPathComponent("src/ramblefix/cli.py").path
    guard manager.fileExists(atPath: pyproject),
          manager.fileExists(atPath: package) || manager.fileExists(atPath: runtimeCLI) else {
        return nil
    }
    return root
}

private func findProjectRoot() -> URL? {
    var candidates: [URL] = [URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)]
    if let executable = Bundle.main.executableURL {
        candidates.append(executable.deletingLastPathComponent())
    }
    for candidate in candidates {
        var cursor = candidate.standardizedFileURL
        for _ in 0..<10 {
            if let root = validatedProjectRoot(cursor) {
                return root
            }
            let parent = cursor.deletingLastPathComponent()
            if parent.path == cursor.path { break }
            cursor = parent
        }
    }
    return nil
}

private func timestampRunID() -> String {
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyyMMdd-HHmmss"
    return "\(formatter.string(from: Date()))-\(UUID().uuidString.prefix(6))"
}

private func iso(_ date: Date) -> String {
    ISO8601DateFormatter().string(from: date)
}

private extension String {
    var fourCharCode: FourCharCode {
        var result: FourCharCode = 0
        for scalar in unicodeScalars.prefix(4) {
            result = (result << 8) + FourCharCode(scalar.value)
        }
        return result
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
