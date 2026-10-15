# scripts\strip-sdk.ps1
# Strips everything the Dofus Touch farm does not need from the bundled Android SDK:
# physical-device support (USB drivers, fastboot), diagnostics tools, crash reporting,
# dev headers, the headless QEMU binary (the farm always shows the windowed emulator),
# non-English Qt locales and unused platform-tools.
#
# Every removed item is MOVED (not deleted) into "sdk-stripped-backup" next to the
# repository root, so a stripped install can be restored at any time with -Restore.
#
# Usage:
#   powershell -File scripts\strip-sdk.ps1            # strip (idempotent, re-runnable)
#   powershell -File scripts\strip-sdk.ps1 -Restore   # undo a previous strip
#   powershell -File scripts\strip-sdk.ps1 -SdkRoot "D:\some\sdk"   # custom SDK location
#
# No paths are hardcoded: the SDK root defaults to "<repo>\sdk" relative to this
# script's location, and can be overridden with -SdkRoot.

param(
    [string]$SdkRoot = "",
    [switch]$Restore
)

$ErrorActionPreference = 'Stop'

# --- Locate repo root relative to this script (scripts\ -> parent) ---
if (-not $SdkRoot) {
    $SdkRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'sdk'
}

if (-not (Test-Path (Join-Path $SdkRoot 'emulator\emulator.exe'))) {
    Write-Host "[ERROR] No Android SDK found at '$SdkRoot' (emulator\emulator.exe missing)." -ForegroundColor Red
    Write-Host "        Pass -SdkRoot <path> or run the script from its original location." -ForegroundColor Red
    exit 1
}

$BackupRoot = Join-Path (Split-Path -Parent $SdkRoot) 'sdk-stripped-backup'

function Move-FileToBackup([string]$relativePath) {
    $src = Join-Path $SdkRoot $relativePath
    if (-not (Test-Path $src -PathType Leaf)) { return 0 }
    $size = (Get-Item $src).Length
    $dst = Join-Path $BackupRoot $relativePath
    New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
    Move-Item -LiteralPath $src -Destination $dst -Force
    Write-Host ("  [STRIP] {0,-72} {1,8:N1} MB" -f $relativePath, ($size / 1MB))
    return $size
}

function Move-DirToBackup([string]$relativePath) {
    $src = Join-Path $SdkRoot $relativePath
    if (-not (Test-Path $src)) { return 0 }
    $size = (Get-ChildItem $src -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    if (-not $size) { $size = 0 }
    $dst = Join-Path $BackupRoot $relativePath
    New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
    Move-Item -LiteralPath $src -Destination $dst -Force
    Write-Host ("  [STRIP] {0,-72} {1,8:N1} MB" -f $relativePath, ($size / 1MB))
    return $size
}

if ($Restore) {
    if (-not (Test-Path $BackupRoot)) {
        Write-Host "[INFO] Nothing to restore: '$BackupRoot' does not exist." -ForegroundColor Yellow
        exit 0
    }
    Write-Host "Restoring stripped SDK files from '$BackupRoot' into '$SdkRoot'..." -ForegroundColor Cyan
    Get-ChildItem $BackupRoot -Recurse -Force | Where-Object { -not $_.PSIsContainer } | ForEach-Object {
        $rel = $_.FullName.Substring($BackupRoot.Length + 1)
        $dst = Join-Path $SdkRoot $rel
        New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
        Move-Item -LiteralPath $_.FullName -Destination $dst -Force
    }
    # Remove the now-empty directory skeleton left in the backup
    Get-ChildItem $BackupRoot -Recurse -Force -Directory | Sort-Object { $_.FullName.Length } -Descending | Remove-Item -Force -ErrorAction SilentlyContinue
    Remove-Item $BackupRoot -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Restore complete." -ForegroundColor Green
    exit 0
}

$sdkSizeBefore = (Get-ChildItem $SdkRoot -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum

Write-Host "Stripping non-Dofus SDK payload from '$SdkRoot'..." -ForegroundColor Cyan
Write-Host "(Backup: '$BackupRoot' - restore anytime with -Restore)" -ForegroundColor DarkGray
$total = [long]0

# --- emulator\ : diagnostics, crash reporting, dev files, USB device support ---
# emulator-check.exe : standalone diagnostics tool, never invoked by the farm.
$total += Move-FileToBackup 'emulator\emulator-check.exe'
# crashpad_handler.exe / crashreport.exe : Google crash reporting (metrics already disabled).
$total += Move-FileToBackup 'emulator\crashpad_handler.exe'
$total += Move-FileToBackup 'emulator\crashreport.exe'
# drivers\ : USB assistant drivers for physical devices - the farm talks to its
# instances over TCP/adb, no USB passthrough is used.
$total += Move-DirToBackup    'emulator\drivers'
# include\, lib\cmake, lib\pkgconfig : C/C++ development headers for building
# against the emulator - not needed at runtime.
$total += Move-DirToBackup    'emulator\include'
$total += Move-DirToBackup    'emulator\lib\cmake'
$total += Move-DirToBackup    'emulator\lib\pkgconfig'
# mksdcard.exe : the farm disables the SD card partition (hw.sdCard = false).
$total += Move-FileToBackup 'emulator\mksdcard.exe'
# resources\macros + macroPreviews : UI macro recorder previews, unused by the farm.
$total += Move-DirToBackup    'emulator\resources\macros'
$total += Move-DirToBackup    'emulator\resources\macroPreviews'

# --- qemu : the farm always launches the WINDOWED emulator (it never passes
# -no-window), so qemu-system-x86_64-headless.exe is dead weight (54.6 MB).
$total += Move-FileToBackup 'emulator\qemu\windows-x86_64\qemu-system-x86_64-headless.exe'

# --- Qt : keep only the en-US locale (QtWebEngine falls back to en-US; the farm
# UI is English-only).
$localeDir = Join-Path $SdkRoot 'emulator\lib64\qt\bin\qtwebengine_locales'
if (Test-Path $localeDir) {
    Get-ChildItem $localeDir -Filter '*.pak' | Where-Object { $_.Name -ne 'en-US.pak' } | ForEach-Object {
        $total += Move-FileToBackup ('emulator\lib64\qt\bin\qtwebengine_locales\' + $_.Name)
    }
}

# --- platform-tools : keep adb + its DLLs + image-format tools (mke2fs, make_f2fs).
# fastboot (flashing physical devices), sqlite3, etc1tool and hprof-conv
# (developer memory-profiling helpers) are never used by the farm.
$total += Move-FileToBackup 'platform-tools\fastboot.exe'
$total += Move-FileToBackup 'platform-tools\sqlite3.exe'
$total += Move-FileToBackup 'platform-tools\etc1tool.exe'
$total += Move-FileToBackup 'platform-tools\hprof-conv.exe'

# --- stray cache-partition image left in the emulator folder (66 MB). Each AVD has
# its own cache.img in its .avd folder; this one is never referenced.
$total += Move-FileToBackup 'emulator\cache.img'
$total += Move-FileToBackup 'emulator\cache.img.qcow2'

$sdkSizeAfter = (Get-ChildItem $SdkRoot -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum

Write-Host ""
Write-Host ("Stripped: {0:N1} MB   SDK size: {1:N1} MB -> {2:N1} MB" -f ($total / 1MB), ($sdkSizeBefore / 1MB), ($sdkSizeAfter / 1MB)) -ForegroundColor Green
Write-Host "Run 'powershell -File scripts\strip-sdk.ps1 -Restore' to undo." -ForegroundColor DarkGray
