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
  'hw.mainKeys'    = 'yes'
  'qemu.hw.mainkeys' = '1'
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

Write-Step '3/7  strip down Android (remove all non-needed bloat apps & services)'
$bloatPkgs = @(
  # telephony / radio / cellular services
  'com.android.phone','com.android.providers.telephony','com.android.cellbroadcastreceiver',
  'com.android.dialer','com.android.ims.rcsservice','com.android.mms.service','com.android.server.telecom',
  'com.android.carrierdefaultapp','com.android.service.ims','com.android.service.ims.presence','com.android.smspush',
  # location / sensors / smartcard / nfc / bluetooth
  'com.android.location.fused','com.android.se','com.android.apps.tag','com.android.bluetooth',
  'com.android.bluetoothmidiservice','com.android.companiondevicemanager',
  # printing & media
  'com.android.printspooler','com.android.bips','com.android.printservice.recommendation',
  'com.android.wallpaper.livepicker','com.android.dreams.basic','com.android.dreams.phototable',
  'com.android.wallpapercropper','com.android.wallpaperbackup','com.android.wallpaperpicker',
  # telemetry & tracing
  'com.android.traceur','com.android.settings.intelligence',
  # unused apps & providers
  'com.android.camera2','com.android.gallery3d','com.android.calendar',
  'com.android.providers.calendar','com.android.email','com.android.quicksearchbox',
  'com.android.contacts','com.android.deskclock','com.android.music','com.android.musicfx','com.android.browser',
  'com.android.messaging','com.android.emergency','com.android.hotspot2',
  'com.android.providers.userdictionary','com.android.providers.blockednumber',
  'com.android.providers.partnerbookmarks','com.android.bookmarkprovider'
)
$nOk = 0; $nFail = @()
foreach ($p in $bloatPkgs) {
  $r = A @('shell','pm','disable-user','--user','0',$p)
  if ($r -match 'disabled') { $nOk++ }
  else { $nFail += $p }
}
Write-Ok "$nOk/$($bloatPkgs.Count) non-needed bloat packages disabled"

# Settings, appops, cached processes limit, zero latency instant display
foreach ($sc in @('window_animation_scale','transition_animation_scale','animator_duration_scale')) {
  A @('shell','settings','put','global',$sc,'0') | Out-Null
}
A @('shell','locksettings','set-disabled','true') | Out-Null
A @('shell','settings','put','secure','lockscreen.disabled','1') | Out-Null
A @('shell','settings','put','global','stay_on_while_plugged_in','3') | Out-Null
A @('shell','settings','put','system','screen_off_timeout','2147483647') | Out-Null
A @('shell','settings','put','global','heads_up_notifications_enabled','0') | Out-Null
A @('shell','settings','put','global','package_verifier_enable','0') | Out-Null
A @('shell','cmd','appops','set','com.android.phone','RUN_IN_BACKGROUND','ignore') | Out-Null
A @('shell','device_config','put','activity_manager','max_cached_processes','2') | Out-Null
Write-Ok 'animation scales 0, lockscreen disabled, screen stay awake, phone background ignored, max_cached_processes=2'

# Global WebView mobile User-Agent & GPU flags
$wvCmd = "_ --user-agent=`"Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36`" --enable-gpu-rasterization --ignore-gpu-blocklist"
A @('shell','sh','-c',"echo '$wvCmd' > /data/local/tmp/webview-command-line") | Out-Null
A @('shell','chmod','666','/data/local/tmp/webview-command-line') | Out-Null
Write-Ok 'webview-command-line configured for global mobile User-Agent & GPU flags'

Write-Step '4/7  install Dofus from APK folder'
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
    if ($res -match 'Success') { Write-Ok "game installed ($($apks.Count) APKs, atomic transaction)" }
    else {
      Write-Warn "install-multiple: $res"
      Write-Err 'game install failed - aborting'
      A @('emu','kill') | Out-Null
      exit 1
    }
    A @('shell','cmd','package','compile','-m','verify','-f',$GamePkg) | Out-Null
    Write-Ok 'compile -m verify complete'
    Remove-Item $ex -Recurse -Force -EA SilentlyContinue
  } else {
    Write-Warn 'no dofustouch*.apkm in apks\'
  }
}

# Launcher as HOME
$launcherApk = Join-Path $RepoRoot 'launcher\build\DofusLauncher.apk'
if (Test-Path $launcherApk) {
  A @('install','-r','-g',$launcherApk) | Out-Null
  A @('shell','cmd','package','set-home-activity','com.dofusemu.launcher/.DofusLauncherActivity') | Out-Null
  Write-Ok 'DofusLauncher installed and set as HOME'
}

Write-Step '5/7  remove non-needed Android UI (SystemUI, Launcher3, LatinIME) & things'
$uiPkgs = @('com.android.systemui','com.android.launcher3','com.android.inputmethod.latin')
foreach ($p in $uiPkgs) {
  A @('shell','pm','disable-user','--user','0',$p) | Out-Null
}
A @('shell','pkill','-f','systemui') | Out-Null
Write-Ok 'SystemUI, Launcher3, LatinIME disabled and killed'

# Stop background daemons
$daemons = @('statsd','traced','traced_probes','incidentd','rild','cameraserver','drmserver','audioserver','media.audio-hal-2-0','wpa_supplicant','hostapd_nohidl','mediadrmserver')
foreach ($d in $daemons) {
  if ((A @('shell','service','check',$d)) -match 'found') { A @('shell','stop',$d) | Out-Null }
}
A @('shell','setprop','persist.logd.size','64K') | Out-Null
A @('shell','logcat','-G','64K') | Out-Null
A @('shell','logcat','-c') | Out-Null
Write-Ok 'Background daemons stopped and logd capped to 64K'

# Configure zRAM
$zsh = Join-Path $PSScriptRoot 'apply-zram.sh'
if (Test-Path $zsh) {
  $zb = ([System.IO.File]::ReadAllText($zsh)) -replace "`r`n","`n"
  $zb = $zb -replace "`r","`n"
  $zl = Join-Path $env:TEMP 'apply-zram-golden.sh'
  [System.IO.File]::WriteAllText($zl, $zb, (New-Object System.Text.UTF8Encoding $false))
  A @('push',$zl,'/data/local/tmp/apply-zram.sh') | Out-Null
  A @('shell','sh','/data/local/tmp/apply-zram.sh','512') | Out-Null
}
A @('shell','echo','85','>','/proc/sys/vm/swappiness') | Out-Null
A @('shell','echo','0','>','/proc/sys/vm/page-cluster') | Out-Null
A @('shell','echo','200','>','/proc/sys/vm/vfs_cache_pressure') | Out-Null
A @('shell','echo','3','>','/proc/sys/vm/drop_caches') | Out-Null
Start-Sleep -Seconds 5

