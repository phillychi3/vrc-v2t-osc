from __future__ import annotations

import logging
import queue
import sys
import threading
from typing import Any

from backend.emotion import EmotionService
from backend.protocol import (
    ProtocolError,
    decode_request,
    encode_message,
    response_error,
    response_ok,
)
from backend.service import BackendService, ServiceError
from backend.translation import TranslationService
from backend.voice import VoiceService


class MessageWriter:
    def __init__(self) -> None:
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        # Capture the protocol stream once. Model loaders may temporarily redirect
        # sys.stdout, but protocol responses must always stay on the original pipe.
        self._stream = sys.stdout.buffer
        self._thread = threading.Thread(target=self._run, name="protocol-writer")
        self._thread.start()

    def send(self, message: dict[str, Any]) -> None:
        self._queue.put(message)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while True:
            message = self._queue.get()
            try:
                if message is None:
                    return
                self._stream.write(encode_message(message))
                self._stream.flush()
            finally:
                self._queue.task_done()


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    if getattr(sys, "frozen", False):
        # Import native speech modules before any application worker threads
        # exist. Frozen Torch can deadlock during its first native import from
        # a multithreaded process even when the importing thread is the main
        # protocol thread.
        import torch  # noqa: F401
        import whisper  # noqa: F401
        import silero_vad  # noqa: F401

    writer = MessageWriter()
    service = BackendService(
        lambda _event, payload: writer.send(payload),
        voice_factory=VoiceService,
        emotion_factory=EmotionService,
        translation_factory=TranslationService,
    )
    try:
        for line in sys.stdin.buffer:
            request_id: str | None = None
            try:
                request = decode_request(line.rstrip(b"\r\n"))
                request_id = request.request_id
                result = service.handle(request)
                writer.send(response_ok(request.request_id, result))
            except ProtocolError as exc:
                writer.send(response_error(request_id, exc.code, str(exc)))
            except ServiceError as exc:
                writer.send(
                    response_error(
                        request_id,
                        exc.code,
                        str(exc),
                        retryable=exc.retryable,
                    )
                )
            except Exception:
                logging.exception("Unhandled backend error")
                writer.send(
                    response_error(request_id, "INTERNAL_ERROR", "後端發生未預期錯誤")
                )
            if service.stopped:
                break
    finally:
        service.close()
        writer.close()
    return 0


def self_test(*, speech_model: bool = False) -> int:
    """Load packaged native/runtime dependencies without downloading model weights."""

    def stage(name: str) -> None:
        sys.stderr.write(f"[self-test] {name}\n")
        sys.stderr.flush()

    stage("numpy")
    import numpy as np

    stage("pydub-pcm")
    from backend.audio import normalize_audio

    converted, _ = normalize_audio(bytes(480 * 4), 2, 48000, None)
    assert len(converted) == 160 * 2

    stage("pyaudiowpatch")
    import pyaudiowpatch  # noqa: F401

    stage("sentencepiece")
    import sentencepiece  # noqa: F401

    stage("torch")
    import torch

    stage("whisper")
    import whisper

    stage("silero-vad")
    from silero_vad import load_silero_vad

    stage("transformers")
    from transformers import AutoModelForSequenceClassification  # noqa: F401

    stage("silero-model")
    vad_model = load_silero_vad()
    stage("silero-inference")
    vad_model(torch.zeros(512), 16000)
    whisper.load_audio
    np.zeros(1, dtype=np.float32)
    if speech_model:
        stage("whisper-tiny-inference")
        model = whisper.load_model("tiny", device="cpu")
        result = model.transcribe(
            np.zeros(16000, dtype=np.float32), fp16=False, language="en"
        )
        assert isinstance(result["text"], str)
    sys.stdout.write('{"ok":true,"torch":"' + torch.__version__ + '"}\n')
    return 0


if __name__ == "__main__":
    if "--self-test-model" in sys.argv:
        raise SystemExit(self_test(speech_model=True))
    raise SystemExit(self_test() if "--self-test" in sys.argv else main())
