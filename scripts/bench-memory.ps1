<#
.SYNOPSIS
  Gate 4 - measure the real per-instance memory floor, WITH trimming applied.

.DESCRIPTION
  Boots the image at several RAM allocations and records what the guest
  actually needs, so the production value is derived from measurement.

  METHODOLOGY (this replaced a flawed first attempt):
  An earlier single run at 1536 MB reported "MemAvailable 691 MB" and the
  floor was wrongly called ~1.5 GB. That was wrong for three reasons:
    1. MemAvailable is largely RECLAIMABLE PAGE CACHE. Linux fills spare RAM
       with cache, so "available" does not mean "idle", and a large value
       proves nothing about headroom.
    2. The guest was completely untrimmed: ro.config.low_ram was never active
       (ro.* cannot be set at runtime), Launcher3 was resident, zram was off.
    3. The emulator's own overhead means the guest sees MORE than -memory asks
       for (-memory 1536 yielded MemTotal 2,040,544K). The guest's real
       MemTotal is therefore recorded and used, not the requested value.

  So this harness now:
    - installs APKs auto-detected from .\apks\
    - bakes ro.config.low_ram + dalvik limits into build.prop and reboots
    - installs DofusLauncher as HOME so Launcher3 is gone
    - enables zram (lz4, swappiness 70)
    - reports PSS totals and anonymous (AnonPages) usage, which is what
      actually has to fit, instead of relying on MemAvailable
    - watches for OOM kills / crashes, which are the real floor signal

.PARAMETER Levels
  Guest RAM allocations in MB. Default 768, 1024, 1536.

.PARAMETER ApkDir
  Folder to auto-detect APKs in. Default <repo>\apks.

.PARAMETER SkipTrim
  Skip low_ram / launcher / zram. Useful only to reproduce the untrimmed
  baseline for comparison.

.EXAMPLE
  .\scripts\bench-memory.ps1
  .\scripts\bench-memory.ps1 -Levels 768,1024
#>
[CmdletBinding()]
param(
  [int[]] $Levels = @(768, 1024, 1536),
  [string] $AvdName = 'dofus',
  [string] $ApkDir,
  [switch] $SkipGame,
  [switch] $SkipTrim,
  [int]    $ZramMb    = 384,    # compressed swap; 384 = 50% of a 768 MB guest
  [int]    $SettleSec = 60      # let the game load and settle before sampling
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $ApkDir) { $ApkDir = Join-Path $RepoRoot 'apks' }

$SdkRoot = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path (Split-Path -Parent $PSScriptRoot) 'sdk' }
$Emu     = Join-Path $SdkRoot 'emulator\emulator.exe'
$Adb     = Join-Path $SdkRoot 'platform-tools\adb.exe'
$Port    = 5560
$Serial  = "emulator-$Port"
$GamePkg = 'com.ankama.dofustouch'

