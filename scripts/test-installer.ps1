param([string]$Makensis)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
# Ask electron-builder where its toolset lives instead of guessing a cache
# layout: the directory names are versioned and have changed between releases,
# and this also downloads the toolset when it has not been fetched yet.
# StdUtils and INetC ship with its NSIS resources, not with NSIS itself.
# Answer through a file: a first-time toolset download logs to stdout too.
$toolsetFile = Join-Path ([IO.Path]::GetTempPath()) ('vrc-nsis-toolset-' + [guid]::NewGuid() + '.json')
& node -e @'
const { createRequire } = require('node:module')
const fs = require('node:fs')
const path = require('node:path')
const builderRequire = createRequire(require.resolve('electron-builder'))
const { getMakeNsisPath, getNsisPluginsPath } = builderRequire('app-builder-lib/out/toolsets/windows')
const root = path.dirname(builderRequire.resolve('app-builder-lib/package.json'))
Promise.all([getMakeNsisPath(), getNsisPluginsPath()]).then(([makensis, plugins]) => {
	fs.writeFileSync(
		process.argv[1],
		JSON.stringify({
			makensis: makensis.path,
			env: makensis.env ?? {},
			plugins: path.join(plugins, 'x86-unicode'),
			templates: path.join(root, 'templates', 'nsis', 'include')
		})
	)
})
'@ $toolsetFile
if ($LASTEXITCODE -ne 0) { throw 'Could not resolve the electron-builder NSIS toolset' }
$toolset = Get-Content -LiteralPath $toolsetFile -Raw | ConvertFrom-Json
Remove-Item -LiteralPath $toolsetFile -Force
if (-not $Makensis) { $Makensis = $toolset.makensis }
$pluginDir = $toolset.plugins
$templateInclude = $toolset.templates
foreach ($required in $Makensis, $pluginDir, $templateInclude) {
    if (-not (Test-Path -LiteralPath $required)) { throw "NSIS toolset is incomplete: $required" }
}
# The legacy bundle needs NSISDIR pointed at itself; newer ones set it themselves.
$originalNsisDir = $env:NSISDIR
foreach ($entry in $toolset.env.PSObject.Properties) {
    Set-Item -LiteralPath "Env:$($entry.Name)" -Value $entry.Value
}

$testRoot = Join-Path $projectRoot ('build/installer-test-' + [guid]::NewGuid())
foreach ($variant in 'cpu', 'cu126') {
    $root = Join-Path $testRoot "build/python-$variant/vrc-v2t-backend"
    New-Item -ItemType Directory -Force -Path (Join-Path $root '_internal/native') | Out-Null
    Set-Content (Join-Path $root "$variant-only.txt") $variant
    Set-Content (Join-Path $root 'runtime.txt') $variant
    Set-Content (Join-Path $root '_internal/native/library.txt') "nested $variant library"
}
$emotionSource = Join-Path $testRoot 'build/models/emotion'
New-Item -ItemType Directory -Force -Path $emotionSource | Out-Null
Set-Content (Join-Path $emotionSource 'model.onnx') 'stand-in emotion model'
Set-Content (Join-Path $emotionSource 'tokenizer.json') '{}'

# Reserve a loopback port, then let the compiled installer download from it.
$reservation = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$reservation.Start()
$port = $reservation.LocalEndpoint.Port
$reservation.Stop()
$payload = Join-Path $testRoot 'build/installer-payload'
$env:VRC_ASSET_BASE_URL = "http://127.0.0.1:$port"
# The stand-in backends have no vrc-v2t-backend.exe to check for.
$env:VRC_BACKEND_ENTRY = 'runtime.txt'
try {
    node (Join-Path $PSScriptRoot 'prepare-assets.cjs') $testRoot
    if ($LASTEXITCODE -ne 0) { throw 'Installer fixture archives failed' }
} finally {
    Remove-Item Env:VRC_ASSET_BASE_URL, Env:VRC_BACKEND_ENTRY
}
$assets = Get-Content (Join-Path $payload 'installer-assets.json') -Raw | ConvertFrom-Json
$archiveOf = @{}
foreach ($asset in $assets.assets) { $archiveOf[$asset.id] = $asset.name }

