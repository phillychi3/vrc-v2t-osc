"""Times a real speaker-loopback start/stop cycle.

The stop path used to free a PortAudio stream while a capture thread sat in a
blocking `read()`, which crashed the process on a silent device. Run this with
nothing playing: both phases should finish well inside a second.
"""

from __future__ import annotations

import sys
import time

from backend.voice import VoiceService


def main() -> int:
    service = VoiceService(
        model_name="tiny",
        language="zh",
        save_audio=False,
        save_dir=".",
        on_model_status=lambda *_args: None,
        on_recording_state=lambda state, source: print(f"  {source}: {state}"),
        on_transcript=lambda *_args: None,
        on_error=lambda message: print(f"  error: {message}"),
    )
    # Skip model loading: this exercises capture lifecycle, not transcription.
    service._ready = True
    service._vad_model = object()
    service._get_speech_timestamps = lambda *_args, **_kwargs: []

    started = time.perf_counter()
    service.start("speaker:default", "speaker")
    print(f"start: {time.perf_counter() - started:.3f}s")

    session = service._sessions["speaker"]
    delivered = 0
    original_submit = session.submit_buffer

    def counting_submit(data: bytes) -> None:
        nonlocal delivered
        delivered += 1
        original_submit(data)

    session.submit_buffer = counting_submit  # type: ignore[method-assign]

    time.sleep(1.5)
    print(f"buffers delivered by the PortAudio callback: {delivered}")

    stopping = time.perf_counter()
    service.stop("speaker")
    stop_seconds = time.perf_counter() - stopping
    print(f"stop:  {stop_seconds:.3f}s")

    closing = time.perf_counter()
    service.close()
    print(f"close: {time.perf_counter() - closing:.3f}s")

    if stop_seconds > 1.0:
        print(f"FAIL: stop took {stop_seconds:.3f}s on a quiet device")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
