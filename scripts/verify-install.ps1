<#
.SYNOPSIS
  Verify this machine is ready to run the Dofus Touch instance farm.

.DESCRIPTION
  Read-only diagnostic. Safe to run on any machine; changes nothing. Useful for
  a friend to run before/after installing, or for you to paste the output when
  asking for help.

  Checks, in order of "if this fails nothing works":
    1. CPU virtualization (VT-x) enabled in firmware
    2. Windows Hypervisor Platform feature enabled
    3. Android SDK components present (emulator, adb, API 29 image)
    4. AVDs created and configured with GPU mode
    5. Emulator hardware acceleration actually active
    6. Free RAM vs. the requested instance count

.EXAMPLE
  .\scripts\verify-install.ps1
  .\scripts\verify-install.ps1 -Count 4 -RamMb 1536
#>
[CmdletBinding()]
param(
  # Default to the in-repo SDK (or $env:ANDROID_SDK_ROOT), never a machine-
  # specific absolute path, so a clone is portable.
  [string] $InstallDir = '',
  [int]    $Count = 1,
  [int]    $RamMb = 1536
)

if (-not $InstallDir) {
  $repo = Split-Path -Parent $PSScriptRoot
  $InstallDir = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $repo 'sdk' }
}

$ErrorActionPreference = 'Continue'
$fail = 0
$warn = 0

function Ok($m)   { Write-Host "  [PASS] $m" -ForegroundColor Green }
function Bad($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red;  $script:fail++ }
function Warn($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow; $script:warn++ }
function Head($m) { Write-Host "`n$m" -ForegroundColor Cyan }

Write-Host "Dofus Touch farm - environment check" -ForegroundColor White

Head '1. CPU virtualization / hypervisor'
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$cs  = Get-CimInstance Win32_ComputerSystem
# IMPORTANT: once a hypervisor is running it MASKS the VT-x/SLAT CPU flags, so
# Win32_Processor.VirtualizationFirmwareEnabled reports False and SLAT reports
# False even though virtualization is working perfectly. systeminfo says the
# same thing in prose: "A hypervisor has been detected. Features required for
# Hyper-V will not be displayed."
#
# So when HypervisorPresent is True we must NOT treat the False flags as a BIOS
# fault - that is a false alarm. The authoritative test is `emulator
# -accel-check`, run in section 5.
if ($cs.HypervisorPresent) {
  Ok 'hypervisor is running (CPU flags are masked by it - this is normal)'
} elseif ($cpu.VirtualizationFirmwareEnabled) {
  Ok "VT-x enabled ($($cpu.Name.Trim()))"
} else {
  Bad 'no hypervisor running and VT-x reports disabled - enable Intel VT-x/AMD-V in firmware, then enable HypervisorPlatform'
  Bad '  without this the emulator exits: "x86_64 emulation currently requires hardware acceleration!"'
}

Head '2. Windows hypervisor'
foreach ($f in @('HypervisorPlatform','VirtualMachinePlatform')) {
  $s = (Get-WindowsOptionalFeature -Online -FeatureName $f -ErrorAction SilentlyContinue).State
  if ($s -eq 'Enabled') { Ok "$f enabled" }
  else { Bad "$f is $s - enable with: dism /online /enable-feature /featurename:$f /all /norestart  (then reboot)" }
}

Head '3. Android SDK components'
$items = @{
  'emulator.exe'      = "$InstallDir\emulator\emulator.exe"
  'adb.exe'           = "$InstallDir\platform-tools\adb.exe"
  'system.img'        = "$InstallDir\system-images\android-29\default\x86_64\system.img"
  'avdmanager.bat'    = "$InstallDir\cmdline-tools\latest\bin\avdmanager.bat"
  'emulator pkg.xml'  = "$InstallDir\emulator\package.xml"
}
foreach ($k in $items.Keys) {
  if (Test-Path $items[$k]) { Ok "$k present" }
  else { Bad "$k missing -> run install.ps1" }
}

Head '4. AVDs'
$avdHome = Join-Path $env:USERPROFILE '.android\avd'
$avds = Get-ChildItem $avdHome -Filter '*.ini' -ErrorAction SilentlyContinue
if ($avds) {
  foreach ($a in $avds) {
    $n = $a.BaseName
    $cfg = Join-Path $avdHome "$n.avd\config.ini"
    if (Test-Path $cfg) {
      $c = Get-Content $cfg
      # Guard against a missing key: Select-String returns nothing and
      # .Matches.Groups[1] on $null throws "Cannot index into a null array".
      # NOTE: the regex allows whitespace around '=' because the AVD files this
      # host actually produced use "hw.gpu.mode = host", not "hw.gpu.mode=host".
      # A strict '^key=(.+)$' silently reports every setting as unset.
      $gpuM = ($c | Select-String '^\s*hw\.gpu\.mode\s*=\s*(.+?)\s*$')
      $ramM = ($c | Select-String '^\s*hw\.ramSize\s*=\s*(.+?)\s*$')
      $gpu = if ($gpuM) { $gpuM.Matches[0].Groups[1].Value } else { '<unset>' }
      $ram = if ($ramM) { $ramM.Matches[0].Groups[1].Value } else { '<unset>' }
      if ($gpu -eq 'host') { Ok "$n : gpu=host ram=$ram" }
      elseif ($gpu -eq '<unset>') { Warn "$n : hw.gpu.mode not set in config.ini (run install.ps1)" }
      else { Warn "$n : gpu='$gpu' (want 'host' for WebGL; 'swiftshader_indirect' rasterises on CPU)" }
    }
  }
} else { Warn "no AVDs found in $avdHome -> run install.ps1" }

Head '5. Emulator acceleration'
$emu = "$InstallDir\emulator\emulator.exe"
if (Test-Path $emu) {
  $out = (& $emu -accel-check 2>&1 | Out-String)
  # Check the failure text first: "hypervisor driver is NOT installed" contains
  # the substring "installed", so a naive positive-first match reports a broken
  # machine as healthy.
  if ($out -match 'not installed') { Bad 'emulator hypervisor not active - reboot after enabling HypervisorPlatform' }
  elseif ($out -match 'accel:\s*1|installed') { Ok 'hardware acceleration active' }
  else { Warn ($out.Trim()) }
} else { Bad 'emulator.exe missing' }

Head '6. Memory budget'
$os = Get-CimInstance Win32_OperatingSystem
$freeGB = [math]::Round($os.FreePhysicalMemory/1MB,1)
$totGB  = [math]::Round($os.TotalVisibleMemorySize/1MB,1)
$needGB = [math]::Round(($Count*$RamMb)/1024 + 1.5, 1)
Ok "RAM: ${freeGB}GB free of ${totGB}GB total"
Ok "$Count x ${RamMb}MB guests needs ~${needGB}GB including Windows"
if ($needGB -gt $freeGB) {
  Warn "over budget by $([math]::Round($needGB-$freeGB,1))GB - reduce -Count, or expect thrash/OOM kills"
}

Head 'Result'
if ($fail -eq 0) { Write-Host "  READY ($warn warning(s))" -ForegroundColor Green }
else { Write-Host "  $fail blocking issue(s), $warn warning(s)" -ForegroundColor Red }

Write-Host ""
Write-Host "Start an instance with:" -ForegroundColor Gray
Write-Host "  .\scripts\instances.ps1" -ForegroundColor Gray
