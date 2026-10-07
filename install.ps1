<#
.SYNOPSIS
  Dofus Touch instance farm - one-shot installer.

.DESCRIPTION
  Installs everything needed to run the farm on a fresh Windows x64 machine:
    1. Host prerequisites (hypervisor features; may require a reboot)
    2. Android SDK: cmdline-tools, platform-tools, emulator, API 29 x86_64 image
    3. The AVD(s), configured for host-GPU rendering
    4. SDK package metadata that manual installs otherwise miss

  Designed to be handed to a friend as a single folder: it downloads only
  public Google artifacts, needs no credentials, and is idempotent - re-running
  it skips anything already present.

.PARAMETER InstallDir
  Where to put the Android SDK. Default C:\android-sdk

.PARAMETER AvdName
  AVD name. Default 'dofus'

.PARAMETER RamMb
  Guest RAM per AVD. Default 1536.

.PARAMETER Count
  Number of instance AVDs (dofus-01 .. dofus-0N). Default 1.

.PARAMETER SkipHostPrereqs
  Skip hypervisor/network changes (use when already prepared).

.PARAMETER RebootWhenNeeded
  Actually reboot if the hypervisor was just enabled. Default: only report.

.EXAMPLE
  .\install.ps1
  .\install.ps1 -Count 4 -RamMb 1536
#>
[CmdletBinding()]
param(
  [string] $InstallDir = '',
  [string] $AvdName    = 'dofus',
  [int]    $RamMb      = 1536,
  [int]    $Cores      = 2,
  [int]    $Count      = 1,
  [switch] $SkipHostPrereqs,
  [switch] $RebootWhenNeeded,
  [switch] $SdkOnly
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
# Default the SDK inside the repo so the project is fully portable and can be
# copied to another machine without reinstalling anything.
if (-not $InstallDir) { $InstallDir = Join-Path $RepoRoot 'sdk' }
$Sdk      = $InstallDir

function Write-Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "    [ok]   $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "    [warn] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "    [FAIL] $m" -ForegroundColor Red }
function Write-Info($m) { Write-Host "    $m" -ForegroundColor Gray }

$script:RebootNeeded = $false
$script:Failed       = @()

