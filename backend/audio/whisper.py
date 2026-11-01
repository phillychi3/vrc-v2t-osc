from __future__ import annotations

import contextlib
import logging
import os
import queue
import re
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from backend.audio.capture import VoiceError
from backend.i18n import t
from backend.inference import inference_lock
from backend.runtime import (
    backend_variant,
    configure_native_runtime,
    missing_cuda_libraries,
)

# (text, source, detected Whisper language code)
TranscriptHandler = Callable[[str, str, str | None], None]
ErrorHandler = Callable[[str], None]
# Whether a capture session is still current: (transcript source, session id).
SessionCheck = Callable[[str, str], bool]
Segment = tuple[str, str, bytes, float]

_MAX_SEGMENT_WAIT_SECONDS = 15.0
# Greedy decoding without temperature fallback was noticeably less accurate;
# beam search costs little next to the encoder on short clips.
_BEAM_SIZE = 5
_TEMPERATURES = (0.0, 0.2, 0.4, 0.6)
# Above this, trust Whisper's language detection even when it names a language
# outside the configured pair. Short clips often score Mandarin as Japanese or
# Cantonese around 0.6-0.7, and decoding with the wrong token garbles the text.
_LANGUAGE_CONFIDENCE = 0.8
# Detected languages folded into a configured one: Mandarin is routinely
# detected as Cantonese, and decoding Cantonese as `zh` still yields Chinese.
_LANGUAGE_ALIASES = {"yue": "zh"}
# Steers Chinese output to Traditional characters with punctuation; Whisper
# otherwise picks Simplified or Traditional per clip.
_INITIAL_PROMPTS = {"zh": "以下是繁體中文的句子。"}
# Each clip is a single Whisper window, so `condition_on_previous_text` never
# carries anything across clips. The previous clip from the same source is
# passed through the prompt instead while the conversation is still going.
_CONTEXT_SECONDS = 10.0
_CONTEXT_CHARS = 120
# Whisper's training data was full of video captions, so silence and noise
# decode to sign-off lines. Drop clips made of nothing else.
_HALLUCINATION_FRAGMENTS = re.compile(
    r"amara\.org|明鏡|明镜|點點欄目|点点栏目|字幕(?:由|提供|製作|制作|志願者|志愿者)",
    re.IGNORECASE,
)
_HALLUCINATION_LINES = {
    "謝謝觀看",
    "谢谢观看",
    "感謝觀看",
    "感谢观看",
    "謝謝收看",
    "谢谢收看",
    "謝謝大家觀看",
    "請不吝點贊訂閱轉發打賞支持",
    "thankyouforwatching",
    "thanksforwatching",
    "pleasesubscribe",
    "ご視聴ありがとうございました",
    "チャンネル登録お願いします",
    "시청해주셔서감사합니다",
    "구독과좋아요부탁드립니다",
    # The prompt itself comes back verbatim on silent clips.
    "以下是繁體中文的句子",
}
_PUNCTUATION = re.compile(r"[\W_]+")


