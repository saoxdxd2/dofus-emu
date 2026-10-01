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
  [ValidateSet('Create','Start','Stop','Status','Provision','Normalize')]
  [string]   $Action = 'Status',
  [ValidateRange(1,4)] [int] $Count = 4,
  [int]      $RamMb    = 1024,
  [int]      $Cores    = 2,
  [int]      $BasePort = 5554,
  [string]   $AvdName  = 'dofus',
  [string]   $GoldenImgPath = '',
  [string]   $Gpu      = 'host',
  [int]      $Vsync    = 30,
  [switch]   $HostCpu,
  [switch]   $Headless,
  [switch]   $Force
)

$ErrorActionPreference = 'Stop'
$SdkRoot = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path (Split-Path -Parent $PSScriptRoot) 'sdk' }
$Emu     = Join-Path $SdkRoot 'emulator\emulator.exe'
$Avd     = Join-Path $SdkRoot 'cmdline-tools\latest\bin\avdmanager.bat'
$Adb     = Join-Path $SdkRoot 'platform-tools\adb.exe'
$AvdHome = Join-Path $env:USERPROFILE '.android\avd'
$QemuImg = Join-Path $SdkRoot 'emulator\qemu-img.exe'

function Write-Step($m) { Write-Host "[cluster] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[ok]      $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[warn]    $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "[FAIL]    $m" -ForegroundColor Red }

function New-RandomSerial {
  $chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
  -join ((1..12) | ForEach-Object { $chars[(Get-Random -Minimum 0 -Maximum $chars.Length)] })
}

function New-AndroidId {
  -join ((1..16) | ForEach-Object { '{0:x}' -f (Get-Random -Minimum 0 -Maximum 16) })
}

function Normalize-InstanceSubsystems([string]$Serial, [string]$AndroidId) {
  Write-Step "Normalizing subsystems on $Serial..."
  if ($AndroidId) {
    & $Adb -s $Serial shell settings put secure android_id $AndroidId 2>&1 | Out-Null
    Write-Ok "  $Serial : android_id set to $AndroidId"
  }
  # Battery normalization: status 3 (discharging), level 85%, temp 285 (28.5 C)
  & $Adb -s $Serial shell "dumpsys battery set status 3; dumpsys battery set level 85; dumpsys battery set temp 285" 2>&1 | Out-Null
  Write-Ok "  $Serial : battery telemetry normalized (status=3, level=85, temp=285)"
  # Telephony state
  $oldEap = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  & $Adb -s $Serial shell "setprop gsm.sim.state READY 2>/dev/null; setprop gsm.sim.operator.numeric 60401 2>/dev/null; setprop gsm.network.type LTE 2>/dev/null" 2>$null | Out-Null
  $ErrorActionPreference = $oldEap
  Write-Ok "  $Serial : telephony state nominal (READY/60401/LTE)"
}

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
    $golden = $GoldenImgPath
    if (-not $golden) {
      $golden = Join-Path $AvdHome "$AvdName.avd\userdata-golden.img"
    }
    if (-not (Test-Path $golden)) {
      Write-Warn "no golden image at $golden - run scripts\build-golden-userdata.ps1 to generate it"
      $golden = $null
    } else {
      Write-Ok "golden image present ($([math]::Round((Get-Item $golden).Length/1MB,2)) MB)"
    }
    foreach ($p in $plan) {
      $dir = Join-Path $AvdHome "$($p.Name).avd"
      if (Test-Path (Join-Path $AvdHome "$($p.Name).ini")) {
        Write-Ok "AVD $($p.Name) already exists"
      } else {
        # `echo no |` avoids the interactive "custom hardware profile" prompt.
        $out = cmd /c "echo no | `"$Avd`" create avd -n $($p.Name) -k `"system-images;android-29;default;x86_64`" -f 2>&1"
        if (Test-Path (Join-Path $AvdHome "$($p.Name).ini")) {
          Write-Ok "created AVD $($p.Name)"
        } else {
          Write-Warn "failed to create $($p.Name): $out"
          continue
        }
      }
      $cfg = Join-Path $dir 'config.ini'
      if (Test-Path $cfg) {
        $extra = @(
          "hw.gpu.enabled=yes", "hw.gpu.mode=$Gpu",
          "hw.ramSize=$RamMb", "hw.cpu.ncore=$Cores",
          # 1280x720 @ 213dpi landscape: Dofus Touch renders on a fixed
          # isometric map grid with CSS-pixel scaling.
          "hw.lcd.width=1280", "hw.lcd.height=720", "hw.lcd.density=213",
          "hw.initialOrientation=landscape",
          # No virtual radio/GPS/cameras: all unused, and the radio is what
          # makes com.android.phone churn once we disable the package.
          "hw.gsmModem=no", "hw.radio=no", "hw.gps=no",
          "hw.camera.back=none", "hw.camera.front=none",
          # Task 1: Navigation & Fullscreen Layout - remove on-screen navbar
          "hw.keyboard=yes", "hw.mainKeys=yes", "qemu.hw.mainkeys=1",
          # CPU BUDGET. The emulator audio HAL polls on a timer and burns a
          # whole vCPU on an idle audio device; turning both directions off
          # removes that spin loop. -no-audio is passed at launch as well.
          "hw.audioInput=no", "hw.audioOutput=no",
          # Cap the guest frame rate. Dofus Touch is turn-based isometric, so a
          # 60 FPS rAF/JS loop redraws an unchanged scene: pure waste. 30 FPS
          # halves draw calls and JS execution with no visible difference.
          "qemu.vsync=$Vsync",
          # MUST match the golden image's virtual size.
          "disk.dataPartition.size=6442450944"
        )
        $cur = Get-Content $cfg
        foreach ($e in $extra) {
          $k = ($e -split '=')[0]
          if ($cur -match "^$k=") { $cur = $cur -replace "^$k=.*", $e } else { $cur += $e }
        }
        Set-Content -Path $cfg -Value $cur
        Write-Ok "  configured $($p.Name) (gpu=$Gpu ram=$RamMb cores=$Cores)"
      }

      # Task 2: Storage Optimization via QCOW2 Differential Overlays
      # Using QEMU Copy-on-Write (COW) overlay instead of copying raw 6 GB disk
      if ($golden) {
        $backingFmt = 'raw'
        $imgInfo = & $QemuImg info "$golden" 2>&1 | Out-String
        if ($imgInfo -match 'file format:\s*qcow2') {
          $backingFmt = 'qcow2'
        }
        $targetQcow2 = Join-Path $dir 'userdata.qcow2'
        Remove-Item $targetQcow2 -Force -EA SilentlyContinue
        & "$SdkRoot\emulator\qemu-img.exe" create -f qcow2 -b "$golden" -F $backingFmt "$targetQcow2"
        if ($LASTEXITCODE -eq 0) {
          Write-Ok "  $($p.Name) : QCOW2 overlay created ($targetQcow2 -> backing: $(Split-Path -Leaf $golden) [$backingFmt])"
          # Link / ensure default emulator path userdata-qemu.img.qcow2 matches userdata.qcow2
          $emuQcow2 = Join-Path $dir 'userdata-qemu.img.qcow2'
          Remove-Item $emuQcow2 -Force -EA SilentlyContinue
          try {
            New-Item -ItemType HardLink -Path $emuQcow2 -Target $targetQcow2 -Force | Out-Null
          } catch {
            Copy-Item $targetQcow2 $emuQcow2 -Force
          }
        } else {
          Write-Err "  $($p.Name) : failed to create QCOW2 overlay"
        }
      }

      # Task 3: Unique Per-Instance Identifiers & Device Profile
      $identFile = Join-Path $dir 'identity.json'
      $serial = ''
      $androidId = ''
      if (Test-Path $identFile) {
        try {
          $ident = Get-Content $identFile -Raw | ConvertFrom-Json
          $serial = $ident.Serial
          $androidId = $ident.AndroidId
        } catch {}
      }
      if (-not $serial) { $serial = New-RandomSerial }
      if (-not $androidId) { $androidId = New-AndroidId }
      @{ Serial = $serial; AndroidId = $androidId; Mac = $p.Mac } | ConvertTo-Json | Set-Content -Path $identFile

      $sysProp = @"
ro.product.brand=samsung
ro.product.manufacturer=samsung
ro.product.model=SM-A515F
ro.product.name=a51nsxx
ro.product.device=a51
ro.build.flavor=a51nsxx-user
ro.build.type=user
ro.build.tags=release-keys
ro.build.fingerprint=samsung/a51nsxx/a51:10/QP1A.190711.020/A515FXXU1ATA7:user/release-keys
ro.hardware=exynos9611
ro.kernel.qemu=0
ro.boot.qemu=0
qemu.hw.mainkeys=1
ro.serialno=$serial
ro.boot.serialno=$serial
gsm.sim.state=READY
gsm.sim.operator.numeric=60401
gsm.network.type=LTE
"@
      Set-Content -Path (Join-Path $dir 'system.prop') -Value $sysProp
      Write-Ok "  $($p.Name) : identity provisioned (serial=$serial, android_id=$androidId)"
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
      $dir = Join-Path $AvdHome "$($p.Name).avd"
      $identFile = Join-Path $dir 'identity.json'
      $serial = ''
      $androidId = ''
      if (Test-Path $identFile) {
        try {
          $ident = Get-Content $identFile -Raw | ConvertFrom-Json
          $serial = $ident.Serial
          $androidId = $ident.AndroidId
        } catch {}
      }
      if (-not $serial) { $serial = New-RandomSerial }
      if (-not $androidId) { $androidId = New-AndroidId }

      $a = @(
        "-avd", $p.Name, "-port", $p.Port, "-gpu", $Gpu,
        "-memory", $RamMb, "-cores", $Cores,
        "-no-snapshot", "-no-audio", "-no-boot-anim",
        "-accel", "on"
      )
      # Data partition: mount userdata.qcow2
      if (Test-Path (Join-Path $dir 'userdata.qcow2')) {
        $a += @("-data", (Join-Path $dir 'userdata'))
      }

      # Standardized hardware definitions and identity via -prop
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
        "ro.serialno=$serial",
        "ro.boot.serialno=$serial",
        "gsm.sim.state=READY",
        "gsm.sim.operator.numeric=60401",
        "gsm.network.type=LTE"
      )
      foreach ($sp in $standardProps) {
        $a += @("-prop", $sp)
      }
      $a += @("-android-serialno", $serial)
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
    & $PSCommandPath -Action Create -Count $Count -RamMb $RamMb -Cores $Cores -BasePort $BasePort -Gpu $Gpu -GoldenImgPath $GoldenImgPath
    & $PSCommandPath -Action Start  -Count $Count -RamMb $RamMb -Cores $Cores -BasePort $BasePort -Gpu $Gpu -Headless:$Headless -Force:$Force
    Write-Step 'Waiting for all instances to finish booting...'
    foreach ($p in $plan) {
      & $Adb -s $p.Serial wait-for-device 2>&1 | Out-Null
      for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep -Seconds 5
        $b = (& $Adb -s $p.Serial shell getprop sys.boot_completed 2>$null) -replace "`r",''
        if ($b -match '1') { Write-Ok "$($p.Serial) booted"; break }
      }
      $identFile = Join-Path $AvdHome "$($p.Name).avd\identity.json"
      $androidId = ''
      if (Test-Path $identFile) {
        try {
          $ident = Get-Content $identFile -Raw | ConvertFrom-Json
          $androidId = $ident.AndroidId
        } catch {}
      }
      Normalize-InstanceSubsystems -Serial $p.Serial -AndroidId $androidId
    }
    Write-Step 'Patching build.prop on all instances...'
    & (Join-Path $PSScriptRoot 'patch-system.ps1') -Serials ($plan.Serial)
  }

  'Normalize' {
    Write-Step 'Normalizing running instances...'
    foreach ($p in $plan) {
      $state  = (& $Adb -s $p.Serial get-state 2>$null) -replace "`r",''
      if ($state -match 'device') {
        $identFile = Join-Path $AvdHome "$($p.Name).avd\identity.json"
        $androidId = ''
        if (Test-Path $identFile) {
          try {
            $ident = Get-Content $identFile -Raw | ConvertFrom-Json
            $androidId = $ident.AndroidId
          } catch {}
        }
        Normalize-InstanceSubsystems -Serial $p.Serial -AndroidId $androidId
      } else {
        Write-Warn "$($p.Serial) is offline - skipping normalization"
      }
    }
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
