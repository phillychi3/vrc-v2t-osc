from __future__ import annotations

import uuid
from typing import Any

from backend.contracts import EmotionClassifier, EventSink, OscSink, TranslationRunner
from backend.core.errors import ServiceError
from backend.core.settings import SettingsService, local_language
from backend.core.state import BackendState
from backend.i18n import t
from backend.osc import text_rejection_reason
from backend.settings import SUPPORTED_LANGUAGES
from backend.translation.base import TranslationProviderFactory


class UtterancePublisher:
    def __init__(
        self,
        *,
        state: BackendState,
        settings: SettingsService,
        events: EventSink,
        osc: OscSink,
        emotion: EmotionClassifier,
        translation: TranslationRunner,
        providers: TranslationProviderFactory,
    ) -> None:
        self._state = state
        self._settings = settings
        self._events = events
        self._osc = osc
        self._emotion = emotion
        self._translation = translation
        self._providers = providers

    def on_transcript(
        self, text: str, source: str = "voice", language: str | None = None
    ) -> None:
        """A local transcript: show it, then translate or send it as is."""
        utterance_id = str(uuid.uuid4())
        with self._state.lock:
            if self._state.lifecycle != "ready" or source in self._state.stopped_sources:
                return
            generation = self._state.generations[source]
            if source == "voice":
                self._remember_voice(utterance_id, generation)
        self._events.emit(
            "transcript.final",
            {"utteranceId": utterance_id, "text": text, "source": source},
        )
        translating = self._submit_text_translation(
            utterance_id, text, source, generation, language
        )
        if not translating and source == "voice":
            self._send(utterance_id, text, source, generation)
        if source == "voice":
            self._classify(utterance_id, text)

    def _submit_text_translation(
        self,
        utterance_id: str,
        text: str,
        source: str,
        generation: int,
        detected_language: str | None,
    ) -> bool:
        settings = self._settings.current["translation"]
        if not settings["enabled"]:
            return False
        source_language, target_language = self._languages(source)
        if detected_language is not None:
            if (
                detected_language == target_language
                or detected_language not in SUPPORTED_LANGUAGES
            ):
                return False
            source_language = detected_language
        job_id = self._translation.submit(
            provider_id=settings["provider"],
            text=text,
            source_language=source_language,
            target_language=target_language,
            options=self._providers.options_for(settings["provider"], settings),
            context={
                "utteranceId": utterance_id,
                "source": source,
                "originalText": text,
                "sendToOsc": source == "voice",
                "recordingGeneration": generation,
            },
        )
        if job_id is None:
            self._busy(utterance_id, "backend.translationBusySentOriginal")
            return False
        return True

    def on_stream_update(
        self,
        *,
        source: str,
        generation: int,
        provider: str,
        utterance_id: str,
        heard: str,
        translated: str,
        language: str | None,
        final: bool,
    ) -> None:
        """A streaming session's view of the current utterance."""
        source_language, target_language = self._languages(source)
        context = self._speech_context(utterance_id, source, generation)
        result = {
            "jobId": "",
            "provider": provider,
            # Speech nobody translated is passed through as it was heard.
            "text": translated or heard,
            "sourceText": heard,
            "sourceLanguage": language or source_language,
            "targetLanguage": target_language,
            "context": context,
        }
        if final:
            self._publish_speech(result, context)
            return
        with self._state.lock:
            if not self._context_active(context):
                return
        if heard:
            self._events.emit(
                "transcript.partial",
                {"utteranceId": utterance_id, "text": heard, "source": source},
            )
        if translated:
            self._events.emit("translation.partial", {**result, "text": translated})

    def on_translation_result(self, result: dict[str, Any]) -> None:
        context = result.get("context")
        self._events.emit("translation.result", result)
        if not isinstance(context, dict) or not context.get("sendToOsc"):
            return
        utterance_id = context.get("utteranceId")
        text = result.get("text")
        with self._state.lock:
            if (
                isinstance(utterance_id, str)
                and isinstance(text, str)
                and self._context_active(context)
            ):
                self._send(
                    utterance_id, text, context["source"], context["recordingGeneration"]
                )

    def on_translation_status(self, provider: str, status: str) -> None:
        self._events.emit("translation.status", {"provider": provider, "status": status})

    def on_translation_error(
        self, job_id: str, message: str, context: dict[str, Any]
    ) -> None:
        self._events.emit(
            "translation.error",
            {"jobId": job_id, "message": message, "context": context},
        )
        if not context.get("sendToOsc"):
            return
        utterance_id = context.get("utteranceId")
        original_text = context.get("originalText")
        with self._state.lock:
            if (
                isinstance(utterance_id, str)
                and isinstance(original_text, str)
                and self._context_active(context)
            ):
                self._send(
                    utterance_id,
                    original_text,
                    context["source"],
                    context["recordingGeneration"],
                )

    def on_emotion_result(self, utterance_id: str, face_id: int) -> None:
        self._events.emit(
            "emotion.result",
            {"utteranceId": utterance_id, "emotion": face_id, "faceId": face_id},
        )
        with self._state.lock:
            if (
                self._settings.current["emotion"]["enabled"]
                and self._state.latest_voice_source == "voice"
                and utterance_id == self._state.latest_voice_utterance_id
            ):
                self._osc.set_face(
                    face_id,
                    utterance_id=utterance_id,
                    source=self._state.latest_voice_source,
                    generation=self._state.latest_voice_generation,
                )

    def send_manual(self, text: str) -> dict[str, Any]:
        if text_rejection_reason(text) in {"too_long", "too_many_lines"}:
            raise ServiceError("TEXT_TOO_LONG", t("backend.textTooLong"))
        utterance_id = str(uuid.uuid4())
        self._events.emit(
            "transcript.final",
            {"utteranceId": utterance_id, "text": text, "source": "manual"},
        )
        accepted = self._send(utterance_id, text)
        return {"utteranceId": utterance_id, "accepted": accepted}

    def _publish_speech(self, result: dict[str, Any], context: dict[str, Any]) -> None:
        """Publishes what the service heard, then its translation."""
        utterance_id = context.get("utteranceId")
        source = context.get("source")
        text = result.get("text")
        if not (
            isinstance(utterance_id, str)
            and isinstance(source, str)
            and isinstance(text, str)
            and text.strip()
        ):
            return
        with self._state.lock:
            if not self._context_active(context):
                return
            if source == "voice":
                self._remember_voice(utterance_id, context["recordingGeneration"])
        heard = result.get("sourceText")
        # Without a transcript of what was said, show the translation instead.
        original = heard if isinstance(heard, str) and heard else text
        self._events.emit(
            "transcript.final",
            {"utteranceId": utterance_id, "text": original, "source": source},
        )
        self._events.emit("translation.result", result)
        if source != "voice":
            return
        with self._state.lock:
            if self._context_active(context):
                self._send(utterance_id, text, source, context["recordingGeneration"])
        self._classify(utterance_id, original)

    def _languages(self, source: str) -> tuple[str, str]:
        """(spoken, wanted): other players are translated into your language."""
        settings = self._settings.current
        if source == "speaker":
            return settings["translation"]["targetLanguage"], local_language(settings)
        return (
            settings["translation"]["sourceLanguage"],
            settings["translation"]["targetLanguage"],
        )

    @staticmethod
    def _speech_context(
        utterance_id: str, source: str, generation: int
    ) -> dict[str, Any]:
        return {
            "utteranceId": utterance_id,
            "source": source,
            "sendToOsc": source == "voice",
            "recordingGeneration": generation,
            "speech": True,
        }

    def _context_active(self, context: dict[str, Any]) -> bool:
        source = context.get("source")
        return (
            self._state.lifecycle == "ready"
            and isinstance(source, str)
            and source in self._state.generations
            and context.get("recordingGeneration") == self._state.generations[source]
        )

    def _remember_voice(self, utterance_id: str, generation: int) -> None:
        self._state.latest_voice_utterance_id = utterance_id
        self._state.latest_voice_source = "voice"
        self._state.latest_voice_generation = generation

    def _classify(self, utterance_id: str, text: str) -> None:
        if self._settings.current["emotion"]["enabled"]:
            self._emotion.submit(utterance_id, text)

    def _busy(self, utterance_id: str, message: str) -> None:
        self._events.emit(
            "translation.error",
            {
                "jobId": "",
                "message": t(message),
                "context": {"utteranceId": utterance_id},
            },
        )

    def _send(
        self,
        utterance_id: str,
        text: str,
        source: str | None = None,
        generation: int = 0,
    ) -> bool:
        return self._osc.send_text(
            text, utterance_id=utterance_id, source=source, generation=generation
        )
