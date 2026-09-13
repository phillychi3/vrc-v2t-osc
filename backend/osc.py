from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any, Protocol

from pythonosc import udp_client


logger = logging.getLogger(__name__)


class _OwnedUDPClient(udp_client.SimpleUDPClient):
    def close(self) -> None:
        # python-osc 1.9.3 exposes no public close method.
        self._sock.close()


class OscClient(Protocol):
    def send_message(self, address: str, value: Any) -> None: ...


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
    ) -> None:
        factory = client_factory or _OwnedUDPClient
        self._client = factory(host, port)
        self._enabled = enabled
        self._emotion_parameter = emotion_parameter
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=max_queue)
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="osc-sender", daemon=True)
        self._thread.start()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = enabled
        if not enabled:
            self._clear_queue()

    def send_text(self, text: str) -> bool:
        if not text.strip():
            return False
        return self._enqueue("/chatbox/input", [text, True])

    def set_face(self, face_id: int) -> bool:
        if isinstance(face_id, bool) or not isinstance(face_id, int):
            raise TypeError("face_id 必須是整數")
        return self._enqueue(self._emotion_parameter, face_id)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self.set_enabled(False)
        self._closed.set()
        self._thread.join(timeout=1.0)

    def _enqueue(self, address: str, value: Any) -> bool:
        if self._closed.is_set() or not self.enabled:
            return False
        try:
            self._queue.put_nowait((address, value))
        except queue.Full:
            logger.warning("OSC queue is full; rejected %s", address)
            return False
        return True

    def _run(self) -> None:
        try:
            self._send_loop()
        finally:
            close = getattr(self._client, "close", None)
            if close is not None:
                close()

    def _send_loop(self) -> None:
        while not self._closed.is_set():
            try:
                address, value = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                if self.enabled:
                    self._client.send_message(address, value)
            except Exception:
                logger.exception("OSC send failed for %s", address)
            finally:
                self._queue.task_done()

    def _clear_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
            else:
                self._queue.task_done()
