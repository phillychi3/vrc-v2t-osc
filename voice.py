import pyaudio
import whisper


class VoiceStream:
    def __init__(self):
        self.chunk = 1024
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 44100
        self.pyaudio = None

    def start_stream(self):
        self.pyaudio = pyaudio.PyAudio()
        self.stream = self.pyaudio.open(
            format=self.format,
            channels=self.channels,
            rate=self.rate,
            input=True,
            frames_per_buffer=self.chunk,
        )

    def read_stream(self):
        return self.stream.read(self.chunk)

    def stop_stream(self):
        self.stream.stop_stream()
        self.stream.close()
        self.pyaudio.terminate()


if __name__ == "__main__":
    voice = VoiceStream()
    voice.start_stream()
    try:
        while True:
            print(voice.read_stream())
    except KeyboardInterrupt:
        voice.stop_stream()
