<#
.SYNOPSIS
  Patch /system/build.prop on a booted -writable-system guest.

.DESCRIPTION
  Applies the low-RAM profile to the guest's persistent build.prop.

  WHY THIS EXISTS: properties in the ro.* namespace (including
  ro.config.low_ram) are read-only once the guest has booted. `setprop` returns
  "failed to set property ... to ...: Access denied" and, in some builds,
  silently does nothing. So an "Android Go mode" flag cannot be turned on at
  runtime - it has to be present in the image before zygote starts.

  Two ways to do that:
    1. -writable-system + `adb remount` (this script) - fast, per-session, and
       the overlay is discarded on exit. Good for gate testing.
    2. Bake it into the image at prep time (Phase 2) - permanent, survives
       reboot, no per-boot remount cost. Preferred for production.

  REQUIREMENT: the guest must have been started with -writable-system,
  otherwise /system is read-only and `adb remount` fails.

  PROPERTIES WRITTEN:
    ro.config.low_ram=true
        the real AOSP low-RAM flag (the "Go Edition" of the original spec
        does not exist on modern Android). Caps background procs/services.
    ro.config.low_ram.bg_apps_limit=1
        further limits how many background apps survive.
    dalvik.vm.heapgrowthlimit / heapstartupsize / heapminfree
        bound ART heap growth so a small guest is not OOM-killed by a runaway
        allocation. These are NOT ro.* so they also apply at runtime.

  A reboot is required for ro.config.low_ram to take effect, because zygote
  reads it during startup.

.PARAMETER Serials
  adb serials. Defaults to all running emulators.

.PARAMETER Reboot
  Reboot each guest afterwards so the zygote-time properties take effect.

.EXAMPLE
  .\scripts\patch-system.ps1 -Serials emulator-5554 -Reboot
#>
[CmdletBinding()]
param(
  [string[]] $Serials,
  [int]      $HeapMb   = 192,
  [int]      $BgLimit  = 1,
  [switch]   $Reboot
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

$propList = @(
  @{ Name = 'ro.config.low_ram';                Value = 'true' },
  @{ Name = 'ro.config.low_ram.bg_apps_limit'; Value = "$BgLimit" },
  @{ Name = 'dalvik.vm.heapgrowthlimit';        Value = "${HeapMb}m" },
  @{ Name = 'dalvik.vm.heapstartupsize';        Value = '32m' },
  @{ Name = 'dalvik.vm.heapminfree';            Value = '2m' }
)


foreach ($s in $Serials) {
  Write-Step "=== $s ==="

  $boot = (& $Adb -s $s shell getprop sys.boot_completed 2>$null) -replace "`r",''
  if ($boot -notmatch '1') { Write-Warn "$s not booted (sys.boot_completed='$boot')"; continue }

  # --- 1. gain root --------------------------------------------------------
  Write-Step '  adb root'
  & $Adb -s $s root 2>&1 | ForEach-Object { "    $_" }
  # adbd restarts after `root`, so the transport drops briefly.
  Start-Sleep -Seconds 4
  & $Adb -s $s wait-for-device 2>&1 | Out-Null

  $who = (& $Adb -s $s shell id 2>$null) -replace "`r",''
  if ($who -match 'uid=0') { Write-Ok "  root acquired ($who)" }
  else {
    Write-Warn "  not running as root ($who)"
    Write-Warn '  a "userdebug"/"eng" image is required for the writable-system flow.'
    continue
  }

  # --- 2. remount /system --------------------------------------------------
  Write-Step '  adb remount (overlays /system for this session)'
  $rmText = (& $Adb -s $s remount 2>&1 | Out-String)
  if ($rmText -match 'remount succeeded|remounted|remount') { Write-Ok '  remount ok' }
  else { Write-Warn "  remount output: $rmText" }

  # Confirm writability empirically rather than trusting the message text.
  $probe = & $Adb -s $s shell 'touch /system/build.prop.__wtest 2>/dev/null && rm -f /system/build.prop.__wtest && echo WRITABLE || echo READONLY' 2>$null
  if ($probe -match 'WRITABLE') { Write-Ok '  /system confirmed writable' }
  else {
    Write-Warn "  /system is read-only ($probe) - ro.* properties cannot be patched"
    Write-Warn '  the AVD was probably started WITHOUT -writable-system'
    continue
  }

  # --- 3. write the properties --------------------------------------------
  Write-Step '  writing properties to /system/build.prop'
  foreach ($p in $propList) {
    $key = $p.Name; $val = $p.Value
    $has = & $Adb -s $s shell "grep -c '^${key}=' /system/build.prop 2>/dev/null" 2>$null
    if ($has -match '^\s*1') {
      & $Adb -s $s shell "sed -i 's|^${key}=.*|${key}=${val}|' /system/build.prop" 2>&1 | Out-Null
      $verb = 'updated'
    } else {
      & $Adb -s $s shell "echo '${key}=${val}' >> /system/build.prop" 2>&1 | Out-Null
      $verb = 'appended'
    }
    # Read back and verify: a silent write failure here would invalidate the
    # whole run and is easy to miss.
    $got = (& $Adb -s $s shell "grep '^${key}=' /system/build.prop" 2>$null) -replace "`r",''
    if ($got -match [regex]::Escape($val)) { Write-Ok "  $key = $val ($verb)" }
    else { Write-Warn "  $key verify FAILED; build.prop shows: '$got'" }
  }

  # --- 4. report live (pre-reboot) values ----------------------------------
  Write-Step '  current runtime values (ro.* stay old until reboot):'
  foreach ($p in $propList) {
    $v = (& $Adb -s $s shell "getprop $($p.Name)" 2>$null) -replace "`r",''
    if ([string]::IsNullOrWhiteSpace($v)) { $v = '<unset>' }
    Write-Host ("    {0,-36} = {1}" -f $p.Name, $v)
  }

  if ($Reboot) {
    Write-Step '  rebooting so zygote picks up the ro.* values'
    & $Adb -s $s reboot 2>&1 | Out-Null
    Write-Ok '  reboot issued; wait for sys.boot_completed=1 then re-read getprop.'
  } else {
    Write-Warn '  pass -Reboot to activate ro.config.low_ram (read at zygote startup).'
  }
  Write-Host ''
}
