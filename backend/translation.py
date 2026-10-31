from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from backend.nllb import MODEL_ID, NllbTranslator
from backend.i18n import t


@dataclass(frozen=True)
class TranslationRequest:
    text: str
    source_language: str
    target_language: str
    # The utterance as 16 kHz mono PCM16, for providers that translate speech.
    audio: bytes | None = None


@dataclass(frozen=True)
class TranslationProviderInfo:
    provider_id: str
    label: str
    local: bool
    audio: bool = False


class TranslationProvider(Protocol):
    def translate(self, request: TranslationRequest) -> str: ...
    def close(self) -> None: ...


ProviderBuilder = Callable[[dict[str, Any]], TranslationProvider]


class TranslationProviderFactory:
    """Registry-based factory; providers can be added without changing callers."""

    def __init__(self) -> None:
        self._builders: dict[str, ProviderBuilder] = {}
        self._providers: dict[str, TranslationProviderInfo] = {}

    def register(
        self,
        provider_id: str,
        *,
        label: str,
        local: bool,
        builder: ProviderBuilder,
        audio: bool = False,
    ) -> None:
        if not provider_id or provider_id in self._builders:
            raise ValueError(f"翻譯提供者已存在或名稱無效: {provider_id}")
        self._builders[provider_id] = builder
        self._providers[provider_id] = TranslationProviderInfo(
            provider_id=provider_id,
            label=label,
            local=local,
            audio=audio,
        )

    def create(
        self, provider_id: str, options: dict[str, Any] | None = None
    ) -> TranslationProvider:
        builder = self._builders.get(provider_id)
        if builder is None:
            raise ValueError(f"不支援的翻譯提供者: {provider_id}")
        return builder(dict(options or {}))

    def available(self) -> list[TranslationProviderInfo]:
        return list(self._providers.values())


class OnnxTranslationProvider:
    """Local INT8 NLLB, always on the CPU ONNX Runtime provider."""

    _LANGUAGES = {
        "zh": "zho_Hant",
        "zh-TW": "zho_Hant",
        "zh-CN": "zho_Hans",
        "en": "eng_Latn",
        "ja": "jpn_Jpan",
        "ko": "kor_Hang",
        "fr": "fra_Latn",
        "de": "deu_Latn",
        "es": "spa_Latn",
    }

    def __init__(self, options: dict[str, Any]) -> None:
        model_name = _string_option(options, "model", MODEL_ID)
        if model_name != MODEL_ID:
            raise ValueError(t("backend.translation.unsupportedModel"))
        self._translator = NllbTranslator()

    def translate(self, request: TranslationRequest) -> str:
        return self._translator.translate(
            request.text,
            self._language_code(request.source_language),
            self._language_code(request.target_language),
        )

    def close(self) -> None:
        self._translator.close()

    @classmethod
    def _language_code(cls, language: str) -> str:
        if language == "auto":
            raise ValueError(t("backend.translation.needsSource"))
        return cls._LANGUAGES.get(language, language)


