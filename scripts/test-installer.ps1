param([string]$Makensis)

$ErrorActionPreference = 'Stop'
if (-not $Makensis) {
    $Makensis = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'electron-builder/Cache/nsis') `
        -Filter makensis.exe -Recurse | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Makensis) { throw 'Build the installer first to download the NSIS compiler' }
# StdUtils and INetC ship with electron-builder's NSIS resources, not with NSIS.
$pluginDir = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'electron-builder/Cache/nsis') `
    -Filter x86-unicode -Recurse -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName 'INetC.dll') } |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $pluginDir) { throw 'Build the installer first to download the NSIS plugins' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$templateInclude = & node -e @'
const { createRequire } = require('node:module')
const path = require('node:path')
const builderRequire = createRequire(require.resolve('electron-builder'))
const root = path.dirname(builderRequire.resolve('app-builder-lib/package.json'))
console.log(path.join(root, 'templates', 'nsis', 'include'))
'@
if ($LASTEXITCODE -ne 0) { throw 'Could not locate the electron-builder NSIS templates' }

$testRoot = Join-Path $projectRoot ('build/installer-test-' + [guid]::NewGuid())
foreach ($variant in 'cpu', 'cu126') {
    $root = Join-Path $testRoot "build/python-$variant/vrc-v2t-backend"
    New-Item -ItemType Directory -Force -Path (Join-Path $root '_internal/native') | Out-Null
    Set-Content (Join-Path $root "$variant-only.txt") $variant
    Set-Content (Join-Path $root 'runtime.txt') $variant
    Set-Content (Join-Path $root '_internal/native/library.txt') "nested $variant library"
}

# Reserve a loopback port, then let the compiled installer download from it.
$reservation = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$reservation.Start()
$port = $reservation.LocalEndpoint.Port
$reservation.Stop()
$payload = Join-Path $testRoot 'build/installer-payload'
$env:VRC_BACKEND_BASE_URL = "http://127.0.0.1:$port"
# The fixture archives have no vrc-v2t-backend.exe to check for.
$env:VRC_BACKEND_ENTRY = 'runtime.txt'
try {
    node (Join-Path $PSScriptRoot 'prepare-backends.cjs') $testRoot
    if ($LASTEXITCODE -ne 0) { throw 'Installer fixture archives failed' }
} finally {
    Remove-Item Env:VRC_BACKEND_BASE_URL, Env:VRC_BACKEND_ENTRY
}
$assets = Get-Content (Join-Path $payload 'backend-assets.json') -Raw | ConvertFrom-Json
$archiveOf = @{}
foreach ($asset in $assets.assets) { $archiveOf[$asset.variant] = $asset.name }

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
  ; The web installer extracts an app package that carries no backend, so only
  ; a stale directory from an interrupted install can be present here.
  SetOutPath "$INSTDIR"
  CreateDirectory "$INSTDIR\resources\backend"
  FileOpen $0 "$INSTDIR\resources\backend\stale.txt" w
  FileWrite $0 "stale"
  FileClose $0
  !insertmacro customInstall
SectionEnd
'@
$harness = $harness.Replace('@ROOT@', $testRoot).Replace('@KEY@', $registryKey).
    Replace('@INCLUDE@', $include).Replace('@TEMPLATES@', $templateInclude).
    Replace('@PLUGINS@', $pluginDir)
$harnessFile = Join-Path $testRoot 'harness.nsi'
Set-Content -LiteralPath $harnessFile -Value $harness -Encoding utf8
# Match electron-builder's compressor. The backends are already 7-Zip archives.
$compilerOutput = & $Makensis /WX /V3 '/XSetCompressor zlib' $harnessFile 2>&1
$compilerExitCode = $LASTEXITCODE
$compilerOutput | Write-Output
if ($compilerExitCode -ne 0) { throw 'Installer test harness did not compile' }
$setup = Join-Path $testRoot 'setup-test.exe'
$appRoot = Join-Path $testRoot 'app'

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

function Invoke-Install([string]$Option, [string]$Expected) {
    $code = Invoke-Setup $Option
    if ($code -ne 0) { throw "Installer failed: $code" }
    $backend = Join-Path $appRoot 'resources/backend'
    if ((Get-Content (Join-Path $backend 'runtime.txt') -Raw).Trim() -ne $Expected) {
        throw "Expected $Expected backend"
    }
    if (Test-Path (Join-Path $backend 'stale.txt')) { throw 'Stale backend directory survived' }
    $unwanted = if ($Expected -eq 'cpu') { 'cu126-only.txt' } else { 'cpu-only.txt' }
    if (Test-Path (Join-Path $backend $unwanted)) { throw "Mixed backends: $unwanted" }
    $nested = Join-Path $backend '_internal/native/library.txt'
    if ((Get-Content $nested -Raw).Trim() -ne "nested $Expected library") {
        throw 'Archive directory structure was not preserved'
    }
}

function Assert-Rejected([string]$Option, [string]$Message) {
    if ((Invoke-Setup $Option) -ne 2) { throw $Message }
    if ((Get-ItemProperty -LiteralPath "HKCU:\$registryKey" -ErrorAction SilentlyContinue).BackendVariant -eq 'cu126') {
        throw 'A failed install persisted the GPU selection'
    }
}

$server = $null
try {
    $server = Start-Payload
    Invoke-Install '' 'cpu'
    Invoke-Install '/BACKEND=cu126' 'cu126'
    Invoke-Install '' 'cu126'
    Invoke-Install '/BACKEND=cpu' 'cpu'
    Assert-Rejected '/BACKEND=invalid' 'Invalid backend option was not rejected'

    # A damaged download must fail the install and not persist the GPU choice.
    $served = Join-Path $payload $archiveOf['cu126']
    $intact = "$served.intact"
    Move-Item -LiteralPath $served -Destination $intact
    Set-Content -LiteralPath $served -Value 'corrupt archive'
    Assert-Rejected '/BACKEND=cu126' 'Corrupt download was not rejected'
    Remove-Item -LiteralPath $served
    Move-Item -LiteralPath $intact -Destination $served

    # An archive beside the installer is used instead of downloading.
    Stop-Process -Id $server.Id -Force
    $server = $null
    Copy-Item -LiteralPath (Join-Path $payload $archiveOf['cpu']) -Destination $testRoot
    Invoke-Install '/BACKEND=cpu' 'cpu'
    # A local archive that does not match this build is ignored, not trusted.
    Set-Content -LiteralPath (Join-Path $testRoot $archiveOf['cpu']) -Value 'wrong archive'
    Assert-Rejected '/BACKEND=cpu' 'A mismatched local archive was not rejected'

    Write-Output ('PASS: CPU default, GPU selection, nested files, remembered selection, ' +
        'GPU-to-CPU switch, stale directory replacement, invalid option, corrupt download, ' +
        'offline install from a local archive and mismatched local archive rejection')
    Write-Output "Interactive test installer: $setup"
} finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    # Remove only the unique test registry value/key, never an app installation.
    Remove-Item -LiteralPath "HKCU:\$registryKey" -ErrorAction SilentlyContinue
}
