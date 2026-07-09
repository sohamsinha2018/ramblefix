// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "RambleFixHotkey",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "RambleFixHotkey", targets: ["RambleFixHotkey"]),
        .executable(name: "RambleFixHotkeyRegressionTests", targets: ["RambleFixHotkeyRegressionTests"]),
        .executable(name: "RambleFixHotkeyPolicyTool", targets: ["RambleFixHotkeyPolicyTool"]),
        .executable(name: "RambleFixHotkeyASRTool", targets: ["RambleFixHotkeyASRTool"]),
        .executable(name: "RambleFixSystemAudioSmokeTool", targets: ["RambleFixSystemAudioSmokeTool"])
    ],
    targets: [
        .target(name: "RambleFixHotkeyCore"),
        .executableTarget(
            name: "RambleFixHotkey",
            dependencies: ["RambleFixHotkeyCore"]
        ),
        .executableTarget(
            name: "RambleFixHotkeyRegressionTests",
            dependencies: ["RambleFixHotkeyCore"]
        ),
        .executableTarget(
            name: "RambleFixHotkeyPolicyTool",
            dependencies: ["RambleFixHotkeyCore"]
        ),
        .executableTarget(
            name: "RambleFixHotkeyASRTool",
            dependencies: ["RambleFixHotkeyCore"]
        ),
        .executableTarget(
            name: "RambleFixSystemAudioSmokeTool",
            dependencies: ["RambleFixHotkeyCore"]
        )
    ],
    swiftLanguageModes: [.v5]
)
