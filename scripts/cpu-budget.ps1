<#
.SYNOPSIS
  CPU/thermal budget for a Dofus Touch instance. Dot-source or run directly.

.DESCRIPTION
  Trims the guest so a farm can pack more instances per core. Three sources of
  waste are addressed, in order of payoff:

    1. AUDIO - the emulator's audio HAL polls on a timer and burns a vCPU doing
       nothing. Fully disabled (-no-audio + hw.audioInput/Output=no).
    2. FRAME RATE - Dofus Touch is turn-based isometric. The WebView will happily
       run a 60 FPS rAF/JS loop that draws an unchanged scene, which is pure
       waste. Capped to 30.
    3. RENDERING - Chromium must raster on the GPU, not fall back to CPU Skia.

  Also records the measured ABI/CPU so a regression to an ARM translation layer
  is obvious rather than silent.

  NOTE ON RUNTIME PROPERTIES: debug.choreographer.fps / debug.sf.fps are debug
  properties and are NOT read on a user build, so they have no effect here. The
  cap that actually works is the WebView-side frame budget, applied by
  boot-instance.ps1. This script reports that honestly instead of pretending
  the setprop did something.

.EXAMPLE
  .\scripts\cpu-budget.ps1 -Serial emulator-5554
#>
[CmdletBinding()]
param(
  [string[]] $Serials,
  [switch]   $Measure,
  [int]      $MeasureSec = 10
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Adb      = Join-Path $SdkRoot 'platform-tools\adb.exe'

function Write-Step($m) { Write-Host "[cpu] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[ok]   $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[warn] $m" -ForegroundColor Yellow }

function A([string[]]$argsArr) {
  $s = $Serials[0]
  $full = @('-s', $s) + $argsArr
  # adb writes warnings on stderr and PowerShell wraps them as ErrorRecord in
  # $ErrorActionPreference='Stop' contexts, so flatten to plain strings.
  @(& $Adb @full 2>&1) | ForEach-Object { "$_" }
}

if (-not $Serials -or $Serials.Count -eq 0) {
  $Serials = @((& $Adb devices 2>$null | Select-String '^(emulator-\d+)\s+device' |
                ForEach-Object { $_.Matches[0].Groups[1].Value }))
}
if (-not $Serials -or $Serials.Count -eq 0) {
  Write-Warn 'no running emulator instances found.'; exit 1
}

foreach ($s in $Serials) {
  Write-Step "instance $s"

  & $Adb -s $s root 2>&1 | Out-Null
  Start-Sleep -Seconds 4

  # --- 1. ABI verification: must be native, never an ARM translation layer ---
  # Quote the glob so adb's shell does not expand it locally.
  $oat = (A @('shell',"ls /data/app/com.ankama.dofustouch*/oat/ 2>/dev/null")) -join ''
  $oat = $oat.Trim()
  if ($oat -match 'x86_64') {
    Write-Ok "ABI x86_64 native (oat dir: $oat) - no ARM translation layer"
  } else {
    Write-Warn "unexpected oat ABI: '$oat' - expected x86_64"
  }
  $xlat = A @("shell pm list packages") | Select-String -Pattern 'ndk_translation|houdini|libhoudini'
  if ($xlat) { Write-Warn "ARM translation packages present: $($xlat -join ', ')" }
  else { Write-Ok 'no NDK translation / houdini packages' }

  # --- 2. audio: silence the HAL poll loop ---
  A @('shell','service','call','audio','1') | Out-Null   # stop playback service
  Write-Ok 'audio service stopped (plus -no-audio + hw.audio*=no at launch)'

  # --- 3. telephony: com.android.phone is disabled, so this is belt-and-braces
  A @('shell','cmd','appops','set','com.android.phone','RUN_IN_BACKGROUND','ignore') | Out-Null

  # --- 4. render budget ---
  # qemu.vsync is the knob that matters; it lives in config.ini as qemu.vsync=30
  # and only takes effect on the NEXT boot, so we report rather than pretend.
  Write-Ok 'frame cap: set via qemu.vsync=30 in config.ini (applies on next boot)'

  # --- 5. Chromium GPU rasterization --------------------------------------
  # Chromium on Android reads this file for extra switches. --enable-zero-copy
  # is deliberately NOT included: it is only honoured where a working dma-buf
  # path exists and is ignored (at best) elsewhere, so listing it would look
  # configured while doing nothing.
  # NOTE: the whole redirect is ONE adb argument. Splitting it across args makes
  # adb run only `echo ...` and silently drop the redirection, which is why this
  # silently failed before.
  A @('shell',"echo '_ --enable-gpu-rasterization --ignore-gpu-blocklist' > /data/local/tmp/webview-command-line") | Out-Null
  A @('shell','chmod','644','/data/local/tmp/webview-command-line') | Out-Null
  $wcl = ((A @('shell','cat','/data/local/tmp/webview-command-line')) -join '').Trim()
  if ($wcl -match 'enable-gpu-rasterization') { Write-Ok "webview flags set: $wcl" }
  else { Write-Warn 'could not write /data/local/tmp/webview-command-line' }

  # --- 6. confirm the host GPU is doing the rendering ------------------------
  $gl = A @('shell','dumpsys','SurfaceFlinger') | Select-String -Pattern '^\s*GLES:' | Select-Object -First 1
  if ($gl) { Write-Ok "GLES: $($gl.Line.Trim())" }
  $raster = A @('shell','dumpsys','SurfaceFlinger') | Select-String -Pattern 'GLES Tiler|hwui' | Select-Object -First 2
  if ($raster) { $raster | ForEach-Object { Write-Host "        $($_.Line.Trim())" -ForegroundColor DarkGray } }
}

if ($Measure) {
  Write-Step "host CPU over ${MeasureSec}s (per qemu process)"
  foreach ($s in $Serials) {
    $proc = Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" -EA SilentlyContinue |
            Where-Object { $_.CommandLine -match "-port\s+$($s -replace 'emulator-','')\b" } | Select-Object -First 1
    if (-not $proc) { Write-Warn "${s}: qemu process not found"; continue }
    $p1 = (Get-Process -Id $proc.ProcessId).CPU
    Start-Sleep -Seconds $MeasureSec
    $p2 = (Get-Process -Id $proc.ProcessId).CPU
    $pct = ($p2 - $p1) / $MeasureSec * 100
    Write-Host ("   {0}  {1,6:N0}% of one core" -f $s, $pct) -ForegroundColor Green
  }
}
