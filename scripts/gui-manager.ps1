<#
.SYNOPSIS
  Lightweight WPF manager for high-efficiency multi-instance Android virtualization.

.DESCRIPTION
  Standalone GUI application offering:
    - Instance Name input field with auto-increment suggestions.
    - Memory selector: 768 MB, 1024 MB (Recommended), 1536 MB, 2048 MB.
    - "Create Instance": Runs full provisioning (QCOW2 overlay, hardware identity, system.prop).
    - "Launch Farm": Launches all provisioned instances and arranges into an edge-to-edge 2x2 grid.
    - "Instance List": Displays provisioned instances with RAM and live status.
    - Control actions: Stop All, Delete Instance, Refresh, Re-layout.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Adb      = Join-Path $SdkRoot 'platform-tools\adb.exe'
$Emu      = Join-Path $SdkRoot 'emulator\emulator.exe'
$Avd      = Join-Path $SdkRoot 'cmdline-tools\latest\bin\avdmanager.bat'
$QemuImg  = Join-Path $SdkRoot 'emulator\qemu-img.exe'
$AvdHome  = Join-Path $env:USERPROFILE '.android\avd'
$Cluster  = Join-Path $PSScriptRoot 'cluster-manager.ps1'
$StartFarm= Join-Path $PSScriptRoot 'start-farm.ps1'
$Golden   = Join-Path $AvdHome 'dofus.avd\userdata-golden.img'

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName System.Windows.Forms

# ------------------------------------------------------------------ host capacity
$cs       = Get-CimInstance Win32_ComputerSystem
$hostGB   = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
$cores    = [int]$cs.NumberOfLogicalProcessors
$freeGB   = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1)
$reserve  = if ($hostGB -le 8) { 2.5 } else { 4.0 }
$usableGB = [math]::Max(0, $freeGB - $reserve)

$ramOptions = @('768 MB', '1024 MB (Recommended)', '1536 MB', '2048 MB')

function New-RandomSerial {
  $chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
  -join ((1..12) | ForEach-Object { $chars[(Get-Random -Minimum 0 -Maximum $chars.Length)] })
}

function New-AndroidId {
  -join ((1..16) | ForEach-Object { '{0:x}' -f (Get-Random -Minimum 0 -Maximum 16) })
}

function Get-Instances {
  Get-ChildItem $AvdHome -Filter '*.ini' -EA SilentlyContinue |
    Where-Object { $_.BaseName -ne 'dofus' } |
    ForEach-Object {
      $name = $_.BaseName
      $dir  = Join-Path $AvdHome "$name.avd"
      $cfg  = Join-Path $dir 'config.ini'
      $ram  = 1024
      if (Test-Path $cfg) {
        $m = Select-String -Path $cfg -Pattern '^\s*hw\.ramSize\s*=\s*(\d+)' -EA SilentlyContinue |
             Select-Object -First 1
        if ($m) { $ram = [int]$m.Matches[0].Groups[1].Value }
      }
      $idx = 0
      if ($name -match '(\d+)$') { $idx = [int]$Matches[1] }
      $port = 5554 + (2 * [math]::Max(0, ($idx - 1)))
      $hasOverlay = (Test-Path (Join-Path $dir 'userdata.qcow2')) -or (Test-Path (Join-Path $dir 'userdata-qemu.img.qcow2'))
      [pscustomobject]@{
        Name = $name
        RamMb = $ram
        Port = $port
        Serial = "emulator-$port"
        Dir = $dir
        Overlay = if ($hasOverlay) { 'Yes (QCOW2)' } else { 'No' }
      }
    } | Sort-Object Name
}

function Get-NextInstanceName {
  $existing = @(Get-Instances | ForEach-Object { $_.Name })
  for ($i = 1; $i -le 16; $i++) {
    $cand = 'dofus-{0:d2}' -f $i
    if ($existing -notcontains $cand) { return $cand }
  }
  return "dofus-$((Get-Random -Minimum 10 -Maximum 99))"
}

