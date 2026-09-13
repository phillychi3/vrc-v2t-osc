import json
import subprocess
import sys
import unittest

from backend.settings import default_settings


class BackendProcessTests(unittest.TestCase):
    def test_stdin_eof_exits_without_shutdown_command(self) -> None:
        process = subprocess.run(
            [sys.executable, "-u", "-m", "backend"],
            input=b"", capture_output=True, check=False, timeout=10,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode())

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
        input_bytes = b"".join(
            (json.dumps(request, ensure_ascii=False) + "\n").encode()
            for request in requests
        )
        process = subprocess.run(
            [sys.executable, "-u", "-m", "backend"],
            input=input_bytes,
            capture_output=True,
            check=False,
            timeout=15,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        messages = [json.loads(line) for line in process.stdout.splitlines()]
        responses = {m["id"]: m for m in messages if m["type"] == "response"}
        self.assertTrue(responses["init"]["ok"])
        self.assertEqual(responses["state"]["result"]["backend"], "ready")
        self.assertEqual(responses["stop"]["result"]["state"], "stopped")
        self.assertTrue(
            all(line.startswith(b"{") for line in process.stdout.splitlines())
        )


if __name__ == "__main__":
    unittest.main()
