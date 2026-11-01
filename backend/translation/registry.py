from __future__ import annotations

from backend.translation.base import TranslationProviderFactory
from backend.translation.gemini_stream import GeminiStreamTranslateProvider
from backend.translation.qwen_stream import QwenStreamTranslateProvider
from backend.translation.text import (
    DeepLTranslationProvider,
    LibreTranslateProvider,
    OnnxTranslationProvider,
)


def create_default_translation_factory() -> TranslationProviderFactory:
    factory = TranslationProviderFactory()
    factory.register(
        "onnx",
        # A message key: translated when listed, in the current interface language.
        label="backend.translation.onnxLabel",
        local=True,
        builder=OnnxTranslationProvider,
        options=lambda settings: {"model": settings["model"]},
    )
    factory.register(
        "libretranslate",
        label="LibreTranslate API",
        local=False,
        builder=LibreTranslateProvider,
        options=lambda settings: {
            "endpoint": settings["endpoint"],
            "apiKey": settings["apiKey"],
        },
    )
    factory.register(
        "deepl",
        label="DeepL API",
        local=False,
        builder=DeepLTranslationProvider,
        options=lambda settings: {
            "plan": settings["deeplPlan"],
            "apiKey": settings["deeplApiKey"],
        },
    )
    factory.register(
        "gemini-live",
        label="Gemini 3.5 Live Translate",
        local=False,
        builder=GeminiStreamTranslateProvider,
        kind="stream",
        options=lambda settings: {"apiKey": settings["geminiApiKey"]},
    )
    factory.register(
        "qwen-live",
        label="Qwen3.5 LiveTranslate",
        local=False,
        builder=QwenStreamTranslateProvider,
        kind="stream",
        options=lambda settings: {
            "endpoint": settings["qwenEndpoint"],
            "apiKey": settings["qwenApiKey"],
        },
    )
    return factory
