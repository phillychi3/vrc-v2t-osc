from __future__ import annotations

import threading


# Whisper, the emotion classifier and the local translation pipeline each run on
# their own worker thread but share one CUDA context. Overlapping inference from
# several threads crashes the process with an access violation instead of
# raising, so every model call is funnelled through this process-wide lock.
#
# It must always be the innermost lock: never invoke a callback or acquire a
# service lock while holding it.
_INFERENCE_LOCK = threading.RLock()


def inference_lock() -> threading.RLock:
    """Guards all GPU model inference across the backend's worker threads."""
    return _INFERENCE_LOCK
