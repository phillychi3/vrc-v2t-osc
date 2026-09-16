from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPECPATH)
# Whisper and Silero load these assets by package-relative path. PyInstaller's
# standard hooks handle Torch/NumPy binaries; only application-used dynamic
# Transformer families are added instead of collecting every model and test.
datas = collect_data_files("whisper") + collect_data_files("silero_vad")
binaries = []
hiddenimports = (
    collect_submodules("transformers.models.bert")
    + collect_submodules("transformers.models.nllb")
    + collect_submodules("transformers.pipelines")
    + ["sentencepiece"]
)

analysis = Analysis(
    [str(project_root / "backend" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    noarchive=False,
)
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
