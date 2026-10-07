<#
.SYNOPSIS
  Renders Tab 5 (Session Vault & Stability) to a high-resolution PNG artifact.
#>
param(
  [string] $OutPath = 'C:\Users\sao\.gemini\antigravity\brain\0b156d3f-8d35-4725-8793-1f182ba87d00\screenshot_gui_vault_tab.png'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

# Load Hardware Diagnostics
. (Join-Path $PSScriptRoot 'detect-hardware.ps1')
$diag = Get-HardwareDiagnostics

# Read XAML from gui-manager.ps1
$guiScript = [System.IO.File]::ReadAllText((Join-Path $PSScriptRoot 'gui-manager.ps1'), [System.Text.Encoding]::UTF8)
$m = [regex]::Match($guiScript, '(?s)\[xml\]\$xaml\s*=\s*@"(.*?)"@\s*\$reader')
$xamlStr = $m.Groups[1].Value
$reader = New-Object System.Xml.XmlNodeReader ([xml]$xamlStr)
$win = [Windows.Markup.XamlReader]::Load($reader)

# Select Tab 5 (Session Vault & Stability)
$tabControl = $win.Content.Children | Where-Object { $_ -is [System.Windows.Controls.TabControl] } | Select-Object -First 1
if ($tabControl) {
  $tabControl.SelectedIndex = 4
}

# Populate Header
$HeaderGpuText = $win.FindName('HeaderGpuText')
$HostStatsHeader = $win.FindName('HostStatsHeader')
$Status = $win.FindName('Status')
$GoldenStatus = $win.FindName('GoldenStatus')

if ($HeaderGpuText) { $HeaderGpuText.Text = "$($diag.GpuName) (-gpu host)" }
if ($HostStatsHeader) { $HostStatsHeader.Text = "Host: $($diag.HostRamGB) GB RAM ($($diag.FreeRamGB) GB Free) | $($diag.LogicalThreads) Threads" }
if ($Status) { $Status.Text = "Vault System Ready | Dynamic FPS Governor Active | Watchdog Supervisor Armed" }
if ($GoldenStatus) { $GoldenStatus.Text = "Session Vault: 4/4 Protected" }

# Populate VaultGrid
$VaultGrid = $win.FindName('VaultGrid')
if ($VaultGrid) {
  $vaultRows = 1..4 | ForEach-Object {
    [pscustomobject]@{
      Instance     = "dofus-0$_"
      VaultStatus  = "$([char]0x25CF) Saved (Protected)"
      Tokens_Cache = "84.2 KB"
      LastBackup   = (Get-Date).ToString("yyyy-MM-dd HH:mm")
      Qcow2Delta   = "68.8 MB"
    }
  }
  $VaultGrid.ItemsSource = $vaultRows
}

[int]$w = 1040
[int]$h = 740
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

$encoder = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
$encoder.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))

$fs = [System.IO.File]::Create($OutPath)
$encoder.Save($fs)
$fs.Close()

Write-Host "Vault Tab snapshot rendered and saved to: $OutPath ($([math]::Round((Get-Item $OutPath).Length/1KB, 1)) KB)" -ForegroundColor Green