function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
  Write-Host "`nAdministrator privileges are required. Re-launching elevated..." -ForegroundColor Yellow
  $elev = @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$($MyInvocation.MyCommand.Path)`"")
  if ($InstallDir) { $elev += @('-InstallDir', $InstallDir) }
  if ($AvdName    -ne 'dofus')        { $elev += @('-AvdName', $AvdName) }
  if ($RamMb      -ne 1536)           { $elev += @('-RamMb', $RamMb) }
  if ($Cores      -ne 2)              { $elev += @('-Cores', $Cores) }
  if ($Count      -ne 1)              { $elev += @('-Count', $Count) }
  if ($SkipHostPrereqs)              { $elev += '-SkipHostPrereqs' }
  if ($RebootWhenNeeded)             { $elev += '-RebootWhenNeeded' }
  if ($SdkOnly)                      { $elev += '-SdkOnly' }
  try { Start-Process powershell -Verb RunAs -ArgumentList $elev -Wait }
  catch { Write-Err "Elevation failed: $($_.Exception.Message)" }
  exit 0
}

Write-Host @"
=========================================================
  Dofus Touch Instance Farm - installer
  SDK : $Sdk
  AVD : $AvdName (x$Count, ${RamMb}MB, $Cores cores)
=========================================================
"@ -ForegroundColor White


# =========================================================== 1. host prereqs
if (-not $SkipHostPrereqs) {
  Write-Step '1/5  Host prerequisites'

  # --- CPU virtualization -------------------------------------------------
  # The emulator requires a hypervisor. Without it the AVD refuses to start
  # with "x86_64 emulation currently requires hardware acceleration!".
  # NOTE: when a hypervisor is already running it MASKS the VT-x/SLAT CPU
  # flags, so Win32_Processor.VirtualizationFirmwareEnabled reads False even
  # though virtualization is fine. Only treat that as a BIOS fault when no
  # hypervisor is present.
  $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
  $cs  = Get-CimInstance Win32_ComputerSystem
  if ($cs.HypervisorPresent) {
    Write-Ok 'hypervisor already active (CPU flags masked by it - normal)'
  } elseif ($cpu.VirtualizationFirmwareEnabled) {
    Write-Ok 'CPU virtualization (VT-x/SLAT) available'
  } else {
    Write-Err 'CPU virtualization DISABLED in firmware (VT-x). Enable it in BIOS.'
    $script:Failed += 'BIOS virtualization disabled'
  }

  foreach ($feat in @('HypervisorPlatform','VirtualMachinePlatform')) {
    $state = (Get-WindowsOptionalFeature -Online -FeatureName $feat -ErrorAction SilentlyContinue).State
    if ($state -eq 'Enabled') { Write-Ok "$feat = Enabled" }
    else {
      Write-Info "Enabling Windows feature: $feat"
      & dism.exe /online /enable-feature /featurename:$feat /all /norestart 2>&1 | Out-Null
      if ($LASTEXITCODE -eq 0) {
        Write-Ok "$feat enabled"
        $script:RebootNeeded = $true
      } else {
        Write-Err "$feat could not be enabled"
        $script:Failed += $feat
      }
    }
  }
  if ($script:RebootNeeded) {
    Write-Warn 'Hypervisor features were just enabled. A REBOOT is required before'
    Write-Warn 'the emulator will start. Re-run this installer after rebooting.'
  }

  # --- network tuning (best effort, optional) -----------------------------
  # Not required to run, but a Realtek card that band-steers onto 2.4 GHz costs
  # ~5-10x throughput (measured here: 423 KB/s on 2.4 GHz vs 433 Mbps link on
  # 5 GHz). Roaming Aggressiveness=65 reliably lands the association on 5 GHz.
  Write-Info 'Checking Wi-Fi band tuning (optional)...'
  $wifi = Get-NetAdapter -ErrorAction SilentlyContinue |
          Where-Object { $_.PhysicalMediaType -eq 'Native 802.11' -or $_.Name -match 'Wi-Fi|WLAN' } |
          Select-Object -First 1
  if ($wifi) {
    Write-Info "adapter: $($wifi.Name)"
    $prop = Get-NetAdapterAdvancedProperty -Name $wifi.Name -ErrorAction SilentlyContinue |
            Where-Object { $_.RegistryKeyword -eq 'RegROAMSensitiveLevel' }
    if ($prop -and ($prop.ValidRegistryValues -contains 65)) {
      if ($prop.RegistryValue -ne 65) {
        Set-NetAdapterAdvancedProperty -Name $wifi.Name -RegistryKeyword 'RegROAMSensitiveLevel' -RegistryValue 65 -ErrorAction SilentlyContinue
        $now = (Get-NetAdapterAdvancedProperty -Name $wifi.Name |
                Where-Object { $_.RegistryKeyword -eq 'RegROAMSensitiveLevel' }).DisplayValue
        if ($now -match 'Highest') { Write-Ok 'Roaming Aggressiveness = Highest (favours 5 GHz)' }
        else { Write-Warn "could not set roaming level (now: $now)" }
      } else { Write-Ok 'Roaming Aggressiveness already Highest' }
    } else {
      Write-Info 'adapter has no roaming-aggressiveness setting; skipping'
    }
    # PreferBand is deliberately NOT changed: value 2 ("5G first") is already the
    # default and already correct. Only 0/1/2 are valid for that keyword.
    Write-Info 'PreferBand left at default (2 = 5G first, already correct)'
  } else {
    Write-Info 'no Wi-Fi adapter found; skipping network tuning'
  }
}


# ============================================================ 2. SDK install
Write-Step '2/5  Android SDK (cmdline-tools, platform-tools, emulator, API 29 image)'

if (-not (Test-Path $Sdk)) { New-Item -ItemType Directory -Force -Path $Sdk | Out-Null }

# --- cmdline-tools ---------------------------------------------------------
# Needed for avdmanager. Installed by direct download because sdkmanager
# throttled heavily on this class of machine; the zip is ~150 MB and involves
# no license-prompt interaction.
$cmdlineBat = Join-Path $Sdk 'cmdline-tools\latest\bin\sdkmanager.bat'
if (Test-Path $cmdlineBat) {
  Write-Ok 'cmdline-tools already installed'
} else {
  $cmdlineZip = Join-Path $env:TEMP 'commandlinetools.zip'
  if (-not (Test-Path $cmdlineZip)) {
    Write-Info 'downloading cmdline-tools (~150 MB)...'
    & curl.exe -L -C - -s -f --retry 5 -o $cmdlineZip `
      'https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip'
    if ($LASTEXITCODE -ne 0) { Write-Err 'cmdline-tools download failed'; $script:Failed += 'cmdline-tools dl' }
  }
  if (Test-Path $cmdlineZip) {
    Write-Info 'unpacking cmdline-tools...'
    $tmp = Join-Path $env:TEMP 'dl-cmdline'
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive -Path $cmdlineZip -DestinationPath $tmp -Force
    $dest = Join-Path $Sdk 'cmdline-tools\latest'
    if (Test-Path (Join-Path $dest 'bin')) { Remove-Item $dest -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Move-Item -Path (Join-Path $tmp 'cmdline-tools\*') -Destination $dest -Force
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $cmdlineBat) { Write-Ok 'cmdline-tools installed' }
    else { Write-Err 'cmdline-tools incomplete'; $script:Failed += 'cmdline-tools' }
  }
}

