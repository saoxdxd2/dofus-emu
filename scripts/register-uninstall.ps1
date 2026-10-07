<#
.SYNOPSIS
  Registers Dofus Touch Farm in Windows Add/Remove Programs (Programs & Features).
#>
[CmdletBinding()]
param()

$RepoRoot = Split-Path -Parent $PSScriptRoot
$RegPath  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\DofusTouchFarm'

if (-not (Test-Path $RegPath)) {
  New-Item -Path $RegPath -Force | Out-Null
}

$uninstallExe = Join-Path $RepoRoot 'uninstall.exe'
if (-not (Test-Path $uninstallExe)) {
  $uninstallExe = "powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File `"$RepoRoot\scripts\uninstall.ps1`""
}
$iconPath = Join-Path $RepoRoot 'installer_icon.ico'
if (-not (Test-Path $iconPath)) { $iconPath = Join-Path $RepoRoot 'app_icon.ico' }

# Calculate estimated size (KB)
$sizeBytes = (Get-ChildItem -Path $RepoRoot -Recurse -File -EA SilentlyContinue | Measure-Object -Property Length -Sum).Sum
$sizeKb = [math]::Round($sizeBytes / 1KB, 0)

Set-ItemProperty -Path $RegPath -Name 'DisplayName' -Value 'Dofus Touch Multi-Instance Farm'
Set-ItemProperty -Path $RegPath -Name 'DisplayVersion' -Value '3.14.2'
Set-ItemProperty -Path $RegPath -Name 'Publisher' -Value 'Dofus Farm Project'
Set-ItemProperty -Path $RegPath -Name 'InstallLocation' -Value $RepoRoot
Set-ItemProperty -Path $RegPath -Name 'UninstallString' -Value $uninstallExe
Set-ItemProperty -Path $RegPath -Name 'QuietUninstallString' -Value "$uninstallExe -Quiet"
Set-ItemProperty -Path $RegPath -Name 'DisplayIcon' -Value "$iconPath,0"
Set-ItemProperty -Path $RegPath -Name 'EstimatedSize' -Value $sizeKb -Type DWord
Set-ItemProperty -Path $RegPath -Name 'NoModify' -Value 1 -Type DWord
Set-ItemProperty -Path $RegPath -Name 'NoRepair' -Value 1 -Type DWord

Write-Host "Registered in Windows Programs & Features: $RegPath" -ForegroundColor Green
