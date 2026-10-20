import struct
import unittest
from unittest.mock import patch

import numpy as np

from backend.audio import normalize_audio


def _stream(pcm: bytes, channels: int, rate: int, block_frames: int = 1024) -> bytes:
    state = None
    output = []
    step = block_frames * channels * 2
    for start in range(0, len(pcm), step):
        block, state = normalize_audio(pcm[start : start + step], channels, rate, state)
        output.append(block)
    return b"".join(output)


def _tone(frequency: float, rate: int, seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * frequency * t) * 12000).astype(np.int16)


class AudioConversionTests(unittest.TestCase):
    def test_stream_keeps_rate_without_frame_drift(self):
        for rate in (8000, 22050, 44100, 48000, 96000):
            with self.subTest(rate=rate):
                pcm = _tone(440, rate, seconds=2.0).tobytes()
                with patch(
                    "subprocess.Popen", side_effect=AssertionError("FFmpeg invoked")
                ):
                    output = _stream(pcm, 1, rate)
                # Only the resampler's filter delay may be missing.
                self.assertLessEqual(32000 - len(output) // 2, 64)
                self.assertLessEqual(len(output) // 2, 32000)

    def test_downsampling_filters_content_above_nyquist(self):
        # Without a low-pass, 14 kHz at 48 kHz folds onto 2 kHz at full level.
        for frequency in (10000, 14000, 20000):
            with self.subTest(frequency=frequency):
                tone = _tone(frequency, 48000)
                stereo = np.stack([tone, tone], axis=1).tobytes()
                output = np.frombuffer(_stream(stereo, 2, 48000), dtype=np.int16)
                self.assertLess(output.astype(np.float64).std(), tone.std() / 100)

    def test_downsampling_preserves_speech_band(self):
        tone = _tone(1000, 48000)
        output = np.frombuffer(_stream(tone.tobytes(), 1, 48000), dtype=np.int16)
        self.assertAlmostEqual(
            output[1000:].astype(np.float64).std() / tone.std(), 1.0, delta=0.02
        )

    def test_stereo_cancellation_and_six_channel_mix(self):
        for channels in (2, 6):
            with self.subTest(channels=channels):
                pcm = struct.pack(f"<{channels}h", *([1000, -1000] * (channels // 2)))
                converted, _ = normalize_audio(pcm, channels, 16000, None)
                self.assertEqual(struct.unpack("<h", converted)[0], 0)

    def test_sessions_do_not_share_resampler_state(self):
        _, state = normalize_audio(struct.pack("<h", 30000), 1, 44100, None)
        converted, fresh = normalize_audio(bytes(441 * 2), 1, 44100, None)
        self.assertEqual(converted, bytes(len(converted)))
        self.assertIsNot(state, fresh)
