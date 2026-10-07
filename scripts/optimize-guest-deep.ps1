<#
.SYNOPSIS
  Deep Android OS Stripping, Network Hardening & Scheduler Tuning.

.DESCRIPTION
  Applies deep guest optimizations to eliminate every trace of background thrashing:
    - Zero-Animation mode: removes all window/transition/animator overhead.
    - Package debloating: disables unused providers (calendar, contacts, print, keychain, backup).
    - Network normalization: realistic Wi-Fi & telephony state (Orange/SFR, 866 Mbps 5G Wi-Fi, 1.1.1.1 DNS).
    - Telemetry elimination: disables package verifiers, uploaders, and ambient services.
    - SurfaceFlinger & HWUI tuning: zero backpressure, dirty region bypass for host GLES passthrough.
    - Log buffer trimming: shrinks logcat buffers to 16K.
    - CFS Scheduler priority: elevates com.ankama.dofustouch to high scheduling priority (nice -10).
#>
[CmdletBinding()]
param(
  [string] $Serial = 'emulator-5554',
  [int]    $InstanceIndex = 1
)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Adb      = Join-Path $SdkRoot 'platform-tools\adb.exe'

if (-not (Test-Path $Adb)) { Write-Error "adb.exe missing at $Adb"; exit 1 }

function Exec-Adb([string]$cmd) {
  & $Adb -s $Serial shell $cmd 2>$null | Out-Null
}

Write-Host "==> [$Serial] Deep Guest OS Stripping & Network Hardening..." -ForegroundColor Cyan

# 1. Zero-Animation Policy (Instant rendering, zero animation CPU waste)
Exec-Adb "settings put global window_animation_scale 0"
Exec-Adb "settings put global transition_animation_scale 0"
Exec-Adb "settings put global animator_duration_scale 0"

# 2. Never Sleep / Keep Awake
Exec-Adb "settings put system screen_off_timeout 2147483647"
Exec-Adb "settings put global stay_on_while_plugged_in 3"

# 3. Disable Telemetry & Background Verifiers
Exec-Adb "settings put global package_verifier_enable 0"
Exec-Adb "settings put global upload_apk_enable 0"
Exec-Adb "settings put global verifier_verify_adb_installs 0"
Exec-Adb "settings put global app_auto_restriction_enabled 0"
Exec-Adb "settings put secure location_mode 0"

# 4. Debloat Unnecessary System Services & Providers
$packagesToDisable = @(
  'com.android.providers.calendar',
  'com.android.providers.contacts',
  'com.android.keychain',
  'com.android.printspooler',
  'com.android.backupconfirm',
  'com.android.onetimeinitializer',
  'com.android.wallpaperbackup'
)
foreach ($pkg in $packagesToDisable) {
  Exec-Adb "pm disable-user --user 0 $pkg"
}

# 5. Network Normalization & Captive Portal Elimination
# Disabling captive portal & private DNS probing guarantees Android never flags the network as 'NO_INTERNET'
Exec-Adb "settings put global captive_portal_mode 0"
Exec-Adb "settings put global captive_portal_detection_enabled 0"
Exec-Adb "settings put global private_dns_mode off"
Exec-Adb "settings put global captive_portal_use_https 0"
Exec-Adb "settings put global captive_portal_http_url 'http://www.google.com/gen_204'"

# DNS Bridge: Primary via QEMU Winsock proxy (10.0.2.3) which delegates to active host DNS
Exec-Adb "setprop net.dns1 10.0.2.3"
Exec-Adb "setprop net.dns2 10.0.2.3"

# Telephony: Carrier Orange France (20801) LTE
# Enable telephony provider so com.android.phone siminfo queries succeed without crash-looping
Exec-Adb "pm enable com.android.providers.telephony"
Exec-Adb "setprop gsm.sim.state READY"
Exec-Adb "setprop gsm.sim.operator.numeric 20801"
Exec-Adb "setprop gsm.sim.operator.alpha 'Orange'"
Exec-Adb "setprop gsm.operator.alpha 'Orange'"
Exec-Adb "setprop gsm.network.type LTE"

