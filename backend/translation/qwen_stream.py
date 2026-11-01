from __future__ import annotations

import base64
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from backend.i18n import t
from backend.translation.base import string_option
from backend.translation.stream import (
    LIVE_SAMPLE_RATE,
    SETUP_TIMEOUT_SECONDS,
    Connector,
    LiveStreamSession,
    StreamErrorHandler,
    StreamFailure,
    StreamUpdateHandler,
)

QWEN_ENDPOINT = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
QWEN_MODEL = "qwen3.5-livetranslate-flash-realtime"
_TRANSCRIPTION_MODEL = "qwen3-asr-flash-realtime"
_LANGUAGES = {"zh-TW": "zh", "zh-CN": "zh"}
# How long the server's VAD waits in silence before it ends an utterance.
# Other players' captions should keep up with them, so theirs end sooner.
_SPEAKER_SILENCE_MS = 500
_MICROPHONE_SILENCE_MS = 800
# The local VAD stops sending audio once speech ends; this much silence after
# it lets the server's VAD see the pause and close the utterance.
_TRAILING_SILENCE = bytes(LIVE_SAMPLE_RATE * 2)
# The transcript of what was heard can land after its translation; wait this
# long for it before publishing the translation alone.
_TRANSCRIPT_WAIT_SECONDS = 1.0
# Heard but never translated (or translated into nothing): give up after this.
_TRANSLATION_WAIT_SECONDS = 5.0
_FINISH_WAIT_SECONDS = 3.0


class QwenStreamTranslateProvider:
    """Qwen3.5 LiveTranslate realtime API with text-only output.

    The service's own recognition replaces Whisper, and its server-side VAD
    decides where one utterance ends and the next begins.
    """

    def __init__(self, options: dict[str, Any]) -> None:
        endpoint = string_option(options, "endpoint", QWEN_ENDPOINT)
        parsed = urlparse(endpoint)
        if parsed.scheme != "wss" or not parsed.netloc:
            raise ValueError(t("backend.translation.invalidQwenEndpoint"))
        if parsed.username or parsed.password:
            raise ValueError(t("backend.translation.endpointCredentials"))
        separator = "&" if parsed.query else "?"
        self._url = f"{endpoint}{separator}model={QWEN_MODEL}"
        self._api_key = str(
            options.get("apiKey") or os.environ.get("DASHSCOPE_API_KEY", "")
        ).strip()
        if not self._api_key:
            raise ValueError(t("backend.translation.qwenKeyMissing"))

    def open_session(
        self,
        *,
        source: str,
        target_language: str,
        on_update: StreamUpdateHandler,
        on_error: StreamErrorHandler,
        connect: Connector | None = None,
    ) -> QwenStreamSession:
        return QwenStreamSession(
            url=self._url,
            api_key=self._api_key,
            target_language=target_language,
            silence_ms=(
                _SPEAKER_SILENCE_MS if source == "speaker" else _MICROPHONE_SILENCE_MS
            ),
            on_update=on_update,
            on_error=on_error,
            connect=connect,
        )

    def close(self) -> None:
        return


@dataclass
class _Utterance:
    utterance_id: str
    heard: str = ""
    translated: str = ""
    language: str | None = None
    heard_at: float | None = None
    translated_at: float | None = None


