from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# text: translates recognized text.
# stream: keeps a live session per audio source and translates as people talk.
ProviderKind = Literal["text", "stream"]
# Builds a provider's options from the "translation" settings section.
OptionsBuilder = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class TranslationRequest:
    text: str
    source_language: str
    target_language: str


def _no_options(_settings: dict[str, Any]) -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class TranslationProviderInfo:
    provider_id: str
    label: str
    local: bool
    kind: ProviderKind = "text"
    options: OptionsBuilder = field(default=_no_options, compare=False)

    @property
    def audio(self) -> bool:
        """Whether the provider listens to speech itself rather than text."""
        return self.kind != "text"


class TranslationProvider(Protocol):
    def translate(self, request: TranslationRequest) -> str: ...
    def close(self) -> None: ...


ProviderBuilder = Callable[[dict[str, Any]], Any]


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
        kind: ProviderKind = "text",
        options: OptionsBuilder = _no_options,
    ) -> None:
        if not provider_id or provider_id in self._builders:
            raise ValueError(f"翻譯提供者已存在或名稱無效: {provider_id}")
        self._builders[provider_id] = builder
        self._providers[provider_id] = TranslationProviderInfo(
            provider_id=provider_id,
            label=label,
            local=local,
            kind=kind,
            options=options,
        )

    def create(self, provider_id: str, options: dict[str, Any] | None = None) -> Any:
        builder = self._builders.get(provider_id)
        if builder is None:
            raise ValueError(f"不支援的翻譯提供者: {provider_id}")
        return builder(dict(options or {}))

    def available(self) -> list[TranslationProviderInfo]:
        return list(self._providers.values())

    def info(self, provider_id: str) -> TranslationProviderInfo | None:
        return self._providers.get(provider_id)

    def options_for(self, provider_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        """The provider's options taken from the "translation" settings section."""
        info = self._providers.get(provider_id)
        return info.options(settings) if info is not None else {}


def string_option(options: dict[str, Any], name: str, default: str) -> str:
    value = options.get(name, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必須是非空字串")
    return value.strip()


def timeout_option(options: dict[str, Any], default: float) -> float:
    timeout = options.get("timeoutSeconds", default)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeoutSeconds 必須是數字")
    return min(max(float(timeout), 1.0), 120.0)


