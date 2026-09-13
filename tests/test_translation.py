import json
import threading
import unittest
from unittest.mock import patch

from backend.translation import (
    DeepLTranslationProvider,
    LibreTranslateProvider,
    TranslationProviderFactory,
    TranslationRequest,
    TranslationService,
)


class FakeProvider:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.closed = False

    def translate(self, request: TranslationRequest) -> str:
        return f"{self.prefix}:{request.text}"

    def close(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class TranslationTests(unittest.TestCase):
    def test_invalidating_provider_closes_cached_models(self) -> None:
        created: list[FakeProvider] = []
        factory = TranslationProviderFactory()

        def build(options: dict[str, object]) -> FakeProvider:
            provider = FakeProvider(str(options["model"]))
            created.append(provider)
            return provider

        factory.register("fake", label="Fake", local=True, builder=build)
        completed = threading.Event()
        service = TranslationService(
            factory=factory,
            on_result=lambda _result: completed.set(),
            on_status=lambda *_args: None,
            on_error=lambda *_args: completed.set(),
        )
        service.submit(
            provider_id="fake",
            text="hello",
            source_language="en",
            target_language="zh",
            options={"model": "first"},
        )
        self.assertTrue(completed.wait(1.0))

        service.invalidate("fake")

        self.assertTrue(created[0].closed)
        service.close()

    def test_factory_supports_multiple_registered_methods(self) -> None:
        factory = TranslationProviderFactory()
        factory.register(
            "first",
            label="First",
            local=True,
            builder=lambda options: FakeProvider(str(options.get("prefix", "one"))),
        )
        factory.register(
            "second",
            label="Second",
            local=False,
            builder=lambda _options: FakeProvider("two"),
        )

        self.assertEqual(
            [provider.provider_id for provider in factory.available()],
            ["first", "second"],
        )
        provider = factory.create("first", {"prefix": "custom"})
        result = provider.translate(TranslationRequest("hello", "en", "zh"))
        self.assertEqual(result, "custom:hello")

    def test_translation_service_returns_result_asynchronously(self) -> None:
        factory = TranslationProviderFactory()
        factory.register(
            "fake",
            label="Fake",
            local=True,
            builder=lambda _options: FakeProvider("translated"),
        )
        completed = threading.Event()
        results: list[dict[str, object]] = []
        statuses: list[tuple[str, str]] = []
        service = TranslationService(
            factory=factory,
            on_result=lambda result: (results.append(result), completed.set()),
            on_status=lambda provider, status: statuses.append((provider, status)),
            on_error=lambda *_args: completed.set(),
        )

        job_id = service.submit(
            provider_id="fake",
            text="hello",
            source_language="en",
            target_language="zh",
            context={"utteranceId": "utt-1"},
        )

        self.assertIsNotNone(job_id)
        self.assertTrue(completed.wait(1.0))
        self.assertEqual(results[0]["text"], "translated:hello")
        self.assertEqual(results[0]["context"], {"utteranceId": "utt-1"})
        self.assertEqual(statuses, [("fake", "loading"), ("fake", "ready")])
        service.close()

    def test_translation_service_reports_provider_failure(self) -> None:
        factory = TranslationProviderFactory()
        completed = threading.Event()
        statuses: list[tuple[str, str]] = []
        errors: list[tuple[str, str]] = []
        service = TranslationService(
            factory=factory,
            on_result=lambda _result: completed.set(),
            on_status=lambda provider, status: statuses.append((provider, status)),
            on_error=lambda job_id, message, _context: (
                errors.append((job_id, message)),
                completed.set(),
            ),
        )

        job_id = service.submit(
            provider_id="missing",
            text="hello",
            source_language="en",
            target_language="zh",
        )

        self.assertIsNotNone(job_id)
        self.assertTrue(completed.wait(1.0))
        self.assertEqual(statuses, [("missing", "loading"), ("missing", "failed")])
        self.assertEqual(errors[0][0], job_id)
        self.assertIn("不支援的翻譯提供者", errors[0][1])
        service.close()

    def test_libretranslate_provider_uses_compatible_json_contract(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request: object, timeout: float) -> FakeResponse:
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse({"translatedText": "你好"})

        provider = LibreTranslateProvider(
            {"endpoint": "http://localhost:5000", "timeoutSeconds": 3}
        )
        with patch("backend.translation.urlopen", fake_urlopen):
            result = provider.translate(TranslationRequest("hello", "en", "zh"))

        request = captured["request"]
        payload = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
        self.assertEqual(result, "你好")
        self.assertEqual(payload["q"], "hello")
        self.assertEqual(payload["source"], "en")
        self.assertEqual(payload["target"], "zh")
        self.assertEqual(captured["timeout"], 3.0)

    def test_deepl_provider_uses_free_endpoint_header_and_language_mapping(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request: object, timeout: float) -> FakeResponse:
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse({"translations": [{"text": "Hello"}]})

        provider = DeepLTranslationProvider(
            {"plan": "free", "apiKey": "test-key:fx", "timeoutSeconds": 4}
        )
        with patch("backend.translation.urlopen", fake_urlopen):
            result = provider.translate(TranslationRequest("你好", "auto", "en"))

        request = captured["request"]
        payload = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
        self.assertEqual(result, "Hello")
        self.assertEqual(request.full_url, "https://api-free.deepl.com/v2/translate")  # type: ignore[attr-defined]
        self.assertEqual(
            request.get_header("Authorization"),  # type: ignore[attr-defined]
            "DeepL-Auth-Key test-key:fx",
        )
        self.assertEqual(payload, {"text": ["你好"], "target_lang": "EN-US"})
        self.assertEqual(captured["timeout"], 4.0)

    def test_deepl_provider_maps_traditional_chinese_target(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request: object, timeout: float) -> FakeResponse:
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse({"translations": [{"text": "你好"}]})

        provider = DeepLTranslationProvider({"plan": "pro", "apiKey": "test-key"})
        with patch("backend.translation.urlopen", fake_urlopen):
            provider.translate(TranslationRequest("hello", "en", "zh"))

        request = captured["request"]
        payload = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
        self.assertEqual(request.full_url, "https://api.deepl.com/v2/translate")  # type: ignore[attr-defined]
        self.assertEqual(payload["source_lang"], "EN")
        self.assertEqual(payload["target_lang"], "ZH-HANT")

    def test_deepl_provider_requires_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "API Key 不可為空"):
                DeepLTranslationProvider({"plan": "free"})


if __name__ == "__main__":
    unittest.main()