function Get-LiveSerials {
  if (-not (Test-Path $Adb)) { return @() }
  $out = & $Adb devices 2>$null
  @($out | Select-String -Pattern '^(emulator-\d+)\s+device' |
    ForEach-Object { $_.Matches[0].Groups[1].Value })
}

# ------------------------------------------------------------------- XAML UI
[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Android Farm Manager - High Efficiency Virtualization" Height="620" Width="880"
        WindowStartupLocation="CenterScreen" Background="#1E1E1E">
  <Grid Margin="16">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <!-- Header -->
    <StackPanel Grid.Row="0" Orientation="Horizontal" Margin="0,0,0,12">
      <TextBlock Text="Android Instance Farm" Foreground="#FFFFFF" FontSize="20" FontWeight="Bold" VerticalAlignment="Center"/>
      <TextBlock x:Name="HostInfo" Foreground="#9AA0A6" FontSize="12" Margin="16,0,0,0" VerticalAlignment="Center"/>
    </StackPanel>

    <!-- Provisioning Controls -->
    <Border Grid.Row="1" Margin="0,0,0,12" Padding="12" Background="#252526" CornerRadius="6" BorderBrush="#3F3F46" BorderThickness="1">
      <StackPanel>
        <TextBlock Text="Instance Provisioning (QCOW2 Overlays &amp; Physical Device Profile)" Foreground="#569CD6" FontWeight="Bold" Margin="0,0,0,10"/>
        <Grid Margin="0,0,0,8">
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto"/>
            <ColumnDefinition Width="220"/>
            <ColumnDefinition Width="24"/>
            <ColumnDefinition Width="Auto"/>
            <ColumnDefinition Width="220"/>
            <ColumnDefinition Width="*"/>
            <ColumnDefinition Width="Auto"/>
          </Grid.ColumnDefinitions>

          <TextBlock Grid.Column="0" Text="Instance Name:" Foreground="#CCCCCC" VerticalAlignment="Center" Margin="0,0,10,0"/>
          <TextBox x:Name="TxtInstanceName" Grid.Column="1" Height="26" VerticalContentAlignment="Center"
                   Background="#2D2D30" Foreground="#FFFFFF" BorderBrush="#3F3F46" Padding="6,2"/>

          <TextBlock Grid.Column="3" Text="Memory:" Foreground="#CCCCCC" VerticalAlignment="Center" Margin="0,0,10,0"/>
          <ComboBox x:Name="RamBox" Grid.Column="4" Height="26" VerticalContentAlignment="Center"
                    Background="#2D2D30" Foreground="#1E1E1E"/>

          <Button x:Name="BtnCreate" Grid.Column="6" Content="Create Instance" Width="130" Height="28"
                  Background="#0E639C" Foreground="#FFFFFF" FontWeight="Bold" BorderThickness="0" Cursor="Hand"/>
        </Grid>
        <StackPanel Orientation="Horizontal" Margin="0,4,0,0">
          <TextBlock x:Name="GoldenInfo" Foreground="#9AA0A6" FontSize="11" Margin="0,0,16,0"/>
          <TextBlock x:Name="RamHint" Foreground="#9AA0A6" FontSize="11"/>
        </StackPanel>
      </StackPanel>
    </Border>

    <!-- Instances Grid -->
    <Border Grid.Row="2" Background="#252526" CornerRadius="6" BorderBrush="#3F3F46" BorderThickness="1" Padding="2">
      <DataGrid x:Name="Grid" AutoGenerateColumns="False" IsReadOnly="True"
                HeadersVisibility="Column" Background="#252526" Foreground="#E0E0E0"
                GridLinesVisibility="Horizontal" BorderThickness="0" RowBackground="#252526"
                AlternatingRowBackground="#2D2D30">
        <DataGrid.Columns>
          <DataGridTextColumn Header="Instance Name" Binding="{Binding Name}" Width="150"/>
          <DataGridTextColumn Header="RAM (MB)" Binding="{Binding RamMb}" Width="90"/>
          <DataGridTextColumn Header="Console" Binding="{Binding Port}" Width="80"/>
          <DataGridTextColumn Header="ADB Serial" Binding="{Binding Serial}" Width="140"/>
          <DataGridTextColumn Header="Overlay" Binding="{Binding Overlay}" Width="110"/>
          <DataGridTextColumn Header="Live State" Binding="{Binding State}" Width="*"/>
        </DataGrid.Columns>
      </DataGrid>
    </Border>

    <!-- Toolbar -->
    <StackPanel Grid.Row="3" Orientation="Horizontal" Margin="0,12,0,0">
      <Button x:Name="BtnLaunchFarm" Content="Launch Farm" Width="140" Height="30" Margin="0,0,10,0"
              Background="#238636" Foreground="#FFFFFF" FontWeight="Bold" BorderThickness="0" Cursor="Hand"/>
      <Button x:Name="BtnStopAll" Content="Stop All" Width="90" Height="30" Margin="0,0,10,0"
              Background="#C93B2B" Foreground="#FFFFFF" BorderThickness="0" Cursor="Hand"/>
      <Button x:Name="BtnRelayout" Content="Re-layout (2x2 Grid)" Width="140" Height="30" Margin="0,0,10,0"
              Background="#3F3F46" Foreground="#FFFFFF" BorderThickness="0" Cursor="Hand"/>
      <Button x:Name="BtnDelete" Content="Delete Instance" Width="120" Height="30" Margin="0,0,10,0"
              Background="#3F3F46" Foreground="#FFFFFF" BorderThickness="0" Cursor="Hand"/>
      <Button x:Name="BtnRefresh" Content="Refresh" Width="80" Height="30" Margin="0,0,10,0"
              Background="#3F3F46" Foreground="#FFFFFF" BorderThickness="0" Cursor="Hand"/>
    </StackPanel>

    <!-- Status message -->
    <TextBlock x:Name="Status" Grid.Row="4" Foreground="#9AA0A6" Margin="0,10,0,0" TextWrapping="Wrap"/>
  </Grid>
</Window>
"@

$reader = New-Object System.Xml.XmlNodeReader $xaml
$win = [Windows.Markup.XamlReader]::Load($reader)

$TxtInstanceName = $win.FindName('TxtInstanceName')
$RamBox          = $win.FindName('RamBox')
$RamHint         = $win.FindName('RamHint')
$Grid            = $win.FindName('Grid')
$Status          = $win.FindName('Status')
$HostInfo        = $win.FindName('HostInfo')
$GoldenInfo      = $win.FindName('GoldenInfo')

$RamBox.ItemsSource = $ramOptions
$RamBox.SelectedIndex = 1  # 1024 MB (Recommended)

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
      Serial = $_.Serial; Overlay = $_.Overlay; State = $state
    }
  }
  $Grid.ItemsSource = $rows
  $TxtInstanceName.Text = Get-NextInstanceName

  $HostInfo.Text = "Host: ${hostGB}GB RAM / $cores cores / ${freeGB}GB free"
  if (Test-Path $Golden) {
    $GoldenInfo.Text = "Golden master: $([math]::Round((Get-Item $Golden).Length/1MB,0)) MB (QCOW2 COW active)"
    $GoldenInfo.Foreground = '#98C379'
  } else {
    $GoldenInfo.Text = "Golden master: MISSING ($Golden) - run build-golden-userdata.ps1"
    $GoldenInfo.Foreground = '#E06C75'
  }
}

