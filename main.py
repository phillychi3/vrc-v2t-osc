import asyncio
import threading
import time
from pythonosc import udp_client
from queue import Queue
from ui import VoiceToTextApp
from voice import VoiceStream


class OSC:
    def __init__(self):
        self.client = udp_client.SimpleUDPClient("127.0.0.1", 9000)
        self.running = True
        self.message_queue = Queue()
        self.loop = asyncio.new_event_loop()
        self.worker_thread = threading.Thread(
            target=self._process_messages, daemon=True
        )
        self.worker_thread.start()

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


class VRChatVoiceToText:
    def __init__(self):
        self.osc = OSC()
        self.app = VoiceToTextApp()
        self.app.BINDINGS.append(("ctrl+q", "exit_app", "退出應用"))
        self.app.action_exit_app = self.exit_app
        self.voice = VoiceStream(model_name="turbo")
        self.running = True
        self.voice_task = None

    def on_speech_detected(self, text):
        self.app.call_from_thread(self.app.add_speech_text, text)
        self.osc.send_message(text)

    async def run_voice_recognition(self):
        self.voice.start_stream(callback=self.on_speech_detected)

        try:
            await self.voice.process_speech()
        except Exception as e:
            print(f"語音識別發生錯誤: {e}")
        finally:
            if self.voice:
                self.voice.stop_stream()

    def start_voice_thread(self):
        """在背景啟動異步語音處理"""

        def run_async_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.voice_task = loop.create_task(self.run_voice_recognition())
            loop.run_until_complete(self.voice_task)

        self.voice_thread = threading.Thread(target=run_async_loop, daemon=True)
        self.voice_thread.start()

    def start(self):
        self.start_voice_thread()
        self.app.on_input_submitted = lambda text: self.osc.send_message(text)
        self.app.run()

    def exit_app(self):
        if self.voice:
            self.voice.is_running = False
        if self.osc:
            self.osc.close()
        self.app.exit()


def main():
    app = VRChatVoiceToText()

    try:
        app.start()
    except KeyboardInterrupt:
        print("程式正在關閉...")
    finally:
        app.exit_app()


if __name__ == "__main__":
    main()
