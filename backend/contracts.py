from __future__ import annotations

from typing import Any, Protocol

from backend.audio.capture import CaptureConsumer


class EventSink(Protocol):
    """Publishes a backend event to the frontend."""

    def emit(self, event: str, data: dict[str, Any]) -> None: ...


class OscSink(Protocol):
    def send_text(
        self,
        text: str,
        *,
        utterance_id: str | None = None,
        source: str | None = None,
        generation: int = 0,
    ) -> bool: ...

    def set_face(
        self,
        face_id: int,
        *,
        utterance_id: str | None = None,
        source: str | None = None,
        generation: int = 0,
    ) -> bool: ...

    def set_source_generation(self, source: str, generation: int) -> None: ...
    def close(self) -> None: ...


class EmotionClassifier(Protocol):
    @property
    def ready(self) -> bool: ...

    def load_model(self, *, wait: bool = False) -> None: ...
    def submit(self, utterance_id: str, text: str) -> bool: ...
    def clear_pending(self) -> None: ...
    def close(self) -> None: ...


class OverlaySink(Protocol):
    def configure(self, settings: dict[str, Any]) -> None: ...
    def show_image(self, path: str) -> None: ...
    def calibrate(self, prompt_image: str, update: bool = False) -> bool: ...
    def hide(self) -> None: ...
    def close(self) -> None: ...


class TranslationRunner(Protocol):
    """Queues translation work and reports results through its callbacks."""

    def providers(self) -> list[dict[str, object]]: ...

    def prepare(self, settings: dict[str, Any]) -> None: ...

    def submit(
        self,
        *,
        provider_id: str,
        text: str,
        source_language: str,
        target_language: str,
        options: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> str | None: ...

    def invalidate(self, provider_id: str) -> None: ...
    def close(self) -> None: ...


class AudioSource(Protocol):
    """Captures microphone or speaker audio and cuts it into utterances."""

    @property
    def vad_ready(self) -> bool: ...

    def load_vad(self) -> None: ...
    def list_devices(self) -> list[dict[str, object]]: ...

    def start(
        self,
        device_id: str,
        source: str,
        consumer: CaptureConsumer,
        *,
        max_segment_ms: int = ...,
    ) -> None: ...

    def stop(self, source: str | None = None) -> None: ...
    def is_active(self, transcript_source: str, session_id: str) -> bool: ...
    def close(self) -> None: ...


class Pipeline(Protocol):
    """One way of turning captured speech into captions and translations."""

    @property
    def ready(self) -> bool: ...

    def load(self) -> None:
        """Prepares models; blocks, and reports the speech model status."""

    def start(self, source: str, device_id: str) -> None: ...
    def stop(self, source: str) -> None: ...
    def update_settings(self, settings: dict[str, Any]) -> None: ...
    def close(self) -> None: ...