class LibreTranslateProvider:
    """LibreTranslate-compatible HTTP provider."""

    def __init__(self, options: dict[str, Any]) -> None:
        endpoint = _string_option(options, "endpoint", "http://127.0.0.1:5000")
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(t("backend.translation.invalidEndpoint"))
        if parsed.username or parsed.password:
            raise ValueError(t("backend.translation.endpointCredentials"))
        self._endpoint = endpoint.rstrip("/")
        if not self._endpoint.endswith("/translate"):
            self._endpoint += "/translate"
        self._api_key = str(
            options.get("apiKey") or os.environ.get("LIBRETRANSLATE_API_KEY", "")
        )
        timeout = options.get("timeoutSeconds", 15)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeoutSeconds 必須是數字")
        self._timeout = min(max(float(timeout), 1.0), 120.0)

    def translate(self, request: TranslationRequest) -> str:
        payload: dict[str, object] = {
            "q": request.text,
            "source": request.source_language,
            "target": request.target_language,
            "format": "text",
        }
        if self._api_key:
            payload["api_key"] = self._api_key
        url_request = UrlRequest(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(url_request, timeout=self._timeout) as response:  # noqa: S310
            result = json.loads(response.read().decode("utf-8"))
        translated = result.get("translatedText") if isinstance(result, dict) else None
        if not isinstance(translated, str) or not translated.strip():
            raise RuntimeError(
                t("backend.translation.invalidResult", provider="LibreTranslate")
            )
        return translated.strip()

    def close(self) -> None:
        return


class DeepLTranslationProvider:
    """DeepL API Free/Pro provider using header-based authentication."""

    _ENDPOINTS = {
        "free": "https://api-free.deepl.com/v2/translate",
        "pro": "https://api.deepl.com/v2/translate",
    }
    _SOURCE_LANGUAGES = {
        "zh": "ZH",
        "zh-TW": "ZH",
        "zh-CN": "ZH",
        "en": "EN",
        "ja": "JA",
        "ko": "KO",
        "fr": "FR",
        "de": "DE",
        "es": "ES",
    }
    _TARGET_LANGUAGES = {
        **_SOURCE_LANGUAGES,
        "zh": "ZH-HANT",
        "zh-TW": "ZH-HANT",
        "zh-CN": "ZH-HANS",
        "en": "EN-US",
    }

    def __init__(self, options: dict[str, Any]) -> None:
        plan = _string_option(options, "plan", "free").lower()
        if plan not in self._ENDPOINTS:
            raise ValueError("DeepL 方案必須是 free 或 pro")
        self._endpoint = self._ENDPOINTS[plan]
        self._api_key = str(
            options.get("apiKey") or os.environ.get("DEEPL_API_KEY", "")
        ).strip()
        if not self._api_key:
            raise ValueError(t("backend.translation.deeplKeyMissing"))
        timeout = options.get("timeoutSeconds", 15)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeoutSeconds 必須是數字")
        self._timeout = min(max(float(timeout), 1.0), 120.0)

    def translate(self, request: TranslationRequest) -> str:
        payload: dict[str, object] = {
            "text": [request.text],
            "target_lang": self._target_language(request.target_language),
        }
        if request.source_language != "auto":
            payload["source_lang"] = self._source_language(request.source_language)
        url_request = UrlRequest(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"DeepL-Auth-Key {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "VRC-V2T-OSC/1.0",
            },
            method="POST",
        )
        with urlopen(url_request, timeout=self._timeout) as response:  # noqa: S310
            result = json.loads(response.read().decode("utf-8"))
        translations = result.get("translations") if isinstance(result, dict) else None
        translated = (
            translations[0].get("text")
            if isinstance(translations, list)
            and translations
            and isinstance(translations[0], dict)
            else None
        )
        if not isinstance(translated, str) or not translated.strip():
            raise RuntimeError(t("backend.translation.invalidResult", provider="DeepL"))
        return translated.strip()

    def close(self) -> None:
        return

    @classmethod
    def _source_language(cls, language: str) -> str:
        return cls._SOURCE_LANGUAGES.get(language, language.upper())

    @classmethod
    def _target_language(cls, language: str) -> str:
        return cls._TARGET_LANGUAGES.get(language, language.upper())


_LIVE_SAMPLE_RATE = 16000
# Both services ask for audio in chunks of about 100 ms.
_LIVE_CHUNK_BYTES = _LIVE_SAMPLE_RATE // 10 * 2
# How long to keep listening after a finished turn; a long utterance can come
# back as several translated sentences, and transcripts arrive unordered.
_LIVE_SETTLE_SECONDS = 0.6
# Gemini translates as one continuous stream and never ends a turn, so an
# utterance is done once its output has been quiet this long.
_LIVE_QUIET_SECONDS = 1.0
# Before any translation arrives, give up after this long without any message:
# the clip was most likely noise.
_LIVE_IDLE_SECONDS = 5.0


