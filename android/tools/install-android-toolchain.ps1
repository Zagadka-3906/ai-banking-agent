<#
.SYNOPSIS
    Provision a portable (no-admin) Android build toolchain on Windows.

.DESCRIPTION
    Installs everything needed to build the Android client of the AI Banking
    Agent without requiring UAC elevation:

      * Temurin JDK 17          -> <Base>\jdk-17
      * Android cmdline-tools   -> <Sdk>\cmdline-tools\latest
      * Android platform-tools  -> <Sdk>\platform-tools   (adb)
      * Android platform/build-tools via sdkmanager
      * Gradle distribution     -> <Base>\gradle-<ver>    (used only to
                                   bootstrap the project's Gradle wrapper)

    Every step is idempotent: an already-present artefact is skipped, so the
    script can be re-run to repair a partial download.  User-level environment
    variables (JAVA_HOME, ANDROID_HOME, ANDROID_SDK_ROOT, PATH) are written at
    the end so Android Studio and future shells pick the same SDK up.

.PARAMETER Base
    Root directory for the portable toolchain.  Defaults to D:\AndroidDev.

.PARAMETER ApiLevel
    Android platform API level to install.  Defaults to 35.

.EXAMPLE
    pwsh -File android\tools\install-android-toolchain.ps1

.NOTES
    Pin the versions below and the Android Gradle Plugin / Gradle / Kotlin
    versions in android/gradle/libs.versions.toml together: AGP 8.7.x requires
    Gradle 8.9+ and JDK 17.
#>
[CmdletBinding()]
param(
    [string]$Base = 'D:\AndroidDev',
    [string]$ApiLevel = '35',
    [string]$BuildTools = '35.0.0',
    [string]$GradleVersion = '8.9',
    [string]$CmdlineToolsBuild = '13114758',
    # Optional "host:port" of a local HTTP proxy to route Gradle's dependency
    # downloads through (see step 7).  Empty keeps Gradle's own defaults.
    [string]$GradleProxy = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$JdkDir   = Join-Path $Base 'jdk-17'
$SdkDir   = Join-Path $Base 'sdk'
$GradleDir = Join-Path $Base "gradle-$GradleVersion"
$DownloadDir = Join-Path $Base '_downloads'

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "    [ok] $Message" -ForegroundColor Green
}

function Get-RemoteFile {
    param([string]$Uri, [string]$OutFile, [string]$Label)

    if ((Test-Path $OutFile) -and ((Get-Item $OutFile).Length -gt 0)) {
        Write-Ok "$Label already downloaded ($([math]::Round((Get-Item $OutFile).Length / 1MB, 1)) MB)"
        return
    }

    Write-Host "    [get] $Label" -ForegroundColor Yellow
    Write-Host "          $Uri"
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromMinutes(45)
    try {
        # ResponseHeadersRead streams the body instead of buffering it in
        # memory, which matters for the ~200 MB archives below.
        $response = $client.GetAsync($Uri, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $response.EnsureSuccessStatusCode() | Out-Null
        $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $file = [System.IO.File]::Create($OutFile)
        try { $stream.CopyTo($file) } finally { $file.Dispose(); $stream.Dispose() }
    }
    finally {
        $client.Dispose()
    }
    Write-Ok ("{0} -> {1} MB in {2:N0}s" -f $Label, [math]::Round((Get-Item $OutFile).Length / 1MB, 1), $watch.Elapsed.TotalSeconds)
}

function Expand-Zip {
    param([string]$ZipPath, [string]$Destination, [string]$Label)

    if (Test-Path $Destination) {
        Write-Ok "$Label already extracted"
        return
    }
    Write-Host "    [zip] $Label" -ForegroundColor Yellow
    [System.IO.Compression.ZipFile]::ExtractToDirectory($ZipPath, $Destination)
    Write-Ok "$Label extracted to $Destination"
}

function Invoke-Native {
    param([string]$FilePath, [string[]]$Arguments)

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "'$FilePath $($Arguments -join ' ')' exited with code $LASTEXITCODE"
    }
}

foreach ($dir in @($Base, $DownloadDir, $SdkDir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

# ---------------------------------------------------------------------------
# 1) JDK 17 (Temurin).  AGP 8.7 requires JDK 17; the machine only has a JRE 8.
# ---------------------------------------------------------------------------
Write-Step 'JDK 17 (Eclipse Temurin)'
if (-not (Test-Path $JdkDir)) {
    $jdkZip = Join-Path $DownloadDir 'temurin17-jdk.zip'
    Get-RemoteFile -Uri 'https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse' -OutFile $jdkZip -Label 'Temurin 17 JDK'
    $staging = Join-Path $Base '_jdk-staging'
    if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
    Expand-Zip -ZipPath $jdkZip -Destination $staging -Label 'Temurin 17 JDK'
    # The archive contains a versioned folder (jdk-17.0.x+y).  Rename it to a
    # stable path so build scripts and CI do not track patch releases.
    $inner = Get-ChildItem $staging -Directory | Select-Object -First 1
    Move-Item $inner.FullName $JdkDir
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Ok "JAVA_HOME candidate: $JdkDir"

# ---------------------------------------------------------------------------
# 2) Android cmdline-tools.  sdkmanager/adb are the only SDK entry points we
#    need; Android Studio can adopt this SDK directory later.
# ---------------------------------------------------------------------------
Write-Step 'Android cmdline-tools'
$CmdlineToolsZip = Join-Path $DownloadDir 'commandlinetools-win.zip'
Get-RemoteFile -Uri "https://dl.google.com/android/repository/commandlinetools-win-${CmdlineToolsBuild}_latest.zip" -OutFile $CmdlineToolsZip -Label 'Android cmdline-tools'
$CmdlineToolsLatest = Join-Path $SdkDir 'cmdline-tools\latest'
if (-not (Test-Path $CmdlineToolsLatest)) {
    $staging = Join-Path $SdkDir '_cmdline-staging'
    if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
    Expand-Zip -ZipPath $CmdlineToolsZip -Destination $staging -Label 'Android cmdline-tools'
    New-Item -ItemType Directory -Force -Path (Split-Path $CmdlineToolsLatest) | Out-Null
    # The archive nests everything under "cmdline-tools"; sdkmanager requires
    # the layout <sdk>/cmdline-tools/latest/bin/...
    Move-Item (Join-Path $staging 'cmdline-tools') $CmdlineToolsLatest
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
}

$SdkManager = Join-Path $CmdlineToolsLatest 'bin\sdkmanager.bat'
if (-not (Test-Path $SdkManager)) { throw "sdkmanager not found at $SdkManager" }

# sdkmanager is a Java program: it needs the JDK we just installed, not the
# JRE 8 that happens to be on PATH.
$env:JAVA_HOME = $JdkDir
$env:ANDROID_HOME = $SdkDir
$env:ANDROID_SDK_ROOT = $SdkDir
$env:PATH = "$JdkDir\bin;$SdkDir\platform-tools;$env:PATH"

# ---------------------------------------------------------------------------
# 3) SDK licences + platform packages.
# ---------------------------------------------------------------------------
Write-Step 'Android SDK licences'
$yes = (1..200 | ForEach-Object { 'y' }) -join "`n"
$yes | & $SdkManager --sdk_root="$SdkDir" --licenses | Out-Null
Write-Ok 'licence prompts answered'

Write-Step "Android SDK packages (platform-tools, android-$ApiLevel, build-tools;$BuildTools)"
Invoke-Native -FilePath $SdkManager -Arguments @(
    "--sdk_root=$SdkDir",
    'platform-tools',
    "platforms;android-$ApiLevel",
    "build-tools;$BuildTools"
)
Write-Ok 'SDK packages installed'

# ---------------------------------------------------------------------------
# 4) Gradle distribution.  Only used to generate the project wrapper; all
#    later builds go through ./gradlew so the version is pinned in-repo.
# ---------------------------------------------------------------------------
Write-Step "Gradle $GradleVersion"
# Check for the launcher, not the directory: a previous interrupted run can
# leave an empty gradle-<ver> folder behind, and "the directory exists" would
# then silently skip the extraction.
if (-not (Test-Path (Join-Path $GradleDir 'bin\gradle.bat'))) {
    $gradleZip = Join-Path $DownloadDir "gradle-$GradleVersion-bin.zip"
    Get-RemoteFile -Uri "https://services.gradle.org/distributions/gradle-$GradleVersion-bin.zip" -OutFile $gradleZip -Label "Gradle $GradleVersion"
    if (Test-Path $GradleDir) { Remove-Item $GradleDir -Recurse -Force }
    [System.IO.Compression.ZipFile]::ExtractToDirectory($gradleZip, $Base)
}
$GradleBin = Join-Path $GradleDir 'bin\gradle.bat'
Write-Ok "gradle: $GradleBin"

# ---------------------------------------------------------------------------
# 5) Persist the environment for Android Studio and future shells.
# ---------------------------------------------------------------------------
Write-Step 'Persisting user-level environment variables'
[Environment]::SetEnvironmentVariable('JAVA_HOME', $JdkDir, 'User')
[Environment]::SetEnvironmentVariable('ANDROID_HOME', $SdkDir, 'User')
[Environment]::SetEnvironmentVariable('ANDROID_SDK_ROOT', $SdkDir, 'User')

$userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
foreach ($entry in @("$JdkDir\bin", "$SdkDir\platform-tools", "$SdkDir\cmdline-tools\latest\bin", "$GradleDir\bin")) {
    if ($userPath -notlike "*$entry*") {
        $userPath = if ([string]::IsNullOrWhiteSpace($userPath)) { $entry } else { "$userPath;$entry" }
    }
}
[Environment]::SetEnvironmentVariable('PATH', $userPath, 'User')
Write-Ok 'JAVA_HOME / ANDROID_HOME / ANDROID_SDK_ROOT / PATH updated for the current user'

# ---------------------------------------------------------------------------
# 7) Optional: route Gradle's dependency downloads through a local proxy.
#
#    On some networks ``dl.google.com/dl/android/maven2`` terminates the TLS
#    handshake - for Java exactly as for curl - while Maven Central stays
#    reachable.  The build then fails with "Remote host terminated the
#    handshake" even though the SDK itself installed fine.  These settings go
#    into the *user-level* Gradle configuration on purpose: nothing
#    machine-specific may be committed, and a CI runner must never inherit a
#    developer's proxy.
# ---------------------------------------------------------------------------
if ($GradleProxy) {
    Write-Step "Gradle dependency proxy ($GradleProxy)"
    $userGradleDir = Join-Path $env:USERPROFILE '.gradle'
    New-Item -ItemType Directory -Force -Path $userGradleDir | Out-Null
    $userGradleFile = Join-Path $userGradleDir 'gradle.properties'
    $existingGradleConfig = if (Test-Path $userGradleFile) { Get-Content $userGradleFile -Raw } else { '' }
    if ($existingGradleConfig -notmatch 'systemProp\.https\.proxyHost') {
        $proxyHost, $proxyPort = $GradleProxy.Split(':')
        Add-Content -Path $userGradleFile -Encoding UTF8 -Value @"

# Added by install-android-toolchain.ps1.  Google Maven is unreachable directly
# from this network, so Gradle resolves dependencies through a local proxy.
# localhost is excluded so a local repository is never tunnelled.
systemProp.http.proxyHost=$proxyHost
systemProp.http.proxyPort=$proxyPort
systemProp.https.proxyHost=$proxyHost
systemProp.https.proxyPort=$proxyPort
systemProp.http.nonProxyHosts=localhost|127.0.0.1|[::1]
"@
        Write-Ok "proxy settings written to $userGradleFile"
    }
    else {
        Write-Ok 'proxy already configured in the user-level gradle.properties'
    }
}

# ---------------------------------------------------------------------------
# 8) Verify what we just installed.
# ---------------------------------------------------------------------------
Write-Step 'Verification'
Write-Host '--- java -version ---'
& "$JdkDir\bin\java.exe" -version 2>&1 | ForEach-Object { "    $_" }
Write-Host '--- sdkmanager --list_installed ---'
& $SdkManager --sdk_root="$SdkDir" --list_installed 2>&1 | Where-Object { $_ -match 'platform|build-tools|Installed' } | ForEach-Object { "    $_" }
Write-Host '--- adb version ---'
& (Join-Path $SdkDir 'platform-tools\adb.exe') version 2>&1 | ForEach-Object { "    $_" }
Write-Host '--- gradle --version ---'
& $GradleBin --version 2>&1 | Where-Object { $_ -match 'Gradle|JVM|Kotlin' } | ForEach-Object { "    $_" }

Write-Host ''
Write-Host 'Toolchain ready.' -ForegroundColor Green
Write-Host "  JAVA_HOME    = $JdkDir"
Write-Host "  ANDROID_HOME = $SdkDir"
Write-Host "  gradle       = $GradleBin"
