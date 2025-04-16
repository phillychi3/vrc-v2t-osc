import pyaudio
import whisper
import asyncio
import numpy as np
import queue
import threading
from typing import Callable, Optional


class VoiceStream:
    def __init__(self, model_name: str = "base"):
        self.chunk = 1024
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 16000
        self.pyaudio = None
        self.stream = None
        self.audio_queue = queue.Queue()
        self.is_running = False
        self.silence_threshold = 200
        self.silence_frames = 0
        self.min_silence_frames = 30
        self.is_speaking = False
        self.frames = []
        self.model = whisper.load_model(model_name)
        self.callback = None
        self.stream_thread = None

    def start_stream(self, callback: Optional[Callable[[str], None]] = None):
        self.callback = callback
        self.pyaudio = pyaudio.PyAudio()
        self.stream = self.pyaudio.open(
            format=self.format,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk,
        )
        self.is_running = True
        self.stream_thread = threading.Thread(target=self._process_audio)
        self.stream_thread.daemon = True
        self.stream_thread.start()

    def _process_audio(self):
        while self.is_running:
            try:
                audio_data = self.stream.read(self.chunk, exception_on_overflow=False)
                audio_array = np.frombuffer(audio_data, dtype=np.int16)
                volume = np.abs(audio_array).mean()

                if volume > self.silence_threshold:
                    self.silence_frames = 0
                    if not self.is_speaking:
                        self.is_speaking = True
                        self.frames = []
                    self.frames.append(audio_data)
                else:
                    # 靜音
                    if self.is_speaking:
                        self.silence_frames += 1
                        self.frames.append(audio_data)
                        if self.silence_frames > self.min_silence_frames:
                            self.is_speaking = False
                            if self.frames and len(self.frames) > 5:
                                self.audio_queue.put(b"".join(self.frames))
                                self.frames = []
            except Exception as e:
                print(f"讀取聲音出錯: {e}")

    async def process_speech(self):
        while self.is_running:
            try:
                if not self.audio_queue.empty():
                    audio_data = self.audio_queue.get()
                    audio_np = (
                        np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
                        / 32768.0
                    )
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, lambda: self.model.transcribe(audio_np)
                    )

                    text = result["text"].strip()
                    if text and self.callback:
                        self.callback(text)
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"處理語音識別時出錯: {e}")
                await asyncio.sleep(1)

        self.is_running = False
        if self.stream_thread:
            self.stream_thread.join(timeout=2)
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if self.pyaudio:
            self.pyaudio.terminate()


async def main():
    def on_speech_detected(text):
        print(f"偵測到語音: {text}")

    voice = VoiceStream(model_name="tiny")
    voice.start_stream(callback=on_speech_detected)

    try:
        await voice.process_speech()
    except KeyboardInterrupt:
        print("程式結束中...")
    finally:
        voice.stop_stream()


if __name__ == "__main__":
    asyncio.run(main())
