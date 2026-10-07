<#
.SYNOPSIS
  Per-boot runtime pass - the small amount the golden image CANNOT capture.

.DESCRIPTION
  A captured userdata-golden.img bakes in everything stored under /data:
  package disables, settings, appops, device_config, the HOME app, installed
  APKs and dexopt artifacts. Those never need re-applying.

  Three things are NOT in /data and are therefore re-applied on every boot:

  1. zRAM and the vm.* sysctls   - kernel runtime state, gone on reboot
    2. init service stops           - statsd/traced/rild/cameraserver/drmserver
                                       are started by init before userdata is read
    3. anything under /sys

  The per-boot CPU budget (telephony background loop, audio, Chromium GPU
  rasterization flags, native-ABI guard) is folded in here too, so one call
  leaves an instance in its production state.

  This script is the whole per-boot cost: a handful of adb calls, a couple of
  seconds. Run it right after sys.boot_completed=1.

.PARAMETER Serials
  adb serials. Defaults to every online emulator.

.PARAMETER ZramMb
  zRAM size. Default 512 - measured to be roughly saturated at 768 MB of guest
  RAM and comfortable at 1024 MB.

.PARAMETER Swappiness
  vm.swappiness. Default 85 - aggressive enough that cold pages land in zRAM
  rather than forcing the guest to compress/uncompress in the foreground.

.EXAMPLE
  .\scripts\boot-instance.ps1 -Serials emulator-5554
#>
[CmdletBinding()]
param(
  [string[]] $Serials,
  [int]      $ZramMb     = 512,
  [int]      $Swappiness = 85
)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Adb      = Join-Path $SdkRoot 'platform-tools\adb.exe'

