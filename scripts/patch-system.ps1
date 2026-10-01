<#
.SYNOPSIS
  Apply the low-RAM profile to a booted guest.

.DESCRIPTION
  Applies what CAN be applied safely on this image, and reports honestly what
  cannot, rather than failing.

  FINDING (API 29 x86_64 image, emulator 37.x) - ro.config.low_ram is NOT
  reachable at runtime here. It lives in the ro.* namespace so `setprop` fails
  with "Access denied", and all three routes into build.prop were tried:

    1. `-writable-system` + `adb remount`: the emulator logs "System image is
       writable" but the guest then HANGS - adb stays "device offline" with
       near-idle qemu CPU (288s CPU over ~5 min), where a normal boot finishes
       in ~47s. Reproduced at 1536 MB and again at 1024 MB with 4.3 GB free,
       so it is not memory pressure.
    2. `emulator -prop ro.config.low_ram=true`: boots fine, but the property is
       absent afterwards - the emulator does not inject arbitrary ro.* values
       into this image.
    3. overlayfs over /system from adb root: "mount: 'overlay'->'/mnt':
       Invalid argument" - the upper dir needs an SELinux label adb cannot set.

  This image is system-as-root: "/" is a read-only ext4 (dm-2) containing
  /system, /product and /vendor, so build.prop is not writable live.

  What IS applied here: the dalvik.vm.* properties, which are not ro.* and so
  work at runtime. That is where most of the real saving is, since it caps ART
  heap growth.

  A permanent ro.config.low_ram needs an offline edit of system.img. This
  script is NOT a substitute for that.

.PARAMETER Serials
  adb serials. Defaults to all running emulators.

.PARAMETER AttemptRoFix
  Still try the remount route, in case a future emulator/image allows it.

.EXAMPLE
  .\scripts\patch-system.ps1 -Serials emulator-5554
#>
[CmdletBinding()]
param(
  [string[]] $Serials,
  [int]      $HeapMb  = 192,
  [switch]   $AttemptRoFix
)

$ErrorActionPreference = 'Stop'
$SdkRoot = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { 'C:\android-sdk' }
$Adb     = Join-Path $SdkRoot 'platform-tools\adb.exe'

function Write-Step($m) { Write-Host "[patch] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[ok]    $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[warn]  $m" -ForegroundColor Yellow }

if (-not $Serials) {
  $Serials = (& $Adb devices) |
    Select-String '^emulator-\d+\s+device$' |
    ForEach-Object { ($_ -split '\s+')[0] }
}
if (-not $Serials) { Write-Warn 'no running emulator instances found.'; exit 1 }
if (-not $Serials) { Write-Warn 'no running emulator instances found.'; exit 1 }

function Write-Err($m)  { Write-Host "[FAIL]  $m" -ForegroundColor Red }

foreach ($s in $Serials) {
  Write-Step "=== $s ==="
  $boot = (& $Adb -s $s shell getprop sys.boot_completed 2>$null) -replace "`r",''
  if ($boot -notmatch '1') { Write-Warn "$s not booted yet"; continue }

  # --- 1. root -------------------------------------------------------------
  Write-Step '  adb root'
  & $Adb -s $s root 2>&1 | Out-Null
  Start-Sleep -Seconds 4
  & $Adb -s $s wait-for-device 2>&1 | Out-Null
  $who = (& $Adb -s $s shell id 2>$null) -replace "`r",''
  if ($who -match 'uid=0') { Write-Ok '  root acquired' } else { Write-Warn "  not root ($who)" }

  # --- 2. ro.config.low_ram ------------------------------------------------
  if ($AttemptRoFix) {
    Write-Step '  attempting ro.config.low_ram via remount'
    $rm = (& $Adb -s $s remount 2>&1 | Out-String)
    $probe = & $Adb -s $s shell 'touch /system/build.prop.__wtest 2>/dev/null && rm -f /system/build.prop.__wtest && echo WRITABLE || echo READONLY' 2>$null
    if ($probe -match 'WRITABLE') {
      & $Adb -s $s shell 'echo "ro.config.low_ram=true" >> /system/build.prop' 2>&1 | Out-Null
      Write-Ok '  wrote ro.config.low_ram (reboot to activate)'
    } else {
      Write-Warn "  /system READONLY ($probe) - cannot write build.prop"
      Write-Warn ('  remount: ' + (($rm.Trim() -split "`n") | Select-Object -Last 1))
    }
  } else {
    Write-Warn '  ro.config.low_ram SKIPPED (needs an offline system.img edit).'
    Write-Warn '  See this script header and README "Known limitations".'
  }

  # --- 3. dalvik.vm.* (runtime-settable: where the real saving is) ---------
  Write-Step '  applying dalvik.vm.* limits (settable at runtime)'
  $dprops = [ordered]@{
    'dalvik.vm.heapgrowthlimit' = "${HeapMb}m"
    'dalvik.vm.heapstartupsize' = '32m'
    'dalvik.vm.heapminfree'     = '2m'
  }
  foreach ($k in $dprops.Keys) {
    & $Adb -s $s shell "setprop $k $($dprops[$k])" | Out-Null
    $got = (& $Adb -s $s shell "getprop $k" 2>$null) -replace "`r",''
    if ($got -eq $dprops[$k]) { Write-Ok "  $k = $got" }
    else { Write-Warn "  $k read back as '$got'" }
  }

  # --- 4. report -----------------------------------------------------------
  Write-Step '  current guest state:'
  foreach ($p in @('ro.config.low_ram','dalvik.vm.heapgrowthlimit','dalvik.vm.heapstartupsize')) {
    $v = (& $Adb -s $s shell "getprop $p" 2>$null) -replace "`r",''
    if ([string]::IsNullOrWhiteSpace($v)) { $v = '<unset>' }
    Write-Host ("    {0,-32} = {1}" -f $p, $v)
  }
  $m = (& $Adb -s $s shell "mount | grep ' / '" 2>$null) -replace "`r",''
  Write-Host "    root mount: $m"
  Write-Host ''
}


