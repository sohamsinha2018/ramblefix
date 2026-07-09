from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "native/RambleFixHotkey/Sources/RambleFixHotkey/main.swift"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    source = MAIN.read_text(encoding="utf-8")
    stop_start = source.index("    func stop() {")
    stop_end = source.index("    private func consume", stop_start)
    stop_body = source[stop_start:stop_end]

    remove_tap = stop_body.index("engine.inputNode.removeTap")
    drain_queue = stop_body.index("fileWriteQueue.sync")
    engine_stop = stop_body.index("engine.stop()")
    accepting_false = stop_body.index("acceptingBuffers = false")

    expect(remove_tap < drain_queue < engine_stop, "streaming recorder must drain file writes before stopping AVAudioEngine")
    expect(drain_queue < accepting_false < engine_stop, "streaming recorder must stop accepting buffers before engine.stop")
    expect("guard acceptingBuffers else { return }" in source, "streaming writer must ignore buffers after stop begins")

    print("regression_streaming_recorder_lifecycle passed")


if __name__ == "__main__":
    main()
