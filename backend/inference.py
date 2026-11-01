from __future__ import annotations

import threading


_INFERENCE_LOCK = threading.RLock()


def inference_lock() -> threading.RLock:
    """Guards speech GPU inference across the backend's worker threads."""
    return _INFERENCE_LOCK
