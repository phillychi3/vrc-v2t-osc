import json
import re
import unittest
from pathlib import Path

from backend import i18n
from backend.protocol import Request
from backend.service import BackendService
from backend.settings import SettingsError, apply_patch, default_settings

LOCALES_DIR = Path(__file__).resolve().parents[1] / "locales"


class LocaleResolutionTests(unittest.TestCase):
    def test_matches_electron_rules(self) -> None:
        cases = {
            ("auto", ("zh-Hant-TW",)): "zh-TW",
            ("auto", ("zh-HK",)): "zh-TW",
            ("auto", ("zh-CN",)): "zh-CN",
            ("auto", ("zh-Hans-SG",)): "zh-CN",
            ("auto", ("ko-KR", "ja-JP")): "ja",
            ("auto", ("ko-KR",)): "en",
            ("auto", ()): "zh-TW",
            ("en", ("ja-JP",)): "en",
        }
        for (preference, languages), expected in cases.items():
            with self.subTest(preference=preference, languages=languages):
                self.assertEqual(i18n.resolve_locale(preference, languages), expected)


class TranslateTests(unittest.TestCase):
    def tearDown(self) -> None:
        i18n.configure("auto", [])

    def test_translates_with_parameters_and_follows_the_setting(self) -> None:
        i18n.configure("en")
        self.assertEqual(
            i18n.t("backend.calibration.timeout", reason="x"), "Calibration timed out: x"
        )
        i18n.configure("ja")
        self.assertIn("調整", i18n.t("backend.calibration.timeout", reason="x"))

    def test_unknown_keys_fall_back_to_the_key(self) -> None:
        self.assertEqual(i18n.t("backend.does-not-exist"), "backend.does-not-exist")

    def test_service_uses_system_languages_and_settings_updates(self) -> None:
        service = BackendService(lambda *_args: None)
        self.addCleanup(service.close)
        service.handle(
            Request(
                "init",
                "system.initialize",
                {
                    "settings": default_settings(),
                    "dataPath": "C:/data",
                    "systemLanguages": ["ja-JP"],
                },
            )
        )
        self.assertEqual(i18n.current_locale(), "ja")
        service.handle(
            Request("1", "settings.update", {"patch": {"ui": {"language": "zh-CN"}}})
        )
        self.assertEqual(i18n.current_locale(), "zh-CN")

    def test_rejects_unknown_interface_language(self) -> None:
        with self.assertRaisesRegex(SettingsError, "ui.language"):
            apply_patch(default_settings(), {"ui": {"language": "fr"}})


class CatalogTests(unittest.TestCase):
    """Guards Crowdin output: same keys and placeholders as the source."""

    def test_translations_match_the_source_catalog(self) -> None:
        source = json.loads((LOCALES_DIR / "zh-TW.json").read_text(encoding="utf-8"))
        placeholders = re.compile(r"\{(\w+)\}")
        for locale in i18n.LOCALES:
            catalog = json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))
            with self.subTest(locale=locale):
                self.assertEqual(set(catalog) - set(source), set(), "unknown keys")
                for key, text in catalog.items():
                    self.assertEqual(
                        set(placeholders.findall(text)),
                        set(placeholders.findall(source[key])),
                        f"{locale} {key}",
                    )


if __name__ == "__main__":
    unittest.main()
