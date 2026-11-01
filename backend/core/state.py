from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any

from backend.contracts import EventSink

SOURCES = ("microphone", "speaker")
_IDLE = {"idle", "error"}


def transcript_source(source: str) -> str:
    """Capture sources are named microphone/speaker; transcripts voice/speaker."""
    return "speaker" if source == "speaker" else "voice"


class BackendState:
    """Authoritative backend state. Guard reads and writes with `lock`."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.lifecycle = "starting"
        self.data_path: str | None = None
        self.suppress_auto_start = False
        self.recordings = {source: "idle" for source in SOURCES}
        # The device each source is recording from, to restart it on a switch.
        self.devices: dict[str, str] = {}
        self.models: dict[str, dict[str, str | None]] = {
            "speech": {"status": "not_loaded", "device": None},
            "emotion": {"status": "not_loaded", "device": None},
        }
        self.overlay: dict[str, str | None] = {"status": "disabled", "message": None}
        # Bumped whenever a source stops, so late results from before are dropped.
        self.generations = {"voice": 0, "speaker": 0}
        self.stopped_sources: set[str] = set()
        self.latest_voice_utterance_id: str | None = None
        self.latest_voice_source: str | None = None
        self.latest_voice_generation = 0

    def active_sources(self) -> list[str]:
        with self.lock:
            return [s for s in SOURCES if self.recordings[s] not in _IDLE]

    def is_recording(self, source: str) -> bool:
        with self.lock:
            return self.recordings[source] not in _IDLE

    def snapshot(
        self, session_id: str, seq: int, settings: dict[str, Any]
    ) -> dict[str, Any]:
        with self.lock:
            active = self.active_sources()
            states = set(self.recordings.values())
            for summary in ("listening", "starting", "stopping", "error"):
                if summary in states:
                    recording = summary
                    break
            else:
                recording = "idle"
            return {
                "sessionId": session_id,
                "seq": seq,
                "backend": self.lifecycle,
                "recording": recording,
                "recordingSource": active[0] if len(active) == 1 else None,
                "recordings": deepcopy(self.recordings),
                "models": deepcopy(self.models),
                "overlay": dict(self.overlay),
                "settings": deepcopy(settings),
            }


class StatusReporter:
    """Records status changes in the state and announces them as events."""

    def __init__(self, state: BackendState, events: EventSink) -> None:
        self._state = state
        self._events = events

    def model(self, name: str, status: str, device: str | None) -> None:
        with self._state.lock:
            self._state.models[name] = {"status": status, "device": device}
        self._events.emit(
            "model.status", {"model": name, "status": status, "device": device}
        )

    def recording(self, state: str, source: str) -> None:
        with self._state.lock:
            self._state.recordings[source] = state
        self._events.emit("recording.state", {"state": state, "source": source})

    def overlay(self, status: str, message: str | None) -> None:
        data = {"status": status, "message": message}
        with self._state.lock:
            self._state.overlay = dict(data)
        self._events.emit("overlay.status", data)

    def calibration(
        self, status: str, basis: list[float] | None, message: str | None
    ) -> None:
        self._events.emit(
            "overlay.calibration", {"status": status, "basis": basis, "message": message}
        )

    def error(self, code: str, message: str, *, retryable: bool = True) -> None:
        self._events.emit(
            "error", {"code": code, "message": message, "retryable": retryable}
        )

    def voice_error(self, message: str) -> None:
        self.error("VOICE_ERROR", message)

    def emotion_error(self, message: str) -> None:
        self.error("EMOTION_ERROR", message)
