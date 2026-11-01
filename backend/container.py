from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend import i18n
from backend.audio.capture import AudioCapture
from backend.audio.whisper import WhisperRecognizer
from backend.contracts import AudioSource, EmotionClassifier, OverlaySink, Pipeline
from backend.core.events import EventBus, MessageSender
from backend.core.osc_link import OscLink, OscFactory
from backend.core.recording import RecordingController
from backend.core.settings import SettingsService, local_language
from backend.core.state import BackendState, StatusReporter
from backend.core.utterances import UtterancePublisher
from backend.emotion import EmotionService
from backend.osc import OscService
from backend.overlay import OverlayService
from backend.pipelines.selector import PipelineSelector
from backend.pipelines.streaming import StreamingPipeline
from backend.pipelines.whisper_text import WhisperTextPipeline
from backend.protocol import Request
from backend.rpc import Commands, Router
from backend.settings import default_settings
from backend.translation.base import ProviderKind, TranslationProviderFactory
from backend.translation.registry import create_default_translation_factory
from backend.translation.worker import TranslationService


class Backend:
    """The assembled backend as the protocol loop sees it."""

    def __init__(self, *, router: Router, state: BackendState, close: Callable[[], None]):
        self._router = router
        self._state = state
        self.close = close

    def handle(self, request: Request) -> object:
        return self._router.handle(request)

    @property
    def stopped(self) -> bool:
        with self._state.lock:
            return self._state.lifecycle == "stopped"


def build(
    send: MessageSender,
    *,
    capture_factory: Callable[..., AudioSource] = AudioCapture,
    osc_factory: OscFactory = OscService,
    emotion_factory: Callable[..., EmotionClassifier] = EmotionService,
    overlay_factory: Callable[..., OverlaySink] = OverlayService,
    providers: TranslationProviderFactory | None = None,
) -> Backend:
    events = EventBus(send)
    state = BackendState()
    status = StatusReporter(state, events)
    settings = SettingsService()
    providers = providers or create_default_translation_factory()
    osc = OscLink(factory=osc_factory, events=events, state=state)
    capture = capture_factory(
        on_recording_state=status.recording, on_error=status.voice_error
    )

    # The worker and the publisher call each other; the lambdas resolve
    # `publisher` once it exists, before any translation can finish.
    publisher: UtterancePublisher
    translation = TranslationService(
        factory=providers,
        on_result=lambda result: publisher.on_translation_result(result),
        on_status=lambda provider, value: publisher.on_translation_status(
            provider, value
        ),
        on_error=lambda job_id, message, context: publisher.on_translation_error(
            job_id, message, context
        ),
    )
    emotion = emotion_factory(
        on_model_status=lambda value, device: status.model("emotion", value, device),
        on_result=lambda utterance_id, face_id: publisher.on_emotion_result(
            utterance_id, face_id
        ),
        on_error=status.emotion_error,
    )
    publisher = UtterancePublisher(
        state=state,
        settings=settings,
        events=events,
        osc=osc,
        emotion=emotion,
        translation=translation,
        providers=providers,
    )
    overlay = overlay_factory(
        settings=default_settings()["overlay"],
        on_status=status.overlay,
        on_calibration=status.calibration,
    )

    def speech_status(value: str, device: str | None) -> None:
        status.model("speech", value, device)

    def build_pipeline(kind: ProviderKind, current: dict[str, Any]) -> Pipeline:
        if kind == "stream":
            provider_id = current["translation"]["provider"]
            return StreamingPipeline(
                capture=capture,
                provider=providers.create(
                    provider_id,
                    providers.options_for(provider_id, current["translation"]),
                ),
                provider_id=provider_id,
                state=state,
                settings=settings,
                on_update=publisher.on_stream_update,
                on_model_status=speech_status,
                on_error=status.voice_error,
            )
        recognizer = WhisperRecognizer(
            model_name=current["speech"]["model"],
            language=local_language(current),
            speaker_language=current["translation"]["targetLanguage"],
            is_active=capture.is_active,
            on_transcript=publisher.on_transcript,
            on_error=status.voice_error,
        )
        return WhisperTextPipeline(
            capture=capture,
            recognizer=recognizer,
            on_model_status=speech_status,
            on_error=status.voice_error,
        )

    def pipeline_failed(message: str) -> None:
        speech_status("failed", None)
        status.voice_error(message)

    pipelines = PipelineSelector(
        providers=providers, build=build_pipeline, on_failure=pipeline_failed
    )
    recording = RecordingController(
        state=state, settings=settings, osc=osc, pipelines=pipelines, status=status
    )

    # Reactions to a settings change, in order; the pipeline switch goes last
    # so it sees the interface language and OSC target already applied.
    settings.subscribe(lambda _old, new: i18n.configure(new["ui"]["language"]))
    settings.subscribe(osc.on_settings_changed)

    def overlay_changed(old: dict[str, Any], new: dict[str, Any]) -> None:
        if old["overlay"] != new["overlay"]:
            overlay.configure(new["overlay"])

    def emotion_changed(old: dict[str, Any], new: dict[str, Any]) -> None:
        if not new["emotion"]["enabled"]:
            emotion.clear_pending()
        elif not old["emotion"]["enabled"]:
            emotion.load_model()

    def translation_changed(old: dict[str, Any], new: dict[str, Any]) -> None:
        # Free cached providers, such as a loaded model, whose options changed.
        for info in providers.available():
            if info.options(old["translation"]) != info.options(new["translation"]):
                translation.invalidate(info.provider_id)

    settings.subscribe(overlay_changed)
    settings.subscribe(emotion_changed)
    settings.subscribe(translation_changed)
    settings.subscribe(recording.on_settings_changed)

    def close() -> None:
        with state.lock:
            if state.lifecycle == "stopped":
                return
            state.lifecycle = "stopping"
        pipelines.close()
        capture.close()
        osc.close()
        emotion.close()
        translation.close()
        overlay.close()
        with state.lock:
            state.recordings = {"microphone": "idle", "speaker": "idle"}
            state.lifecycle = "stopped"

    commands = Commands(
        state=state,
        settings=settings,
        events=events,
        status=status,
        osc=osc,
        capture=capture,
        recording=recording,
        pipelines=pipelines,
        publisher=publisher,
        translation=translation,
        emotion=emotion,
        overlay=overlay,
        shutdown=close,
    )
    router = Router(state=state, handlers=commands.handlers())
    return Backend(router=router, state=state, close=close)
