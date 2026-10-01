<#
.SYNOPSIS
  Lightweight WPF manager for the Dofus Touch farm.

.DESCRIPTION
  Shows the provisioned instances, their assigned RAM and their live status, and
  offers: create instances (768 / 1024 / 1536 MB), delete them, launch the whole
  farm (laid out to fill the screen), and stop it.

  Capacity is read from the host, so the instance ceiling is correct on any
  machine rather than being baked in.

.EXAMPLE
  .\scripts\gui-manager.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Adb      = Join-Path $SdkRoot 'platform-tools\adb.exe'
$AvdHome  = Join-Path $env:USERPROFILE '.android\avd'
$Cluster  = Join-Path $PSScriptRoot 'cluster-manager.ps1'
$StartFarm= Join-Path $PSScriptRoot 'start-farm.ps1'
$Golden   = Join-Path $AvdHome 'dofus.avd\userdata-golden.img'

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

# ------------------------------------------------------------------ host facts
$cs       = Get-CimInstance Win32_ComputerSystem
$hostGB   = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
$cores    = [int]$cs.NumberOfLogicalProcessors
$freeGB   = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1)
$reserve  = if ($hostGB -le 8) { 2.5 } else { 4.0 }
$usableGB = [math]::Max(0, $freeGB - $reserve)
# Dofus Touch needs >= 1024 MB in production; 768 is the experimental floor.
$ramTiers = @(768, 1024, 1536)

function Get-Instances {
  # An instance is "provisioned" when its .avd dir exists.
  Get-ChildItem $AvdHome -Filter 'dofus-*.ini' -EA SilentlyContinue |
    ForEach-Object {
      $name = $_.BaseName
      $dir  = Join-Path $AvdHome "$name.avd"
      $cfg  = Join-Path $dir 'config.ini'
      $ram  = 1024
      if (Test-Path $cfg) {
        $m = Select-String -Path $cfg -Pattern '^hw\.ramSize=(\d+)' -EA SilentlyContinue |
             Select-Object -First 1
        if ($m) { $ram = [int]$m.Matches[0].Groups[1].Value }
      }
      $idx = 0
      if ($name -match '(\d+)$') { $idx = [int]$Matches[1] }
      $port = 5554 + (2 * ($idx - 1))
      [pscustomobject]@{
        Name = $name; RamMb = $ram; Port = $port; Serial = "emulator-$port"; Dir = $dir
        Seeded = (Test-Path (Join-Path $dir 'userdata-qemu.img.qcow2'))
      }
    } | Sort-Object Name
}

function Get-LiveSerials {
  if (-not (Test-Path $Adb)) { return @() }
  $out = & $Adb devices 2>$null
  @($out | Select-String -Pattern '^(emulator-\d+)\s+device' |
    ForEach-Object { $_.Matches[0].Groups[1].Value })
}

function Max-CountForRam([int]$ram) {
  if ($usableGB -le 0) { return 0 }
  [math]::Floor($usableGB * 1024 / $ram)
}

