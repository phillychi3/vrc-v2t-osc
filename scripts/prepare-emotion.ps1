$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$modelRoot = Join-Path $projectRoot 'build/models/emotion'
$manifest = Join-Path $modelRoot 'manifest.json'
if ((Test-Path $manifest) -and (Test-Path (Join-Path $modelRoot 'model.onnx'))) {
    $metadata = Get-Content $manifest -Raw -Encoding utf8 | ConvertFrom-Json
    $hash = (Get-FileHash (Join-Path $modelRoot 'model.onnx') -Algorithm SHA256).Hash
    if ($metadata.revision -eq 'a8b83db21e778e99d56058124eaab4ab99efe598' -and $metadata.format -eq 'onnx-fp32' -and $metadata.sha256 -eq $hash.ToLowerInvariant() -and (Test-Path (Join-Path $modelRoot 'tokenizer.json')) -and (Test-Path (Join-Path $modelRoot 'tokenizer_config.json'))) {
        Write-Host 'Using validated emotion ONNX artifact'
        return
    }
}
$exportRoot = Join-Path $projectRoot 'build/export-env'
$exportPython = Join-Path $exportRoot 'Scripts/python.exe'
if (-not (Test-Path $exportPython)) {
    python -m venv $exportRoot
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create model export environment' }
}
& $exportPython -m pip --no-input --keyring-provider disabled install --disable-pip-version-check --no-cache-dir --index-url https://download.pytorch.org/whl/cpu 'torch==2.6.0+cpu'
if ($LASTEXITCODE -ne 0) { throw 'Cannot prepare build-only Torch' }
& $exportPython -m pip --no-input --keyring-provider disabled install --disable-pip-version-check --no-cache-dir 'transformers==4.51.3' 'numpy==2.2.4' 'sentencepiece==0.2.1' 'onnx==1.17.0' 'onnxruntime==1.22.0'
if ($LASTEXITCODE -ne 0) { throw 'Cannot prepare model export tools' }
& $exportPython (Join-Path $PSScriptRoot 'export-emotion.py') --output $modelRoot
if ($LASTEXITCODE -ne 0) { throw 'Emotion ONNX export or validation failed' }
