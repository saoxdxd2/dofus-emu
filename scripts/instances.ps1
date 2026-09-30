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
  [int]    $RamMb   = 1536,
  [int]    $Cores   = 2,
  [int]    $Port    = 5554,
  [string] $AvdName = 'dofus',
  [string] $Mac     = '02:00:00:00:00:01',
  [string] $Gpu     = 'host',
  [switch] $Headless,
  [switch] $NoWait,
  [switch] $SkipProfile,
  [switch] $WritableSystem
)

$ErrorActionPreference = 'Stop'

$SdkRoot = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { 'C:\android-sdk' }
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
# -writable-system gives a writable copy of /system for this session so
# ro.config.low_ram can be written to /system/build.prop. Without it the guest
# mounts /system read-only and the property write below fails.
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

  # --- ro.config.low_ram -------------------------------------------------
  # IMPORTANT: properties in the ro.* namespace are read-only once the guest has
  # booted; `setprop ro.config.low_ram true` fails with
  # "failed to set property ... to ...: Access denied" (or silently no-ops).
  # The value is read by zygote/ActivityManager at startup, so it must be
  # present in /system/build.prop BEFORE boot completes to take effect.
  #
  # Requires the emulator to have been started with -writable-system, which
  # provides a writable overlay of /system for this session.
  $needsRemount = $true
  Write-Step 'Remounting /system for build.prop edit (required for ro.* properties)'
  & $Adb -s $Serial root 2>&1 | Out-Null
  Start-Sleep -Seconds 3
  $remount = & $Adb -s $Serial remount 2>&1
  if ($remount -match 'remount succeeded|remounted') {
    Write-Ok 'remount succeeded'
    $needsRemount = $false
  } else {
    Write-Warn "remount output: $remount"
    if (-not $WritableSystem) {
      Write-Warn 'This usually means the AVD was started WITHOUT -writable-system.'
      Write-Warn 'Relaunch with -WritableSystem to make ro.config.low_ram effective.'
    }
  }

  if (-not $needsRemount) {
    # Append to /system/build.prop. Check first so re-runs stay idempotent.
    $existing = & $Adb -s $Serial shell 'grep -c "^ro.config.low_ram=" /system/build.prop 2>/dev/null' 2>$null
    if ($existing -match '1') {
      & $Adb -s $Serial shell 'sed -i "s/^ro.config.low_ram=.*/ro.config.low_ram=true/" /system/build.prop' 2>&1 | Out-Null
      Write-Ok 'ro.config.low_ram updated to true in /system/build.prop'
    } else {
      & $Adb -s $Serial shell 'echo "ro.config.low_ram=true" >> /system/build.prop' 2>&1 | Out-Null
      Write-Ok 'ro.config.low_ram=true appended to /system/build.prop'
    }
    # Verify it actually landed, rather than assuming the write worked.
    $verify = (& $Adb -s $Serial shell 'grep "^ro.config.low_ram=" /system/build.prop' 2>$null) -replace "`r",''
    if ($verify -match 'true') { Write-Ok "verified in build.prop: $verify" }
    else { Write-Err "build.prop write did not verify. Line reads: '$verify'" }
    Write-Warn 'ro.config.low_ram is read at zygote startup: reboot the guest for it to take effect.'
  } else {
    Write-Warn 'ro.config.low_ram NOT applied (see above). Phase 2 trimming depends on it.'
  }

  # --- dalvik.* ----------------------------------------------------------
  # dalvik.vm.* is a regular read-write property (no ro. prefix), so setprop
  # works at runtime. This is what actually bounds ART's heap growth.
  Write-Step 'Applying runtime Dalvik heap limits'
  & $Adb -s $Serial shell 'setprop dalvik.vm.heapgrowthlimit 192m' | Out-Null
  & $Adb -s $Serial shell 'setprop dalvik.vm.heapstartupsize 32m'  | Out-Null
  $hg = (& $Adb -s $Serial shell 'getprop dalvik.vm.heapgrowthlimit' 2>$null) -replace "`r",''
  if ($hg -eq '192m') { Write-Ok "dalvik.vm.heapgrowthlimit = $hg" }
  else { Write-Warn "heapgrowthlimit read back as '$hg'" }

  # Coherence check: report what the guest actually is, so the profile is
  # validated against reality rather than assumed.
  Write-Step 'Verifying device identity coherence'
  foreach ($p in @('ro.product.cpu.abi','ro.product.model','ro.build.type','ro.debuggable','ro.build.tags')) {
    $v = (& $Adb -s $Serial shell "getprop $p" 2>$null) -replace "`r", ''
    Write-Host ("          {0,-22} = {1}" -f $p, $v)
  }
  $rootCheck = & $Adb -s $Serial shell 'which su' 2>$null
  if ($rootCheck -match 'su') { Write-Warn 'su binary present - should be absent for a clean profile' }
  else { Write-Ok 'no su binary present' }
}

# ------------------------------------------------------------------- gates
Write-Step 'GATE 1: WebView provider'
$wv = & $Adb -s $Serial shell 'pm list packages | grep -i webview' 2>$null
if ($wv -match 'webview') { Write-Ok "WebView packages: $wv" }
else {
  Write-Warn 'No WebView package found. AOSP ships only a stub; a real Chromium'
  Write-Warn 'WebView must be sideloaded before the game can run (gate 1 FAIL).'
}

Write-Step 'GATE 2: WebGL2 probe (must report a HOST GPU, not software)'
$glScript = 'var c=document.createElement("canvas");var g=c.getContext("webgl2");' +
            'if(!g){console.log("WEBGL2=NULL");}' +
            'else{var d=g.getExtension("WEBGL_debug_renderer_info");' +
            'console.log("WEBGL2=OK");' +
            'console.log("RENDERER="+(d?g.getParameter(d.UNMASKED_RENDERER_WEBGL):g.getParameter(g.RENDERER)));' +
            'console.log("VENDOR="+(d?g.getParameter(d.UNMASKED_VENDOR_WEBGL):g.getParameter(g.VENDOR)));' +
            'console.log("MAX_TEXTURE_SIZE="+g.getParameter(g.MAX_TEXTURE_SIZE));}'
$glFile = Join-Path $env:TEMP 'webgl-probe.html'
Set-Content -Path $glFile -Value "<html><body><script>$glScript</script></body></html>" -Encoding UTF8
& $Adb -s $Serial push $glFile /sdcard/webgl-probe.html 2>&1 | Out-Null
Write-Ok 'probe pushed to /sdcard/webgl-probe.html'
Write-Host '          open it in the guest WebView and read console output.' -ForegroundColor DarkGray

Write-Step 'GATE 3: memory high-water mark'
$meminfo = & $Adb -s $Serial shell dumpsys meminfo 2>$null
if ($meminfo) {
  foreach ($pat in @('Total RAM','Used RAM','Free RAM','Lost RAM')) {
    $l = $meminfo | Select-String -Pattern $pat | Select-Object -First 1
    if ($l) { Write-Host "          $($l.Line.Trim())" }
  }
}

Write-Step "Instance ready. adb: $Adb -s $Serial shell"
Write-Step "Profile: ${RamMb}MB guest / ${Cores} cores / gpu=$Gpu"
if ($Headless) { Write-Host 'Headless: running in background.' -ForegroundColor DarkGray }
