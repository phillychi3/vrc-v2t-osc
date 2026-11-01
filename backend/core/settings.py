from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from backend.settings import apply_patch, default_settings, validate_settings

# (previous settings, new settings)
SettingsListener = Callable[[dict[str, Any], dict[str, Any]], None]


def local_language(settings: dict[str, Any]) -> str:
    """The user's own language: "your language", else the speech setting."""
    language = settings["translation"]["sourceLanguage"]
    if language == "auto":
        return settings["speech"]["language"]
    return language


class SettingsService:
    """Holds the validated settings and tells subscribers when they change."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._settings = default_settings()
        self._listeners: list[SettingsListener] = []

    @property
    def current(self) -> dict[str, Any]:
        """The live settings; callers must not modify them."""
        with self._lock:
            return self._settings

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._settings)

    def subscribe(self, listener: SettingsListener) -> None:
        self._listeners.append(listener)

    def load(self, value: object) -> None:
        """Replaces the settings at startup, without notifying anyone."""
        settings = validate_settings(value)
        with self._lock:
            self._settings = settings

    def preview(self, patch: object) -> dict[str, Any]:
        """The settings a patch would produce; raises SettingsError if invalid."""
        with self._lock:
            return apply_patch(self._settings, patch)

    def commit(self, candidate: dict[str, Any]) -> None:
        with self._lock:
            previous, self._settings = self._settings, candidate
        for listener in self._listeners:
            try:
                listener(previous, candidate)
            except Exception:
                # One failing reaction must not stop the others from applying.
                logging.exception("Applying a settings change failed")