# ------------------------------------------------------------------- XAML
[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Dofus Touch Farm" Height="560" Width="820"
        WindowStartupLocation="CenterScreen" Background="#1E1E1E">
  <Grid Margin="14">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <StackPanel Grid.Row="0" Orientation="Horizontal">
      <TextBlock Text="Dofus Touch Farm" Foreground="#FFFFFF" FontSize="22" FontWeight="Bold" VerticalAlignment="Center"/>
      <TextBlock x:Name="HostInfo" Foreground="#9AA0A6" FontSize="12" Margin="16,0,0,0" VerticalAlignment="Center"/>
    </StackPanel>

    <Border Grid.Row="1" Margin="0,12,0,12" Padding="10" Background="#252526" CornerRadius="4">
      <StackPanel>
        <TextBlock Text="Host capacity" Foreground="#FFFFFF" FontWeight="Bold" Margin="0,0,0,6"/>
        <StackPanel Orientation="Horizontal">
          <ComboBox x:Name="RamBox" Width="110" Margin="0,0,8,0"/>
          <TextBlock x:Name="RamHint" Foreground="#9AA0A6" VerticalAlignment="Center"/>
        </StackPanel>
        <TextBlock x:Name="GoldenInfo" Foreground="#9AA0A6" FontSize="11" Margin="0,6,0,0"/>
      </StackPanel>
    </Border>

    <DataGrid x:Name="Grid" Grid.Row="2" AutoGenerateColumns="False" IsReadOnly="True"
              HeadersVisibility="Column" Background="#252526" Foreground="#E0E0E0"
              GridLinesVisibility="Horizontal" BorderThickness="0" RowBackground="#252526"
              AlternatingRowBackground="#2D2D30">
      <DataGrid.Columns>
        <DataGridTextColumn Header="Instance" Binding="{Binding Name}" Width="140"/>
        <DataGridTextColumn Header="RAM (MB)" Binding="{Binding RamMb}" Width="90"/>
        <DataGridTextColumn Header="Console" Binding="{Binding Port}" Width="80"/>
        <DataGridTextColumn Header="adb serial" Binding="{Binding Serial}" Width="130"/>
        <DataGridTextColumn Header="Seeded" Binding="{Binding Seeded}" Width="70"/>
        <DataGridTextColumn Header="State" Binding="{Binding State}" Width="*"/>
      </DataGrid.Columns>
    </DataGrid>

    <StackPanel Grid.Row="3" Orientation="Horizontal" Margin="0,12,0,0">
      <Button x:Name="BtnRefresh" Content="Refresh" Width="90" Margin="0,0,8,0" Padding="6,4"/>
      <Button x:Name="BtnCreate" Content="Create" Width="90" Margin="0,0,8,0" Padding="6,4"/>
      <Button x:Name="BtnDelete" Content="Delete" Width="90" Margin="0,0,8,0" Padding="6,4"/>
      <Button x:Name="BtnLaunch" Content="Launch Farm" Width="120" Margin="0,0,8,0" Padding="6,4" FontWeight="Bold"/>
      <Button x:Name="BtnStop"   Content="Stop All"  Width="90" Margin="0,0,8,0" Padding="6,4"/>
      <Button x:Name="BtnLayout"  Content="Re-layout" Width="100" Margin="0,0,8,0" Padding="6,4"/>
    </StackPanel>

    <TextBlock x:Name="Status" Grid.Row="4" Foreground="#9AA0A6" Margin="0,10,0,0" TextWrapping="Wrap"/>
  </Grid>
</Window>
"@

$reader = New-Object System.Xml.XmlNodeReader $xaml
$win = [Windows.Markup.XamlReader]::Load($reader)

$RamBox = $win.FindName('RamBox')
$RamHint = $win.FindName('RamHint')
$Grid = $win.FindName('Grid')
$Status = $win.FindName('Status')
$HostInfo = $win.FindName('HostInfo')
$GoldenInfo = $win.FindName('GoldenInfo')

$RamBox.ItemsSource = $ramTiers | ForEach-Object { "$_ MB" }
$RamBox.SelectedIndex = 1

function Set-Status($msg, $color = '#9AA0A6') {
  $Status.Text = $msg
  $Status.Foreground = $color
}

function Refresh-Grid {
  $live = Get-LiveSerials
  $rows = Get-Instances | ForEach-Object {
    $state = if ($live -contains $_.Serial) { 'running' } else { 'stopped' }
    [pscustomobject]@{
      Name = $_.Name; RamMb = $_.RamMb; Port = $_.Port
      Serial = $_.Serial; Seeded = $_.Seeded; State = $state
    }
  }
  $Grid.ItemsSource = $rows

  $HostInfo.Text = "host ${hostGB}GB RAM / $cores cores / ${freeGB}GB free"
  if (Test-Path $Golden) {
    $GoldenInfo.Text = "Golden image: $([math]::Round((Get-Item $Golden).Length/1MB,0)) MB (instances inherit the trimmed /data)"
  } else {
    $GoldenInfo.Text = "Golden image: MISSING - run setup.bat or make-golden-image.ps1"
    $GoldenInfo.Foreground = '#E06C75'
  }
  $sel = $RamBox.SelectedItem
  if ($sel) {
    $r = [int]($sel -replace '\D','')
    $max = Max-CountForRam $r
    $RamHint.Text = "at $r MB this host can sustain ~$max instance(s)"
  }
}

# Run a child powershell script without blocking the UI.
function Invoke-Script([string]$file, [string[]]$scriptArgs, [string]$label) {
  Set-Status "$label ..."
  $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $file) + $scriptArgs
  $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -PassThru -WindowStyle Minimized
  $p | Wait-Process -Timeout 600 -EA SilentlyContinue
  if (-not $p.HasExited) {
    Set-Status "$label still running in the background (pid $($p.Id))." '#E5C07B'
  } else {
    Set-Status "$label finished (exit $($p.ExitCode))." '#98C379'
  }
  Refresh-Grid
}

