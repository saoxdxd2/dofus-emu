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
  [switch] $HostCpu,
  [switch] $Auto,
  [switch] $Headless,
  [switch] $NoLayout,
  [switch] $Force
)

$ErrorActionPreference = 'Stop'

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
  # Guest RAM tier from host RAM. Dofus Touch needs ~1024 MB to be comfortable;
  # 768 MB runs but saturates zRAM, and anything below that OOMs on startup.
  $RamMb = if ($hostGB -ge 32) { 2048 }
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

# Final core allocation, now that Count is known: split the host cores across
# the instances but never starve the host and never exceed 4 per guest.
if ($Cores -le 2 -and -not $PSBoundParameters.ContainsKey('Cores')) {
  $Cores = [math]::Max(1, [math]::Min(4, [math]::Floor(($cpuCores - 2) / $Count)))
  Write-Step "Cores -> ${Cores} per instance (${cpuCores} host cores across ${Count} instance(s))"
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
    Mac   = '52:54:00:00:00:{0:x2}' -f $i
    Serial= "emulator-$($BasePort + (2 * ($i - 1)))"
  }
}
Write-Step 'Instance plan:'
$plan | Format-Table -AutoSize

# ============================================================ provision + launch
$started = @()
foreach ($p in $plan) {
  $avdDir = Join-Path $env:USERPROFILE ".android\avd\$($p.Name).avd"

  # --- seed /data from the golden image -----------------------------------
  # The golden is a self-contained qcow2 and must land at the path the emulator
  # actually opens. A stale overlay would silently boot yesterday's state.
  #
  # The raw 6 GB backing file MUST exist first: on a fresh AVD the emulator
  # discards a seeded overlay that has no backing file and reformats /data,
  # which is the "first boot ignores the golden image" bug. Created sparse, so
  # it consumes no disk until written.
  if (Test-Path $Golden) {
    if (-not (Test-Path $avdDir)) {
      Write-Warn "$($p.Name): AVD does not exist - run cluster-manager.ps1 -Action Create first."
      continue
    }
    $targetQcow2 = Join-Path $avdDir 'userdata.qcow2'
    $emuQcow2 = Join-Path $avdDir 'userdata-qemu.img.qcow2'
    if (-not (Test-Path $targetQcow2)) {
      $qemuImg = Join-Path $SdkRoot 'emulator\qemu-img.exe'
      $backingFmt = 'raw'
      $imgInfo = & $qemuImg info "$Golden" 2>&1 | Out-String
      if ($imgInfo -match 'file format:\s*qcow2') { $backingFmt = 'qcow2' }
      & $qemuImg create -f qcow2 -b "$Golden" -F $backingFmt "$targetQcow2"
      Remove-Item $emuQcow2 -Force -EA SilentlyContinue
      try {
        New-Item -ItemType HardLink -Path $emuQcow2 -Target $targetQcow2 -Force | Out-Null
      } catch {
        Copy-Item $targetQcow2 $emuQcow2 -Force
      }
      Write-Ok "$($p.Name): QCOW2 differential overlay created"
    } else {
      Write-Ok "$($p.Name): QCOW2 overlay already present"
    }
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
    "-no-snapshot", "-no-audio", "-no-boot-anim", "-no-metrics", "-accel", "on"
  )
  if (Test-Path (Join-Path $avdDir 'userdata.qcow2')) {
    $a += @("-data", (Join-Path $avdDir 'userdata'))
  }
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

  # Stagger: simultaneous boots on a small host distort memory measurement and
  # slow both to the point of looking like a hang.
  if ($p.Index -lt $Count) { Start-Sleep -Seconds 15 }
}

# ============================================================ window layout
if (-not $Headless -and -not $NoLayout -and $started.Count -gt 0) {
  Write-Step 'Placing windows...'
  Set-FarmLayout -Procs @($started | ForEach-Object { $_.Pid }) -MaximizeSingle
}

Write-Step 'Launched. Instance map:'
$started | Format-Table -AutoSize
Write-Step "adb devices: & '$Adb' devices"
Write-Step 'Per-boot runtime state (zRAM, daemon stops) is NOT in the golden image.'
Write-Host "    .\scripts\boot-instance.ps1 -Serial <serial>" -ForegroundColor DarkGray
