<#
.SYNOPSIS
    Provision a portable (no-admin) Android build toolchain on Windows.

.DESCRIPTION
    Installs everything needed to build the Android client of the AI Banking
    Agent without requiring UAC elevation:

      * Temurin JDK 17          -> <Base>\jdk-17
      * Android cmdline-tools   -> <Sdk>\cmdline-tools\<revision>
      * Android platform-tools  -> <Sdk>\platform-tools   (adb)
      * Android platform/build-tools via Android CLI
      * Gradle distribution     -> <Base>\gradle-<ver>    (used only to
                                   bootstrap the project's Gradle wrapper)

    Every step is idempotent: an already-present artefact is skipped, so the
    script can be re-run to repair a partial download.  User-level environment
    variables (JAVA_HOME, ANDROID_HOME, ANDROID_SDK_ROOT, PATH) are written at
    the end so Android Studio and future shells pick the same SDK up.

.PARAMETER Base
    Root directory for the portable toolchain.  Defaults to D:\AndroidDev.

.PARAMETER ApiLevel
    Android platform package suffix to install.  Defaults to 37.0 (API 37).

.EXAMPLE
    pwsh -File android\tools\install-android-toolchain.ps1

.NOTES
    Keep these defaults aligned with android/app/build.gradle.kts,
    android/gradle/libs.versions.toml and the Gradle wrapper: this project uses
    AGP 9.4.1, Gradle 9.8.1, Build Tools 36.0.0 and JDK 17 for SDK 37.
#>
[CmdletBinding()]
param(
    [string]$Base = 'D:\AndroidDev',
    [string]$ApiLevel = '37.0',
    [string]$BuildTools = '36.0.0',
    [string]$GradleVersion = '9.8.1',
    [string]$CmdlineToolsBuild = '16111833',
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
# 1) JDK 17 (Temurin), the minimum supported by AGP 9.4.1.
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
# 2) Android cmdline-tools. Android CLI 23 understands the SDK 37.0 metadata;
#    Android Studio can adopt this SDK directory later.
# ---------------------------------------------------------------------------
Write-Step 'Android cmdline-tools'
$CmdlineToolsZip = Join-Path $DownloadDir "commandlinetools-win-${CmdlineToolsBuild}.zip"
Get-RemoteFile -Uri "https://dl.google.com/android/repository/commandlinetools-win-${CmdlineToolsBuild}_latest.zip" -OutFile $CmdlineToolsZip -Label 'Android cmdline-tools'
if ($CmdlineToolsBuild -eq '16111833' -and
    (Get-FileHash -LiteralPath $CmdlineToolsZip -Algorithm SHA1).Hash -ne '57d04f2d75eb8e8fffc5000a987e5de4b5a63e9d') {
    throw "Android cmdline-tools checksum mismatch. Remove the incomplete archive and retry: $CmdlineToolsZip"
}

# Read the archive's revision so an old cmdline-tools/latest is never mistaken
# for this build. Keep older installations intact and use the standard SDK
# package directory (23.0 for build 16111833) for the selected revision.
$archive = [System.IO.Compression.ZipFile]::OpenRead($CmdlineToolsZip)
try {
    $sourceProperties = $archive.GetEntry('cmdline-tools/source.properties')
    if (-not $sourceProperties) { throw 'cmdline-tools archive has no source.properties' }
    $reader = [System.IO.StreamReader]::new($sourceProperties.Open())
    try { $archiveProperties = $reader.ReadToEnd() } finally { $reader.Dispose() }
} finally { $archive.Dispose() }
$revisionMatch = [regex]::Match($archiveProperties, '(?m)^Pkg\.Revision\s*=\s*(\d+(?:\.\d+)+)\s*$')
if (-not $revisionMatch.Success) { throw 'cmdline-tools archive has an invalid revision' }
$CmdlineToolsRevision = $revisionMatch.Groups[1].Value
$CmdlineToolsDir = Join-Path $SdkDir "cmdline-tools\$CmdlineToolsRevision"
$SdkManager = Join-Path $CmdlineToolsDir 'bin\sdkmanager.bat'
$installedProperties = Join-Path $CmdlineToolsDir 'source.properties'
$correctRevision = (Test-Path -LiteralPath $installedProperties) -and
    ((Get-Content -LiteralPath $installedProperties -Raw) -match "(?m)^Pkg\.Revision\s*=\s*$([regex]::Escape($CmdlineToolsRevision))\s*$")
if (-not ((Test-Path -LiteralPath $SdkManager) -and $correctRevision)) {
    $staging = Join-Path $SdkDir "_cmdline-staging-$([guid]::NewGuid().ToString('N'))"
    try {
        Expand-Zip -ZipPath $CmdlineToolsZip -Destination $staging -Label 'Android cmdline-tools'
        New-Item -ItemType Directory -Force -Path $CmdlineToolsDir | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $staging 'cmdline-tools') |
            Copy-Item -Destination $CmdlineToolsDir -Recurse -Force
    } finally {
        $sdkRootPath = [System.IO.Path]::GetFullPath($SdkDir).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
        $stagingPath = [System.IO.Path]::GetFullPath($staging)
        if (-not $stagingPath.StartsWith("$sdkRootPath\", [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a staging directory outside the SDK: $stagingPath"
        }
        if (Test-Path -LiteralPath $stagingPath) { Remove-Item -LiteralPath $stagingPath -Recurse -Force }
    }
}
if (-not (Test-Path $SdkManager)) { throw "sdkmanager not found at $SdkManager" }
$AndroidCli = Join-Path $CmdlineToolsDir 'bin\android.exe'
if (-not (Test-Path -LiteralPath $AndroidCli)) { throw "Android CLI missing at $AndroidCli; use Command-line Tools 23 or later." }

# Gradle and avdmanager need the selected JDK rather than an older Java on PATH.
$env:JAVA_HOME = $JdkDir
$env:ANDROID_HOME = $SdkDir
$env:ANDROID_SDK_ROOT = $SdkDir
$env:PATH = "$JdkDir\bin;$SdkDir\platform-tools;$CmdlineToolsDir\bin;$env:PATH"

# ---------------------------------------------------------------------------
# 3) Platform packages. CLI 23 handles licences without the obsolete
#    sdkmanager --licenses step. Call it directly: the compatibility .bat can
#    split semicolon package names and still report exit code zero on failure.
# ---------------------------------------------------------------------------
Write-Step "Android SDK packages (platform-tools, platforms/android-$ApiLevel, build-tools/$BuildTools)"
Invoke-Native -FilePath $AndroidCli -Arguments @(
    '--no-metrics',
    "--sdk=$SdkDir",
    'sdk', 'install',
    'platform-tools',
    "platforms/android-$ApiLevel",
    "build-tools/$BuildTools"
)
# Check files and exact package metadata as well as the process exit status.
foreach ($relativeFile in @('platform-tools\adb.exe', "platforms\android-$ApiLevel\android.jar", "build-tools\$BuildTools\aapt2.exe")) {
    $installedFile = Join-Path $SdkDir $relativeFile
    if (-not (Test-Path -LiteralPath $installedFile -PathType Leaf) -or (Get-Item -LiteralPath $installedFile).Length -eq 0) {
        throw "SDK installation did not produce the expected file: $installedFile"
    }
}
$platformProperties = Get-Content -LiteralPath (Join-Path $SdkDir "platforms\android-$ApiLevel\source.properties") -Raw
$buildToolsProperties = Get-Content -LiteralPath (Join-Path $SdkDir "build-tools\$BuildTools\source.properties") -Raw
if ($platformProperties -notmatch "(?m)^AndroidVersion\.ApiLevel\s*=\s*$([regex]::Escape($ApiLevel))\s*$") {
    throw "Installed Android platform metadata does not match $ApiLevel"
}
if ($buildToolsProperties -notmatch "(?m)^Pkg\.Revision\s*=\s*$([regex]::Escape($BuildTools))\s*$") {
    throw "Installed Build Tools metadata does not match $BuildTools"
}
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
    if ($GradleVersion -eq '9.8.1' -and
        (Get-FileHash -LiteralPath $gradleZip -Algorithm SHA256).Hash -ne 'dce76f55f8e251a3a1f130eb120f30b3d271de2b76c9b0729d316b5a1b6dc01f') {
        throw "Gradle archive checksum mismatch. Remove the incomplete archive and retry: $gradleZip"
    }
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
# Prefer the selected tools over older entries while preserving the user's PATH.
$preferredEntries = @("$JdkDir\bin", "$SdkDir\platform-tools", "$CmdlineToolsDir\bin", "$GradleDir\bin")
$remainingEntries = @($userPath -split ';' | Where-Object { $_ -and $_ -notin $preferredEntries })
$userPath = ($preferredEntries + $remainingEntries) -join ';'
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
Write-Host '--- android sdk list ---'
Invoke-Native -FilePath $AndroidCli -Arguments @('--no-metrics', "--sdk=$SdkDir", 'sdk', 'list')
Write-Host '--- adb version ---'
& (Join-Path $SdkDir 'platform-tools\adb.exe') version 2>&1 | ForEach-Object { "    $_" }
Write-Host '--- gradle --version ---'
& $GradleBin --version 2>&1 | Where-Object { $_ -match 'Gradle|JVM|Kotlin' } | ForEach-Object { "    $_" }

Write-Host ''
Write-Host 'Toolchain ready.' -ForegroundColor Green
Write-Host "  JAVA_HOME    = $JdkDir"
Write-Host "  ANDROID_HOME = $SdkDir"
Write-Host "  gradle       = $GradleBin"
