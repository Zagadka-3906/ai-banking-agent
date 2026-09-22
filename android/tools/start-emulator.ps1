<#
.SYNOPSIS
    Build this checkout, update the emulator app, then launch it.
.EXAMPLE
    .\start-emulator.bat
.EXAMPLE
    .\start-emulator.bat -Offline
    Use cached Gradle dependencies; the app is still rebuilt from current sources.
.EXAMPLE
    .\start-emulator.bat -NetworkMode
    Build the backend-connected variant and start Core/Gateway for integration testing.
#>
[CmdletBinding()]
param(
    [string]$AvdName = 'bankagent',
    [ValidateRange(30, 900)][int]$BootTimeoutSeconds = 180,
    [switch]$Offline,
    [switch]$NetworkMode
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$androidRoot = Join-Path $repoRoot 'android'
$apk = Join-Path $androidRoot 'app\build\outputs\apk\debug\app-debug.apk'
$package = 'com.aibanking.agent'
$logDir = Join-Path $repoRoot 'build\quick-start'
$runId = Get-Date -Format 'yyyyMMdd-HHmmss'

function Get-BackendHealth([int]$Port) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2 -UseBasicParsing
    } catch { return $null }
}

function Start-Backend([string]$Module, [int]$Port, [string]$Service) {
    $health = Get-BackendHealth $Port
    if ($health -and $health.status -eq 'ok' -and $health.service -eq $Service) {
        Write-Host "  Reusing $Service on port $Port."
        return
    }
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is occupied but $Service is not healthy. Check the existing service first."
    }
    $python = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Missing .venv\Scripts\python.exe. Set up the backend environment as described in README.md.'
    }
    $errorLog = Join-Path $logDir "$Module-$runId.err.log"
    Start-Process -FilePath $python -WorkingDirectory $repoRoot -WindowStyle Hidden `
        -ArgumentList '-m', 'uvicorn', "${Module}:app", '--host', '0.0.0.0', '--port', "$Port" `
        -RedirectStandardOutput (Join-Path $logDir "$Module-$runId.log") `
        -RedirectStandardError $errorLog | Out-Null
    $deadline = (Get-Date).AddSeconds(45)
    do {
        $health = Get-BackendHealth $Port
        if ($health -and $health.status -eq 'ok' -and $health.service -eq $Service) {
            Write-Host "  $Service is ready on port $Port."
            return
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    throw "The $Service startup timed out. See $errorLog"
}

function Find-Emulator {
    # Ignore transient offline transports while the emulator is starting.
    $ErrorActionPreference = 'Continue'
    $devices = & $adb devices 2>$null
    foreach ($line in $devices) {
        if ($line -match '^(emulator-\d+)\s+device\s*$') {
            $candidate = $Matches[1]
            $name = & $adb -s $candidate emu avd name 2>$null
            if ($LASTEXITCODE -eq 0 -and $name -contains $AvdName) { return $candidate }
        }
    }
    return $null
}

function Invoke-Adb([string[]]$Command) {
    $ErrorActionPreference = 'Continue'
    $output = & $adb -s $serial @Command 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "adb $($Command -join ' ') failed:`n$($output -join "`n")"
    }
    return ($output -join "`n").Trim()
}

function Test-EmulatorUnlocked {
    if ((Invoke-Adb @('shell', 'am', 'get-started-user-state', $userId)) -ne 'RUNNING_UNLOCKED') {
        return $false
    }
    # Credential storage stays unlocked after the screen locks again. Also wait
    # for the actual keyguard to disappear before claiming the app is open.
    $policy = Invoke-Adb @('shell', 'dumpsys', 'window', 'policy')
    return $policy -match '(?m)^\s*showing=false\s*$'
}

