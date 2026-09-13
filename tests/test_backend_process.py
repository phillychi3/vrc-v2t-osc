import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.settings import default_settings


class BackendProcessTests(unittest.TestCase):
    def run_protocol(self, requests):
        with tempfile.TemporaryDirectory(prefix="vrc-protocol-") as directory:
            for request in requests:
                if request["method"] == "system.initialize":
                    request["params"]["dataPath"] = directory
            input_bytes = b"".join(
                (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
                for request in requests
            )
            try:
                process = subprocess.run(
                    [sys.executable, "-u", "-m", "tests.fixtures.backend_protocol"],
                    cwd=Path(__file__).resolve().parents[1],
                    env={
                        **os.environ,
                        "HF_HUB_OFFLINE": "1",
                        "HF_HOME": str(Path(directory) / "huggingface"),
                        "XDG_CACHE_HOME": str(Path(directory) / "cache"),
                    },
                    input=input_bytes,
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
            except subprocess.TimeoutExpired as exc:
                self.fail(
                    "Protocol fixture exceeded 15 seconds\n"
                    f"stdout: {(exc.stdout or b'').decode('utf-8', errors='replace')}\n"
                    f"stderr: {(exc.stderr or b'').decode('utf-8', errors='replace')}"
                )
            self.assertEqual(
                process.returncode, 0, process.stderr.decode("utf-8", errors="replace")
            )
            self.assertEqual(
                list(Path(directory).rglob("*")),
                [],
                "Protocol test wrote model/data files",
            )
            return process

    def test_stdin_eof_exits_without_shutdown_command(self) -> None:
        self.run_protocol([])

    def test_stdin_eof_closes_initialized_services(self) -> None:
        process = self.run_protocol(
            [
                {
                    "v": 1,
                    "type": "request",
                    "id": "init",
                    "method": "system.initialize",
                    "params": {"settings": default_settings()},
                }
            ]
        )
        messages = [json.loads(line) for line in process.stdout.splitlines()]
        response = next(message for message in messages if message.get("id") == "init")
        self.assertTrue(response["ok"])

    def test_ndjson_initialize_state_and_shutdown(self) -> None:
        requests = [
            {
                "v": 1,
                "type": "request",
                "id": "init",
                "method": "system.initialize",
                "params": {"settings": default_settings(), "dataPath": "C:/data"},
            },
            {
                "v": 1,
                "type": "request",
                "id": "state",
                "method": "system.getState",
                "params": {},
            },
            {
                "v": 1,
                "type": "request",
                "id": "stop",
                "method": "system.shutdown",
                "params": {},
            },
        ]
        process = self.run_protocol(requests)
        messages = [json.loads(line) for line in process.stdout.splitlines()]
        responses = {m["id"]: m for m in messages if m["type"] == "response"}
        self.assertTrue(responses["init"]["ok"])
        self.assertEqual(responses["state"]["result"]["backend"], "ready")
        self.assertEqual(
            responses["state"]["result"]["models"]["speech"]["status"], "ready"
        )
        self.assertEqual(
            responses["state"]["result"]["models"]["emotion"]["status"], "ready"
        )
        self.assertEqual(responses["stop"]["result"]["state"], "stopped")
        self.assertTrue(
            all(line.startswith(b"{") for line in process.stdout.splitlines())
        )


if __name__ == "__main__":
    unittest.main()
