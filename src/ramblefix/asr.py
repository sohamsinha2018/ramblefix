from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


FAST_MLX_MODEL = "mlx-community/whisper-tiny"
BALANCED_MLX_MODEL = "mlx-community/whisper-base-mlx"
ACCURATE_MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_MLX_MODEL = FAST_MLX_MODEL


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None
    segments: list[Segment]
    engine: str


def transcribe_audio(
    audio_path: str | Path,
    *,
    model: str = DEFAULT_MLX_MODEL,
    language: str | None = None,
    condition_on_previous_text: bool = False,
) -> Transcript:
    """Transcribe an audio file with local MLX Whisper.

    The model may download on first run. After that, transcription is local.
    """
    path = Path(audio_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    try:
        import mlx_whisper
    except ImportError as exc:
        raise RuntimeError(
            "mlx-whisper is not installed. Run `pip install -r requirements.txt`."
        ) from exc

    kwargs: dict[str, Any] = {
        "path_or_hf_repo": model,
        "condition_on_previous_text": condition_on_previous_text,
        "hallucination_silence_threshold": 2.0,
        "compression_ratio_threshold": 2.2,
        "no_speech_threshold": 0.6,
    }
    if language:
        kwargs["language"] = language

    result = mlx_whisper.transcribe(str(path), **kwargs)
    segments = [
        Segment(
            start=float(item.get("start", 0.0)),
            end=float(item.get("end", 0.0)),
            text=str(item.get("text", "")).strip(),
        )
        for item in result.get("segments", [])
        if str(item.get("text", "")).strip()
    ]

    text = str(result.get("text", "")).strip()
    if not text:
        text = " ".join(segment.text for segment in segments).strip()

    return Transcript(
        text=text,
        language=result.get("language"),
        segments=segments,
        engine=f"mlx-whisper:{model}",
    )
