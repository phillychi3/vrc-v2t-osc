param(
    [switch]$RecreateEnvironment
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
python (Join-Path $PSScriptRoot 'sync-version.py')
if ($LASTEXITCODE -ne 0) { throw 'Failed to synchronize application version' }
$environmentRoot = Join-Path $projectRoot 'build/package-env'
$packagePython = Join-Path $environmentRoot 'Scripts/python.exe'

if ($RecreateEnvironment -and (Test-Path -LiteralPath $environmentRoot)) {
    $resolvedEnvironment = (Resolve-Path -LiteralPath $environmentRoot).Path
    $resolvedBuild = (Resolve-Path -LiteralPath (Join-Path $projectRoot 'build')).Path
    if (-not $resolvedEnvironment.StartsWith($resolvedBuild + [IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to remove package environment outside build: $resolvedEnvironment"
    }
    Remove-Item -LiteralPath $resolvedEnvironment -Recurse -Force
}

if (-not (Test-Path -LiteralPath $packagePython)) {
    python -m venv $environmentRoot
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create packaging environment' }
}

& $packagePython -m pip install --disable-pip-version-check `
    --index-url https://download.pytorch.org/whl/cpu `
    'torch==2.6.0' `
    'torchaudio==2.6.0'
if ($LASTEXITCODE -ne 0) {
    throw "CPU Torch installation failed with exit code $LASTEXITCODE"
}
& $packagePython -m pip install --disable-pip-version-check `
    'numpy==2.2.4' `
    'PyAudioWPatch==0.2.12.8' `
    'pyinstaller==6.22.2' `
    'python-osc==1.9.3' `
    'pydub==0.25.1' `
    "audioop-lts==0.2.2; python_version >= '3.13'" `
    'sentencepiece==0.2.1' `
    'setuptools==78.1.0' `
    'silero-vad==6.2.1' `
    'transformers==4.51.3' `
    'wheel'
if ($LASTEXITCODE -ne 0) {
    throw "Backend dependency installation failed with exit code $LASTEXITCODE"
}
& $packagePython -m pip install --disable-pip-version-check `
    --no-build-isolation `
    'openai-whisper==20240930'
if ($LASTEXITCODE -ne 0) {
    throw "Whisper installation failed with exit code $LASTEXITCODE"
}

Push-Location $projectRoot
try {
    & $packagePython -m PyInstaller `
        --noconfirm `
        --distpath build/python `
        --workpath build/backend-cpu `
        backend.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
