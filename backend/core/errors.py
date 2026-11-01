from __future__ import annotations


class ServiceError(RuntimeError):
    """A request failure reported to the frontend with a stable code."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
