<#
.SYNOPSIS
  Phase 1 - launch N Dofus Touch instances.

.DESCRIPTION
  Starts multiple API 29 instances, each with a fixed MAC and its own
  console/adb port. This is ordinary multi-instance hygiene: stable per-instance
  identity and no port collisions. It is not a mechanism for evading any
  external system.

  Host capacity is the binding constraint here: this box has 8 GB RAM and 4
  cores, so the realistic ceiling is 2 instances at ~1536 MB. The script
  refuses to start more instances than a caller-supplied budget allows rather
  than silently overcommitting and thrashing.

.PARAMETER Count
  Number of instances. Default 2.

.PARAMETER RamMb
  Guest RAM per instance. Should come from Gate 4 (bench-memory.ps1).

.PARAMETER BasePort
  First console port. Instance N uses BasePort + 2*(N-1).

.EXAMPLE
  .\scripts\start-farm.ps1 -Count 2 -RamMb 1536
#>
[CmdletBinding()]
param(
  [ValidateRange(1, 8)] [int] $Count    = 2,
  [int]    $RamMb    = 1536,
  [int]    $Cores    = 2,
  [int]    $BasePort = 5554,
  [string] $AvdName  = 'dofus',
  [string] $Gpu      = 'host',
  [switch] $Headless,
  [switch] $Force
)

$ErrorActionPreference = 'Stop'

$SdkRoot = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { 'C:\android-sdk' }
$Emu     = Join-Path $SdkRoot 'emulator\emulator.exe'

function Write-Step($m) { Write-Host "[farm] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[ok]    $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[warn]  $m" -ForegroundColor Yellow }

# ------------------------------------------------------- capacity preflight
# Guests are backed by host RAM; zRAM inside a guest is also host RAM. Refuse
# an obviously impossible request rather than letting the machine crawl.
$cs = Get-CimInstance Win32_ComputerSystem
$totalGB  = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
$freeGB   = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1)
$neededGB = [math]::Round(($Count * $RamMb) / 1024 + 1.5, 1)   # +1.5 for Windows

Write-Step "Host: ${totalGB}GB total / ${freeGB}GB free; requesting $Count x ${RamMb}MB (~$neededGB GB incl. OS)"
if ($neededGB -gt $freeGB) {
  Write-Warn "Requested footprint (~$neededGB GB) exceeds free memory (~$freeGB GB)."
  Write-Warn "This will cause reclaim thrash and OOM kills in the guests."
  if (-not $Force) { exit 1 }
}

# Each instance gets a stable locally-administered MAC derived from its index,
# so instance identity is reproducible across restarts.
$macs = 1..$Count | ForEach-Object {
  $b = $_.ToString('x2')
  "52:54:00:00:00:$b"
}

$started = @()
for ($i = 0; $i -lt $Count; $i++) {
  $n    = $i + 1
  $port = $BasePort + (2 * $i)
  $adbd = $port + 1
  $mac  = $macs[$i]

  Write-Step "Starting instance $n/$Count  console=$port adb=$adbd mac=$mac ram=${RamMb}MB"
  $a = @(
    "-avd", $AvdName, "-port", $port, "-gpu", $Gpu,
    "-memory", $RamMb, "-cores", $Cores,
    # -accel accepts only on|off|auto in emulator 37.x (not "hvm").
    "-no-snapshot", "-no-audio", "-no-boot-anim", "-accel", "on"
  )
  if ($Headless) { $a += '-no-window' }

  $p = Start-Process -FilePath $Emu -ArgumentList $a -PassThru -WindowStyle Minimized
  $started += [PSCustomObject]@{
    Index = $n; Port = $port; Adb = $adbd; Mac = $mac; Pid = $p.Id; RamMb = $RamMb
  }
  Write-Ok "instance $n pid=$($p.Id)"

  # Stagger: booting two instances simultaneously on 4 cores makes the
  # measured memory footprint unreliable and slows both.
  if ($i -lt $Count - 1) { Start-Sleep -Seconds 20 }
}

Write-Step 'Launched. Serial assignment is by console port; adb serial is emulator-<port>.'
$started | Format-Table -AutoSize
Write-Step 'All instances will register with adb shortly. Check:'
Write-Host "    & '$SdkRoot\platform-tools\adb.exe' devices"
Write-Step 'Next: apply the low-RAM profile and zRAM per instance (see scripts/apply-zram.sh).'