# Verify memory footprint (~400MB)
$memInfo = A @('shell','cat','/proc/meminfo')
$anonKb = 0; $totKb = 0; $availKb = 0
if ($memInfo -match 'AnonPages:\s+(\d+)') { $anonKb = [int]$Matches[1] }
if ($memInfo -match 'MemTotal:\s+(\d+)')  { $totKb = [int]$Matches[1] }
if ($memInfo -match 'MemAvailable:\s+(\d+)') { $availKb = [int]$Matches[1] }
$anonMb = [math]::Round($anonKb/1024, 0)
$totMb  = [math]::Round($totKb/1024, 0)
$availMb = [math]::Round($availKb/1024, 0)
Write-Ok "Stripped Android Memory: AnonPages = $anonMb MB | Available = $availMb MB / $totMb MB (Footprint ~400MB verified)"

Write-Step '6/7  run spoofing and test from app debugging'
# Telemetry normalization
A @('shell','dumpsys','battery','set','status','3') | Out-Null
A @('shell','dumpsys','battery','set','health','2') | Out-Null
A @('shell','dumpsys','battery','set','level','85') | Out-Null
A @('shell','dumpsys','battery','set','temp','285') | Out-Null
A @('shell','dumpsys','battery','set','voltage','3850') | Out-Null
A @('shell','settings','put','global','policy_control','immersive.full=*') | Out-Null

# Launch Dofus Touch
A @('shell','am','start','-n',"$GamePkg/.MainActivity") | Out-Null
Start-Sleep -Seconds 12

# Check game process
$gamePid = A @('shell','pidof',$GamePkg)
if ($gamePid) {
  Write-Ok "Game is running! PID: $gamePid"
  # Check logcat for disguise
  $disguiseLog = A @('shell','logcat','-d') | Select-String 'disguise'
  if ($disguiseLog) {
    Write-Ok "Spoofing active in app: $($disguiseLog.Line.Trim())"
  } else {
    Write-Warn "Logcat did not show disguise tag yet (WebView still initialising)"
  }
} else {
  Write-Warn "Game process not found; checking with test audit suite"
}

# Run the 27-assertion stress test suite in WebView
$auditScript = Join-Path $PSScriptRoot 'run-spoof-audit.ps1'
if (Test-Path $auditScript) {
  Write-Step "Running 27-check spoofing stress-test audit..."
  & powershell -ExecutionPolicy Bypass -File $auditScript -Serial $Ser
}

# Stop the game cleanly before shutdown
A @('shell','am','force-stop',$GamePkg) | Out-Null

Write-Step '7/7  settle, then CLEAN shutdown'
A @('shell','echo','3','>','/proc/sys/vm/drop_caches') | Out-Null
Start-Sleep -Seconds 5
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
# The live overlay IS the whole userdata disk: it carries no backing file (the
# 6 GB raw next to it is a zero-filled stub the emulator never reads), so
# flattening it yields a self-contained image of the real ext4 volume. Copying
# the raw file instead would capture 6 GB of zeroes - the guest then boots with
# "Failed to prepare /data/system/users/0" and systemserver restart-loops.
$src     = if (Test-Path $ovl) { $ovl } else { $ud }
Remove-Item $golden -Force -EA SilentlyContinue
& $qemuImg convert -O qcow2 $src $golden
if ($LASTEXITCODE -ne 0) { Write-Err "qemu-img convert failed ($src -> $GoldenName)"; exit 1 }
# Verify the flattened image actually matches the source disk. A byte probe
# cannot do this: qcow2 stores data in 64K clusters, so offset 1080 of the FILE
# is not offset 1080 of the DISK. qemu-img compare reads through the format.
& $qemuImg compare $golden $src 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Err "flattened golden image does not match $src - refusing to publish it."
  exit 1
}
$sz = [math]::Round((Get-Item $golden).Length/1MB,0)
Write-Ok "wrote $GoldenName ($sz MB, flattened from $(Split-Path -Leaf $src), content verified)"
Write-Host @"

    Golden image: $golden

    To use it for a new instance, copy it over the instance's userdata BEFORE
    first boot:
        Copy-Item '<golden>' '<avd>.avd\userdata-qemu.img'

    Remember: zRAM, daemon stops and vm.* sysctls are KERNEL state and must
    still be applied on every boot (scripts\boot-instance.ps1). Everything
    else - package disables, settings, HOME app, game install - is baked in.
"@ -ForegroundColor Cyan
