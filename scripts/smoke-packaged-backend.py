from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.settings import default_settings  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: smoke-packaged-backend.py <backend executable>")
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit(f"packaged backend does not exist: {executable}")

    dependency_check = subprocess.run(
        [str(executable), "--self-test"],
        capture_output=True,
        check=False,
        timeout=120,
    )
    if dependency_check.returncode != 0:
        sys.stderr.buffer.write(dependency_check.stderr)
        return dependency_check.returncode
    dependency_result = json.loads(dependency_check.stdout)
    assert dependency_result["ok"] is True
    assert "+cpu" in dependency_result["torch"]

    # This is the real-model integration tier. Keep it small and explicit;
    # the regular protocol tests use a fixture and never load/download models.
    settings = default_settings()
    settings["speech"]["model"] = "tiny"
    settings["emotion"]["enabled"] = False
    settings["osc"]["enabled"] = False
    with tempfile.TemporaryDirectory(prefix="vrc-v2t-packaged-") as data_path:
        requests = [
            {
                "v": 1,
                "type": "request",
                "id": "init",
                "method": "system.initialize",
                "params": {
                    "settings": settings,
                    "dataPath": data_path,
                },
            },
            {
                "v": 1,
                "type": "request",
                "id": "providers",
                "method": "translation.listProviders",
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
            (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
            for request in requests
        )
        process = subprocess.run(
            [str(executable)],
            input=input_bytes,
            capture_output=True,
            check=False,
            timeout=120,
        )

    if process.returncode != 0:
        sys.stderr.buffer.write(process.stderr)
        return process.returncode
    messages = [json.loads(line) for line in process.stdout.splitlines()]
    responses = {
        message["id"]: message
        for message in messages
        if message.get("type") == "response"
    }
    assert responses["init"]["ok"] is True
    assert responses["init"]["result"]["models"]["speech"]["status"] == "ready"
    assert responses["providers"]["ok"] is True
    assert {item["id"] for item in responses["providers"]["result"]["providers"]} == {
        "transformers",
        "libretranslate",
        "deepl",
    }
    assert responses["stop"]["result"] == {"state": "stopped"}
    print(f"packaged backend smoke test passed: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
