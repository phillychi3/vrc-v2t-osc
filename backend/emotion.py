from __future__ import annotations

import logging
import queue
import sys
import threading
from collections.abc import Callable
from typing import Any

from backend.runtime import cpu_session, emotion_model_directory


ModelStatusHandler = Callable[[str, str | None], None]
EmotionResultHandler = Callable[[str, int], None]
ErrorHandler = Callable[[str], None]


class EmotionService:
    """Loads the classifier in the background and runs bounded, ordered inference."""

    def __init__(
        self,
        *,
        max_queue: int = 3,
        on_model_status: ModelStatusHandler,
        on_result: EmotionResultHandler,
        on_error: ErrorHandler,
    ) -> None:
        self._on_model_status = on_model_status
        self._on_result = on_result
        self._on_error = on_error
        self._queue: queue.Queue[tuple[str, str] | None] = queue.Queue(maxsize=max_queue)
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._ready = False
        self._tokenizer: Any = None
        self._model: Any = None
        self._device: Any = None
        self._load_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready

    def load_model(self) -> None:
        with self._lock:
            if self._closed.is_set() or self._ready:
                return
            if self._load_thread and self._load_thread.is_alive():
                return
            self._load_thread = threading.Thread(
                target=self._load_model,
                name="emotion-model-loader",
                daemon=True,
            )
            self._load_thread.start()

    def submit(self, utterance_id: str, text: str) -> bool:
        if self._closed.is_set() or not self.ready or not text.strip():
            return False
        item = (utterance_id, text)
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(item)
                return True
            except queue.Full:
                return False

    def clear_pending(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            else:
                self._queue.task_done()
                if item is None:
                    return

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self.clear_pending()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        worker = self._worker_thread
        if worker and worker is not threading.current_thread():
            worker.join(timeout=2.0)
        with self._lock:
            self._ready = False
            self._tokenizer = None
            self._model = None

    def _load_model(self) -> None:
        self._on_model_status("loading", None)
        try:
            from transformers import AutoTokenizer

            directory = emotion_model_directory()
            if not (directory / "model.onnx").is_file():
                # The installer only downloads this model when the user ticks
                # the box, so a packaged build can legitimately be without it.
                hint = (
                    "重新執行安裝程式並勾選「安裝情緒辨識模型」"
                    if getattr(sys, "frozen", False)
                    else "請先執行 scripts/export-emotion.py"
                )
                raise FileNotFoundError(f"缺少情緒 ONNX 模型，{hint}：{directory}")
            tokenizer = AutoTokenizer.from_pretrained(
                str(directory), local_files_only=True
            )
            model = cpu_session(directory / "model.onnx")
            device = "cpu"
            if self._closed.is_set():
                return
            with self._lock:
                self._tokenizer = tokenizer
                self._model = model
                self._device = device
                self._ready = True
                self._worker_thread = threading.Thread(
                    target=self._run,
                    name="emotion-inference",
                    daemon=True,
                )
                self._worker_thread.start()
            self._on_model_status("ready", str(device))
        except Exception as exc:
            logging.exception("Failed to load emotion model")
            if not self._closed.is_set():
                self._on_model_status("failed", None)
                self._on_error(f"情緒分析模型載入失敗：{exc}")

    def _run(self) -> None:
        while not self._closed.is_set():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if item is None:
                    return
                utterance_id, text = item
                face_id = self._predict(text)
                if not self._closed.is_set():
                    self._on_result(utterance_id, face_id)
            except Exception as exc:
                logging.exception("Emotion inference failed")
                if not self._closed.is_set():
                    self._on_error(f"情緒辨識失敗：{exc}")
            finally:
                self._queue.task_done()

    def _predict(self, text: str) -> int:
        import numpy as np

        with self._lock:
            tokenizer = self._tokenizer
            model = self._model
        inputs = tokenizer(
            text,
            return_tensors="np",
            padding=True,
            truncation=True,
            max_length=512,
        )
        feeds = {
            item.name: np.asarray(inputs[item.name], dtype=np.int64)
            for item in model.get_inputs()
        }
        logits = model.run(["logits"], feeds)[0]
        if logits.shape != (1, 8) or not np.isfinite(logits).all():
            raise RuntimeError("情緒 ONNX 模型輸出格式不正確")
        return int(np.argmax(logits[0]))
