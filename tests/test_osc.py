import threading
import unittest
import socket

from pythonosc.osc_packet import OscPacket

from backend.osc import OscService


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []
        self.received = threading.Event()

    def send_message(self, address: str, value: object) -> None:
        self.messages.append((address, value))
        self.received.set()


class OscTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeClient()
        self.osc = OscService(client_factory=lambda _host, _port: self.client)

    def tearDown(self) -> None:
        self.osc.close()

    def test_text_address_and_argument_types(self) -> None:
        self.assertTrue(self.osc.send_text("你好"))
        self.assertTrue(self.client.received.wait(1))
        self.assertEqual(self.client.messages, [("/chatbox/input", ["你好", True])])

    def test_face_parameter(self) -> None:
        self.assertTrue(self.osc.set_face(2))
        self.assertTrue(self.client.received.wait(1))
        self.assertEqual(
            self.client.messages,
            [("/avatar/parameters/v2t_sync_emo", 2)],
        )

    def test_disabled_sender_rejects_new_messages(self) -> None:
        self.osc.set_enabled(False)
        self.assertFalse(self.osc.send_text("不應送出"))
        self.assertFalse(self.client.received.wait(0.1))

    def test_close_is_idempotent(self) -> None:
        self.osc.close()
        self.osc.close()
        self.assertFalse(self.osc.send_text("不應送出"))

    def test_real_udp_preserves_order_types_and_disable(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(1)
            sender = OscService(port=receiver.getsockname()[1])
            try:
                self.assertTrue(sender.send_text("中文 UDP"))
                self.assertTrue(sender.set_face(2))
                messages = [
                    OscPacket(receiver.recv(4096)).messages[0].message for _ in range(2)
                ]
                self.assertEqual(messages[0].address, "/chatbox/input")
                self.assertEqual(messages[0].params, ["中文 UDP", True])
                self.assertIs(type(messages[0].params[1]), bool)
                self.assertEqual(messages[1].address, "/avatar/parameters/v2t_sync_emo")
                self.assertEqual(messages[1].params, [2])
                self.assertIs(type(messages[1].params[0]), int)
                sender.set_enabled(False)
                self.assertFalse(sender.send_text("不可發送"))
                receiver.settimeout(0.1)
                with self.assertRaises(socket.timeout):
                    receiver.recv(4096)
            finally:
                sender.close()
            self.assertEqual(sender._client._sock.fileno(), -1)


if __name__ == "__main__":
    unittest.main()
