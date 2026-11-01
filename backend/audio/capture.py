from __future__ import annotations

import contextlib
import hashlib
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from backend.audio.resample import ResampleState, normalize_audio
from backend.i18n import t
from backend.runtime import configure_native_runtime

RecordingStateHandler = Callable[[str, str], None]
ErrorHandler = Callable[[str], None]

_FRAMES_PER_BUFFER = 1024
# ~1.4s of 48 kHz loopback audio; deep enough to ride out a Whisper burst.
_FRAME_QUEUE_SIZE = 64
# How long the processing thread waits for audio before rechecking stop flags.
_FRAME_POLL_SECONDS = 0.1
_VAD_BATCH_SAMPLES = 1600  # Run VAD at most ten times per second per device.
_END_SILENCE_SECONDS = 0.8
# Whisper's window is 30s; a forced cut lands mid-word, so keep it rare.
_MAX_SEGMENT_MS = 15_000


class CaptureConsumer(Protocol):
    """Receives one capture session's audio as 16 kHz mono PCM16."""

    def on_audio(
        self, transcript_source: str, session_id: str, pcm: bytes, speech: bool
    ) -> None: ...

    def on_segment(
        self, transcript_source: str, session_id: str, segment: bytes
    ) -> None: ...


class _UtteranceBuffer:
    """Keeps audio before the VAD trigger so initial phonemes are not discarded."""

    def __init__(
        self,
        *,
        sample_rate: int,
        pre_roll_ms: int = 500,
        end_silence_ms: int = 800,
        # Whisper's window is 30s; a forced cut lands mid-word, so keep it rare.
        max_utterance_ms: int = 15_000,
    ) -> None:
        self._pre_roll_limit = sample_rate * pre_roll_ms // 1000 * 2
        self._end_silence_samples = sample_rate * end_silence_ms // 1000
        self._max_utterance_samples = sample_rate * max_utterance_ms // 1000
        self._pre_roll = bytearray()
        self._frames: list[bytes] = []
        self._speaking = False
        self._silence_samples = 0
        self._utterance_samples = 0

    def feed(self, data: bytes, sample_count: int, speech_detected: bool) -> bytes | None:
        if not self._speaking:
            self._pre_roll.extend(data)
            if len(self._pre_roll) > self._pre_roll_limit:
                del self._pre_roll[: len(self._pre_roll) - self._pre_roll_limit]
            if not speech_detected:
                return None
            self._speaking = True
            self._frames = [bytes(self._pre_roll)]
            self._utterance_samples = len(self._pre_roll) // 2
            self._pre_roll.clear()
            self._silence_samples = 0
            return None

        self._frames.append(data)
        self._utterance_samples += sample_count
        if speech_detected:
            self._silence_samples = 0
            if self._utterance_samples >= self._max_utterance_samples:
                return self._finish_segment(continue_speaking=True)
            return None

        self._silence_samples += sample_count
        if self._silence_samples < self._end_silence_samples:
            return None

        return self._finish_segment(continue_speaking=False)

    def _finish_segment(self, *, continue_speaking: bool) -> bytes:
        segment = b"".join(self._frames)
        self._frames = []
        self._speaking = continue_speaking
        self._silence_samples = 0
        self._utterance_samples = 0
        return segment

    def flush(self) -> bytes | None:
        """Finish speech when a loopback device stops delivering buffers."""
        if not self._speaking:
            return None
        return self._finish_segment(continue_speaking=False)


class VoiceError(RuntimeError):
    pass


@dataclass
class _CaptureSession:
    """A capture device plus the queue its PortAudio callback feeds.

    The stream runs in callback mode, so no Python thread ever sits inside a
    blocking device read. That matters for stopping: a silent WASAPI loopback
    device hands out no audio at all, and a reader parked in `read()` could not
    be woken, which left `close()` to free the stream underneath it — a native
    use-after-free that killed the process with an access violation instead of
    raising. Now the processing thread only ever waits on `frames`, and
    `Pa_StopStream` is responsible for retiring the callback.
    """

    source: str
    transcript_source: str
    session_id: str
    stream: Any
    input_rate: int
    input_channels: int
    stop_event: threading.Event
    consumer: CaptureConsumer
    max_segment_ms: int
    thread: threading.Thread | None = None

    frames: queue.Queue[bytes] = field(
        default_factory=lambda: queue.Queue(maxsize=_FRAME_QUEUE_SIZE)
    )
    dropped_buffers: int = 0
    stream_lock: threading.Lock = field(default_factory=threading.Lock)
    stream_closed: bool = False

    def submit_buffer(self, data: bytes) -> None:
        """Runs on PortAudio's audio thread; must never block or do real work."""
        try:
            self.frames.put_nowait(data)
        except queue.Full:
            self.dropped_buffers += 1
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            with contextlib.suppress(queue.Full):
                self.frames.put_nowait(data)

    def next_buffer(self, timeout: float) -> bytes | None:
        """Waits briefly for captured audio; None means nothing arrived."""
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def close_stream(self) -> None:
        """Stops and closes the stream once; safe to call from any thread."""
        with self.stream_lock:
            if self.stream_closed:
                return
            self.stream_closed = True
            if self.stream is None:
                return
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                logging.exception("Failed to close %s audio stream", self.source)


