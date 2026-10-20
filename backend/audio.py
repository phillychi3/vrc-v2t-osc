from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

TARGET_RATE = 16000


@dataclass
class ResampleState:
    """Streaming resampler for one capture session.

    swresample low-pass filters before decimating and keeps its filter history
    between calls, so chunk boundaries neither click nor drift. Linear
    interpolation (pydub/audioop) skipped that filter, folding game audio and
    sibilants above 8 kHz back into the speech band.
    """

    resampler: Any
    input_rate: int
    pts: int = 0


def _downmix(data: bytes, channels: int) -> np.ndarray:
    samples = np.frombuffer(data, dtype=np.int16)
    if channels == 1:
        return samples
    frames = samples[: len(samples) // channels * channels].reshape(-1, channels)
    return np.round(frames.mean(axis=1, dtype=np.float32)).astype(np.int16)


def normalize_audio(
    data: bytes, channels: int, input_rate: int, state: ResampleState | None
) -> tuple[bytes, ResampleState | None]:
    """Converts interleaved 16-bit PCM to 16 kHz mono 16-bit PCM."""
    if channels < 1 or input_rate < 1:
        raise ValueError("channels and input_rate must be positive")
    mono = _downmix(data, channels)
    if input_rate == TARGET_RATE:
        return mono.tobytes(), None

    import av

    if state is None or state.input_rate != input_rate:
        state = ResampleState(
            resampler=av.AudioResampler(format="s16", layout="mono", rate=TARGET_RATE),
            input_rate=input_rate,
        )
    if not mono.size:
        return b"", state
    frame = av.AudioFrame.from_ndarray(mono.reshape(1, -1), format="s16", layout="mono")
    frame.sample_rate = input_rate
    frame.pts = state.pts
    state.pts += mono.size
    output = b"".join(
        bytes(converted.planes[0])[: converted.samples * 2]
        for converted in state.resampler.resample(frame)
    )
    return output, state
