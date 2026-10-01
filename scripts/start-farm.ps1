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
  if (Test-Path $Golden) {
    if (-not (Test-Path $avdDir)) {
      Write-Warn "$($p.Name): AVD does not exist - run cluster-manager.ps1 -Action Create first."
      continue
    }
    $dst = Join-Path $avdDir 'userdata-qemu.img.qcow2'
    if ((Test-Path $dst) -and ((Get-Item $dst).Length -eq (Get-Item $Golden).Length)) {
      Write-Ok "$($p.Name): userdata already seeded"
    } else {
      Remove-Item $dst -Force -EA SilentlyContinue
      Copy-Item $Golden $dst -Force
      Write-Ok "$($p.Name): userdata seeded from golden image"
    }
  }

  # --- launch ---------------------------------------------------------------
  Write-Step "Starting $($p.Name)  console=$($p.Port) adb=$($p.Adb) mac=$($p.Mac)"
  $a = @(
    "-avd", $p.Name,
    "-port", $p.Port,
    "-gpu", $Gpu,
    "-memory", $RamMb,
    "-cores", $Cores,
    # -accel accepts only on|off|auto in emulator 37.x (not "hvm").
    "-no-snapshot", "-no-audio", "-no-boot-anim", "-no-metrics", "-accel", "on"
  )
  # NOTE: -qemu must come LAST and everything emulator-level must precede it;
  # flags after -qemu are handed to qemu-system-x86_64 directly. -smp/-cpu are
  # QEMU flags, so they belong on the far side of -qemu.
  $qemuArgs = @("-m", "${RamMb}M")
  # Pin the vCPU count explicitly so the guest cannot drift from the budget.
  $qemuArgs += @("-smp", "$Cores")
  if ($HostCpu) {
    # Expose the real host ISA (AVX2/SSE4.2/BMI2 on this class of CPU) so the
    # guest JIT can emit those instructions instead of a baseline subset.
    # Without it QEMU emulates a generic CPU and every vector op is a helper
    # function call, which is expensive for the WebView's JS/V8 work.
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
