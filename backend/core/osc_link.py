from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from backend.contracts import EventSink, OscSink
from backend.core.state import BackendState

OscFactory = Callable[..., OscSink]


class OscLink:
    """The current OSC sender, rebuilt when its address or parameter changes.

    Everything else holds this link rather than a sender, so replacing the
    sender never leaves anyone pointing at a closed one.
    """

    def __init__(
        self, *, factory: OscFactory, events: EventSink, state: BackendState
    ) -> None:
        self._factory = factory
        self._events = events
        self._state = state
        self._lock = threading.RLock()
        self._osc: Any = None

    def configure(self, settings: dict[str, Any]) -> None:
        osc = settings["osc"]
        replacement = self._factory(
            host=osc["host"],
            port=osc["port"],
            enabled=osc["enabled"],
            emotion_parameter=settings["emotion"]["parameter"],
            on_event=self._events.emit,
        )
        with self._state.lock:
            generations = dict(self._state.generations)
        for source, generation in generations.items():
            replacement.set_source_generation(source, generation)
        with self._lock:
            previous, self._osc = self._osc, replacement
        if previous is not None:
            previous.close()

    def on_settings_changed(
        self, previous: dict[str, Any], current: dict[str, Any]
    ) -> None:
        if (
            previous["osc"]["host"] != current["osc"]["host"]
            or previous["osc"]["port"] != current["osc"]["port"]
            or previous["emotion"]["parameter"] != current["emotion"]["parameter"]
        ):
            self.configure(current)
        elif self._osc is not None:
            self._osc.set_enabled(current["osc"]["enabled"])

    def send_text(
        self,
        text: str,
        *,
        utterance_id: str | None = None,
        source: str | None = None,
        generation: int = 0,
    ) -> bool:
        osc = self._osc
        if osc is None:
            self._events.emit(
                "osc.skipped",
                {"utteranceId": utterance_id, "kind": "text", "reason": "closed"},
            )
            return False
        return osc.send_text(
            text, utterance_id=utterance_id, source=source, generation=generation
        )

    def set_face(
        self,
        face_id: int,
        *,
        utterance_id: str | None = None,
        source: str | None = None,
        generation: int = 0,
    ) -> bool:
        osc = self._osc
        if osc is None:
            return False
        return osc.set_face(
            face_id, utterance_id=utterance_id, source=source, generation=generation
        )

    def set_source_generation(self, source: str, generation: int) -> None:
        osc = self._osc
        if osc is not None:
            osc.set_source_generation(source, generation)

    def close(self) -> None:
        with self._lock:
            osc, self._osc = self._osc, None
        if osc is not None:
            osc.close()
