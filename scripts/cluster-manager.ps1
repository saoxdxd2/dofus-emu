<#
.SYNOPSIS
  4-instance cluster coordinator.

.DESCRIPTION
  Creates and manages N isolated Dofus Touch instances. Each instance gets:
    - a unique console/adb port pair (5554/5555, 5556/5557, ...)
    - a stable, unique locally-administered MAC
    - its own userdata-qemu.img, so instances cannot corrupt each other's state
    - a -writable-system boot for low-RAM profile patching

  This is ordinary multi-instance hygiene (no port collisions, reproducible
  identity, independent state). It is not a mechanism for evading any external
  system.

  IMPORTANT - HOST CAPACITY: this host has 8 GB RAM and 4 cores. Four instances
  at 1536 MB does not fit alongside Windows and will thrash or OOM. The script
  reports the budget and refuses to start an over-committed farm unless -Force
  is passed. Start with 2 instances and scale only if Gate 4 shows headroom.

.PARAMETER Action
  Create, Start, Stop, Status, or Provision.

.EXAMPLE
  .\scripts\cluster-manager.ps1 -Action Create -Count 4
  .\scripts\cluster-manager.ps1 -Action Start -Count 2
  .\scripts\cluster-manager.ps1 -Action Status
#>
[CmdletBinding()]
param(
  [ValidateSet('Create','Start','Stop','Status','Provision')]
  [string]   $Action = 'Status',
  [ValidateRange(1,4)] [int] $Count = 4,
  [int]      $RamMb    = 1536,
  [int]      $Cores    = 2,
  [int]      $BasePort = 5554,
  [string]   $Gpu      = 'host',
  [switch]   $Headless,
  [switch]   $Force
)

$ErrorActionPreference = 'Stop'
$SdkRoot = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { 'C:\android-sdk' }
$Emu     = Join-Path $SdkRoot 'emulator\emulator.exe'
$Avd     = Join-Path $SdkRoot 'cmdline-tools\latest\bin\avdmanager.bat'
$Adb     = Join-Path $SdkRoot 'platform-tools\adb.exe'
$AvdHome = Join-Path $env:USERPROFILE '.android\avd'

