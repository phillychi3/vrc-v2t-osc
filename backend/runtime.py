from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_dll_handles: list[object] = []
_configured = False


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def backend_variant() -> str:
    marker = resource_root() / "runtime.json"
    if marker.exists():
        return str(json.loads(marker.read_text(encoding="utf-8"))["variant"])
    return "auto"


def configure_native_runtime() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    if sys.platform != "win32":
        return
    # CTranslate2 loads CUDA libraries dynamically. Scope PATH changes to this
    # child process; never modify the user's system PATH or use PyTorch DLLs.
    directories = [resource_root() / "cuda"]
    for entry in sys.path:
        for package in ("cublas", "cudnn", "cuda_runtime"):
            directories.append(Path(entry) / "nvidia" / package / "bin")
    for directory in directories:
        if directory.is_dir():
            _dll_handles.append(os.add_dll_directory(str(directory)))
            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")


def cpu_session(path: str | Path):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = min(4, max(1, (os.cpu_count() or 2) // 2))
    options.inter_op_num_threads = 1
    return ort.InferenceSession(
        str(path), sess_options=options, providers=["CPUExecutionProvider"]
    )


def emotion_model_directory() -> Path:
    override = os.environ.get("VRC_EMOTION_MODEL_DIR")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        # Shared by CPU/CUDA backends; replacing resources/backend must not
        # duplicate or delete this model.
        return Path(sys.executable).resolve().parent.parent / "models" / "emotion"
    return resource_root() / "build" / "models" / "emotion"
