import unittest

from backend.settings import SettingsError, apply_patch, default_settings


class SettingsTests(unittest.TestCase):
    def test_defaults_match_migration_spec(self) -> None:
        settings = default_settings()
        self.assertEqual(settings["speech"]["model"], "large-v3-turbo")
        self.assertEqual(settings["osc"]["port"], 9000)
        self.assertFalse(settings["audio"]["autoStart"])
        self.assertEqual(settings["audio"]["speakerDeviceId"], "speaker:default")
        self.assertFalse(settings["translation"]["enabled"])
        self.assertEqual(settings["translation"]["provider"], "transformers")
        self.assertEqual(
            settings["translation"]["model"],
            "facebook/nllb-200-distilled-600M",
        )
        self.assertEqual(settings["translation"]["deeplPlan"], "free")

    def test_patch_is_immutable(self) -> None:
        original = default_settings()
        changed = apply_patch(original, {"osc": {"enabled": False}})
        self.assertTrue(original["osc"]["enabled"])
        self.assertFalse(changed["osc"]["enabled"])

    def test_rejects_invalid_port(self) -> None:
        with self.assertRaisesRegex(SettingsError, "1 與 65535"):
            apply_patch(default_settings(), {"osc": {"port": 0}})

    def test_rejects_unknown_key(self) -> None:
        with self.assertRaisesRegex(SettingsError, "未知欄位"):
            apply_patch(default_settings(), {"osc": {"secret": True}})

    def test_updates_translation_connection_settings(self) -> None:
        changed = apply_patch(
            default_settings(),
            {
                "translation": {
                    "enabled": True,
                    "provider": "libretranslate",
                    "model": "facebook/nllb-200-distilled-1.3B",
                    "sourceLanguage": "auto",
                    "targetLanguage": "zh",
                    "endpoint": "https://translate.example.test",
                    "apiKey": "secret",
                }
            },
        )
        self.assertTrue(changed["translation"]["enabled"])
        self.assertEqual(changed["translation"]["apiKey"], "secret")


if __name__ == "__main__":
    unittest.main()
