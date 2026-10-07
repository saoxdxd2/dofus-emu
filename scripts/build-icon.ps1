<#
.SYNOPSIS
  Converts sao_image.png into app_icon.ico for setup.exe and Windows shortcuts.
#>
[CmdletBinding()]
param(
  [string] $PngPath = 'C:\Users\sao\Documents\dofus-emu\sao_image.png',
  [string] $IcoPath = 'C:\Users\sao\Documents\dofus-emu\app_icon.ico'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

if (-not (Test-Path $PngPath)) {
  Write-Error "Source PNG not found at $PngPath"
  exit 1
}

$src = [System.Drawing.Image]::FromFile($PngPath)

# Create 256x256 square canvas with smooth bicubic scaling
$sq = New-Object System.Drawing.Bitmap 256, 256
$g = [System.Drawing.Graphics]::FromImage($sq)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
$g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$g.Clear([System.Drawing.Color]::Transparent)
$g.DrawImage($src, 0, 0, 256, 256)
$g.Dispose()
$src.Dispose()

$ms = New-Object System.IO.MemoryStream
$sq.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
$pngBytes = $ms.ToArray()
$sq.Dispose()
$ms.Dispose()

$fs = [System.IO.File]::Create($IcoPath)
$bw = New-Object System.IO.BinaryWriter($fs)
$bw.Write([uint16]0)                  # Reserved
$bw.Write([uint16]1)                  # Type (1 = ICO)
$bw.Write([uint16]1)                  # Image count
$bw.Write([byte]0)                    # Width (0 = 256px)
$bw.Write([byte]0)                    # Height (0 = 256px)
$bw.Write([byte]0)                    # Colors
$bw.Write([byte]0)                    # Reserved
$bw.Write([uint16]1)                  # Color planes
$bw.Write([uint16]32)                 # Bits per pixel
$bw.Write([uint32]$pngBytes.Length)   # PNG size
$bw.Write([uint32]22)                 # Offset (6 header + 16 dir entry = 22)
$bw.Write($pngBytes)
$bw.Close()
$fs.Close()

Write-Host "Created icon: $IcoPath ($($pngBytes.Length) bytes)" -ForegroundColor Green
