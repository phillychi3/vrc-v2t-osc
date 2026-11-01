from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from typing import Any
from urllib.parse import quote

from backend.i18n import t
from backend.translation.stream import (
    LIVE_SAMPLE_RATE,
    SETUP_TIMEOUT_SECONDS,
    Connector,
    LiveStreamSession,
    StreamErrorHandler,
    StreamUpdateHandler,
)

GEMINI_ENDPOINT = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
GEMINI_MODEL = "models/gemini-3.5-live-translate-preview"
GEMINI_TARGET_LANGUAGES = {"zh": "zh-Hant", "zh-TW": "zh-Hant", "zh-CN": "zh-Hans"}

# The model translates as one continuous stream and never ends a turn, so an
# utterance is done once its output has been quiet this long.
_QUIET_SECONDS = 1.0
# Before any translation arrives, give up after this long without any message.
_IDLE_SECONDS = 5.0
# Setup fields a model may not accept; dropped and retried if rejected.
_OPTIONAL_SETUP = {
    "sessionResumption": ("sessionResumption", "session_resumption"),
    "contextWindowCompression": (
        "contextWindowCompression",
        "context_window_compression",
    ),
}
# Continuous speech (a video, a game) rarely pauses long enough to end an
# utterance on quiet alone, so a long one is cut where its translation ends a
# sentence, and cut regardless once it grows past the hard limit.
_SENTENCE_ENDINGS = tuple(".!?。！？…")
# A long utterance with no sentence end in sight may be cut at a clause.
_CLAUSE_ENDINGS = tuple(",;，、；")
# (soft, hard) seconds before an utterance is cut: other players' captions
# should keep up with them rather than wait out a long monologue.
_SPEAKER_LIMITS = (3.0, 8.0)
_MICROPHONE_LIMITS = (8.0, 15.0)
_CLAUSE_CHARACTERS = 60
_HARD_CHARACTERS = 120


class GeminiStreamTranslateProvider:
    """Gemini 3.5 Live Translate: speech in, transcripts out, as people talk.

    The translated speech it returns is discarded; the input transcription
    replaces Whisper and the output transcription is the translation.
    """

    def __init__(self, options: dict[str, Any]) -> None:
        self._api_key = str(
            options.get("apiKey") or os.environ.get("GEMINI_API_KEY", "")
        ).strip()
        if not self._api_key:
            raise ValueError(t("backend.translation.geminiKeyMissing"))

    def open_session(
        self,
        *,
        source: str,
        target_language: str,
        on_update: StreamUpdateHandler,
        on_error: StreamErrorHandler,
        connect: Connector | None = None,
    ) -> GeminiStreamSession:
        return GeminiStreamSession(
            api_key=self._api_key,
            target_language=target_language,
            on_update=on_update,
            on_error=on_error,
            utterance_limits=(
                _SPEAKER_LIMITS if source == "speaker" else _MICROPHONE_LIMITS
            ),
            connect=connect,
        )

    def close(self) -> None:
        return


