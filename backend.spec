from pathlib import Path
import json
import os
import sys

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    collect_dynamic_libs,
)


project_root = Path(SPECPATH)
variant = os.environ.get("VRC_BUILD_VARIANT", "cpu")
if variant not in {"cpu", "cu126"}:
    raise ValueError("Invalid backend build variant")
marker_dir = project_root / "build" / ("runtime-config-" + variant)
marker_dir.mkdir(parents=True, exist_ok=True)
(marker_dir / "runtime.json").write_text(
    json.dumps({"variant": variant}), encoding="utf-8"
)
datas = collect_data_files("faster_whisper") + [(str(marker_dir / "runtime.json"), ".")]
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
if variant == "cu126":
    for package in ("cublas", "cudnn", "cuda_runtime"):
        roots = [Path(p) / "nvidia" / package for p in sys.path]
        files = [f for root in roots if root.is_dir() for f in root.rglob("*.dll")]
        if not files:
            raise RuntimeError("Missing NVIDIA runtime: " + package)
        binaries += [(str(f), "cuda") for f in files]
        for root in roots:
            if root.is_dir():
                datas += [
                    (str(f), "licenses/" + package)
                    for f in root.rglob("*LICENSE*")
                    if f.is_file()
                ]
hiddenimports = (
    collect_submodules("transformers.models.deberta_v2")
    + collect_submodules("transformers.models.nllb")
    + ["sentencepiece"]
)

analysis = Analysis(
    [str(project_root / "backend" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["torch", "torchaudio", "whisper", "silero_vad", "tensorflow", "jax"],
    noarchive=False,
)
if variant == "cu126":
    # Native dependency analysis can add NVIDIA DLLs again under nvidia/*/bin.
    # Keep one copy in cuda, which configure_native_runtime registers explicitly.
    cuda_names = {
        Path(entry[0]).name.lower()
        for entry in analysis.binaries
        if Path(entry[0]).parts[0] == "cuda"
    }
    analysis.binaries = [
        entry
        for entry in analysis.binaries
        if Path(entry[0]).name.lower() not in cuda_names
        or Path(entry[0]).parts[0] == "cuda"
    ]
if sys.platform == "win32":
    # Python/CTranslate2 can contribute an older VC++ runtime (14.31), while
    # ONNX Runtime needs a newer, consistent set. Never mix these DLL versions.
    import pefile

    crt_root = Path(
        os.environ.get(
            "VRC_MSVC_RUNTIME_DIR", Path(os.environ["SystemRoot"]) / "System32"
        )
    )
    crt_names = {
        "msvcp140.dll",
        "msvcp140_1.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    }
    for name in crt_names:
        source = crt_root / name
        with pefile.PE(str(source)) as pe:
            version = pe.VS_FIXEDFILEINFO[0].FileVersionMS
            if (version >> 16, version & 65535) < (14, 40):
                raise RuntimeError(
                    "Install the current VC++ 2022 x64 Redistributable on the build host"
                )
    analysis.binaries = [
        entry
        for entry in analysis.binaries
        if Path(entry[0]).name.lower() not in crt_names
    ]
    analysis.binaries += [
        (name, str(crt_root / name), "BINARY") for name in sorted(crt_names)
    ]
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="vrc-v2t-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="vrc-v2t-backend",
)
