<#
.SYNOPSIS
  Dofus Touch Multi-Instance Farm Uninstaller.

.DESCRIPTION
  Safely halts active virtual instances, cleans up Windows Desktop / Start Menu
  shortcuts, removes AVD profiles and differential QCOW2 overlays if selected,
  and unregisters the application from Windows Programs & Features.
#>
[CmdletBinding()]
param(
  [switch] $Quiet,
  [switch] $PurgeData
)

$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName Microsoft.VisualBasic

$RepoRoot = Split-Path -Parent $PSScriptRoot
$AvdHome  = Join-Path $env:USERPROFILE '.android\avd'
$RegPath  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\DofusTouchFarm'

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "       DOFUS TOUCH MULTI-INSTANCE FARM UNINSTALLER        " -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor Cyan

$removeAvds = $PurgeData.IsPresent

if (-not $Quiet) {
  $msg = "Are you sure you want to uninstall Dofus Touch Farm?`n`n" +
         "This will terminate running emulators, remove desktop shortcuts, and deregister the app from Windows."
  $confirm = [System.Windows.Forms.MessageBox]::Show(
    $msg,
    "Uninstall Dofus Touch Farm",
    [System.Windows.Forms.MessageBoxButtons]::YesNo,
    [System.Windows.Forms.MessageBoxIcon]::Question
  )

  if ($confirm -ne [System.Windows.Forms.DialogResult]::Yes) {
    Write-Host "Uninstallation aborted by user." -ForegroundColor Yellow
    exit 0
  }

  $purgePrompt = [System.Windows.Forms.MessageBox]::Show(
    "Do you also want to PERMANENTLY delete all AVD instances and virtual disk data ($AvdHome\dofus*)?`n`nClick 'Yes' to purge all instance disks, or 'No' to keep them.",
    "Purge Virtual Data?",
    [System.Windows.Forms.MessageBoxButtons]::YesNo,
    [System.Windows.Forms.MessageBoxIcon]::Question
  )
  if ($purgePrompt -eq [System.Windows.Forms.DialogResult]::Yes) {
    $removeAvds = $true
  }
}

# 1. Stop active emulator processes
Write-Host "[1/5] Halting active emulator processes..." -ForegroundColor Cyan
$Adb = Join-Path $RepoRoot 'sdk\platform-tools\adb.exe'
if (Test-Path $Adb) {
  $devices = & $Adb devices 2>$null | Select-String '^(emulator-\d+)\s+device' | ForEach-Object { $_.Matches[0].Groups[1].Value }
  foreach ($d in $devices) {
    & $Adb -s $d emu kill 2>$null | Out-Null
  }
}
Start-Sleep -Seconds 1
Get-Process -Name 'qemu-system-x86_64', 'emulator', 'dofus-net-proxy' -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue
Write-Host "      Emulators and network proxy stopped." -ForegroundColor Green

# 2. Remove Desktop & Start Menu shortcuts
Write-Host "[2/5] Removing desktop & start menu shortcuts..." -ForegroundColor Cyan
$desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Dofus Farm Manager.lnk'
$commonDesktopLnk = Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'Dofus Farm Manager.lnk'
$startMenuLnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'Dofus Farm Manager.lnk'

@($desktopLnk, $commonDesktopLnk, $startMenuLnk) | ForEach-Object {
  if (Test-Path $_) {
    Remove-Item $_ -Force -EA SilentlyContinue
    Write-Host "      Removed shortcut: $_" -ForegroundColor Green
  }
}

# 3. Purge AVDs if selected
if ($removeAvds) {
  Write-Host "[3/5] Purging AVD instances and differential disks..." -ForegroundColor Cyan
  if (Test-Path $AvdHome) {
    Get-ChildItem -Path $AvdHome -Filter 'dofus*' -EA SilentlyContinue | ForEach-Object {
      Remove-Item $_.FullName -Recurse -Force -EA SilentlyContinue
      Write-Host "      Deleted: $($_.Name)" -ForegroundColor Green
    }
  }
} else {
  Write-Host "[3/5] Preserving AVD instances in $AvdHome (skipped)." -ForegroundColor DarkGray
}

# 4. Remove Windows Registry Uninstall Entry
Write-Host "[4/5] Removing Windows Programs & Features registration..." -ForegroundColor Cyan
if (Test-Path $RegPath) {
  Remove-Item -Path $RegPath -Recurse -Force -EA SilentlyContinue
  Write-Host "      Unregistered from Windows Add/Remove Programs." -ForegroundColor Green
}

# 5. Clean up temporary and log files
Write-Host "[5/5] Cleaning up temporary files..." -ForegroundColor Cyan
Get-ChildItem -Path $RepoRoot -Filter '*.lock' -Recurse -EA SilentlyContinue | Remove-Item -Force -EA SilentlyContinue
Get-ChildItem -Path $RepoRoot -Filter 'gui-error.log' -EA SilentlyContinue | Remove-Item -Force -EA SilentlyContinue

Write-Host "`nUninstallation complete!" -ForegroundColor Green

if (-not $Quiet) {
  [System.Windows.Forms.MessageBox]::Show(
    "Dofus Touch Multi-Instance Farm has been successfully uninstalled.",
    "Uninstall Complete",
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Information
  ) | Out-Null
}
