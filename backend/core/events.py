from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

from backend.protocol import PROTOCOL_VERSION

MessageSender = Callable[[dict[str, Any]], None]


class EventBus:
    """Numbers events within one backend session and hands them to the writer."""

    def __init__(self, send: MessageSender) -> None:
        self._send = send
        self._lock = threading.Lock()
        self._seq = 0
        self.session_id = str(uuid.uuid4())

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def emit(self, event: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            payload = {
                "v": PROTOCOL_VERSION,
                "type": "event",
                "sessionId": self.session_id,
                "seq": self._seq,
                "event": event,
                "data": data,
            }
        self._send(payload)