class WhisperRecognizer:
    """Transcribes finished utterances on one worker thread."""

    def __init__(
        self,
        *,
        model_name: str,
        language: str,
        speaker_language: str,
        is_active: SessionCheck,
        on_transcript: TranscriptHandler,
        on_error: ErrorHandler,
    ) -> None:
        self._model_name = model_name
        self._languages: dict[str, str | None] = {
            "voice": self._normalize_language(language),
            "speaker": self._normalize_language(speaker_language),
        }
        self._is_active = is_active
        self._on_transcript = on_transcript
        self._on_error = on_error
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._segments: queue.Queue[Segment | None] = queue.Queue(maxsize=6)
        # transcript source -> (text, language, finished at); transcriber only.
        self._context: dict[str, tuple[str, str, float]] = {}
        self._model: Any = None
        self._device = "cpu"
        self._worker_thread: threading.Thread | None = None

    def submit(self, transcript_source: str, session_id: str, segment: bytes) -> None:
        """Queues an utterance, dropping the oldest when recognition lags."""
        self._offer_segment((transcript_source, session_id, segment, time.monotonic()))

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._offer_segment(None)
        worker = self._worker_thread
        if worker and worker is not threading.current_thread():
            worker.join(timeout=5.0)
        with self._lock:
            self._model = None

    def set_languages(self, microphone_language: str, speaker_language: str) -> None:
        with self._lock:
            self._languages = {
                "voice": self._normalize_language(microphone_language),
                "speaker": self._normalize_language(speaker_language),
            }

    def load(self) -> str:
        """Loads Whisper, falling back to the CPU; returns the device it runs on."""
        with self._lock:
            if self._closed.is_set():
                raise VoiceError(t("backend.voice.closed"))
            if self._model is not None:
                return self._device
        configure_native_runtime()
        try:
            import ctranslate2
            from faster_whisper import WhisperModel as whisper_model
        except Exception as exc:
            raise VoiceError(t("backend.voice.runtimeFailed", error=exc)) from exc
        try:
            import numpy as np

            threads = min(4, max(1, (os.cpu_count() or 2) // 2))
            device = "cpu"
            if backend_variant() != "cpu":
                try:
                    if ctranslate2.get_cuda_device_count() > 0:
                        device = "cuda"
                except RuntimeError:
                    logging.warning("CUDA detection failed; using CPU", exc_info=True)
                missing = missing_cuda_libraries() if device == "cuda" else []
                if missing:
                    logging.warning(
                        "CUDA libraries unavailable (%s); using CPU", ", ".join(missing)
                    )
                    device = "cpu"

            def create_model(target: str):
                name = self._model_name
                if name == "auto":
                    name = "large-v3-turbo" if target == "cuda" else "base"
                compute_type = "int8" if target == "cpu" else "float16"
                candidate = whisper_model(
                    name,
                    device=target,
                    compute_type=compute_type,
                    cpu_threads=threads,
                    num_workers=1,
                )
                # CUDA DLLs may load only on first inference. Validate before
                # reporting ready, and consume the lazy segments here too.
                segments, _ = candidate.transcribe(
                    np.zeros(16000, dtype=np.float32), language="en", beam_size=1
                )
                list(segments)
                return candidate, name

            with contextlib.redirect_stdout(sys.stderr):
                try:
                    model, model_name = create_model(device)
                except (RuntimeError, OSError):
                    if device != "cuda":
                        raise
                    logging.warning(
                        "CUDA initialization failed; falling back to CPU",
                        exc_info=True,
                    )
                    device = "cpu"
                    model, model_name = create_model(device)
            if self._closed.is_set():
                raise VoiceError(t("backend.voice.closed"))
            logging.info(
                "Speech model=%s device=%s CPU threads=%d",
                model_name,
                device,
                threads,
            )
            with self._lock:
                self._model = model
                self._device = device
                self._worker_thread = threading.Thread(
                    target=self._transcribe_loop,
                    name="speech-transcriber",
                    daemon=True,
                )
                self._worker_thread.start()
            return device
        except VoiceError:
            raise
        except Exception as exc:
            logging.exception("Failed to load speech models")
            raise VoiceError(t("backend.voice.modelFailed", error=exc)) from exc

    def _transcribe_loop(self) -> None:
        import numpy as np

        while not self._closed.is_set() or not self._segments.empty():
            try:
                item = self._segments.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if item is None:
                    return
                transcript_source, session_id, segment, queued_at = item
                if not self._is_active(transcript_source, session_id):
                    continue
                audio = (
                    np.frombuffer(segment, dtype=np.int16).astype(np.float32) / 32768.0
                )
                with self._lock:
                    hints = self._language_hints(transcript_source)
                with inference_lock():
                    wait_seconds = time.monotonic() - queued_at
                    stale = wait_seconds > _MAX_SEGMENT_WAIT_SECONDS
                    if not stale:
                        started_at = time.monotonic()
                        language, probability = self._choose_language(audio, hints)
                        context = self._recent_context(transcript_source, language)
                        segments, _ = self._model.transcribe(
                            audio,
                            language=language,
                            task="transcribe",
                            temperature=_TEMPERATURES,
                            beam_size=_BEAM_SIZE,
                            condition_on_previous_text=False,
                            without_timestamps=True,
                            initial_prompt=self._prompt(language, context),
                            vad_filter=False,  # Capture already applies built-in VAD.
                        )
                        # Inference is lazy: keep iteration inside the lock.
                        text = "".join(part.text for part in segments).strip()
                if stale:
                    logging.warning(
                        "Dropped stale %s speech after %.1fs waiting",
                        transcript_source,
                        wait_seconds,
                    )
                    self._on_error(t("backend.voice.tooSlow"))
                    continue
                if self._is_hallucination(text):
                    logging.info("Dropped caption-like %s hallucination", transcript_source)
                    text = ""
                elif context and self._repeats_context(text, context):
                    # Silence or noise tends to echo the prompt back verbatim.
                    logging.info("Dropped %s echo of previous text", transcript_source)
                    text = ""
                if text:
                    self._context[transcript_source] = (
                        text,
                        language,
                        time.monotonic(),
                    )
                logging.info(
                    "Speech source=%s language=%s (%.2f) audio=%.2fs queue=%.2fs "
                    "inference=%.2fs",
                    transcript_source,
                    language,
                    probability,
                    len(audio) / 16000,
                    wait_seconds,
                    time.monotonic() - started_at,
                )
                if text and self._is_active(transcript_source, session_id):
                    self._on_transcript(text, transcript_source, language)
            except Exception as exc:
                logging.exception("Transcription failed")
                if not self._closed.is_set():
                    self._on_error(t("backend.voice.recognitionFailed", error=exc))
            finally:
                self._segments.task_done()

    def _offer_segment(self, segment: Segment | None) -> None:
        try:
            self._segments.put_nowait(segment)
        except queue.Full:
            try:
                self._segments.get_nowait()
                self._segments.task_done()
            except queue.Empty:
                pass
            self._segments.put_nowait(segment)

    @staticmethod
    def _normalize_language(language: str) -> str | None:
        normalized = language.strip()
        return None if normalized == "auto" else normalized

    def _language_hints(self, transcript_source: str) -> list[str]:
        """Configured languages for a source, its own language first."""
        own = "speaker" if transcript_source == "speaker" else "voice"
        other = "voice" if own == "speaker" else "speaker"
        hints = [self._languages[own], self._languages[other]]
        return list(dict.fromkeys(code for code in hints if code))

    def _choose_language(self, audio: Any, hints: list[str]) -> tuple[str, float]:
        """Detects the spoken language instead of forcing the configured one.

        A forced language token makes Whisper translate into that language, so
        another player's English came out as Japanese when Japanese was set.
        """
        language, probability, all_probs = self._model.detect_language(audio)
        if not hints:
            return language, probability
        scores: dict[str, float] = {}
        for code, score in all_probs:
            alias = _LANGUAGE_ALIASES.get(code)
            if alias in hints:
                code = alias
            scores[code] = scores.get(code, 0.0) + score
        top = max(scores, key=scores.__getitem__)
        if top in hints or scores[top] >= _LANGUAGE_CONFIDENCE:
            return top, scores[top]
        best = max(hints, key=lambda code: scores.get(code, 0.0))
        return best, scores.get(best, 0.0)

    def _recent_context(self, transcript_source: str, language: str) -> str | None:
        """The previous clip's text while the same speaker is still talking."""
        entry = self._context.get(transcript_source)
        if entry is None:
            return None
        text, previous_language, finished_at = entry
        if (
            previous_language != language
            or time.monotonic() - finished_at > _CONTEXT_SECONDS
        ):
            return None
        return text[-_CONTEXT_CHARS:]

    @staticmethod
    def _prompt(language: str, context: str | None) -> str | None:
        parts = [part for part in (_INITIAL_PROMPTS.get(language), context) if part]
        return "".join(parts) or None

    @staticmethod
    def _repeats_context(text: str, context: str) -> bool:
        # Short replies such as 「好」 legitimately recur inside the last line.
        normalized = _PUNCTUATION.sub("", text).lower()
        return (
            len(normalized) >= 4
            and normalized in _PUNCTUATION.sub("", context).lower()
        )

    @staticmethod
    def _is_hallucination(text: str) -> bool:
        if _HALLUCINATION_FRAGMENTS.search(text):
            return True
        return _PUNCTUATION.sub("", text).lower() in _HALLUCINATION_LINES
