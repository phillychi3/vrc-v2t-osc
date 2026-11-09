import threading
import unittest
from unittest.mock import Mock, patch

from backend.container import build
from backend.core.errors import ServiceError
from backend.emotion import EmotionService
from backend.protocol import Request
from backend.settings import default_settings
from backend.translation.base import TranslationProviderFactory
from backend.translation.worker import TranslationService


class StartupTests(unittest.TestCase):
    def test_live_startup_prepares_vad_before_ready_without_whisper(self):
        events = []
        capture = Mock()
        providers = TranslationProviderFactory()
        providers.register(
            "gemini-live",
            label="Live",
            local=False,
            kind="stream",
            builder=lambda _options: Mock(),
        )
        settings = default_settings()
        settings["translation"].update(enabled=True, provider="gemini-live")
        settings["emotion"]["enabled"] = True
        emotion = Mock(ready=True)

        def assert_starting():
            self.assertFalse(any(e["event"] == "backend.ready" for e in events))

        capture.load_vad.side_effect = assert_starting

        def load_emotion(*, wait=False):
            self.assertTrue(wait)
            assert_starting()

        emotion.load_model.side_effect = load_emotion
        with patch("backend.container.WhisperRecognizer") as whisper:
            backend = build(
                events.append,
                capture_factory=lambda **_: capture,
                osc_factory=Mock(),
                emotion_factory=lambda **_: emotion,
                overlay_factory=Mock(),
                providers=providers,
            )
            self.addCleanup(backend.close)
            result = backend.handle(
                Request(
                    "init",
                    "system.initialize",
                    {
                        "settings": settings,
                        "dataPath": "test-data",
                    },
                )
            )
            whisper.assert_not_called()
        capture.load_vad.assert_called_once()
        self.assertEqual(result["models"]["speech"]["status"], "ready")
        ready = [e for e in events if e["event"] == "backend.ready"]
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["data"]["models"]["speech"]["status"], "ready")

    def test_failed_speech_preparation_never_announces_ready(self):
        events = []
        capture = Mock()
        capture.load_vad.side_effect = RuntimeError("vad failed")
        backend = build(
            events.append,
            capture_factory=lambda **_: capture,
            osc_factory=Mock(),
            emotion_factory=Mock(),
            overlay_factory=Mock(),
        )
        self.addCleanup(backend.close)
        with self.assertRaises(ServiceError) as caught:
            backend.handle(
                Request(
                    "init",
                    "system.initialize",
                    {
                        "settings": default_settings(),
                        "dataPath": "test-data",
                    },
                )
            )
        self.assertEqual(caught.exception.code, "MODEL_LOAD_FAILED")
        self.assertFalse(any(e["event"] == "backend.ready" for e in events))

    def test_emotion_wait_joins_an_existing_background_load(self):
        entered, release = threading.Event(), threading.Event()
        service = EmotionService(
            on_model_status=Mock(),
            on_result=Mock(),
            on_error=Mock(),
        )
        self.addCleanup(service.close)

        def load():
            entered.set()
            release.wait(2)

        with patch.object(service, "_load_model", side_effect=load) as loader:
            service.load_model()
            self.assertTrue(entered.wait(1))
            waiter = threading.Thread(target=lambda: service.load_model(wait=True))
            waiter.start()
            waiter.join(0.05)
            self.assertTrue(waiter.is_alive())
            release.set()
            waiter.join(1)
            self.assertFalse(waiter.is_alive())
            loader.assert_called_once()

    def test_enabled_model_failures_keep_startup_from_finishing(self):
        for model in ("emotion", "translation"):
            with self.subTest(model=model):
                events = []
                settings = default_settings()
                settings[model]["enabled"] = True
                provider = Mock()
                provider.load.side_effect = RuntimeError("translation model failed")
                providers = TranslationProviderFactory()
                providers.register(
                    "onnx",
                    label="Local",
                    local=True,
                    builder=lambda _: provider,
                )
                with patch("backend.container.WhisperRecognizer") as whisper:
                    whisper.return_value.load.return_value = "cpu"
                    backend = build(
                        events.append,
                        capture_factory=Mock(),
                        osc_factory=Mock(),
                        emotion_factory=lambda **_: Mock(ready=False),
                        overlay_factory=Mock(),
                        providers=providers,
                    )
                    try:
                        with self.assertRaises(ServiceError) as caught:
                            backend.handle(
                                Request(
                                    "init",
                                    "system.initialize",
                                    {
                                        "settings": settings,
                                        "dataPath": "test-data",
                                    },
                                )
                            )
                        self.assertEqual(caught.exception.code, "MODEL_LOAD_FAILED")
                        self.assertFalse(
                            any(e["event"] == "backend.ready" for e in events)
                        )
                    finally:
                        backend.close()

    def test_local_translation_preloads_once_and_reuses_provider(self):
        provider = Mock()
        factory = TranslationProviderFactory()
        factory.register("local", label="Local", local=True, builder=lambda _: provider)
        statuses = []
        done = threading.Event()
        service = TranslationService(
            factory=factory,
            on_result=lambda _: done.set(),
            on_status=lambda *args: statuses.append(args),
            on_error=Mock(),
        )
        self.addCleanup(service.close)
        settings = {"enabled": True, "provider": "local"}
        service.prepare(settings)
        service.prepare(settings)
        provider.load.assert_called_once()
        service.submit(
            provider_id="local", text="hello", source_language="en", target_language="zh"
        )
        self.assertTrue(done.wait(1))
        self.assertEqual(statuses.count(("local", "loading")), 1)

    def test_disabled_and_remote_translation_do_not_load_or_connect(self):
        builder = Mock()
        factory = TranslationProviderFactory()
        factory.register("remote", label="Remote", local=False, builder=builder)
        factory.register("local", label="Local", local=True, builder=builder)
        service = TranslationService(
            factory=factory,
            on_result=Mock(),
            on_status=Mock(),
            on_error=Mock(),
        )
        self.addCleanup(service.close)
        service.prepare({"enabled": True, "provider": "remote"})
        service.prepare({"enabled": False, "provider": "local"})
        builder.assert_not_called()
