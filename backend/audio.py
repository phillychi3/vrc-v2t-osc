"""Bounded, phase-continuous PCM conversion using pydub (no FFmpeg needed)."""

from dataclasses import dataclass
from math import gcd


@dataclass
class ResampleState:
    pending: bytes = b""
    history: bytes = b""
    emitted: int = 0


def normalize_audio(
    data: bytes, channels: int, input_rate: int, state: ResampleState | None
) -> tuple[bytes, ResampleState | None]:
    from pydub import AudioSegment

    if channels < 1 or input_rate < 1:
        raise ValueError("channels and input_rate must be positive")
    segment = AudioSegment(
        data=data, sample_width=2, frame_rate=input_rate, channels=channels
    ).set_channels(1)
    if input_rate == 16000:
        return segment.raw_data, None

    state = state or ResampleState()
    # pydub's set_frame_rate is stateless. Keep one complete rational-rate
    # period as overlap so every conversion starts at the same sample phase.
    # At 44.1 kHz this buffers <10 ms; at 48 kHz it buffers <3 input frames.
    period = input_rate // gcd(input_rate, 16000)
    pending = state.pending + segment.raw_data
    usable = len(pending) // (period * 2) * (period * 2)
    if not usable:
        state.pending = pending
        return b"", state
    combined = state.history + pending[:usable]
    converted = AudioSegment(
        data=combined, sample_width=2, frame_rate=input_rate, channels=1
    ).set_frame_rate(16000).raw_data
    output = converted[state.emitted * 2:]
    discarded_frames = len(combined) // 2 - period
    state.emitted = len(converted) // 2 - discarded_frames * 16000 // input_rate
    state.history = combined[-period * 2:]
    state.pending = pending[usable:]
    return output, state