# --- SDK licenses ----------------------------------------------------------

# --- platform-tools / emulator / system image -----------------------------
# Delegated to scripts\download-artifacts.bat, which downloads with aria2c
# (16 streams) and unpacks into the correct SDK layout. It is byte-size
# verified, which matters because a truncated zip previously produced a
# confusing "emulator package must be installed" failure.
$emuExe = Join-Path $Sdk 'emulator\emulator.exe'
$adbExe = Join-Path $Sdk 'platform-tools\adb.exe'
$sysImg = Join-Path $Sdk 'system-images\android-29\default\x86_64\system.img'
if ((Test-Path $emuExe) -and (Test-Path $adbExe) -and (Test-Path $sysImg)) {
  Write-Ok 'platform-tools, emulator and API 29 image already installed'
} else {
  $dl = Join-Path $RepoRoot 'scripts\download-artifacts.bat'
  if (-not (Test-Path $dl)) {
    Write-Err "missing $dl"
    $script:Failed += 'download-artifacts.bat missing'
  } else {
    Write-Info 'downloading SDK components (~890 MB total, 16-way parallel)...'
    $env:ANDROID_SDK_ROOT = $Sdk
    $out = & cmd.exe /c "`"$dl`" `"$Sdk`"" 2>&1
    if ($LASTEXITCODE -ne 0) {
      Write-Info ($out | Out-String)
    }
    if ((Test-Path $emuExe) -and (Test-Path $adbExe) -and (Test-Path $sysImg)) {
      Write-Ok 'SDK components installed'
    } else {
      Write-Err 'SDK component install incomplete'
      $script:Failed += 'SDK components'
    }
  }
}

