param([string]$Makensis)

$ErrorActionPreference = 'Stop'
if (-not $Makensis) {
    $Makensis = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'electron-builder/Cache/nsis') `
        -Filter makensis.exe -Recurse | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Makensis) { throw 'Build the installer first to download the NSIS compiler' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$testRoot = Join-Path $projectRoot ('build/installer-test-' + [guid]::NewGuid())
$gpuRoot = Join-Path $testRoot 'build/python-cu126/vrc-v2t-backend'
New-Item -ItemType Directory -Force -Path $gpuRoot | Out-Null
Set-Content (Join-Path $gpuRoot 'gpu-only.txt') 'cu126'
Set-Content (Join-Path $gpuRoot 'runtime.txt') 'cu126'
$registryKey = 'Software\VRC2T-InstallerTest-' + [guid]::NewGuid()
$include = Join-Path $PSScriptRoot 'installer.nsh'
$harness = @'
Unicode true
RequestExecutionLevel user
!include "LogicLib.nsh"
!include "FileFunc.nsh"
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
  ; Simulate electron-builder removing the previous app and extracting CPU.
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\resources\backend"
  CreateDirectory "$INSTDIR\resources\backend"
  FileOpen $0 "$INSTDIR\resources\backend\cpu-only.txt" w
  FileWrite $0 "cpu"
  FileClose $0
  FileOpen $0 "$INSTDIR\resources\backend\runtime.txt" w
  FileWrite $0 "cpu"
  FileClose $0
  !insertmacro customInstall
SectionEnd
'@
$harness = $harness.Replace('@ROOT@', $testRoot).Replace('@KEY@', $registryKey).Replace('@INCLUDE@', $include)
$harnessFile = Join-Path $testRoot 'harness.nsi'
Set-Content -LiteralPath $harnessFile -Value $harness -Encoding utf8
& $Makensis /WX /V2 $harnessFile
if ($LASTEXITCODE -ne 0) { throw 'Installer test harness did not compile' }
$setup = Join-Path $testRoot 'setup-test.exe'
$appRoot = Join-Path $testRoot 'app'

function Invoke-Install([string]$Option, [string]$Expected) {
    $child = Start-Process -FilePath $setup -ArgumentList "/S $Option /D=$appRoot" -WindowStyle Hidden -PassThru
    if (-not $child.WaitForExit(30000)) {
        Stop-Process -Id $child.Id -Force
        throw 'Installer test timed out'
    }
    if ($child.ExitCode -ne 0) { throw "Installer failed: $($child.ExitCode)" }
    $backend = Join-Path $appRoot 'resources/backend'
    if ((Get-Content (Join-Path $backend 'runtime.txt') -Raw).Trim() -ne $Expected) {
        throw "Expected $Expected backend"
    }
    $unwanted = if ($Expected -eq 'cpu') { 'gpu-only.txt' } else { 'cpu-only.txt' }
    if (Test-Path (Join-Path $backend $unwanted)) { throw "Mixed backends: $unwanted" }
}

try {
    Invoke-Install '' 'cpu'
    Invoke-Install '/BACKEND=cu126' 'cu126'
    Invoke-Install '' 'cu126'
    Invoke-Install '/BACKEND=cpu' 'cpu'
    $child = Start-Process -FilePath $setup -ArgumentList "/S /BACKEND=invalid /D=$appRoot" -WindowStyle Hidden -PassThru
    if (-not $child.WaitForExit(30000)) {
        Stop-Process -Id $child.Id -Force
        throw 'Invalid option test timed out'
    }
    if ($child.ExitCode -ne 2) { throw 'Invalid backend option was not rejected' }
    Write-Output 'PASS: default CPU, GPU selection, remembered selection, GPU-to-CPU switch, invalid option rejection'
    Write-Output "Interactive test installer: $setup"
} finally {
    # Remove only the unique test registry value/key, never an app installation.
    Remove-Item -LiteralPath "HKCU:\$registryKey" -ErrorAction SilentlyContinue
}
