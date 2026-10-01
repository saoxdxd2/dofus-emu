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
  [switch] $WritableSystem,
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

# ------------------------------------------------------------------ launch
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
  # Passing "hvm" (the older spelling) aborts startup with:
  #   ERROR | Invalid '-accel hvm' parameter, valid values are: on off auto
  # VT-x is present on this host, so "on" gives hardware acceleration.
  "-accel", "on"
)
# -writable-system is opt-in and OFF by default. It makes the emulator log
# "System image is writable" but the guest then HANGS at boot on this image
# (adb stays offline with near-idle CPU) instead of finishing in ~47s.
# Reproduced at 1536 MB and again at 1024 MB with 4.3 GB free RAM.
# It does not help anyway: /system is read-only (system-as-root), so adb remount
# still cannot write build.prop. See patch-system.ps1 for the details.
if ($WritableSystem) { $emuArgs += '-writable-system' }
if ($Headless) { $emuArgs += '-no-window' }

Write-Step "Launching AVD '$AvdName' port=$Port ram=${RamMb}MB cores=$Cores gpu=$Gpu"
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
  if ($booted) { Write-Ok 'boot_completed=1' }
  else { Write-Err 'Guest did not report boot_completed within timeout.'; exit 2 }
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
  Write-Step 'Verifying device identity coherence'
  foreach ($p in @('ro.product.cpu.abi','ro.product.model','ro.build.type','ro.debuggable','ro.build.tags')) {
    $v = (& $Adb -s $Serial shell "getprop $p" 2>$null) -replace "`r",''
    if ([string]::IsNullOrWhiteSpace($v)) { $v = '<unset>' }
    Write-Host ("          {0,-22} = {1}" -f $p, $v)
  }
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