# --- emulator package.xml --------------------------------------------------
# The official emulator zip ships source.properties but NOT package.xml, which
# sdkmanager would normally generate. Because we install by unzipping, that
# metadata is absent and avdmanager then fails with:
#   Error: "emulator" package must be installed!
# We synthesise it. The XML must match the SDK schema exactly (full namespace
# list + a <license> node), otherwise it is rejected as "Invalid package.xml".
$emuPkg = Join-Path $Sdk 'emulator\package.xml'
$emuSrc = Join-Path $Sdk 'emulator\source.properties'
# NOTE: do NOT write this as
#   if ((Test-Path (Join-Path ...)) -and -not (Test-Path ...))
# PowerShell parses `(Join-Path ...) -and -not` as ARGUMENTS to Join-Path and
# fails with "A parameter cannot be found that matches parameter name 'and'".
# Precomputing the path into a variable avoids the ambiguity.
if ((Test-Path $emuSrc) -and (-not (Test-Path $emuPkg))) {
  Write-Info 'creating missing emulator package.xml ...'
  $ns = 'xmlns:ns2="http://schemas.android.com/repository/android/common/02" ' +
        'xmlns:ns3="http://schemas.android.com/repository/android/common/01" ' +
        'xmlns:ns4="http://schemas.android.com/repository/android/generic/01" ' +
        'xmlns:ns5="http://schemas.android.com/repository/android/generic/02" ' +
        'xmlns:ns9="http://schemas.android.com/sdk/android/repo/repository2/01" ' +
        'xmlns:ns10="http://schemas.android.com/sdk/android/repo/repository2/02" ' +
        'xmlns:ns11="http://schemas.android.com/sdk/android/repo/repository2/03"'
  $xml  = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
  $xml += "<ns2:repository $ns>"
  $xml += '<license id="license-24333f" type="text"/>'
  $xml += '<localPackage path="emulator" obsolete="false">'
  $xml += '<type-details xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="ns5:genericDetailsType"/>'
  $xml += '<revision><major>37</major><minor>3</minor><micro>2</micro></revision>'
  $xml += '<display-name>Android Emulator</display-name>'
  $xml += '<uses-license ref="license-24333f"/></localPackage></ns2:repository>'
  Set-Content -Path $emuPkg -Value $xml -Encoding UTF8 -NoNewline
  Write-Ok 'emulator package.xml created'
}

# ============================================================ 3. AVDs
if ($SdkOnly) {
  Write-Step '3/5  AVDs skipped (-SdkOnly)'
} else {
  Write-Step "3/5  Creating $Count AVD(s)"

  $env:ANDROID_SDK_ROOT = $Sdk
  $env:ANDROID_HOME     = $Sdk
  $avdmanager = Join-Path $Sdk 'cmdline-tools\latest\bin\avdmanager.bat'
  $avdHome    = Join-Path $env:USERPROFILE '.android\avd'

  if (-not (Test-Path $avdmanager)) {
    Write-Err 'avdmanager missing - cannot create AVDs'
    $script:Failed += 'avdmanager missing'
  } else {
    for ($i = 1; $i -le $Count; $i++) {
      $name = if ($Count -eq 1) { $AvdName } else { '{0}-{1:d2}' -f $AvdName, $i }
      $ini  = Join-Path $avdHome "$name.ini"
      if (Test-Path $ini) {
        Write-Ok "AVD $name already exists"
        continue
      }
      Write-Info "creating AVD $name ..."
      # `echo no` answers avdmanager's "custom hardware profile?" prompt.
      # Invoked via cmd.exe; using the PowerShell call operator here would try to
      # bind "no" as a parameter to this script.
      $out = & cmd.exe /c "echo no | `"$avdmanager`" create avd -n $name -k `"system-images;android-29;default;x86_64`" -d pixel -f" 2>&1
      if (Test-Path $ini) { Write-Ok "created $name" }
      else {
        Write-Err "failed to create $name"
        Write-Info ($out | Out-String)
        $script:Failed += "AVD $name"
        continue
      }
      # Apply the hardware profile. avdmanager does not expose -gpu mode, so it
      # has to be written into config.ini directly.
      $cfg = Join-Path $avdHome "$name.avd\config.ini"
      if (Test-Path $cfg) {
        $settings = @{
          'hw.gpu.enabled'            = 'yes'
          'hw.gpu.mode'               = 'host'   # real host GPU, NOT SwiftShader
          'hw.ramSize'                = "$RamMb"
          'hw.cpu.ncore'              = "$Cores"
          'hw.keyboard'               = 'yes'
          'hw.mainKeys'               = 'no'
          'hw.audioInput'             = 'no'
          'hw.audioOutput'            = 'no'
          'hw.camera.back'            = 'none'
          'hw.camera.front'           = 'none'
          'disk.dataPartition.size'   = '2048M'
        }
        $cur = Get-Content $cfg
        foreach ($k in $settings.Keys) {
          # AVD config.ini may use "key = value" or "key=value" depending on how
          # it was generated, so match either form and replace instead of
          # appending a duplicate key.
          $esc = [regex]::Escape($k)
          if ($cur -match "^\s*$esc\s*=") {
            $cur = $cur -replace "^\s*$esc\s*=.*$", "$k=$($settings[$k])"
          } else { $cur += "$k=$($settings[$k])" }
        }
        Set-Content -Path $cfg -Value $cur
        Write-Ok "  configured (gpu=host, ram=${RamMb}MB, cores=$Cores)"
      }
    }
  }
}