@dataclass(frozen=True)
class SpeechTranslation:
    """What a speech translation service heard, and its translation."""

    text: str
    source_text: str
    source_language: str | None


class GeminiLiveTranslateProvider:
    """Gemini 3.5 Live Translate: speech in, transcripts out.

    The model only accepts audio, so each utterance opens its own Live API
    session. The translated speech it returns is discarded; the input
    transcription replaces Whisper and the output transcription is the
    translation.
    """

    _ENDPOINT = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
    )
    _MODEL = "models/gemini-3.5-live-translate-preview"
    _TARGET_LANGUAGES = {"zh": "zh-Hant", "zh-TW": "zh-Hant", "zh-CN": "zh-Hans"}

    def __init__(self, options: dict[str, Any]) -> None:
        self._api_key = str(
            options.get("apiKey") or os.environ.get("GEMINI_API_KEY", "")
        ).strip()
        if not self._api_key:
            raise ValueError(t("backend.translation.geminiKeyMissing"))
        self._timeout = _timeout_option(options, 30)

    def translate(self, request: TranslationRequest) -> str:
        raise ValueError(t("backend.translation.audioOnly", provider="Gemini"))

    def translate_speech(self, request: TranslationRequest) -> SpeechTranslation:
        audio = _require_audio(request, "Gemini")
        from websockets.exceptions import ConnectionClosed
        from websockets.sync.client import connect

        setup = {
            "setup": {
                "model": self._MODEL,
                # Transcription belongs to the setup itself; the server rejects
                # it inside generationConfig.
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "translationConfig": {
                        "targetLanguageCode": self._TARGET_LANGUAGES.get(
                            request.target_language, request.target_language
                        ),
                        # Without echo, speech already in the target language
                        # produces nothing and the request would time out.
                        "echoTargetLanguage": True,
                    },
                },
            }
        }
        deadline = time.monotonic() + self._timeout
        heard: list[str] = []
        translated: list[str] = []
        events: list[str] = []
        language: str | None = None
        try:
            with connect(
                f"{self._ENDPOINT}?key={quote(self._api_key, safe='')}",
                open_timeout=self._timeout,
                max_size=None,
            ) as websocket:
                websocket.send(json.dumps(setup))
                while True:
                    message = _receive_json(websocket, deadline)
                    if message is None:
                        raise TimeoutError(t("backend.translation.liveTimeout"))
                    if "setupComplete" in message:
                        break
                for chunk in _audio_chunks(audio):
                    websocket.send(
                        json.dumps(
                            {
                                "realtimeInput": {
                                    "audio": {
                                        "data": base64.b64encode(chunk).decode("ascii"),
                                        "mimeType": f"audio/pcm;rate={_LIVE_SAMPLE_RATE}",
                                    }
                                }
                            }
                        )
                    )
                # Trailing silence lets the server's activity detection close
                # the turn; the utterance is cut right after the speech ends.
                websocket.send(
                    json.dumps(
                        {
                            "realtimeInput": {
                                "audio": {
                                    "data": base64.b64encode(
                                        bytes(_LIVE_SAMPLE_RATE * 2)
                                    ).decode("ascii"),
                                    "mimeType": f"audio/pcm;rate={_LIVE_SAMPLE_RATE}",
                                }
                            }
                        }
                    )
                )
                websocket.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                activity_at = time.monotonic()
                while True:
                    quiet = (
                        _LIVE_QUIET_SECONDS
                        if "".join(translated).strip()
                        else _LIVE_IDLE_SECONDS
                    )
                    message = _receive_json(
                        websocket, min(deadline, activity_at + quiet)
                    )
                    if message is None:
                        break
                    events.append(_describe_gemini_message(message))
                    content = message.get("serverContent")
                    if not isinstance(content, dict):
                        continue
                    source = content.get("inputTranscription")
                    if isinstance(source, dict) and isinstance(source.get("text"), str):
                        heard.append(source["text"])
                        if isinstance(source.get("languageCode"), str):
                            language = source["languageCode"]
                    output = content.get("outputTranscription")
                    if isinstance(output, dict) and isinstance(output.get("text"), str):
                        translated.append(output["text"])
                    # Transcripts arrive well ahead of the translated speech,
                    # which streams at speaking pace; waiting for the audio to
                    # finish would add its whole duration. Audio only counts as
                    # progress until the first translated text shows up.
                    if (
                        source is not None
                        or output is not None
                        or ("modelTurn" in content and not "".join(translated).strip())
                    ):
                        activity_at = time.monotonic()
                    if (
                        content.get("turnComplete") or content.get("generationComplete")
                    ) and "".join(translated).strip():
                        deadline = min(deadline, time.monotonic() + _LIVE_SETTLE_SECONDS)
        except ConnectionClosed as exc:
            if not "".join(translated).strip():
                raise RuntimeError(_closed_message("Gemini", exc)) from exc
        text = "".join(translated).strip()
        summary = _collapse_events(events)
        logging.info(
            "Gemini Live: %.2fs audio, %d message(s): %s",
            len(audio) / 2 / _LIVE_SAMPLE_RATE,
            len(events),
            summary,
        )
        source_text = "".join(heard).strip()
        if not text and not source_text:
            # Nothing was said: noise the local VAD mistook for speech.
            return SpeechTranslation(text="", source_text="", source_language=None)
        if not text:
            # Say what did come back; nothing else reaches the interface.
            raise RuntimeError(
                t(
                    "backend.translation.liveNoText",
                    provider="Gemini",
                    events=summary or t("backend.translation.liveNothing"),
                )
            )
        return SpeechTranslation(
            text=text,
            source_text=source_text,
            source_language=_primary_language(language),
        )

    def close(self) -> None:
        return


