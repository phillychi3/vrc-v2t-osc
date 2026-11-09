from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from backend import i18n
from backend.contracts import (
    AudioSource,
    EmotionClassifier,
    OverlaySink,
    TranslationRunner,
)
from backend.core.errors import ServiceError
from backend.core.events import EventBus
from backend.core.osc_link import OscLink
from backend.core.recording import RecordingController
from backend.core.settings import SettingsService
from backend.core.state import BackendState, StatusReporter
from backend.core.utterances import UtterancePublisher
from backend.i18n import t
from backend.pipelines.selector import PipelineSelector
from backend.protocol import Request

Handler = Callable[[dict[str, Any]], object]


class Commands:
    """The work behind each protocol method; collaborators are injected."""

    def __init__(
        self,
        *,
        state: BackendState,
        settings: SettingsService,
        events: EventBus,
        status: StatusReporter,
        osc: OscLink,
        capture: AudioSource,
        recording: RecordingController,
        pipelines: PipelineSelector,
        publisher: UtterancePublisher,
        translation: TranslationRunner,
        emotion: EmotionClassifier,
        overlay: OverlaySink,
        shutdown: Callable[[], None],
    ) -> None:
        self._state = state
        self._settings = settings
        self._events = events
        self._status = status
        self._osc = osc
        self._capture = capture
        self._recording = recording
        self._pipelines = pipelines
        self._publisher = publisher
        self._translation = translation
        self._emotion = emotion
        self._overlay = overlay
        self._shutdown = shutdown

    def handlers(self) -> dict[str, Handler]:
        return {
            "system.initialize": self.initialize,
            "system.getState": self.get_state,
            "audio.listDevices": self.list_audio_devices,
            "recording.start": self.start_recording,
            "recording.stop": self.stop_recording,
            "translation.listProviders": self.list_translation_providers,
            "overlay.show": self.show_overlay,
            "overlay.hide": self.hide_overlay,
            "overlay.calibrate": self.calibrate_overlay,
            "settings.update": self.update_settings,
            "text.send": self.send_text,
            "system.shutdown": self.shutdown,
        }

    def snapshot(self) -> dict[str, Any]:
        return self._state.snapshot(
            self._events.session_id, self._events.seq, self._settings.current
        )

    def initialize(self, params: dict[str, Any]) -> object:
        with self._state.lock:
            if self._state.lifecycle != "starting":
                return self.snapshot()
            value = params.get("settings")
            if value is None:
                raise ServiceError("INVALID_PARAMS", "缺少 settings")
            try:
                self._settings.load(value)
            except ValueError as exc:
                raise ServiceError("INVALID_SETTINGS", str(exc)) from exc
            data_path = params.get("dataPath")
            if not isinstance(data_path, str) or not data_path:
                raise ServiceError("INVALID_PARAMS", "dataPath 必須是非空字串")
            self._state.data_path = data_path
            self._state.suppress_auto_start = params.get("suppressAutoStart") is True
            settings = self._settings.current
            system_languages = params.get("systemLanguages")
            i18n.configure(
                settings["ui"]["language"],
                system_languages if isinstance(system_languages, list) else None,
            )
            self._osc.configure(settings)
        self._pipelines.replace(settings)
        if settings["emotion"]["enabled"]:
            self._emotion.load_model(wait=True)
        else:
            self._status.model("emotion", "not_loaded", None)
        self._overlay.configure(settings["overlay"])
        pipeline = self._pipelines.current
        if pipeline is None or not pipeline.ready:
            raise ServiceError("MODEL_LOAD_FAILED", t("backend.voice.notReady"))
        if settings["emotion"]["enabled"] and not self._emotion.ready:
            raise ServiceError("MODEL_LOAD_FAILED", t("startup.modelsFailed"))
        try:
            self._translation.prepare(settings["translation"])
        except Exception as exc:
            raise ServiceError("MODEL_LOAD_FAILED", str(exc), retryable=True) from exc
        with self._state.lock:
            self._state.lifecycle = "ready"
        if (
            settings["audio"]["autoStart"]
            and not self._state.suppress_auto_start
            and pipeline is not None
            and pipeline.ready
        ):
            try:
                self._recording.start("microphone", settings["audio"]["deviceId"])
            except ServiceError as exc:
                self._status.voice_error(str(exc))
        with self._state.lock:
            ready_state = self.snapshot()
        self._events.emit("backend.ready", ready_state)
        return self.snapshot()

    def get_state(self, _params: dict[str, Any]) -> object:
        with self._state.lock:
            return self.snapshot()

    def list_audio_devices(self, _params: dict[str, Any]) -> object:
        try:
            return {"devices": self._capture.list_devices()}
        except Exception as exc:
            raise ServiceError("AUDIO_UNAVAILABLE", str(exc), retryable=True) from exc

    def start_recording(self, params: dict[str, Any]) -> object:
        return self._recording.start(
            params.get("source", "microphone"), params.get("deviceId")
        )

    def stop_recording(self, params: dict[str, Any]) -> object:
        return self._recording.stop(params.get("source"))  # type: ignore[arg-type]

    def list_translation_providers(self, _params: dict[str, Any]) -> object:
        return {"providers": self._translation.providers()}

    def show_overlay(self, params: dict[str, Any]) -> object:
        self._overlay.show_image(self._overlay_image(params))
        return {"accepted": True}

    def calibrate_overlay(self, params: dict[str, Any]) -> object:
        prompt = self._overlay_image(params)
        if not self._overlay.calibrate(prompt, params.get("update") is True):
            raise ServiceError("OVERLAY_NOT_READY", t("backend.overlayNotReady"))
        return {"accepted": True}

    def hide_overlay(self, _params: dict[str, Any]) -> object:
        self._overlay.hide()
        return {"accepted": True}

    def update_settings(self, params: dict[str, Any]) -> object:
        try:
            candidate = self._settings.preview(params.get("patch"))
        except ValueError as exc:
            raise ServiceError("INVALID_SETTINGS", str(exc)) from exc
        current = self._settings.current
        if (
            self._state.is_recording("microphone")
            and candidate["audio"]["deviceId"] != current["audio"]["deviceId"]
        ):
            raise ServiceError("RECORDING_ACTIVE", t("backend.stopRecordingForDevice"))
        if (
            self._state.is_recording("speaker")
            and candidate["audio"]["speakerDeviceId"]
            != current["audio"]["speakerDeviceId"]
        ):
            raise ServiceError("RECORDING_ACTIVE", t("backend.stopSpeakerForDevice"))
        self._settings.commit(candidate)
        settings = self._settings.snapshot()
        self._events.emit("settings.changed", {"settings": deepcopy(settings)})
        return {"settings": settings}

    def send_text(self, params: dict[str, Any]) -> object:
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ServiceError("INVALID_PARAMS", "text 必須是非空字串")
        return self._publisher.send_manual(text)

    def shutdown(self, _params: dict[str, Any]) -> object:
        self._shutdown()
        return {"state": "stopped"}

    def _overlay_image(self, params: dict[str, Any]) -> str:
        path = params.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ServiceError("INVALID_PARAMS", "path 必須是非空字串")
        root = Path(str(self._state.data_path), "overlay").resolve()
        image = Path(path.strip()).resolve()
        if image.parent != root or image.suffix.lower() != ".png" or not image.is_file():
            raise ServiceError("INVALID_PARAMS", "VR 字幕圖片路徑無效")
        return str(image)


class Router:
    """Dispatches requests, refusing them before startup and during shutdown."""

    def __init__(self, *, state: BackendState, handlers: dict[str, Handler]) -> None:
        self._state = state
        self._handlers = handlers

    def handle(self, request: Request) -> object:
        handler = self._handlers.get(request.method)
        if handler is None:
            raise ServiceError("METHOD_NOT_FOUND", f"未知方法: {request.method}")
        lifecycle = self._state.lifecycle
        if request.method != "system.initialize" and lifecycle == "starting":
            raise ServiceError(
                "NOT_INITIALIZED", t("backend.notInitialized"), retryable=True
            )
        if lifecycle in {"stopping", "stopped"}:
            raise ServiceError("SHUTTING_DOWN", t("backend.shuttingDown"))
        return handler(request.params)
