import math
import struct
import unittest
from unittest.mock import patch

from pydub import AudioSegment

from backend.audio import normalize_audio


class AudioConversionTests(unittest.TestCase):
    def test_stream_matches_whole_signal_without_frame_drift(self):
        for rate in (8000, 16000, 22050, 44100, 48000, 96000):
            with self.subTest(rate=rate):
                samples = [int(12000 * math.sin(i * 0.13)) for i in range(rate)]
                pcm = struct.pack(f"<{len(samples)}h", *samples)
                expected = AudioSegment(
                    pcm, sample_width=2, frame_rate=rate, channels=1
                ).set_frame_rate(16000).raw_data
                state = None
                output = []
                # Exercise boundaries that do not align to resampling periods.
                with patch("subprocess.Popen", side_effect=AssertionError("FFmpeg invoked")):
                    for start in range(0, len(pcm), 1024 * 2):
                        block, state = normalize_audio(pcm[start:start + 2048], 1, rate, state)
                        output.append(block)
                self.assertEqual(b"".join(output), expected)
                if state:
                    self.assertLess(len(state.pending) + len(state.history), 1764)

    def test_stereo_cancellation_and_six_channel_mix(self):
        for channels in (2, 6):
            with self.subTest(channels=channels):
                pcm = struct.pack(f"<{channels}h", *([1000, -1000] * (channels // 2)))
                converted, _ = normalize_audio(pcm, channels, 16000, None)
                # pydub rounds each channel contribution for multichannel PCM.
                self.assertLessEqual(abs(struct.unpack("<h", converted)[0]), channels // 2)

    def test_sessions_do_not_share_pending_audio(self):
        _, state = normalize_audio(struct.pack("<h", 30000), 1, 44100, None)
        converted, fresh = normalize_audio(bytes(441 * 2), 1, 44100, None)
        self.assertEqual(converted, bytes(160 * 2))
        self.assertIsNot(state, fresh)