class QwenLiveTranslateProvider:
    """Qwen3.5 LiveTranslate realtime API with text-only output.

    Server VAD is off: the backend has already cut the utterance, so it is
    committed as one turn. The service's own recognition replaces Whisper.
    """

    _MODEL = "qwen3.5-livetranslate-flash-realtime"
    _TRANSCRIPTION_MODEL = "qwen3-asr-flash-realtime"
    _LANGUAGES = {"zh-TW": "zh", "zh-CN": "zh"}
    _TRANSCRIPTION_DONE = {
        "conversation.item.input_audio_transcription.completed",
        "conversation.item.input_audio_transcription.failed",
    }

    def __init__(self, options: dict[str, Any]) -> None:
        endpoint = _string_option(
            options, "endpoint", "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
        )
        parsed = urlparse(endpoint)
        if parsed.scheme != "wss" or not parsed.netloc:
            raise ValueError(t("backend.translation.invalidQwenEndpoint"))
        if parsed.username or parsed.password:
            raise ValueError(t("backend.translation.endpointCredentials"))
        separator = "&" if parsed.query else "?"
        self._url = f"{endpoint}{separator}model={self._MODEL}"
        self._api_key = str(
            options.get("apiKey") or os.environ.get("DASHSCOPE_API_KEY", "")
        ).strip()
        if not self._api_key:
            raise ValueError(t("backend.translation.qwenKeyMissing"))
        self._timeout = _timeout_option(options, 30)

    def translate(self, request: TranslationRequest) -> str:
        raise ValueError(t("backend.translation.audioOnly", provider="Qwen"))

    def translate_speech(self, request: TranslationRequest) -> SpeechTranslation:
        audio = _require_audio(request, "Qwen")
        from websockets.exceptions import ConnectionClosed
        from websockets.sync.client import connect

        transcription: dict[str, object] = {"model": self._TRANSCRIPTION_MODEL}
        if request.source_language != "auto":
            transcription["language"] = self._language(request.source_language)
        session: dict[str, object] = {
            "modalities": ["text"],
            "input_audio_format": "pcm",
            "sample_rate": _LIVE_SAMPLE_RATE,
            "turn_detection": None,
            "input_audio_transcription": transcription,
            "translation": {"language": self._language(request.target_language)},
        }
        deadline = time.monotonic() + self._timeout
        translated: dict[str, Any] | None = None
        heard: dict[str, Any] | None = None
        try:
            with connect(
                self._url,
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
                open_timeout=self._timeout,
                max_size=None,
            ) as websocket:
                self._send(websocket, "session.update", session=session)
                while self._next_event(websocket, deadline).get("type") != (
                    "session.updated"
                ):
                    pass
                for chunk in _audio_chunks(audio):
                    self._send(
                        websocket,
                        "input_audio_buffer.append",
                        audio=base64.b64encode(chunk).decode("ascii"),
                    )
                self._send(websocket, "input_audio_buffer.commit")
                # The translation and the transcript of what was heard arrive
                # in no fixed order; wait for both.
                while translated is None or heard is None:
                    try:
                        message = self._next_event(websocket, deadline)
                    except TimeoutError:
                        if translated is None:
                            raise
                        break
                    if message.get("type") == "response.text.done":
                        translated = message
                        deadline = min(deadline, time.monotonic() + _LIVE_SETTLE_SECONDS)
                    elif message.get("type") in self._TRANSCRIPTION_DONE:
                        heard = message
                self._send(websocket, "session.finish")
        except ConnectionClosed as exc:
            if translated is None:
                raise RuntimeError(_closed_message("Qwen", exc)) from exc
        text = translated.get("text") if translated else None
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(t("backend.translation.invalidResult", provider="Qwen"))
        source_text = heard.get("transcript") if heard else None
        language = heard.get("language") if heard else None
        return SpeechTranslation(
            text=text.strip(),
            source_text=source_text.strip() if isinstance(source_text, str) else "",
            source_language=_primary_language(
                language if isinstance(language, str) else None
            ),
        )

    def close(self) -> None:
        return

    @staticmethod
    def _send(websocket: Any, event_type: str, **fields: object) -> None:
        websocket.send(
            json.dumps(
                {"event_id": f"event_{uuid.uuid4().hex}", "type": event_type, **fields}
            )
        )

    @staticmethod
    def _next_event(websocket: Any, deadline: float) -> dict[str, Any]:
        """The next server event; errors and failed responses raise."""
        message = _receive_json(websocket, deadline)
        if message is None:
            raise TimeoutError(t("backend.translation.liveTimeout"))
        current = message.get("type")
        if current == "error":
            error = message.get("error")
            detail = error.get("message") if isinstance(error, dict) else None
            raise RuntimeError(
                t(
                    "backend.translation.liveFailed",
                    provider="Qwen",
                    error=detail or json.dumps(error, ensure_ascii=False),
                )
            )
        if current == "response.done":
            response = message.get("response")
            status = response.get("status") if isinstance(response, dict) else None
            if status not in {None, "completed"}:
                raise RuntimeError(
                    t("backend.translation.liveFailed", provider="Qwen", error=status)
                )
        return message

    @classmethod
    def _language(cls, language: str) -> str:
        return cls._LANGUAGES.get(language, language)


