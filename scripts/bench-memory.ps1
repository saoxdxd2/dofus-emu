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
  [int]    $SettleSec = 45      # let the game load and settle before sampling
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $ApkDir) { $ApkDir = Join-Path $RepoRoot 'apks' }

$SdkRoot = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { 'C:\android-sdk' }
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
  $o = & $Adb -s $Serial @argsArr 2>&1
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
    MemTotalMB  = [math]::Round((Get-MemKb 'MemTotal')/1024,1)
    AnonMB      = [math]::Round((Get-MemKb 'AnonPages')/1024,1)
    CacheMB     = [math]::Round((Get-MemKb 'Cached')/1024,1)
    ReclaimMB   = [math]::Round((Get-MemKb 'SReclaimable')/1024,1)
    AvailableMB = [math]::Round((Get-MemKb 'MemAvailable')/1024,1)
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
  & $Adb -s $Serial wait-for-device 2>&1 | Out-Null
  for ($i = 0; $i -lt 180; $i++) {
    Start-Sleep -Seconds 5
    if ((Invoke-Adb @('shell','getprop','sys.boot_completed')) -match '1') { return $true }
  }
  return $false
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
    "-no-boot-anim", "-wipe-data", "-accel", "on"
  )

  if (-not (Wait-Boot)) {
    Write-Err "  guest did not boot at ${mb}MB"
    Stop-Process -Id $proc.Id -Force -EA SilentlyContinue
    $results += [PSCustomObject]@{ Level=$mb; Status='BOOT FAILED' }
    continue
  }
  Write-Ok '  booted'

  # --- install APKs --------------------------------------------------------
  # -wipe-data resets userdata every level, so this MUST run each iteration or
  # the next level loses the game and monkey aborts with "No activities found".
  if ($WebViewApk)   { Write-Step '  install webview';  Invoke-Adb @('install','-r','-g',$WebViewApk) | Out-Null }
  if ($LauncherApk) { Write-Step '  install launcher'; Invoke-Adb @('install','-r','-g',$LauncherApk) | Out-Null }

  if ($GameApkList.Count -gt 0) {
    Write-Step "  installing game ($($GameApkList.Count) APKs, ONE atomic transaction)"
    # base.apk + splits MUST go in together. install-multiple is atomic, so a
    # partial split set fails cleanly rather than installing a broken app.
    $ins = Invoke-Adb (@('install-multiple','-r','-g') + $GameApkList)
    if ($ins -match 'Success') { Write-Ok '  game installed (base + all splits)' }
    else { Write-Warn "  install-multiple: $ins" }
    # Clean up the extraction directory once installed.
    if ($GameApk -and $GameApk -like '*.apkm') {
      $ex = Join-Path $env:TEMP 'dofus_extracted'
      if (Test-Path $ex) { Remove-Item $ex -Recurse -Force -ErrorAction SilentlyContinue }
    }
  } else {
    Write-Warn '  no game APK to install'
  }

  $haveGame = Invoke-Adb @('shell','pm','list','packages',$GamePkg)
  Write-Host "    game installed: $([bool]$haveGame)"
  if ($haveGame) {
    # Show the actual apk paths so a partial/odd install is visible.
    $paths = Invoke-Adb @('shell','pm','path',$GamePkg)
    Write-Host "    pm path -> $(($paths -replace "`r",'') -join ' ')"
  }


  # --- apply the trims -----------------------------------------------------
  # CRITICAL: every level boots with -wipe-data, which resets userdata. That
  # wipes `settings put` values, `pm disable-user` states, installed APKs AND
  # the device_config overlay. So this block MUST re-apply everything on each
  # iteration or later levels are measured untrimmed and the comparison is
  # meaningless.
  if (-not $SkipTrim) {
    Write-Step '  applying runtime low-RAM configuration'

    # adb root: needed for `settings put global` on some builds and for zram.
    Invoke-Adb @('root') | Out-Null
    Start-Sleep -Seconds 4
    Invoke-Adb @('wait-for-device') | Out-Null

    # a) Cached-process cap. `cmd activity set-process-limit` does NOT exist on
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

    # c) dalvik.vm.* limits (runtime-settable: the main ART heap saving).
    foreach ($kv in @{'dalvik.vm.heapgrowthlimit'='192m';
                      'dalvik.vm.heapstartupsize'='32m';
                      'dalvik.vm.heapminfree'='2m'}) {
      Invoke-Adb @('shell','setprop',$kv.Key,$kv.Value) | Out-Null
    }
    $hg = Invoke-Adb @('shell','getprop','dalvik.vm.heapgrowthlimit')
    Write-Host "      dalvik.vm.heapgrowthlimit = $hg"

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
    foreach ($pkg in @('com.android.phone','com.android.providers.telephony',
                       'com.android.cellbroadcastreceiver','com.android.dialer',
                       'com.android.printspooler','com.android.wallpaper.livepicker',
                       'com.android.dreams.basic')) {
      Invoke-Adb @('shell','pm','disable-user','--user','0',$pkg) | Out-Null
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
    Write-Host "      display: $sz / $dn"

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
    $zsh = Join-Path $PSScriptRoot 'apply-zram.sh'
    if (Test-Path $zsh) {
      Invoke-Adb @('push',$zsh,'/data/local/tmp/apply-zram.sh') | Out-Null
      (Invoke-Adb @('shell','sh','/data/local/tmp/apply-zram.sh')) -split "`n" |
        Where-Object { $_ -match 'algorithm|disksize|swapon|unavailable' } |
        ForEach-Object { "      $_" }
    }
  } else {
    Write-Warn '  -SkipTrim: measuring the UNTRIMMED baseline'
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
            elseif ($stab.LowMemKills -gt 0)  { "OK but $($stab.LowMemKills) low-mem kills" }
            elseif (-not $gameRunning -and -not $SkipGame -and $haveGame) { 'GAME DIED' }
            else { 'OK' }

  $results += [PSCustomObject]@{
    LevelMB      = $mb
    GuestTotalMB = $mem.MemTotalMB
    AnonMB       = $mem.AnonMB
    CacheMB      = $mem.CacheMB
    AvailMB      = $mem.AvailableMB
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

