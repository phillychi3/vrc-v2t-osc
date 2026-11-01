from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from backend.contracts import Pipeline
from backend.core.settings import local_language
from backend.translation.base import ProviderKind, TranslationProviderFactory

PipelineBuilder = Callable[[ProviderKind, dict[str, Any]], Pipeline]
FailureHandler = Callable[[str], None]


def pipeline_kind(
    providers: TranslationProviderFactory, settings: dict[str, Any]
) -> ProviderKind:
    """Whisper plus text translation, unless a streaming service takes over."""
    translation = settings["translation"]
    info = providers.info(translation["provider"]) if translation["enabled"] else None
    return info.kind if info is not None else "text"


class PipelineSelector:
    """Owns the pipeline the current settings call for."""

    def __init__(
        self,
        *,
        providers: TranslationProviderFactory,
        build: PipelineBuilder,
        on_failure: FailureHandler,
    ) -> None:
        self._providers = providers
        self._build = build
        self._on_failure = on_failure
        self._current: Pipeline | None = None
        self._key: tuple[Any, ...] | None = None

    @property
    def current(self) -> Pipeline | None:
        return self._current

    def matches(self, settings: dict[str, Any]) -> bool:
        """Whether the current pipeline can carry on under these settings."""
        return self._current is not None and self._key == self._pipeline_key(settings)

    def replace(self, settings: dict[str, Any]) -> None:
        """Closes the current pipeline, then builds and loads the new one."""
        previous, self._current = self._current, None
        if previous is not None:
            previous.close()
        kind = pipeline_kind(self._providers, settings)
        self._key = self._pipeline_key(settings)
        try:
            pipeline = self._build(kind, settings)
        except Exception as exc:
            logging.warning("Could not set up the %s pipeline: %s", kind, exc)
            self._on_failure(str(exc))
            return
        self._current = pipeline
        pipeline.load()

    def close(self) -> None:
        previous, self._current = self._current, None
        if previous is not None:
            previous.close()

    def _pipeline_key(self, settings: dict[str, Any]) -> tuple[Any, ...]:
        """Changes exactly when the pipeline has to be rebuilt."""
        kind = pipeline_kind(self._providers, settings)
        if kind == "text":
            return (kind, settings["speech"]["model"])
        translation = settings["translation"]
        options = self._providers.options_for(translation["provider"], translation)

        return (
            kind,
            translation["provider"],
            json.dumps(options, sort_keys=True),
            translation["targetLanguage"],
            local_language(settings),
        )
