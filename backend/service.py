from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from copy import deepcopy
from typing import Any, Protocol

from backend.osc import OscService, text_rejection_reason
from backend.protocol import Request
from backend.settings import apply_patch, default_settings, validate_settings


EventHandler = Callable[[str, dict[str, Any]], None]
OscFactory = Callable[..., OscService]


class VoiceProtocol(Protocol):
    @property
    def recording(self) -> bool: ...

    def load_models(self) -> None: ...
    def list_devices(self) -> list[dict[str, object]]: ...
    def start(self, device_id: str, source: str = "microphone") -> None: ...
    def stop(self, source: str | None = None) -> None: ...
    def set_languages(self, microphone_language: str, speaker_language: str) -> None: ...
    def close(self) -> None: ...


VoiceFactory = Callable[..., VoiceProtocol]


class EmotionProtocol(Protocol):
    @property
    def ready(self) -> bool: ...

    def load_model(self) -> None: ...
    def submit(self, utterance_id: str, text: str) -> bool: ...
    def clear_pending(self) -> None: ...
    def close(self) -> None: ...


EmotionFactory = Callable[..., EmotionProtocol]


class TranslationProtocol(Protocol):
    def providers(self) -> list[dict[str, object]]: ...
    def submit(
        self,
        *,
        provider_id: str,
        text: str,
        source_language: str,
        target_language: str,
        options: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> str | None: ...
    def close(self) -> None: ...
    def invalidate(self, provider_id: str) -> None: ...


TranslationFactory = Callable[..., TranslationProtocol]


class ServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class BackendService:
    """UI-independent command router and authoritative backend state."""

    def __init__(
        self,
        emit: EventHandler,
        *,
        osc_factory: OscFactory = OscService,
        voice_factory: VoiceFactory | None = None,
        emotion_factory: EmotionFactory | None = None,
        translation_factory: TranslationFactory | None = None,
    ) -> None:
        self._emit_handler = emit
        self._osc_factory = osc_factory
        self._voice_factory = voice_factory
        self._emotion_factory = emotion_factory
        self._translation_factory = translation_factory
        self._osc: OscService | None = None
        self._voice: VoiceProtocol | None = None
        self._emotion: EmotionProtocol | None = None
        self._translation: TranslationProtocol | None = None
        self._lock = threading.RLock()
        self._settings = default_settings()
        self._session_id = str(uuid.uuid4())
        self._seq = 0
        self._lifecycle = "starting"
        self._recording = "idle"
        self._recording_source: str | None = None
        self._recordings = {"microphone": "idle", "speaker": "idle"}
        self._models: dict[str, dict[str, str | None]] = {
            "speech": {"status": "not_loaded", "device": None},
            "emotion": {"status": "not_loaded", "device": None},
        }
        self._data_path: str | None = None
        self._latest_voice_utterance_id: str | None = None
        self._latest_voice_source: str | None = None
        self._suppress_auto_start = False
        self._recording_generations = {"voice": 0, "speaker": 0}
        self._latest_voice_generation = 0
        self._stopped_sources: set[str] = set()

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._lifecycle == "stopped"

    def handle(self, request: Request) -> object:
        handlers: dict[str, Callable[[dict[str, Any]], object]] = {
            "system.initialize": self._initialize,
            "system.getState": self._get_state,
            "audio.listDevices": self._list_audio_devices,
            "recording.start": self._start_recording,
            "recording.stop": self._stop_recording,
            "translation.listProviders": self._list_translation_providers,
            "translation.translate": self._translate,
            "settings.update": self._update_settings,
            "text.send": self._send_text,
            "system.shutdown": self._shutdown,
        }
        handler = handlers.get(request.method)
        if handler is None:
            raise ServiceError("METHOD_NOT_FOUND", f"未知方法: {request.method}")
        if request.method != "system.initialize" and self._lifecycle == "starting":
            raise ServiceError("NOT_INITIALIZED", "後端尚未初始化", retryable=True)
        if self._lifecycle in {"stopping", "stopped"}:
            raise ServiceError("SHUTTING_DOWN", "後端正在關閉")
        return handler(request.params)

    def close(self) -> None:
        with self._lock:
            if self._lifecycle == "stopped":
                return
            self._lifecycle = "stopping"
            osc, self._osc = self._osc, None
            voice, self._voice = self._voice, None
            emotion, self._emotion = self._emotion, None
            translation, self._translation = self._translation, None
        if osc is not None:
            osc.close()
        if voice is not None:
            voice.close()
        if emotion is not None:
            emotion.close()
        if translation is not None:
            translation.close()
        with self._lock:
            self._recording = "idle"
            self._recording_source = None
            self._recordings = {"microphone": "idle", "speaker": "idle"}
            self._lifecycle = "stopped"

    def _initialize(self, params: dict[str, Any]) -> object:
        with self._lock:
            if self._lifecycle != "starting":
                return self._state()
            settings = params.get("settings")
            if settings is None:
                raise ServiceError("INVALID_PARAMS", "缺少 settings")
            try:
                self._settings = validate_settings(settings)
            except ValueError as exc:
                raise ServiceError("INVALID_SETTINGS", str(exc)) from exc
            data_path = params.get("dataPath")
            if not isinstance(data_path, str) or not data_path:
                raise ServiceError("INVALID_PARAMS", "dataPath 必須是非空字串")
            self._data_path = data_path
            self._suppress_auto_start = params.get("suppressAutoStart") is True
            self._replace_osc()
            self._lifecycle = "ready"
            ready_state = self._state()

        self._emit("backend.ready", ready_state)
        if self._voice_factory is not None:
            self._voice = self._create_voice()
            self._voice.load_models()
        else:
            self._emit(
                "model.status",
                {"model": "speech", "status": "not_loaded", "device": None},
            )
        if self._emotion_factory is not None:
            self._emotion = self._emotion_factory(
                on_model_status=self._on_emotion_model_status,
                on_result=self._on_emotion_result,
                on_error=self._on_emotion_error,
            )
            if self._settings["emotion"]["enabled"]:
                self._emotion.load_model()
            else:
                self._emit(
                    "model.status",
                    {"model": "emotion", "status": "not_loaded", "device": None},
                )
        else:
            self._emit(
                "model.status",
                {"model": "emotion", "status": "not_loaded", "device": None},
            )
        if self._translation_factory is not None:
            self._translation = self._translation_factory(
                on_result=self._on_translation_result,
                on_status=self._on_translation_status,
                on_error=self._on_translation_error,
            )
        with self._lock:
            return self._state()

    def _get_state(self, _params: dict[str, Any]) -> object:
        with self._lock:
            return self._state()

    def _list_audio_devices(self, _params: dict[str, Any]) -> object:
        voice = self._voice
        if voice is None:
            return {"devices": []}
        try:
            return {"devices": voice.list_devices()}
        except Exception as exc:
            raise ServiceError("AUDIO_UNAVAILABLE", str(exc), retryable=True) from exc

    def _start_recording(self, params: dict[str, Any]) -> object:
        voice = self._voice
        if voice is None:
            raise ServiceError("AUDIO_UNAVAILABLE", "錄音服務尚未提供", retryable=True)
        source = params.get("source", "microphone")
        if source not in {"microphone", "speaker"}:
            raise ServiceError("INVALID_PARAMS", "source 必須是 microphone 或 speaker")
        default_device = (
            self._settings["audio"]["speakerDeviceId"]
            if source == "speaker"
            else self._settings["audio"]["deviceId"]
        )
        device_id = params.get("deviceId", default_device)
        if not isinstance(device_id, str) or not device_id:
            raise ServiceError("INVALID_PARAMS", "deviceId 必須是非空字串")
        with self._lock:
            if self._recordings[source] not in {"idle", "error"}:
                return {"state": self._recordings[source]}
            self._stopped_sources.discard(
                "voice" if source == "microphone" else "speaker"
            )
        try:
            voice.start(device_id, source)
        except Exception as exc:
            with self._lock:
                self._stopped_sources.add(
                    "voice" if source == "microphone" else "speaker"
                )
            raise ServiceError("RECORDING_FAILED", str(exc), retryable=True) from exc
        return {"state": self._recordings[source]}

    def _stop_recording(self, params: dict[str, Any]) -> object:
        source = params.get("source")
        if source not in {"microphone", "speaker"}:
            raise ServiceError("INVALID_PARAMS", "source 必須是 microphone 或 speaker")
        with self._lock:
            transcript_source = "voice" if source == "microphone" else "speaker"
            self._stopped_sources.add(transcript_source)
            self._recording_generations[transcript_source] += 1
            if self._osc is not None:
                self._osc.set_source_generation(
                    transcript_source, self._recording_generations[transcript_source]
                )
            if self._latest_voice_source == transcript_source:
                self._latest_voice_utterance_id = None
        voice = self._voice
        if voice is not None:
            voice.stop(source)
        return {"state": self._recordings[source]}

    def _list_translation_providers(self, _params: dict[str, Any]) -> object:
        if self._translation is None:
            return {"providers": []}
        return {"providers": self._translation.providers()}

    def _translate(self, params: dict[str, Any]) -> object:
        translation = self._translation
        if translation is None:
            raise ServiceError(
                "TRANSLATION_UNAVAILABLE",
                "翻譯服務尚未提供",
                retryable=True,
            )
        provider = self._required_string(params, "provider")
        text = self._required_string(params, "text")
        source_language = self._required_string(params, "sourceLanguage")
        target_language = self._required_string(params, "targetLanguage")
        options = params.get("options", {})
        context = params.get("context", {})
        if not isinstance(options, dict) or not isinstance(context, dict):
            raise ServiceError("INVALID_PARAMS", "options 與 context 必須是物件")
        if provider == "onnx":
            options = {**options, "model": self._settings["translation"]["model"]}
        elif provider == "deepl":
            options = {
                **options,
                "plan": self._settings["translation"]["deeplPlan"],
                "apiKey": self._settings["translation"]["deeplApiKey"],
            }
        job_id = translation.submit(
            provider_id=provider,
            text=text,
            source_language=source_language,
            target_language=target_language,
            options=options,
            context=context,
        )
        if job_id is None:
            raise ServiceError(
                "TRANSLATION_OVERLOADED",
                "翻譯佇列已滿，請稍後再試",
                retryable=True,
            )
        return {"jobId": job_id, "accepted": True}

    def _update_settings(self, params: dict[str, Any]) -> object:
        patch = params.get("patch")
        try:
            candidate = apply_patch(self._settings, patch)
        except ValueError as exc:
            raise ServiceError("INVALID_SETTINGS", str(exc)) from exc

        old_osc = self._settings["osc"]
        old_device = self._settings["audio"]["deviceId"]
        old_speaker_device = self._settings["audio"]["speakerDeviceId"]
        old_speech_model = self._settings["speech"]["model"]
        old_translation_model = self._settings["translation"]["model"]
        old_deepl_plan = self._settings["translation"]["deeplPlan"]
        old_deepl_api_key = self._settings["translation"]["deeplApiKey"]
        old_parameter = self._settings["emotion"]["parameter"]
        old_emotion_enabled = self._settings["emotion"]["enabled"]
        if (
            self._recordings["microphone"] not in {"idle", "error"}
            and candidate["audio"]["deviceId"] != old_device
        ):
            raise ServiceError("RECORDING_ACTIVE", "請先停止錄音再更換裝置")
        if (
            self._recordings["speaker"] not in {"idle", "error"}
            and candidate["audio"]["speakerDeviceId"] != old_speaker_device
        ):
            raise ServiceError("RECORDING_ACTIVE", "請先停止喇叭辨識再更換裝置")
        speech_model_changed = candidate["speech"]["model"] != old_speech_model
        if speech_model_changed and any(
            state not in {"idle", "error"} for state in self._recordings.values()
        ):
            raise ServiceError("RECORDING_ACTIVE", "請先停止錄音再更換語音模型")
        if speech_model_changed and self._models["speech"]["status"] == "loading":
            raise ServiceError(
                "MODEL_LOADING",
                "語音模型正在載入，請稍後再更換",
                retryable=True,
            )
        self._settings = candidate
        if speech_model_changed and self._voice_factory is not None:
            old_voice, self._voice = self._voice, None
            if old_voice is not None:
                old_voice.close()
            with self._lock:
                self._models["speech"] = {"status": "not_loaded", "device": None}
            self._voice = self._create_voice()
            self._voice.load_models()
        elif self._voice is not None:
            self._voice.set_languages(
                candidate["speech"]["language"],
                candidate["translation"]["targetLanguage"],
            )
        if (
            candidate["translation"]["model"] != old_translation_model
            and self._translation is not None
        ):
            self._translation.invalidate("onnx")
        if self._translation is not None and (
            candidate["translation"]["deeplPlan"] != old_deepl_plan
            or candidate["translation"]["deeplApiKey"] != old_deepl_api_key
        ):
            self._translation.invalidate("deepl")
        if not candidate["emotion"]["enabled"] and self._emotion is not None:
            self._emotion.clear_pending()
        elif (
            candidate["emotion"]["enabled"]
            and not old_emotion_enabled
            and self._emotion is not None
        ):
            self._emotion.load_model()
        new_osc = candidate["osc"]
        needs_replacement = (
            old_osc["host"] != new_osc["host"]
            or old_osc["port"] != new_osc["port"]
            or old_parameter != candidate["emotion"]["parameter"]
        )
        if needs_replacement:
            self._replace_osc()
        elif self._osc is not None:
            self._osc.set_enabled(new_osc["enabled"])
        return {"settings": deepcopy(self._settings)}

    def _send_text(self, params: dict[str, Any]) -> object:
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ServiceError("INVALID_PARAMS", "text 必須是非空字串")
        reason = text_rejection_reason(text)
        if reason in {"too_long", "too_many_lines"}:
            raise ServiceError(
                "TEXT_TOO_LONG", "聊天文字最多 144 字（含表情符號長度）及 9 行"
            )
        utterance_id = str(uuid.uuid4())
        self._emit(
            "transcript.final",
            {"utteranceId": utterance_id, "text": text, "source": "manual"},
        )
        accepted = self._send_transcript_to_osc(utterance_id, text)
        return {"utteranceId": utterance_id, "accepted": accepted}

    def _shutdown(self, _params: dict[str, Any]) -> object:
        self.close()
        return {"state": "stopped"}

    def _replace_osc(self) -> None:
        old_osc = self._osc
        osc = self._settings["osc"]
        self._osc = self._osc_factory(
            host=osc["host"],
            port=osc["port"],
            enabled=osc["enabled"],
            emotion_parameter=self._settings["emotion"]["parameter"],
            on_event=self._emit,
        )
        for source, generation in self._recording_generations.items():
            self._osc.set_source_generation(source, generation)
        if old_osc is not None:
            old_osc.close()

    def _create_voice(self) -> VoiceProtocol:
        if self._voice_factory is None:
            raise RuntimeError("語音服務工廠未設定")
        return self._voice_factory(
            model_name=self._settings["speech"]["model"],
            language=self._settings["speech"]["language"],
            save_audio=self._settings["privacy"]["saveAudio"],
            save_dir=str(self._data_path) + "/voice_samples",
            on_model_status=self._on_speech_model_status,
            on_recording_state=self._on_recording_state,
            on_transcript=self._on_voice_transcript,
            on_error=self._on_voice_error,
            speaker_language=self._settings["translation"]["targetLanguage"],
        )

    def _on_speech_model_status(self, status: str, device: str | None) -> None:
        with self._lock:
            self._models["speech"] = {"status": status, "device": device}
        self._emit(
            "model.status",
            {"model": "speech", "status": status, "device": device},
        )
        if (
            status == "ready"
            and self._settings["audio"]["autoStart"]
            and not self._suppress_auto_start
        ):
            try:
                self._start_recording(
                    {
                        "deviceId": self._settings["audio"]["deviceId"],
                        "source": "microphone",
                    }
                )
            except ServiceError as exc:
                self._on_voice_error(str(exc))

    def _on_recording_state(self, state: str, source: str) -> None:
        with self._lock:
            self._recordings[source] = state
            active_sources = [
                name
                for name, current in self._recordings.items()
                if current not in {"idle", "error"}
            ]
            self._recording_source = (
                active_sources[0] if len(active_sources) == 1 else None
            )
            states = set(self._recordings.values())
            if "listening" in states:
                self._recording = "listening"
            elif "starting" in states:
                self._recording = "starting"
            elif "stopping" in states:
                self._recording = "stopping"
            elif "error" in states:
                self._recording = "error"
            else:
                self._recording = "idle"
        self._emit("recording.state", {"state": state, "source": source})

    def _on_voice_transcript(self, text: str, source: str = "voice") -> None:
        utterance_id = str(uuid.uuid4())
        with self._lock:
            if self._lifecycle != "ready" or source in self._stopped_sources:
                return
            generation = self._recording_generations[source]
            if source == "voice":
                self._latest_voice_utterance_id = utterance_id
                self._latest_voice_source = source
                self._latest_voice_generation = generation
        self._emit(
            "transcript.final",
            {"utteranceId": utterance_id, "text": text, "source": source},
        )
        translating = self._submit_transcript_translation(
            utterance_id, text, source, generation
        )
        if not translating and source == "voice":
            self._send_transcript_to_osc(utterance_id, text, source, generation)
        emotion = self._emotion
        if (
            source == "voice"
            and self._settings["emotion"]["enabled"]
            and emotion is not None
        ):
            emotion.submit(utterance_id, text)

    def _on_emotion_model_status(self, status: str, device: str | None) -> None:
        with self._lock:
            self._models["emotion"] = {"status": status, "device": device}
        self._emit(
            "model.status",
            {"model": "emotion", "status": status, "device": device},
        )

    def _on_emotion_result(self, utterance_id: str, face_id: int) -> None:
        self._emit(
            "emotion.result",
            {
                "utteranceId": utterance_id,
                "emotion": face_id,
                "faceId": face_id,
            },
        )
        with self._lock:
            should_apply = (
                self._settings["emotion"]["enabled"]
                and self._latest_voice_source == "voice"
                and utterance_id == self._latest_voice_utterance_id
            )
            osc = self._osc
            if should_apply and osc is not None:
                osc.set_face(
                    face_id,
                    utterance_id=utterance_id,
                    source=self._latest_voice_source,
                    generation=self._latest_voice_generation,
                )

    def _on_emotion_error(self, message: str) -> None:
        self._emit(
            "error",
            {"code": "EMOTION_ERROR", "message": message, "retryable": True},
        )

    def _on_voice_error(self, message: str) -> None:
        self._emit(
            "error",
            {"code": "VOICE_ERROR", "message": message, "retryable": True},
        )

    def _on_translation_result(self, result: dict[str, Any]) -> None:
        self._emit("translation.result", result)
        context = result.get("context")
        if not isinstance(context, dict) or not context.get("sendToOsc"):
            return
        utterance_id = context.get("utteranceId")
        text = result.get("text")
        with self._lock:
            if (
                isinstance(utterance_id, str)
                and isinstance(text, str)
                and self._translation_context_active(context)
            ):
                self._send_transcript_to_osc(
                    utterance_id, text, context["source"], context["recordingGeneration"]
                )

    def _on_translation_status(self, provider: str, status: str) -> None:
        self._emit(
            "translation.status",
            {"provider": provider, "status": status},
        )

    def _on_translation_error(
        self, job_id: str, message: str, context: dict[str, Any]
    ) -> None:
        self._emit(
            "translation.error",
            {"jobId": job_id, "message": message, "context": context},
        )
        if context.get("sendToOsc"):
            utterance_id = context.get("utteranceId")
            original_text = context.get("originalText")
            with self._lock:
                if (
                    isinstance(utterance_id, str)
                    and isinstance(original_text, str)
                    and self._translation_context_active(context)
                ):
                    self._send_transcript_to_osc(
                        utterance_id,
                        original_text,
                        context["source"],
                        context["recordingGeneration"],
                    )

    def _translation_context_active(self, context: dict[str, Any]) -> bool:
        source = context.get("source")
        return (
            self._lifecycle == "ready"
            and isinstance(source, str)
            and source in self._recording_generations
            and context.get("recordingGeneration") == self._recording_generations[source]
        )

    def _submit_transcript_translation(
        self, utterance_id: str, text: str, source: str, generation: int
    ) -> bool:
        translation = self._translation
        settings = self._settings["translation"]
        if not settings["enabled"] or translation is None:
            return False

        local_language = settings["sourceLanguage"]
        if local_language == "auto":
            local_language = self._settings["speech"]["language"]
        if source == "speaker":
            source_language = settings["targetLanguage"]
            target_language = local_language
        else:
            source_language = settings["sourceLanguage"]
            target_language = settings["targetLanguage"]
        options: dict[str, Any] = {}
        if settings["provider"] == "onnx":
            options = {"model": settings["model"]}
        elif settings["provider"] == "libretranslate":
            options = {
                "endpoint": settings["endpoint"],
                "apiKey": settings["apiKey"],
            }
        elif settings["provider"] == "deepl":
            options = {
                "plan": settings["deeplPlan"],
                "apiKey": settings["deeplApiKey"],
            }
        job_id = translation.submit(
            provider_id=settings["provider"],
            text=text,
            source_language=source_language,
            target_language=target_language,
            options=options,
            context={
                "utteranceId": utterance_id,
                "source": source,
                "originalText": text,
                "sendToOsc": source == "voice",
                "recordingGeneration": generation,
            },
        )
        if job_id is None:
            self._emit(
                "translation.error",
                {
                    "jobId": "",
                    "message": "翻譯佇列已滿，已傳送原始文字",
                    "context": {"utteranceId": utterance_id},
                },
            )
            return False
        return True

    def _send_transcript_to_osc(
        self, utterance_id: str, text: str, source: str | None = None, generation: int = 0
    ) -> bool:
        osc = self._osc
        if osc is None:
            self._emit(
                "osc.skipped",
                {"utteranceId": utterance_id, "kind": "text", "reason": "closed"},
            )
            return False
        return osc.send_text(
            text, utterance_id=utterance_id, source=source, generation=generation
        )

    @staticmethod
    def _required_string(params: dict[str, Any], name: str) -> str:
        value = params.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ServiceError("INVALID_PARAMS", f"{name} 必須是非空字串")
        return value.strip()

    def _state(self) -> dict[str, Any]:
        return {
            "sessionId": self._session_id,
            "seq": self._seq,
            "backend": self._lifecycle,
            "recording": self._recording,
            "recordingSource": self._recording_source,
            "recordings": deepcopy(self._recordings),
            "models": deepcopy(self._models),
            "settings": deepcopy(self._settings),
        }

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            payload = {
                "v": 1,
                "type": "event",
                "sessionId": self._session_id,
                "seq": self._seq,
                "event": event,
                "data": data,
            }
        self._emit_handler(event, payload)
