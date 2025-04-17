import pyaudio
import whisper
import asyncio
import numpy as np
import queue
import threading
from typing import Callable, Optional
import torch


class VoiceStream:
    def __init__(self, model_name: str = "base", language: str = "en"):
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
        self.language = language

        torch.set_num_threads(1)
        self.vad_model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad", model="silero_vad"
        )
        self.get_speech_timestamps = utils[0]

        self.get_speech_timestamps = utils[0]

        self.vad_threshold = 0.5
        self.min_silence_ms = 500

        self.audio_buffer = []
        self.buffer_max_len = int(self.rate * 10)
        self.silence_counter = 0
        self.min_silence_frames = int(
            self.min_silence_ms * self.rate / 1000 / self.chunk
        )

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
                self.audio_buffer.extend(audio_array)

                if len(self.audio_buffer) >= self.rate // 2:
                    tensor_data = torch.FloatTensor(self.audio_buffer) / 32768.0
                    speech_timestamps = self.get_speech_timestamps(
                        tensor_data,
                        self.vad_model,
                        return_seconds=False,
                    )

                    if speech_timestamps:
                        if not self.is_speaking:
                            self.is_speaking = True
                            self.frames = []
                            print("語音開始")
                            self.frames.append(audio_data)
                        self.silence_counter = 0
                        self.frames.append(audio_data)
                    else:
                        if self.is_speaking:
                            self.silence_counter += 1
                            self.frames.append(audio_data)

                            actual_silence_sec = (
                                self.silence_counter * self.chunk
                            ) / self.rate
                            if actual_silence_sec > 0.5:
                                self.is_speaking = False
                                print(f"語音結束 (靜音 {actual_silence_sec:.2f} 秒)")
                                if len(self.frames) > 2:
                                    self.audio_queue.put(b"".join(self.frames))
                                self.frames = []

                    self.audio_buffer = self.audio_buffer[-self.rate // 2 :]

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
                        None,
                        lambda: self.model.transcribe(audio_np, language=self.language),
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

    voice = VoiceStream(model_name="large-v3-turbo", language="zh")
    voice.start_stream(callback=on_speech_detected)

    try:
        await voice.process_speech()
    except KeyboardInterrupt:
        print("程式結束中...")
    finally:
        voice.stop_stream()


if __name__ == "__main__":
    asyncio.run(main())
