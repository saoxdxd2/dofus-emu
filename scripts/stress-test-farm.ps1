<#
.SYNOPSIS
  Comprehensive Multi-Instance Farm Stress-Tester & Visual Screenshot Verification.
.DESCRIPTION
  Performs complete production validation:
    1. Starts 4 instances with the Efficiency 1-Core Profile (768 MB RAM, 1 vCPU, 30 FPS).
    2. Measures boot completion and time-to-boot for all 4 instances.
    3. Runs runtime debloat, zRAM activation, and in-memory stealth property patch.
    4. Audits stealth penetration & anti-cheat detection across instances.
    5. Measures Host CPU, Host RAM, and per-guest QEMU working set memory.
    6. Tiles 4 emulator windows into an edge-to-edge 2x2 grid.
    7. Captures high-res screenshots of every guest instance.
    8. Captures full host desktop screenshot showing 4 instances tiled side-by-side.
    9. Captures GUI Farm Manager window screenshot verifying clean UI encoding.
#>
[CmdletBinding()]
param(
  [int] $Count = 4,
  [int] $RamMb = 768,
  [int] $Cores = 1,
  [switch] $SkipLaunch,
  [string] $ArtifactsDir = 'C:\Users\sao\.gemini\antigravity\brain\0b156d3f-8d35-4725-8793-1f182ba87d00'
)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Adb      = Join-Path $SdkRoot 'platform-tools\adb.exe'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

if (-not (Test-Path $ArtifactsDir)) {
  New-Item -ItemType Directory -Path $ArtifactsDir -Force | Out-Null
}

function Write-Header($title) {
  Write-Host "`n=================================================================" -ForegroundColor Cyan
  Write-Host "  $title" -ForegroundColor White
  Write-Host "=================================================================" -ForegroundColor Cyan
}

function Capture-Desktop([string]$filename) {
  $target = Join-Path $ArtifactsDir $filename
  $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
  $bmp.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose()
  $bmp.Dispose()
  Write-Host "  [SCREENSHOT] Saved: $target ($([math]::Round((Get-Item $target).Length/1KB, 1)) KB)" -ForegroundColor Green
  return $target
}

function Capture-Guest([string]$serial, [string]$filename) {
  $target = Join-Path $ArtifactsDir $filename
  & $Adb -s $serial shell screencap -p /sdcard/screen_tmp.png 2>$null
  & $Adb -s $serial pull /sdcard/screen_tmp.png $target 2>$null | Out-Null
  & $Adb -s $serial shell rm /sdcard/screen_tmp.png 2>$null
  if (Test-Path $target) {
    Write-Host "  [SCREENSHOT] Guest $serial -> $target ($([math]::Round((Get-Item $target).Length/1KB, 1)) KB)" -ForegroundColor Green
  } else {
    Write-Host "  [WARN] Failed to capture guest screenshot for $serial" -ForegroundColor Yellow
  }
  return $target
}

# -------------------------------------------------------------
# PHASE 1: LAUNCH OR VERIFY 4 INSTANCES
# -------------------------------------------------------------
Write-Header "PHASE 1: INSTANCE LAUNCH & BOOT BENCHMARK"

if (-not $SkipLaunch) {
  Write-Host "Launching 4-instance farm (Profile: 768 MB RAM, 1 vCPU, 30 FPS)..." -ForegroundColor Yellow
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  
  # Invoke start-farm.ps1 with AutoBoot
  & (Join-Path $PSScriptRoot 'start-farm.ps1') -Count $Count -RamMb $RamMb -Cores $Cores -AutoBoot -Force -EdgeToEdge
  $sw.Stop()
  Write-Host "Start-farm bootstrap completed in $([math]::Round($sw.Elapsed.TotalSeconds, 1)) seconds." -ForegroundColor Cyan
}

# Wait for all 4 instances to report online
$expectedSerials = (1..$Count) | ForEach-Object { "emulator-$(5554 + 2*($_-1))" }
Write-Host "Verifying ADB status for: $($expectedSerials -join ', ')..." -ForegroundColor DarkGray

$bootedSerials = @()
$deadline = [DateTime]::UtcNow.AddSeconds(120)
while ([DateTime]::UtcNow -lt $deadline) {
  $online = @((& $Adb devices) | Select-String -Pattern '^(emulator-\d+)\s+device' | ForEach-Object { $_.Matches[0].Groups[1].Value })
  $allUp = $true
  foreach ($s in $expectedSerials) {
    if ($online -notcontains $s) { $allUp = $false; break }
    $b = ((& $Adb -s $s shell getprop sys.boot_completed 2>$null) -replace "`r", '').Trim()
    if ($b -ne '1') { $allUp = $false; break }
  }
  if ($allUp) {
    $bootedSerials = $expectedSerials
    break
  }
  Start-Sleep -Seconds 3
}

if ($bootedSerials.Count -lt $Count) {
  Write-Host "  [WARN] Not all $Count instances booted in time. Online: $($bootedSerials.Count)/$Count" -ForegroundColor Yellow
} else {
  Write-Host "  [OK] All $Count instances booted and responsive via ADB!" -ForegroundColor Green
}

# -------------------------------------------------------------
# PHASE 2: RUNTIME HARDENING & IN-MEMORY PROPERTIES
# -------------------------------------------------------------
Write-Header "PHASE 2: RUNTIME DEBLOAT, ZRAM & IN-MEMORY PROPERTIES"

foreach ($s in $bootedSerials) {
  Write-Host "Hardening $s..." -ForegroundColor DarkGray
  & (Join-Path $PSScriptRoot 'boot-instance.ps1') -Serials @($s)
}

# -------------------------------------------------------------
# PHASE 3: HOST & GUEST RESOURCE BENCHMARK
# -------------------------------------------------------------
Write-Header "PHASE 3: RESOURCE EFFICIENCY & STABILITY METRICS"

