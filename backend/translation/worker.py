from __future__ import annotations

import hashlib
import json
import queue
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from backend.i18n import t
from backend.translation.base import (
    LocalTranslationProvider,
    TranslationProvider,
    TranslationProviderFactory,
    TranslationRequest,
)


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
        if factory is None:
            from backend.translation.registry import create_default_translation_factory

            factory = create_default_translation_factory()
        self._factory = factory
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
                "kind": provider.kind,
            }
            for provider in self._factory.available()
        ]

    def prepare(self, settings: dict[str, Any]) -> None:
        """Load the enabled local translator before startup completes."""
        info = self._factory.info(settings["provider"])
        if not settings["enabled"] or info is None or not info.local:
            return
        options = self._factory.options_for(info.provider_id, settings)
        key = self._provider_cache_key(info.provider_id, options)
        with self._lock:
            if key in self._ready_providers:
                return
            self._on_status(info.provider_id, "loading")
            try:
                provider = cast(
                    LocalTranslationProvider,
                    self._provider(info.provider_id, options, key),
                )
                provider.load()
            except Exception:
                self._on_status(info.provider_id, "failed")
                raise
            self._ready_providers.add(key)
            self._on_status(info.provider_id, "ready")

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
                result: dict[str, Any] = {
                    "jobId": job.job_id,
                    "provider": job.provider_id,
                    "sourceLanguage": job.request.source_language,
                    "targetLanguage": job.request.target_language,
                    "context": job.context,
                }
                with self._lock:
                    provider = self._provider(job.provider_id, job.options, cache_key)
                    result["text"] = provider.translate(job.request)
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
