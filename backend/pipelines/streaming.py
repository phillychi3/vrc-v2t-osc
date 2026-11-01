from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from backend.contracts import AudioSource
from backend.core.settings import SettingsService, local_language
from backend.core.state import BackendState, transcript_source
from backend.pipelines.base import CapturePipeline, ErrorHandler, ModelStatusHandler


StreamUpdateHandler = Callable[..., None]


_LEAD_FRAMES = 5
_TAIL_FRAMES = 8


class _SpeechGate:
    """Lets audio through only while someone talks, with a lead-in and a tail.

    A live session is billed for the audio it receives, and silence between
    sentences would otherwise stream for as long as the source records.
    """

    def __init__(self) -> None:
        self._lead: deque[bytes] = deque(maxlen=_LEAD_FRAMES)
        self._open = False
        self._tail = 0

    def feed(self, pcm: bytes, speech: bool) -> tuple[list[bytes], bool]:
        """(frames to send, whether speech just ended)."""
        if speech:
            frames = [] if self._open else list(self._lead)
            self._lead.clear()
            self._open = True
            self._tail = 0
            return [*frames, pcm], False
        if not self._open:
            self._lead.append(pcm)
            return [], False
        self._tail += 1
        if self._tail < _TAIL_FRAMES:
            return [pcm], False
        self._open = False
        self._tail = 0
        return [pcm], True


class StreamingPipeline(CapturePipeline):
    """Each recording source keeps its own live translation session.

    The sources never wait on each other, and captions update while people are
    still talking. Whisper is never loaded.
    """

    def __init__(
        self,
        *,
        capture: AudioSource,
        provider: Any,
        provider_id: str,
        state: BackendState,
        settings: SettingsService,
        on_update: StreamUpdateHandler,
        on_model_status: ModelStatusHandler,
        on_error: ErrorHandler,
    ) -> None:
        super().__init__(
            capture=capture, on_model_status=on_model_status, on_error=on_error
        )
        self._provider = provider
        self._provider_id = provider_id
        self._state = state
        self._settings = settings
        self._on_update = on_update
        self._lock = threading.Lock()
        # transcript source -> (session, gate)
        self._sessions: dict[str, tuple[Any, _SpeechGate]] = {}

    def on_audio(
        self, transcript_source: str, session_id: str, pcm: bytes, speech: bool
    ) -> None:
        with self._lock:
            entry = self._sessions.get(transcript_source)
        if entry is None or not self._capture.is_active(transcript_source, session_id):
            return
        session, gate = entry
        frames, ended = gate.feed(pcm, speech)
        for frame in frames:
            session.send_audio(frame)
        if ended:
            session.end_audio()

    def _before_start(self, source: str) -> None:
        name = transcript_source(source)
        with self._state.lock:
            generation = self._state.generations[name]
        settings = self._settings.current
        target = (
            local_language(settings)
            if name == "speaker"
            else settings["translation"]["targetLanguage"]
        )

        def update(
            utterance_id: str,
            heard: str,
            translated: str,
            language: str | None,
            final: bool,
        ) -> None:
            self._on_update(
                source=name,
                generation=generation,
                provider=self._provider_id,
                utterance_id=utterance_id,
                heard=heard,
                translated=translated,
                language=language,
                final=final,
            )

        session = self._provider.open_session(
            source=name,
            target_language=target,
            on_update=update,
            on_error=self._on_error,
        )
        with self._lock:
            previous = self._sessions.pop(name, None)
            self._sessions[name] = (session, _SpeechGate())
        if previous is not None:
            previous[0].close()

    def _after_stop(self, source: str) -> None:
        with self._lock:
            entry = self._sessions.pop(transcript_source(source), None)
        if entry is not None:
            entry[0].close()

    def _close(self) -> None:
        with self._lock:
            entries = list(self._sessions.values())
            self._sessions.clear()
        for session, _gate in entries:
            session.close()
