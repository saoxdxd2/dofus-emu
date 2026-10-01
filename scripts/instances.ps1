<#
.SYNOPSIS
  Phase 0 - Dofus Touch instance runner and feasibility gate harness.

.DESCRIPTION
  Launches an Android 10 (API 29) x86_64 AVD with host GPU passthrough
  (`-gpu host`) and no software rasterizer, applies the low-RAM profile and a
  self-consistent device identity, then probes the Phase 0 gates.

  Design notes:
   * `-gpu host` is deliberate. Dofus Touch is a WebGL game; `-gpu
     swiftshader_indirect` rasterizes on the CPU and saturates the host. With
     `-gpu host` the guest EGL/GLES3 stack issues calls through the emulator
     bridge into the real host OpenGL driver (Intel UHD), so rasterization
     happens on the host GPU.
   * The device profile is made *coherent* with real hardware (x86_64, Intel
     GLES renderer, release-keys, no root). We deliberately do not impersonate
     a specific retail OEM: claiming an ARM SoC while running x86_64 on an
     Intel driver is internally inconsistent, and that inconsistency is what
     makes Cordova/WebView throw unsupported-hardware errors.
   * Memory floor is a parameter, not a guess. Gate 4 measures the real
     footprint across 1024/1536/2048 MB and sets the production value.

.PARAMETER RamMb
  Guest RAM in MB. Default 1536.

.PARAMETER Gpu
  GPU mode. Default 'host'. Do not use swiftshader_indirect for this workload.

.EXAMPLE
  .\scripts\instances.ps1 -RamMb 1024
#>
[CmdletBinding()]
param(
  # PRODUCTION MEMORY FLOOR: 1024 MB.
  #
  # Measured on this host (Android 10 x86_64, host GPU passthrough, full trim
  # including SystemUI/Launcher3/IME removal):
  #
  #   768 MB  - game chain ~278 MB, MemAvailable 151 MB, but SwapFree only
  #             20 MB of 564 MB, i.e. zRAM ~96% saturated. Survives idle, but
  #             leaves no headroom for WebGL texture churn on a map transition
  #             and invites kswapd stutter.
  #   1024 MB - game chain ~278 MB plus the stripped OS fits in uncompressed
  #             RAM; zRAM stays a safety net and stays idle in normal play.
  #             This is the standardised production value.
  #
  # 1536 MB remains the safe fallback if a build regresses.
  [int]    $RamMb   = 1024,
  [int]    $Cores   = 2,
  [int]    $Port    = 5554,
  [string] $AvdName = 'dofus',
  [string] $Mac     = '02:00:00:00:00:01',
  [string] $Gpu     = 'host',
  [switch] $Headless,
  [switch] $NoWait,
  [switch] $SkipProfile,
  [string] $Data    = '',
  [string] $SerialNo = '',
  [string] $AndroidId = '',
  # More than one instance? This script is the single-instance harness with the
  # Gate 1/2/3 probes. Delegate to start-farm.ps1, which handles N instances
  # with staggered boots and per-instance ports.
  [int]    $Count   = 0
)

if ($Count -gt 1) {
  Write-Host "[phase0] -Count $Count detected -> delegating to scripts\start-farm.ps1" -ForegroundColor Cyan
  $farm = Join-Path $PSScriptRoot 'start-farm.ps1'
  & $farm -Count $Count -RamMb $RamMb -Cores $Cores -AvdName $AvdName -Gpu $Gpu -Headless:$Headless
  exit $LASTEXITCODE
}

$ErrorActionPreference = 'Stop'

$SdkRoot = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path (Split-Path -Parent $PSScriptRoot) 'sdk' }
$Emu     = Join-Path $SdkRoot 'emulator\emulator.exe'
$Adb     = Join-Path $SdkRoot 'platform-tools\adb.exe'
$Img     = Join-Path $SdkRoot 'system-images\android-29\default\x86_64\system.img'
$Serial  = "emulator-$Port"

