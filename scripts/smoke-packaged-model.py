from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.settings import default_settings  # noqa: E402


def main() -> int:
    executable = Path(sys.argv[1]).resolve()
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0
    messages: queue.Queue[dict[str, object]] = queue.Queue()
    stderr_lines: list[str] = []
    process = subprocess.Popen(
        [str(executable)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    def read_stdout() -> None:
        for line in process.stdout:
            messages.put(json.loads(line))

    def read_stderr() -> None:
        for line in process.stderr:
            stderr_lines.append(line.rstrip())

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()
    settings = default_settings()
    started_at = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="vrc-v2t-model-smoke-") as data_path:
        send(
            process.stdin,
            "initialize",
            "system.initialize",
            {"settings": settings, "dataPath": data_path},
        )
        deadline = started_at + timeout
        try:
            while time.monotonic() < deadline:
                try:
                    message = messages.get(timeout=0.25)
                except queue.Empty:
                    if process.poll() is not None:
                        raise RuntimeError(f"backend exited with {process.returncode}")
                    continue
                if (
                    message.get("type") != "event"
                    or message.get("event") != "model.status"
                ):
                    continue
                data = message.get("data")
                if not isinstance(data, dict) or data.get("model") != "speech":
                    continue
                status = data.get("status")
                elapsed = time.monotonic() - started_at
                print(f"speech model {status} after {elapsed:.1f}s")
                if status == "failed":
                    raise RuntimeError("speech model reported failure")
                if status == "ready":
                    send(process.stdin, "shutdown", "system.shutdown", {})
                    process.wait(timeout=15)
                    print(f"packaged speech model smoke test passed: {executable}")
                    return 0
            raise TimeoutError(f"speech model did not become ready within {timeout:.0f}s")
        except Exception as exc:
            process.kill()
            process.wait(timeout=5)
            details = "\n".join(stderr_lines[-100:])
            raise RuntimeError(f"{exc}\n{details}") from exc


def send(
    stream: object,
    request_id: str,
    method: str,
    params: dict[str, object],
) -> None:
    payload = {
        "v": 1,
        "type": "request",
        "id": request_id,
        "method": method,
        "params": params,
    }
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")  # type: ignore[attr-defined]
    stream.flush()  # type: ignore[attr-defined]


if __name__ == "__main__":
    raise SystemExit(main())