function Provision-Instance([string]$Name, [int]$RamMb) {
  if (-not (Test-Path $Avd)) { throw "avdmanager not found at $Avd" }
  $dir = Join-Path $AvdHome "$Name.avd"
  $ini = Join-Path $AvdHome "$Name.ini"

  Set-Status "Creating AVD $Name..." '#569CD6'
  if (-not (Test-Path $ini)) {
    cmd /c "echo no | `"$Avd`" create avd -n $Name -k `"system-images;android-29;default;x86_64`" -f 2>&1" | Out-Null
  }

  # Hardware configuration with soft navbar disabled
  $cfg = Join-Path $dir 'config.ini'
  if (Test-Path $cfg) {
    $extra = @(
      "hw.gpu.enabled=yes", "hw.gpu.mode=host",
      "hw.ramSize=$RamMb", "hw.cpu.ncore=2",
      "hw.lcd.width=1280", "hw.lcd.height=720", "hw.lcd.density=213",
      "hw.initialOrientation=landscape",
      "hw.gsmModem=no", "hw.radio=no", "hw.gps=no",
      "hw.camera.back=none", "hw.camera.front=none",
      "hw.keyboard=yes", "hw.mainKeys=yes", "qemu.hw.mainkeys=1",
      "hw.audioInput=no", "hw.audioOutput=no",
      "qemu.vsync=30",
      "disk.dataPartition.size=6442450944"
    )
    $cur = Get-Content $cfg
    foreach ($e in $extra) {
      $k = ($e -split '=')[0]
      if ($cur -match "^$k=") { $cur = $cur -replace "^$k=.*", $e } else { $cur += $e }
    }
    Set-Content -Path $cfg -Value $cur
  }

  # QCOW2 differential overlay
  if (Test-Path $Golden) {
    Set-Status "Creating QCOW2 overlay for $Name..." '#569CD6'
    $backingFmt = 'raw'
    $imgInfo = & $QemuImg info "$Golden" 2>&1 | Out-String
    if ($imgInfo -match 'file format:\s*qcow2') { $backingFmt = 'qcow2' }
    $targetQcow2 = Join-Path $dir 'userdata.qcow2'
    Remove-Item $targetQcow2 -Force -EA SilentlyContinue
    & $QemuImg create -f qcow2 -b "$Golden" -F $backingFmt "$targetQcow2" | Out-Null
    $emuQcow2 = Join-Path $dir 'userdata-qemu.img.qcow2'
    Remove-Item $emuQcow2 -Force -EA SilentlyContinue
    try {
      New-Item -ItemType HardLink -Path $emuQcow2 -Target $targetQcow2 -Force | Out-Null
    } catch {
      Copy-Item $targetQcow2 $emuQcow2 -Force
    }
  }

  # Unique per-instance identifiers & standardized device profile
  $serial = New-RandomSerial
  $androidId = New-AndroidId
  @{ Serial = $serial; AndroidId = $androidId } | ConvertTo-Json | Set-Content -Path (Join-Path $dir 'identity.json')

  $sysProp = @"
ro.product.brand=samsung
ro.product.manufacturer=samsung
ro.product.model=SM-A515F
ro.product.name=a51nsxx
ro.product.device=a51
ro.build.flavor=a51nsxx-user
ro.build.type=user
ro.build.tags=release-keys
ro.build.fingerprint=samsung/a51nsxx/a51:10/QP1A.190711.020/A515FXXU1ATA7:user/release-keys
ro.hardware=exynos9611
ro.kernel.qemu=0
ro.boot.qemu=0
qemu.hw.mainkeys=1
ro.serialno=$serial
ro.boot.serialno=$serial
gsm.sim.state=READY
gsm.sim.operator.numeric=60401
gsm.network.type=LTE
"@
  Set-Content -Path (Join-Path $dir 'system.prop') -Value $sysProp
  Set-Status "Provisioned $Name with QCOW2 overlay & Samsung profile (serial=$serial)." '#98C379'
}

