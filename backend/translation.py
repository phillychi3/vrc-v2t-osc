from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from backend.inference import inference_lock


@dataclass(frozen=True)
class TranslationRequest:
    text: str
    source_language: str
    target_language: str


@dataclass(frozen=True)
class TranslationProviderInfo:
    provider_id: str
    label: str
    local: bool


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
    ) -> None:
        if not provider_id or provider_id in self._builders:
            raise ValueError(f"翻譯提供者已存在或名稱無效: {provider_id}")
        self._builders[provider_id] = builder
        self._providers[provider_id] = TranslationProviderInfo(
            provider_id=provider_id,
            label=label,
            local=local,
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


class TransformersTranslationProvider:
    """Local Hugging Face translation pipeline, loaded on first use."""

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
        self._model_name = _string_option(
            options,
            "model",
            "facebook/nllb-200-distilled-600M",
        )
        self._pipeline: Any = None
        self._lock = threading.Lock()

    def translate(self, request: TranslationRequest) -> str:
        source = self._language_code(request.source_language)
        target = self._language_code(request.target_language)
        pipeline = self._get_pipeline()
        with inference_lock():
            result = pipeline(
                request.text,
                src_lang=source,
                tgt_lang=target,
                truncation=True,
            )
        if not isinstance(result, list) or not result:
            raise RuntimeError("本機翻譯模型沒有回傳結果")
        translated = result[0].get("translation_text")
        if not isinstance(translated, str) or not translated.strip():
            raise RuntimeError("本機翻譯模型回傳了無效文字")
        return translated.strip()

    def close(self) -> None:
        with self._lock:
            self._pipeline = None

    def _get_pipeline(self) -> Any:
        with self._lock:
            if self._pipeline is None:
                import torch
                from transformers import pipeline

                device = 0 if torch.cuda.is_available() else -1
                # Loading ~600M parameters onto the GPU must not overlap with
                # inference on the speech or emotion worker threads.
                with inference_lock():
                    self._pipeline = pipeline(
                        "translation",
                        model=self._model_name,
                        device=device,
                    )
            return self._pipeline

    @classmethod
    def _language_code(cls, language: str) -> str:
        if language == "auto":
            raise ValueError("本機 Transformers 翻譯需要明確的來源語言")
        return cls._LANGUAGES.get(language, language)


class LibreTranslateProvider:
    """LibreTranslate-compatible HTTP provider."""

    def __init__(self, options: dict[str, Any]) -> None:
        endpoint = _string_option(options, "endpoint", "http://127.0.0.1:5000")
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("LibreTranslate endpoint 必須是有效的 HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("LibreTranslate endpoint 不可包含帳號或密碼")
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
            raise RuntimeError("LibreTranslate 回傳了無效結果")
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
            raise ValueError("DeepL API Key 不可為空")
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
            raise RuntimeError("DeepL 回傳了無效結果")
        return translated.strip()

    def close(self) -> None:
        return

    @classmethod
    def _source_language(cls, language: str) -> str:
        return cls._SOURCE_LANGUAGES.get(language, language.upper())

    @classmethod
    def _target_language(cls, language: str) -> str:
        return cls._TARGET_LANGUAGES.get(language, language.upper())


def create_default_translation_factory() -> TranslationProviderFactory:
    factory = TranslationProviderFactory()
    factory.register(
        "transformers",
        label="本機 Transformers",
        local=True,
        builder=TransformersTranslationProvider,
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
                "label": provider.label,
                "local": provider.local,
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
                with self._lock:
                    provider = self._provider(job.provider_id, job.options, cache_key)
                    translated = provider.translate(job.request)
                    if warming_up:
                        self._ready_providers.add(cache_key)
                    self._on_status(job.provider_id, "ready")
                self._on_result(
                    {
                        "jobId": job.job_id,
                        "provider": job.provider_id,
                        "text": translated,
                        "sourceLanguage": job.request.source_language,
                        "targetLanguage": job.request.target_language,
                        "context": job.context,
                    }
                )
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
