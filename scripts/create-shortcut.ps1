<#
.SYNOPSIS
  Creates desktop and start menu shortcuts for Dofus Farm Manager with embedded sao_image icon.
#>
[CmdletBinding()]
param(
  [string] $TargetExe = '',
  [switch] $AllUsers
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot

if (-not $TargetExe) {
  $TargetExe = Join-Path $RepoRoot 'DofusFarm.exe'
  if (-not (Test-Path $TargetExe)) {
    $TargetExe = Join-Path $RepoRoot 'setup.exe'
  }
}

$appDir = Split-Path -Parent $TargetExe
$IconPath = Join-Path $appDir 'app_icon.ico'
if (-not (Test-Path $IconPath)) { $IconPath = Join-Path $RepoRoot 'app_icon.ico' }

$DesktopPath = if ($AllUsers) {
  [Environment]::GetFolderPath('CommonDesktopDirectory')
} else {
  [Environment]::GetFolderPath('Desktop')
}

$ShortcutPath = Join-Path $DesktopPath 'Dofus Farm Manager.lnk'

Write-Host "Creating desktop shortcut: $ShortcutPath" -ForegroundColor Cyan

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $TargetExe
$shortcut.WorkingDirectory = $appDir
$shortcut.Arguments = ''
$shortcut.Description = 'Dofus Touch High-Efficiency Android Farm Manager'
$shortcut.IconLocation = "$IconPath,0"
$shortcut.WindowStyle = 1
$shortcut.Save()

[System.Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut) | Out-Null
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($wsh) | Out-Null

if (Test-Path $ShortcutPath) {
  Write-Host "  [OK] Created desktop shortcut with SAO icon: $ShortcutPath" -ForegroundColor Green
  Write-Host "       Target: $TargetExe" -ForegroundColor DarkGray
  Write-Host "       Icon:   $IconPath" -ForegroundColor DarkGray
} else {
  Write-Error "Failed to create desktop shortcut."
}
