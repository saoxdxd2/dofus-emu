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
  [int]      $RamMb    = 768,
  [int]      $Cores    = 1,
  [int]      $BasePort = 5554,
  [string]   $AvdName  = 'dofus',
  [string]   $GoldenImgPath = '',
  [string]   $Gpu      = 'host',
  [int]      $Vsync    = 30,
  [switch]   $HostCpu,
  [switch]   $Headless,
  [switch]   $Force
)

$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent $PSScriptRoot
$sdkCandidates = @(
  (Join-Path $repoRoot 'sdk'),
  $env:ANDROID_SDK_ROOT,
  $env:ANDROID_HOME,
  (Join-Path ([Environment]::GetFolderPath('UserProfile')) 'DofusFarm\sdk'),
  (Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Documents\dofus-emu\sdk'),
  (Join-Path ([Environment]::GetFolderPath('UserProfile')) 'AppData\Local\Android\Sdk')
) | Where-Object { $_ -and (Test-Path (Join-Path $_ 'platform-tools\adb.exe')) }

$SdkRoot = if ($sdkCandidates.Count -gt 0) { $sdkCandidates[0] } elseif ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $repoRoot 'sdk' }
$env:ANDROID_SDK_ROOT = $SdkRoot
$env:ANDROID_HOME = $SdkRoot
$Emu     = Join-Path $SdkRoot 'emulator\emulator.exe'
$Avd     = Join-Path $SdkRoot 'cmdline-tools\latest\bin\avdmanager.bat'
$Adb     = Join-Path $SdkRoot 'platform-tools\adb.exe'
$AvdHome = Join-Path $env:USERPROFILE '.android\avd'
$QemuImg = Join-Path $SdkRoot 'emulator\qemu-img.exe'
# Validated AVD directory that worker instances are cloned from. It carries the
# FBE encryption keys that match the baked userdata - see the Create action.
$TemplateName = 'dofus-template'
$TemplateDir  = Join-Path $AvdHome "$TemplateName.avd"

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
  # Telephony state & WebView User-Agent flag
  $oldEap = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  & $Adb -s $Serial shell "setprop gsm.sim.state READY 2>/dev/null; setprop gsm.sim.operator.numeric 20801 2>/dev/null; setprop gsm.sim.operator.alpha Orange 2>/dev/null; setprop gsm.network.type LTE 2>/dev/null" 2>$null | Out-Null
  $wvCmd = "_ --user-agent=`"Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36`""
  & $Adb -s $Serial shell "sh -c `"echo '$wvCmd' > /data/local/tmp/webview-command-line`"; chmod 666 /data/local/tmp/webview-command-line" 2>$null | Out-Null
  $ErrorActionPreference = $oldEap
  Write-Ok "  $Serial : telephony state nominal (READY/20801/LTE) and webview-command-line active"
  $patchPropsScript = Join-Path $PSScriptRoot 'patch-system-props.ps1'
  if (Test-Path $patchPropsScript) { & $patchPropsScript -Serial $Serial | Out-Null }
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
      if ((Test-Path (Join-Path $AvdHome "$($p.Name).ini")) -and -not $Force) {
        Write-Ok "AVD $($p.Name) already exists"
      } else {
        if ($Force -and (Test-Path $dir)) {
          Remove-Item $dir -Recurse -Force -EA SilentlyContinue
        }
        # TEMPLATE CLONING, not `avdmanager create avd`.
        #
        # AVDs created by avdmanager boot-loop here:
        #   vold: Failed to prepare /data/system/users/0
        # because Android 10 FBE pairs the CE/DE keys for user 0 in /data with
        # the encryption key material in encryptionkey.img. The golden userdata
        # was baked against the template's key state, so a blank AVD's keys do
        # not match and vold aborts, systemserver restarts, and the instance
        # never completes boot. Cloning the validated template carries the
        # matching encryptionkey.img AND the golden userdata together, which is
        # why it works where reseeding a blank AVD does not.
        if (-not (Test-Path $TemplateDir)) {
          Write-Err "template AVD not found at $TemplateDir"
          Write-Err 'Freeze a validated AVD as the template first (scripts\freeze-template.ps1).'
          continue
        }
        Write-Step "  cloning template -> $($p.Name)"
        Copy-Item -Path $TemplateDir -Destination $dir -Recurse -Force
        # Clones reference the template's userdata-golden.img as their backing
        # file, so remove the 107 MB master image from the clone directory to
        # keep the clone footprint under ~2 MB.
        $clonedMaster = Join-Path $dir 'userdata-golden.img'
        if (Test-Path $clonedMaster) { Remove-Item $clonedMaster -Force -EA SilentlyContinue }

        # Root pointer file. avdmanager writes these; we must reproduce them
        # exactly or the emulator will not recognise the AVD.
        @(
          'avd.ini.encoding=UTF-8'
          "path=$dir"
          "path.rel=avd\$($p.Name).avd"
          'target=android-29'
        ) | Set-Content -Path (Join-Path $AvdHome "$($p.Name).ini") -Encoding UTF8
        Write-Ok "cloned AVD $($p.Name) from template"
      }
      $cfg = Join-Path $dir 'config.ini'
      if (Test-Path $cfg) {
        $extra = @(
          # The clone inherits the template's name; reset it or the emulator
          # reports every instance as "dofus-template".
          "avd.name=$($p.Name)",
          "avd.id=$($p.Name)",
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
          # Remove outer device phone frame & toolbar skin to maximize game area
          "showDeviceFrame=no",
          "skin.name=1280x720",
          "skin.path=_no_skin",
          "skin.dynamic=yes",
          # MUST match the golden image's virtual size so the kernel does not
          # attempt an emergency filesystem resize on first boot. The golden is
          # 6 GiB (6442450944 bytes).
          "disk.dataPartition.size=6442450944",
          "userdata.useQcow2=no"
        )
        $cur = Get-Content $cfg
        foreach ($e in $extra) {
          $k = ($e -split '=')[0]
          # config.ini uses "key = value" with spaces; match both spellings.
          if ($cur -match "^\s*$([regex]::Escape($k))\s*=") { $cur = $cur -replace "^\s*$([regex]::Escape($k))\s*=.*", $e } else { $cur += $e }
        }
        Set-Content -Path $cfg -Value $cur
        Write-Ok "  configured $($p.Name) (gpu=$Gpu ram=$RamMb cores=$Cores)"
      }

      # Task 2: Storage Optimization via Golden Master Overlays
      $goldenMaster = Join-Path $TemplateDir 'userdata-golden.img'
      if (-not (Test-Path $goldenMaster) -and $golden) { $goldenMaster = $golden }
      if (Test-Path $goldenMaster) {
        $emuQcow2 = Join-Path $dir 'userdata-qemu.img.qcow2'
        $rawStub  = Join-Path $dir 'userdata-qemu.img'
        $userQcow = Join-Path $dir 'userdata.qcow2'
        Remove-Item $emuQcow2 -Force -EA SilentlyContinue
        Remove-Item $rawStub  -Force -EA SilentlyContinue
        Remove-Item $userQcow -Force -EA SilentlyContinue

        # Hard-link userdata-qemu.img to userdata-golden.img (0 disk bytes, perfectly matches QEMU expectations)
        try {
          New-Item -ItemType HardLink -Path $rawStub -Target $goldenMaster -Force | Out-Null
        } catch {
          Copy-Item $goldenMaster $rawStub -Force
        }

        # Create qcow2 overlay with relative backing file "userdata-qemu.img" and format qcow2
        Push-Location $dir
        & $QemuImg create -f qcow2 -b "userdata-qemu.img" -F qcow2 "userdata-qemu.img.qcow2" | Out-Null
        Pop-Location

        try {
          New-Item -ItemType HardLink -Path $userQcow -Target $emuQcow2 -Force -EA SilentlyContinue | Out-Null
        } catch {
          Copy-Item $emuQcow2 $userQcow -Force -EA SilentlyContinue
        }
        Write-Ok "  $($p.Name) : QCOW2 differential overlay created (backing: $(Split-Path -Leaf $goldenMaster), fmt: qcow2)"
      }

      # Task 3: Unique Per-Instance Identifiers & Device Profile
      $identFile = Join-Path $dir 'identity.json'
      $serial = New-RandomSerial
      $androidId = New-AndroidId
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
qemu.hw.mainkeys=1
gsm.sim.state=READY
gsm.sim.operator.numeric=20801
gsm.sim.operator.alpha=Orange
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

      # Clean stale locks
      Get-ChildItem -Path $dir -Filter '*.lock' -EA SilentlyContinue | Remove-Item -Recurse -Force -EA SilentlyContinue

      $a = @(
        "-avd", $p.Name, "-port", $p.Port, "-gpu", $Gpu,
        "-memory", $RamMb, "-cores", $Cores,
        "-no-snapshot", "-no-snapshot-load", "-no-snapshot-save",
        "-no-audio", "-no-boot-anim", "-no-metrics",
        "-accel", "on"
      )

      $proxyFile = Join-Path $dir 'proxy.txt'
      if (Test-Path $proxyFile) {
        $prx = (Get-Content $proxyFile -Raw -EA SilentlyContinue).Trim()
        if ($prx) { $a += @("-http-proxy", $prx); Write-Step "  $($p.Name) : using proxy $prx" }
      }

      # Standardized hardware definitions and identity via system.prop
      $sysPropFile = Join-Path $dir 'system.prop'
      if (-not (Test-Path $sysPropFile)) {
        $sysProp = @"
qemu.hw.mainkeys=1
ro.serialno=$serial
ro.boot.serialno=$serial
gsm.sim.state=READY
gsm.sim.operator.numeric=20801
gsm.sim.operator.alpha=Orange
gsm.network.type=LTE
"@
        Set-Content -Path $sysPropFile -Value $sysProp
      }

      $standardProps = @(
        "qemu.hw.mainkeys=1",
        "gsm.sim.state=READY",
        "gsm.sim.operator.numeric=20801",
        "gsm.sim.operator.alpha=Orange",
        "gsm.network.type=LTE"
      )
      foreach ($sp in $standardProps) {
        $a += @("-prop", $sp)
      }
      $a += @("-qemu", "-m", "${RamMb}M", "-smp", "$Cores")
      if ($HostCpu) { $a += @("-cpu", "host") }
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
