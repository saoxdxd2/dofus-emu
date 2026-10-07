<#
.SYNOPSIS
  Launch N Dofus Touch instances as a farm, laid out to fill the screen.

.DESCRIPTION
  Starts N API 29 instances, each with:
    - its own AVD (dofus-01, dofus-02, ...) so /data is never shared
    - a fixed console/adb port pair (5554/5555, 5556/5557, ...)
    - a stable locally-administered MAC
    - a data disk seeded from the golden image

  Capacity is computed from the HOST, not hardcoded. Guest RAM, core count and
  instance count are all derived from this machine's actual RAM and CPU, so the
  script behaves sensibly on a 4 GB laptop and a 64 GB workstation alike.
  Pass -RamMb / -Cores / -Count to override.

  Window placement is adaptive (see layout.ps1):
    1 -> maximised, 2 -> side by side, 3 -> 2-over-1, 4+ -> even grid.

.EXAMPLE
  .\scripts\start-farm.ps1 -Count 2
.EXAMPLE
  .\scripts\start-farm.ps1 -Auto          # pick the largest count this host can run
#>
[CmdletBinding()]
param(
  [int]    $Count    = 0,      # 0 = derive from host capacity
  [int]    $RamMb    = 0,      # 0 = derive from host capacity
  [int]    $Cores    = 0,      # 0 = derive from host CPU
  [int]    $BasePort = 5554,
  [string] $AvdPrefix = 'dofus',
  [string] $Gpu      = 'host',
  [int]    $Vsync    = 30,
  [string] $Proxy    = '',
  [switch] $HostCpu,
  [switch] $Auto,
  [switch] $Headless,
  [switch] $NoLayout,
  [switch] $EdgeToEdge,
  [switch] $AutoBoot,
  [switch] $Wait,
  [switch] $Force,
  [switch] $ResetOverlays,
  [switch] $Restart
)

$ErrorActionPreference = 'Continue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Emu      = Join-Path $SdkRoot 'emulator\emulator.exe'
$Adb      = Join-Path $SdkRoot 'platform-tools\adb.exe'
$Golden   = Join-Path $env:USERPROFILE ".android\avd\$AvdPrefix.avd\userdata-golden.img"

. (Join-Path $PSScriptRoot 'layout.ps1')

