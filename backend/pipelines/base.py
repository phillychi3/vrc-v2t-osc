from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from backend.audio.capture import VoiceError
from backend.contracts import AudioSource
from backend.i18n import t


ModelStatusHandler = Callable[[str, str | None], None]
ErrorHandler = Callable[[str], None]


_SPEAKER_SEGMENT_MS = 7_000


class CapturePipeline:
    """Shared plumbing: capture lifecycle and the speech model status.

    Subclasses decide what happens to the audio by overriding `on_audio`,
    `on_segment`, `_prepare` and the per-source hooks.
    """

    def __init__(
        self,
        *,
        capture: AudioSource,
        on_model_status: ModelStatusHandler,
        on_error: ErrorHandler,
    ) -> None:
        self._capture = capture
        self._on_model_status = on_model_status
        self._on_error = on_error
        self._ready = False
        self._closed = False

    @property
    def ready(self) -> bool:
        return self._ready and not self._closed

    def load(self) -> None:
        self._on_model_status("loading", None)
        try:
            self._capture.load_vad()
            device = self._prepare()
        except Exception as exc:
            logging.exception("Failed to prepare speech recognition")
            if not self._closed:
                self._on_model_status("failed", None)
                message = (
                    str(exc)
                    if isinstance(exc, VoiceError)
                    else t("backend.voice.modelFailed", error=exc)
                )
                self._on_error(message)
            return
        if self._closed:
            return
        self._ready = True
        self._on_model_status("ready", device)

    def start(self, source: str, device_id: str) -> None:
        self._before_start(source)
        try:
            self._capture.start(
                device_id, source, self, max_segment_ms=self._segment_limit(source)
            )
        except Exception:
            self._after_stop(source)
            raise

    def stop(self, source: str) -> None:
        self._capture.stop(source)
        self._after_stop(source)

    def update_settings(self, settings: dict[str, Any]) -> None:
        return

    def close(self) -> None:
        self._closed = True
        self._capture.stop()
        self._close()

    def on_audio(
        self, transcript_source: str, session_id: str, pcm: bytes, speech: bool
    ) -> None:
        return

    def on_segment(self, transcript_source: str, session_id: str, segment: bytes) -> None:
        return

    def _prepare(self) -> str | None:
        """Loads what the pipeline needs beyond the VAD; returns its device."""
        return "cpu"

    def _segment_limit(self, source: str) -> int:
        return _SPEAKER_SEGMENT_MS if source == "speaker" else 15_000

    def _before_start(self, source: str) -> None:
        return

    def _after_stop(self, source: str) -> None:
        return

    def _close(self) -> None:
        return
