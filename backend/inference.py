from __future__ import annotations

import threading


# Serialize speech GPU work, including lazy CTranslate2 segment iteration.
# Emotion and translation have their own CPU ONNX sessions and do not use this
# lock, so they cannot hold up GPU transcription.
#
# It must always be the innermost lock: never invoke a callback or acquire a
# service lock while holding it.
_INFERENCE_LOCK = threading.RLock()


def inference_lock() -> threading.RLock:
    """Guards speech GPU inference across the backend's worker threads."""
    return _INFERENCE_LOCK