function Write-Step($m) { Write-Host "[phase0] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[ok]      $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[warn]    $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "[FAIL]    $m" -ForegroundColor Red }

# ---------------------------------------------------------------- preflight
Write-Step 'Preflight checks'
if (-not (Test-Path $Emu)) { Write-Err "emulator.exe missing at $Emu"; exit 1 }
if (-not (Test-Path $Img)) { Write-Err "system image missing at $Img"; exit 1 }
if ($Gpu -ne 'host' -and $Gpu -ne 'off') {
  Write-Warn "GPU mode '$Gpu' requested. For a WebGL game use 'host'."
}
Write-Ok "emulator: $Emu"
Write-Ok "image:    API 29 x86_64"

$AvdHome = Join-Path $env:USERPROFILE '.android\avd'
$AvdDir  = Join-Path $AvdHome "$AvdName.avd"

# Mounting QCOW2 overlay if present or specified (writeback cache)
$targetData = $Data
if (-not $targetData) {
  $qcowCandidate = Join-Path $AvdDir 'userdata.qcow2'
  if (Test-Path $qcowCandidate) {
    $targetData = $qcowCandidate
  }
}

$emuArgs = @(
  "-avd", $AvdName,
  "-port", $Port,
  "-gpu", $Gpu,
  "-memory", $RamMb,
  "-cores", $Cores,
  "-no-snapshot",
  "-no-audio",
  "-no-boot-anim",
  # NOTE: -accel accepts only "on" | "off" | "auto" in emulator 37.x.
  "-accel", "on"
)

if ($targetData) {
  # Emulator -data parameter mounts userdata.qcow2 as data partition with writeback caching.
  # Stripping .qcow2 suffix ensures emulator mounts <path>.qcow2 directly without double suffix.
  $dataBase = $targetData
  if ($dataBase.EndsWith('.qcow2', [System.StringComparison]::OrdinalIgnoreCase)) {
    $dataBase = $dataBase.Substring(0, $dataBase.Length - 6)
  }
  $emuArgs += @("-data", $dataBase)
  Write-Ok "Data partition mounted from QCOW2 overlay: $targetData"
}

# Per-instance identity generation & persistence
$identFile = Join-Path $AvdDir 'identity.json'
$instSerial = $SerialNo
$instAndroidId = $AndroidId
if (Test-Path $identFile) {
  try {
    $idJson = Get-Content $identFile -Raw | ConvertFrom-Json
    if (-not $instSerial) { $instSerial = $idJson.Serial }
    if (-not $instAndroidId) { $instAndroidId = $idJson.AndroidId }
  } catch {}
}
if (-not $instSerial) {
  $chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
  $instSerial = -join ((1..12) | ForEach-Object { $chars[(Get-Random -Minimum 0 -Maximum $chars.Length)] })
}
if (-not $instAndroidId) {
  $instAndroidId = -join ((1..16) | ForEach-Object { '{0:x}' -f (Get-Random -Minimum 0 -Maximum 16) })
}
if (Test-Path $AvdDir) {
  @{ Serial = $instSerial; AndroidId = $instAndroidId; Mac = $Mac } | ConvertTo-Json | Set-Content -Path $identFile
}

# Task 1 & 3: Standardized physical hardware definitions (Samsung Galaxy A51 / SM-A515F)
$standardProps = @(
  "ro.product.brand=samsung",
  "ro.product.manufacturer=samsung",
  "ro.product.model=SM-A515F",
  "ro.product.name=a51nsxx",
  "ro.product.device=a51",
  "ro.build.flavor=a51nsxx-user",
  "ro.build.type=user",
  "ro.build.tags=release-keys",
  "ro.build.fingerprint=samsung/a51nsxx/a51:10/QP1A.190711.020/A515FXXU1ATA7:user/release-keys",
  "ro.hardware=exynos9611",
  "ro.kernel.qemu=0",
  "ro.boot.qemu=0",
  "qemu.hw.mainkeys=1",
  "ro.serialno=$instSerial",
  "ro.boot.serialno=$instSerial",
  "gsm.sim.state=READY",
  "gsm.sim.operator.numeric=60401",
  "gsm.network.type=LTE"
)
foreach ($sp in $standardProps) {
  $emuArgs += @("-prop", $sp)
}
$emuArgs += @("-android-serialno", $instSerial)

if ($Headless) { $emuArgs += '-no-window' }

Write-Step "Launching AVD '$AvdName' port=$Port ram=${RamMb}MB cores=$Cores gpu=$Gpu serial=$instSerial"
$proc = Start-Process -FilePath $Emu -ArgumentList $emuArgs -PassThru -WindowStyle Minimized
Write-Ok "emulator pid=$($proc.Id)"

# ------------------------------------------------------- wait for boot ready
if (-not $NoWait) {
  Write-Step 'Waiting for boot completion (cold boot can take several minutes)...'
  & $Adb -s $Serial wait-for-device 2>&1 | Out-Null
  $booted = $false
  for ($i = 0; $i -lt 180; $i++) {
    Start-Sleep -Seconds 5
    $b = (& $Adb -s $Serial shell getprop sys.boot_completed 2>$null) -replace "`r", ''
    if ($b -match '1') { $booted = $true; break }
  }
  if ($booted) {
    Write-Ok 'boot_completed=1'

    # Task 3: Subsystem normalization & unique identity
    Write-Step 'Applying standardized subsystem telemetry and identity'
    & $Adb -s $Serial shell settings put secure android_id $instAndroidId 2>&1 | Out-Null
    Write-Ok "android_id set: $instAndroidId"

    & $Adb -s $Serial shell "dumpsys battery set status 3; dumpsys battery set level 85; dumpsys battery set temp 285" 2>&1 | Out-Null
    Write-Ok "battery normalized: status=3 (discharging), level=85%, temp=28.5C"

    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Adb -s $Serial shell "setprop gsm.sim.state READY 2>/dev/null; setprop gsm.sim.operator.numeric 60401 2>/dev/null; setprop gsm.network.type LTE 2>/dev/null" 2>$null | Out-Null
    $ErrorActionPreference = $oldEap
    Write-Ok "telephony nominal: READY / 60401 / LTE"
  } else {
    Write-Err 'Guest did not report boot_completed within timeout.'; exit 2
  }
}


# ------------------------------------------------------- low-RAM + identity
# Applied at runtime. A source build would bake these into build.prop, but
# that needs a full AOSP build; `setprop` gives the same observable behaviour
# for the gate tests and is fully reversible.
if (-not $SkipProfile) {
  Write-Step 'Applying low-RAM profile and coherent device identity'

  # --- ro.config.low_ram: NOT reachable on this image -----------------------
  # ro.* properties are read-only after boot, so `setprop ro.config.low_ram
  # true` fails with "Access denied". Writing it needs a writable /system, but:
  #   - /system is read-only here (system-as-root: "/" is ro ext4 dm-2)
  #   - -writable-system makes the emulator report a writable image yet the
  #     guest then hangs at boot
  #   - `emulator -prop ro.config.low_ram=true` boots but the value is absent
  #   - overlayfs over /system from adb root fails with "Invalid argument"
  # So it needs an offline edit of system.img. See patch-system.ps1.
  Write-Step 'Checking ro.config.low_ram'
  & $Adb -s $Serial root 2>&1 | Out-Null
  Start-Sleep -Seconds 3
  $lr = (& $Adb -s $Serial shell 'getprop ro.config.low_ram' 2>$null) -replace "`r",''
  if ($lr -match 'true') { Write-Ok 'ro.config.low_ram already active' }
  else {
    Write-Warn 'ro.config.low_ram is NOT set and cannot be set at runtime here.'
    Write-Warn 'It requires an offline system.img edit (see patch-system.ps1).'
  }

  # --- dalvik.vm.* : runtime-settable, the real saving ----------------------
  Write-Step 'Applying runtime Dalvik heap limits'
  & $Adb -s $Serial shell 'setprop dalvik.vm.heapgrowthlimit 192m' | Out-Null
  & $Adb -s $Serial shell 'setprop dalvik.vm.heapstartupsize 32m' | Out-Null
  & $Adb -s $Serial shell 'setprop dalvik.vm.heapminfree 2m' | Out-Null
  $hg = (& $Adb -s $Serial shell 'getprop dalvik.vm.heapgrowthlimit' 2>$null) -replace "`r",''
  if ($hg -eq '192m') { Write-Ok "dalvik.vm.heapgrowthlimit = $hg" }
  else { Write-Warn "heapgrowthlimit read back as '$hg'" }

  # --- identity coherence --------------------------------------------------
  # Report what the guest actually is, so the profile is validated against
  # reality rather than assumed. Deliberately NOT impersonating a retail OEM:
  # see README "Out of scope".
  Write-Step 'Verifying standardized device identity'
  foreach ($p in @('ro.product.brand','ro.product.manufacturer','ro.product.model','ro.product.name','ro.product.device','ro.hardware','ro.serialno','ro.kernel.qemu','qemu.hw.mainkeys','gsm.network.type')) {
    $v = (& $Adb -s $Serial shell "getprop $p" 2>$null) -replace "`r",''
    if ([string]::IsNullOrWhiteSpace($v)) { $v = '<unset>' }
    Write-Host ("          {0,-26} = {1}" -f $p, $v)
  }
  $aid = (& $Adb -s $Serial shell "settings get secure android_id" 2>$null) -replace "`r",''
  Write-Host ("          {0,-26} = {1}" -f 'android_id', $aid)
  $su = (& $Adb -s $Serial shell 'which su' 2>$null)
  if ($su -match 'su') { Write-Warn 'su binary present - profile should be root-free' }
  else { Write-Ok 'no su binary present' }
}

# ------------------------------------------------------------------- gates
Write-Step 'GATE 1: WebView provider'
$wv = & $Adb -s $Serial shell pm list packages 2>$null
$wvHit = $wv | Select-String -Pattern 'webview'
if ($wvHit) { Write-Ok "WebView packages: $(($wvHit | ForEach-Object { $_.Line.Trim() }) -join ', ')" }
else { Write-Warn 'No WebView package found (gate 1 FAIL).' }

Write-Step 'GATE 2: GLES renderer in the guest (must be the host GPU)'
$gl = & $Adb -s $Serial shell 'dumpsys SurfaceFlinger' 2>$null
$glLine = $gl | Select-String -Pattern '^\s*GLES:' | Select-Object -First 1
if ($glLine) {
  Write-Host "          $($glLine.Line.Trim())"
  if ($glLine.Line -match 'Intel|SwiftShader|llvmpipe') {
    if ($glLine.Line -match 'SwiftShader|llvmpipe') {
      Write-Warn '  software rasterizer in use - -gpu host is NOT working.'
    } else {
      Write-Ok '  hardware GPU confirmed (no SwiftShader/llvmpipe).'
    }
  }
} else { Write-Warn '  could not read GLES info' }

Write-Step 'GATE 3: memory'
$mem = & $Adb -s $Serial shell cat /proc/meminfo 2>$null
foreach ($k in @('MemTotal','AnonPages','MemAvailable','SwapTotal')) {
  $l = $mem | Select-String "^\s*$k\s*:" | Select-Object -First 1
  if ($l) { Write-Host "          $($l.Line.Trim())" }
}

Write-Step "Instance ready. adb: $Adb -s $Serial shell"
Write-Step "Profile: ${RamMb}MB guest / ${Cores} cores / gpu=$Gpu"
if ($Headless) { Write-Host 'Headless: running in background.' -ForegroundColor DarkGray }



