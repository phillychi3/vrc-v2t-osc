from __future__ import annotations

import logging
from typing import Any

from backend.contracts import OscSink
from backend.core.errors import ServiceError
from backend.core.settings import SettingsService
from backend.core.state import SOURCES, BackendState, StatusReporter, transcript_source
from backend.i18n import t
from backend.pipelines.selector import PipelineSelector


class RecordingController:
    """Starts and stops sources, and moves them across pipeline switches.

    When a settings change calls for a different pipeline, the sources that
    were recording are stopped, the pipeline is replaced, and the same sources
    start again on the devices they used, without the user having to.
    """

    def __init__(
        self,
        *,
        state: BackendState,
        settings: SettingsService,
        osc: OscSink,
        pipelines: PipelineSelector,
        status: StatusReporter,
    ) -> None:
        self._state = state
        self._settings = settings
        self._osc = osc
        self._pipelines = pipelines
        self._status = status

    def start(self, source: str, device_id: str | None = None) -> dict[str, Any]:
        if source not in SOURCES:
            raise ServiceError("INVALID_PARAMS", "source 必須是 microphone 或 speaker")
        pipeline = self._pipelines.current
        if pipeline is None:
            raise ServiceError(
                "AUDIO_UNAVAILABLE", t("backend.audioUnavailable"), retryable=True
            )
        if device_id is None:
            audio = self._settings.current["audio"]
            device_id = (
                audio["speakerDeviceId"] if source == "speaker" else audio["deviceId"]
            )
        if not isinstance(device_id, str) or not device_id:
            raise ServiceError("INVALID_PARAMS", "deviceId 必須是非空字串")
        name = transcript_source(source)
        with self._state.lock:
            if self._state.is_recording(source):
                return {"state": self._state.recordings[source]}
            self._state.stopped_sources.discard(name)
        try:
            if not pipeline.ready:
                raise RuntimeError(t("backend.voice.notReady"))
            pipeline.start(source, device_id)
        except Exception as exc:
            with self._state.lock:
                self._state.stopped_sources.add(name)
            raise ServiceError("RECORDING_FAILED", str(exc), retryable=True) from exc
        with self._state.lock:
            self._state.devices[source] = device_id
            return {"state": self._state.recordings[source]}

    def stop(self, source: str) -> dict[str, Any]:
        if source not in SOURCES:
            raise ServiceError("INVALID_PARAMS", "source 必須是 microphone 或 speaker")
        name = transcript_source(source)
        with self._state.lock:
            self._state.stopped_sources.add(name)
            self._state.generations[name] += 1
            generation = self._state.generations[name]
            if self._state.latest_voice_source == name:
                self._state.latest_voice_utterance_id = None
        self._osc.set_source_generation(name, generation)
        pipeline = self._pipelines.current
        if pipeline is not None:
            pipeline.stop(source)
        else:
            self._status.recording("idle", source)
        with self._state.lock:
            return {"state": self._state.recordings[source]}

    def stop_all(self) -> None:
        for source in self._state.active_sources():
            self.stop(source)

    def on_settings_changed(
        self, previous: dict[str, Any], current: dict[str, Any]
    ) -> None:
        if self._pipelines.matches(current):
            pipeline = self._pipelines.current
            if pipeline is not None:
                pipeline.update_settings(current)
            return
        with self._state.lock:
            resume = [
                (source, self._state.devices.get(source))
                for source in self._state.active_sources()
            ]
        for source, _device in resume:
            self.stop(source)
        # Loading a model (switching back to Whisper) blocks until it is ready,
        # so the sources restart only once they can be recognized again.
        self._pipelines.replace(current)
        for source, device in resume:
            try:
                self.start(source, device)
            except ServiceError as exc:
                logging.warning("Could not resume %s after a switch: %s", source, exc)
                self._status.voice_error(str(exc))
