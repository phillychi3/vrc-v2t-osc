import asyncio
import threading
import time
from pythonosc import udp_client
from queue import Queue
from ui import VoiceToTextApp
from voice import VoiceStream
from emo import Emotion


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

    def _change_face(self, face_id: int):
        try:
            self.client.send_message("/avatar/parameters/v2t_sync_emo", face_id)
            print(f"Face changed to: {face_id}")
        except Exception as e:
            print(f"Error changing face: {e}")

    def send_message(self, message: str):
        """將消息添加到隊列中"""
        self.message_queue.put(message)

    def close(self):
        """關閉 OSC 客戶端"""
        self.running = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.0)
        try:
            if self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
        except:  # noqa: E722
            pass


class VRChatVoiceToText:
    def __init__(self):
        self.osc = OSC()
        self.app = VoiceToTextApp()
        self.app.BINDINGS.append(("ctrl+q", "exit_app", "退出應用"))
        self.app.action_exit_app = self.exit_app
        self.voice = VoiceStream(model_name="large-v3-turbo", language="zh")
        self.running = True
        self.voice_task = None
        self.emotion_loaded = False
        self.emotion_analyzer = None

        self.init_emotion_analyzer()

    def init_emotion_analyzer(self):
        """初始化情緒分析，在後台載入模型"""
        try:
            from emo import Emotion

            self.emotion_analyzer = Emotion(use_async=True)

            print("情緒分析模型正在後台載入中...")

            threading.Thread(
                target=self._check_emotion_model_ready, daemon=True
            ).start()
        except Exception as e:
            print(f"初始化情緒分析模型失敗: {e}")

    def _check_emotion_model_ready(self):
        """定期檢查模型是否載入完成"""
        check_interval = 2
        max_checks = 30
        checks = 0

        while checks < max_checks and self.running:
            if self.emotion_analyzer and self.emotion_analyzer.is_ready():
                self.emotion_loaded = True
                self.app.call_from_thread(
                    self.app.add_system_message,
                    "情緒分析模型載入完成",
                )
                return

            if self.emotion_analyzer and self.emotion_analyzer.get_loading_error():
                self.app.call_from_thread(
                    self.app.add_error_message,
                    f"情緒分析模型載入失敗: {self.emotion_analyzer.get_loading_error()}",
                )
                self.app.call_from_thread(self.app.disable_emotion_switch)
                return

            time.sleep(check_interval)
            checks += 1

        if not self.emotion_loaded and self.running:
            self.app.call_from_thread(
                self.app.add_warning_message,
                "情緒分析模型載入時間過長",
            )
            self.app.call_from_thread(self.app.disable_emotion_switch)

    async def analyze_emotion(self, text):
        if not self.emotion_loaded or not self.app.emotion_enabled:
            return None

        try:
            emotion = await self.emotion_analyzer.predict_async(text)
            return {"emotion": emotion, "face_id": emotion}
        except Exception as e:
            print(f"情緒分析出錯: {e}")
            return None

    def on_speech_detected(self, text):
        """處理語音辨識結果"""
        self.app.call_from_thread(self.app.add_speech_text, text)

        if self.app.emotion_enabled and self.emotion_loaded:
            threading.Thread(
                target=self._process_emotion_and_send, args=(text,), daemon=True
            ).start()
        elif self.app.osc_enabled:
            self.osc.send_message(text)

    def _process_emotion_and_send(self, text):
        """在獨立線程中處理情緒分析並發送結果"""
        emotion_result = self.analyze_emotion(text)

        if self.app.osc_enabled:
            self.osc.send_message(text)
            if emotion_result and self.app.emotion_enabled:
                self.osc._change_face(emotion_result["face_id"])

    def handle_text_input(self, text):
        """處理文字輸入"""
        if self.app.osc_enabled:
            self.osc.send_message(text)
        else:
            print(f"OSC 已停用，不發送消息: {text}")

    def handle_settings_changed(self, setting_name, value):
        """處理設定變更"""
        print(f"設定 '{setting_name}' 變更為: {value}")

        if setting_name == "osc":
            print(f"OSC 功能已{'啟用' if value else '停用'}")
        elif setting_name == "translation":
            pass
        elif setting_name == "emotion":
            if value and not self.emotion_loaded:
                self.app.call_from_thread(
                    self.app.add_system_message,
                    "情緒分析模型未正確載入，此功能無法使用",
                )
                self.app.call_from_thread(self.app.disable_emotion_switch)
            else:
                print(f"情緒辨識已{'啟用' if value else '停用'}")

    async def run_voice_recognition(self):
        self.voice.start_stream(callback=self.on_speech_detected)

        try:
            await self.voice.process_speech()
        except Exception as e:
            self.app.call_from_thread(
                self.app.add_error_message,
                f"語音辨識出錯: {e}",
            )
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

    def setup_ui_callbacks(self):
        self.app.on_input_submitted = self.handle_text_input
        self.app.on_settings_changed = self.handle_settings_changed

    def start(self):
        """啟動整個應用程式"""
        self.start_voice_thread()
        self.setup_ui_callbacks()
        self.app.run()

    def exit_app(self):
        """安全關閉應用程式的所有部分"""
        print("正在關閉應用程式...")

        if self.voice:
            print("正在停止語音服務...")
            self.voice.is_running = False

            if hasattr(self, "voice_task") and self.voice_task:
                print("正在取消語音處理任務...")

                temp_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(temp_loop)
                try:
                    if not self.voice_task.done():
                        # 安全地取消任務
                        self.voice_task.cancel()
                except Exception as e:
                    print(f"取消語音任務發生錯誤: {e}")
                finally:
                    temp_loop.close()

        if (
            hasattr(self, "voice_thread")
            and self.voice_thread
            and self.voice_thread.is_alive()
        ):
            print("等待語音線程結束...")
            self.voice_thread.join(timeout=2.0)
            if self.voice_thread.is_alive():
                print("語音線程未在預期時間內結束")


        if self.osc:
            print("正在關閉OSC服務...")
            try:
                self.osc.running = False
                self.osc.close()
            except Exception as e:
                print(f"關閉OSC時發生錯誤: {e}")


        print("正在關閉UI...")
        self.app.exit()

        print("程式已完全關閉")


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