function Write-Step($m) { Write-Host "[farm] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[ok]    $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[warn]  $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "[FAIL]  $m" -ForegroundColor Red }

if (-not (Test-Path $Emu)) { Write-Err "emulator.exe missing at $Emu"; exit 1 }

# ============================================================ host capacity
# Everything below is derived from the machine we are actually running on.
$cs      = Get-CimInstance Win32_ComputerSystem
$os      = Get-CimInstance Win32_OperatingSystem
$hostGB  = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
$freeGB  = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
$cpuCores= [int]$cs.NumberOfLogicalProcessors

# Reserve for Windows + the host GPU compositor + our own tooling. Without this
# the farm happily "fits" and then the OOM killer reaps the guests.
$reserveGB = if ($hostGB -le 8) { 2.5 } else { 4.0 }

if ($RamMb -le 0) {
  # Guest RAM tier from host RAM and count. With stripped AOSP and 512MB zRAM,
  # 768 MB is the sweet spot for 4 instances on 8GB host to prevent host paging.
  $RamMb = if ($Count -ge 4 -and $hostGB -le 8) { 768 }
           elseif ($hostGB -ge 32) { 2048 }
           elseif ($hostGB -ge 16) { 1536 }
           else { 1024 }
  Write-Step "Guest RAM not specified -> ${RamMb}MB (host has ${hostGB}GB)"
}

if ($Cores -le 0) {
  # Provisional: used only for the CPU-bound count check below. The final
  # value is recomputed once Count is known, so this must stay conservative.
  $Cores = [math]::Max(1, [math]::Min(2, [math]::Floor($cpuCores / 4)))
  Write-Step "Cores not specified -> provisional ${Cores} per instance (host has ${cpuCores} logical)"
}

$usableGB = $freeGB - $reserveGB
$maxByRam = if ($usableGB -gt 0) { [math]::Floor($usableGB * 1024 / $RamMb) } else { 0 }
$maxByCpu = [math]::Floor($cpuCores / $Cores)

# Only 2.4 GB free can happen legitimately (browser open, IDE, prior guest still
# shutting down). Refusing outright would make the script useless, so always
# permit at least one instance and let the warning speak for itself.
$maxCount = [math]::Max(1, [math]::Min($maxByRam, $maxByCpu))

if ($Count -le 0) {
  $Count = if ($Auto) { $maxCount } else { 2 }
  Write-Step "Count not specified -> ${Count} (RAM allows ~${maxByRam}, CPU allows ~${maxByCpu}, auto-suggest ${maxCount})"
}



# Final core allocation, now that Count is known:
# For 3+ instances, 1 core per instance prevents host core thrashing and runs
# smoothly with stripped AOSP & Intel UHD GPU rasterization.
if (-not $PSBoundParameters.ContainsKey('Cores') -or $Cores -le 0) {
  $Cores = if ($Count -ge 3) { 1 } else { 2 }
  Write-Step "Cores -> $Cores per instance (optimized for $Count instance(s) on $cpuCores host threads)"
}

Write-Step "Host: ${hostGB}GB total / ${freeGB}GB free / ${cpuCores} logical cores"
Write-Step "Plan: ${Count} instance(s) x ${RamMb}MB x ${Cores} core(s)"

if ($Count -gt $maxCount) {
  Write-Warn "Requested ${Count} but this host can sustain ~${maxCount} at ${RamMb}MB/${Cores} cores."
  Write-Warn "Expect reclaim thrash and OOM kills in the guests."
  if ($Force) {
    Write-Warn '-Force: proceeding anyway.'
  } elseif ($Auto) {
    Write-Warn "Clamping ${Count} -> ${maxCount} to fit this host."
    $Count = $maxCount
  } else {
    Write-Err 'Pass -Force to override, or -Auto to pick a count this host can sustain.'
    exit 1
  }
}

# ============================================================ per-instance plan
# Distinct AVD per instance is mandatory: two emulators sharing one AVD share
# one userdata disk and will corrupt each other's /data.
$plan = 1..$Count | ForEach-Object {
  $i = $_
  [pscustomobject]@{
    Index = $i
    Name  = '{0}-{1:d2}' -f $AvdPrefix, $i
    Port  = $BasePort + (2 * ($i - 1))
    Adb   = $BasePort + (2 * ($i - 1)) + 1
    Mac   = 'bc:72:b7:{0:x2}:{1:x2}:{2:x2}' -f (Get-Random -Minimum 10 -Maximum 250), (Get-Random -Minimum 10 -Maximum 250), $i
    Serial= "emulator-$($BasePort + (2 * ($i - 1)))"
  }
}
Write-Step 'Instance plan:'
$plan | Format-Table -AutoSize

# ============================================================ provision + launch
# Auto-detect active route interface DNS servers (the live route to the Internet)
$activeDnsList = @()
try {
  $activeRoute = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -EA SilentlyContinue |
                 Sort-Object RouteMetric | Select-Object -First 1
  if ($activeRoute) {
    $dnsFromIf = (Get-DnsClientServerAddress -InterfaceIndex $activeRoute.InterfaceIndex -AddressFamily IPv4 -EA SilentlyContinue).ServerAddresses
    if ($dnsFromIf) { $activeDnsList += @($dnsFromIf) }
    if ($activeRoute.NextHop -and $activeRoute.NextHop -ne '0.0.0.0') {
      $activeDnsList += @($activeRoute.NextHop)
    }
  }
} catch {}

if (-not $activeDnsList -or $activeDnsList.Count -eq 0) {
  try {
    $activeDnsList = @(Get-DnsClientServerAddress -AddressFamily IPv4 -EA SilentlyContinue |
      Where-Object { $_.ServerAddresses.Count -gt 0 } |
      Select-Object -ExpandProperty ServerAddresses) |
      Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' }
  } catch {}
}

if (-not $activeDnsList -or $activeDnsList.Count -eq 0) {
  $activeDnsList = @('192.168.1.1', '1.1.1.1', '8.8.8.8')
} else {
  $activeDnsList += @('1.1.1.1', '8.8.8.8')
}
$dnsArg = ($activeDnsList | Select-Object -Unique -First 3) -join ','
Write-Step "Network DNS configuration: $dnsArg"

# Ensure native Go network proxy daemon is running on 127.0.0.1:8880
$proxyPort = 8880
$proxyExe  = Join-Path $PSScriptRoot 'dofus-net-proxy.exe'
$proxyProc = Get-Process -Name 'dofus-net-proxy' -EA SilentlyContinue | Select-Object -First 1
if (-not $proxyProc -and (Test-Path $proxyExe)) {
  Write-Step "Starting native Go network proxy daemon on 127.0.0.1:$proxyPort..."
  Start-Process -FilePath $proxyExe -ArgumentList "-port $proxyPort -quiet" -WindowStyle Hidden
  Start-Sleep -Milliseconds 600
}

$started = @()
foreach ($p in $plan) {
  $avdDir = Join-Path $env:USERPROFILE ".android\avd\$($p.Name).avd"

  # Check if instance emulator process is already running on this port
  $existingProc = Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" -EA SilentlyContinue |
                  Where-Object { $_.CommandLine -match "-port\s+$($p.Port)\b" } | Select-Object -First 1

  if ($existingProc) {
    if ($Restart) {
      Write-Step "Restarting $($p.Name) (killing PID $($existingProc.ProcessId))..."
      & $Adb -s $p.Serial emu kill 2>$null | Out-Null
      Start-Sleep -Seconds 2
      $liveP = Get-Process -Id $existingProc.ProcessId -EA SilentlyContinue
      if ($liveP) { $liveP | Stop-Process -Force -EA SilentlyContinue }
      Start-Sleep -Seconds 1
    } else {
      Write-Ok "$($p.Name) is already running (PID: $($existingProc.ProcessId), Serial: $($p.Serial))"
      $started += [pscustomobject]@{
        Index = $p.Index; Name = $p.Name; Port = $p.Port; Adb = $p.Adb
        Mac = $p.Mac; Serial = $p.Serial; Pid = $existingProc.ProcessId; RamMb = $RamMb; Cores = $Cores
      }
      continue
    }
  }

  if (Test-Path $Golden) {
    if (-not (Test-Path $avdDir)) {
      Write-Warn "$($p.Name): AVD does not exist - run cluster-manager.ps1 -Action Create first."
      continue
    }
    $emuQcow2 = Join-Path $avdDir 'userdata-qemu.img.qcow2'
    $rawUd    = Join-Path $avdDir 'userdata-qemu.img'
    $qemuImg = Join-Path $SdkRoot 'emulator\qemu-img.exe'
    $goldenMaster = Join-Path $env:USERPROFILE ".android\avd\dofus-template.avd\userdata-golden.img"
    if (-not (Test-Path $goldenMaster) -and (Test-Path $Golden)) { $goldenMaster = $Golden }

    $needOverlay = $ResetOverlays -or (-not (Test-Path $emuQcow2)) -or ((Get-Item $emuQcow2 -EA SilentlyContinue).Length -eq 0) -or (-not (Test-Path $rawUd)) -or ((Get-Item $rawUd -EA SilentlyContinue).Length -eq 0)
    if ($needOverlay) {
      # Stop any orphan emulator process using this port before touching files
      $oldP = Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" -EA SilentlyContinue |
              Where-Object { $_.CommandLine -match "-port\s+$($p.Port)\b" } | Select-Object -First 1
      if ($oldP) {
        Stop-Process -Id $oldP.ProcessId -Force -EA SilentlyContinue
        Start-Sleep -Milliseconds 500
      }
      Get-ChildItem -Path $avdDir -Filter '*.lock' -EA SilentlyContinue | Remove-Item -Recurse -Force -EA SilentlyContinue
      Remove-Item $emuQcow2 -Force -EA SilentlyContinue
      Remove-Item $rawUd -Force -EA SilentlyContinue
      Remove-Item (Join-Path $avdDir 'userdata.qcow2') -Force -EA SilentlyContinue
      try {
        New-Item -ItemType HardLink -Path $rawUd -Target $goldenMaster -Force | Out-Null
      } catch {
        Copy-Item $goldenMaster $rawUd -Force
      }
      Push-Location $avdDir
      & $qemuImg create -f qcow2 -b "userdata-qemu.img" -F qcow2 "userdata-qemu.img.qcow2" | Out-Null
      Pop-Location
      Write-Ok "$($p.Name): QCOW2 differential overlay created (backing: $(Split-Path -Leaf $goldenMaster), fmt: qcow2)"
    } else {
      Write-Ok "$($p.Name): userdata overlay ready ($([math]::Round((Get-Item $emuQcow2).Length/1MB,0)) MB)"
    }
    Get-ChildItem -Path $avdDir -Filter '*.lock' -EA SilentlyContinue | Remove-Item -Recurse -Force -EA SilentlyContinue
  }

  # --- identity -------------------------------------------------------------
  $identFile = Join-Path $avdDir 'identity.json'
  $instSerial = ''
  $instAndroidId = ''
  if (Test-Path $identFile) {
    try {
      $idJson = Get-Content $identFile -Raw | ConvertFrom-Json
      $instSerial = $idJson.Serial
      $instAndroidId = $idJson.AndroidId
    } catch {}
  }
  if (-not $instSerial) {
    $chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    $instSerial = -join ((1..12) | ForEach-Object { $chars[(Get-Random -Minimum 0 -Maximum $chars.Length)] })
  }
  if (-not $instAndroidId) {
    $instAndroidId = -join ((1..16) | ForEach-Object { '{0:x}' -f (Get-Random -Minimum 0 -Maximum 16) })
  }
  if (Test-Path $avdDir) {
    @{ Serial = $instSerial; AndroidId = $instAndroidId; Mac = $p.Mac } | ConvertTo-Json | Set-Content -Path $identFile
  }

  # --- launch ---------------------------------------------------------------
  Write-Step "Starting $($p.Name)  console=$($p.Port) adb=$($p.Adb) mac=$($p.Mac) serial=$instSerial"
  $a = @(
    "-avd", $p.Name,
    "-port", $p.Port,
    "-gpu", $Gpu,
    "-memory", $RamMb,
    "-cores", $Cores,
    # Never -wipe-data: that would discard the seeded /data, which is the whole
    # point of the golden image.
    "-no-snapshot", "-no-snapshot-load", "-no-snapshot-save",
    "-no-audio", "-no-boot-anim", "-no-metrics", "-accel", "on",
    "-skip-adb-auth", "-no-location-ui", "-no-passive-gps",
    # Robust Multi-Instance Networking & Active Host DNS passthrough
    "-dns-server", $dnsArg
  )
  $instanceProxy = $Proxy
  $instProxyFile = Join-Path $avdDir 'proxy.txt'
  if (-not $instanceProxy -and (Test-Path $instProxyFile)) {
    $pContent = (Get-Content $instProxyFile -Raw -EA SilentlyContinue).Trim()
    if ($pContent) { $instanceProxy = $pContent }
  }
  if (-not $instanceProxy) {
    # Default to high-performance local Go network accelerator proxy
    $instanceProxy = "127.0.0.1:$proxyPort"
  }
  if ($instanceProxy) {
    $a += @("-http-proxy", $instanceProxy)
    Write-Step "  [$($p.Name)] Routing traffic via accelerator proxy: $instanceProxy"
  }
  # Standard Samsung Mobile Properties & High-Speed Boot Performance
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
    "qemu.hw.mainkeys=1",
    "gsm.sim.state=READY",
    "gsm.sim.operator.numeric=20801",
    "gsm.sim.operator.alpha=Orange",
    "gsm.network.type=LTE",
    "persist.sys.timezone=Europe/Paris",
    "persist.sys.country=FR",
    "persist.sys.language=fr",
    "persist.sys.locale=fr-FR",
    "net.dns1=10.0.2.3",
    "net.dns2=1.1.1.1",
    "net.dns3=8.8.8.8",
    "config.disable_animations=1",
    "dalvik.vm.verify-bytecode=false",
    "debug.sf.latch_unsignaled=1",
    "sys.use_fifo_ui=1",
    "ro.config.hw_quickpoweron=true"
  )
  foreach ($sp in $standardProps) {
    $a += @("-prop", $sp)
  }
  $qemuArgs = @("-m", "${RamMb}M", "-smp", "$Cores")
  if ($HostCpu) {
    $qemuArgs += @("-cpu", "host")
  }
  $a += @("-qemu") + $qemuArgs
  if ($Headless) { $a += '-no-window' }

  $proc = Start-Process -FilePath $Emu -ArgumentList $a -PassThru -WindowStyle Minimized
  $started += [pscustomobject]@{
    Index = $p.Index; Name = $p.Name; Port = $p.Port; Adb = $p.Adb
    Mac = $p.Mac; Serial = $p.Serial; Pid = $proc.Id; RamMb = $RamMb; Cores = $Cores
  }
  Write-Ok "$($p.Name) pid=$($proc.Id) serial=$($p.Serial)"

  # Stagger: smooth WHPX page table allocation without lock thrash
  if ($p.Index -lt $Count) { Start-Sleep -Milliseconds 2500 }
}

