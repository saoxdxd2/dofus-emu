<#
.SYNOPSIS
  Extracts or generates the classic Windows Installer Computer icon for setup.exe.
#>
[CmdletBinding()]
param()

$RepoRoot = Split-Path -Parent $PSScriptRoot
$OutIco = Join-Path $RepoRoot 'installer_icon.ico'

Add-Type -AssemblyName System.Drawing

# 1. Try extracting from msiexec.exe (Classic Windows Installer icon)
$msiExe = Join-Path $env:SystemRoot 'System32\msiexec.exe'
if (Test-Path $msiExe) {
  try {
    $icon = [System.Drawing.Icon]::ExtractAssociatedIcon($msiExe)
    if ($icon) {
      $fs = [System.IO.File]::OpenWrite($OutIco)
      $icon.Save($fs)
      $fs.Close()
      Write-Host "  [OK] Extracted classic Windows Installer icon from msiexec.exe ($((Get-Item $OutIco).Length) bytes)" -ForegroundColor Green
      return
    }
  } catch {
    Write-Warning "Could not extract from msiexec.exe: $_"
  }
}

# 2. Fallback: Draw high-res professional Setup Computer icon
$bmp = New-Object System.Drawing.Bitmap 128, 128
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias

# Background circle
$brushBg = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 30, 115, 190))
$g.FillEllipse($brushBg, 4, 4, 120, 120)

# Computer Monitor outline
$penWhite = New-Object System.Drawing.Pen ([System.Drawing.Color]::White), 6
$brushWhite = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
$g.FillRectangle($brushWhite, 24, 24, 80, 56)

# Screen inner (Dark blue)
$brushScreen = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 20, 30, 45))
$g.FillRectangle($brushScreen, 30, 30, 68, 44)

# Monitor stand
$g.FillRectangle($brushWhite, 58, 80, 12, 16)
$g.FillRectangle($brushWhite, 42, 96, 44, 8)

# Setup disc / installation gear on screen
$brushDisc = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 0, 200, 100))
$g.FillEllipse($brushDisc, 52, 40, 24, 24)
$g.FillEllipse($brushScreen, 59, 47, 10, 10)

$hIcon = $bmp.GetHicon()
$drawnIcon = [System.Drawing.Icon]::FromHandle($hIcon)
$fs = [System.IO.File]::OpenWrite($OutIco)
$drawnIcon.Save($fs)
$fs.Close()

$g.Dispose()
$bmp.Dispose()
Write-Host "  [OK] Generated sleek Setup Computer icon: $OutIco" -ForegroundColor Green
