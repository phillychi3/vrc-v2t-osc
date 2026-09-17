import unittest
import threading
from unittest.mock import Mock

from backend.osc import OscService
from backend.protocol import Request
from backend.service import BackendService, ServiceError
from backend.settings import default_settings


class FakeOsc:
    def __init__(self, **kwargs: object) -> None:
        self.enabled = bool(kwargs["enabled"])
        self.sent: list[str] = []
        self.faces: list[int] = []
        self.closed = False
        self.on_event = kwargs.get("on_event")

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def set_source_generation(self, source: str, generation: int) -> None:
        pass

    def send_text(self, text: str, **kwargs: object) -> bool:
        if not self.enabled or self.closed:
            if self.on_event:
                self.on_event(
                    "osc.skipped",
                    {
                        "utteranceId": kwargs.get("utterance_id"),
                        "kind": "text",
                        "reason": "disabled",
                    },
                )
            return False
        self.sent.append(text)
        return True

    def set_face(self, face_id: int, **kwargs: object) -> bool:
        if not self.enabled or self.closed:
            return False
        self.faces.append(face_id)
        return True

    def close(self) -> None:
        self.closed = True


class FakeVoice:
    def __init__(self, **kwargs: object) -> None:
        self.callbacks = kwargs
        self.recording = False
        self.device_id: str | None = None
        self.source: str | None = None
        self.active_sources: set[str] = set()
        self.languages: tuple[str, str] | None = None
        self.closed = False

    def load_models(self) -> None:
        callback = self.callbacks["on_model_status"]
        callback("ready", "cpu")  # type: ignore[operator]

    def list_devices(self) -> list[dict[str, object]]:
        return [
            {
                "id": "index:2",
                "name": "Test microphone",
                "hostApi": "Test API",
                "maxInputChannels": 1,
                "isDefault": True,
            }
        ]

    def start(self, device_id: str, source: str = "microphone") -> None:
        self.recording = True
        self.device_id = device_id
        self.source = source
        self.active_sources.add(source)
        callback = self.callbacks["on_recording_state"]
        callback("listening", source)  # type: ignore[operator]

    def stop(self, source: str | None = None) -> None:
        callback = self.callbacks["on_recording_state"]
        sources = [source] if source else list(self.active_sources)
        for current in sources:
            if current is None:
                continue
            self.active_sources.discard(current)
            callback("idle", current)  # type: ignore[operator]
        self.recording = bool(self.active_sources)

    def set_languages(self, microphone_language: str, speaker_language: str) -> None:
        self.languages = (microphone_language, speaker_language)

    def close(self) -> None:
        self.closed = True


class FakeEmotion:
    def __init__(self, **kwargs: object) -> None:
        self.callbacks = kwargs
        self.ready = False
        self.load_calls = 0
        self.submitted: list[tuple[str, str]] = []
        self.closed = False

    def load_model(self) -> None:
        self.load_calls += 1
        self.ready = True
        callback = self.callbacks["on_model_status"]
        callback("ready", "cpu")  # type: ignore[operator]

    def submit(self, utterance_id: str, text: str) -> bool:
        self.submitted.append((utterance_id, text))
        callback = self.callbacks["on_result"]
        callback(utterance_id, 2)  # type: ignore[operator]
        return True

    def clear_pending(self) -> None:
        self.submitted.clear()

    def close(self) -> None:
        self.closed = True