$registryKey = 'Software\VRC2T-InstallerTest-' + [guid]::NewGuid()
$include = Join-Path $PSScriptRoot 'installer.nsh'
$harness = @'
Unicode true
RequestExecutionLevel user
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!addincludedir "@TEMPLATES@"
!addplugindir /x86-unicode "@PLUGINS@"
!include "StdUtils.nsh"
!define PROJECT_DIR "@ROOT@"
!define INSTALL_REGISTRY_KEY "@KEY@"
!define SHELL_CONTEXT HKCU
!include "@INCLUDE@"
; electron-builder loads the custom include before installer.nsi loads MUI2.
!include "MUI2.nsh"
Name "VRC2T installer test"
OutFile "@ROOT@\setup-test.exe"
InstallDir "@ROOT@\app"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro customPageAfterChangeDir
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "TradChinese"
!insertmacro MUI_LANGUAGE "English"
!insertmacro customHeader
Function .onInit
  !insertmacro customInit
FunctionEnd
Section
  ; The web installer extracts an app package that carries neither the backend
  ; nor the model, so customInstall has to create both directories itself.
  SetOutPath "$INSTDIR"
  !insertmacro customInstall
SectionEnd
'@
$harness = $harness.Replace('@ROOT@', $testRoot).Replace('@KEY@', $registryKey).
    Replace('@INCLUDE@', $include).Replace('@TEMPLATES@', $templateInclude).
    Replace('@PLUGINS@', $pluginDir)
$harnessFile = Join-Path $testRoot 'harness.nsi'
Set-Content -LiteralPath $harnessFile -Value $harness -Encoding utf8
# Match electron-builder's compressor. The assets are already 7-Zip archives.
$compilerOutput = & $Makensis /WX /V3 '/XSetCompressor zlib' $harnessFile 2>&1
$compilerExitCode = $LASTEXITCODE
$compilerOutput | Write-Output
if ($compilerExitCode -ne 0) { throw 'Installer test harness did not compile' }
$setup = Join-Path $testRoot 'setup-test.exe'
$appRoot = Join-Path $testRoot 'app'
$backendDir = Join-Path $appRoot 'resources/backend'
$emotionDir = Join-Path $appRoot 'resources/models/emotion'

function Start-Payload {
    $process = Start-Process -FilePath 'node' `
        -ArgumentList (Join-Path $PSScriptRoot 'serve-payload.mjs'), $payload, $port `
        -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $probe = [Net.Sockets.TcpClient]::new('127.0.0.1', $port)
            $probe.Close()
            return $process
        } catch { Start-Sleep -Milliseconds 100 }
    }
    Stop-Process -Id $process.Id -Force
    throw 'Payload server did not start'
}