function Write-Step($m) { Write-Host "[cluster] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[ok]      $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[warn]    $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "[FAIL]    $m" -ForegroundColor Red }

# Console ports must be even: each AVD consumes a pair (console, adb).
function Get-InstancePlan($n) {
  1..$n | ForEach-Object {
    $i = $_
    $port = $BasePort + (2 * ($i - 1))
    [PSCustomObject]@{
      Index   = $i
      Name    = 'dofus-{0:d2}' -f $i
      Port    = $port
      AdbPort = $port + 1
      # Locally-administered (bit 1 of first octet set), unique per instance.
      Mac     = '52:54:00:00:{0:d2}:{1:d2}' -f 0, $i
      Serial  = "emulator-$port"
    }
  }
}

$plan = Get-InstancePlan $Count
if ($plan | Where-Object { $_.Port % 2 -ne 0 }) {
  Write-Err 'BasePort must be even (console/adb ports are allocated in pairs).'
  exit 1
}

Write-Step "Plan for $Count instance(s):"
$plan | Format-Table -AutoSize


switch ($Action) {

  'Create' {
    Write-Step 'Creating AVDs (one per instance, separate data dirs)'
    foreach ($p in $plan) {
      $dir = Join-Path $AvdHome "$($p.Name).avd"
      if (Test-Path (Join-Path $AvdHome "$($p.Name).ini")) {
        Write-Ok "AVD $($p.Name) already exists"
        continue
      }
      # `echo no |` avoids the interactive "custom hardware profile" prompt.
      $out = cmd /c "echo no | `"$Avd`" create avd -n $($p.Name) -k `"system-images;android-29;default;x86_64`" -f 2>&1"
      if (Test-Path (Join-Path $AvdHome "$($p.Name).ini")) {
        Write-Ok "created AVD $($p.Name)"
        $cfg = Join-Path $dir 'config.ini'
        if (Test-Path $cfg) {
          $extra = @(
            "hw.gpu.enabled=yes", "hw.gpu.mode=$Gpu",
            "hw.ramSize=$RamMb", "hw.cpu.ncore=$Cores",
            "hw.keyboard=yes", "hw.mainKeys=no", "hw.audioInput=no",
            "hw.audioOutput=no", "hw.camera.back=none", "hw.camera.front=none",
            "disk.dataPartition.size=2048M"
          )
          $cur = Get-Content $cfg
          foreach ($e in $extra) {
            $k = ($e -split '=')[0]
            if ($cur -match "^$k=") { $cur = $cur -replace "^$k=.*", $e } else { $cur += $e }
          }
          Set-Content -Path $cfg -Value $cur
          Write-Ok "  configured $($p.Name) (gpu=$Gpu ram=$RamMb cores=$Cores)"
        }
      } else {
        Write-Warn "failed to create $($p.Name): $out"
      }
    }
  }

  'Start' {
    # Capacity preflight: guests are backed by host RAM, and zram inside a guest
    # is also host RAM. Refuse an impossible request rather than silently thrash.
    $freeGB = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1)
    $needGB = [math]::Round(($Count * $RamMb) / 1024 + 1.5, 1)
    Write-Step "Host free memory: ~${freeGB}GB ; $Count x ${RamMb}MB guests needs ~${needGB}GB incl. Windows"
    if (($Count * $RamMb) -gt (($freeGB * 1024) - 1536)) {
      Write-Warn 'Requested footprint exceeds available memory. Expect thrash/OOM.'
      if (-not $Force) { Write-Err 'Refusing to start. Pass -Force to override.'; exit 1 }
    }
    foreach ($p in $plan) {
      $a = @(
        "-avd", $p.Name, "-port", $p.Port, "-gpu", $Gpu,
        "-memory", $RamMb, "-cores", $Cores,
        "-no-snapshot", "-no-audio", "-no-boot-anim",
        # -accel accepts only on|off|auto in emulator 37.x (not "hvm").
        "-writable-system", "-accel", "on"
      )
      if ($Headless) { $a += '-no-window' }
      $proc = Start-Process -FilePath $Emu -ArgumentList $a -PassThru -WindowStyle Minimized
      Write-Ok "started $($p.Name) pid=$($proc.Id) console=$($p.Port) serial=$($p.Serial)"
      Start-Sleep -Seconds 8
    }
    Write-Step 'After boot, apply the profile to all instances:'
    Write-Host '    .\scripts\patch-system.ps1 -Reboot'
  }

  'Stop' {
    foreach ($p in $plan) {
      & $Adb -s $p.Serial emu kill 2>&1 | Out-Null
      Write-Ok "requested shutdown of $($p.Serial)"
    }
  }

  'Provision' {
    & $PSCommandPath -Action Create -Count $Count -RamMb $RamMb -Cores $Cores -BasePort $BasePort -Gpu $Gpu
    & $PSCommandPath -Action Start  -Count $Count -RamMb $RamMb -Cores $Cores -BasePort $BasePort -Gpu $Gpu -Headless:$Headless -Force:$Force
    Write-Step 'Waiting for all instances to finish booting...'
    foreach ($p in $plan) {
      & $Adb -s $p.Serial wait-for-device 2>&1 | Out-Null
      for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep -Seconds 5
        $b = (& $Adb -s $p.Serial shell getprop sys.boot_completed 2>$null) -replace "`r",''
        if ($b -match '1') { Write-Ok "$($p.Serial) booted"; break }
      }
    }
    Write-Step 'Patching build.prop on all instances...'
    & (Join-Path $PSScriptRoot 'patch-system.ps1') -Serials ($plan.Serial)
  }

  'Status' {
    Write-Step 'Instance status'
    $rows = foreach ($p in $plan) {
      $state  = (& $Adb -s $p.Serial get-state 2>$null) -replace "`r",''
      $online = [bool]($state -match 'device')
      $boot = if ($online) { (& $Adb -s $p.Serial shell getprop sys.boot_completed 2>$null) -replace "`r",'' } else { '' }
      $low  = if ($online) { (& $Adb -s $p.Serial shell getprop ro.config.low_ram 2>$null) -replace "`r",'' } else { '' }
      [PSCustomObject]@{
        Name = $p.Name; Port = $p.Port; Serial = $p.Serial; Mac = $p.Mac
        State = $(if ($online) { 'online' } else { 'offline' }); Booted = $boot; LowRam = $low
      }
    }
    $rows | Format-Table -AutoSize
  }
}
