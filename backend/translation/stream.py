from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

from backend.i18n import t

LIVE_SAMPLE_RATE = 16000

# (utterance id, heard, translated, heard language, final)
StreamUpdateHandler = Callable[[str, str, str, str | None, bool], None]
StreamErrorHandler = Callable[[str], None]
Connector = Callable[..., Any]

# 30 s of 100 ms audio frames waiting out a reconnect; older frames go first.
_OUTBOX_LIMIT = 300
SETUP_TIMEOUT_SECONDS = 10.0
_RECEIVE_POLL_SECONDS = 0.2
_MAX_BACKOFF_SECONDS = 30.0


class StreamFailure(RuntimeError):
    """The service refused the session; the message is shown as is."""


def primary_language(language: str | None) -> str | None:
    """"zh-Hant" or "en-US" as the bare codes used everywhere else."""
    if not language or not language.strip():
        return None
    return language.strip().split("-")[0].lower()


class LiveStreamSession:
    """One long-lived translation connection for one audio source.

    One thread owns the connection and reads from it; another sends queued
    audio, so a slow network never blocks the capture thread. A dropped
    connection is reopened with exponential backoff, and the audio that piled
    up meanwhile is sent once it is back. Subclasses speak the service's
    protocol and turn its messages into utterances.
    """

    provider_name = ""

    def __init__(
        self,
        *,
        on_update: StreamUpdateHandler,
        on_error: StreamErrorHandler,
        connect: Connector | None = None,
    ) -> None:
        if connect is None:
            from websockets.sync.client import connect as websocket_connect

            connect = websocket_connect
        self._connect = connect
        self._on_update = on_update
        self._on_error = on_error
        self._outbox: queue.Queue[str] = queue.Queue(maxsize=_OUTBOX_LIMIT)
        self._closed = threading.Event()
        self._ready = threading.Event()
        self._socket: Any = None
        self._last_error: str | None = None
        self._receiver = threading.Thread(
            target=self._run, name=f"{self.provider_name.lower()}-stream", daemon=True
        )
        self._sender = threading.Thread(
            target=self._send_loop,
            name=f"{self.provider_name.lower()}-stream-send",
            daemon=True,
        )

    def _start(self) -> None:
        """Called by subclasses once their own state exists."""
        self._receiver.start()
        self._sender.start()

    def send_audio(self, pcm: bytes) -> None:
        """Queues 16 kHz mono PCM16; never blocks."""
        self._enqueue(self._audio_message(pcm))

    def end_audio(self) -> None:
        """Speech paused; lets the service flush what it has buffered."""
        message = self._end_message()
        if message is not None:
            self._enqueue(message)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._before_close()
        self._closed.set()
        socket = self._socket
        if socket is not None:
            try:
                socket.close()
            except Exception:
                logging.debug(
                    "Closing the %s stream failed", self.provider_name, exc_info=True
                )
        self._receiver.join(timeout=3.0)
        self._sender.join(timeout=1.0)

    def _open(self) -> Any:
        """A connection context manager."""
        raise NotImplementedError

    def _handshake(self, socket: Any) -> None:
        """Configures the session; returns once audio may be sent."""
        raise NotImplementedError

    def _handle(self, message: dict[str, Any]) -> bool:
        """Absorbs one server message; True asks for a reconnect."""
        raise NotImplementedError

    def _audio_message(self, pcm: bytes) -> dict[str, Any]:
        raise NotImplementedError

    def _end_message(self) -> dict[str, Any] | None:
        return None

    def _tick(self) -> None:
        """Runs after every message and poll; completes utterances gone quiet."""
        return

    def _disconnected(self) -> None:
        """A dropped connection ends whatever was being said."""
        return

    def _rejected(self, reason: str) -> bool:
        """True when the session can be retried right away with other settings."""
        return False

    def _before_close(self) -> None:
        return

    def _enqueue(self, message: dict[str, Any]) -> None:
        if self._closed.is_set():
            return
        encoded = json.dumps(message)
        try:
            self._outbox.put_nowait(encoded)
        except queue.Full:
            try:
                self._outbox.get_nowait()
            except queue.Empty:
                pass
            try:
                self._outbox.put_nowait(encoded)
            except queue.Full:
                pass

    def _emit(
        self,
        utterance_id: str,
        heard: str,
        translated: str,
        language: str | None,
        final: bool,
    ) -> None:
        if not (heard or translated):
            return
        try:
            self._on_update(
                utterance_id, heard, translated, primary_language(language), final
            )
        except Exception:
            logging.exception("Publishing a streamed translation failed")

    def _report(self, reason: str) -> None:
        logging.warning("%s stream error: %s", self.provider_name, reason)
        # Say it once; a reconnect loop would otherwise repeat the same error.
        if reason != self._last_error:
            self._last_error = reason
            self._on_error(
                t(
                    "backend.translation.liveFailed",
                    provider=self.provider_name,
                    error=reason,
                )
            )

    @staticmethod
    def _decode(raw: str | bytes) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        message = json.loads(raw)
        return message if isinstance(message, dict) else {}

    def _send_loop(self) -> None:
        from websockets.exceptions import ConnectionClosed

        while not self._closed.is_set():
            try:
                message = self._outbox.get(timeout=_RECEIVE_POLL_SECONDS)
            except queue.Empty:
                continue
            # Hold the message until a connection takes it, across reconnects.
            while not self._closed.is_set():
                if not self._ready.wait(_RECEIVE_POLL_SECONDS):
                    continue
                socket = self._socket
                if socket is None:
                    continue
                try:
                    socket.send(message)
                    break
                except ConnectionClosed:
                    self._ready.clear()
                except Exception:
                    logging.exception("Sending audio to %s failed", self.provider_name)
                    break

    def _run(self) -> None:
        from websockets.exceptions import (
            ConnectionClosed,
            ConnectionClosedOK,
            InvalidHandshake,
        )

        backoff = 1.0
        while not self._closed.is_set():
            planned = False
            try:
                with self._open() as socket:
                    self._handshake(socket)
                    self._socket = socket
                    self._ready.set()
                    self._last_error = None
                    backoff = 1.0
                    planned = self._receive(socket)
            except ConnectionClosedOK:
                # The service ended an idle session; open a fresh one quietly.
                logging.info("%s stream closed by the server", self.provider_name)
            except ConnectionClosed as exc:
                if self._closed.is_set():
                    break
                reason = getattr(getattr(exc, "rcvd", None), "reason", "") or str(exc)
                if self._rejected(reason):
                    planned = True
                else:
                    self._report(reason)
            except (StreamFailure, InvalidHandshake) as exc:
                # A refused key, endpoint or setting: say what the service said.
                self._report(str(exc))
            except (OSError, TimeoutError) as exc:
                if self._closed.is_set():
                    break
                self._report(str(exc) or type(exc).__name__)
            except Exception:
                logging.exception("%s stream failed", self.provider_name)
                self._report(t("backend.internalError"))
            finally:
                self._ready.clear()
                self._socket = None
            self._disconnected()
            if self._closed.is_set():
                break
            if not planned and self._closed.wait(backoff):
                break
            if not planned:
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    def _receive(self, socket: Any) -> bool:
        """Reads until the connection ends; True when the server asked to move."""
        while not self._closed.is_set():
            try:
                raw = socket.recv(timeout=_RECEIVE_POLL_SECONDS)
            except TimeoutError:
                self._tick()
                continue
            if self._handle(self._decode(raw)):
                return True
            self._tick()
        return False