class QwenStreamSession(LiveStreamSession):
    """One realtime session; each utterance the server's VAD cuts is published.

    The transcript and the translation of an utterance arrive as separate
    items: the translation item names the transcript item it belongs to as its
    `previous_item_id`, which is how the two are paired.
    """

    provider_name = "Qwen"

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        target_language: str,
        silence_ms: int,
        on_update: StreamUpdateHandler,
        on_error: StreamErrorHandler,
        connect: Connector | None = None,
    ) -> None:
        super().__init__(on_update=on_update, on_error=on_error, connect=connect)
        self._url = url
        self._api_key = api_key
        self._target = _LANGUAGES.get(target_language, target_language)
        self._silence_ms = silence_ms
        self._finished = threading.Event()
        # Keyed by transcript item id; only the receiving thread touches these.
        self._utterances: dict[str, _Utterance] = {}
        # translation item id -> transcript item id
        self._links: dict[str, str] = {}
        self._start()

    def _open(self) -> Any:
        return self._connect(
            self._url,
            additional_headers={"Authorization": f"Bearer {self._api_key}"},
            open_timeout=SETUP_TIMEOUT_SECONDS,
            max_size=None,
        )

    def _audio_message(self, pcm: bytes) -> dict[str, Any]:
        return self._event(
            "input_audio_buffer.append", audio=base64.b64encode(pcm).decode("ascii")
        )

    def _end_message(self) -> dict[str, Any] | None:
        return self._audio_message(_TRAILING_SILENCE)

    def _handshake(self, socket: Any) -> None:
        session = {
            "modalities": ["text"],
            "input_audio_format": "pcm",
            "sample_rate": LIVE_SAMPLE_RATE,
            "turn_detection": {
                "type": "server_vad",
                "silence_duration_ms": self._silence_ms,
            },
            "input_audio_transcription": {"model": _TRANSCRIPTION_MODEL},
            "translation": {"language": self._target},
        }
        socket.send(json.dumps(self._event("session.update", session=session)))
        deadline = time.monotonic() + SETUP_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(t("backend.translation.liveTimeout"))
            message = self._decode(socket.recv(timeout=remaining))
            if message.get("type") == "error":
                raise StreamFailure(self._error_text(message))
            if message.get("type") == "session.updated":
                return

    def _before_close(self) -> None:
        # Without session.finish, the last utterance is never translated.
        if not self._ready.is_set():
            return
        self._enqueue(self._event("session.finish"))
        self._finished.wait(_FINISH_WAIT_SECONDS)

    def _handle(self, message: dict[str, Any]) -> bool:
        kind = message.get("type")
        if kind == "conversation.item.input_audio_transcription.text":
            utterance = self._utterance(message.get("item_id"))
            utterance.heard = self._joined(message)
            self._take_language(utterance, message)
            self._publish(utterance, final=False)
        elif kind == "conversation.item.input_audio_transcription.completed":
            utterance = self._utterance(message.get("item_id"))
            transcript = message.get("transcript")
            if isinstance(transcript, str):
                utterance.heard = transcript.strip()
            self._take_language(utterance, message)
            utterance.heard_at = time.monotonic()
        elif kind == "conversation.item.input_audio_transcription.failed":
            self._utterance(message.get("item_id")).heard_at = time.monotonic()
        elif kind == "conversation.item.created":
            item = message.get("item")
            previous = message.get("previous_item_id")
            if (
                isinstance(item, dict)
                and item.get("role") == "assistant"
                and isinstance(item.get("id"), str)
                and isinstance(previous, str)
            ):
                self._links[item["id"]] = previous
        elif kind == "response.text.text":
            utterance = self._translation(message.get("item_id"))
            utterance.translated = self._joined(message)
            self._publish(utterance, final=False)
        elif kind == "response.text.done":
            utterance = self._translation(message.get("item_id"))
            text = message.get("text")
            if isinstance(text, str):
                utterance.translated = text.strip()
            utterance.translated_at = time.monotonic()
        elif kind == "response.done":
            response = message.get("response")
            status = response.get("status") if isinstance(response, dict) else None
            if status not in {None, "completed"}:
                self._report(str(status))
        elif kind == "error":
            self._report(self._error_text(message))
        elif kind == "session.finished":
            self._finished.set()
        return False

    def _tick(self) -> None:
        now = time.monotonic()
        for key, utterance in list(self._utterances.items()):
            translated_at, heard_at = utterance.translated_at, utterance.heard_at
            if (
                (translated_at is not None and heard_at is not None)
                or (
                    translated_at is not None
                    and now - translated_at >= _TRANSCRIPT_WAIT_SECONDS
                )
                or (heard_at is not None and now - heard_at >= _TRANSLATION_WAIT_SECONDS)
            ):
                self._complete(key)

    def _disconnected(self) -> None:
        for key in list(self._utterances):
            self._complete(key)
        self._links.clear()

    def _complete(self, key: str) -> None:
        utterance = self._utterances.pop(key)
        self._links = {
            item: target for item, target in self._links.items() if target != key
        }
        self._publish(utterance, final=True)

    def _utterance(self, item_id: object) -> _Utterance:
        key = item_id if isinstance(item_id, str) else ""
        utterance = self._utterances.get(key)
        if utterance is None:
            utterance = self._utterances[key] = _Utterance(str(uuid.uuid4()))
        return utterance

    def _translation(self, item_id: object) -> _Utterance:
        """The utterance a translation item belongs to."""
        key = item_id if isinstance(item_id, str) else ""
        return self._utterance(self._links.get(key, key))

    def _publish(self, utterance: _Utterance, *, final: bool) -> None:
        self._emit(
            utterance.utterance_id,
            utterance.heard,
            utterance.translated,
            utterance.language,
            final,
        )

    @staticmethod
    def _take_language(utterance: _Utterance, message: dict[str, Any]) -> None:
        language = message.get("language")
        if isinstance(language, str) and language:
            utterance.language = language

    @staticmethod
    def _joined(message: dict[str, Any]) -> str:
        """Confirmed text plus the part the service may still revise."""
        text = message.get("text")
        stash = message.get("stash")
        return (
            (text if isinstance(text, str) else "")
            + (stash if isinstance(stash, str) else "")
        ).strip()

    @staticmethod
    def _error_text(message: dict[str, Any]) -> str:
        error = message.get("error")
        detail = error.get("message") if isinstance(error, dict) else None
        return detail or json.dumps(error, ensure_ascii=False)

    @staticmethod
    def _event(event_type: str, **fields: object) -> dict[str, Any]:
        return {"event_id": f"event_{uuid.uuid4().hex}", "type": event_type, **fields}
