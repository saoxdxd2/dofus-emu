<#
.SYNOPSIS
  Self-Healing Watchdog Supervisor for 24/7 Multi-Instance Farm Stability.
.DESCRIPTION
  Periodically verifies the health, ADB responsiveness, and game status of all active slots.
  If any individual slot freezes, crashes, or gets OOM-killed:
    1. Identifies the malfunctioning slot without interrupting the other 3.
    2. Gracefully halts and cleans up only that slot's process.
    3. Restarts the slot with preserved session and differential overlay.
    4. Re-applies the Samsung Galaxy A51 stealth disguise.
    5. Re-launches Dofus Touch in the foreground.
    6. Re-tiles the 2x2 grid layout seamlessly.
#>
[CmdletBinding()]
param(
  [int] $IntervalSec = 25,
  [string[]] $WatchInstances = @(),
  [switch] $SinglePass
)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Adb      = Join-Path $SdkRoot 'platform-tools\adb.exe'

function Write-Log($msg, $color = 'White') {
  $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
  Write-Host "[$ts] [WATCHDOG] $msg" -ForegroundColor $color
}

function Check-And-Heal-Instances {
  # Discover instances currently expected to be running
  $liveSerials = @((& $Adb devices 2>$null) | Select-String -Pattern '^(emulator-\d+)\s+device' | ForEach-Object { $_.Matches[0].Groups[1].Value })

  $targets = $WatchInstances
  if ($targets.Count -eq 0) {
    # Default to any dofus-0X that has an online or registered port
    $targets = (1..4) | ForEach-Object { "dofus-0$_" }
  }

  foreach ($name in $targets) {
    $idx = 1
    if ($name -match '(\d+)$') { $idx = [int]$Matches[1] }
    $port = 5554 + 2 * ($idx - 1)
    $serial = "emulator-$port"

    # Check if QEMU process exists for this port
    $proc = Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" -EA SilentlyContinue |
            Where-Object { $_.CommandLine -match "-port\s+$port\b" } | Select-Object -First 1

    if (-not $proc) {
      # Instance not currently running - skip if user stopped it intentionally
      continue
    }

    # Verify ADB responsiveness
    $isResponsive = $false
    try {
      $b = (& $Adb -s $serial shell getprop sys.boot_completed 2>$null) -replace "`r|`n",''
      if ($b -match '1') {
        $isResponsive = $true
      }
    } catch {}

    if (-not $isResponsive) {
      Write-Log "HEAL TRIGGERED: $name ($serial, PID: $($proc.ProcessId)) is UNRESPONSIVE / FROZEN!" 'Red'
      
      # 1. Terminate stale process
      Write-Log "Halting frozen instance $name..." 'Yellow'
      & $Adb -s $serial emu kill 2>$null | Out-Null
      Start-Sleep -Seconds 1
      Stop-Process -Id $proc.ProcessId -Force -EA SilentlyContinue
      Start-Sleep -Seconds 2

      # 2. Restart only this single instance
      Write-Log "Relaunching slot $idx ($name)..." 'Cyan'
      & (Join-Path $PSScriptRoot 'start-farm.ps1') -Count $idx -RamMb 768 -Cores 1 -AutoBoot -Force -EdgeToEdge
      Start-Sleep -Seconds 3

      # 3. Re-assert game in foreground
      Write-Log "Asserting Dofus Touch in foreground for $serial..." 'Cyan'
      & $Adb -s $serial shell am start -n com.ankama.dofustouch/.MainActivity 2>$null | Out-Null

      # 4. Re-tile windows into 2x2 grid
      Write-Log "Restoring 2x2 grid layout..." 'Cyan'
      & (Join-Path $PSScriptRoot 'layout.ps1')
      $allQemu = @(Get-Process -Name 'qemu-system-x86_64' -EA SilentlyContinue | ForEach-Object { $_.Id })
      Set-FarmLayout -Procs $allQemu -EdgeToEdge

      Write-Log "Self-heal completed successfully for $name ($serial)!" 'Green'
    } else {
      # Verify if Dofus Touch crashed inside responsive Android
      $gamePid = (& $Adb -s $serial shell pidof com.ankama.dofustouch 2>$null) -replace "`r|`n",''
      if (-not $gamePid) {
        Write-Log "Game crashed or closed in $name ($serial). Relaunching com.ankama.dofustouch..." 'Yellow'
        & $Adb -s $serial shell am start -n com.ankama.dofustouch/.MainActivity 2>$null | Out-Null
      }
    }
  }
}

Write-Log "Self-Healing Watchdog Supervisor initialized (polling every ${IntervalSec}s)..." 'Cyan'

if ($SinglePass) {
  Check-And-Heal-Instances
  return
}

while ($true) {
  Check-And-Heal-Instances
  Start-Sleep -Seconds $IntervalSec
}
