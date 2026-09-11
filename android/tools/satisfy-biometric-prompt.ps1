<#
.SYNOPSIS
    Answer the system credential prompt that the red-tier UI test triggers.

.DESCRIPTION
    `BiometricPrompt` renders a *system* window, so the instrumented test that
    clicks "用生物识别签名并执行" cannot answer it from inside its own process.
    This script is the other half: it waits for a credential prompt to take over
    the foreground, then types the device PIN.

    An emulator has no enrolled fingerprint, so the app's gate accepts the device
    credential instead (see BiometricGate).  The PIN is set beforehand with:

        adb shell locksettings set-pin 1234

    Usage (start it just before the test run; it exits after answering once):

        pwsh -File android\tools\satisfy-biometric-prompt.ps1 -Pin 1234 -TimeoutSeconds 180

.PARAMETER Adb
    Path to adb.exe.  Defaults to the toolchain installed by
    install-android-toolchain.ps1.

.PARAMETER Pin
    The device credential to type.

.PARAMETER TimeoutSeconds
    How long to wait for the prompt before giving up.
#>
[CmdletBinding()]
param(
    [string]$Adb = 'D:\AndroidDev\sdk\platform-tools\adb.exe',
    [string]$Pin = '1234',
    [int]$TimeoutSeconds = 180,
    [string]$Package = 'com.aibanking.agent'
)

$ErrorActionPreference = 'Stop'

# The credential prompt can be either a system activity (then the resumed
# activity stops being ours) or a dialog owned by our own activity (then the
# resumed activity does not change at all).  Looking at the *focused window*
# covers both: it becomes a different window in either case.
function Get-FocusedWindow {
    $dump = & $Adb shell dumpsys window 2>&1
    $line = $dump | Select-String 'mCurrentFocus' | Select-Object -First 1
    if (-not $line) { return '' }
    return $line.Line.Trim()
}

Write-Host "waiting up to ${TimeoutSeconds}s for a credential prompt..." -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)

# Phase 1: wait for the app itself to hold focus.  The instrumented run installs
# and launches it, and during that window the launcher is on top - treating that
# as "the app lost focus" made an earlier version of this script type the PIN
# into the launcher and exit immediately.
$appForeground = $false
while ((Get-Date) -lt $deadline) {
    $focus = Get-FocusedWindow
    if ($focus -match [regex]::Escape($Package)) {
        $appForeground = $true
        Write-Host "  app is in the foreground" -ForegroundColor DarkGray
        break
    }
    Start-Sleep -Milliseconds 500
}
if (-not $appForeground) {
    Write-Host 'the app never reached the foreground' -ForegroundColor Red
    exit 1
}

# Phase 2: wait for the prompt to take focus away from the app.  The focus line
# must name a real window: an earlier version treated "mCurrentFocus=null" (the
# transient gap while windows swap) as the prompt, typed the PIN into nothing and
# exited, leaving the actual prompt unanswered.
$seen = $false
while ((Get-Date) -lt $deadline) {
    $focus = Get-FocusedWindow
    $realWindow = $focus -and $focus -match 'Window\{' -and $focus -notmatch 'mCurrentFocus=null'
    if ($realWindow -and $focus -notmatch [regex]::Escape($Package) -and $focus -notmatch 'nexuslauncher|Launcher') {
        $seen = $true
        Write-Host "  prompt detected: $focus" -ForegroundColor Yellow
        break
    }
    Start-Sleep -Milliseconds 700
}

if (-not $seen) {
    Write-Host 'no credential prompt appeared; nothing to answer' -ForegroundColor Red
    exit 1
}

# Give the keypad a moment to accept input before typing into it.
Start-Sleep -Seconds 2
& $Adb shell input text $Pin
Start-Sleep -Milliseconds 800
& $Adb shell input keyevent 66   # ENTER confirms the credential
Start-Sleep -Milliseconds 800
Write-Host 'PIN submitted' -ForegroundColor Green
exit 0
