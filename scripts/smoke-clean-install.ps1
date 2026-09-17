param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [ValidateSet('cpu', 'cu126')][string]$Variant = 'cpu'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$evidence = Join-Path $projectRoot 'build/clean-install-evidence'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('vrc-clean-' + [guid]::NewGuid())
$installRoot = Join-Path $testRoot 'app'
$cacheRoot = Join-Path $testRoot 'cache'
$userData = Join-Path $testRoot 'user-data'
New-Item -ItemType Directory -Force -Path $testRoot, $cacheRoot, $userData | Out-Null
$originalPath = $env:PATH
$originalCache = $env:XDG_CACHE_HOME
$originalHfHome = $env:HF_HOME
$originalOffline = $env:HF_HUB_OFFLINE
$ruleName = 'vrc-clean-' + [guid]::NewGuid()
$appProcess = $null
$backendExe = $null
$appExe = $null

function Invoke-BackendCheck([string]$Label, [string]$Argument) {
    $stdout = Join-Path $evidence "$Label.stdout.json"
    $stderr = Join-Path $evidence "$Label.stderr.log"
    $process = Start-Process -FilePath $backendExe -ArgumentList $Argument `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    if (-not $process.WaitForExit(300000)) {
        Stop-Process -Id $process.Id -Force
        throw "$Label exceeded five minutes"
    }
    if ($process.ExitCode -ne 0) { throw "$Label failed: $($process.ExitCode)" }
    $result = Get-Content -LiteralPath $stdout -Raw | ConvertFrom-Json
    if (-not $result.ok -or $result.variant -ne $Variant -or $result.pytorch -ne $false) {
        throw "$Label did not report a successful $Variant runtime"
    }
}

try {
    Get-FileHash -LiteralPath $installerPath -Algorithm SHA256 |
        Format-List | Out-File (Join-Path $evidence 'installer-sha256.txt')
    # NSIS requires /D to be the final argument; do not quote the directory.
    # The web installer unpacks the app package and the selected backend, which
    # is several gigabytes for CUDA, so allow far more than a local copy needs.
    $installerProcess = Start-Process -FilePath $installerPath `
        -ArgumentList "/S /BACKEND=$Variant /EMOTION=yes /D=$installRoot" -WindowStyle Hidden -PassThru
    if (-not $installerProcess.WaitForExit(900000)) {
        Stop-Process -Id $installerProcess.Id -Force
        throw 'Installer timed out'
    }
    if ($installerProcess.ExitCode -ne 0) { throw 'Installer failed' }
    $backendExe = Join-Path $installRoot 'resources/backend/vrc-v2t-backend.exe'
    $appExe = Join-Path $installRoot 'VRC2T.exe'
    $emotionModel = Join-Path $installRoot 'resources/models/emotion/model.onnx'
    if (-not (Test-Path -LiteralPath $backendExe) -or -not (Test-Path -LiteralPath $appExe)) {
        throw 'Installed application or downloaded backend missing'
    }
    if (-not (Test-Path -LiteralPath $emotionModel)) {
        throw 'Optional emotion model was requested but not installed'
    }

    # Hosted runners have developer tools installed. Hide them for child processes;
    # never uninstall runner-wide software or use the build job's environment.
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:XDG_CACHE_HOME = $cacheRoot
    $env:HF_HOME = Join-Path $cacheRoot 'huggingface'
    if (Get-Command python, node, poetry -ErrorAction SilentlyContinue) {
        throw 'Development tools are still reachable on PATH'
    }
    Invoke-BackendCheck 'native-runtime' '--self-test'
    Invoke-BackendCheck 'shared-emotion-onnx' '--self-test-emotion'
    Invoke-BackendCheck 'first-download-inference' '--self-test-model'
    $modelFile = Get-ChildItem -LiteralPath $env:HF_HOME -Recurse -Filter model.bin -File | Select-Object -First 1 -ExpandProperty FullName
    if (-not $modelFile) { throw 'Fresh CTranslate2 model cache was not populated' }
    Get-FileHash -LiteralPath $modelFile -Algorithm SHA256 |
        Format-List | Out-File (Join-Path $evidence 'model-sha256.txt')

    # Enforce offline execution for the whole frozen executable.
    New-NetFirewallRule -DisplayName $ruleName -Direction Outbound -Action Block `
        -Program $backendExe -Profile Any | Out-Null
    $env:HF_HUB_OFFLINE = '1'
    Invoke-BackendCheck 'offline-inference' '--self-test-model'

    $settings = @{
        schemaVersion = 1
        audio = @{ deviceId = 'default'; speakerDeviceId = 'speaker:default'; autoStart = $false }
        speech = @{ model = 'tiny'; language = 'zh' }
        translation = @{
            enabled = $false; provider = 'onnx'; model = 'venddair/nllb-200-distilled-600M-onnx'
            sourceLanguage = 'zh'; targetLanguage = 'en'; endpoint = 'http://127.0.0.1:5000'
            apiKey = ''; deeplPlan = 'free'; deeplApiKey = ''
        }
        osc = @{ enabled = $false; host = '127.0.0.1'; port = 9000 }
        emotion = @{ enabled = $false; parameter = '/avatar/parameters/v2t_sync_emo' }
        privacy = @{ saveAudio = $false; saveTranscripts = $false }
    }
    $settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $userData 'settings.json') -Encoding utf8
    $appProcess = Start-Process -FilePath $appExe -ArgumentList "--user-data-dir=`"$userData`"" `
        -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        Start-Sleep -Milliseconds 250
        $appProcess.Refresh()
        if ($appProcess.HasExited) { throw 'Installed Electron exited before showing its main window' }
    } until ($appProcess.MainWindowTitle -eq 'VRC Voice to Text' -or [DateTime]::UtcNow -gt $deadline)
    if ($appProcess.MainWindowTitle -ne 'VRC Voice to Text') { throw 'Main window did not become available' }
    if (-not $appProcess.CloseMainWindow()) { throw 'Could not request normal application close' }
    if (-not $appProcess.WaitForExit(15000)) { throw 'Electron did not close within 15 seconds' }
    Start-Sleep -Seconds 1
    $remaining = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $backendExe }
    if ($remaining) { throw 'Bundled backend remained after closing Electron' }
    "PASS: silent web install, isolated PATH, $Variant runtime, fresh tiny download, offline CPU inference, Electron window and exit" |
        Set-Content (Join-Path $evidence 'result.txt')
} catch {
    $_ | Out-String | Set-Content (Join-Path $evidence 'failure.txt')
    throw
} finally {
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if ($appProcess -and -not $appProcess.HasExited) { Stop-Process -Id $appProcess.Id -Force }
    if ($backendExe) {
        Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $backendExe } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    $env:PATH = $originalPath
    $env:XDG_CACHE_HOME = $originalCache
    $env:HF_HOME = $originalHfHome
    $env:HF_HUB_OFFLINE = $originalOffline
    # Leave the isolated installation in TEMP for inspection; the hosted runner
    # is discarded after the job. Never delete a user installation or cache.
}
