<#
.SYNOPSIS
  Build a "golden" userdata.img that already has every instance-scoped tweak
  baked in, so new instances need no adb trim pass at all.

.DESCRIPTION
  Rationale: everything we tune per instance lives in /data (userdata.img), not
  in the read-only system.img. That means it can be captured once and copied:

    pm disable-user ...   -> /data/system/users/0/package-restrictions.xml
    settings put global   -> /data/system/users/0/settings_secure.xml
    device_config put     -> /data/system/users/0/device_config.xml
    appops set            -> /data/system/users/0/appops.xml
    HOME app selection    -> /data/system/users/0/package-restrictions.xml
    installed APKs        -> /data/app/*
    dexopt artifacts      -> /data/dalvik-cache/*

  So the plan is:
    1. boot a clean instance
    2. apply the full trim once, install the game, shut down CLEANLY
    3. flatten the userdata overlay+backing into a self-contained raw
       userdata-golden.img (qemu-img convert)
    4. every later instance starts by copying the golden image

  This removes the adb trim pass from instance startup entirely - it is both
  faster and far more reliable, because there is no window in which the guest is
  half-trimmed.

  WHAT THIS DOES *NOT* CAPTURE (still needs adb at boot):
    - zRAM / vm.* sysctls: these are KERNEL runtime state, reset every boot.
      Must be re-applied each boot.
    - stopping init services (statsd, traced, rild, cameraserver, drmserver):
      these are started by init before any userdata is read.
    - Anything touching /sys.

  So the honest split is:
    userdata golden image  -> package disables, settings, HOME app, APKs
    per-boot adb pass      -> zRAM + daemon stops + sysctls (a few seconds)

.NOTES
  The golden image is copied AFTER a clean shutdown. Booting and killing an
  instance with `adb emu kill` while it is still writing userdata can produce a
  torn image, so we always shut down first and then copy.

.PARAMETER AvdName
  AVD to build from. Default 'dofus'.

.PARAMETER GoldenName
  Output file name, placed next to the AVD. Default 'userdata-golden.img'.

.PARAMETER RamMb
  RAM for the build instance. Default 1024 (production floor).

.PARAMETER SkipGame
  Do not install the game (package/APK state only).

.EXAMPLE
  .\scripts\make-golden-image.ps1
#>
[CmdletBinding()]
param(
  [string] $AvdName    = 'dofus',
  [string] $GoldenName = 'userdata-golden.img',
  [int]    $RamMb      = 1024,
  [switch] $SkipGame,
  [switch] $Force
)

# adb writes "device offline" to stderr while the guest is still coming up.
# With ErrorActionPreference='Stop' that aborts the script during the boot poll,
# even though the device is progressing normally. Relax it globally and rely on
# explicit checks instead - errors are handled per-step.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Emu      = Join-Path $SdkRoot 'emulator\emulator.exe'
$Adb      = Join-Path $SdkRoot 'platform-tools\adb.exe'
$AvdDir   = Join-Path $env:USERPROFILE ".android\avd\$AvdName.avd"
$Port     = 5590
$Ser      = "emulator-$Port"
$GamePkg  = 'com.ankama.dofustouch'

function Write-Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "    [ok]   $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "    [warn] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "    [FAIL] $m" -ForegroundColor Red }
function A($x) { (& $Adb -s $Ser @x 2>&1 | Out-String).Trim() }

if (-not (Test-Path $Emu))   { Write-Err "emulator not found at $Emu"; exit 1 }
if (-not (Test-Path $Adb))   { Write-Err "adb not found at $Adb"; exit 1 }
if (-not (Test-Path $AvdDir)){ Write-Err "AVD dir not found: $AvdDir"; exit 1 }

$golden = Join-Path $AvdDir $GoldenName
if ((Test-Path $golden) -and -not $Force) {
  Write-Err "$GoldenName already exists. Use -Force to rebuild."
  exit 1
}

Write-Step '1/6  AVD hardware profile (shared by every instance)'
$cfg = Join-Path $AvdDir 'config.ini'
$want = [ordered]@{
  'hw.ramSize'     = "$RamMb"
  'hw.gpu.enabled' = 'yes'
  'hw.gpu.mode'    = 'host'
  'hw.cpu.ncore'   = '2'
  'hw.lcd.width'   = '1280'
  'hw.lcd.height'  = '720'
  'hw.lcd.density' = '213'
  'hw.initialOrientation' = 'landscape'
  'hw.gps'         = 'no'
  'hw.gsmModem'    = 'no'
  'hw.radio'       = 'no'
  'hw.audioInput'  = 'no'
  'hw.audioOutput' = 'no'
  'hw.camera.back' = 'none'
  'hw.camera.front'= 'none'
  'hw.keyboard'    = 'yes'
  'hw.mainKeys'    = 'no'
}
$cur = Get-Content $cfg
foreach ($k in $want.Keys) {
  $esc = [regex]::Escape($k)
  if ($cur -match "^\s*$esc\s*=") { $cur = $cur -replace "^\s*$esc\s*=.*$", "$k = $($want[$k])" }
  else { $cur += "$k = $($want[$k])" }
}
Set-Content -Path $cfg -Value $cur
Write-Ok 'config.ini updated (ram=$RamMb gpu=host 1280x720@213 landscape, no radio/gps/camera)'

Write-Step '2/6  boot clean instance (wipe-data)'
Get-Process -Name qemu-system-x86_64,emulator -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
Start-Sleep -Seconds 4
$ud = Join-Path $AvdDir 'userdata-qemu.img'
if (Test-Path $ud) { Remove-Item $ud -Force -EA SilentlyContinue }
# Remove the stale overlay too. It backs onto the raw image we just deleted, so
# leaving it would make the emulator boot through a chain whose backing file has
# been silently recreated underneath it - producing a guest whose /data is a mix
# of the old overlay deltas and a new, unrelated backing disk.
Remove-Item "$ud.qcow2" -Force -EA SilentlyContinue

Start-Process -FilePath $Emu -ArgumentList @(
  # NOTE ordering: every emulator-level flag must come BEFORE -qemu. Anything
  # after -qemu is handed to qemu-system-x86_64 instead, which fails with
  # "-wipe-data: invalid option" (that happened here).
  "-avd",$AvdName,"-port",$Port,"-gpu","host","-memory","$RamMb","-cores","2",
  "-no-snapshot","-no-audio","-no-boot-anim","-no-metrics","-accel","on",
  "-wipe-data",
  "-qemu","-m","${RamMb}M"
) -WindowStyle Hidden | Out-Null

for ($i=0; $i -lt 120; $i++) {
  Start-Sleep -Seconds 5
  $b = A @('shell','getprop','sys.boot_completed')
  if ($b -match '1') { break }
  # The emulator can register as "offline" for a while after launch; that is
  # normal and is not a failure.
  if ($i % 6 -eq 0) { Write-Host "    ...booting ($i*5s) state=$((A @('get-state')))" }
}
if ((A @('shell','getprop','sys.boot_completed')) -notmatch '1') { Write-Err 'boot failed'; exit 1 }
Write-Ok 'booted'
# `adb root` restarts adbd. If we proceed before it comes back, EVERY later
# adb call fails with "device offline" and the golden image silently captures an
# untrimmed, game-less state - which is exactly what happened on the first run
# (it reported "0 packages disabled" and still exited 0).
# So: hard-fail rather than capture garbage.
A @('root') | Out-Null
# `adb root` restarts adbd. A single `adb root` can appear to succeed and then
# be lost while the daemon is still coming up, leaving uid=2000(shell) forever.
# So: issue the command AND verify, retrying the command itself rather than
# only re-checking. Without this the run aborted on a perfectly healthy guest.
$rooted = $false
for ($i=0; $i -lt 30; $i++) {
  A @('root') | Out-Null
  for ($j=0; $j -lt 8; $j++) {
    Start-Sleep -Seconds 3
    if ((A @('shell','id')) -match 'uid=0') { $rooted = $true; break }
  }
  if ($rooted) { break }
  Write-Host "    ...adbd still uid=2000, retrying adb root ($i)"
}
if (-not $rooted) {
  Write-Err 'adbd would not return as root - aborting rather than capturing a bad golden image'
  A @('emu','kill') | Out-Null
  exit 1
}
Write-Ok "root confirmed: $((A @('shell','id')))"

Write-Step '3/6  apply instance-scoped state (captured by the golden image)'
# NOTE: zRAM/daemon stops/sysctls are deliberately NOT done here - they are
# kernel/init runtime state and are LOST on reboot. They belong in the per-boot
# pass (see boot-instance.ps1), not in the golden image.
$pkgs = @(
  # UI layer - the single biggest win (~135 MB PSS combined)
  'com.android.systemui','com.android.launcher3','com.android.inputmethod.latin',
  # telephony / radio
  'com.android.phone','com.android.providers.telephony','com.android.cellbroadcastreceiver',
  'com.android.dialer','com.android.ims.rcsservice','com.android.mms.service',
  # location / sensors
  'com.android.location.fused',
  # media / misc
  'com.android.printspooler','com.android.wallpaper.livepicker','com.android.dreams.basic',
  # unused apps
  'com.android.camera2','com.android.gallery3d','com.android.calendar',
  'com.android.providers.calendar','com.android.email','com.android.quicksearchbox',
  'com.android.contacts','com.android.deskclock','com.android.music','com.android.browser'
)
$nOk = 0; $nFail = @()
foreach ($p in $pkgs) {
  $r = A @('shell','pm','disable-user','--user','0',$p)
  if ($r -match 'disabled') { $nOk++ }
  else { $nFail += $p }
}
Write-Ok "$nOk/$($pkgs.Count) packages disabled"
if ($nOk -lt ($pkgs.Count * 0.5)) {
  Write-Err "more than half the package disables failed: $($nFail -join ', ')"
  Write-Err 'the guest is probably not usable - aborting instead of capturing a bad golden image'
  A @('emu','kill') | Out-Null
  exit 1
}

# Settings, appops, HOME selection - all persisted in /data.
foreach ($sc in @('window_animation_scale','transition_animation_scale','animator_duration_scale')) {
  A @('shell','settings','put','global',$sc,'0') | Out-Null
}
A @('shell','locksettings','set-disabled','true') | Out-Null
A @('shell','cmd','appops','set','com.android.phone','RUN_IN_BACKGROUND','ignore') | Out-Null
A @('shell','device_config','put','activity_manager','max_cached_processes','2') | Out-Null
Write-Ok 'animation scales 0, lockscreen disabled, phone appops ignored, max_cached_processes=2'

# Launcher as HOME.
$launcherApk = Join-Path $RepoRoot 'launcher\build\DofusLauncher.apk'
if (Test-Path $launcherApk) {
  A @('install','-r','-g',$launcherApk) | Out-Null
  A @('shell','cmd','package','set-home-activity','com.dofusemu.launcher/.DofusLauncherActivity') | Out-Null
  Write-Ok 'DofusLauncher installed and set as HOME'
} else {
  Write-Warn 'DofusLauncher.apk not built - run scripts\build-launcher.bat first'
}

Write-Step '4/6  install the game (if provided)'
if (-not $SkipGame) {
  $apkm = Get-ChildItem (Join-Path $RepoRoot 'apks') -Filter '*dofustouch*.apkm' -File -EA SilentlyContinue | Select-Object -First 1
  if ($apkm) {
    $ex = Join-Path $env:TEMP 'golden_extract'
    if (Test-Path $ex) { Remove-Item $ex -Recurse -Force -EA SilentlyContinue }
    New-Item -ItemType Directory -Force -Path $ex | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($apkm.FullName, $ex)
    $apks = (Get-ChildItem $ex -Filter '*.apk' -File).FullName
    $res = A (@('install-multiple','-r','-g') + $apks)
    if ($res -match 'Success') { Write-Ok "game installed ($($apks.Count) APKs, one atomic transaction)" }
    else {
      Write-Warn "install-multiple: $res"
      Write-Err 'game install failed - the golden image would not be usable'
      A @('emu','kill') | Out-Null
      exit 1
    }
    # Verify-only compilation keeps dexopt artifacts small and avoids a full AOT
    # pass, which keeps the golden image small.
    A @('shell','cmd','package','compile','-m','verify','-f',$GamePkg) | Out-Null
    Write-Ok 'compile -m verify'
    Remove-Item $ex -Recurse -Force -EA SilentlyContinue
  } else {
    Write-Warn 'no dofustouch*.apkm in apks\ - golden will have no game'
  }
}

Write-Step '5/6  settle, then CLEAN shutdown'
A @('shell','echo','3','>','/proc/sys/vm/drop_caches') | Out-Null
Start-Sleep -Seconds 10
# Clean shutdown. Killing mid-write risks a torn userdata image.
A @('emu','kill') | Out-Null
$gone = $false
for ($i=0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 3
  if (-not (Get-Process -Name qemu-system-x86_64 -EA SilentlyContinue)) { $gone = $true; break }
}
if (-not $gone) {
  Write-Warn 'emulator still running; force-stopping (image may be torn)'
  Get-Process -Name qemu-system-x86_64,emulator -EA SilentlyContinue | Stop-Process -Force
  Start-Sleep -Seconds 5
} else {
  Write-Ok 'clean shutdown'
}

Write-Step '6/6  capture userdata -> golden'

# When the emulator boots, it NEVER writes to the raw userdata-qemu.img; it
# writes to a qcow2 overlay (userdata-qemu.img.qcow2) whose backing file is the
# raw image. Copying userdata-qemu.img would therefore capture the pristine
# factory disk and silently lose every change (package disables, game install).
#
# Flatten overlay+backing into ONE self-contained qcow2 with qemu-img convert.
# Output MUST be qcow2, not raw: the emulator's bundled qemu-img is built with a
# 32-bit write path and dies with "Input/output error" at exactly byte 2147483648
# (2 GiB) when emitting a raw image, while qcow2 output completes fine. Raw is not
# an option here - this is a tool limitation, not a disk-space one.
$qemuImg = Join-Path $SdkRoot 'emulator\qemu-img.exe'
$ud      = Join-Path $AvdDir 'userdata-qemu.img'
$ovl     = "$ud.qcow2"
$src     = if (Test-Path $ovl) { $ovl } else { $ud }
Remove-Item $golden -Force -EA SilentlyContinue
& $qemuImg convert -O qcow2 $src $golden
if ($LASTEXITCODE -ne 0) { Write-Err "qemu-img convert failed ($src -> $GoldenName)"; exit 1 }
$sz = [math]::Round((Get-Item $golden).Length/1MB,0)
Write-Ok "wrote $GoldenName ($sz MB, flattened from $(Split-Path -Leaf $src))"
Write-Host @"

    Golden image: $golden

    To use it for a new instance, copy it over the instance's userdata BEFORE
    first boot:
        Copy-Item '<golden>' '<avd>.avd\userdata-qemu.img'

    Remember: zRAM, daemon stops and vm.* sysctls are KERNEL state and must
    still be applied on every boot (scripts\boot-instance.ps1). Everything
    else - package disables, settings, HOME app, game install - is baked in.
"@ -ForegroundColor Cyan
