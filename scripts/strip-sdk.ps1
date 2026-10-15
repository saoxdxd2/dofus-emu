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

# --- bin64\ : legacy Cygwin toolset (mksdcard, cygwin1.dll & friends) from the
# pre-Qt emulator era. The modern 35.x emulator never touches it. (5.3 MB)
$total += Move-DirToBackup    'emulator\bin64'

# --- lib64\vulkan\ : Vulkan host renderer. The farm only offers "host",
# "angle_indirect" (D3D11) and "swiftshader_indirect" - never -gpu vulkan.
# (0.9 MB)
$total += Move-DirToBackup    'emulator\lib64\vulkan'

# --- libandroid-emu-metrics.dll : usage metrics / crash upload library.
# The farm always launches with -no-metrics. (11.7 MB) MUST be verified with a
# live cold boot after stripping - if emulator.exe fails to start, run with
# -Restore and remove this line.
$total += Move-FileToBackup 'emulator\libandroid-emu-metrics.dll'

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

# --- system-images : convert the raw read-only partitions to UNCOMPRESSED qcow2.
# system.img ships as a sparse RAW image (3.2 GB virtual). Converting it to
# qcow2 keeps the exact same guest-visible content (verified live: cold boot to
# sys.boot_completed=1 with ADB online in ~2-4 s in BOTH formats) and cuts the
# installed size from ~3.2 GB to ~1.5 GB. The emulator auto-detects the qcow2
# format, so no launch-args change is needed.
#
# Deliberately NOT zlib-compressed (-c): measured live, a compressed system.img
# (~700 MB) is INCOMPRESSIBLE for the 7z installer payload, so setup.exe grows
# by ~300 MB (553 MB -> ~850 MB archive) even though the installed SDK is ~900 MB
# smaller. Since setup.exe size and runtime CPU are the priorities, we ship
# UNCOMPRESSED qcow2: 7z squeezes its ext4 payload ~4.5:1 (553 MB archive) and
# QEMU reads it with zero zlib-inflate overhead. If minimal installed disk
# matters more to you, run: qemu-img convert -c -O qcow2 system.img tmp && move
# Idempotent: qemu-img info is used to detect the current format, so re-running
# the script never re-converts an image that is already qcow2 (compressed or not).
$qemuImg = Join-Path $SdkRoot 'emulator\qemu-img.exe'
if (Test-Path $qemuImg) {
    $sysimgRoots = Get-ChildItem (Join-Path $SdkRoot 'system-images') -Directory -Recurse -ErrorAction SilentlyContinue |
                   Where-Object { Test-Path (Join-Path $_.FullName 'system.img') }
    foreach ($root in $sysimgRoots) {
        foreach ($part in @('system.img', 'vendor.img')) {
            $img = Join-Path $root.FullName $part
            if (-not (Test-Path $img -PathType Leaf)) { continue }
            $json = (& $qemuImg info --output=json $img 2>$null) -join "`n"
            try { $info = $json | ConvertFrom-Json } catch { $info = $null }
            if ($info.format -eq 'qcow2') {
                $ratio = 0
                if ($info.'virtual-size' -and $info.'virtual-size' -gt 0) {
                    $ratio = (Get-Item $img).Length / $info.'virtual-size'
                }
                Write-Host ("  [SKIP ] {0} already qcow2 ({1:P0} of virtual size)" -f (Join-Path $root.Name $part), $ratio)
                continue
            }
            $sizeBefore = (Get-Item $img).Length
            $tmp = "$img.qcow2new"
            Write-Host ("  [CONV ] {0} -> qcow2 (raw {1:N0} MB)..." -f $part, ($sizeBefore / 1MB)) -NoNewline
            # NO -c here on purpose: zlib-compressing the clusters would shrink the
            # installed image (~705 -> ~584 MB) but PREVENT 7z/LZMA2 in setup.exe
            # from compressing the raw ext4 payload (~4.5:1), so the shipped archive
            # would GROW by ~300 MB (measured live). Uncompressed qcow2 is also
            # zero-overhead at runtime (no zlib inflate on the boot path).
            & $qemuImg convert -c -O qcow2 -p $img $tmp 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tmp)) {
                Write-Host " FAILED (keeping original image)" -ForegroundColor Red
                if (Test-Path $tmp) { Remove-Item $tmp -Force }
                continue
            }
            # Keep the pre-conversion image in the backup tree so -Restore can undo this.
            $rel = $img.Substring($SdkRoot.Length + 1)
            $dst = Join-Path $BackupRoot $rel
            New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
            if (Test-Path $dst) {
                # A previous conversion already stored an original here; the old
                # pre-conversion file is superseded - drop it to save disk space.
                Remove-Item -LiteralPath $dst -Force
            }
            Move-Item -LiteralPath $img -Destination $dst -Force
            Move-Item -LiteralPath $tmp -Destination $img -Force
            $sizeAfter = (Get-Item $img).Length
            Write-Host (" done ({0:N0} MB -> {1:N0} MB)" -f ($sizeBefore / 1MB), ($sizeAfter / 1MB))
            $total += ($sizeBefore - $sizeAfter)
        }
    }
} else {
    Write-Host "[WARN] qemu-img.exe not found - skipping system-image conversion." -ForegroundColor Yellow
}

# --- NOTE (do NOT strip): Qt WebEngine (emulator\lib64\qt\lib\Qt6WebEngineCore*.dll,
# ~116 MB + QtWebEngineProcess.exe + qtwebengine_resources*.pak) may look unused -
# it only renders the emulator's sign-in/help web pages - but emulator.exe HARD-LINKS
# Qt6WebEngineWidgetsAndroidEmu.dll at startup. Removing it makes the emulator exit
# silently right after "Found systemPath". Verified by live test: emulator dies
# without it and boots normally again once restored. Leave the Qt folder intact.

$sdkSizeAfter = (Get-ChildItem $SdkRoot -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum

Write-Host ""
Write-Host ("Stripped: {0:N1} MB   SDK size: {1:N1} MB -> {2:N1} MB" -f ($total / 1MB), ($sdkSizeBefore / 1MB), ($sdkSizeAfter / 1MB)) -ForegroundColor Green
Write-Host "Run 'powershell -File scripts\strip-sdk.ps1 -Restore' to undo." -ForegroundColor DarkGray