# ------------------------------------------------------------------- Button Handlers
$win.FindName('BtnRefresh').Add_Click({ Refresh-Grid })

$win.FindName('BtnCreate').Add_Click({
  $name = $TxtInstanceName.Text.Trim()
  if (-not $name) { Set-Status 'Please enter an instance name.' '#E06C75'; return }
  $sel = $RamBox.SelectedItem
  $r = 1024
  if ($sel -match '(\d+)') { $r = [int]$matches[1] }

  try {
    Provision-Instance -Name $name -RamMb $r
    Refresh-Grid
  } catch {
    Set-Status "Creation failed: $($_.Exception.Message)" '#E06C75'
  }
})

$win.FindName('BtnDelete').Add_Click({
  $sel = $Grid.SelectedItem
  if (-not $sel) { Set-Status 'Select an instance row to delete.' '#E06C75'; return }
  if ($sel.State -eq 'running') { Set-Status 'Stop that instance before deleting it.' '#E5C07B'; return }
  $ans = [System.Windows.MessageBox]::Show(
    "Delete instance $($sel.Name)? This will permanently remove its QCOW2 overlay and data.",
    "Confirm Delete",
    [System.Windows.MessageBoxButton]::YesNo,
    [System.Windows.MessageBoxImage]::Warning
  )
  if ($ans -eq [System.Windows.MessageBoxResult]::Yes) {
    Remove-Item (Join-Path $AvdHome "$($sel.Name).avd") -Recurse -Force -EA SilentlyContinue
    Remove-Item (Join-Path $AvdHome "$($sel.Name).ini") -Force -EA SilentlyContinue
    Set-Status "Deleted $($sel.Name)." '#98C379'
    Refresh-Grid
  }
})

