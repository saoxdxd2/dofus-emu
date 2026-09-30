<#
.SYNOPSIS
  Phase 0 Gate 4 - measure the real per-instance memory floor.

.DESCRIPTION
  Boots the image at a set of RAM allocations, records the guest's own memory
  accounting, and reports the high-water mark so the production value is
  derived from measurement rather than estimated.

  The point of this gate is that 768 MB - the figure in the original spec -
  is below the practical floor for a WebGL page: the Chromium renderer alone
  typically wants 200-400 MB, before the game's own textures. zRAM does not
  change that, because zram0's backing store is the guest's own RAM.

  Each run is a cold boot with a throwaway userdata so measurements are not
  polluted by the previous run.

.PARAMETER Levels
  RAM allocations in MB to test. Default 1024, 1536, 2048.

.EXAMPLE
  .\scripts\bench-memory.ps1 -Levels 768,1024,1536
#>
[CmdletBinding()]
param(
  [int[]] $Levels = @(1024, 1536, 2048),
  [string] $AvdName = 'dofus-bench',
  [string] $WebViewApk,
  [string] $GameApk,
  [switch] $SkipGame
)

$ErrorActionPreference = 'Stop'

$SdkRoot = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { 'C:\android-sdk' }
$Emu     = Join-Path $SdkRoot 'emulator\emulator.exe'
$Adb     = Join-Path $SdkRoot 'platform-tools\adb.exe'
$AvdHome = Join-Path $env:USERPROFILE '.android\avd'
$Port    = 5560
$Serial  = "emulator-$Port"
$GamePkg = 'com.ankama.dofustouch'

function Write-Step($m) { Write-Host "[bench] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[ok]    $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[warn]  $m" -ForegroundColor Yellow }

# Fresh throwaway AVD per level: snapshot/cache carryover would bias the result.
$baseAvi = Join-Path $AvdHome "$AvdName.avd\config.ini"
$baseIni = Join-Path $AvdHome "$AvdName.ini"
if (-not (Test-Path $baseAvi)) {
  Write-Step "No bench AVD at $baseAvi. Create it first:"
  Write-Host "  avdmanager create avd -n $AvdName -k 'system-images;android-29;default;x86_64' -d pixel"
  exit 1
}

$results = @()