try {
    Write-Host '[1/5] Resolving Android SDK and JDK...'
    # Prefer the same SDK as Gradle. Fall back to the installed user toolchain.
    $localSdk = $null
    $localProperties = Join-Path $androidRoot 'local.properties'
    if (Test-Path -LiteralPath $localProperties) {
        $sdkLine = Get-Content -LiteralPath $localProperties | Select-String '^sdk\.dir\s*=' | Select-Object -First 1
        if ($sdkLine) { $localSdk = ($sdkLine.Line -replace '^sdk\.dir\s*=\s*', '') -replace '\\:', ':' -replace '\\\\', '\' }
    }
    $sdk = @($localSdk, $env:ANDROID_HOME, $env:ANDROID_SDK_ROOT,
        [Environment]::GetEnvironmentVariable('ANDROID_HOME', 'User'), 'D:\AndroidDev\sdk') |
        Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ 'platform-tools\adb.exe')) -and
            (Test-Path -LiteralPath (Join-Path $_ 'emulator\emulator.exe')) } | Select-Object -First 1
    if (-not $sdk) { throw 'Android SDK not found. Run android\tools\install-android-toolchain.ps1 first.' }
    $jdk = @($env:JAVA_HOME, [Environment]::GetEnvironmentVariable('JAVA_HOME', 'User'), 'D:\AndroidDev\jdk-17') |
        Where-Object {
            $_ -and (Test-Path -LiteralPath (Join-Path $_ 'bin\javac.exe')) -and
            (Test-Path -LiteralPath (Join-Path $_ 'release')) -and
            ((Get-Content -LiteralPath (Join-Path $_ 'release')) -match '^JAVA_VERSION="(17|21)(\.|"|-)')
        } | Select-Object -First 1
    if (-not $jdk) { throw 'JDK 17 or 21 not found. Set JAVA_HOME or run android\tools\install-android-toolchain.ps1.' }
    $env:JAVA_HOME = $jdk
    $env:ANDROID_HOME = $sdk
    $env:ANDROID_SDK_ROOT = $sdk
    $env:PATH = "$jdk\bin;$env:PATH"
    $adb = Join-Path $sdk 'platform-tools\adb.exe'
    $emulator = Join-Path $sdk 'emulator\emulator.exe'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null

    Write-Host '[2/5] Building the latest app from current sources...'
    Push-Location $androidRoot
    try {
        $standalone = if ($NetworkMode) { 'false' } else { 'true' }
        $gradleArgs = @(':app:assembleDebug', '--console=plain', "-PbankStandalone=$standalone",
            '-PbankCoreUrl=http://10.0.2.2:8000/', '-PbankGatewayUrl=http://10.0.2.2:8100/')
        if ($Offline) { $gradleArgs += '--offline' }
        & '.\gradlew.bat' @gradleArgs
        if ($LASTEXITCODE -ne 0) { throw 'Gradle build failed. No APK was installed or launched.' }
    } finally { Pop-Location }
    if (-not (Test-Path -LiteralPath $apk)) { throw "Build output missing: $apk" }

    if ($NetworkMode) {
        Write-Host '[3/5] Checking backend services for network mode...'
        Start-Backend 'main' 8000 'mock-banking-core'
        Start-Backend 'agent_api' 8100 'banking-agent-gateway'
    } else {
        Write-Host '[3/5] Standalone mode: no computer backend is needed.'
    }

    Write-Host "[4/5] Starting or reusing emulator $AvdName..."
    & $adb start-server
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the ADB server.' }
    $serial = Find-Emulator
    if (-not $serial) {
        $availableAvds = & $emulator -list-avds
        if ($LASTEXITCODE -ne 0 -or $availableAvds -notcontains $AvdName) {
            throw "AVD '$AvdName' was not found. Available AVDs: $($availableAvds -join ', ')"
        }
        # This window is intentionally visible for interactive app debugging.
        Start-Process -FilePath $emulator -ArgumentList '-avd', "`"$AvdName`"" `
            -RedirectStandardOutput (Join-Path $logDir "emulator-$runId.log") `
            -RedirectStandardError (Join-Path $logDir "emulator-$runId.err.log") | Out-Null
    }
    $deadline = (Get-Date).AddSeconds($BootTimeoutSeconds)
    $booted = $false
    do {
        $serial = Find-Emulator
        if ($serial) {
            try {
                $booted = (Invoke-Adb @('shell', 'getprop', 'sys.boot_completed')) -eq '1' -and
                    (Invoke-Adb @('shell', 'pm', 'path', 'android')) -match 'package:'
            } catch { $booted = $false }
        }
        if ($booted) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    if (-not $booted) { throw "Emulator startup timed out. Check the emulator window and logs in $logDir" }
    Write-Host "  Target device: $serial ($AvdName)"
    Invoke-Adb @('shell', 'input', 'keyevent', 'KEYCODE_WAKEUP') | Out-Null
    try { Invoke-Adb @('shell', 'wm', 'dismiss-keyguard') | Out-Null } catch {
        Write-Host '  The screen is locked. Please unlock it in the emulator window.' -ForegroundColor Yellow
    }
    $userId = Invoke-Adb @('shell', 'am', 'get-current-user')
    $deadline = (Get-Date).AddSeconds($BootTimeoutSeconds)
    $unlockHintShown = $false
    while (-not (Test-EmulatorUnlocked)) {
        if (-not $unlockHintShown) {
            Write-Host '  Please unlock the emulator screen to continue installing and opening the app.' -ForegroundColor Yellow
            $unlockHintShown = $true
        }
        if ((Get-Date) -ge $deadline) { throw 'Unlock timed out. Unlock the emulator, then run this script again.' }
        Start-Sleep -Seconds 2
    }

    Write-Host '[5/5] Installing and opening the freshly built app...'
    $install = Invoke-Adb @('install', '-r', '-t', $apk)
    Write-Host $install
    if ($install -notmatch '(?m)^Success\s*$') { throw 'APK installation did not report success. App launch cancelled.' }
    $launch = Invoke-Adb @('shell', 'am', 'start', '-S', '-W', '--user', $userId, '-n', "$package/.MainActivity")
    Write-Host $launch
    if ($launch -match '(?im)^Error' -or $launch -notmatch '(?m)^Status: ok\s*$') {
        throw 'The updated app could not be opened. Check the emulator screen.'
    }
    $mode = if ($NetworkMode) { 'network integration' } else { 'standalone' }
    Write-Host "`nLatest $mode app built, installed and launched successfully." -ForegroundColor Green
    Write-Host "APK: $apk"
    Write-Host "Logs: $logDir"
    exit 0
} catch {
    Write-Host "`nQuick start failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
