from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pythonosc import udp_client


logger = logging.getLogger(__name__)


class _OwnedUDPClient(udp_client.SimpleUDPClient):
    def close(self) -> None:
        # python-osc 1.9.3 exposes no public close method.
        self._sock.close()


class OscClient(Protocol):
    def send_message(self, address: str, value: Any) -> None: ...


@dataclass(frozen=True)
class _Message:
    address: str
    value: Any
    utterance_id: str | None
    source: str | None
    generation: int


# Application pacing policy, not a claimed VRChat server rate limit.
CHAT_INTERVAL_SECONDS = 1.5
CHAT_MAX_LENGTH = 144


def text_rejection_reason(text: str) -> str | None:
    if not text.strip():
        return "empty"
    # Count UTF-16 units conservatively, including surrogate pairs for emoji.
    if len(text.encode("utf-16-le")) // 2 > CHAT_MAX_LENGTH:
        return "too_long"
    if len(text.splitlines()) > 9:
        return "too_many_lines"
    return None


class OscService:
    """Ordered, bounded OSC sender whose state is checked at dequeue time."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9000,
        *,
        enabled: bool = True,
        emotion_parameter: str = "/avatar/parameters/v2t_sync_emo",
        max_queue: int = 20,
        client_factory: Callable[[str, int], OscClient] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        chat_interval: float = CHAT_INTERVAL_SECONDS,
    ) -> None:
        factory = client_factory or _OwnedUDPClient
        self._client = factory(host, port)
        self._enabled = enabled
        self._emotion_parameter = emotion_parameter
        self._queue: deque[_Message] = deque()
        self._max_queue = max_queue
        self._condition = threading.Condition()
        self._closed = False
        self._generations: dict[str, int] = {}
        self._on_event = on_event
        self._chat_interval = max(0.0, chat_interval)
        self._next_chat_at = 0.0
        self._thread = threading.Thread(target=self._run, name="osc-sender", daemon=True)
        self._thread.start()

    @property
    def enabled(self) -> bool:
        with self._condition:
            return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        with self._condition:
            self._enabled = enabled
            discarded = list(self._queue) if not enabled else []
            if not enabled:
                self._queue.clear()
            self._condition.notify_all()
        for message in discarded:
            self._report(message, "disabled")

    def set_source_generation(self, source: str, generation: int) -> None:
        with self._condition:
            self._generations[source] = generation
            discarded = [m for m in self._queue if m.source == source]
            self._queue = deque(m for m in self._queue if m.source != source)
            self._condition.notify_all()
        for message in discarded:
            self._report(message, "stale")

    def send_text(
        self,
        text: str,
        *,
        utterance_id: str | None = None,
        source: str | None = None,
        generation: int = 0,
    ) -> bool:
        message = _Message(
            "/chatbox/input", [text, True], utterance_id, source, generation
        )
        reason = text_rejection_reason(text)
        if reason:
            self._report(message, reason)
            return False
        return self._enqueue(message)

    def set_face(
        self,
        face_id: int,
        *,
        utterance_id: str | None = None,
        source: str | None = None,
        generation: int = 0,
    ) -> bool:
        if isinstance(face_id, bool) or not isinstance(face_id, int):
            raise TypeError("face_id 必須是整數")
        return self._enqueue(
            _Message(self._emotion_parameter, face_id, utterance_id, source, generation)
        )

    def close(self) -> None:
        with self._condition:
            self._closed = True
            discarded = list(self._queue)
            self._queue.clear()
            self._condition.notify_all()
        for message in discarded:
            self._report(message, "closed")
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.0)

    def _inactive_reason(self, message: _Message) -> str | None:
        if self._closed:
            return "closed"
        if not self._enabled:
            return "disabled"
        if message.source is not None and message.generation != self._generations.get(
            message.source, 0
        ):
            return "stale"
        return None

    def _enqueue(self, message: _Message) -> bool:
        with self._condition:
            reason = self._inactive_reason(message)
            if reason is None and len(self._queue) >= self._max_queue:
                reason = "overloaded"
            if reason is None:
                self._queue.append(message)
                self._condition.notify_all()
        if reason:
            self._report(message, reason)
        return reason is None

    def _run(self) -> None:
        try:
            self._send_loop()
        finally:
            close = getattr(self._client, "close", None)
            if close is not None:
                close()

    def _send_loop(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                message = self._queue[0]
                reason = self._inactive_reason(message)
                chat = message.address == "/chatbox/input"
                delay = self._next_chat_at - time.monotonic() if chat else 0.0
                if reason is None and delay > 0:
                    self._condition.wait(delay)
                    continue
                self._queue.popleft()
                if reason is None:
                    try:
                        # Invalidation and socket dispatch share this lock. Once
                        # stop/disable returns, no old packet can start sending.
                        self._client.send_message(message.address, message.value)
                        if chat:
                            self._next_chat_at = time.monotonic() + self._chat_interval
                    except Exception:
                        logger.exception("OSC send failed for %s", message.address)
                        reason = "send_failed"
            # Never invoke service callbacks while holding the sender lock.
            self._report(message, reason)

    def _report(self, message: _Message, reason: str | None) -> None:
        if self._on_event is None or message.utterance_id is None:
            return
        data = {
            "utteranceId": message.utterance_id,
            "kind": "text" if message.address == "/chatbox/input" else "face",
        }
        if reason:
            data["reason"] = reason
        try:
            self._on_event("osc.skipped" if reason else "osc.sent", data)
        except Exception:
            logger.exception("OSC event callback failed")