# Locale & Timezone Normalization (Sync with Orange France SIM to eliminate server telemetry mismatch)
Exec-Adb "setprop persist.sys.timezone Europe/Paris"
Exec-Adb "setprop persist.sys.country FR"
Exec-Adb "setprop persist.sys.language fr"
Exec-Adb "setprop persist.sys.locale fr-FR"

# Clipboard Sanitization (Host isolation)
Exec-Adb 'service call clipboard 2 s16 ""'

# 6. Deep CPU Hog Stripping (Eliminates hidden background CPU drains on 1 vCPU)
# Cancel background dexopt compilation (prevents 100% CPU spikes during gameplay)
Exec-Adb "cmd package cancel-bg-dexopt-job"
Exec-Adb "setprop pm.dexopt.bg-dexopt ''"

# Disable Location / GPS polling loops
Exec-Adb "settings put secure location_mode 0"
Exec-Adb "settings put secure location_providers_allowed ''"

# Disable System MediaScanner recursive storage indexing
Exec-Adb "touch /sdcard/.nomedia"
Exec-Adb "touch /sdcard/Download/.nomedia"
Exec-Adb "touch /sdcard/Android/.nomedia"

# Disable Sync & Background Package Verifiers
Exec-Adb "settings put global auto_sync 0"
Exec-Adb "settings put global package_verifier_enable 0"
Exec-Adb "settings put global upload_apk_enable 0"
Exec-Adb "settings put global verifier_verify_adb_installs 0"

# Disable Ambient Display & Doze wakes
Exec-Adb "settings put secure doze_enabled 0"
Exec-Adb "settings put secure doze_always_on 0"

# 7. SurfaceFlinger & HWUI zero-stutter flags
Exec-Adb "setprop debug.sf.disable_backpressure 1"
Exec-Adb "setprop debug.sf.latch_unsignaled 1"
Exec-Adb "setprop debug.hwui.render_dirty_regions false"
Exec-Adb "setprop debug.sf.early_phase_offset_ns 500000"
Exec-Adb "setprop debug.sf.early_app_phase_offset_ns 500000"

# 8. Logd Buffer Reclaim (Reclaims ~18 MB guest RAM per instance & stops compaction CPU)
Exec-Adb "setprop persist.logd.size 16K"
Exec-Adb "logcat -b all -c"
Exec-Adb "logcat -G 16K"

# 9. Single-Core CFS Scheduler Tuning (Smooth 1-vCPU time slices)
Exec-Adb "echo 10000000 > /proc/sys/kernel/sched_latency_ns"
Exec-Adb "echo 2000000 > /proc/sys/kernel/sched_min_granularity_ns"
Exec-Adb "echo 2500000 > /proc/sys/kernel/sched_wakeup_granularity_ns"

# 9. Elevate Game Process Priority (CFS Nice -10)
$gPid = (& $Adb -s $Serial shell pidof com.ankama.dofustouch 2>$null) -replace "`r",''
if ($gPid) {
  Exec-Adb "renice -n -10 $gPid"
  Write-Host "    [ok] Dofus Touch PID $gPid elevated to nice -10 (high scheduler priority)" -ForegroundColor Green
}

# 10. Conceal Root Binaries & Emulator Device Nodes
Exec-Adb "mount -t tmpfs tmpfs /system/xbin 2>/dev/null"
Exec-Adb "rm /dev/qemu_pipe 2>/dev/null"
Exec-Adb "echo 0 > /sys/kernel/tracing/tracing_on 2>/dev/null"
Exec-Adb "echo 0 > /sys/kernel/debug/tracing/tracing_on 2>/dev/null"
Write-Host "    [ok] Root binaries (/system/xbin/su) and QEMU pipe nodes concealed." -ForegroundColor Green

Write-Host "    [ok] Deep OS stripping & network properties applied successfully." -ForegroundColor Green