class AudioCapture:
    """Owns the PortAudio manager, the VAD model and one session per source."""

    def __init__(
        self,
        *,
        on_recording_state: RecordingStateHandler,
        on_error: ErrorHandler,
    ) -> None:
        self._on_recording_state = on_recording_state
        self._on_error = on_error
        self._lock = threading.RLock()
        self._vad_lock = threading.Lock()
        self._closed = threading.Event()
        self._sessions: dict[str, _CaptureSession] = {}
        self._vad_model: Any = None
        self._get_speech_timestamps: Any = None
        self._audio: Any = None

    @property
    def vad_ready(self) -> bool:
        with self._lock:
            return self._vad_model is not None

    @property
    def recording(self) -> bool:
        with self._lock:
            return bool(self._sessions)

    def load_vad(self) -> None:
        """Loads Silero VAD once; raises when the runtime is unavailable."""
        with self._lock:
            if self._vad_model is not None:
                return
        configure_native_runtime()
        import numpy as np
        from faster_whisper.vad import get_speech_timestamps, get_vad_model

        vad_model = get_vad_model()
        vad_model(np.zeros(512, dtype=np.float32))
        with self._lock:
            self._vad_model = vad_model
            self._get_speech_timestamps = get_speech_timestamps

    def list_devices(self) -> list[dict[str, object]]:
        with self._lock:
            active_audio = self._audio if self._sessions else None
            self._release_idle_audio_manager()
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as exc:
            raise VoiceError(t("backend.voice.noPyAudio")) from exc
        audio = active_audio if active_audio is not None else pyaudio.PyAudio()
        devices: list[dict[str, object]] = []
        try:
            default_input_index: int | None = None
            default_speaker_index: int | None = None
            try:
                default_input_index = int(audio.get_default_input_device_info()["index"])
            except (IOError, KeyError, TypeError, ValueError):
                pass
            try:
                default_speaker_index = int(audio.get_default_wasapi_loopback()["index"])
            except (IOError, KeyError, TypeError, ValueError):
                pass
            for index in range(audio.get_device_count()):
                info = audio.get_device_info_by_index(index)
                is_loopback = bool(info.get("isLoopbackDevice", False))
                channels = int(info.get("maxInputChannels", 0))
                if channels <= 0:
                    continue
                host_api_index = int(info.get("hostApi", 0))
                host_api = audio.get_host_api_info_by_index(host_api_index)
                devices.append(
                    {
                        "id": self._device_id(info),
                        "name": str(info.get("name", f"Input {index}")),
                        "hostApi": str(host_api.get("name", "")),
                        "maxInputChannels": channels,
                        "isDefault": index
                        == (
                            default_speaker_index if is_loopback else default_input_index
                        ),
                        "source": "speaker" if is_loopback else "microphone",
                    }
                )
            if any(device["source"] == "microphone" for device in devices):
                devices.insert(
                    0,
                    {
                        "id": "default",
                        "name": t("backend.voice.defaultMicrophone"),
                        "hostApi": "",
                        "maxInputChannels": 1,
                        "isDefault": True,
                        "source": "microphone",
                    },
                )
            if any(device["source"] == "speaker" for device in devices):
                devices.insert(
                    0,
                    {
                        "id": "speaker:default",
                        "name": t("backend.voice.defaultSpeaker"),
                        "hostApi": "WASAPI",
                        "maxInputChannels": 2,
                        "isDefault": True,
                        "source": "speaker",
                    },
                )
        finally:
            if active_audio is None:
                audio.terminate()
        return devices

    def start(
        self,
        device_id: str,
        source: str,
        consumer: CaptureConsumer,
        *,
        max_segment_ms: int = _MAX_SEGMENT_MS,
    ) -> None:
        if source not in {"microphone", "speaker"}:
            raise VoiceError(t("backend.voice.unsupportedSource"))
        with self._lock:
            if source in self._sessions:
                return
            if self._closed.is_set():
                raise VoiceError(t("backend.voice.closed"))
            if self._vad_model is None:
                raise VoiceError(t("backend.voice.notReady"))
        self._on_recording_state("starting", source)
        try:
            audio = self._get_audio_manager()
            if source == "speaker":
                if device_id in {"default", "speaker:default"}:
                    device = audio.get_default_wasapi_loopback()
                else:
                    speaker_index = self._resolve_device_index(audio, device_id, source)
                    if speaker_index is None:
                        raise VoiceError(t("backend.voice.invalidSpeaker"))
                    device = audio.get_device_info_by_index(speaker_index)
                    if not bool(device.get("isLoopbackDevice", False)):
                        raise VoiceError(t("backend.voice.notLoopback"))
                input_device_index = int(device["index"])
                input_rate = int(device["defaultSampleRate"])
                input_channels = max(1, int(device["maxInputChannels"]))
            else:
                input_device_index = self._resolve_device_index(audio, device_id, source)
                input_rate = 16000
                input_channels = 1
            import pyaudiowpatch as pyaudio

            # The session (and its queue) must exist before the callback can
            # fire, so build it first and open the stream stopped.
            session = _CaptureSession(
                source=source,
                transcript_source="speaker" if source == "speaker" else "voice",
                session_id=str(uuid.uuid4()),
                stream=None,
                input_rate=input_rate,
                input_channels=input_channels,
                stop_event=threading.Event(),
                consumer=consumer,
                max_segment_ms=max_segment_ms,
            )

            def on_frames(
                in_data: bytes | None,
                _frame_count: int,
                _time_info: Any,
                _status: int,
            ) -> tuple[bytes | None, int]:
                if in_data:
                    session.submit_buffer(in_data)
                return (None, pyaudio.paContinue)

            session.stream = audio.open(
                format=pyaudio.paInt16,
                channels=input_channels,
                rate=input_rate,
                input=True,
                input_device_index=input_device_index,
                frames_per_buffer=_FRAMES_PER_BUFFER,
                stream_callback=on_frames,
                start=False,
            )
        except Exception as exc:
            self._on_recording_state("error", source)
            raise VoiceError(t("backend.voice.openFailed", error=exc)) from exc
        session.thread = threading.Thread(
            target=self._capture_loop,
            args=(session,),
            name=f"audio-capture-{source}",
            daemon=True,
        )
        with self._lock:
            if self._closed.is_set() or source in self._sessions:
                session.close_stream()
                raise VoiceError(t("backend.voice.sourceChanged"))
            self._sessions[source] = session
            session.thread.start()
            session.stream.start_stream()
        self._on_recording_state("listening", source)

    def stop(self, source: str | None = None) -> None:
        if source is not None and source not in {"microphone", "speaker"}:
            raise VoiceError(t("backend.voice.unsupportedSource"))
        with self._lock:
            sources = [source] if source else list(self._sessions)
            sessions = [
                self._sessions.pop(name) for name in sources if name in self._sessions
            ]
            for session in sessions:
                session.stop_event.set()
        if source is not None and not sessions:
            self._on_recording_state("idle", source)
            return
        for session in sessions:
            self._on_recording_state("stopping", session.source)
            self._close_capture(session)
            self._on_recording_state("idle", session.source)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self.stop()
        with self._lock:
            audio, self._audio = self._audio, None
            self._vad_model = None

        if audio is not None:
            audio.terminate()

    def is_active(self, transcript_source: str, session_id: str) -> bool:
        """Whether this capture session is still the one recording the source."""
        source = "speaker" if transcript_source == "speaker" else "microphone"
        with self._lock:
            session = self._sessions.get(source)
            return session is not None and session.session_id == session_id

    def _get_audio_manager(self) -> Any:
        with self._lock:
            self._release_idle_audio_manager()
            if self._audio is None:
                import pyaudiowpatch as pyaudio

                self._audio = pyaudio.PyAudio()
            return self._audio

    def _release_idle_audio_manager(self) -> None:
        if not self._sessions and self._audio is not None:
            audio, self._audio = self._audio, None
            audio.terminate()

    def _capture_loop(self, session: _CaptureSession) -> None:
        rate = 16000
        rate_state: ResampleState | None = None
        rolling: list[int] = []
        pending = bytearray()
        last_audio_at = time.monotonic()
        utterance = _UtteranceBuffer(
            sample_rate=rate, max_utterance_ms=session.max_segment_ms
        )
        try:
            import numpy as np

            while not session.stop_event.is_set() and not self._closed.is_set():
                audio_data = session.next_buffer(_FRAME_POLL_SECONDS)
                if audio_data is None:
                    if not pending:
                        if time.monotonic() - last_audio_at >= _END_SILENCE_SECONDS:
                            segment = utterance.flush()
                            if segment:
                                session.consumer.on_segment(
                                    session.transcript_source, session.session_id, segment
                                )
                            rolling.clear()
                        continue
                else:
                    last_audio_at = time.monotonic()
                    normalized, rate_state = self._normalize_audio(
                        audio_data,
                        session.input_channels,
                        session.input_rate,
                        rate_state,
                    )
                    pending.extend(normalized)
                    if len(pending) < _VAD_BATCH_SAMPLES * 2:
                        continue
                audio_data = bytes(pending)
                pending.clear()
                samples = np.frombuffer(audio_data, dtype=np.int16)
                if samples.size == 0:
                    continue
                rolling.extend(cast("list[int]", samples.tolist()))
                rolling = rolling[-rate // 2 :]
                timestamps = []
                if len(rolling) >= rate * 3 // 10:
                    tensor = np.asarray(rolling, dtype=np.float32) / 32768.0

                    with self._vad_lock:
                        timestamps = self._get_speech_timestamps(
                            tensor,
                            threshold=0.5,
                            min_speech_duration_ms=150,
                        )
                speech = bool(timestamps)
                session.consumer.on_audio(
                    session.transcript_source, session.session_id, audio_data, speech
                )
                segment = utterance.feed(audio_data, len(samples), speech)
                if segment:
                    session.consumer.on_segment(
                        session.transcript_source, session.session_id, segment
                    )
                rolling = rolling[-rate // 2 :]
        except Exception as exc:
            if not session.stop_event.is_set() and not self._closed.is_set():
                logging.exception("Audio capture failed for %s", session.source)
                with self._lock:
                    if self._sessions.get(session.source) is session:
                        session.close_stream()
                        self._sessions.pop(session.source, None)
                self._close_capture(session)
                self._on_error(
                    t(
                        "backend.voice.captureFailed",
                        source=self._source_label(session.source),
                        error=exc,
                    )
                )
                self._on_recording_state("error", session.source)
        finally:
            # The reading thread always closes its own stream, so a stop that
            # gave up waiting never frees it underneath this loop.
            session.close_stream()

    @staticmethod
    def _close_capture(session: _CaptureSession, *, join_timeout: float = 2.0) -> None:
        thread = session.thread
        if thread and thread is not threading.current_thread():
            # The processing thread only waits on its frame queue, so it retires
            # within one poll interval however quiet the device is.
            thread.join(timeout=join_timeout)
            if thread.is_alive():
                logging.warning(
                    "%s processing thread did not stop within %.1fs",
                    session.source,
                    join_timeout,
                )

        session.close_stream()
        if session.dropped_buffers:
            logging.warning(
                "%s capture dropped %d buffer(s) while processing lagged",
                session.source,
                session.dropped_buffers,
            )

    @staticmethod
    def _device_id(info: dict[str, Any]) -> str:
        identity = repr(
            (
                info.get("hostApi"),
                info.get("name"),
                bool(info.get("isLoopbackDevice", False)),
            )
        )
        return "device:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @classmethod
    def _resolve_device_index(cls, audio: Any, device_id: str, source: str) -> int | None:
        if device_id == "default" and source == "microphone":
            return None
        if not device_id.startswith("device:"):
            raise VoiceError(t("backend.voice.staleDevice"))
        matches = []
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            if (
                cls._device_id(info) == device_id
                and int(info.get("maxInputChannels", 0)) > 0
                and bool(info.get("isLoopbackDevice", False)) == (source == "speaker")
            ):
                matches.append(index)
        if len(matches) != 1:
            raise VoiceError(t("backend.voice.unknownDevice"))
        return matches[0]

    @staticmethod
    def _source_label(source: str) -> str:
        return t(
            "backend.voice.source.speaker"
            if source == "speaker"
            else "backend.voice.source.microphone"
        )

    @staticmethod
    def _normalize_audio(
        data: bytes,
        channels: int,
        input_rate: int,
        rate_state: ResampleState | None,
    ) -> tuple[bytes, ResampleState | None]:
        return normalize_audio(data, channels, input_rate, rate_state)
