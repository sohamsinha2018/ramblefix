from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from omnilingual_asr.models.inference.pipeline import ASRInferencePipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an isolated Omnilingual ASR CTC probe.")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model-card", default="omniASR_CTC_300M")
    args = parser.parse_args()

    audio = args.audio.expanduser().resolve()
    if not audio.exists():
        raise FileNotFoundError(audio)

    started = time.perf_counter()
    pipeline = ASRInferencePipeline(model_card=args.model_card)
    load_seconds = round(time.perf_counter() - started, 3)
    run_started = time.perf_counter()
    text = pipeline.transcribe([str(audio)], batch_size=1)[0]
    run_seconds = round(time.perf_counter() - run_started, 3)
    print(
        json.dumps(
            {
                "model_card": args.model_card,
                "load_seconds": load_seconds,
                "run_seconds": run_seconds,
                "text": str(text).strip(),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
