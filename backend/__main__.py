from __future__ import annotations

import logging
import json
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
from backend.runtime import backend_variant, configure_native_runtime


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
    configure_native_runtime()
    if getattr(sys, "frozen", False):
        import ctranslate2  # noqa: F401
        import onnxruntime  # noqa: F401

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


def self_test(
    *,
    speech_model: bool = False,
    gpu: bool = False,
    emotion: bool = False,
    translation: bool = False,
) -> int:
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

    configure_native_runtime()
    stage("ctranslate2")
    import ctranslate2

    stage("faster-whisper")
    from faster_whisper import WhisperModel
    from faster_whisper.vad import get_vad_model

    stage("transformers")
    from transformers import AutoTokenizer  # noqa: F401

    stage("silero-model")
    vad_model = get_vad_model()
    stage("silero-inference")
    assert np.isfinite(vad_model(np.zeros(512, dtype=np.float32))).all()
    if backend_variant() == "cu126":
        stage("cuda-dlls")
        import ctypes

        for name in ("cublas64_12.dll", "cudnn64_9.dll"):
            ctypes.WinDLL(name)
    import importlib.util

    assert importlib.util.find_spec("torch") is None, "Runtime must not contain PyTorch"
    if speech_model:
        stage("whisper-tiny-inference")
        device = "cuda" if gpu else "cpu"
        model = WhisperModel(
            "tiny",
            device=device,
            compute_type="float16" if gpu else "int8",
            cpu_threads=2,
        )
        segments, _ = model.transcribe(
            np.zeros(16000, dtype=np.float32), language="en", beam_size=1
        )
        assert all(isinstance(segment.text, str) for segment in segments)
    if emotion:
        stage("emotion-onnx-cpu")
        errors = []
        classifier = EmotionService(
            on_model_status=lambda *_: None,
            on_result=lambda *_: None,
            on_error=errors.append,
        )
        try:
            classifier._load_model()
            assert classifier.ready, errors
            assert classifier._predict("今天一起玩遊戲吧。") == 0
            assert classifier._device == "cpu"
        finally:
            classifier.close()
    if translation:
        stage("nllb-int8-cpu")
        from backend.nllb import NllbTranslator

        translator = NllbTranslator()
        try:
            assert translator.translate("Hello.", "eng_Latn", "zho_Hant")
            assert translator.translate("你好。", "zho_Hant", "eng_Latn")
        finally:
            translator.close()
    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "variant": backend_variant(),
                "ctranslate2": ctranslate2.__version__,
                "pytorch": False,
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    if any(arg.startswith("--self-test") for arg in sys.argv[1:]):
        raise SystemExit(
            self_test(
                speech_model="--self-test-model" in sys.argv
                or "--self-test-gpu" in sys.argv,
                gpu="--self-test-gpu" in sys.argv,
                emotion="--self-test-emotion" in sys.argv,
                translation="--self-test-translation" in sys.argv,
            )
        )
    raise SystemExit(main())
