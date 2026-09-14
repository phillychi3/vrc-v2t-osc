import threading
import unittest
import socket
import queue
import time

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

    def make_sender(self, **kwargs):
        events = queue.Queue()
        sender = OscService(
            client_factory=lambda *_args: self.client,
            on_event=lambda name, data: events.put((name, data)),
            **kwargs,
        )
        self.addCleanup(sender.close)
        return sender, events

    def test_limits_reject_without_truncating_or_sending(self) -> None:
        sender, events = self.make_sender(chat_interval=0)
        for text, reason in (
            ("中" * 145, "too_long"),
            ("😀" * 73, "too_long"),
            ("a\n" * 9 + "b", "too_many_lines"),
        ):
            with self.subTest(reason=reason, text=text):
                self.assertFalse(sender.send_text(text, utterance_id="rejected"))
                name, data = events.get(timeout=1)
                self.assertEqual((name, data["reason"]), ("osc.skipped", reason))
        self.assertEqual(self.client.messages, [])
        self.assertTrue(sender.send_text("中" * 144, utterance_id="boundary"))
        self.assertEqual(events.get(timeout=1)[0], "osc.sent")

    def test_stopping_source_cancels_queued_text_and_faces_but_keeps_manual(self) -> None:
        sender, events = self.make_sender(chat_interval=0.1)
        sender.send_text("first", utterance_id="first")
        self.assertEqual(events.get(timeout=1)[0], "osc.sent")
        sender.send_text("stale", utterance_id="stale", source="voice")
        sender.set_face(2, utterance_id="stale-face", source="voice")
        sender.send_text("manual", utterance_id="manual")
        sender.set_source_generation("voice", 1)
        for _ in range(2):
            name, data = events.get(timeout=1)
            self.assertEqual((name, data["reason"]), ("osc.skipped", "stale"))
        name, data = events.get(timeout=1)
        self.assertEqual((name, data["utteranceId"]), ("osc.sent", "manual"))
        self.assertFalse(sender.send_text("late", source="voice", generation=0))
        sender.send_text("new", utterance_id="new", source="voice", generation=1)
        self.assertEqual(events.get(timeout=1)[0], "osc.sent")
        self.assertEqual(
            [v[0] for _, v in self.client.messages], ["first", "manual", "new"]
        )

    def test_disable_and_close_cancel_pacing_wait_without_replaying(self) -> None:
        sender, events = self.make_sender(chat_interval=60)
        sender.send_text("first", utterance_id="first")
        events.get(timeout=1)
        sender.send_text("pending", utterance_id="pending")
        sender.set_enabled(False)
        self.assertEqual(events.get(timeout=1)[1]["reason"], "disabled")
        sender.set_enabled(True)
        sender.send_text("closing", utterance_id="closing")
        started = time.monotonic()
        sender.close()
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(events.get(timeout=1)[1]["reason"], "closed")
        self.assertEqual(len(self.client.messages), 1)

    def test_queue_is_bounded_and_chat_is_paced(self) -> None:
        sender, events = self.make_sender(chat_interval=0.15, max_queue=1)
        sender.send_text("first", utterance_id="first")
        events.get(timeout=1)
        started = time.monotonic()
        self.assertTrue(sender.send_text("second", utterance_id="second"))
        self.assertFalse(sender.send_text("overflow", utterance_id="overflow"))
        self.assertEqual(events.get(timeout=1)[1]["reason"], "overloaded")
        self.assertEqual(events.get(timeout=1)[0], "osc.sent")
        self.assertGreaterEqual(time.monotonic() - started, 0.12)

    def test_socket_error_reports_failure_and_worker_keeps_running(self) -> None:
        sender, events = self.make_sender(chat_interval=0)
        original = self.client.send_message
        self.client.send_message = lambda *_args: (_ for _ in ()).throw(
            OSError("offline")
        )
        with self.assertLogs("backend.osc", level="ERROR"):
            sender.send_text("failed", utterance_id="failed")
            name, data = events.get(timeout=1)
        self.assertEqual((name, data["reason"]), ("osc.skipped", "send_failed"))
        self.client.send_message = original
        sender.send_text("recovered", utterance_id="recovered")
        self.assertEqual(events.get(timeout=1)[0], "osc.sent")

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