$win.FindName('BtnRefresh').Add_Click({ Refresh-Grid })

$win.FindName('BtnCreate').Add_Click({
  $sel = $RamBox.SelectedItem
  if (-not $sel) { Set-Status 'Pick a RAM tier first.' '#E06C75'; return }
  $r = [int]($sel -replace '\D','')
  $target = [math]::Min(4, [math]::Max(1, (Get-Instances).Count + 1))
  Invoke-Script $Cluster @('-Action','Create',"-Count","$target","-RamMb","$r","-Cores","2") "Create $target instance(s) @ ${r}MB"
})

$win.FindName('BtnDelete').Add_Click({
  $sel = $Grid.SelectedItem
  if (-not $sel) { Set-Status 'Select an instance row to delete.' '#E06C75'; return }
  if ($sel.State -eq 'running') { Set-Status 'Stop that instance before deleting it.' '#E5C07B'; return }
  $dir = Join-Path $AvdHome "$($sel.Name).avd"
  $ini = Join-Path $AvdHome "$($sel.Name).ini"
  $r = Read-Host "Delete $($sel.Name)? This destroys its /data. Type 'yes' to confirm"
  if ($r -eq 'yes') {
    Remove-Item $dir -Recurse -Force -EA SilentlyContinue
    Remove-Item $ini -Force -EA SilentlyContinue
    Set-Status "Deleted $($sel.Name)." '#98C379'
    Refresh-Grid
  } else {
    Set-Status 'Delete cancelled.'
  }
})

$win.FindName('BtnLaunch').Add_Click({
  $inst = Get-Instances
  if (-not $inst) { Set-Status 'No instances provisioned - click Create first.' '#E06C75'; return }
  $sel = $RamBox.SelectedItem
  $r = if ($sel) { [int]($sel -replace '\D','') } else { 1024 }
  $n = $inst.Count
  Invoke-Script $StartFarm @("-Count","$n","-RamMb","$r","-Force") "Launch $n instance(s)"
})

$win.FindName('BtnStop').Add_Click({
  foreach ($s in (Get-LiveSerials)) {
    & $Adb -s $s emu kill 2>&1 | Out-Null
  }
  Set-Status 'Stop requested for all instances.' '#98C379'
  Start-Sleep -Seconds 3
  Refresh-Grid
})

$win.FindName('BtnLayout').Add_Click({
  . (Join-Path $PSScriptRoot 'layout.ps1')
  $live = Get-LiveSerials
  if (-not $live) { Set-Status 'Nothing running to re-layout.' '#E5C07B'; return }
  $pids = @()
  foreach ($s in $live) {
    $pid_ = [int]($s -replace 'emulator-','')
    # map console port -> emulator process holding that console port
    $p = Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" -EA SilentlyContinue |
         Where-Object { $_.CommandLine -match "-port\s+$pid_\b" } | Select-Object -First 1
    if ($p) { $pids += $p.ProcessId }
  }
  if ($pids.Count -eq 0) { Set-Status 'Could not map live instances to processes.' '#E06C75'; return }
  Set-FarmLayout -Procs $pids -MaximizeSingle
  Set-Status "Re-laid out $($pids.Count) window(s)." '#98C379'
})

Refresh-Grid
Set-Status 'Ready. Create instances, then Launch Farm.'
$win.ShowDialog() | Out-Null
