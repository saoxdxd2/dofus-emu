<#
.SYNOPSIS
  Renders the WPF GUI Farm Manager interface to a high-resolution PNG artifact.
#>
param(
  [string] $OutPath = 'C:\Users\sao\.gemini\antigravity\brain\0b156d3f-8d35-4725-8793-1f182ba87d00\screenshot_gui_manager.png'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName Microsoft.VisualBasic

# Load Hardware Diagnostics
. (Join-Path $PSScriptRoot 'detect-hardware.ps1')
$diag = Get-HardwareDiagnostics

# Read XAML from gui-manager.ps1
$guiScript = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot 'gui-manager.ps1'), [System.Text.Encoding]::UTF8)
$m = [regex]::Match($guiScript, '(?s)\[xml\]\$xaml\s*=\s*@"([^"]*)"@')
if (-not $m.Success) {
  # Try matching without quote restriction
  $m = [regex]::Match($guiScript, '(?s)\[xml\]\$xaml\s*=\s*@"(.*?)"@\s*\$reader')
}

$xamlStr = $m.Groups[1].Value
$reader = New-Object System.Xml.XmlNodeReader ([xml]$xamlStr)
$win = [Windows.Markup.XamlReader]::Load($reader)

# Populate header diagnostics
$TxtDiagCpu = $win.FindName('TxtDiagCpu')
$TxtDiagCores = $win.FindName('TxtDiagCores')
$TxtDiagGpu = $win.FindName('TxtDiagGpu')
$TxtDiagGpuDriver = $win.FindName('TxtDiagGpuDriver')
$TxtDiagProfileName = $win.FindName('TxtDiagProfileName')
$TxtDiagReason = $win.FindName('TxtDiagReason')
$HeaderGpuText = $win.FindName('HeaderGpuText')
$HostStatsHeader = $win.FindName('HostStatsHeader')
$Status = $win.FindName('Status')
$GoldenStatus = $win.FindName('GoldenStatus')

if ($TxtDiagCpu) { $TxtDiagCpu.Text = "CPU: $($diag.HostCpu)" }
if ($TxtDiagCores) { $TxtDiagCores.Text = "Cores: $($diag.PhysicalCores) Physical / $($diag.LogicalThreads) Logical Threads" }
if ($TxtDiagGpu) { $TxtDiagGpu.Text = "GPU: $($diag.GpuName)" }
if ($TxtDiagGpuDriver) { $TxtDiagGpuDriver.Text = "Driver Version: $($diag.GpuDriver)" }
if ($TxtDiagProfileName) { $TxtDiagProfileName.Text = "Profile: $($diag.ProfileName)" }
if ($TxtDiagReason) { $TxtDiagReason.Text = $diag.Recommendation }
if ($HeaderGpuText) { $HeaderGpuText.Text = "$($diag.GpuName) (-gpu host)" }
if ($HostStatsHeader) { $HostStatsHeader.Text = "Host: $($diag.HostRamGB) GB RAM ($($diag.FreeRamGB) GB Free) | $($diag.LogicalThreads) Threads" }
if ($Status) { $Status.Text = "Farm Online: 4/4 Instances Running (Optimized Efficiency 1-Core Profile)" }
if ($GoldenStatus) { $GoldenStatus.Text = "Golden Master: 107 MB (QCOW2 Differential Active)" }

# Populate Slots
1..4 | ForEach-Object {
  $b = $win.FindName("Slot${_}Border")
  $n = $win.FindName("Slot${_}Name")
  $s = $win.FindName("Slot${_}Status")
  if ($n) { $n.Text = "Slot ${_}: dofus-0${_}" }
  if ($s) {
    $port = 5554 + 2*($_ - 1)
    $s.Text = "$([char]0x25CF) RUNNING (Port $port)"
    $s.Foreground = [System.Windows.Media.BrushConverter]::new().ConvertFromString('#98C379')
  }
  if ($b) {
    $b.Background = [System.Windows.Media.BrushConverter]::new().ConvertFromString('#1E3A25')
  }
}

# Populate DataGrid
$Grid = $win.FindName('Grid')
if ($Grid) {
  $rows = 1..4 | ForEach-Object {
    $port = 5554 + 2*($_ - 1)
    [pscustomobject]@{
      StateDisplay = "$([char]0x25CF) RUNNING"
      DisplayName  = "dofus-0$_"
      Name         = "dofus-0$_"
      Index        = $_
      RamMb        = "768 MB"
      Cores        = "1"
      Port         = $port
      Serial       = "emulator-$port"
      Overlay      = "Yes (QCOW2)"
      Mac          = "bc:72:b7:1$($_):2$($_):0$($_)"
      Proxy        = "Direct LAN (Auto-DNS)"
    }
  }
  $Grid.ItemsSource = $rows
}

# Wrap in background container for off-screen rendering
[int]$w = 1020
[int]$h = 730
$content = $win.Content
$win.Content = $null
$container = New-Object System.Windows.Controls.Border
$container.Background = [System.Windows.Media.BrushConverter]::new().ConvertFromString('#181818')
$container.Child = $content
$container.Width = $w
$container.Height = $h
$container.Measure((New-Object System.Windows.Size($w, $h)))
$container.Arrange((New-Object System.Windows.Rect(0, 0, $w, $h)))
$container.UpdateLayout()

# Render visual tree to RenderTargetBitmap
$rtb = New-Object System.Windows.Media.Imaging.RenderTargetBitmap($w, $h, 96, 96, [System.Windows.Media.PixelFormats]::Pbgra32)
$rtb.Render($container)

# Encode as PNG
$encoder = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
$encoder.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))

$dir = Split-Path -Parent $OutPath
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$fs = [System.IO.File]::Create($OutPath)
$encoder.Save($fs)
$fs.Close()

Write-Host "GUI snapshot rendered and saved to: $OutPath ($([math]::Round((Get-Item $OutPath).Length/1KB, 1)) KB)" -ForegroundColor Green