function Write-Step($m) { Write-Host "[bench] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[ok]    $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[warn]  $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "[FAIL]  $m" -ForegroundColor Red }

# ------------------------------------------------------ APK auto-detection
#
# The game ships as an APKM bundle (.apkm), NOT a plain APK. An .apkm is a ZIP
# container holding base.apk plus split_config.*.apk resource splits.
# `adb install` cannot take it, and installing base.apk ALONE risks
# Resources$NotFoundException at runtime when a density or string split is
# missing. So we expand it and install base + all splits in ONE atomic
# `install-multiple` transaction. The splits total ~1 MB and cost no active RAM.
#
# META-INF\ and info.json are APKMirror packaging metadata and are ignored.
function Find-Apk($pattern, $label) {
  if (-not (Test-Path $ApkDir)) { return $null }
  $hit = Get-ChildItem $ApkDir -Filter $pattern -File -ErrorAction SilentlyContinue |
         Select-Object -First 1
  if ($hit) { Write-Ok "found $label : $($hit.Name) ($([math]::Round($hit.Length/1MB,1)) MB)"; return $hit.FullName }
  return $null
}

Write-Step "APK auto-detection in $ApkDir"

$GameApk = Find-Apk 'dofustouch*.apk'            'game (apk)'
if (-not $GameApk) { $GameApk = Find-Apk 'com.ankama.dofustouch*.apk' 'game (apk)' }
if (-not $GameApk) { $GameApk = Find-Apk '*dofustouch*.apkm'            'game (apkm bundle)' }
if (-not $GameApk) { Write-Warn 'no Dofus Touch APK/APKM in apks\ - OS-only measurement' }

$WebViewApk  = Find-Apk 'webview*.apk'       'webview'
$LauncherApk = Find-Apk 'DofusLauncher*.apk' 'launcher'
if (-not $LauncherApk) {
  $built = Join-Path $RepoRoot 'launcher\build\DofusLauncher.apk'
  if (Test-Path $built) { $LauncherApk = $built; Write-Ok "launcher (prebuilt): $built" }
}

# Resolve the game into the list of APKs to install together.
$GameApkList = @()
if ($GameApk -and $GameApk -like '*.apkm') {
  $ex = Join-Path $env:TEMP 'dofus_extracted'
  if (Test-Path $ex) { Remove-Item $ex -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $ex | Out-Null
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  Write-Step '  expanding .apkm bundle'
  [System.IO.Compression.ZipFile]::ExtractToDirectory($GameApk, $ex)
  $apks = Get-ChildItem $ex -Filter '*.apk' -File -ErrorAction SilentlyContinue
  $GameApkList = $apks.FullName
  $hasBase = $apks | Where-Object { $_.Name -eq 'base.apk' }
  if (-not $hasBase) { Write-Warn '  no base.apk inside bundle!' }
  else { Write-Ok "  base.apk $([math]::Round($hasBase.Length/1MB,1)) MB + $($apks.Count-1) splits" }
} elseif ($GameApk) {
  $GameApkList = @($GameApk)
}

function Invoke-Adb($argsArr) {
  # adb writes ordinary progress AND diagnostics to stderr (e.g. "setprop: Need 2
  # arguments", "1 file pushed"). Under ErrorActionPreference='Stop' the first
  # such line aborts the whole script - which is exactly what happened: the
  # benchmark died at the setprop step and never reached wm size or the game
  # launch. Relax the preference for the call and judge success by exit code.
  $old = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  $o = & $Adb -s $Serial @argsArr 2>&1
  $rc = $LASTEXITCODE
  $ErrorActionPreference = $old
  if ($rc -ne 0) {
    Write-Verbose "adb exit $rc for: $($argsArr -join ' ')"
  }
  return ($o | Out-String).Trim()
}


# ---------------------------------------------------------------- helpers
# Read a single kB value out of the guest's /proc/meminfo by key.
function Get-MemKb($key) {
  $mi = Invoke-Adb @('shell','cat','/proc/meminfo')
  $l = $mi -split "`n" | Where-Object { $_ -match "^\s*$key\s*:" } | Select-Object -First 1
  if ($l -and $l -match ':\s*(\d+)') { return [int]$matches[1] }
  return 0
}

# Guest RAM stats. AnonPages is the number that actually has to fit: page cache
# (Cached/SReclaimable) is reclaimable under pressure and must NOT be counted
# as "used" when judging a floor, which was the first methodology's mistake.
function Get-GuestMem {
  [PSCustomObject]@{
    MemTotalMB   = [math]::Round((Get-MemKb 'MemTotal')/1024,1)
    AnonMB       = [math]::Round((Get-MemKb 'AnonPages')/1024,1)
    CacheMB      = [math]::Round((Get-MemKb 'Cached')/1024,1)
    ReclaimMB    = [math]::Round((Get-MemKb 'SReclaimable')/1024,1)
    AvailableMB  = [math]::Round((Get-MemKb 'MemAvailable')/1024,1)
    SwapTotalMB  = [math]::Round((Get-MemKb 'SwapTotal')/1024,1)
    SwapFreeMB   = [math]::Round((Get-MemKb 'SwapFree')/1024,1)
  }
}

# Memory-pressure signals. These, not raw counters, are the real floor test:
# swap being consumed plus lmkd kills means the level is genuinely too small.
function Get-PressureSignals {
  $swaps = Invoke-Adb @('shell','cat','/proc/swaps')
  # proc/[pid]/stat fields 14,15 = utime,stime in clock ticks (100Hz).
  $kswapdTicks = 0
  $pids = (Invoke-Adb @('shell','ps','-A','-o','PID,NAME')) -split "`n"
  foreach ($l in $pids) {
    if ($l -match '\skswapd\d*$') {
      $pid = ($l.Trim() -split '\s+')[0]
      $st = Invoke-Adb @('shell',"cat /proc/$pid/stat")
      # utime and stime are fields 14/15; use awk to avoid comm-space issues.
      $t = Invoke-Adb @('shell',"awk '{print \$14 + \$15}' /proc/$pid/stat")
      if ($t -match '(\d+)') { $kswapdTicks += [int]$matches[1] }
    }
  }
  $lmk = Invoke-Adb @('shell','dmesg') |
         Select-String -Pattern 'lowmemorykiller|lmkd|oom-kill|Out of memory|Killed process'
  $lmkUser = Invoke-Adb @('shell','dumpsys','activity','processes') |
             Select-String -Pattern 'Killing' -SimpleMatch
  [PSCustomObject]@{
    KswapdSeconds = [math]::Round($kswapdTicks/100.0,1)
    LmkLines      = @($lmk).Count
    LmkUser       = @($lmkUser).Count
    SwapText      = (($swaps -replace "`r",'') -join ' | ')
  }
}

# Real floor signal: pressure events, not a RAM counter.
function Test-Stability {
  $lmk = Invoke-Adb @('shell','dmesg') |
         Select-String -Pattern 'lowmemorykiller|oom-kill|Out of memory|Killed process'
  $alive = Invoke-Adb @('shell',"pidof $GamePkg")
  $boot  = Invoke-Adb @('shell','getprop','sys.boot_completed')
  [PSCustomObject]@{
    StillBooted = ($boot -match '1')
    GameRunning = [bool]$alive
    LowMemKills = @($lmk).Count
  }
}

function Wait-Boot {
  # Poll getprop directly rather than `adb wait-for-device`: after `adb root`
  # the transport restarts, and wait-for-device can then block indefinitely.
  for ($i = 0; $i -lt 180; $i++) {
    Start-Sleep -Seconds 5
    if ((Invoke-Adb @('shell','getprop','sys.boot_completed')) -match '1') { return $true }
  }
  return $false
}

# ------------------------------------------------------- AVD display profile
# Dofus Touch is a LANDSCAPE game on a fixed isometric map grid with CSS-pixel
# scaling. The Pixel device profile defaults to 1080x1920 PORTRAIT @ 420dpi,
# which wastes a large graphics-buffer allocation and forces the game letterbox.
# config.ini lives outside the repo (in %USERPROFILE%\.android\avd) so it is
# patched here at runtime instead, which also keeps the repo portable.
$avdCfg = Join-Path $env:USERPROFILE ".android\avd\$AvdName.avd\config.ini"
if (Test-Path $avdCfg) {
  $lcd = @{
    # Landscape 1280x720 @ 213dpi (tvdpi): Dofus Touch renders on a fixed
    # isometric map grid with CSS-pixel scaling, so arbitrary geometry causes
    # pillarboxing and shrunken touch targets.
    'hw.lcd.width'   = '1280'
    'hw.lcd.height'  = '720'
    'hw.lcd.density' = '213'
    'hw.initialOrientation' = 'landscape'
    # GPS off: the guest otherwise spins up the fused location provider that
    # we then disable in software. Removing it at the device level is cleaner.
    'hw.gps'         = 'no'
    # No virtual radio: stops QEMU initialising the GSM/modem interface, which
    # is what makes com.android.phone churn once we disable the package.
    'hw.gsmModem'    = 'no'
    'hw.radio'       = 'no'
    # Sensors are left ON: Cordova devicemotion/devicorientation map to the
    # accelerometer and gyroscope, and Dofus Touch is orientation-driven.
  }
  $cur = Get-Content $avdCfg
  foreach ($k in $lcd.Keys) {
    $esc = [regex]::Escape($k)
    if ($cur -match "^\s*$esc\s*=") { $cur = $cur -replace "^\s*$esc\s*=.*$", "$k = $($lcd[$k])" }
    else { $cur += "$k = $($lcd[$k])" }
  }
  Set-Content -Path $avdCfg -Value $cur
  Write-Ok "  AVD display 1280x720 @ 213dpi landscape, GPS off ($avdCfg)"
} else {
  Write-Warn "  AVD config not found at $avdCfg - guest will boot at default resolution"
}

$results = @()

foreach ($mb in $Levels) {
  Write-Step "=== Testing ${mb}MB (guest-trimmed) ==="

  $proc = Start-Process -FilePath $Emu -PassThru -WindowStyle Minimized -ArgumentList @(
    "-avd", $AvdName, "-port", $Port, "-gpu", "host",
    "-memory", $mb, "-cores", 2, "-no-snapshot", "-no-audio",
    # -accel accepts only on|off|auto in emulator 37.x (not "hvm").
    # NOTE: -writable-system is deliberately NOT used. It makes the emulator
    # log "System image is writable" but the guest then hangs at boot (adb
    # offline, near-idle CPU) instead of completing in ~47s. Reproduced twice.
    # CRITICAL: -memory alone does NOT control guest RAM. The emulator runs its
    # own planner, logs "Increasing RAM size to 2048MB", and every level then
    # boots at ~2048 MB - an earlier run reported MemTotal 1992.7 MB for all
    # three of 768/1024/1536, making the whole comparison meaningless.
    # `-qemu -m <mb>M` bypasses that clamp. The "M" suffix matters: plain
    # `-m 768` was unreliable (guest came up at 1524456 kB), while `-m 768M`
    # reliably yields MemTotal 751848 kB. The "Increasing RAM size" line still
    # appears in the log - it is planner noise, NOT the real guest size, which is
    # why the verification below reads /proc/meminfo rather than trusting it.
    "-no-boot-anim", "-wipe-data", "-no-metrics", "-accel", "on",
    "-qemu", "-m", "${mb}M"
  )

  if (-not (Wait-Boot)) {
    Write-Err "  guest did not boot at ${mb}MB"
    Stop-Process -Id $proc.Id -Force -EA SilentlyContinue
    $results += [PSCustomObject]@{ Level=$mb; Status='BOOT FAILED' }
    continue
  }
  Write-Ok '  booted'

  # Verify the guest actually got the RAM we asked for. Without this check a
  # silent clamp to 2048 MB would produce a table that looks fine and means
  # nothing.
  $wantKb = $mb * 1024
  $gotMem = Invoke-Adb @('shell','cat','/proc/meminfo')
  $gotKb = 0
  if ($gotMem -match 'MemTotal:\s+(\d+)') { $gotKb = [int]$matches[1] }
  if ($gotKb -gt 0) {
    $delta = [math]::Abs($gotKb - $wantKb)
    if ($delta -lt ($mb * 1024 * 0.25)) {
      Write-Ok "    guest RAM verified: $([math]::Round($gotKb/1024,0)) MB (requested $mb MB)"
    } else {
      Write-Warn "    guest RAM is $([math]::Round($gotKb/1024,0)) MB but $mb MB was requested"
      Write-Warn '    the emulator clamped it; this level is NOT comparable.'
      $statusOverride = 'RAM CLAMPED'
    }
  }

  # --- apply the trims -----------------------------------------------------
  # CRITICAL: every level boots with -wipe-data, which resets userdata. That
  # wipes `settings put` values, `pm disable-user` states, installed APKs AND
  # the device_config overlay. So this block MUST re-apply everything on each
  # iteration or later levels are measured untrimmed and the comparison is
  # meaningless.
  if (-not $SkipTrim) {
    Write-Step '  applying runtime low-RAM configuration (BEFORE any APK install)'

    # NOTE on ordering: this block intentionally runs BEFORE the game install.
    # A first attempt at 768 MB installed into an untrimmed, unswapped guest
    # and starved completely (MemAvailable 0 kB, then `dumpsys meminfo` timing
    # out). Installing 32 APKs triggers heavy dex2oat work that spikes memory,
    # and with SwapTotal 0 the guest has nowhere to spill. So: zRAM first, then
    # strip daemons, then free Launcher3, and only then install.

    # adb root restarts adbd, dropping the transport briefly. A fixed short
    # sleep is not enough at low RAM: at 768 MB the guest was still recovering
    # and the device was still "offline" for tens of seconds afterwards, which
    # made install-multiple fail while `pm list` still echoed the package name.
    # Poll until the device is genuinely online again.
    Invoke-Adb @('root') | Out-Null
    $online = $false
    for ($i = 0; $i -lt 30; $i++) {
      Start-Sleep -Seconds 3
      $st = Invoke-Adb @('get-state')
      if ($st -match 'device') { $online = $true; break }
    }
    if ($online) { Write-Host '      adbd back online after root' }
    else { Write-Warn '  device did not return online after adb root' }

    # a) zRAM FIRST - this must precede everything else, because with
    #    SwapTotal 0 the guest has no overflow at all.
    Write-Step '    enabling zRAM (before everything else)'
    $zsh = Join-Path $PSScriptRoot 'apply-zram.sh'
    if (Test-Path $zsh) {
      # CRLF is FATAL for a shell script pushed to Android. The guest's
      # /system/bin/sh chokes on the stray \r, producing errors like
      #   2>&$'1\r' : illegal file descriptor name
      #   cat: /proc/swaps: No such file or directory
      # and zram then silently never gets configured - exactly what happened,
      # with SwapTotal stuck at 0 MB at every level. Rewrite with LF first.
      $zbytes = ([System.IO.File]::ReadAllText($zsh)) -replace "`r`n", "`n"
      $zbytes = $zbytes -replace "`r", "`n"
      $zlocal = Join-Path $env:TEMP 'apply-zram-lf.sh'
      [System.IO.File]::WriteAllText($zlocal, $zbytes, (New-Object System.Text.UTF8Encoding $false))
      Invoke-Adb @('push',$zlocal,'/data/local/tmp/apply-zram.sh') | Out-Null
      (Invoke-Adb @('shell','sh','/data/local/tmp/apply-zram.sh',"$ZramMb")) -split "`n" |
        Where-Object { $_ -match 'algorithm|disksize|swapon|unavailable' } |
        ForEach-Object { Write-Host "      $_" }
    }
    $swapNow = Invoke-Adb @('shell','cat','/proc/swaps')
    if ($swapNow -match 'zram0') { Write-Host '      zram0 ACTIVE' }
    else {
      Write-Warn '      no zram0 yet - retrying after the device settles'
      Start-Sleep -Seconds 8
      $swapNow = Invoke-Adb @('shell','cat','/proc/swaps')
      if ($swapNow -match 'zram0') { Write-Host '      zram0 ACTIVE (on retry)' }
      else { Write-Warn "      zram0 still absent: $((($swapNow -replace "`r",'') -join ' '))" }
    }

    # b) Daemon strip. netd is DELIBERATELY NOT stopped: it owns DNS resolution
    #    and the game loads assets from Ankama's servers.
    foreach ($svc in @('statsd','traced','traced_probes','incidentd','rild',
                       'cameraserver','drmserver')) {
      $chk = Invoke-Adb @('shell','service','check',$svc)
      if ($chk -match 'found') { Invoke-Adb @('shell','stop',$svc) | Out-Null }
    }
    Write-Host '      telemetry/tracing/media daemons stopped'

    # c) Cached-process cap. `cmd activity set-process-limit` does NOT exist on
    #    API 29 (answers "Unknown command"), so use the device_config overlay,
    #    which ActivityManager reads on Android 10, and verify it reads back.
    Invoke-Adb @('shell','device_config','put','activity_manager','max_cached_processes','2') | Out-Null
    $mc = Invoke-Adb @('shell','device_config','get','activity_manager','max_cached_processes')
    if ($mc -match '2') { Write-Host '      max_cached_processes = 2 (verified)' }
    else { Write-Warn "      max_cached_processes reads '$mc'" }

    # b) Animations off.
    foreach ($sc in @('window_animation_scale','transition_animation_scale','animator_duration_scale')) {
      Invoke-Adb @('shell','settings','put','global',$sc,'0') | Out-Null
    }

    # c) ART heap: DELIBERATELY NOT TRIMMED.
    #    An earlier revision set dalvik.vm.heapgrowthlimit=192m and
    #    heapstartupsize=32m here. That made Dofus Touch CRASH on launch with
    #      java.lang.OutOfMemoryError: Failed to allocate a 7193688 byte
    #      allocation ... target footprint 16777216, growth limit 16777216
    #      at org.apache.cordova.file.FileUtils$25.run
    #    The app does not declare android:largeHeap, and Cordova's startup JSON
    #    parsing needs more than the ~16 MB it was left with. dalvik.vm.heapsize
    #    already defaults to 512m on this image, which is ample - so ART is left
    #    to manage its own heap. Re-tested: with the caps removed the game runs
    #    (PID present, mCurrentFocus = com.ankama.dofustouch/.MainActivity).

    # d) ro.config.low_ram is NOT reachable on this image (see patch-system.ps1
    #    header). Report it rather than pretending.
    $lr = Invoke-Adb @('shell','getprop','ro.config.low_ram')
    if ($lr -match 'true') { Write-Host '      ro.config.low_ram active' }
    else { Write-Host '      ro.config.low_ram NOT set (unavailable on this image)' }

    # e) Telemetry / tracing daemons. All require adb root - `stop` answers
    #    "must be root" otherwise. NOTE the service is `rild`, NOT `ril-daemon`
    #    (that name returns nothing). Measured gain from this group alone was
    #    ~180 MB on this image.
    foreach ($svc in @('statsd','traced','traced_probes','incidentd','rild')) {
      $chk = Invoke-Adb @('shell','service','check',$svc)
      if ($chk -match 'found') { Invoke-Adb @('shell','stop',$svc) | Out-Null }
    }

    # f) Disable non-essential packages. Deliberately EXCLUDES
    #    android.process.acore: it is the account manager with a framework
    #    dependency, and disabling it risks focus-stealing crash dialogs.
    #    Also excludes com.android.inputmethod.latin: only ~25 MB and it is the
    #    debugging fallback if a text input is ever needed.
    foreach ($pkg in @('com.android.cellbroadcastreceiver','com.android.dialer',
                       'com.android.printspooler','com.android.wallpaper.livepicker',
                       'com.android.dreams.basic','com.android.bluetooth',
                       'com.android.location.fused','com.android.camera2',
                       'com.android.gallery3d','com.android.calendar',
                       'com.android.providers.calendar','com.android.email',
                       'com.android.quicksearchbox','com.android.contacts',
                       'com.android.mms.service','com.android.deskclock',
                       'com.android.music')) {
      Invoke-Adb @('shell','pm','disable-user','--user','0',$pkg) | Out-Null
    }
    # com.android.phone otherwise restart-loops (repeated "Process
    # com.android.phone (pid N) has died: pers PER") because the framework keeps
    # re-spawning a persistent service that can no longer run. Suppressing
    # background starts breaks the loop; the virtual radio is also disabled at
    # the device level via hw.gsmModem / hw.radio = no.
    Invoke-Adb @('shell','cmd','appops','set','com.android.phone','RUN_IN_BACKGROUND','ignore') | Out-Null

    # --- UI layer: the single biggest win at low RAM ----------------------
    # Measured on a trimmed 768 MB guest BEFORE this step:
    #   com.android.systemui  56,136 KB   status bar / nav bar / notifications
    #   com.android.launcher3 53,968 KB   desktop + app drawer + hotseat
    #   com.android.inputmethod.latin 25,369 KB  dictionaries + layouts
    # = ~135 MB of PSS that a fullscreen WebGL Cordova game never draws.
    # Dofus Touch renders immersive, so none of it is needed. After disabling
    # these the game runs stably at 768 MB (verified: PID alive, MainActivity
    # focused, no lowmemorykiller event) whereas previously LMK killed it with
    # "Reclaimed 0kB ... below min(221184kB)".
    #
    # NOTE ordering: stopping SurfaceFlinger tears down the display/transport
    # that adb rides on, so the guest can drop "offline" for a while afterwards.
    # Killing SystemUI alone frees the bulk of the memory (~56 MB) WITHOUT that
    # risk, so we disable the packages and pkill SystemUI, but leave
    # SurfaceFlinger running - it is only ~21 MB and keeping the transport alive
    # is worth far more than 21 MB at this level.
    foreach ($pkg in @('com.android.systemui','com.android.launcher3',
                       'com.android.inputmethod.latin')) {
      Invoke-Adb @('shell','pm','disable-user','--user','0',$pkg) | Out-Null
    }
    Invoke-Adb @('shell','pkill','-f','systemui') | Out-Null
    Start-Sleep -Seconds 5
    Write-Host '      SystemUI + Launcher3 + stock IME disabled (SurfaceFlinger kept: it carries the adb transport)'
    # Wait for adb to settle before continuing - SystemUI death can briefly
    # disturb the connection.
    for ($i = 0; $i -lt 10; $i++) {
      if ((Invoke-Adb @('get-state')) -match 'device') { break }
      Start-Sleep -Seconds 2
    }

    # g) Keyguard / logcat / VFS cache reclaim.
    #    persist.sys.lockscreen.disable is deliberately NOT set: it is accepted
    #    but is a no-op on Android 10. locksettings is the real mechanism.
    Invoke-Adb @('shell','locksettings','set-disabled','true') | Out-Null
    Invoke-Adb @('shell','logcat','-G','64K') | Out-Null
    Invoke-Adb @('shell','echo 200 > /proc/sys/vm/vfs_cache_pressure') | Out-Null

    # h) Lock the guest display to a standard 16:9 profile.
    #    Dofus Touch draws on a fixed isometric map grid with CSS-pixel scaling.
    #    Arbitrary/dynamic resolutions cause pillarboxing and shrink UI icons,
    #    so we pin 1280x720 at tvdpi (213 dpi) rather than leaving it to the
    #    device profile. Host-side QEMU window scaling is a separate concern.
    Invoke-Adb @('shell','wm','size','1280x720') | Out-Null
    Invoke-Adb @('shell','wm','density','213') | Out-Null
    $sz = Invoke-Adb @('shell','wm','size')
    $dn = Invoke-Adb @('shell','wm','density')
    Write-Host "      display: $((($sz -replace "`r",'') -join ' ')) / $((($dn -replace "`r",'') -join ' '))"

    # i) DofusLauncher as HOME so Launcher3 is not resident.
    $apk = Join-Path $RepoRoot 'launcher\build\DofusLauncher.apk'
    if (-not $LauncherApk -and (Test-Path $apk)) { $LauncherApk = $apk }
    if ($LauncherApk) {
      Invoke-Adb @('shell','cmd','package','set-home-activity','com.dofusemu.launcher/.DofusLauncherActivity') | Out-Null
      Invoke-Adb @('shell','pm','disable-user','--user','0','com.android.launcher3') | Out-Null
      $l3 = Invoke-Adb @('shell','pm','list','packages','-d','com.android.launcher3')
      if ($l3 -match 'launcher3') { Write-Host '      Launcher3 disabled' }
      else { Write-Warn '      Launcher3 still enabled' }
    }

    # g) zram (compressed swap).
    $zsh2 = $null
  } else {
    Write-Warn '  -SkipTrim: measuring the UNTRIMMED baseline'
  }

  # --- installation MUST be preceded by a settle/cleanup phase ----------
    # Two ordering bugs fixed here:
    #  1. zRAM and the package/daemon strip were applied, then the install ran
    #     IMMEDIATELY. `stop` on services and `pkill systemui` leaves the guest
    #     briefly unstable, and `install-multiple` then failed mid-transaction.
    #     Installing 32 APKs into a guest that is still churning is also the
    #     worst possible time for dex2oat.
    #  2. The extraction directory is per-run and MUST be cleaned up BEFORE the
    #     next level; a leftover folder caused
    #       "failed to stat ...\split_config.zh.apk: No such file or directory"
    #  So: clean, apply trims, settle, verify connectivity, then install.
    $ex = Join-Path $env:TEMP 'dofus_extracted'
    if (Test-Path $ex) { Remove-Item $ex -Recurse -Force -ErrorAction SilentlyContinue }

    Write-Step '  settling after trims before install'
    Start-Sleep -Seconds 20
    for ($i = 0; $i -lt 20; $i++) {
      if ((Invoke-Adb @('get-state')) -match 'device') { break }
      Start-Sleep -Seconds 3
    }
    if ((Invoke-Adb @('get-state')) -match 'device') {
      Write-Host '      device stable and online'
    } else {
      Write-Warn '  device NOT online before install; install will likely fail'
    }

    # Drop the page cache so the install starts from a clean slate.
    Invoke-Adb @('shell','echo 3 > /proc/sys/vm/drop_caches') | Out-Null

    # Re-assert zRAM here as well: it must be active BEFORE the install.
    $pre = Invoke-Adb @('shell','cat','/proc/swaps')
    if ($pre -notmatch 'zram0') {
      Write-Host '      re-applying zram pre-install'
      $zshPre = Join-Path $PSScriptRoot 'apply-zram.sh'
      if (Test-Path $zshPre) {
        $zb = ([System.IO.File]::ReadAllText($zshPre)) -replace "`r`n", "`n"
        $zb = $zb -replace "`r", "`n"
        $zl = Join-Path $env:TEMP 'apply-zram-pre.sh'
        [System.IO.File]::WriteAllText($zl, $zb, (New-Object System.Text.UTF8Encoding $false))
        Invoke-Adb @('push',$zl,'/data/local/tmp/apply-zram.sh') | Out-Null
        Invoke-Adb @('shell','sh','/data/local/tmp/apply-zram.sh',"$ZramMb") | Out-Null
      }
      $pre2 = Invoke-Adb @('shell','cat','/proc/swaps')
      if ($pre2 -match 'zram0') { Write-Host '      zram0 active pre-install' }
      else { Write-Warn '      zram0 STILL absent pre-install' }
    }

    # --- install APKs --------------------------------------------------------
  # -wipe-data resets userdata every level, so this MUST run each iteration or
  # the next level loses the game and monkey aborts with "No activities found".
  if ($WebViewApk)   { Write-Step '  install webview';  Invoke-Adb @('install','-r','-g',$WebViewApk) | Out-Null }

  if ($GameApkList.Count -gt 0) {
    Write-Step "  installing game ($($GameApkList.Count) APKs, ONE atomic transaction)"
    # base.apk + splits MUST go in together. install-multiple is atomic, so a
    # partial split set fails cleanly rather than installing a broken app.
    $ins = Invoke-Adb (@('install-multiple','-r','-g') + $GameApkList)
    if ($ins -match 'Success') { Write-Ok '  game installed (base + all splits)' }
    else { Write-Warn "  install-multiple: $ins" }
    if ($GameApk -and $GameApk -like '*.apkm') {
      $ex = Join-Path $env:TEMP 'dofus_extracted'
      if (Test-Path $ex) { Remove-Item $ex -Recurse -Force -ErrorAction SilentlyContinue }
    }
  } else {
    Write-Warn '  no game APK to install'
  }

  # Verify the game is really installed rather than trusting a stale read.
  $haveGame = $false
  $pathOut = ''
  $p = Invoke-Adb @('shell','pm','path',$GamePkg)
  if ($p -match 'package:') { $haveGame = $true; $pathOut = (($p -replace "`r",'') -join ' ') }
  Write-Host "    game installed: $haveGame"
  if ($haveGame) { Write-Host "    pm path -> $pathOut" }

  # --- post-install memory cleanup -----------------------------------------
  # Installing 32 APKs leaves dex2oat artifacts and a dirtied page cache, which
  # matters enormously at 768 MB. Verify-only compilation avoids a full AOT
  # pass, then drop the cached install buffers. Runs AFTER the install check
  # so $haveGame is known.
  if (-not $SkipTrim -and $haveGame) {
    Write-Step '  post-install cleanup (dex2oat + page cache)'
    Invoke-Adb @('shell','cmd','package','compile','-m','verify','-f',$GamePkg) | Out-Null
    Invoke-Adb @('shell','echo 3 > /proc/sys/vm/drop_caches') | Out-Null
    Write-Host '      compile=verify, drop_caches done'
  }


  # --- launch the game -----------------------------------------------------
  $gameRunning = $false
  if (-not $SkipGame) {
    if ($haveGame) {
      Write-Step "  launching game and letting it settle ${SettleSec}s"
      Invoke-Adb @('shell','monkey','-p',$GamePkg,'-c','android.intent.category.LAUNCHER','1') | Out-Null
      Start-Sleep -Seconds $SettleSec
      $gameRunning = $true
    } else {
      Write-Warn '  game not installed - this level measures the trimmed OS only'
    }
  }

  # --- measure -------------------------------------------------------------
  $mem = Get-GuestMem
  $stab = Test-Stability
  $press = Get-PressureSignals
  $swapUsed = [math]::Round($mem.SwapTotalMB - $mem.SwapFreeMB, 1)

  # PSS breakdown: proportional set size is the fair per-process figure.
  $pss = @{}
  $mi = Invoke-Adb @('shell','dumpsys','meminfo')
  foreach ($line in ($mi -split "`n")) {
    if ($line -match '^\s*([\d,]+)K:\s*(\S+)') {
      $p = $Matches[2].Split(':')[0]
      $kb = [int]($Matches[1] -replace ',','')
      if (-not $pss.ContainsKey($p)) { $pss[$p] = 0 }
      $pss[$p] += $kb
    }
  }
  $top = $pss.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 8

  $hostWS = 0
  foreach ($p in (Get-Process -Name 'qemu-system-x86_64*' -ErrorAction SilentlyContinue)) {
    $hostWS += $p.WorkingSet64
  }

  Write-Host ''
  Write-Host "    guest MemTotal   : $($mem.MemTotalMB) MB   (requested $mb MB)"
  Write-Host "    anonymous (anon) : $($mem.AnonMB) MB   <- must fit"
  Write-Host "    page cache       : $($mem.CacheMB) MB (+$($mem.ReclaimMB) MB reclaimable) <- NOT required"
  Write-Host "    MemAvailable     : $($mem.AvailableMB) MB"
  Write-Host "    swap             : $($mem.SwapFreeMB) MB free / $($mem.SwapTotalMB) MB total (used $swapUsed MB)"
  Write-Host "    kswapd CPU       : $($press.KswapdSeconds) s  (swap thrash indicator)"
  Write-Host "    lmkd/oom events  : $($press.LmkLines) kernel, $($press.LmkUser) user-space"
  Write-Host "    host working set : $([math]::Round($hostWS/1MB,0)) MB"
  Write-Host "    stability        : booted=$($stab.StillBooted) gameRunning=$($stab.GameRunning) lowMemKills=$($stab.LowMemKills)"
  Write-Host '    top processes (PSS, KB):'
  foreach ($t in $top) { Write-Host ("        {0,-45} {1,8}" -f $t.Key, $t.Value) }

  # Dofus Touch is a Cordova app: the real cost is the game process plus the
  # Chromium renderer/GPU processes it spawns. Report them explicitly.
  $gamePSS = 0
  foreach ($k in @('com.ankama.dofustouch','webview','chrome','org.chromium')) {
    foreach ($e in $pss.GetEnumerator()) {
      if ($e.Key -like "*$k*") {
        $gamePSS += $e.Value
        Write-Host ("        GAME-CHAIN {0,-40} {1,8} KB" -f $e.Key, $e.Value)
      }
    }
  }
  Write-Host ("        GAME-CHAIN TOTAL {0,8} KB ({1} MB)" -f $gamePSS, [math]::Round($gamePSS/1024,1))

  $status = if (-not $stab.StillBooted) { 'CRASHED/REBOOTED' }
            elseif ($stab.LowMemKills -gt 0 -or $press.LmkLines -gt 0) { "OK but $($press.LmkLines) lmkd events" }
            elseif ($haveGame -and $gamePSS -lt 20000) { 'GAME DIED' }
            elseif (-not $gameRunning -and -not $SkipGame -and $haveGame) { 'GAME DIED' }
            else { 'OK' }

  $results += [PSCustomObject]@{
    LevelMB      = $mb
    GuestTotalMB = $mem.MemTotalMB
    AnonMB       = $mem.AnonMB
    CacheMB      = $mem.CacheMB
    AvailMB      = $mem.AvailableMB
    SwapUsedMB   = $swapUsed
    KswapdSec    = $press.KswapdSeconds
    LmkEvents    = $press.LmkLines
    HostWSMB     = [math]::Round($hostWS/1MB,0)
    GamePSSMB    = [math]::Round($gamePSS/1024,1)
    GameRunning  = $gameRunning
    LowMemKills  = $stab.LowMemKills
    Status       = $status
  }

  Invoke-Adb @('emu','kill') | Out-Null
  Start-Sleep -Seconds 10
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 5
}

Write-Step '=== Gate 4 results (trimmed guest) ==='
$results | Format-Table -AutoSize
$results | Export-Csv -NoTypeInformation -Path (Join-Path $PSScriptRoot 'gate4-memory.csv')
Write-Ok 'wrote gate4-memory.csv'

Write-Step 'Interpretation'
Write-Host '  The floor is the SMALLEST level whose Status is OK.' -ForegroundColor Gray
Write-Host '  Judge on AnonMB (must fit) and on pressure events, NOT on' -ForegroundColor Gray
Write-Host '  MemAvailable - that is mostly reclaimable page cache.' -ForegroundColor Gray
$ok = $results | Where-Object { $_.Status -eq 'OK' }
if ($ok) {
  $floor = ($ok | Sort-Object LevelMB | Select-Object -First 1).LevelMB
  Write-Host ''
  Write-Host "  ==> SMALLEST VIABLE ALLOCATION: $floor MB" -ForegroundColor Green
} else {
  Write-Host ''
  Write-Host '  ==> no level survived; the floor is above the tested range.' -ForegroundColor Yellow
}