# ============================================================ window layout
if (-not $Headless -and -not $NoLayout -and $started.Count -gt 0) {
  Write-Step 'Placing windows...'
  Set-FarmLayout -Procs @($started | ForEach-Object { $_.Pid }) -MaximizeSingle -EdgeToEdge
}

Write-Step 'Launched. Instance map:'
$started | Format-Table -AutoSize
Write-Step "adb devices: & '$Adb' devices"

if ($AutoBoot) {
  Write-Step 'Waiting for instances to complete boot...'
  $bootSerials = @()
  $pending = [System.Collections.Generic.List[object]]::new($started)
  $deadline = [DateTime]::UtcNow.AddSeconds(180)
  while ($pending.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Seconds 3
    for ($idx = $pending.Count - 1; $idx -ge 0; $idx--) {
      $p = $pending[$idx]
      $b = (& $Adb -s $p.Serial shell getprop sys.boot_completed 2>$null) -replace "`r",''
      if ($b -match '1') {
        Write-Ok "$($p.Name) ($($p.Serial)) booted"
        $bootSerials += $p.Serial
        $pending.RemoveAt($idx)
      }
    }
  }
  if ($bootSerials.Count -gt 0) {
    Write-Step 'Applying per-boot runtime pass (zRAM, daemons, disguise)...'
    & (Join-Path $PSScriptRoot 'boot-instance.ps1') -Serials $bootSerials
  }
} else {
  Write-Step 'Per-boot runtime state (zRAM, daemon stops) is NOT in the golden image.'
  Write-Host "    .\scripts\boot-instance.ps1 -Serials $($started.Serial -join ',')" -ForegroundColor DarkGray
}

if ($Wait) {
  Write-Step "Farm running with $($started.Count) active instance(s). Press Ctrl+C to stop."
  while ($true) {
    Start-Sleep -Seconds 10
  }
}