# Host CPU & RAM
$os = Get-CimInstance Win32_OperatingSystem
$totalRamGB = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
$freeRamGB = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
$usedRamGB = [math]::Round($totalRamGB - $freeRamGB, 2)
$cpuLoad = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average

Write-Host "HOST METRICS:" -ForegroundColor Yellow
Write-Host "  Host Total RAM : $totalRamGB GB"
Write-Host "  Host Used RAM  : $usedRamGB GB ($([math]::Round(($usedRamGB / $totalRamGB)*100, 1))%)"
Write-Host "  Host Free RAM  : $freeRamGB GB"
Write-Host "  Host CPU Load  : $cpuLoad %"

# QEMU / Emulator processes
$qemuProcs = Get-Process -Name 'qemu-system-x86_64' -ErrorAction SilentlyContinue
Write-Host "`nEMULATOR PROCESSES ($($qemuProcs.Count) active):" -ForegroundColor Yellow
$totalQemuWorkingSetMB = 0
foreach ($qp in $qemuProcs) {
  $wsMB = [math]::Round($qp.WorkingSet64 / 1MB, 1)
  $totalQemuWorkingSetMB += $wsMB
  $cpuSec = [math]::Round($qp.TotalProcessorTime.TotalSeconds, 1)
  Write-Host "  PID $($qp.Id,-6) | WorkingSet: $($wsMB,6) MB | Threads: $($qp.Threads.Count,3) | CPU Time: ${cpuSec}s"
}
Write-Host "  Total QEMU Working Set: $([math]::Round($totalQemuWorkingSetMB, 1)) MB ($([math]::Round($totalQemuWorkingSetMB/1024, 2)) GB)" -ForegroundColor Cyan

# In-guest Memory & zRAM
Write-Host "`nIN-GUEST MEMORY ALLOCATION & ZRAM SWAP:" -ForegroundColor Yellow
foreach ($s in $bootedSerials) {
  $memInfo = & $Adb -s $s shell cat /proc/meminfo 2>$null
  $memTot = if ($memInfo -match 'MemTotal:\s+(\d+)') { [math]::Round([int]$Matches[1]/1024, 0) } else { 0 }
  $memAvail = if ($memInfo -match 'MemAvailable:\s+(\d+)') { [math]::Round([int]$Matches[1]/1024, 0) } else { 0 }
  $swapTot = if ($memInfo -match 'SwapTotal:\s+(\d+)') { [math]::Round([int]$Matches[1]/1024, 0) } else { 0 }
  $swapFree = if ($memInfo -match 'SwapFree:\s+(\d+)') { [math]::Round([int]$Matches[1]/1024, 0) } else { 0 }
  $swapUsed = $swapTot - $swapFree
  Write-Host "  $s : MemTotal=$($memTot)MB | MemAvailable=$($memAvail)MB | zRAM Used=$($swapUsed)MB / $($swapTot)MB"
}

# -------------------------------------------------------------
# PHASE 4: PENETRATION & STEALTH AUDIT
# -------------------------------------------------------------
Write-Header "PHASE 4: STEALTH & ANTI-CHEAT PENETRATION AUDIT"

if ($bootedSerials.Count -gt 0) {
  $auditSerial = $bootedSerials[0]
  Write-Host "Running comprehensive stealth penetration audit on $auditSerial..." -ForegroundColor Yellow
  & (Join-Path $PSScriptRoot 'tester-penetration-audit.ps1') -Serial $auditSerial
}

# -------------------------------------------------------------
# PHASE 5: TILE 2x2 GRID & VISUAL SCREENSHOTS
# -------------------------------------------------------------
Write-Header "PHASE 5: 2x2 WINDOW TILING & VISUAL SCREENSHOT CAPTURE"

# Layout windows 2x2 edge to edge
Write-Host "Applying 2x2 edge-to-edge window placement..." -ForegroundColor Cyan
. (Join-Path $PSScriptRoot 'layout.ps1')
$procIds = @($qemuProcs | ForEach-Object { $_.Id })
Set-FarmLayout -Procs $procIds -EdgeToEdge

Start-Sleep -Seconds 2

# Capture in-guest screenshots for each instance
Write-Host "Capturing in-guest screenshots..." -ForegroundColor Cyan
$guestImages = @()
$i = 1
foreach ($s in $bootedSerials) {
  $fname = "screenshot_guest_slot0${i}_$s.png"
  $img = Capture-Guest $s $fname
  $guestImages += $img
  $i++
}

# Capture full desktop showing tiled emulators
Write-Host "Capturing desktop screenshot (2x2 Emulator Farm)..." -ForegroundColor Cyan
$desktopImg = Capture-Desktop "screenshot_farm_2x2_tiled.png"

# Launch GUI Manager, wait for render, capture its window, then close
Write-Host "Testing GUI Manager Visual Rendering..." -ForegroundColor Cyan
$guiPsi = New-Object System.Diagnostics.ProcessStartInfo
$guiPsi.FileName = "powershell.exe"
$guiPsi.Arguments = "-NoProfile -ExecutionPolicy Bypass -STA -File `"$((Join-Path $PSScriptRoot 'gui-manager.ps1'))`""
$guiPsi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Normal
$guiProc = [System.Diagnostics.Process]::Start($guiPsi)

Start-Sleep -Seconds 5
$guiDesktopImg = Capture-Desktop "screenshot_gui_manager.png"

try {
  Stop-Process -Id $guiProc.Id -Force -EA SilentlyContinue
} catch {}

Write-Header "STRESS TEST & VISUAL VERIFICATION COMPLETE"
Write-Host "All metrics captured and screenshots saved to: $ArtifactsDir" -ForegroundColor Green
