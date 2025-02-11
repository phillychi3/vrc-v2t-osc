import asyncio
from pythonosc import udp_client
import threading
from queue import Queue
import time


class OSC:
    def __init__(self):
        self.client = udp_client.SimpleUDPClient("127.0.0.1", 9000)
        self.message_queue = Queue()
        self.loop = asyncio.new_event_loop()
        self.worker_thread = threading.Thread(
            target=self._process_messages, daemon=True
        )
        self.worker_thread.start()
        self.running = True

    def _process_messages(self):
        asyncio.set_event_loop(self.loop)
        while self.running:
            try:
                if not self.message_queue.empty():
                    message = self.message_queue.get()
                    self._send_message(message)
                time.sleep(0.01)
            except Exception as e:
                print(f"Error processing message: {e}")

    def _send_message(self, message: str):
        """發送消息到 VRChat"""
        try:
            self.client.send_message("/chatbox/input", [message, True])
            print(f"Message sent: {message}")
        except Exception as e:
            print(f"Error sending message: {e}")

    def send_message(self, message: str):
        """將消息添加到隊列中"""
        self.message_queue.put(message)

    def close(self):
        """關閉 OSC 客戶端"""
        self.running = False
        if self.worker_thread.is_alive():
            self.worker_thread.join()
        self.loop.close()


if __name__ == "__main__":
    osc = OSC()

    try:
        osc.send_message("Hello VRChat!")
        time.sleep(1)
        osc.send_message("This is a test message")
        time.sleep(1)
        osc.send_message("Goodbye!")
        time.sleep(1)
    finally:
        osc.close()