function Write-Ok($m)   { Write-Host "    [ok]   $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "    [warn] $m" -ForegroundColor Yellow }

if (-not $Serials) {
  $Serials = (& $Adb devices) | Select-String '^emulator-\d+\s+device$' |
             ForEach-Object { ($_ -split '\s+')[0] }
}
if (-not $Serials) { Write-Warn 'no online emulator instances'; exit 1 }

# netd owns DNS and the game loads assets from Ankama's servers - stopping it
# breaks the network, so it is deliberately excluded.
# NOTE: audioserver is deliberately kept alive so Cordova/WebAudio AudioTrack does NOT hang on Binder.
$DAEMONS = @('statsd','traced','traced_probes','incidentd','rild','cameraserver','drmserver','wpa_supplicant','hostapd_nohidl','mediadrmserver')

foreach ($s in $Serials) {
  function A($x) { (& $Adb -s $s @x 2>&1 | Out-String).Trim() }

  $boot = A @('shell','getprop','sys.boot_completed')
  if ($boot -notmatch '1') { Write-Warn "$s not booted"; continue }

  Write-Host "`n==> $s : kernel/runtime pass" -ForegroundColor Cyan

  A @('root') | Out-Null
  for ($i=0; $i -lt 25; $i++) { Start-Sleep 2; if ((A @('get-state')) -match 'device') { break } }

  # --- 1. zRAM (kernel state - cannot live in the golden image) -----------
  $zsh = Join-Path $PSScriptRoot 'apply-zram.sh'
  if (Test-Path $zsh) {
    # CRLF is fatal for a shell script pushed to Android: the guest's sh chokes
    # on the stray \r and zram silently never configures.
    $zb = ([System.IO.File]::ReadAllText($zsh)) -replace "`r`n","`n"
    $zb = $zb -replace "`r","`n"
    $zl = Join-Path $env:TEMP 'apply-zram-boot.sh'
    [System.IO.File]::WriteAllText($zl, $zb, (New-Object System.Text.UTF8Encoding $false))
    A @('push',$zl,'/data/local/tmp/apply-zram.sh') | Out-Null
    A @('shell','sh','/data/local/tmp/apply-zram.sh',"$ZramMb") | Out-Null
  }
  if ((A @('shell','cat','/proc/swaps')) -match 'zram0') { Write-Ok 'zram0 active' }
  else { Write-Warn 'zram0 NOT active' }

  # --- 2. vm sysctls ------------------------------------------------------
  foreach ($kv in @(@('vm/swappiness',$Swappiness),
                    @('vm/page-cluster',0),
                    @('vm/vfs_cache_pressure',200))) {
    A @('shell',"echo $($kv[1]) > /proc/sys/$($kv[0])") | Out-Null
  }
  A @('shell','setprop','dalvik.vm.heapgrowthlimit','128m') | Out-Null
  A @('shell','setprop','dalvik.vm.heapsize','256m') | Out-Null
  $sw = A @('shell','cat','/proc/sys/vm/swappiness')
  if ($sw -match "$Swappiness") { Write-Ok "vm sysctls set (swappiness=$sw page-cluster=0 vfs_cache_pressure=200)" }
  else { Write-Warn "swappiness reads '$sw'" }

  # --- 3. init daemons ----------------------------------------------------
  # traced / traced_probes are kernel-tracing daemons: they are pure overhead
  # for a game workload and were measurably the largest source of guest `sys`
  # time. rild restarts telephony in a loop once the radio is off.
  $stopped = 0
  foreach ($d in $DAEMONS) {
    if ((A @('shell','service','check',$d)) -match 'found') { A @('shell','stop',$d) | Out-Null; $stopped++ }
  }
  Write-Ok "$stopped/$($DAEMONS.Count) daemons stopped"

  # --- 4. CPU budget (merged from cpu-budget.ps1) -------------------------
  # Silence the telephony background loop so ActivityManager does not keep
  # waking the disabled com.android.phone package.
  A @('shell','cmd','appops','set','com.android.phone','RUN_IN_BACKGROUND','ignore') | Out-Null

  # Silence kernel ftrace to eliminate background kernel tracing CPU spikes
  A @('shell','echo 0 > /sys/kernel/tracing/tracing_on 2>/dev/null; echo 0 > /sys/kernel/debug/tracing/tracing_on 2>/dev/null') | Out-Null

  # Cap logd memory buffer to 16K to reclaim guest RAM and stop compaction CPU
  A @('shell','logcat','-G','16K') | Out-Null
  A @('shell','logcat','-c') | Out-Null

  # Chromium Single-Core Worker Tuning & Ultra-Lean Memory Allocation:
  $wvFlags = "_ --ignore-gpu-blocklist --enable-gpu-rasterization --num-raster-threads=1 --disable-background-timer-throttling --renderer-process-limit=1 --disable-smooth-scrolling --disable-speech-api --disable-breakpad --no-pings --force-gpu-mem-available-mb=256"
  A @('shell',"echo '$wvFlags' > /data/local/tmp/webview-command-line") | Out-Null
  A @('shell','chmod','644','/data/local/tmp/webview-command-line') | Out-Null
  $wcl = (A @('shell','cat','/data/local/tmp/webview-command-line'))
  if ($wcl -match 'num-raster-threads') { Write-Ok 'webview single-core worker & font stability flags asserted' }
  else { Write-Warn 'could not write /data/local/tmp/webview-command-line' }

  # Single-core kernel scheduler optimization (reduces preemption latency)
  A @('shell','echo 10000000 > /proc/sys/kernel/sched_latency_ns 2>/dev/null') | Out-Null
  A @('shell','echo 2000000 > /proc/sys/kernel/sched_min_granularity_ns 2>/dev/null') | Out-Null
  A @('shell','echo 2500000 > /proc/sys/kernel/sched_wakeup_granularity_ns 2>/dev/null') | Out-Null

  # ABI guard: a regression to an ARM translation layer would reintroduce a
  # large, silent CPU cost, so assert the native x86_64 oat dir every boot.
  $oat = (A @('shell','ls /data/app/com.ankama.dofustouch*/oat/ 2>/dev/null')).Trim()
  if ($oat -match 'x86_64') { Write-Ok "game ABI native x86_64 (oat: $oat)" }
  # --- 5. subsystem normalization (identity, battery & telephony) ---------
  $instIdx = if ($s -match 'emulator-(\d+)') { [int]([math]::Floor(([int]$matches[1] - 5554) / 2) + 1) } else { 1 }
  $instAvd = 'dofus-{0:d2}' -f $instIdx
  $identFile = Join-Path $env:USERPROFILE ".android\avd\$instAvd.avd\identity.json"
  if (Test-Path $identFile) {
    try {
      $idObj = Get-Content $identFile -Raw | ConvertFrom-Json
      if ($idObj.AndroidId) {
        A @('shell','settings','put','secure','android_id', $idObj.AndroidId) | Out-Null
        Write-Ok "android_id asserted ($($idObj.AndroidId))"
      }
    } catch {}
  }

  A @('shell','dumpsys battery set status 3; dumpsys battery set level 85; dumpsys battery set temp 285') | Out-Null
  Write-Ok 'battery telemetry normalized (status=3 level=85 temp=285)'

  A @('shell','setprop gsm.sim.state READY; setprop gsm.sim.operator.numeric 20801; setprop gsm.sim.operator.alpha "Orange"; setprop gsm.network.type LTE') | Out-Null
  Write-Ok 'telephony state nominal (Orange/20801/LTE)'

  # --- 6. deep guest OS debloating, network hardening & scheduler priority ---
  $optScript = Join-Path $PSScriptRoot 'optimize-guest-deep.ps1'
  if (Test-Path $optScript) {
    & $optScript -Serial $s -InstanceIndex $instIdx
  }

  # --- 6.1 patch in-memory system properties (ro.product.*, ro.build.*) ---
  $patchPropsScript = Join-Path $PSScriptRoot 'patch-system-props.ps1'
  if (Test-Path $patchPropsScript) {
    & $patchPropsScript -Serial $s
  }

  # --- 7. ensure game is foreground ---------------------------------------
  A @('shell','am','start','-n','com.ankama.dofustouch/.MainActivity') | Out-Null
  Write-Ok 'game activity asserted in foreground'

  # --- 7. final state -----------------------------------------------------
  $m = A @('shell','cat','/proc/meminfo')
  $tot = if ($m -match 'MemTotal:\s+(\d+)') { [math]::Round([int]$matches[1]/1024,0) } else { 0 }
  $av  = if ($m -match 'MemAvailable:\s+(\d+)') { [math]::Round([int]$matches[1]/1024,0) } else { 0 }
  $st  = if ($m -match 'SwapTotal:\s+(\d+)') { [math]::Round([int]$matches[1]/1024,0) } else { 0 }
  $sf  = if ($m -match 'SwapFree:\s+(\d+)') { [math]::Round([int]$matches[1]/1024,0) } else { 0 }
  Write-Host ("    MemTotal {0}MB | MemAvailable {1}MB | Swap {2}/{3}MB free" -f $tot,$av,$sf,$st) -ForegroundColor Gray
}
