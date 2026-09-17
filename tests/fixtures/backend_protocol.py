"""Run the real NDJSON entry point with model loading replaced for IPC tests."""

import importlib.abc
import sys
from unittest.mock import patch


class NoModelImports(importlib.abc.MetaPathFinder):
    def __init__(self):
        self.attempts = []

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {
            "torch",
            "whisper",
            "silero_vad",
            "transformers",
            "pyaudiowpatch",
            "faster_whisper",
            "ctranslate2",
            "onnxruntime",
        }:
            self.attempts.append(fullname)
            raise AssertionError(f"Protocol test attempted to import {fullname}")
        return None


def main():
    guard = NoModelImports()
    sys.meta_path.insert(0, guard)
    try:
        from backend import __main__ as entry

        def ready(service):
            service._on_model_status("ready", "cpu")

        with (
            patch.object(
                entry.VoiceService, "load_models", autospec=True, side_effect=ready
            ),
            patch.object(
                entry.EmotionService, "load_model", autospec=True, side_effect=ready
            ),
            patch(
                "socket.socket.connect",
                side_effect=AssertionError("Network access in protocol test"),
            ) as connect,
        ):
            result = entry.main()
            connect.assert_not_called()
        # A loader might swallow an import failure and report a model event;
        # still fail the subprocess so such regressions cannot pass silently.
        assert not guard.attempts, f"Unexpected model imports: {guard.attempts}"
        return result
    finally:
        sys.meta_path.remove(guard)


if __name__ == "__main__":
    raise SystemExit(main())
