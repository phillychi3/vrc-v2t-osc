param(
    [ValidateSet('cpu', 'cu126')][string]$Variant = 'cpu',
    [switch]$RecreateEnvironment
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
python (Join-Path $PSScriptRoot 'sync-version.py')
if ($LASTEXITCODE -ne 0) { throw 'Failed to synchronize application version' }
$environmentRoot = Join-Path $projectRoot "build/runtime-env-$Variant"
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

& $packagePython -m pip --no-input --keyring-provider disabled install --disable-pip-version-check --no-cache-dir `
    -r (Join-Path $PSScriptRoot 'requirements-runtime.txt') 'pyinstaller==6.22.2'
if ($LASTEXITCODE -ne 0) {
    throw "Backend dependency installation failed with exit code $LASTEXITCODE"
}
if ($Variant -eq 'cu126') {
    & $packagePython -m pip --no-input --keyring-provider disabled install --disable-pip-version-check --no-cache-dir `
        'nvidia-cublas-cu12==12.6.4.1' 'nvidia-cudnn-cu12==9.5.1.17' 'nvidia-cuda-runtime-cu12==12.6.77'
    if ($LASTEXITCODE -ne 0) { throw 'CUDA runtime installation failed' }
}
& $packagePython -c "import importlib.util; assert importlib.util.find_spec('torch') is None, 'Runtime environment contains Torch; recreate it'"
if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency isolation failed' }

Push-Location $projectRoot
try {
    $previousVariant = $env:VRC_BUILD_VARIANT
    $env:VRC_BUILD_VARIANT = $Variant
    & $packagePython -m PyInstaller `
        --noconfirm `
        --distpath "build/python-$Variant" `
        --workpath "build/backend-$Variant" `
        backend.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    $env:VRC_BUILD_VARIANT = $previousVariant
    Pop-Location
}
