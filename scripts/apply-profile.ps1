<#
.SYNOPSIS
  Apply the low-RAM profile, device identity, and zRAM to a booted instance.

.DESCRIPTION
  Pushes and runs apply-zram.sh, sets the low-RAM properties, and prints a
  consolidated profile report for one or more instances. Intended to be run
  after the guest reports boot_completed=1.

  All changes are runtime setprop/sysfs values: fully reversible by a reboot,
  which keeps the instance clean rather than baking in test state.

.PARAMETER Serials
  adb serials to configure. Defaults to every running emulator.

.EXAMPLE
  .\scripts\apply-profile.ps1 -Serials emulator-5554,emulator-5556
#>
[CmdletBinding()]
param(
  [string[]] $Serials,
  [int]      $ZramMb = 0,      # 0 => use 50% of guest MemTotal
  [int]      $HeapMb = 192
)

$ErrorActionPreference = 'Stop'
$SdkRoot = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path (Split-Path -Parent $PSScriptRoot) 'sdk' }
$Adb     = Join-Path $SdkRoot 'platform-tools\adb.exe'
$Script  = Join-Path $PSScriptRoot 'apply-zram.sh'

if (-not $Serials) {
  $Serials = (& $Adb devices) |
    Select-String '^emulator-\d+\s+device$' |
    ForEach-Object { ($_ -split '\s+')[0] }
}
if (-not $Serials) { Write-Host "[profile] no running emulator instances found."; exit 1 }

foreach ($s in $Serials) {
  Write-Host "=== $s ===" -ForegroundColor Cyan

  $boot = (& $Adb -s $s shell getprop sys.boot_completed 2>$null) -replace "`r",''
  if ($boot -notmatch '1') { Write-Host "  [warn] not booted yet (sys.boot_completed=$boot)"; continue }

  # --- low-RAM profile -----------------------------------------------------
  # ro.config.low_ram is in the ro.* namespace, which is READ-ONLY after boot:
  #   `setprop ro.config.low_ram true` -> "failed to set property ... Access denied".
  # It is also consumed by zygote/ActivityManager at startup, so it only takes
  # effect if present in /system/build.prop before boot. The guest therefore
  # must have been started with -writable-system.
  Write-Host '  remounting /system...'
  & $Adb -s $s root 2>&1 | Out-Null
  Start-Sleep -Seconds 2
  $rm = & $Adb -s $s remount 2>&1
  if ($rm -match 'remount succeeded|remounted') {
    $cnt = (& $Adb -s $s shell 'grep -c "^ro.config.low_ram=" /system/build.prop 2>/dev/null' 2>$null)
    if ($cnt -match '1') {
      & $Adb -s $s shell 'sed -i "s/^ro.config.low_ram=.*/ro.config.low_ram=true/" /system/build.prop' 2>&1 | Out-Null
    } else {
      & $Adb -s $s shell 'echo "ro.config.low_ram=true" >> /system/build.prop' 2>&1 | Out-Null
    }
    $v = (& $Adb -s $s shell 'grep "^ro.config.low_ram=" /system/build.prop' 2>$null) -replace "`r",''
    if ($v -match 'true') { Write-Host "  ro.config.low_ram = true (in build.prop; reboot to activate)" }
    else { Write-Host "  [warn] build.prop write did not verify: '$v'" }
  } else {
    Write-Host '  [warn] remount failed - guest must be started with -writable-system.' -ForegroundColor Yellow
    Write-Host '  [warn] ro.config.low_ram cannot be set at runtime; skipping.' -ForegroundColor Yellow
  }

  # dalvik.vm.* has no ro. prefix, so it IS settable at runtime. This is the
  # part that actually bounds ART heap growth during the gate tests.
  Write-Host '  applying Dalvik heap limits (runtime-settable)...'
  & $Adb -s $s shell "setprop dalvik.vm.heapgrowthlimit ${HeapMb}m" | Out-Null
  & $Adb -s $s shell "setprop dalvik.vm.heapstartupsize 32m" | Out-Null
  $hg = (& $Adb -s $s shell getprop dalvik.vm.heapgrowthlimit 2>$null) -replace "`r",''
  Write-Host "  dalvik.vm.heapgrowthlimit = $hg"

  # --- zRAM ----------------------------------------------------------------
  if (Test-Path $Script) {
    Write-Host '  configuring zRAM...'
    & $Adb -s $s push $Script /data/local/tmp/apply-zram.sh 2>&1 | Out-Null
    $out = & $Adb -s $s shell "sh /data/local/tmp/apply-zram.sh $ZramMb" 2>&1
    $out | ForEach-Object { "    $_" }
  }

  # --- identity coherence --------------------------------------------------
  Write-Host '  device profile:'
  foreach ($p in @('ro.product.cpu.abi','ro.product.cpu.abilist','ro.build.type','ro.build.tags','ro.debuggable')) {
    $v = (& $Adb -s $s shell "getprop $p" 2>$null) -replace "`r",''
    Write-Host ("    {0,-24} = {1}" -f $p, $v)
  }
  $su = (& $Adb -s $s shell 'which su' 2>$null)
  if ($su -match 'su') { Write-Host '    [warn] su present - profile should be root-free' -ForegroundColor Yellow }
  else { Write-Host '    no su (root-free profile confirmed)' }

  # --- memory snapshot -----------------------------------------------------
  Write-Host '  memory:'
  $mi = & $Adb -s $s shell cat /proc/meminfo 2>$null
  foreach ($k in @('MemTotal','MemAvailable','SwapTotal','SwapFree')) {
    $l = $mi | Select-String "^$k" | Select-Object -First 1
    if ($l) { Write-Host "    $($l.Line.Trim())" }
  }
  Write-Host ''
}
