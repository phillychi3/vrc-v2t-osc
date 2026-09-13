import unittest

from backend.protocol import (
    MAX_LINE_BYTES,
    ProtocolError,
    decode_request,
    encode_message,
    response_ok,
)


class ProtocolTests(unittest.TestCase):
    def test_decodes_chinese_request(self) -> None:
        request = decode_request(
            '{"v":1,"type":"request","id":"一","method":"text.send",'
            '"params":{"text":"你好"}}'.encode()
        )
        self.assertEqual(request.request_id, "一")
        self.assertEqual(request.params["text"], "你好")

    def test_rejects_invalid_json(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "無法解析") as caught:
            decode_request(b"{")
        self.assertEqual(caught.exception.code, "INVALID_JSON")

    def test_rejects_wrong_version(self) -> None:
        with self.assertRaises(ProtocolError) as caught:
            decode_request(b'{"v":2,"type":"request","id":"1","method":"x"}')
        self.assertEqual(caught.exception.code, "VERSION_MISMATCH")

    def test_rejects_oversized_line(self) -> None:
        with self.assertRaises(ProtocolError) as caught:
            decode_request(b"x" * (MAX_LINE_BYTES + 1))
        self.assertEqual(caught.exception.code, "LINE_TOO_LARGE")

    def test_encoder_preserves_unicode_and_newline(self) -> None:
        encoded = encode_message(response_ok("1", {"text": "你好"}))
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertIn("你好".encode(), encoded)


if __name__ == "__main__":
    unittest.main()
