from __future__ import annotations

from typing import Any

from backend.audio.whisper import WhisperRecognizer
from backend.contracts import AudioSource
from backend.core.settings import local_language
from backend.pipelines.base import CapturePipeline, ErrorHandler, ModelStatusHandler


class WhisperTextPipeline(CapturePipeline):
    """Local Whisper recognition; translation, if any, works on the text."""

    def __init__(
        self,
        *,
        capture: AudioSource,
        recognizer: WhisperRecognizer,
        on_model_status: ModelStatusHandler,
        on_error: ErrorHandler,
    ) -> None:
        super().__init__(
            capture=capture, on_model_status=on_model_status, on_error=on_error
        )
        self._recognizer = recognizer

    def on_segment(self, transcript_source: str, session_id: str, segment: bytes) -> None:
        self._recognizer.submit(transcript_source, session_id, segment)

    def update_settings(self, settings: dict[str, Any]) -> None:
        self._recognizer.set_languages(
            local_language(settings), settings["translation"]["targetLanguage"]
        )

    def _prepare(self) -> str | None:
        return self._recognizer.load()

    def _close(self) -> None:
        self._recognizer.close()
