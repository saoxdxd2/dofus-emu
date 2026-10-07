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

$destFolders = @(
  [Environment]::GetFolderPath('Desktop'),
  [Environment]::GetFolderPath('Programs')
)
if ($AllUsers) {
  $destFolders += [Environment]::GetFolderPath('CommonDesktopDirectory')
  $destFolders += [Environment]::GetFolderPath('CommonPrograms')
}

$wsh = New-Object -ComObject WScript.Shell
foreach ($dir in ($destFolders | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)) {
  $ShortcutPath = Join-Path $dir 'Dofus Farm Manager.lnk'
  try {
    $shortcut = $wsh.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = $TargetExe
    $shortcut.WorkingDirectory = $appDir
    $shortcut.Arguments = ''
    $shortcut.Description = 'Dofus Touch High-Efficiency Android Farm Manager'
    if (Test-Path $IconPath) {
      $shortcut.IconLocation = "$IconPath,0"
    }
    $shortcut.WindowStyle = 1
    $shortcut.Save()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut) | Out-Null
    Write-Host "  [OK] Shortcut created: $ShortcutPath" -ForegroundColor Green
  } catch {
    Write-Warning "Could not write shortcut to ${dir}: $_"
  }
}
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($wsh) | Out-Null

if (Test-Path $ShortcutPath) {
  Write-Host "  [OK] Created desktop shortcut with SAO icon: $ShortcutPath" -ForegroundColor Green
  Write-Host "       Target: $TargetExe" -ForegroundColor DarkGray
  Write-Host "       Icon:   $IconPath" -ForegroundColor DarkGray
} else {
  Write-Error "Failed to create desktop shortcut."
}
