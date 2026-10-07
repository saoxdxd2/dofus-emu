<#
.SYNOPSIS
  Composites the 4 instance screen captures into a high-res 2x2 visual grid image.
#>
param(
  [string] $ArtifactsDir = 'C:\Users\sao\.gemini\antigravity\brain\0b156d3f-8d35-4725-8793-1f182ba87d00'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$outPath = Join-Path $ArtifactsDir 'screenshot_farm_2x2_composite.png'

# Source guest images
$img1 = Join-Path $ArtifactsDir 'screenshot_guest_slot01_emulator-5554.png'
$img2 = Join-Path $ArtifactsDir 'screenshot_guest_slot02_emulator-5556.png'
$img3 = Join-Path $ArtifactsDir 'screenshot_dofus_online_test.png'
$img4 = Join-Path $ArtifactsDir 'screenshot_guest_slot04_emulator-5560.png'

# Desired single tile size (640x360)
$tileW = 640
$tileH = 360
$gap = 6
$bannerH = 26

$totalW = ($tileW * 2) + ($gap * 3)
$totalH = (($tileH + $bannerH) * 2) + ($gap * 3)

$bmp = New-Object System.Drawing.Bitmap($totalW, $totalH)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality

# Background
$bgBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(24, 24, 24))
$g.FillRectangle($bgBrush, 0, 0, $totalW, $totalH)

$font = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
$titleBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(220, 220, 220))
$greenBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(152, 195, 121))
$bannerBg = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(32, 32, 34))
$borderPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(60, 60, 65), 1)

$tiles = @(
  @{ Path = $img1; X = $gap; Y = $gap; Title = "Slot 1: dofus-01 (Port 5554 | 768 MB | 1 Core)"; Active = $true },
  @{ Path = $img2; X = ($gap * 2) + $tileW; Y = $gap; Title = "Slot 2: dofus-02 (Port 5556 | 768 MB | 1 Core)"; Active = $true },
  @{ Path = $img3; X = $gap; Y = ($gap * 2) + $tileH + $bannerH; Title = "Slot 3: dofus-03 (Port 5558 | Online Server Connected)"; Active = $true },
  @{ Path = $img4; X = ($gap * 2) + $tileW; Y = ($gap * 2) + $tileH + $bannerH; Title = "Slot 4: dofus-04 (Port 5560 | 768 MB | 1 Core)"; Active = $true }
)

foreach ($t in $tiles) {
  $x = $t.X
  $y = $t.Y

  # Banner bar
  $g.FillRectangle($bannerBg, $x, $y, $tileW, $bannerH)
  $g.DrawRectangle($borderPen, $x, $y, $tileW, $bannerH)

  # Status bullet
  $g.FillEllipse($greenBrush, ($x + 8), ($y + 8), 9, 9)
  $g.DrawString($t.Title, $font, $titleBrush, ($x + 22), ($y + 3))

  # Image
  $imgY = $y + $bannerH
  if (Test-Path $t.Path) {
    $srcImg = [System.Drawing.Image]::FromFile($t.Path)
    $g.DrawImage($srcImg, $x, $imgY, $tileW, $tileH)
    $srcImg.Dispose()
  } else {
    $placeholderBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(30, 30, 32))
    $g.FillRectangle($placeholderBrush, $x, $imgY, $tileW, $tileH)
    $placeholderBrush.Dispose()
  }
  $g.DrawRectangle($borderPen, $x, $imgY, $tileW, $tileH)
}

$bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()
$bgBrush.Dispose()
$font.Dispose()
$titleBrush.Dispose()
$greenBrush.Dispose()
$bannerBg.Dispose()
$borderPen.Dispose()

Write-Host "Composite 2x2 farm screenshot saved: $outPath ($([math]::Round((Get-Item $outPath).Length/1KB, 1)) KB)" -ForegroundColor Green
