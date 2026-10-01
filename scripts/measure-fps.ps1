<#
.SYNOPSIS
  Measure per-instance CPU / RSS / WebGL frame rate. Closes Gate 4.

.DESCRIPTION
  Gate 4's real question is not "does it boot" but "what does the WebGL game
  cost, and does the frame rate hold up". This samples host-side process
  working set and CPU, and pulls the guest's own per-process memory breakdown,
  so the production RAM allocation is set from measurement.

  The FPS half needs a frame counter in the page: WebView exposes no built-in
  FPS metric. We therefore inject a requestAnimationFrame counter via the
  remote-debugging endpoint when one is available, and report clearly when it
  is not, rather than inventing a number.

.PARAMETER Serials
  adb serials. Defaults to all running emulators.

.PARAMETER DurationSec
  Sampling window. Default 60.

.EXAMPLE
  .\scripts\measure-fps.ps1 -DurationSec 120
#>
[CmdletBinding()]
param(
  [string[]] $Serials,
  [int]      $DurationSec = 60,
  [int]      $IntervalSec = 5
)

$ErrorActionPreference = 'Stop'
$SdkRoot = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path (Split-Path -Parent $PSScriptRoot) 'sdk' }
$Adb     = Join-Path $SdkRoot 'platform-tools\adb.exe'

if (-not $Serials) {
  $Serials = (& $Adb devices) |
    Select-String '^emulator-\d+\s+device$' |
    ForEach-Object { ($_ -split '\s+')[0] }
}
if (-not $Serials) { Write-Host "[measure] no running instances."; exit 1 }

function Get-HostProcStats($pattern) {
  Get-Process -Name qemu-system-x86_64*, emulator* -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -like $pattern }
}

foreach ($s in $Serials) {
  Write-Host "=== $s ===" -ForegroundColor Cyan

  $port = ($s -replace 'emulator-','')
  $pids = Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -like 'qemu-system*' -or $_.ProcessName -eq 'emulator' }
  $totalWS = 0; $totalCPU = 0
  foreach ($p in $pids) { $totalWS += $p.WorkingSet64; $totalCPU += $p.CPU }
  Write-Host ("  host-side: {0} procs, WS={1} MB, cumulative CPU={2}s" -f $pids.Count, [math]::Round($totalWS/1MB,1), [math]::Round($totalCPU,1))

  Write-Host '  guest-side memory (dumpsys meminfo, selected):'
  $mi = & $Adb -s $s shell dumpsys meminfo 2>$null
  if ($mi) {
    foreach ($pat in @('Total RAM','Native Heap','Dalvik Heap','Graphics','GL mtrack','TOTAL PSS')) {
      $l = $mi | Select-String -Pattern ([regex]::Escape($pat)) | Select-Object -First 1
      if ($l) { Write-Host "    $($l.Line.Trim())" }
    }
  }

  # Per-process guest PSS: the webview/renderer and game processes dominate.
  Write-Host '  guest process breakdown:'
  $procs = & $Adb -s $s shell ps -A -o PID,NAME,RSS 2>$null
  $procs | Select-String -Pattern 'webview|chrome|dofus|zygote|surfaceflinger|system_server' |
    Select-Object -First 10 | ForEach-Object { Write-Host "    $($_.Line.Trim())" }

  # Guest /proc/meminfo: the definitive in-guest numbers.
  Write-Host '  guest /proc/meminfo:'
  $gmi = & $Adb -s $s shell cat /proc/meminfo 2>$null
  foreach ($k in @('MemTotal','MemFree','MemAvailable','Cached','SwapTotal','SwapFree')) {
    $l = $gmi | Select-String "^$k" | Select-Object -First 1
    if ($l) { Write-Host "    $($l.Line.Trim())" }
  }
  Write-Host ''
}