def create_default_translation_factory() -> TranslationProviderFactory:
    factory = TranslationProviderFactory()
    factory.register(
        "onnx",
        # A message key: translated when listed, in the current interface language.
        label="backend.translation.onnxLabel",
        local=True,
        builder=OnnxTranslationProvider,
    )
    factory.register(
        "libretranslate",
        label="LibreTranslate API",
        local=False,
        builder=LibreTranslateProvider,
    )
    factory.register(
        "deepl",
        label="DeepL API",
        local=False,
        builder=DeepLTranslationProvider,
    )
    factory.register(
        "gemini-live",
        label="Gemini 3.5 Live Translate",
        local=False,
        builder=GeminiLiveTranslateProvider,
        audio=True,
    )
    factory.register(
        "qwen-live",
        label="Qwen3.5 LiveTranslate",
        local=False,
        builder=QwenLiveTranslateProvider,
        audio=True,
    )
    return factory


@dataclass(frozen=True)
class _TranslationJob:
    job_id: str
    provider_id: str
    request: TranslationRequest
    options: dict[str, Any]
    context: dict[str, Any]


TranslationResultHandler = Callable[[dict[str, Any]], None]
TranslationStatusHandler = Callable[[str, str], None]
TranslationErrorHandler = Callable[[str, str, dict[str, Any]], None]


