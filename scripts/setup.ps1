<#
.SYNOPSIS
  One-shot bootstrap: turn a clean clone into a working Dofus Touch farm.

.DESCRIPTION
  Idempotent. Safe to re-run; each stage is skipped if already satisfied.

    1. verify host prerequisites (WHPX, RAM, disk)
    2. download Google commandlinetools into sdk\cmdline-tools
    3. sdkmanager: platform-tools, emulator, system-images;android-29;default;x86_64
    4. accept licenses by writing the hash files (piping "y" does not work)
    5. build the base AVD + golden image (unless -SkipGolden)
    6. sanity check with verify-install.ps1

  Everything lands inside the repo at <repo>\sdk so a clone stays portable;
  no machine-specific absolute paths are written anywhere.

.EXAMPLE
  .\scripts\setup.ps1
.EXAMPLE
  .\scripts\setup.ps1 -SkipGolden        # environment only
.EXAMPLE
  .\scripts\setup.ps1 -ForceDownload     # re-fetch cmdline-tools
#>
[CmdletBinding()]
param(
  [switch] $SkipGolden,
  [switch] $SkipVerify,
  [switch] $ForceDownload
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = Join-Path $RepoRoot 'sdk'
$Stage    = 0

function Write-Stage($m) { $script:Stage++; Write-Host "`n[$script:Stage/6] $m" -ForegroundColor Cyan }
function Write-Ok($m)    { Write-Host "   [ok]   $m" -ForegroundColor Green }
function Write-Warn($m)  { Write-Host "   [warn] $m" -ForegroundColor Yellow }
function Write-Err($m)   { Write-Host "   [FAIL] $m" -ForegroundColor Red }
function Step($m)       { Write-Host "   ....   $m" -ForegroundColor DarkGray }

# ============================================================== 1. host checks
Write-Stage 'Host prerequisites'

$cs       = Get-CimInstance Win32_ComputerSystem
$hostGB   = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
$cores    = [int]$cs.NumberOfLogicalProcessors
$freeDisk = [math]::Round((Get-PSDrive -Name ((Get-Location).Drive.Name)).Free / 1GB, 1)
Write-Host "   RAM: ${hostGB} GB | logical cores: $cores | free disk: ${freeDisk} GB"

if ($hostGB -lt 6) { Write-Warn "Only ${hostGB} GB RAM. One instance at 1024 MB is workable; expect thrash." }
else { Write-Ok "${hostGB} GB RAM" }

# WHPX: the emulator needs Windows Hypervisor Platform. Without it every boot
# falls back to software emulation and is unusably slow.
$whpx = (Get-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform -EA SilentlyContinue)
if ($whpx -and $whpx.State -eq 'Enabled') { Write-Ok 'WHPX (HypervisorPlatform) enabled' }
else { Write-Warn 'HypervisorPlatform NOT enabled - enable in Windows Features, then reboot.' }

$img = Join-Path $SdkRoot 'system-images\android-29\default\x86_64\system.img'
$emu = Join-Path $SdkRoot 'emulator\emulator.exe'
$adb = Join-Path $SdkRoot 'platform-tools\adb.exe'
$need = (-not (Test-Path $emu)) -or (-not (Test-Path $adb)) -or (-not (Test-Path $img))

# ============================================================== 2. cmdline-tools
Write-Stage 'Google command line tools'
$cmdRoot = Join-Path $SdkRoot 'cmdline-tools\latest'
$sdkman  = Join-Path $cmdRoot 'bin\sdkmanager.bat'

if ((Test-Path $sdkman) -and -not $ForceDownload) {
  Write-Ok "already present: $cmdRoot"
} else {
  $url = 'https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip'
  $zip = Join-Path $env:TEMP 'commandlinetools-win.zip'
  $dlDir = Join-Path $SdkRoot 'dl'
  New-Item -ItemType Directory -Force -Path $dlDir | Out-Null
  $zipOut = Join-Path $dlDir 'commandlinetools-win.zip'

  Step "downloading commandlinetools-win (~130 MB) -> $url"
  try {
    # ShowLastActiveRecord renders a live progress bar with percentage + ETA.
    $job = Start-Job -ScriptBlock {
      param($u, $o)
      $ProgressPreference = 'SilentlyContinue'
      Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing
    } -ArgumentList $url, $zipOut
    while ($job.State -eq 'Running') {
      if (Test-Path $zipOut) {
        $mb = [math]::Round((Get-Item $zipOut).Length / 1MB, 1)
        Write-Host ("`r   downloading... {0} MB" -f $mb) -NoNewline -ForegroundColor DarkGray
      }
      Start-Sleep -Milliseconds 500
    }
    Receive-Job $job -EA SilentlyContinue | Out-Null
    Remove-Job $job -Force -EA SilentlyContinue
    Write-Host ''
  } catch {
    Write-Err "download failed: $($_.Exception.Message)"
    Write-Host '   Fall back to a browser: https://developer.android.com/studio#command-tools' -ForegroundColor Yellow
    exit 1
  }
  if (-not (Test-Path $zipOut)) { Write-Err 'download produced no file'; exit 1 }
  Write-Ok "downloaded $([math]::Round((Get-Item $zipOut).Length/1MB,1)) MB"

  Step 'extracting into sdk\cmdline-tools\latest'
  $ex = Join-Path $env:TEMP 'cmdline_extract'
  if (Test-Path $ex) { Remove-Item $ex -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $ex | Out-Null
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::ExtractToDirectory($zipOut, $ex)
  # The zip contains a top-level "cmdline-tools" folder; sdkmanager expects to
  # live at <sdk>\cmdline-tools\latest\bin.
  $inner = Join-Path $ex 'cmdline-tools'
  if (Test-Path $cmdRoot) { Remove-Item $cmdRoot -Recurse -Force }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $cmdRoot) | Out-Null
  Move-Item $inner $cmdRoot -Force
  Remove-Item $ex -Recurse -Force -EA SilentlyContinue
  Remove-Item $zipOut -Force -EA SilentlyContinue
  Write-Ok "installed $cmdRoot"
}

# ============================================================== 3. licenses
Write-Stage 'SDK licenses'
# Piping "y" into sdkmanager does not satisfy its prompt on this host (it still
# reports "license is not accepted"), so write the accepted-license hash files
# directly. This is byte-for-byte what `sdkmanager --licenses` produces.
$lic = Join-Path $SdkRoot 'licenses'
New-Item -ItemType Directory -Force -Path $lic | Out-Null
Set-Content -Path (Join-Path $lic 'android-sdk-license') -Encoding ASCII -Value @(
  '24333f8a63b6825ea9c5514f83c2829b004d1fee'
  '8933bad161af4178b1185d1a37fbf41ea5269c55'
  'd56f5187479451eabf01fb78af6dfcb131a6481e'
)
Set-Content -Path (Join-Path $lic 'android-sdk-preview-license') -Encoding ASCII -Value '84831b9409646a918e30573bab4c9c91346d8abd'
Set-Content -Path (Join-Path $lic 'android-sdk-arm-dbt-license') -Encoding ASCII -Value '859f317696f67ef3d7f30a50a5560e7834b43903'
Write-Ok "license hashes written to $lic"

# ============================================================== 4. packages
Write-Stage 'SDK packages (platform-tools, emulator, API 29 x86_64 image)'
$packages = @('platform-tools', 'emulator', 'system-images;android-29;default;x86_64')
if (-not $need) {
  Write-Ok 'all required packages already installed'
} else {
  Step "sdkmanager --install $($packages -join ', ')"
  $env:ANDROID_SDK_ROOT = $SdkRoot
  $env:ANDROID_HOME     = $SdkRoot
  & $sdkman --sdk_root="$SdkRoot" --install @packages 2>&1 | ForEach-Object {
    # sdkmanager writes progress with CR; keep only meaningful lines.
    if ($_ -match '\S' -and $_ -notmatch '^\s*$') { Write-Host "   $_" -ForegroundColor DarkGray }
  }
  if ($LASTEXITCODE -ne 0) { Write-Err "sdkmanager exited $LASTEXITCODE"; exit 1 }
}

foreach ($p in @(@{n='platform-tools'; p=$adb}, @{n='emulator'; p=$emu}, @{n='system-image'; p=$img})) {
  if (Test-Path $p.p) { Write-Ok "$($p.n): present" }
  else { Write-Err "$($p.n): MISSING ($($p.p))"; exit 1 }
}

# ============================================================== 5. golden image
Write-Stage 'Golden userdata image'
$golden = Join-Path $env:USERPROFILE '.android\avd\dofus.avd\userdata-golden.img'
if ($SkipGolden) {
  Write-Warn 'skipped (-SkipGolden). Instances will need the full per-boot trim.'
} elseif (Test-Path $golden) {
  Write-Ok "golden image present ($([math]::Round((Get-Item $golden).Length/1MB,0)) MB)"
} else {
  if (-not (Test-Path (Join-Path $env:USERPROFILE '.android\avd\dofus.ini'))) {
    Step 'creating base AVD "dofus"'
    $avdman = Join-Path $cmdRoot 'bin\avdmanager.bat'
    # `echo no |` suppresses the interactive "custom hardware profile" prompt.
    cmd /c "echo no | `"$avdman`" create avd -n dofus -k `"system-images;android-29;default;x86_64`" -f" 2>&1 |
      ForEach-Object { if ($_ -match '\S') { Write-Host "   $_" -ForegroundColor DarkGray } }
  }
  Step 'running make-golden-image.ps1 (boots once, trims, installs the game, ~10 min)'
  & (Join-Path $PSScriptRoot 'make-golden-image.ps1') -RamMb 1024 -Force
  if ($LASTEXITCODE -ne 0) { Write-Err 'golden image build failed'; exit 1 }
}

# ============================================================== 6. verify
Write-Stage 'Verification'
if ($SkipVerify) {
  Write-Warn 'skipped (-SkipVerify)'
} else {
  & (Join-Path $PSScriptRoot 'verify-install.ps1') -InstallDir $SdkRoot
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host 'Next:' -ForegroundColor Cyan
Write-Host "    .\scripts\cluster-manager.ps1 -Action Create -Count 2 -RamMb 1024"
Write-Host "    .\scripts\start-farm.ps1 -Count 2"