Write-Step '4/5  Verification'

$checks = @(
  @{ Name = 'emulator.exe';      Path = (Join-Path $Sdk 'emulator\emulator.exe') },
  @{ Name = 'adb.exe';           Path = (Join-Path $Sdk 'platform-tools\adb.exe') },
  @{ Name = 'system.img';        Path = (Join-Path $Sdk 'system-images\android-29\default\x86_64\system.img') },
  @{ Name = 'emulator pkg.xml';  Path = (Join-Path $Sdk 'emulator\package.xml') },
  @{ Name = 'cmdline-tools';     Path = (Join-Path $Sdk 'cmdline-tools\latest\bin\avdmanager.bat') }
)
foreach ($c in $checks) {
  if (Test-Path $c.Path) { Write-Ok "$($c.Name) present" }
  else { Write-Err "$($c.Name) MISSING"; $script:Failed += $c.Name }
}

# The decisive gate: can the emulator actually use hardware acceleration?
Write-Info 'checking hardware acceleration ...'
$accel = (& (Join-Path $Sdk 'emulator\emulator.exe') -accel-check 2>&1 | Out-String)
# Order matters: the failure text is "Android Emulator hypervisor driver is NOT
# installed", which contains the substring "installed". Matching the positive
# case first would report a broken machine as healthy.
if ($accel -match 'not installed') {
  Write-Err 'emulator hypervisor NOT active - reboot after enabling HypervisorPlatform'
  $script:Failed += 'hypervisor not active'
  $script:RebootNeeded = $true
} elseif ($accel -match 'accel:\s*1|installed') {
  Write-Ok 'hardware acceleration available'
} else {
  Write-Info ($accel.Trim())
}

# ============================================================ 5. summary
Write-Step '5/5  Summary'
if ($script:RebootNeeded) {
  Write-Warn 'A REBOOT is required (hypervisor). Re-run install.ps1 afterwards.'
}
if ($script:Failed.Count -eq 0) {
  Write-Host "`n    INSTALL COMPLETE." -ForegroundColor Green
  Write-Host "    Start an instance:  .\scripts\instances.ps1 -AvdName $AvdName`n" -ForegroundColor Gray
} else {
  Write-Host "`n    Finished with issues:" -ForegroundColor Yellow
  $script:Failed | ForEach-Object { Write-Host "      - $_" -ForegroundColor Yellow }
}

if ($RebootWhenNeeded -and $script:RebootNeeded) {
  Write-Warn 'rebooting now (-RebootWhenNeeded)...'
  Restart-Computer -Force
}
