from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import queue
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.inference import inference_lock
from backend.audio import ResampleState, normalize_audio


ModelStatusHandler = Callable[[str, str, str | None], None]
RecordingStateHandler = Callable[[str, str], None]
TranscriptHandler = Callable[[str, str], None]
ErrorHandler = Callable[[str], None]
Segment = tuple[str, str, bytes, float]
_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_FRAMES_PER_BUFFER = 1024
# ~1.4s of 48 kHz loopback audio; deep enough to ride out a Whisper burst.
_FRAME_QUEUE_SIZE = 64
# How long the processing thread waits for audio before rechecking stop flags.
_FRAME_POLL_SECONDS = 0.1
_VAD_BATCH_SAMPLES = 1600  # Run VAD at most ten times per second per device.
_END_SILENCE_SECONDS = 0.8
_MAX_SEGMENT_WAIT_SECONDS = 15.0


class _UtteranceBuffer:
    """Keeps audio before the VAD trigger so initial phonemes are not discarded."""

    def __init__(
        self,
        *,
        sample_rate: int,
        pre_roll_ms: int = 500,
        end_silence_ms: int = 800,
        max_utterance_ms: int = 10_000,
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
    thread: threading.Thread | None = None
    # Bounded so a slow processing thread cannot grow it without limit; the
    # oldest buffers are dropped first, matching the existing segment queue.
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
                # Blocks until the callback has retired, so the stream is never
                # freed while PortAudio is still calling into it.
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                logging.exception("Failed to close %s audio stream", self.source)


class VoiceService:
    """Owns shared speech models and independent microphone/speaker capture."""

    def __init__(
        self,
        *,
        model_name: str,
        language: str,
        save_audio: bool,
        save_dir: str,
        on_model_status: ModelStatusHandler,
        on_recording_state: RecordingStateHandler,
        on_transcript: TranscriptHandler,
        on_error: ErrorHandler,
        speaker_language: str | None = None,
    ) -> None:
        self._model_name = model_name
        self._languages: dict[str, str | None] = {
            "voice": self._normalize_language(language),
            "speaker": self._normalize_language(speaker_language or language),
        }
        self._save_audio = save_audio
        self._save_dir = Path(save_dir)
        self._on_model_status = on_model_status
        self._on_recording_state = on_recording_state
        self._on_transcript = on_transcript
        self._on_error = on_error
        self._lock = threading.RLock()
        self._vad_lock = threading.Lock()
        self._closed = threading.Event()
        self._segments: queue.Queue[Segment | None] = queue.Queue(maxsize=6)
        self._sessions: dict[str, _CaptureSession] = {}
        self._model: Any = None
        self._vad_model: Any = None
        self._get_speech_timestamps: Any = None
        self._audio: Any = None
        self._worker_thread: threading.Thread | None = None
        self._load_thread: threading.Thread | None = None
        self._ready = False

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready

    @property
    def recording(self) -> bool:
        with self._lock:
            return bool(self._sessions)

    def load_models(self) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            if self._ready or (self._load_thread and self._load_thread.is_alive()):
                return
        self._on_model_status("loading", None)
        try:
            # Keep imports and model construction on the protocol thread. Apart
            # from avoiding PyInstaller's frozen-import deadlock, this prevents
            # PyTorch's thread-local device mode from sporadically constructing
            # Whisper on the weightless meta device after rapid app restarts.
            import torch
            import whisper
            from silero_vad import get_speech_timestamps, load_silero_vad

            torch.set_default_device("cpu")
        except Exception as exc:
            logging.exception("Failed to load speech runtime")
            if not self._closed.is_set():
                self._on_model_status("failed", None)
                self._on_error(f"語音執行環境載入失敗：{exc}")
            return
        if self._closed.is_set():
            return
        self._load_models(torch, whisper, get_speech_timestamps, load_silero_vad)

    def set_languages(self, microphone_language: str, speaker_language: str) -> None:
        with self._lock:
            self._languages = {
                "voice": self._normalize_language(microphone_language),
                "speaker": self._normalize_language(speaker_language),
            }

    def list_devices(self) -> list[dict[str, object]]:
        with self._lock:
            active_audio = self._audio if self._sessions else None
            self._release_idle_audio_manager()
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as exc:
            raise VoiceError("找不到 PyAudioWPatch，無法列舉錄音裝置") from exc
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
                        "name": "系統預設麥克風",
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
                        "name": "系統預設喇叭",
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

    def start(self, device_id: str, source: str = "microphone") -> None:
        if source not in {"microphone", "speaker"}:
            raise VoiceError("不支援的音訊來源")
        with self._lock:
            if source in self._sessions:
                return
            if self._closed.is_set():
                raise VoiceError("語音服務已關閉")
            if not self._ready:
                raise VoiceError("語音模型尚未載入完成")
        self._on_recording_state("starting", source)
        try:
            audio = self._get_audio_manager()
            if source == "speaker":
                if device_id in {"default", "speaker:default"}:
                    device = audio.get_default_wasapi_loopback()
                else:
                    speaker_index = self._resolve_device_index(audio, device_id, source)
                    if speaker_index is None:
                        raise VoiceError("喇叭裝置 ID 無效")
                    device = audio.get_device_info_by_index(speaker_index)
                    if not bool(device.get("isLoopbackDevice", False)):
                        raise VoiceError("選取的裝置不是可用的喇叭回放裝置")
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
            raise VoiceError(f"無法開啟錄音裝置：{exc}") from exc
        session.thread = threading.Thread(
            target=self._capture_loop,
            args=(session,),
            name=f"audio-capture-{source}",
            daemon=True,
        )
        with self._lock:
            if self._closed.is_set() or source in self._sessions:
                session.close_stream()
                raise VoiceError("音訊來源狀態已變更")
            self._sessions[source] = session
            session.thread.start()
            session.stream.start_stream()
        self._on_recording_state("listening", source)

    def stop(self, source: str | None = None) -> None:
        if source is not None and source not in {"microphone", "speaker"}:
            raise VoiceError("不支援的音訊來源")
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
        self._offer_segment(None)
        worker = self._worker_thread
        if worker and worker is not threading.current_thread():
            worker.join(timeout=5.0)
        with self._lock:
            audio, self._audio = self._audio, None
            self._ready = False
            self._model = None
            self._vad_model = None
        # `terminate()` frees every stream PortAudio owns, so it is only safe
        # once `stop()` above has closed them all.
        if audio is not None:
            audio.terminate()

    def _get_audio_manager(self) -> Any:
        with self._lock:
            self._release_idle_audio_manager()
            if self._audio is None:
                import pyaudiowpatch as pyaudio

                self._audio = pyaudio.PyAudio()
            return self._audio

    def _release_idle_audio_manager(self) -> None:
        # PortAudio caches the device list until its last manager terminates.
        # Never terminate the manager while the other source is capturing.
        if not self._sessions and self._audio is not None:
            audio, self._audio = self._audio, None
            audio.terminate()

    def _load_models(
        self,
        torch: Any,
        whisper: Any,
        get_speech_timestamps: Any,
        load_silero_vad: Any,
    ) -> None:
        try:
            # Silero sets the process-wide Torch thread count to one on import.
            # Give CPU Whisper a bounded budget, leaving room for VRChat.
            torch.set_num_threads(min(4, max(1, (os.cpu_count() or 2) // 2)))
            # Pin implicit module allocations to real CPU storage.
            torch.set_default_device("cpu")
            with contextlib.redirect_stdout(sys.stderr):
                device = "cuda" if torch.cuda.is_available() else "cpu"
                model_name = self._model_name
                if model_name == "auto":
                    model_name = "large-v3-turbo" if device == "cuda" else "base"
                model = whisper.load_model(model_name, device=device)
                vad_model = load_silero_vad()
            if self._closed.is_set():
                return
            device = str(next(model.parameters()).device)
            logging.info(
                "Speech model=%s device=%s CPU threads=%d",
                model_name,
                device,
                torch.get_num_threads(),
            )
            with self._lock:
                self._model = model
                self._vad_model = vad_model
                self._get_speech_timestamps = get_speech_timestamps
                self._ready = True
                self._worker_thread = threading.Thread(
                    target=self._transcribe_loop,
                    name="speech-transcriber",
                    daemon=True,
                )
                self._worker_thread.start()
            self._on_model_status("ready", device)
        except Exception as exc:
            logging.exception("Failed to load speech models")
            if not self._closed.is_set():
                self._on_model_status("failed", None)
                self._on_error(f"語音模型載入失敗：{exc}")

    def _capture_loop(self, session: _CaptureSession) -> None:
        rate = 16000
        rate_state: ResampleState | None = None
        rolling: list[int] = []
        pending = bytearray()
        last_audio_at = time.monotonic()
        utterance = _UtteranceBuffer(sample_rate=rate)
        try:
            import numpy as np
            import torch

            while not session.stop_event.is_set() and not self._closed.is_set():
                audio_data = session.next_buffer(_FRAME_POLL_SECONDS)
                if audio_data is None:
                    if not pending:
                        if time.monotonic() - last_audio_at >= _END_SILENCE_SECONDS:
                            segment = utterance.flush()
                            if segment:
                                self._offer_segment(
                                    (
                                        session.transcript_source,
                                        session.session_id,
                                        segment,
                                        time.monotonic(),
                                    )
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
                rolling.extend(samples.tolist())
                rolling = rolling[-rate // 2 :]
                timestamps = []
                if len(rolling) >= rate * 3 // 10:
                    tensor = torch.tensor(rolling, dtype=torch.float32) / 32768.0
                    with self._vad_lock:
                        timestamps = self._get_speech_timestamps(
                            tensor,
                            self._vad_model,
                            threshold=0.6,
                            return_seconds=False,
                            min_speech_duration_ms=300,
                            min_silence_duration_ms=800,
                        )
                segment = utterance.feed(audio_data, len(samples), bool(timestamps))
                if segment:
                    self._offer_segment(
                        (
                            session.transcript_source,
                            session.session_id,
                            segment,
                            time.monotonic(),
                        )
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
                self._on_error(f"{self._source_label(session.source)}擷取失敗：{exc}")
                self._on_recording_state("error", session.source)
        finally:
            # The reading thread always closes its own stream, so a stop that
            # gave up waiting never frees it underneath this loop.
            session.close_stream()

    def _transcribe_loop(self) -> None:
        import numpy as np

        while not self._closed.is_set() or not self._segments.empty():
            try:
                item = self._segments.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if item is None:
                    return
                transcript_source, session_id, segment, queued_at = item
                if not self._is_session_active(transcript_source, session_id):
                    continue
                audio = (
                    np.frombuffer(segment, dtype=np.int16).astype(np.float32) / 32768.0
                )
                with self._lock:
                    language = self._languages[
                        "speaker" if transcript_source == "speaker" else "voice"
                    ]
                # Only `text` is read below, so word timestamps are skipped: they
                # buy nothing here and force Whisper onto the numba DTW fallback
                # whenever Triton cannot find a CUDA toolkit.
                with inference_lock():
                    wait_seconds = time.monotonic() - queued_at
                    stale = wait_seconds > _MAX_SEGMENT_WAIT_SECONDS
                    if not stale:
                        started_at = time.monotonic()
                        result = self._model.transcribe(
                            audio,
                            language=language,
                            task="transcribe",
                            temperature=0.0,
                            fp16=str(next(self._model.parameters()).device).startswith(
                                "cuda"
                            ),
                        )
                if stale:
                    logging.warning(
                        "Dropped stale %s speech after %.1fs waiting",
                        transcript_source,
                        wait_seconds,
                    )
                    self._on_error(
                        "辨識速度跟不上，已略過過期音訊；請改用自動或較小的語音模型"
                    )
                    continue
                text = str(result.get("text", "")).strip()
                if self._needs_english_retry(transcript_source, language, text):
                    with inference_lock():
                        result = self._model.transcribe(
                            audio,
                            language=None,
                            task="translate",
                            temperature=0.0,
                            fp16=str(next(self._model.parameters()).device).startswith(
                                "cuda"
                            ),
                        )
                    text = str(result.get("text", "")).strip()
                logging.info(
                    "Speech source=%s audio=%.2fs queue=%.2fs inference=%.2fs",
                    transcript_source,
                    len(audio) / 16000,
                    wait_seconds,
                    time.monotonic() - started_at,
                )
                with self._lock:
                    # Stop must retire this session before a later start can
                    # admit callbacks; checking and publishing are one operation.
                    if text and self._is_session_active(transcript_source, session_id):
                        self._on_transcript(text, transcript_source)
            except Exception as exc:
                logging.exception("Transcription failed")
                if not self._closed.is_set():
                    self._on_error(f"語音辨識失敗：{exc}")
            finally:
                self._segments.task_done()

    def _is_session_active(self, transcript_source: str, session_id: str) -> bool:
        source = "speaker" if transcript_source == "speaker" else "microphone"
        with self._lock:
            session = self._sessions.get(source)
            return session is not None and session.session_id == session_id

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
        # Safe regardless: the thread never touches the stream, and PortAudio
        # retires the callback before the handle is freed.
        session.close_stream()
        if session.dropped_buffers:
            logging.warning(
                "%s capture dropped %d buffer(s) while processing lagged",
                session.source,
                session.dropped_buffers,
            )

    def _offer_segment(self, segment: Segment | None) -> None:
        try:
            self._segments.put_nowait(segment)
        except queue.Full:
            try:
                self._segments.get_nowait()
                self._segments.task_done()
            except queue.Empty:
                pass
            self._segments.put_nowait(segment)

    @staticmethod
    def _device_id(info: dict[str, Any]) -> str:
        # Numeric PortAudio indices can be reassigned after unplug/replug.
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
            raise VoiceError("裝置識別碼已過期，請重新整理並選擇裝置")
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
            raise VoiceError("所選裝置不存在或無法唯一識別，請重新整理並選擇裝置")
        return matches[0]

    @staticmethod
    def _source_label(source: str) -> str:
        return "喇叭" if source == "speaker" else "麥克風"

    @staticmethod
    def _normalize_language(language: str) -> str | None:
        normalized = language.strip()
        return None if normalized == "auto" else normalized

    @staticmethod
    def _needs_english_retry(
        transcript_source: str, language: str | None, text: str
    ) -> bool:
        return (
            transcript_source == "speaker"
            and language == "en"
            and bool(_CJK_PATTERN.search(text))
        )

    @staticmethod
    def _normalize_audio(
        data: bytes,
        channels: int,
        input_rate: int,
        rate_state: ResampleState | None,
    ) -> tuple[bytes, ResampleState | None]:
        return normalize_audio(data, channels, input_rate, rate_state)
