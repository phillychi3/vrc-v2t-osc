import json
import threading
import unittest
from unittest.mock import patch

from backend.translation import (
    DeepLTranslationProvider,
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

        def on_result(result: dict[str, object]) -> None:
            results.append(result)
            completed.set()

        service = TranslationService(
            factory=factory,
            on_result=on_result,
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

        def on_error(job_id: str, message: str, _context: dict[str, object]) -> None:
            errors.append((job_id, message))
            completed.set()

        service = TranslationService(
            factory=factory,
            on_result=lambda _result: completed.set(),
            on_status=lambda provider, status: statuses.append((provider, status)),
            on_error=on_error,
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

    def test_deepl_provider_requires_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "DeepL API 金鑰"):
                DeepLTranslationProvider({"plan": "free"})


if __name__ == "__main__":
    unittest.main()
