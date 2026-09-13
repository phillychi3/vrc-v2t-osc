import unittest
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from backend.voice import VoiceService, _CaptureSession, _UtteranceBuffer


class VoiceAudioConversionTests(unittest.TestCase):
    def test_lists_microphone_and_speaker_loopback_devices(self) -> None:
        class FakeAudio:
            def get_default_input_device_info(self) -> dict[str, object]:
                return {"index": 1}

            def get_default_wasapi_loopback(self) -> dict[str, object]:
                return {"index": 2}

            def get_device_count(self) -> int:
                return 3

            def get_device_info_by_index(self, index: int) -> dict[str, object]:
                return [
                    {"index": 0, "name": "Output", "maxInputChannels": 0},
                    {
                        "index": 1,
                        "name": "Microphone",
                        "maxInputChannels": 1,
                        "hostApi": 0,
                    },
                    {
                        "index": 2,
                        "name": "Speakers (loopback)",
                        "maxInputChannels": 2,
                        "hostApi": 0,
                        "isLoopbackDevice": True,
                    },
                ][index]

            def get_host_api_info_by_index(self, _index: int) -> dict[str, object]:
                return {"name": "Windows WASAPI"}

            def terminate(self) -> None:
                pass

        fake_module = SimpleNamespace(PyAudio=FakeAudio)
        service = VoiceService(
            model_name="test",
            language="zh",
            save_audio=False,
            save_dir=".",
            on_model_status=lambda *_args: None,
            on_recording_state=lambda *_args: None,
            on_transcript=lambda *_args: None,
            on_error=lambda *_args: None,
        )
        with patch.dict("sys.modules", {"pyaudiowpatch": fake_module}):
            devices = service.list_devices()

        microphones = [item for item in devices if item["source"] == "microphone"]
        speakers = [item for item in devices if item["source"] == "speaker"]
        self.assertEqual(microphones[0]["id"], "default")
        self.assertEqual(microphones[1]["id"], "index:1")
        self.assertEqual(speakers[0]["id"], "speaker:default")
        self.assertEqual(speakers[1]["id"], "index:2")

    def test_uses_independent_microphone_and_speaker_languages(self) -> None:
        service = VoiceService(
            model_name="test",
            language="zh",
            speaker_language="en",
            save_audio=False,
            save_dir=".",
            on_model_status=lambda *_args: None,
            on_recording_state=lambda *_args: None,
            on_transcript=lambda *_args: None,
            on_error=lambda *_args: None,
        )

        self.assertEqual(service._languages, {"voice": "zh", "speaker": "en"})
        service.set_languages("ja", "auto")
        self.assertEqual(service._languages, {"voice": "ja", "speaker": None})

    def test_speaker_english_retries_cjk_output_as_translation(self) -> None:
        self.assertTrue(VoiceService._needs_english_retry("speaker", "en", "因為我們"))
        self.assertFalse(VoiceService._needs_english_retry("speaker", "en", "because we"))
        self.assertFalse(VoiceService._needs_english_retry("voice", "en", "因為我們"))
        self.assertFalse(VoiceService._needs_english_retry("speaker", "ja", "因為我們"))

    def test_vad_segment_includes_audio_before_trigger(self) -> None:
        buffer = _UtteranceBuffer(
            sample_rate=10,
            pre_roll_ms=500,
            end_silence_ms=200,
        )

        self.assertIsNone(buffer.feed(b"aa", 1, False))
        self.assertIsNone(buffer.feed(b"bb", 1, False))
        self.assertIsNone(buffer.feed(b"cc", 1, True))
        self.assertIsNone(buffer.feed(b"dd", 1, False))
        segment = buffer.feed(b"ee", 1, False)

        self.assertEqual(segment, b"aabbccddee")

    def test_continuous_speech_is_split_at_maximum_duration(self) -> None:
        buffer = _UtteranceBuffer(
            sample_rate=10,
            pre_roll_ms=100,
            end_silence_ms=200,
            max_utterance_ms=300,
        )

        self.assertIsNone(buffer.feed(b"aa", 1, True))
        self.assertIsNone(buffer.feed(b"bb", 1, True))
        first = buffer.feed(b"cc", 1, True)
        self.assertEqual(first, b"aabbcc")

        self.assertIsNone(buffer.feed(b"dd", 1, True))
        self.assertIsNone(buffer.feed(b"ee", 1, False))
        second = buffer.feed(b"ff", 1, False)
        self.assertEqual(second, b"ddeeff")

    def test_microphone_and_speaker_sessions_can_run_together(self) -> None:
        states: list[tuple[str, str]] = []

        class FakeStream:
            started = False

            def start_stream(self) -> None:
                self.started = True

            def stop_stream(self) -> None:
                pass

            def close(self) -> None:
                pass

        class FakeAudio:
            def get_default_wasapi_loopback(self) -> dict[str, object]:
                return {
                    "index": 9,
                    "defaultSampleRate": 48000,
                    "maxInputChannels": 2,
                }

            def get_device_info_by_index(self, index: int) -> dict[str, object]:
                return {
                    "index": index,
                    "defaultSampleRate": 44100,
                    "maxInputChannels": 2,
                    "isLoopbackDevice": True,
                }

            def open(self, **kwargs: object) -> FakeStream:
                self.opened = kwargs
                self.stream = FakeStream()
                return self.stream

            def terminate(self) -> None:
                pass

        class TestVoiceService(VoiceService):
            def _get_audio_manager(self) -> FakeAudio:
                return fake_audio

            def _capture_loop(self, session: _CaptureSession) -> None:
                session.stop_event.wait()

        fake_audio = FakeAudio()
        service = TestVoiceService(
            model_name="test",
            language="zh",
            save_audio=False,
            save_dir=".",
            on_model_status=lambda *_args: None,
            on_recording_state=lambda state, source: states.append((state, source)),
            on_transcript=lambda *_args: None,
            on_error=lambda *_args: None,
        )
        service._ready = True

        service.start("default", "microphone")
        service.start("index:12", "speaker")

        self.assertEqual(set(service._sessions), {"microphone", "speaker"})
        self.assertEqual(fake_audio.opened["input_device_index"], 12)
        self.assertEqual(fake_audio.opened["rate"], 44100)
        # Callback mode, opened stopped and started only once the session and
        # its processing thread are registered.
        self.assertIs(fake_audio.opened["start"], False)
        self.assertTrue(callable(fake_audio.opened["stream_callback"]))
        self.assertTrue(fake_audio.stream.started)
        self.assertTrue(service.recording)
        service.stop("microphone")
        self.assertEqual(set(service._sessions), {"speaker"})
        self.assertTrue(service.recording)
        service.stop("speaker")
        self.assertFalse(service.recording)
        self.assertIn(("listening", "microphone"), states)
        self.assertIn(("listening", "speaker"), states)

    def test_downmixes_stereo_to_mono(self) -> None:
        stereo = np.array([1000, 1000, -1000, -1000], dtype=np.int16).tobytes()

        converted, state = VoiceService._normalize_audio(stereo, 2, 16000, None)

        self.assertIsNone(state)
        self.assertEqual(np.frombuffer(converted, dtype=np.int16).tolist(), [1000, -1000])

    def test_resamples_loopback_audio_to_16_khz(self) -> None:
        stereo = np.zeros((480, 2), dtype=np.int16).tobytes()

        converted, state = VoiceService._normalize_audio(stereo, 2, 48000, None)

        self.assertIsNotNone(state)
        self.assertGreaterEqual(len(converted) // 2, 159)
        self.assertLessEqual(len(converted) // 2, 160)

    def test_capture_thread_finishes_before_stream_is_closed(self) -> None:
        operations: list[str] = []

        class FakeStream:
            def stop_stream(self) -> None:
                operations.append("stop-stream")

            def close(self) -> None:
                operations.append("close-stream")

        def finish_capture() -> None:
            time.sleep(0.02)
            operations.append("capture-finished")

        capture = threading.Thread(target=finish_capture)
        session = _CaptureSession(
            source="microphone",
            transcript_source="voice",
            session_id="test",
            stream=FakeStream(),
            input_rate=16000,
            input_channels=1,
            stop_event=threading.Event(),
            thread=capture,
        )
        capture.start()

        VoiceService._close_capture(session)

        self.assertLess(
            operations.index("capture-finished"), operations.index("close-stream")
        )

    def test_silent_device_still_stops_and_closes_the_stream(self) -> None:
        """A silent WASAPI loopback delivers no audio at all. The processing
        thread must still retire on its own and the stream must be closed,
        which is what the old blocking `read()` could not guarantee."""
        operations: list[str] = []

        class FakeStream:
            def stop_stream(self) -> None:
                operations.append("stop-stream")

            def close(self) -> None:
                operations.append("close-stream")

        service = VoiceService.__new__(VoiceService)
        service._closed = threading.Event()
        session = _CaptureSession(
            source="speaker",
            transcript_source="speaker",
            session_id="test",
            stream=FakeStream(),
            input_rate=48000,
            input_channels=2,
            stop_event=threading.Event(),
        )
        # No buffers are ever submitted: the device is silent. `torch` is stubbed
        # because the loop imports it up front but only uses it per buffer.
        capture = threading.Thread(
            target=service._capture_loop, args=(session,), daemon=True
        )
        session.thread = capture
        with patch.dict("sys.modules", {"torch": SimpleNamespace()}):
            capture.start()
            session.stop_event.set()
            VoiceService._close_capture(session)

        self.assertFalse(capture.is_alive())
        self.assertTrue(session.stream_closed)
        self.assertEqual(operations, ["stop-stream", "close-stream"])

    def test_callback_buffers_reach_the_processing_thread(self) -> None:
        received: list[bytes] = []
        session = _CaptureSession(
            source="microphone",
            transcript_source="voice",
            session_id="test",
            stream=None,
            input_rate=16000,
            input_channels=1,
            stop_event=threading.Event(),
        )

        session.submit_buffer(b"\x01\x00")
        session.submit_buffer(b"\x02\x00")
        while (buffer := session.next_buffer(0.05)) is not None:
            received.append(buffer)

        self.assertEqual(received, [b"\x01\x00", b"\x02\x00"])
        self.assertEqual(session.dropped_buffers, 0)

    def test_callback_drops_oldest_buffers_instead_of_blocking(self) -> None:
        """The callback runs on PortAudio's audio thread, so a lagging consumer
        must cost old audio rather than stall the device."""
        session = _CaptureSession(
            source="speaker",
            transcript_source="speaker",
            session_id="test",
            stream=None,
            input_rate=48000,
            input_channels=2,
            stop_event=threading.Event(),
        )
        capacity = session.frames.maxsize

        for index in range(capacity + 3):
            session.submit_buffer(index.to_bytes(2, "little"))

        self.assertEqual(session.dropped_buffers, 3)
        self.assertEqual(session.frames.qsize(), capacity)
        # The three oldest buffers were evicted, the newest survived.
        self.assertEqual(session.next_buffer(0.05), (3).to_bytes(2, "little"))

    def test_closing_the_stream_twice_is_harmless(self) -> None:
        operations: list[str] = []

        class FakeStream:
            def stop_stream(self) -> None:
                operations.append("stop-stream")

            def close(self) -> None:
                operations.append("close-stream")

        session = _CaptureSession(
            source="microphone",
            transcript_source="voice",
            session_id="test",
            stream=FakeStream(),
            input_rate=16000,
            input_channels=1,
            stop_event=threading.Event(),
        )

        session.close_stream()
        session.close_stream()

        self.assertEqual(operations, ["stop-stream", "close-stream"])


if __name__ == "__main__":
    unittest.main()
