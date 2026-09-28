"""User-facing backend messages, from the same Crowdin-managed JSON catalogs
(`/locales/*.json`) as the Electron UI. Keys live under `backend.*`.

The language follows `settings.ui.language`; `auto` uses the OS languages
that Electron passes to `system.initialize`. Locale matching mirrors
`electron/src/shared/i18n-core.ts`.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Iterable

from backend.runtime import resource_root

logger = logging.getLogger(__name__)

SOURCE_LOCALE = "zh-TW"
LOCALES = ("zh-TW", "zh-CN", "en", "ja")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

_lock = threading.Lock()
_catalogs: dict[str, dict[str, str]] | None = None
_preference = "auto"
_system_languages: tuple[str, ...] = ()


def match_locale(tag: str) -> str | None:
    lower = tag.lower()
    if lower.startswith("zh"):
        # Simplified Chinese regions and scripts; any other Chinese is Traditional.
        return "zh-CN" if re.match(r"^zh-(hans|cn|sg|my)\b", lower) else "zh-TW"
    if lower.startswith("ja"):
        return "ja"
    if lower.startswith("en"):
        return "en"
    return None


def resolve_locale(preference: str, system_languages: Iterable[str]) -> str:
    if preference in LOCALES:
        return preference
    languages = list(system_languages)
    for tag in languages:
        locale = match_locale(tag)
        if locale:
            return locale
    # Unknown OS languages read English; with no OS information at all (tests,
    # older callers) keep the source language.
    return "en" if languages else SOURCE_LOCALE


def configure(
    preference: str | None = None, system_languages: Iterable[str] | None = None
) -> None:
    global _preference, _system_languages
    with _lock:
        if preference is not None:
            _preference = preference
        if system_languages is not None:
            _system_languages = tuple(
                tag for tag in system_languages if isinstance(tag, str)
            )


def current_locale() -> str:
    with _lock:
        return resolve_locale(_preference, _system_languages)


def _load_catalogs() -> dict[str, dict[str, str]]:
    global _catalogs
    with _lock:
        if _catalogs is None:
            catalogs: dict[str, dict[str, str]] = {}
            for locale in LOCALES:
                path = resource_root() / "locales" / f"{locale}.json"
                try:
                    catalogs[locale] = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    logger.warning("Missing or invalid locale catalog: %s", path)
                    catalogs[locale] = {}
            _catalogs = catalogs
        return _catalogs


def t(key: str, **params: object) -> str:
    """Translates `key`, falling back to Traditional Chinese, then the key."""
    catalogs = _load_catalogs()
    template = (
        catalogs.get(current_locale(), {}).get(key)
        or catalogs[SOURCE_LOCALE].get(key)
        or key
    )
    return _PLACEHOLDER.sub(
        lambda match: str(params[match.group(1)])
        if match.group(1) in params
        else match.group(0),
        template,
    )
