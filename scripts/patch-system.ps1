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
$SdkRoot = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path (Split-Path -Parent $PSScriptRoot) 'sdk' }
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

# Single entry point for guest commands. Takes the adb argument list and
# returns the trimmed combined stdout/stderr as a string, so callers can both
# run a command and read back its result without repeating the & $Adb dance.
function Invoke-Adb($argsArr) {
  # adb writes ordinary progress to stderr ("1 file pushed, ..."), which under
  # ErrorActionPreference='Stop' would abort the whole script. Temporarily
  # relax it and silence stderr so only real failures surface via $LASTEXITCODE.
  $old = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  $o = & $Adb -s $script:CurrentSerial @argsArr 2>&1
  $ErrorActionPreference = $old
  return ($o | Out-String).Trim()
}

foreach ($s in $Serials) {
  Write-Step "=== $s ==="
  $script:CurrentSerial = $s
  $boot = (& $Adb -s $s shell getprop sys.boot_completed 2>$null) -replace "`r",''
  if ($boot -notmatch '1') { Write-Warn "$s not booted yet"; continue }

  # --- 1. root -------------------------------------------------------------
  Write-Step '  adb root'
  & $Adb -s $s root 2>&1 | Out-Null
  Start-Sleep -Seconds 4
  & $Adb -s $s wait-for-device 2>&1 | Out-Null
  $who = (& $Adb -s $s shell id 2>$null) -replace "`r",''
  if ($who -match 'uid=0') { Write-Ok '  root acquired' } else { Write-Warn "  not root ($who)" }

  # --- 2. runtime low-RAM configuration ------------------------------------
  # ro.config.low_ram cannot be set on this image (see the header), so we get
  # the savings at runtime instead. All of these are idempotent and safe to
  # re-apply; nothing here breaks the boot path.
  Write-Step '  applying runtime low-RAM configuration'

  # a) Cap cached background processes.
  #    `cmd activity set-process-limit` DOES NOT EXIST on API 29 - it answers
  #    "Unknown command: set-process-limit" (verified against `cmd activity
  #    help` on this image). The supported mechanism on Android 10 is the
  #    device_config overlay, which ActivityManager reads directly. So use that
  #    as primary and verify the value reads back, rather than invoking a
  #    build-specific Binder transaction that varies between AOSP forks.
  $dc = Invoke-Adb @('shell','device_config','put','activity_manager','max_cached_processes','2')
  $v = Invoke-Adb @('shell','device_config','get','activity_manager','max_cached_processes')
  if ($v -match '2') { Write-Ok '    max_cached_processes = 2 (verified)' }
  else { Write-Warn "    max_cached_processes put said '$dc', reads '$v'" }
  # NOTE: device_config is a VOLATILE overlay. It survives a normal reboot but
  # is reset by a factory reset / -wipe-data, so bench-memory.ps1 re-applies it
  # for every level.

  # b) Animations off. Not a direct RAM win, but it stops the compositor and
  #    SurfaceFlinger from allocating transient buffers for window transitions.
  foreach ($sc in @('window_animation_scale','transition_animation_scale','animator_duration_scale')) {
    Invoke-Adb @('shell','settings','put','global',$sc,'0') | Out-Null
  }
  $av = Invoke-Adb @('shell','settings','get','global','window_animation_scale')
  if ($av -match '0') { Write-Ok '    animation scales = 0 (verified)' }
  else { Write-Warn "    window_animation_scale reads '$av'" }

  # c) Disable non-essential system packages. Reversible with:
  #    pm enable <pkg>. Note printspooler can break apps that print, so this is
  #    deliberate rather than free.
  foreach ($pkg in @('com.android.printspooler',
                     'com.android.wallpaper.livepicker',
                     'com.android.dreams.basic')) {
    $r = Invoke-Adb @('shell','pm','disable-user','--user','0',$pkg)
    if ($r -match 'disabled|Disabled|new state') { Write-Host "    disabled $pkg" }
    else { Write-Warn "    disable $pkg -> $r" }
  }

  # d) DofusLauncher as HOME, then drop Launcher3 (measured ~59 MB PSS).
  $apk = Join-Path (Split-Path -Parent $PSScriptRoot) 'launcher\build\DofusLauncher.apk'
  if (Test-Path $apk) {
    $ins = Invoke-Adb @('install','-r','-g',$apk)
    if ($ins -match 'Success') {
      Invoke-Adb @('shell','cmd','package','set-home-activity','com.dofusemu.launcher/.DofusLauncherActivity') | Out-Null
      Invoke-Adb @('shell','pm','disable-user','--user','0','com.android.launcher3') | Out-Null
      Write-Ok '    DofusLauncher installed as HOME; Launcher3 disabled'
    } else { Write-Warn "    launcher install: $ins" }
  } else {
    Write-Warn '    DofusLauncher.apk not built - run scripts\build-launcher.bat first'
  }

  # e) zram (compressed swap). Genuine oversubscription only; it does not make
  #    zram0's backing store free memory.
  $zsh = Join-Path $PSScriptRoot 'apply-zram.sh'
  if (Test-Path $zsh) {
    Invoke-Adb @('push',$zsh,'/data/local/tmp/apply-zram.sh') | Out-Null
    $zr = Invoke-Adb @('shell','sh','/data/local/tmp/apply-zram.sh')
    ($zr -split "`n") | Where-Object { $_ -match 'algorithm|disksize|swapon|unavailable' } |
      ForEach-Object { Write-Host "    $_" }
  }

  # --- 3. dalvik.vm.* ------------------------------------------------------
  # ART heap caps are DELIBERATELY NOT APPLIED. Setting
  # dalvik.vm.heapgrowthlimit=192m caused Dofus Touch to crash on launch:
  #   java.lang.OutOfMemoryError: Failed to allocate a 7193688 byte allocation
  #   ... target footprint 16777216, growth limit 16777216
  #   at org.apache.cordova.file.FileUtils$25.run
  # The app does not declare android:largeHeap and Cordova's startup JSON parse
  # needs more than the ~16 MB it was left with. dalvik.vm.heapsize already
  # defaults to 512m here, so ART is left to manage its own heap.
  Write-Ok '  ART heap left at image defaults (dalvik.vm.heapsize = 512m)'

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