$win.FindName('BtnLaunchFarm').Add_Click({
  $inst = @(Get-Instances)
  if ($inst.Count -eq 0) { Set-Status 'No instances provisioned. Click Create Instance first.' '#E06C75'; return }
  
  $sel = $RamBox.SelectedItem
  $r = 1024
  if ($sel -match '(\d+)') { $r = [int]$matches[1] }

  Set-Status "Launching farm ($($inst.Count) instance(s))..." '#569CD6'
  
  # Start instances via cluster-manager or start-farm
  $n = [math]::Min(4, $inst.Count)
  $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $StartFarm, '-Count', "$n", '-RamMb', "$r", '-Force')
  $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -PassThru -WindowStyle Minimized
  $p | Wait-Process -Timeout 120 -EA SilentlyContinue
  
  # Re-layout windows in 2x2 grid
  Start-Sleep -Seconds 3
  . (Join-Path $PSScriptRoot 'layout.ps1')
  $live = Get-LiveSerials
  $pids = @()
  foreach ($s in $live) {
    $port_ = [int]($s -replace 'emulator-','')
    $proc = Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" -EA SilentlyContinue |
            Where-Object { $_.CommandLine -match "-port\s+$port_\b" } | Select-Object -First 1
    if ($proc) { $pids += $proc.ProcessId }
  }
  if ($pids.Count -gt 0) {
    Set-FarmLayout -Procs $pids -EdgeToEdge
  }

  Set-Status "Farm launched ($($pids.Count) windows arranged into edge-to-edge 2x2 grid)." '#98C379'
  Refresh-Grid
})

$win.FindName('BtnStopAll').Add_Click({
  $live = Get-LiveSerials
  foreach ($s in $live) {
    & $Adb -s $s emu kill 2>&1 | Out-Null
  }
  Set-Status "Shutdown signal sent to $($live.Count) instance(s)." '#98C379'
  Start-Sleep -Seconds 3
  Refresh-Grid
})

$win.FindName('BtnRelayout').Add_Click({
  . (Join-Path $PSScriptRoot 'layout.ps1')
  $live = Get-LiveSerials
  if (-not $live) { Set-Status 'No running instances to arrange.' '#E5C07B'; return }
  $pids = @()
  foreach ($s in $live) {
    $port_ = [int]($s -replace 'emulator-','')
    $p = Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" -EA SilentlyContinue |
         Where-Object { $_.CommandLine -match "-port\s+$port_\b" } | Select-Object -First 1
    if ($p) { $pids += $p.ProcessId }
  }
  if ($pids.Count -eq 0) { Set-Status 'Could not map live instances to emulator processes.' '#E06C75'; return }
  Set-FarmLayout -Procs $pids -EdgeToEdge
  Set-Status "Arranged $($pids.Count) window(s) into edge-to-edge 2x2 grid." '#98C379'
})

Refresh-Grid
Set-Status 'Ready. Enter instance name, pick RAM, and click Create Instance.'
$win.ShowDialog() | Out-Null