class GeminiStreamSession(LiveStreamSession):
    """Keeps one Live API session open and turns its transcripts into utterances.

    When the server sends `goAway` or the connection drops, the session
    reconnects with the latest resumption handle. An utterance is complete
    once its transcripts have been quiet for a moment, or once it has run long
    enough to cut at a sentence end.
    """

    provider_name = "Gemini"

    def __init__(
        self,
        *,
        api_key: str,
        target_language: str,
        on_update: StreamUpdateHandler,
        on_error: StreamErrorHandler,
        utterance_limits: tuple[float, float] = _MICROPHONE_LIMITS,
        connect: Connector | None = None,
    ) -> None:
        super().__init__(on_update=on_update, on_error=on_error, connect=connect)
        self._url = f"{GEMINI_ENDPOINT}?key={quote(api_key, safe='')}"
        self._target = GEMINI_TARGET_LANGUAGES.get(target_language, target_language)
        self._soft_seconds, self._hard_seconds = utterance_limits
        self._handle_id: str | None = None
        self._optional = set(_OPTIONAL_SETUP)
        # The current utterance; only the receiving thread touches these.
        self._utterance: str | None = None
        self._heard: list[str] = []
        self._translated: list[str] = []
        self._language: str | None = None
        self._started_at = 0.0
        self._last_text_at = 0.0
        self._start()

    def _open(self) -> Any:
        return self._connect(self._url, open_timeout=SETUP_TIMEOUT_SECONDS, max_size=None)

    def _audio_message(self, pcm: bytes) -> dict[str, Any]:
        return {
            "realtimeInput": {
                "audio": {
                    "data": base64.b64encode(pcm).decode("ascii"),
                    "mimeType": f"audio/pcm;rate={LIVE_SAMPLE_RATE}",
                }
            }
        }

    def _end_message(self) -> dict[str, Any] | None:
        return {"realtimeInput": {"audioStreamEnd": True}}

    def _handshake(self, socket: Any) -> None:
        socket.send(json.dumps(self._setup()))
        deadline = time.monotonic() + SETUP_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(t("backend.translation.liveTimeout"))
            if "setupComplete" in self._decode(socket.recv(timeout=remaining)):
                return

    def _setup(self) -> dict[str, Any]:
        setup: dict[str, Any] = {
            "model": GEMINI_MODEL,
            # Transcription belongs to the setup itself; the server rejects it
            # inside generationConfig.
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "translationConfig": {
                    "targetLanguageCode": self._target,
                    # Without echo, speech already in the target language would
                    # produce no transcript at all.
                    "echoTargetLanguage": True,
                },
            },
        }
        if "sessionResumption" in self._optional:
            setup["sessionResumption"] = (
                {"handle": self._handle_id} if self._handle_id else {}
            )
        if "contextWindowCompression" in self._optional:
            # Lifts the 15 minute limit on audio in one session.
            setup["contextWindowCompression"] = {"slidingWindow": {}}
        return {"setup": setup}

    def _rejected(self, reason: str) -> bool:
        """Retries without an optional setup field the server refused."""
        for name, spellings in _OPTIONAL_SETUP.items():
            if name in self._optional and any(word in reason for word in spellings):
                logging.warning("Gemini stream rejected %s; continuing without it", name)
                self._optional.discard(name)
                if name == "sessionResumption":
                    self._handle_id = None
                return True
        return False

    def _handle(self, message: dict[str, Any]) -> bool:
        update = message.get("sessionResumptionUpdate")
        if isinstance(update, dict) and isinstance(update.get("newHandle"), str):
            if update.get("resumable", True):
                self._handle_id = update["newHandle"]
        if "goAway" in message:
            logging.info("Gemini stream asked to reconnect: %s", message["goAway"])
            return True
        content = message.get("serverContent")
        if isinstance(content, dict):
            self._absorb(content)
        return False

    def _absorb(self, content: dict[str, Any]) -> None:
        heard = content.get("inputTranscription")
        translated = content.get("outputTranscription")
        changed = False
        if isinstance(heard, dict) and isinstance(heard.get("text"), str):
            self._begin()
            self._heard.append(heard["text"])
            if isinstance(heard.get("languageCode"), str):
                self._language = heard["languageCode"]
            changed = True
        if isinstance(translated, dict) and isinstance(translated.get("text"), str):
            self._begin()
            self._translated.append(translated["text"])
            changed = True
        if changed:
            self._last_text_at = time.monotonic()
            if self._too_long():
                self._finish(force=True)
            else:
                self._publish(final=False)

    def _begin(self) -> None:
        if self._utterance is None:
            self._utterance = str(uuid.uuid4())
            self._started_at = time.monotonic()

    def _too_long(self) -> bool:
        """Whether to cut the utterance here instead of waiting for a pause."""
        translated = "".join(self._translated).strip()
        if not translated:
            return False
        elapsed = time.monotonic() - self._started_at
        if elapsed >= self._hard_seconds or len(translated) >= _HARD_CHARACTERS:
            return True
        if elapsed < self._soft_seconds:
            return False
        if translated.endswith(_SENTENCE_ENDINGS):
            return True
        return len(translated) >= _CLAUSE_CHARACTERS and translated.endswith(
            _CLAUSE_ENDINGS
        )

    def _tick(self) -> None:
        self._finish()

    def _disconnected(self) -> None:
        self._finish(force=True)

    def _finish(self, *, force: bool = False) -> None:
        """Completes the utterance once its transcripts have gone quiet."""
        if self._utterance is None:
            return
        quiet = time.monotonic() - self._last_text_at
        translated = "".join(self._translated).strip()
        # Without any translation, wait longer in case it is still coming.
        wait = _QUIET_SECONDS if translated else _IDLE_SECONDS
        if not force and quiet < wait:
            return
        self._publish(final=True)
        self._utterance = None
        self._heard = []
        self._translated = []
        self._language = None

    def _publish(self, *, final: bool) -> None:
        if self._utterance is None:
            return
        self._emit(
            self._utterance,
            "".join(self._heard).strip(),
            "".join(self._translated).strip(),
            self._language,
            final,
        )