class TranslationService:
    """Runs provider work off the protocol thread with bounded backpressure."""

    def __init__(
        self,
        *,
        factory: TranslationProviderFactory | None = None,
        max_queue: int = 10,
        on_result: TranslationResultHandler,
        on_status: TranslationStatusHandler,
        on_error: TranslationErrorHandler,
    ) -> None:
        self._factory = factory or create_default_translation_factory()
        self._on_result = on_result
        self._on_status = on_status
        self._on_error = on_error
        self._queue: queue.Queue[_TranslationJob | None] = queue.Queue(maxsize=max_queue)
        self._providers: dict[str, TranslationProvider] = {}
        self._ready_providers: set[str] = set()
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._worker = threading.Thread(
            target=self._run,
            name="translation-worker",
            daemon=True,
        )
        self._worker.start()

    def providers(self) -> list[dict[str, object]]:
        return [
            {
                "id": provider.provider_id,
                "label": t(provider.label)
                if provider.label.startswith("backend.")
                else provider.label,
                "local": provider.local,
                "audio": provider.audio,
            }
            for provider in self._factory.available()
        ]

    def submit(
        self,
        *,
        provider_id: str,
        text: str,
        source_language: str,
        target_language: str,
        options: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        audio: bytes | None = None,
    ) -> str | None:
        if self._closed.is_set():
            return None
        job = _TranslationJob(
            job_id=str(uuid.uuid4()),
            provider_id=provider_id,
            request=TranslationRequest(
                text=text,
                source_language=source_language,
                target_language=target_language,
                audio=audio,
            ),
            options=dict(options or {}),
            context=dict(context or {}),
        )
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            return None
        return job.job_id

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            self._queue.put_nowait(None)
        self._worker.join(timeout=2.0)
        for provider in self._providers.values():
            provider.close()
        self._providers.clear()
        self._ready_providers.clear()

    def invalidate(self, provider_id: str) -> None:
        prefix = f"{provider_id}:"
        with self._lock:
            matching = [key for key in self._providers if key.startswith(prefix)]
            providers = [self._providers.pop(key) for key in matching]
            self._ready_providers.difference_update(matching)
        for provider in providers:
            provider.close()

    def _run(self) -> None:
        while not self._closed.is_set() or not self._queue.empty():
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            cache_key: str | None = None
            try:
                if job is None:
                    return
                cache_key = self._provider_cache_key(job.provider_id, job.options)
                warming_up = cache_key not in self._ready_providers
                if warming_up:
                    self._on_status(job.provider_id, "loading")
                result: dict[str, Any] = {
                    "jobId": job.job_id,
                    "provider": job.provider_id,
                    "sourceLanguage": job.request.source_language,
                    "targetLanguage": job.request.target_language,
                    "context": job.context,
                }
                with self._lock:
                    provider = self._provider(job.provider_id, job.options, cache_key)
                    if job.request.audio is None:
                        result["text"] = provider.translate(job.request)
                    else:
                        translate_speech = getattr(provider, "translate_speech", None)
                        if translate_speech is None:
                            raise ValueError(
                                t(
                                    "backend.translation.textOnly",
                                    provider=job.provider_id,
                                )
                            )
                        speech: SpeechTranslation = translate_speech(job.request)
                        result["text"] = speech.text
                        result["sourceText"] = speech.source_text
                        if speech.source_language:
                            result["sourceLanguage"] = speech.source_language
                    if warming_up:
                        self._ready_providers.add(cache_key)
                    self._on_status(job.provider_id, "ready")
                self._on_result(result)
            except Exception as exc:
                if job is not None:
                    if cache_key is not None and cache_key not in self._ready_providers:
                        self._on_status(job.provider_id, "failed")
                    self._on_error(job.job_id, str(exc), job.context)
            finally:
                self._queue.task_done()

    def _provider(
        self,
        provider_id: str,
        options: dict[str, Any],
        cache_key: str,
    ) -> TranslationProvider:
        provider = self._providers.get(cache_key)
        if provider is None:
            provider = self._factory.create(provider_id, options)
            self._providers[cache_key] = provider
        return provider

    @staticmethod
    def _provider_cache_key(provider_id: str, options: dict[str, Any]) -> str:
        serialized_options = json.dumps(
            options,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        options_hash = hashlib.sha256(serialized_options.encode("utf-8")).hexdigest()
        return f"{provider_id}:{options_hash}"


def _string_option(options: dict[str, Any], name: str, default: str) -> str:
    value = options.get(name, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必須是非空字串")
    return value.strip()


def _timeout_option(options: dict[str, Any], default: float) -> float:
    timeout = options.get("timeoutSeconds", default)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeoutSeconds 必須是數字")
    return min(max(float(timeout), 1.0), 120.0)


def _require_audio(request: TranslationRequest, provider: str) -> bytes:
    if not request.audio:
        raise ValueError(t("backend.translation.audioOnly", provider=provider))
    return request.audio


def _audio_chunks(audio: bytes) -> list[bytes]:
    return [
        audio[start : start + _LIVE_CHUNK_BYTES]
        for start in range(0, len(audio), _LIVE_CHUNK_BYTES)
    ]


def _receive_json(websocket: Any, deadline: float) -> dict[str, Any] | None:
    """The next JSON message, or None once the deadline has passed."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    try:
        raw = websocket.recv(timeout=remaining)
    except TimeoutError:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    message = json.loads(raw)
    return message if isinstance(message, dict) else {}


def _closed_message(provider: str, exc: Exception) -> str:
    received = getattr(exc, "rcvd", None)
    reason = getattr(received, "reason", "") or str(exc)
    return t("backend.translation.liveFailed", provider=provider, error=reason)


def _primary_language(language: str | None) -> str | None:
    """"zh-Hant" or "en-US" as the bare codes used everywhere else."""
    if not language or not language.strip():
        return None
    return language.strip().split("-")[0].lower()


def _describe_gemini_message(message: dict[str, Any]) -> str:
    """A short, audio-free summary of one Live API server message."""
    content = message.get("serverContent")
    if not isinstance(content, dict):
        return "/".join(key for key in message if key != "usageMetadata") or "{}"
    labels = []
    for key, value in content.items():
        if key in {"inputTranscription", "outputTranscription"} and isinstance(
            value, dict
        ):
            labels.append(f"{key}={str(value.get('text', ''))[:40]!r}")
        elif key == "modelTurn":
            labels.append("modelTurn(audio)")
        else:
            labels.append(key)
    return "serverContent:" + "+".join(labels)


def _collapse_events(events: list[str]) -> str:
    """Joins message summaries, folding repeats such as streamed audio chunks."""
    collapsed: list[list[object]] = []
    for event in events:
        if collapsed and collapsed[-1][0] == event:
            collapsed[-1][1] = int(collapsed[-1][1]) + 1  # type: ignore[call-overload]
        else:
            collapsed.append([event, 1])
    return ", ".join(
        f"{event} x{count}" if count != 1 else str(event) for event, count in collapsed
    )[-600:]
