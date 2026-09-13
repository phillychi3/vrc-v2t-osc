import contextlib
import io
import json
import sys
import unittest

from backend.__main__ import MessageWriter


class _CapturedStdout:
    def __init__(self, buffer: io.BytesIO) -> None:
        self.buffer = buffer


class MessageWriterTests(unittest.TestCase):
    def test_protocol_keeps_original_stream_during_stdout_redirect(self) -> None:
        protocol = io.BytesIO()
        original_stdout = sys.stdout
        sys.stdout = _CapturedStdout(protocol)  # type: ignore[assignment]
        try:
            writer = MessageWriter()
            with contextlib.redirect_stdout(io.StringIO()):
                writer.send({"v": 1, "type": "response", "id": "test", "ok": True})
                writer.close()
        finally:
            sys.stdout = original_stdout

        message = json.loads(protocol.getvalue())
        self.assertEqual(message["id"], "test")
        self.assertTrue(message["ok"])


if __name__ == "__main__":
    unittest.main()