foreach ($mb in $Levels) {
  Write-Step "=== Testing ${mb}MB ==="

  # Wipe userdata so each level starts from the same clean state.
  $userdata = Join-Path $AvdHome "$AvdName.avd\userdata-qemu.img"
  if (Test-Path $userdata) { Remove-Item $userdata -Force -ErrorAction SilentlyContinue }

  $proc = Start-Process -FilePath $Emu -PassThru -WindowStyle Minimized -ArgumentList @(
    "-avd", $AvdName, "-port", $Port, "-gpu", "host",
    "-memory", $mb, "-cores", 2, "-no-snapshot", "-no-audio",
    # -accel accepts only on|off|auto in emulator 37.x (not "hvm").
    "-no-boot-anim", "-wipe-data", "-writable-system", "-accel", "on"
  )

  & $Adb -s $Serial wait-for-device 2>&1 | Out-Null
  $booted = $false
  for ($i = 0; $i -lt 180; $i++) {
    Start-Sleep -Seconds 5
    $b = (& $Adb -s $Serial shell getprop sys.boot_completed 2>$null) -replace "`r",''
    if ($b -match '1') { $booted = $true; break }
  }
  if (-not $booted) { Write-Step "  boot failed at ${mb}MB"; Stop-Process -Id $proc.Id -Force -EA SilentlyContinue; continue }

  # Settle: let zygote/media finish starting so we measure steady state.
  Start-Sleep -Seconds 30

  # --- reinstall apps ------------------------------------------------------
  # Each level runs with -wipe-data, so userdata is reset to factory every
  # time. Any app installed by hand is therefore gone on the NEXT iteration,
  # and `monkey` aborts with "No activities found" unless we reinstall first.
  # Doing it here keeps every level measured under identical conditions.
  if ($WebViewApk -and (Test-Path $WebViewApk)) {
    Write-Step "  installing WebView from $WebViewApk"
    & $Adb -s $Serial install -r -g "$WebViewApk" 2>&1 | ForEach-Object { "    $_" }
  }
  if ($GameApk -and (Test-Path $GameApk)) {
    Write-Step "  installing game from $GameApk"
    & $Adb -s $Serial install -r -g "$GameApk" 2>&1 | ForEach-Object { "    $_" }
  }
  # Confirm what is actually present before trying to launch, so a failed
  # install is reported as such instead of surfacing as a confusing monkey error.
  $haveGame = & $Adb -s $Serial shell "pm list packages $GamePkg" 2>$null
  $haveWv   = & $Adb -s $Serial shell 'pm list packages | grep -i webview' 2>$null
  Write-Host "    webview present: $([bool]$haveWv)   game present: $([bool]$haveGame)"

  function Get-Mem($pat) {
    $line = (& $Adb -s $Serial shell dumpsys meminfo 2>$null) | Select-String -Pattern $pat | Select-Object -First 1
    if ($line) { if ($line.Line -match '([\d,]+)\s*K') { return [int](($matches[1]) -replace ',','') } }
    return 0
  }

  $total = Get-Mem 'Total RAM'
  $used  = Get-Mem 'Used RAM'
  $free  = Get-Mem 'Free RAM'

  # Kernel view: what the guest itself thinks it has.
  $mi = & $Adb -s $Serial shell cat /proc/meminfo 2>$null
  $mt = ($mi | Select-String '^MemTotal' | Select-Object -First 1)
  $ma = ($mi | Select-String '^MemAvailable' | Select-Object -First 1)

  $gameRunning = $false
  if (-not $SkipGame) {
    # Only attempt the launch if the package is actually installed, otherwise
    # monkey aborts with "No activities found" and we record a false failure.
    if ($haveGame) {
      & $Adb -s $Serial shell monkey -p $GamePkg -c android.intent.category.LAUNCHER 1 2>&1 | Out-Null
      Start-Sleep -Seconds 45   # let the WebView load and the first scene draw
      $gameRunning = $true
    } else {
      Write-Warn "  game not installed; skipping launch (level measures idle OS only)"
    }
    # Per-process breakdown for the top consumers.
    $top = & $Adb -s $Serial shell dumpsys meminfo 2>$null |
           Select-String -Pattern 'webview|chrome|dofus|TOTAL PSS' | Select-Object -First 8
    foreach ($t in $top) { Write-Host "        $($t.Line.Trim())" }
  }

  $usedMB   = [math]::Round($used/1024, 1)
  $totalMB  = [math]::Round($total/1024, 1)
  $availKB  = if ($ma -and $ma.Line -match ':\s*(\d+)') { [int]$matches[1] } else { 0 }

  Write-Ok "  total=${totalMB}MB used=${usedMB}MB avail=$([math]::Round($availKB/1024,1))MB"
  if ($gameRunning) { Write-Ok '  game launch attempted' }

  $results += [PSCustomObject]@{
    AllocatedMB = $mb
    TotalMB     = $totalMB
    UsedMB      = $usedMB
    AvailMB     = [math]::Round($availKB/1024,1)
  }

  & $Adb -s $Serial emu kill 2>&1 | Out-Null
  Start-Sleep -Seconds 10
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 5
}

Write-Step '=== Gate 4 results ==='
$results | Format-Table -AutoSize

# Interpret: the smallest level that leaves a healthy Available margin under
# load is the production floor. ~150MB+ available is the practical target.
$results | Export-Csv -NoTypeInformation -Path (Join-Path $PSScriptRoot 'gate4-memory.csv')
Write-Ok "wrote gate4-memory.csv"
Write-Step 'Viable levels (Available >= 150MB):'
$results | Where-Object { $_.AvailMB -ge 150 } | ForEach-Object { Write-Host "    $($_.AllocatedMB)MB" }