function Invoke-Setup([string]$Option) {
    $child = Start-Process -FilePath $setup -ArgumentList "/S $Option /D=$appRoot" `
        -WindowStyle Hidden -PassThru
    if (-not $child.WaitForExit(120000)) {
        Stop-Process -Id $child.Id -Force
        throw 'Installer test timed out'
    }
    return $child.ExitCode
}

function Invoke-Install([string]$Option, [string]$Expected, [bool]$WithEmotion) {
    $code = Invoke-Setup $Option
    if ($code -ne 0) { throw "Installer failed: $code" }
    if ((Get-Content (Join-Path $backendDir 'runtime.txt') -Raw).Trim() -ne $Expected) {
        throw "Expected $Expected backend"
    }
    if (Test-Path (Join-Path $backendDir 'stale.txt')) { throw 'Stale backend directory survived' }
    $unwanted = if ($Expected -eq 'cpu') { 'cu126-only.txt' } else { 'cpu-only.txt' }
    if (Test-Path (Join-Path $backendDir $unwanted)) { throw "Mixed backends: $unwanted" }
    $nested = Join-Path $backendDir '_internal/native/library.txt'
    if ((Get-Content $nested -Raw).Trim() -ne "nested $Expected library") {
        throw 'Archive directory structure was not preserved'
    }
    $model = Join-Path $emotionDir 'model.onnx'
    if ($WithEmotion) {
        if (-not (Test-Path $model)) { throw 'Emotion model was requested but not installed' }
        if (Test-Path (Join-Path $emotionDir 'old.txt')) { throw 'Stale emotion model survived' }
    } elseif (Test-Path $emotionDir) {
        throw 'Emotion model was installed without being requested'
    }
}

function Assert-Rejected([string]$Option, [string]$Variant, [string]$Emotion, [string]$Message) {
    if ((Invoke-Setup $Option) -ne 2) { throw $Message }
    $stored = Get-ItemProperty -LiteralPath "HKCU:\$registryKey" -ErrorAction SilentlyContinue
    if ($stored.BackendVariant -ne $Variant -or $stored.EmotionModel -ne $Emotion) {
        throw "A failed install changed the remembered selection: $Message"
    }
}

$server = $null
try {
    $server = Start-Payload
    # Nothing exists yet: the installer has to create both directories.
    Invoke-Install '' 'cpu' $false

    # Leftovers from an interrupted install must be replaced, not merged.
    Set-Content (Join-Path $backendDir 'stale.txt') 'stale'
    New-Item -ItemType Directory -Force -Path $emotionDir | Out-Null
    Set-Content (Join-Path $emotionDir 'old.txt') 'old'
    Invoke-Install '/BACKEND=cu126 /EMOTION=yes' 'cu126' $true

    Invoke-Install '' 'cu126' $true
    Invoke-Install '/BACKEND=cpu /EMOTION=no' 'cpu' $false
    Assert-Rejected '/BACKEND=invalid' 'cpu' 'no' 'Invalid backend option was not rejected'
    Assert-Rejected '/EMOTION=maybe' 'cpu' 'no' 'Invalid emotion option was not rejected'

    # A damaged download must fail the install and not persist the selection.
    foreach ($case in @{ id = 'cu126'; option = '/BACKEND=cu126' },
        @{ id = 'emotion'; option = '/EMOTION=yes' }) {
        $served = Join-Path $payload $archiveOf[$case.id]
        $intact = "$served.intact"
        Move-Item -LiteralPath $served -Destination $intact
        Set-Content -LiteralPath $served -Value 'corrupt archive'
        Assert-Rejected $case.option 'cpu' 'no' "Corrupt $($case.id) download was not rejected"
        Remove-Item -LiteralPath $served
        Move-Item -LiteralPath $intact -Destination $served
    }

    # Archives beside the installer are used instead of downloading.
    Stop-Process -Id $server.Id -Force
    $server = $null
    Copy-Item -LiteralPath (Join-Path $payload $archiveOf['cpu']) -Destination $testRoot
    Copy-Item -LiteralPath (Join-Path $payload $archiveOf['emotion']) -Destination $testRoot
    Invoke-Install '/BACKEND=cpu /EMOTION=yes' 'cpu' $true
    # A local archive that does not match this build is ignored, not trusted.
    Set-Content -LiteralPath (Join-Path $testRoot $archiveOf['cpu']) -Value 'wrong archive'
    Assert-Rejected '/BACKEND=cpu' 'cpu' 'yes' 'A mismatched local archive was not rejected'

    Write-Output ('PASS: fresh install, GPU and emotion selection, nested files, remembered ' +
        'selection, switching both off, stale directory replacement, invalid options, corrupt ' +
        'backend and emotion downloads, offline install from local archives and mismatched ' +
        'local archive rejection')
    Write-Output "Interactive test installer: $setup"
} finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    if ($null -eq $originalNsisDir) {
        Remove-Item -LiteralPath 'Env:NSISDIR' -ErrorAction SilentlyContinue
    } else {
        $env:NSISDIR = $originalNsisDir
    }
    # Remove only the unique test registry value/key, never an app installation.
    Remove-Item -LiteralPath "HKCU:\$registryKey" -ErrorAction SilentlyContinue
}
