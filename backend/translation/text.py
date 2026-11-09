from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from backend.i18n import t
from backend.nllb import MODEL_ID, NllbTranslator
from backend.translation.base import TranslationRequest, string_option


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
        model_name = string_option(options, "model", MODEL_ID)
        if model_name != MODEL_ID:
            raise ValueError(t("backend.translation.unsupportedModel"))
        self._translator = NllbTranslator()

    def load(self) -> None:
        self._translator.load()

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
        endpoint = string_option(options, "endpoint", "http://127.0.0.1:5000")
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
        plan = string_option(options, "plan", "free").lower()
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