class FakeTranslation:
    def __init__(self, **kwargs: object) -> None:
        self.callbacks = kwargs
        self.closed = False
        self.invalidated: list[str] = []
        self.submissions: list[dict[str, object]] = []

    def providers(self) -> list[dict[str, object]]:
        return [{"id": "fake", "label": "Fake", "local": True}]

    def submit(self, **kwargs: object) -> str:
        self.submissions.append(kwargs)
        callback = self.callbacks["on_result"]
        callback(
            {
                "jobId": "translation-1",
                "provider": kwargs["provider_id"],
                "text": "Hello",
                "sourceLanguage": kwargs["source_language"],
                "targetLanguage": kwargs["target_language"],
                "context": kwargs["context"],
            }
        )  # type: ignore[operator]
        return "translation-1"

    def invalidate(self, provider_id: str) -> None:
        self.invalidated.append(provider_id)

    def close(self) -> None:
        self.closed = True


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[dict[str, object]] = []
        self.osc_instances: list[FakeOsc] = []

        def make_osc(**kwargs: object) -> FakeOsc:
            osc = FakeOsc(**kwargs)
            self.osc_instances.append(osc)
            return osc

        self.service = BackendService(
            lambda _name, payload: self.events.append(payload),
            osc_factory=make_osc,  # type: ignore[arg-type]
        )

    def initialize(self) -> object:
        return self.service.handle(
            Request(
                "init",
                "system.initialize",
                {"settings": default_settings(), "dataPath": "C:/data"},
            )
        )

    def test_requires_initialization(self) -> None:
        with self.assertRaises(ServiceError) as caught:
            self.service.handle(Request("1", "system.getState", {}))
        self.assertEqual(caught.exception.code, "NOT_INITIALIZED")

    def test_restart_suppresses_auto_recording_without_changing_preference(self) -> None:
        service = BackendService(
            lambda *_args: None, voice_factory=FakeVoice, osc_factory=FakeOsc
        )
        self.addCleanup(service.close)
        settings = default_settings()
        settings["audio"]["autoStart"] = True
        service.handle(
            Request(
                "init",
                "system.initialize",
                {
                    "settings": settings,
                    "dataPath": "C:/data",
                    "suppressAutoStart": True,
                },
            )
        )
        service._on_speech_model_status("ready", "cpu")
        snapshot = service.handle(Request("state", "system.getState", {}))
        self.assertTrue(snapshot["settings"]["audio"]["autoStart"])
        self.assertEqual(
            snapshot["recordings"], {"microphone": "idle", "speaker": "idle"}
        )

    def test_initialize_emits_ordered_session_events(self) -> None:
        state = self.initialize()
        self.assertEqual(state["backend"], "ready")  # type: ignore[index]
        self.assertEqual([event["seq"] for event in self.events], [1, 2, 3])
        self.assertEqual(self.events[0]["event"], "backend.ready")

    def test_manual_text_does_not_request_emotion(self) -> None:
        self.initialize()
        result = self.service.handle(Request("1", "text.send", {"text": "你好"}))
        self.assertTrue(result["accepted"])  # type: ignore[index]
        self.assertEqual(self.osc_instances[0].sent, ["你好"])
        self.assertEqual(self.events[-1]["event"], "transcript.final")

    def test_disabling_osc_skips_text(self) -> None:
        self.initialize()
        self.service.handle(
            Request(
                "settings",
                "settings.update",
                {"patch": {"osc": {"enabled": False}}},
            )
        )
        result = self.service.handle(Request("1", "text.send", {"text": "你好"}))
        self.assertFalse(result["accepted"])  # type: ignore[index]
        self.assertEqual(self.events[-1]["event"], "osc.skipped")

    def test_shutdown_is_complete(self) -> None:
        self.initialize()
        result = self.service.handle(Request("bye", "system.shutdown", {}))
        self.assertEqual(result, {"state": "stopped"})
        self.assertTrue(self.service.stopped)
        self.assertTrue(self.osc_instances[0].closed)

    def test_late_transcript_after_stop_or_shutdown_is_ignored(self) -> None:
        self.initialize()
        self.service.handle(Request("stop", "recording.stop", {"source": "microphone"}))
        before = len(self.events)
        self.service._on_voice_transcript("late", "voice")
        self.assertEqual(len(self.events), before)
        self.assertEqual(self.osc_instances[0].sent, [])
        self.service.close()
        self.service._on_voice_transcript("late speaker", "speaker")
        self.assertEqual(len(self.events), before)

    def test_manual_oversize_is_rejected_before_creating_transcript(self) -> None:
        self.initialize()
        before = len(self.events)
        with self.assertRaises(ServiceError) as caught:
            self.service.handle(Request("long", "text.send", {"text": "中" * 145}))
        self.assertEqual(caught.exception.code, "TEXT_TOO_LONG")
        self.assertEqual(len(self.events), before)
        self.assertEqual(self.osc_instances[0].sent, [])

    def test_real_sender_cancels_pending_translation_on_stop(self) -> None:
        client = Mock()
        sent = threading.Event()
        events = []

        def emit(name, payload):
            events.append(payload)
            if name == "osc.sent":
                sent.set()

        service = BackendService(
            emit,
            osc_factory=lambda **kwargs: OscService(
                **kwargs, client_factory=lambda *_args: client, chat_interval=60
            ),
            translation_factory=FakeTranslation,
        )
        self.addCleanup(service.close)
        settings = default_settings()
        settings["translation"]["enabled"] = True
        service.handle(
            Request(
                "init", "system.initialize", {"settings": settings, "dataPath": "C:/data"}
            )
        )
        service.handle(Request("manual", "text.send", {"text": "first"}))
        self.assertTrue(sent.wait(1))
        service._on_voice_transcript("pending translation", "voice")
        service.handle(Request("stop", "recording.stop", {"source": "microphone"}))
        skipped = [e for e in events if e["event"] == "osc.skipped"]
        self.assertEqual(skipped[-1]["data"]["reason"], "stale")
        client.send_message.assert_called_once_with("/chatbox/input", ["first", True])

    def test_audio_devices_and_recording_commands(self) -> None:
        voices: list[FakeVoice] = []

        def make_voice(**kwargs: object) -> FakeVoice:
            voice = FakeVoice(**kwargs)
            voices.append(voice)
            return voice

        service = BackendService(
            lambda _name, payload: self.events.append(payload),
            osc_factory=lambda **kwargs: FakeOsc(**kwargs),  # type: ignore[arg-type]
            voice_factory=make_voice,  # type: ignore[arg-type]
        )
        service.handle(
            Request(
                "init",
                "system.initialize",
                {"settings": default_settings(), "dataPath": "C:/data"},
            )
        )

        devices = service.handle(Request("devices", "audio.listDevices", {}))
        self.assertEqual(devices["devices"][0]["id"], "index:2")  # type: ignore[index]

        started = service.handle(
            Request(
                "start",
                "recording.start",
                {"deviceId": "index:2", "source": "microphone"},
            )
        )
        self.assertEqual(started, {"state": "listening"})
        self.assertEqual(voices[0].device_id, "index:2")
        self.assertEqual(voices[0].source, "microphone")

        speaker_started = service.handle(
            Request(
                "speaker",
                "recording.start",
                {"deviceId": "default", "source": "speaker"},
            )
        )
        self.assertEqual(speaker_started, {"state": "listening"})
        state = service.handle(Request("both", "system.getState", {}))
        self.assertEqual(state["recordings"]["microphone"], "listening")  # type: ignore[index]
        self.assertEqual(state["recordings"]["speaker"], "listening")  # type: ignore[index]
        self.assertIsNone(state["recordingSource"])  # type: ignore[index]

        mic_stopped = service.handle(
            Request("stop-mic", "recording.stop", {"source": "microphone"})
        )
        self.assertEqual(mic_stopped, {"state": "idle"})
        state = service.handle(Request("speaker-remains", "system.getState", {}))
        self.assertEqual(state["recording"], "listening")  # type: ignore[index]
        self.assertEqual(state["recordingSource"], "speaker")  # type: ignore[index]

        service.handle(Request("stop-speaker", "recording.stop", {"source": "speaker"}))

    def test_voice_transcript_runs_emotion_and_sets_face(self) -> None:
        voices: list[FakeVoice] = []
        emotions: list[FakeEmotion] = []
        oscs: list[FakeOsc] = []

        def make_voice(**kwargs: object) -> FakeVoice:
            voice = FakeVoice(**kwargs)
            voices.append(voice)
            return voice

        def make_emotion(**kwargs: object) -> FakeEmotion:
            emotion = FakeEmotion(**kwargs)
            emotions.append(emotion)
            return emotion

        def make_osc(**kwargs: object) -> FakeOsc:
            osc = FakeOsc(**kwargs)
            oscs.append(osc)
            return osc

        service = BackendService(
            lambda _name, payload: self.events.append(payload),
            osc_factory=make_osc,  # type: ignore[arg-type]
            voice_factory=make_voice,  # type: ignore[arg-type]
            emotion_factory=make_emotion,  # type: ignore[arg-type]
        )
        service.handle(
            Request(
                "init",
                "system.initialize",
                {"settings": default_settings(), "dataPath": "C:/data"},
            )
        )

        on_transcript = voices[0].callbacks["on_transcript"]
        on_transcript("今天很開心", "voice")  # type: ignore[operator]

        self.assertEqual(emotions[0].submitted[0][1], "今天很開心")
        self.assertEqual(oscs[0].sent, ["今天很開心"])
        self.assertEqual(oscs[0].faces, [2])
        emotion_events = [
            event for event in self.events if event["event"] == "emotion.result"
        ]
        self.assertEqual(emotion_events[-1]["data"]["emotion"], 2)  # type: ignore[index]

        microphone_id = emotions[0].submitted[0][0]
        on_transcript("speaker must stay local", "speaker")
        self.assertEqual(len(emotions[0].submitted), 1)
        self.assertEqual(oscs[0].faces, [2])
        self.assertEqual(len(oscs[0].sent), 1)
        self.assertEqual(service._latest_voice_utterance_id, microphone_id)
        service._on_emotion_result(microphone_id, 3)
        self.assertEqual(oscs[0].faces, [2, 3])

        service.close()
        self.assertTrue(emotions[0].closed)

    def test_emotion_model_loads_only_after_feature_is_enabled(self) -> None:
        emotions: list[FakeEmotion] = []

        def make_emotion(**kwargs: object) -> FakeEmotion:
            emotion = FakeEmotion(**kwargs)
            emotions.append(emotion)
            return emotion

        settings = default_settings()
        settings["emotion"]["enabled"] = False
        service = BackendService(
            lambda _name, payload: self.events.append(payload),
            osc_factory=lambda **kwargs: FakeOsc(**kwargs),  # type: ignore[arg-type]
            emotion_factory=make_emotion,  # type: ignore[arg-type]
        )
        service.handle(
            Request(
                "init",
                "system.initialize",
                {"settings": settings, "dataPath": "C:/data"},
            )
        )
        self.assertEqual(emotions[0].load_calls, 0)

        service.handle(
            Request(
                "enable-emotion",
                "settings.update",
                {"patch": {"emotion": {"enabled": True}}},
            )
        )
        self.assertEqual(emotions[0].load_calls, 1)

    def test_translation_target_updates_speaker_whisper_language(self) -> None:
        voices: list[FakeVoice] = []

        def make_voice(**kwargs: object) -> FakeVoice:
            voice = FakeVoice(**kwargs)
            voices.append(voice)
            return voice

        service = BackendService(
            lambda _name, payload: self.events.append(payload),
            osc_factory=lambda **kwargs: FakeOsc(**kwargs),  # type: ignore[arg-type]
            voice_factory=make_voice,  # type: ignore[arg-type]
        )
        service.handle(
            Request(
                "init",
                "system.initialize",
                {"settings": default_settings(), "dataPath": "C:/data"},
            )
        )
        self.assertEqual(voices[0].callbacks["speaker_language"], "en")

        service.handle(
            Request(
                "language",
                "settings.update",
                {"patch": {"translation": {"targetLanguage": "ja"}}},
            )
        )

        self.assertEqual(voices[0].languages, ("zh", "ja"))
        service.close()

    def test_speech_model_change_replaces_voice_service(self) -> None:
        voices: list[FakeVoice] = []

        def make_voice(**kwargs: object) -> FakeVoice:
            voice = FakeVoice(**kwargs)
            voices.append(voice)
            return voice

        service = BackendService(
            lambda _name, payload: self.events.append(payload),
            osc_factory=lambda **kwargs: FakeOsc(**kwargs),  # type: ignore[arg-type]
            voice_factory=make_voice,  # type: ignore[arg-type]
        )
        service.handle(
            Request(
                "init",
                "system.initialize",
                {"settings": default_settings(), "dataPath": "C:/data"},
            )
        )

        result = service.handle(
            Request(
                "model",
                "settings.update",
                {"patch": {"speech": {"model": "small"}}},
            )
        )

        self.assertEqual(len(voices), 2)
        self.assertTrue(voices[0].closed)
        self.assertEqual(voices[1].callbacks["model_name"], "small")
        self.assertEqual(result["settings"]["speech"]["model"], "small")  # type: ignore[index]
        service.close()

    def test_translation_interface_lists_factory_methods_and_submits_job(self) -> None:
        translations: list[FakeTranslation] = []

        def make_translation(**kwargs: object) -> FakeTranslation:
            translation = FakeTranslation(**kwargs)
            translations.append(translation)
            return translation

        service = BackendService(
            lambda _name, payload: self.events.append(payload),
            osc_factory=lambda **kwargs: FakeOsc(**kwargs),  # type: ignore[arg-type]
            translation_factory=make_translation,  # type: ignore[arg-type]
        )
        service.handle(
            Request(
                "init",
                "system.initialize",
                {"settings": default_settings(), "dataPath": "C:/data"},
            )
        )

        providers = service.handle(Request("providers", "translation.listProviders", {}))
        self.assertEqual(providers["providers"][0]["id"], "fake")  # type: ignore[index]
        result = service.handle(
            Request(
                "translate",
                "translation.translate",
                {
                    "provider": "fake",
                    "text": "你好",
                    "sourceLanguage": "zh",
                    "targetLanguage": "en",
                    "options": {},
                    "context": {"utteranceId": "utt-1"},
                },
            )
        )
        self.assertEqual(result, {"jobId": "translation-1", "accepted": True})
        self.assertEqual(self.events[-1]["event"], "translation.result")

        updated = service.handle(
            Request(
                "translation-model",
                "settings.update",
                {
                    "patch": {
                        "translation": {"model": "venddair/nllb-200-distilled-600M-onnx"}
                    }
                },
            )
        )
        self.assertEqual(translations[0].invalidated, [])
        self.assertEqual(
            updated["settings"]["translation"]["model"],  # type: ignore[index]
            "venddair/nllb-200-distilled-600M-onnx",
        )
        service.close()
        self.assertTrue(translations[0].closed)

    def test_stopped_source_translation_cannot_send_after_restart(self) -> None:
        class DeferredTranslation(FakeTranslation):
            def submit(self, **kwargs: object) -> str:
                self.submissions.append(kwargs)
                return "pending"

        translations = []
        oscs = []

        def make_translation(**kwargs):
            instance = DeferredTranslation(**kwargs)
            translations.append(instance)
            return instance

        def make_osc(**kwargs):
            instance = FakeOsc(**kwargs)
            oscs.append(instance)
            return instance

        service = BackendService(
            lambda *_args: None,
            osc_factory=make_osc,
            voice_factory=FakeVoice,
            translation_factory=make_translation,
        )
        self.addCleanup(service.close)
        settings = default_settings()
        settings["translation"]["enabled"] = True
        service.handle(
            Request(
                "init",
                "system.initialize",
                {
                    "settings": settings,
                    "dataPath": "C:/data",
                },
            )
        )
        for source in ("microphone", "speaker"):
            service.handle(Request("start", "recording.start", {"source": source}))
        service._on_voice_transcript("舊麥克風", "voice")
        service._on_voice_transcript("喇叭", "speaker")
        mic, speaker = [job["context"] for job in translations[0].submissions]
        service.handle(Request("stop", "recording.stop", {"source": "microphone"}))
        service.handle(Request("restart", "recording.start", {"source": "microphone"}))
        service._on_translation_result({"context": mic, "text": "stale"})
        service._on_translation_error("pending", "failed", mic)
        self.assertEqual(oscs[0].sent, [])
        service._on_translation_result({"context": speaker, "text": "speaker"})
        self.assertEqual(oscs[0].sent, [])

    def test_speaker_text_stays_local_for_all_translation_outcomes(self) -> None:
        for outcome in ("success", "failure", "overloaded", "disabled"):
            with self.subTest(outcome=outcome):
                events = []
                osc = FakeOsc(enabled=True)

                class Translation(FakeTranslation):
                    def submit(self, **kwargs):
                        if outcome == "overloaded":
                            return None
                        if outcome == "failure":
                            self.callbacks["on_error"](
                                "failed-job", "failed", kwargs["context"]
                            )
                            return "failed-job"
                        return super().submit(**kwargs)

                service = BackendService(
                    lambda _name, payload: events.append(payload),
                    osc_factory=lambda **kwargs: osc,
                    translation_factory=Translation,
                )
                self.addCleanup(service.close)
                settings = default_settings()
                settings["translation"]["enabled"] = outcome != "disabled"
                service.handle(
                    Request(
                        "init",
                        "system.initialize",
                        {"settings": settings, "dataPath": "C:/data"},
                    )
                )

                service._on_voice_transcript("speaker original", "speaker")

                self.assertEqual(osc.sent, [])
                transcripts = [e for e in events if e["event"] == "transcript.final"]
                self.assertEqual(transcripts[-1]["data"]["text"], "speaker original")
                if outcome == "success":
                    results = [e for e in events if e["event"] == "translation.result"]
                    self.assertEqual(results[-1]["data"]["text"], "Hello")
                elif outcome in {"failure", "overloaded"}:
                    self.assertTrue(
                        any(e["event"] == "translation.error" for e in events)
                    )

    def test_voice_transcript_is_translated_before_osc_when_enabled(self) -> None:
        translations: list[FakeTranslation] = []
        oscs: list[FakeOsc] = []

        def make_translation(**kwargs: object) -> FakeTranslation:
            translation = FakeTranslation(**kwargs)
            translations.append(translation)
            return translation

        def make_osc(**kwargs: object) -> FakeOsc:
            osc = FakeOsc(**kwargs)
            oscs.append(osc)
            return osc

        settings = default_settings()
        settings["translation"]["enabled"] = True
        service = BackendService(
            lambda _name, payload: self.events.append(payload),
            osc_factory=make_osc,  # type: ignore[arg-type]
            translation_factory=make_translation,  # type: ignore[arg-type]
        )
        service.handle(
            Request(
                "init",
                "system.initialize",
                {"settings": settings, "dataPath": "C:/data"},
            )
        )

        service._on_voice_transcript("你好", "voice")

        self.assertEqual(oscs[0].sent, ["Hello"])
        self.assertEqual(
            translations[0].submissions[-1]["options"],
            {"model": "venddair/nllb-200-distilled-600M-onnx"},
        )
        result_events = [
            event for event in self.events if event["event"] == "translation.result"
        ]
        self.assertEqual(
            result_events[-1]["data"]["context"]["source"],
            "voice",  # type: ignore[index]
        )
        service.close()


if __name__ == "__main__":
    unittest.main()
