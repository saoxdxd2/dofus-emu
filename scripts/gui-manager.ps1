<#
.SYNOPSIS
  Ultimate High-Efficiency Android Farm Manager & Anti-Detection Control Center.

.DESCRIPTION
  Modern WPF application featuring:
    - Tab 1: Live Farm Dashboard with 2x2 Visual Slot Map, status badges, single/all launch & stop controls, and live grid retiling.
    - Tab 2: Hardware Diagnostics & 1-Click Auto-Tuning for laptop drivers (Intel UHD / WHPX).
    - Tab 3: Deep Anti-Detection & Network Center (Samsung OUI MAC, Orange France SIM, Cloudflare DNS, Per-Instance Proxy routing).
    - Tab 4: Fast Instance Provisioning (QCOW2 differential overlays, custom cores, RAM, resolution).
    - Double-click inline renaming (works even while running).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SdkRoot  = if ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Adb      = Join-Path $SdkRoot 'platform-tools\adb.exe'
$Emu      = Join-Path $SdkRoot 'emulator\emulator.exe'
$Avd      = Join-Path $SdkRoot 'cmdline-tools\latest\bin\avdmanager.bat'
$QemuImg  = Join-Path $SdkRoot 'emulator\qemu-img.exe'
$AvdHome  = Join-Path $env:USERPROFILE '.android\avd'
$Cluster  = Join-Path $PSScriptRoot 'cluster-manager.ps1'
$StartFarm= Join-Path $PSScriptRoot 'start-farm.ps1'
$Layout   = Join-Path $PSScriptRoot 'layout.ps1'
$Golden   = Join-Path $AvdHome 'dofus-template.avd\userdata-golden.img'
if (-not (Test-Path $Golden)) { $Golden = Join-Path $AvdHome 'dofus.avd\userdata-golden.img' }

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName Microsoft.VisualBasic

# Load Hardware Diagnostics
. (Join-Path $PSScriptRoot 'detect-hardware.ps1')
$diag = Get-HardwareDiagnostics

function New-RandomSerial {
  $chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
  -join ((1..12) | ForEach-Object { $chars[(Get-Random -Minimum 0 -Maximum $chars.Length)] })
}

function New-AndroidId {
  -join ((1..16) | ForEach-Object { '{0:x}' -f (Get-Random -Minimum 0 -Maximum 16) })
}

function Get-Instances {
  Get-ChildItem $AvdHome -Filter '*.ini' -EA SilentlyContinue |
    Where-Object { $_.BaseName -ne 'dofus' -and $_.BaseName -ne 'dofus-template' } |
    ForEach-Object {
      $name = $_.BaseName
      $dir  = Join-Path $AvdHome "$name.avd"
      $cfg  = Join-Path $dir 'config.ini'
      $ram  = 768
      $cpuCores = 1
      if (Test-Path $cfg) {
        $m = Select-String -Path $cfg -Pattern '^\s*hw\.ramSize\s*=\s*(\d+)' -EA SilentlyContinue | Select-Object -First 1
        if ($m) { $ram = [int]$m.Matches[0].Groups[1].Value }
        $c = Select-String -Path $cfg -Pattern '^\s*hw\.cpu\.ncore\s*=\s*(\d+)' -EA SilentlyContinue | Select-Object -First 1
        if ($c) { $cpuCores = [int]$c.Matches[0].Groups[1].Value }
      }
      $idx = 0
      if ($name -match '(\d+)$') { $idx = [int]$Matches[1] }
      $port = 5554 + (2 * [math]::Max(0, ($idx - 1)))
      $hasOverlay = (Test-Path (Join-Path $dir 'userdata.qcow2')) -or (Test-Path (Join-Path $dir 'userdata-qemu.img.qcow2'))

      # Read alias
      $alias = $name
      $aliasFile = Join-Path $dir 'alias.json'
      if (Test-Path $aliasFile) {
        try {
          $aObj = Get-Content $aliasFile -Raw | ConvertFrom-Json
          if ($aObj.Alias) { $alias = $aObj.Alias }
        } catch {}
      }

      # Read proxy
      $proxy = "Direct LAN (No Proxy)"
      $proxyFile = Join-Path $dir 'proxy.txt'
      if (Test-Path $proxyFile) {
        $p = (Get-Content $proxyFile -Raw).Trim()
        if ($p) { $proxy = $p }
      }

      # Samsung MAC
      $mac = "bc:72:b7:{0:x2}:{1:x2}:{2:x2}" -f (10 + $idx), (20 + $idx), $idx

      [pscustomobject]@{
        DisplayName = $alias
        Name        = $name
        Index       = $idx
        RamMb       = $ram
        Cores       = $cpuCores
        Port        = $port
        Serial      = "emulator-$port"
        Dir         = $dir
        Overlay     = if ($hasOverlay) { 'Yes (QCOW2)' } else { 'No' }
        Mac         = $mac
        Proxy       = $proxy
      }
    } | Sort-Object Index
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
        Title="Dofus Touch Virtualization &amp; Stealth Farm Manager" Height="730" Width="1020"
        WindowStartupLocation="CenterScreen" Background="#181818">
  <Window.Resources>
    <Style TargetType="TabItem">
      <Setter Property="Background" Value="#252526"/>
      <Setter Property="Foreground" Value="#CCCCCC"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Padding" Value="16,8"/>
      <Setter Property="BorderThickness" Value="0"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="TabItem">
            <Border x:Name="TabBorder" Background="{TemplateBinding Background}" Padding="{TemplateBinding Padding}" CornerRadius="6,6,0,0" Margin="0,0,4,0">
              <ContentPresenter ContentSource="Header" HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsSelected" Value="True">
                <Setter TargetName="TabBorder" Property="Background" Value="#0E639C"/>
                <Setter Property="Foreground" Value="#FFFFFF"/>
              </Trigger>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="TabBorder" Property="Background" Value="#3F3F46"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>

  <Grid Margin="16">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <!-- Top Navigation Header -->
    <Border Grid.Row="0" Background="#202022" CornerRadius="8" Padding="14,12" Margin="0,0,0,12" BorderBrush="#333333" BorderThickness="1">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <StackPanel Grid.Column="0" Orientation="Horizontal" VerticalAlignment="Center">
          <TextBlock Text="⚡ DOFUS TOUCH FARM" Foreground="#569CD6" FontSize="18" FontWeight="ExtraBold" VerticalAlignment="Center"/>
          <Border Background="#2D2D30" CornerRadius="4" Padding="6,2" Margin="12,0,0,0" VerticalAlignment="Center">
            <TextBlock Text="🔒 Samsung A51 Disguise: ALWAYS-ON (LOCKED)" Foreground="#98C379" FontSize="11" FontWeight="Bold"/>
          </Border>
          <Border Background="#2D2D30" CornerRadius="4" Padding="6,2" Margin="8,0,0,0" VerticalAlignment="Center">
            <TextBlock x:Name="HeaderGpuText" Text="Intel UHD Graphics Passthrough" Foreground="#DCDCAA" FontSize="11"/>
          </Border>
        </StackPanel>

        <StackPanel Grid.Column="1" Orientation="Horizontal" VerticalAlignment="Center">
          <TextBlock x:Name="HostStatsHeader" Text="Host: 7.8 GB RAM (4.9 GB Free) | 8 Cores" Foreground="#9AA0A6" FontSize="11" VerticalAlignment="Center" Margin="0,0,12,0"/>
          <Button x:Name="BtnHeaderAutoTune" Content="⚡ 1-Click Auto-Tune" Height="26" Width="130"
                  Background="#238636" Foreground="#FFFFFF" FontWeight="Bold" FontSize="11" BorderThickness="0" Cursor="Hand"/>
          <Button x:Name="BtnHeaderUninstall" Content="🗑 Uninstall" Height="26" Width="85"
                  Background="#4A1D1D" Foreground="#FFAAAA" FontWeight="Bold" FontSize="11" BorderThickness="0" Cursor="Hand" Margin="8,0,0,0"
                  ToolTip="Safely halt instances, clean up shortcuts, and uninstall farm"/>
        </StackPanel>
      </Grid>
    </Border>

    <!-- Main Tab Control -->
    <TabControl Grid.Row="1" Background="Transparent" BorderThickness="0">
      
      <!-- TAB 1: Live Farm Dashboard -->
      <TabItem Header="  🎮 Farm Dashboard  ">
        <Grid Margin="0,12,0,0">
          <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
          </Grid.RowDefinitions>

          <!-- 2x2 Grid Visual Slot Map & Quick Control Center -->
          <Grid Grid.Row="0" Margin="0,0,0,12">
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="380"/>
              <ColumnDefinition Width="12"/>
              <ColumnDefinition Width="*"/>
            </Grid.ColumnDefinitions>

            <!-- Visual 2x2 Layout Map Card -->
            <Border Grid.Column="0" Background="#202022" CornerRadius="8" Padding="12" BorderBrush="#333333" BorderThickness="1">
              <StackPanel>
                <TextBlock Text="2x2 Screen Tile Preview" FontWeight="Bold" Foreground="#CCCCCC" FontSize="12" Margin="0,0,0,8"/>
                <Grid Height="128">
                  <Grid.RowDefinitions>
                    <RowDefinition Height="*"/>
                    <RowDefinition Height="4"/>
                    <RowDefinition Height="*"/>
                  </Grid.RowDefinitions>
                  <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="4"/>
                    <ColumnDefinition Width="*"/>
                  </Grid.ColumnDefinitions>

                  <!-- Slot 1 -->
                  <Border x:Name="Slot1Border" Grid.Row="0" Grid.Column="0" Background="#28282B" CornerRadius="4" Padding="6">
                    <Grid>
                      <Grid.RowDefinitions>
                        <RowDefinition Height="*"/>
                        <RowDefinition Height="Auto"/>
                      </Grid.RowDefinitions>
                      <StackPanel Grid.Row="0" VerticalAlignment="Center">
                        <TextBlock x:Name="Slot1Name" Text="Slot 1: dofus-01" Foreground="#FFF" FontWeight="Bold" FontSize="11" TextTrimming="CharacterEllipsis"/>
                        <TextBlock x:Name="Slot1Status" Text="● Stopped (5554)" Foreground="#888" FontSize="10"/>
                      </StackPanel>
                      <StackPanel Grid.Row="1" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,3,0,0">
                        <Button x:Name="BtnSlot1Reduce" Content="_" Width="20" Height="17" FontSize="9" FontWeight="Bold" Background="#38383B" Foreground="#CCCCCC" BorderThickness="0" Margin="0,0,2,0" Cursor="Hand" ToolTip="Reduce / Minimize"/>
                        <Button x:Name="BtnSlot1Max" Content="🗖" Width="20" Height="17" FontSize="9" FontWeight="Bold" Background="#38383B" Foreground="#CCCCCC" BorderThickness="0" Margin="0,0,2,0" Cursor="Hand" ToolTip="Maximize / Restore Tile"/>
                        <Button x:Name="BtnSlot1Exit" Content="✕" Width="20" Height="17" FontSize="9" FontWeight="Bold" Background="#5A1D1D" Foreground="#FFAAAA" BorderThickness="0" Cursor="Hand" ToolTip="Exit / Close Instance"/>
                      </StackPanel>
                    </Grid>
                  </Border>

                  <!-- Slot 2 -->
                  <Border x:Name="Slot2Border" Grid.Row="0" Grid.Column="2" Background="#28282B" CornerRadius="4" Padding="6">
                    <Grid>
                      <Grid.RowDefinitions>
                        <RowDefinition Height="*"/>
                        <RowDefinition Height="Auto"/>
                      </Grid.RowDefinitions>
                      <StackPanel Grid.Row="0" VerticalAlignment="Center">
                        <TextBlock x:Name="Slot2Name" Text="Slot 2: dofus-02" Foreground="#FFF" FontWeight="Bold" FontSize="11" TextTrimming="CharacterEllipsis"/>
                        <TextBlock x:Name="Slot2Status" Text="● Stopped (5556)" Foreground="#888" FontSize="10"/>
                      </StackPanel>
                      <StackPanel Grid.Row="1" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,3,0,0">
                        <Button x:Name="BtnSlot2Reduce" Content="_" Width="20" Height="17" FontSize="9" FontWeight="Bold" Background="#38383B" Foreground="#CCCCCC" BorderThickness="0" Margin="0,0,2,0" Cursor="Hand" ToolTip="Reduce / Minimize"/>
                        <Button x:Name="BtnSlot2Max" Content="🗖" Width="20" Height="17" FontSize="9" FontWeight="Bold" Background="#38383B" Foreground="#CCCCCC" BorderThickness="0" Margin="0,0,2,0" Cursor="Hand" ToolTip="Maximize / Restore Tile"/>
                        <Button x:Name="BtnSlot2Exit" Content="✕" Width="20" Height="17" FontSize="9" FontWeight="Bold" Background="#5A1D1D" Foreground="#FFAAAA" BorderThickness="0" Cursor="Hand" ToolTip="Exit / Close Instance"/>
                      </StackPanel>
                    </Grid>
                  </Border>

                  <!-- Slot 3 -->
                  <Border x:Name="Slot3Border" Grid.Row="2" Grid.Column="0" Background="#28282B" CornerRadius="4" Padding="6">
                    <Grid>
                      <Grid.RowDefinitions>
                        <RowDefinition Height="*"/>
                        <RowDefinition Height="Auto"/>
                      </Grid.RowDefinitions>
                      <StackPanel Grid.Row="0" VerticalAlignment="Center">
                        <TextBlock x:Name="Slot3Name" Text="Slot 3: dofus-03" Foreground="#FFF" FontWeight="Bold" FontSize="11" TextTrimming="CharacterEllipsis"/>
                        <TextBlock x:Name="Slot3Status" Text="● Stopped (5558)" Foreground="#888" FontSize="10"/>
                      </StackPanel>
                      <StackPanel Grid.Row="1" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,3,0,0">
                        <Button x:Name="BtnSlot3Reduce" Content="_" Width="20" Height="17" FontSize="9" FontWeight="Bold" Background="#38383B" Foreground="#CCCCCC" BorderThickness="0" Margin="0,0,2,0" Cursor="Hand" ToolTip="Reduce / Minimize"/>
                        <Button x:Name="BtnSlot3Max" Content="🗖" Width="20" Height="17" FontSize="9" FontWeight="Bold" Background="#38383B" Foreground="#CCCCCC" BorderThickness="0" Margin="0,0,2,0" Cursor="Hand" ToolTip="Maximize / Restore Tile"/>
                        <Button x:Name="BtnSlot3Exit" Content="✕" Width="20" Height="17" FontSize="9" FontWeight="Bold" Background="#5A1D1D" Foreground="#FFAAAA" BorderThickness="0" Cursor="Hand" ToolTip="Exit / Close Instance"/>
                      </StackPanel>
                    </Grid>
                  </Border>

                  <!-- Slot 4 -->
                  <Border x:Name="Slot4Border" Grid.Row="2" Grid.Column="2" Background="#28282B" CornerRadius="4" Padding="6">
                    <Grid>
                      <Grid.RowDefinitions>
                        <RowDefinition Height="*"/>
                        <RowDefinition Height="Auto"/>
                      </Grid.RowDefinitions>
                      <StackPanel Grid.Row="0" VerticalAlignment="Center">
                        <TextBlock x:Name="Slot4Name" Text="Slot 4: dofus-04" Foreground="#FFF" FontWeight="Bold" FontSize="11" TextTrimming="CharacterEllipsis"/>
                        <TextBlock x:Name="Slot4Status" Text="● Stopped (5560)" Foreground="#888" FontSize="10"/>
                      </StackPanel>
                      <StackPanel Grid.Row="1" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,3,0,0">
                        <Button x:Name="BtnSlot4Reduce" Content="_" Width="20" Height="17" FontSize="9" FontWeight="Bold" Background="#38383B" Foreground="#CCCCCC" BorderThickness="0" Margin="0,0,2,0" Cursor="Hand" ToolTip="Reduce / Minimize"/>
                        <Button x:Name="BtnSlot4Max" Content="🗖" Width="20" Height="17" FontSize="9" FontWeight="Bold" Background="#38383B" Foreground="#CCCCCC" BorderThickness="0" Margin="0,0,2,0" Cursor="Hand" ToolTip="Maximize / Restore Tile"/>
                        <Button x:Name="BtnSlot4Exit" Content="✕" Width="20" Height="17" FontSize="9" FontWeight="Bold" Background="#5A1D1D" Foreground="#FFAAAA" BorderThickness="0" Cursor="Hand" ToolTip="Exit / Close Instance"/>
                      </StackPanel>
                    </Grid>
                  </Border>
                </Grid>
              </StackPanel>
            </Border>

            <!-- Major Action Controls -->
            <Border Grid.Column="2" Background="#202022" CornerRadius="8" Padding="14" BorderBrush="#333333" BorderThickness="1">
              <Grid>
                <Grid.RowDefinitions>
                  <RowDefinition Height="Auto"/>
                  <RowDefinition Height="*"/>
                </Grid.RowDefinitions>
                <TextBlock Grid.Row="0" Text="Cluster Orchestration" FontWeight="Bold" Foreground="#CCCCCC" FontSize="12" Margin="0,0,0,10"/>
                
                <Grid Grid.Row="1">
                  <Grid.RowDefinitions>
                    <RowDefinition Height="40"/>
                    <RowDefinition Height="8"/>
                    <RowDefinition Height="36"/>
                  </Grid.RowDefinitions>
                  <Grid.ColumnDefinitions>
                    <ColumnDefinition Width="*"/>
                    <ColumnDefinition Width="10"/>
                    <ColumnDefinition Width="160"/>
                  </Grid.ColumnDefinitions>

                  <Button x:Name="BtnLaunchFarm" Grid.Row="0" Grid.Column="0" Content="▶  LAUNCH 4-INSTANCE FARM (2x2 GRID)"
                          Background="#238636" Foreground="#FFFFFF" FontWeight="ExtraBold" FontSize="13" BorderThickness="0" Cursor="Hand"/>

                  <Button x:Name="BtnStopAll" Grid.Row="0" Grid.Column="2" Content="⏹  STOP ALL INSTANCES"
                          Background="#DA3633" Foreground="#FFFFFF" FontWeight="Bold" FontSize="11" BorderThickness="0" Cursor="Hand"/>

                  <StackPanel Grid.Row="2" Grid.Column="0" Grid.ColumnSpan="3" Orientation="Horizontal">
                    <Button x:Name="BtnRelayout" Content="⊞ Re-tile 2x2 Edge-to-Edge Grid" Width="200" Height="32" Margin="0,0,8,0"
                            Background="#3F3F46" Foreground="#FFFFFF" FontSize="11" BorderThickness="0" Cursor="Hand"/>
                    <Button x:Name="BtnAuditAll" Content="🛡 Audit Stealth &amp; WebGL Leaks" Width="190" Height="32" Margin="0,0,8,0"
                            Background="#0E639C" Foreground="#FFFFFF" FontSize="11" BorderThickness="0" Cursor="Hand"/>
                    <Button x:Name="BtnRefreshDashboard" Content="🔄 Refresh Status" Width="120" Height="32"
                            Background="#3F3F46" Foreground="#FFFFFF" FontSize="11" BorderThickness="0" Cursor="Hand"/>
                  </StackPanel>
                </Grid>
              </Grid>
            </Border>
          </Grid>

          <!-- Instances DataGrid -->
          <Border Grid.Row="1" Background="#202022" CornerRadius="8" BorderBrush="#333333" BorderThickness="1" Padding="2">
            <DataGrid x:Name="Grid" AutoGenerateColumns="False" IsReadOnly="True"
                      HeadersVisibility="Column" Background="#202022" Foreground="#E0E0E0"
                      GridLinesVisibility="Horizontal" BorderThickness="0" RowBackground="#202022"
                      AlternatingRowBackground="#262629">
              <DataGrid.ContextMenu>
                <ContextMenu Background="#252526" Foreground="#E0E0E0">
                  <MenuItem x:Name="CtxLaunch" Header="▶ Launch This Instance"/>
                  <MenuItem x:Name="CtxStop" Header="⏹ Stop This Instance"/>
                  <MenuItem x:Name="CtxRename" Header="✏ Rename (Double-Click)..."/>
                  <MenuItem x:Name="CtxProxy" Header="🌐 Configure Proxy / SOCKS5..."/>
                  <Separator/>
                  <MenuItem x:Name="CtxAudit" Header="🛡 Run Deep Spoof Audit"/>
                  <MenuItem x:Name="CtxDelete" Header="🗑 Delete Instance"/>
                </ContextMenu>
              </DataGrid.ContextMenu>
              <DataGrid.Columns>
                <DataGridTextColumn Header="State" Binding="{Binding StateDisplay}" Width="90"/>
                <DataGridTextColumn Header="Display Name (Double-Click)" Binding="{Binding DisplayName}" Width="190"/>
                <DataGridTextColumn Header="AVD" Binding="{Binding Name}" Width="90"/>
                <DataGridTextColumn Header="RAM" Binding="{Binding RamMb}" Width="65"/>
                <DataGridTextColumn Header="vCPU" Binding="{Binding Cores}" Width="55"/>
                <DataGridTextColumn Header="Console" Binding="{Binding Port}" Width="65"/>
                <DataGridTextColumn Header="ADB Serial" Binding="{Binding Serial}" Width="110"/>
                <DataGridTextColumn Header="Samsung MAC (OUI)" Binding="{Binding Mac}" Width="130"/>
                <DataGridTextColumn Header="Network Proxy" Binding="{Binding Proxy}" Width="*"/>
              </DataGrid.Columns>
            </DataGrid>
          </Border>

          <!-- Selected Instance Action Toolbar -->
          <Border Grid.Row="2" Background="#202022" CornerRadius="8" Padding="10" Margin="0,10,0,0" BorderBrush="#333333" BorderThickness="1">
            <StackPanel Orientation="Horizontal">
              <TextBlock Text="Selected Instance:" Foreground="#888" VerticalAlignment="Center" Margin="0,0,10,0" FontSize="11"/>
              <Button x:Name="BtnLaunchSingle" Content="▶ Launch Selected" Width="130" Height="28" Margin="0,0,6,0"
                      Background="#2EA043" Foreground="#FFFFFF" FontWeight="Bold" FontSize="11" BorderThickness="0" Cursor="Hand"/>
              <Button x:Name="BtnStopSingle" Content="⏹ Stop Selected" Width="110" Height="28" Margin="0,0,6,0"
                      Background="#DA3633" Foreground="#FFFFFF" FontWeight="Bold" FontSize="11" BorderThickness="0" Cursor="Hand"/>
              <Button x:Name="BtnRename" Content="✏ Rename..." Width="90" Height="28" Margin="0,0,6,0"
                      Background="#0E639C" Foreground="#FFFFFF" FontSize="11" BorderThickness="0" Cursor="Hand"/>
              <Button x:Name="BtnSetProxy" Content="🌐 Set Proxy..." Width="100" Height="28" Margin="0,0,6,0"
                      Background="#3F3F46" Foreground="#FFFFFF" FontSize="11" BorderThickness="0" Cursor="Hand"/>
              <Button x:Name="BtnAuditSingle" Content="🛡 Audit Spoofing" Width="110" Height="28" Margin="0,0,6,0"
                      Background="#3F3F46" Foreground="#FFFFFF" FontSize="11" BorderThickness="0" Cursor="Hand"/>
              <Button x:Name="BtnDelete" Content="🗑 Delete" Width="80" Height="28" Margin="0,0,0,0"
                      Background="#3F3F46" Foreground="#FFFFFF" FontSize="11" BorderThickness="0" Cursor="Hand"/>
            </StackPanel>
          </Border>

          <!-- Row 3: Quick Navigation Controls (Replaces Side Toolbar) -->
          <Border Grid.Row="3" Background="#202022" CornerRadius="8" Padding="10" Margin="0,8,0,0" BorderBrush="#333333" BorderThickness="1">
            <Grid>
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="Auto"/>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="Auto"/>
              </Grid.ColumnDefinitions>
              <StackPanel Grid.Column="0" Orientation="Horizontal" VerticalAlignment="Center">
                <TextBlock Text="⚡ Quick Controls:" Foreground="#569CD6" FontWeight="Bold" FontSize="11" VerticalAlignment="Center" Margin="0,0,8,0"/>
                <Button x:Name="BtnNavBack" Content="◀ Return" Width="72" Height="26" Margin="0,0,5,0"
                        Background="#3F3F46" Foreground="#FFFFFF" FontSize="11" BorderThickness="0" Cursor="Hand" ToolTip="Sends KEYCODE_BACK (ESC) to selected or active instance"/>
                <Button x:Name="BtnNavHome" Content="● Home" Width="64" Height="26" Margin="0,0,5,0"
                        Background="#3F3F46" Foreground="#FFFFFF" FontSize="11" BorderThickness="0" Cursor="Hand" ToolTip="Sends KEYCODE_HOME to selected or active instance"/>
                <Button x:Name="BtnNavRecents" Content="■ Tabs" Width="60" Height="26" Margin="0,0,5,0"
                        Background="#3F3F46" Foreground="#FFFFFF" FontSize="11" BorderThickness="0" Cursor="Hand" ToolTip="Sends KEYCODE_APP_SWITCH to selected or active instance"/>
                <Button x:Name="BtnNavSettings" Content="⚙ Settings" Width="76" Height="26" Margin="0,0,8,0"
                        Background="#3F3F46" Foreground="#FFFFFF" FontSize="11" BorderThickness="0" Cursor="Hand" ToolTip="Opens Android Settings on selected instance"/>
                <Button x:Name="BtnReduceAll" Content="_ Minimize All" Width="95" Height="26" Margin="0,0,5,0"
                        Background="#2D2D30" Foreground="#CCCCCC" FontSize="11" BorderThickness="0" Cursor="Hand" ToolTip="Reduce / minimize all running emulator windows"/>
                <Button x:Name="BtnRetileAll" Content="🗖 Retile 2x2" Width="85" Height="26" Margin="0,0,5,0"
                        Background="#2D2D30" Foreground="#CCCCCC" FontSize="11" BorderThickness="0" Cursor="Hand" ToolTip="Restore all running windows into 2x2 edge-to-edge grid"/>
              </StackPanel>
              <StackPanel Grid.Column="2" Orientation="Horizontal" VerticalAlignment="Center">
                <Button x:Name="BtnToggleBorders" Content="🗖 Toggle Window Borders" Width="165" Height="26" Margin="0,0,6,0"
                        Background="#3F3F46" Foreground="#FFFFFF" FontWeight="Bold" FontSize="11" BorderThickness="0" Cursor="Hand" ToolTip="Toggle between seamless edge-to-edge canvas and standard window title bars"/>
                <Button x:Name="BtnToggleSidebars" Content="📱 Toggle Sidebars" Width="135" Height="26"
                        Background="#0E639C" Foreground="#FFFFFF" FontWeight="Bold" FontSize="11" BorderThickness="0" Cursor="Hand" ToolTip="Show or Hide the Qt side menu toolbar to reclaim screen space"/>
              </StackPanel>
            </Grid>
          </Border>
        </Grid>
      </TabItem>

      <!-- TAB 2: Hardware Diagnostics & Compatibility -->
      <TabItem Header="  💻 Hardware &amp; Drivers  ">
        <Border Background="#202022" CornerRadius="8" Padding="20" Margin="0,12,0,0" BorderBrush="#333333" BorderThickness="1">
          <ScrollViewer VerticalScrollBarVisibility="Auto">
            <StackPanel>
              <TextBlock Text="Host Hardware &amp; Driver Compatibility Profile" FontSize="16" FontWeight="Bold" Foreground="#FFFFFF" Margin="0,0,0,6"/>
              <TextBlock Text="Diagnostics engine scans your host CPU, GPU driver, and memory to eliminate bottlenecks and optimize virtualization cost."
                         Foreground="#9AA0A6" FontSize="12" Margin="0,0,0,16"/>

              <Grid Margin="0,0,0,16">
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="*"/>
                  <ColumnDefinition Width="12"/>
                  <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>

                <!-- CPU Specs -->
                <Border Grid.Column="0" Background="#181818" CornerRadius="6" Padding="14" BorderBrush="#333333" BorderThickness="1">
                  <StackPanel>
                    <TextBlock Text="PROCESSOR &amp; LOGICAL CORES" Foreground="#569CD6" FontWeight="Bold" FontSize="12" Margin="0,0,0,8"/>
                    <TextBlock x:Name="TxtDiagCpu" Text="CPU: Intel Core i5-1035G1" Foreground="#FFF" FontSize="13" FontWeight="SemiBold"/>
                    <TextBlock x:Name="TxtDiagCores" Text="Cores: 4 Physical / 8 Logical Threads" Foreground="#AAA" FontSize="11" Margin="0,4,0,0"/>
                    <TextBlock Text="Virtualization Engine: Windows Hypervisor Platform (WHPX)" Foreground="#98C379" FontSize="11" Margin="0,4,0,0"/>
                  </StackPanel>
                </Border>

                <!-- GPU Specs -->
                <Border Grid.Column="2" Background="#181818" CornerRadius="6" Padding="14" BorderBrush="#333333" BorderThickness="1">
                  <StackPanel>
                    <TextBlock Text="GRAPHICS HARDWARE &amp; DRIVER" Foreground="#DCDCAA" FontWeight="Bold" FontSize="12" Margin="0,0,0,8"/>
                    <TextBlock x:Name="TxtDiagGpu" Text="GPU: Intel(R) UHD Graphics" Foreground="#FFF" FontSize="13" FontWeight="SemiBold"/>
                    <TextBlock x:Name="TxtDiagGpuDriver" Text="Driver Version: 30.0.101.2079" Foreground="#AAA" FontSize="11" Margin="0,4,0,0"/>
                    <TextBlock Text="Passthrough Mode: -gpu host (Direct3D 11 ANGLE GLES 3.0)" Foreground="#98C379" FontSize="11" Margin="0,4,0,0"/>
                  </StackPanel>
                </Border>
              </Grid>

              <!-- Memory & Tuning Analysis -->
              <Border Background="#181818" CornerRadius="6" Padding="16" BorderBrush="#0E639C" BorderThickness="1" Margin="0,0,0,16">
                <StackPanel>
                  <TextBlock Text="RECOMMENDED OPTIMAL VIRTUALIZATION PROFILE" Foreground="#569CD6" FontWeight="Bold" FontSize="13" Margin="0,0,0,6"/>
                  <TextBlock x:Name="TxtDiagProfileName" Text="Profile: Efficiency 1-Core Mobile Farm" Foreground="#FFF" FontSize="14" FontWeight="Bold"/>
                  <TextBlock x:Name="TxtDiagReason" Text="Host RAM is 8 GB. Allocating 768 MB per instance with 512 MB zRAM prevents host paging..."
                             Foreground="#CCC" FontSize="12" TextWrapping="Wrap" Margin="0,6,0,12"/>

                  <StackPanel Orientation="Horizontal">
                    <Button x:Name="BtnApplyAutoTuneProfile" Content="⚡ Apply Auto-Tuned Profile to All Instances" Width="280" Height="32"
                            Background="#238636" Foreground="#FFFFFF" FontWeight="Bold" BorderThickness="0" Cursor="Hand"/>
                    <TextBlock Text="(Updates config.ini across all instances instantly)" Foreground="#888" VerticalAlignment="Center" Margin="12,0,0,0" FontSize="11"/>
                  </StackPanel>
                </StackPanel>
              </Border>
            </StackPanel>
          </ScrollViewer>
        </Border>
      </TabItem>

      <!-- TAB 3: Network & Anti-Detection Center -->
      <TabItem Header="  🌐 Network &amp; Anti-Detection  ">
        <Border Background="#202022" CornerRadius="8" Padding="20" Margin="0,12,0,0" BorderBrush="#333333" BorderThickness="1">
          <ScrollViewer VerticalScrollBarVisibility="Auto">
            <StackPanel>
              <TextBlock Text="Server-Side &amp; Network Fingerprint Countermeasures" FontSize="16" FontWeight="Bold" Foreground="#FFFFFF" Margin="0,0,0,6"/>
              <TextBlock Text="Ankama servers inspect network clustering, identical socket origins, and QEMU SLIRP signatures. These defenses neutralize detection:"
                         Foreground="#9AA0A6" FontSize="12" Margin="0,0,0,12"/>

              <Border Background="#1C2D1F" CornerRadius="6" Padding="14,10" Margin="0,0,0,14" BorderBrush="#238636" BorderThickness="1">
                <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
                  <TextBlock Text="🔒 ALWAYS-ON MOBILE DISGUISE:" Foreground="#98C379" FontWeight="Bold" FontSize="12" VerticalAlignment="Center"/>
                  <TextBlock Text=" PERMANENTLY LOCKED &amp; ACTIVE (Cannot be disabled or altered by user)" Foreground="#DCDCAA" FontSize="12" Margin="8,0,0,0" VerticalAlignment="Center"/>
                </StackPanel>
              </Border>

              <!-- Defenses Checklist -->
              <Grid Margin="0,0,0,16">
                <Grid.RowDefinitions>
                  <RowDefinition Height="Auto"/>
                  <RowDefinition Height="Auto"/>
                  <RowDefinition Height="Auto"/>
                  <RowDefinition Height="Auto"/>
                </Grid.RowDefinitions>

                <Border Grid.Row="0" Background="#181818" CornerRadius="6" Padding="12" Margin="0,0,0,8" BorderBrush="#333333" BorderThickness="1">
                  <Grid>
                    <Grid.ColumnDefinitions>
                      <ColumnDefinition Width="Auto"/>
                      <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <TextBlock Grid.Column="0" Text="🛡" FontSize="18" Margin="0,0,12,0" VerticalAlignment="Center"/>
                    <StackPanel Grid.Column="1">
                      <TextBlock Text="Samsung Hardware MAC OUI (bc:72:b7:xx:xx:xx)" Foreground="#98C379" FontWeight="Bold" FontSize="12"/>
                      <TextBlock Text="Replaces generic QEMU MAC (52:54:00:...) with physical Samsung Electronics mobile network adapter OUIs." Foreground="#AAA" FontSize="11"/>
                    </StackPanel>
                  </Grid>
                </Border>

                <Border Grid.Row="1" Background="#181818" CornerRadius="6" Padding="12" Margin="0,0,0,8" BorderBrush="#333333" BorderThickness="1">
                  <Grid>
                    <Grid.ColumnDefinitions>
                      <ColumnDefinition Width="Auto"/>
                      <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <TextBlock Grid.Column="0" Text="🌐" FontSize="18" Margin="0,0,12,0" VerticalAlignment="Center"/>
                    <StackPanel Grid.Column="1">
                      <TextBlock Text="QEMU SLIRP 10.0.2.15 Invariant Elimination" Foreground="#98C379" FontWeight="Bold" FontSize="12"/>
                      <TextBlock Text="DNS points to Cloudflare (1.1.1.1) instead of QEMU 10.0.2.3. WebRTC ICE gathers 192.168.1.10X mobile LAN candidates." Foreground="#AAA" FontSize="11"/>
                    </StackPanel>
                  </Grid>
                </Border>

                <Border Grid.Row="2" Background="#181818" CornerRadius="6" Padding="12" Margin="0,0,0,8" BorderBrush="#333333" BorderThickness="1">
                  <Grid>
                    <Grid.ColumnDefinitions>
                      <ColumnDefinition Width="Auto"/>
                      <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <TextBlock Grid.Column="0" Text="📶" FontSize="18" Margin="0,0,12,0" VerticalAlignment="Center"/>
                    <StackPanel Grid.Column="1">
                      <TextBlock Text="Orange France (20801) LTE Carrier Telephony" Foreground="#98C379" FontWeight="Bold" FontSize="12"/>
                      <TextBlock Text="Telephony subsystem reports standard nominal carrier state: Orange France SIM, READY, LTE." Foreground="#AAA" FontSize="11"/>
                    </StackPanel>
                  </Grid>
                </Border>

                <Border Grid.Row="3" Background="#181818" CornerRadius="6" Padding="12" BorderBrush="#333333" BorderThickness="1">
                  <Grid>
                    <Grid.ColumnDefinitions>
                      <ColumnDefinition Width="Auto"/>
                      <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <TextBlock Grid.Column="0" Text="🔌" FontSize="18" Margin="0,0,12,0" VerticalAlignment="Center"/>
                    <StackPanel Grid.Column="1">
                      <TextBlock Text="Per-Instance Dedicated Proxy / SOCKS5 Routing" Foreground="#569CD6" FontWeight="Bold" FontSize="12"/>
                      <TextBlock Text="Assign distinct residential or datacenter proxies per instance so accounts connect from different external IP addresses." Foreground="#AAA" FontSize="11"/>
                    </StackPanel>
                  </Grid>
                </Border>
              </Grid>
            </StackPanel>
          </ScrollViewer>
        </Border>
      </TabItem>

      <!-- TAB 4: Fast Instance Provisioning -->
      <TabItem Header="  ➕ Provision Instance  ">
        <Border Background="#202022" CornerRadius="8" Padding="20" Margin="0,12,0,0" BorderBrush="#333333" BorderThickness="1">
          <StackPanel>
            <TextBlock Text="Create New Virtualized Instance" FontSize="16" FontWeight="Bold" Foreground="#FFFFFF" Margin="0,0,0,6"/>
            <TextBlock Text="Generates a zero-copy QCOW2 differential overlay backed onto userdata-golden.img with randomized Samsung identity."
                       Foreground="#9AA0A6" FontSize="12" Margin="0,0,0,16"/>

            <Grid Margin="0,0,0,12">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="160"/>
                <ColumnDefinition Width="240"/>
              </Grid.ColumnDefinitions>
              <TextBlock Grid.Column="0" Text="Instance Name:" Foreground="#CCCCCC" VerticalAlignment="Center"/>
              <TextBox x:Name="TxtInstanceName" Grid.Column="1" Height="28" VerticalContentAlignment="Center"
                       Background="#181818" Foreground="#FFFFFF" BorderBrush="#3F3F46" Padding="6,2"/>
            </Grid>

            <Grid Margin="0,0,0,12">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="160"/>
                <ColumnDefinition Width="240"/>
              </Grid.ColumnDefinitions>
              <TextBlock Grid.Column="0" Text="Memory (RAM):" Foreground="#CCCCCC" VerticalAlignment="Center"/>
              <ComboBox x:Name="RamBox" Grid.Column="1" Height="28" VerticalContentAlignment="Center" Background="#181818"/>
            </Grid>

            <Grid Margin="0,0,0,12">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="160"/>
                <ColumnDefinition Width="240"/>
              </Grid.ColumnDefinitions>
              <TextBlock Grid.Column="0" Text="vCPU Cores:" Foreground="#CCCCCC" VerticalAlignment="Center"/>
              <ComboBox x:Name="CoresBox" Grid.Column="1" Height="28" VerticalContentAlignment="Center" Background="#181818"/>
            </Grid>

            <Grid Margin="0,0,0,16">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="160"/>
                <ColumnDefinition Width="240"/>
              </Grid.ColumnDefinitions>
              <TextBlock Grid.Column="0" Text="Proxy (Optional):" Foreground="#CCCCCC" VerticalAlignment="Center"/>
              <TextBox x:Name="TxtProxy" Grid.Column="1" Height="28" VerticalContentAlignment="Center"
                       Background="#181818" Foreground="#FFFFFF" BorderBrush="#3F3F46" Padding="6,2" Text=""/>
            </Grid>

            <Button x:Name="BtnCreate" Content="Create &amp; Provision Instance" Width="220" Height="34"
                    Background="#0E639C" Foreground="#FFFFFF" FontWeight="Bold" BorderThickness="0" Cursor="Hand" HorizontalAlignment="Left"/>
          </StackPanel>
        </Border>
      </TabItem>
    </TabControl>

    <!-- Bottom Status Bar -->
    <Border Grid.Row="2" Background="#202022" CornerRadius="6" Padding="12,8" Margin="0,12,0,0" BorderBrush="#333333" BorderThickness="1">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <TextBlock x:Name="Status" Grid.Column="0" Text="Ready." Foreground="#9AA0A6" FontSize="12" VerticalAlignment="Center"/>
        <TextBlock x:Name="GoldenStatus" Grid.Column="1" Text="Golden Master: Validated" Foreground="#98C379" FontSize="11" VerticalAlignment="Center"/>
      </Grid>
    </Border>
  </Grid>
</Window>
"@

$reader = New-Object System.Xml.XmlNodeReader $xaml
$win = [Windows.Markup.XamlReader]::Load($reader)

# Element references
$TxtInstanceName        = $win.FindName('TxtInstanceName')
$RamBox                 = $win.FindName('RamBox')
$CoresBox               = $win.FindName('CoresBox')
$TxtProxy               = $win.FindName('TxtProxy')
$Grid                   = $win.FindName('Grid')
$Status                 = $win.FindName('Status')
$GoldenStatus           = $win.FindName('GoldenStatus')
$HeaderGpuText          = $win.FindName('HeaderGpuText')
$HostStatsHeader        = $win.FindName('HostStatsHeader')
$TxtDiagCpu             = $win.FindName('TxtDiagCpu')
$TxtDiagCores           = $win.FindName('TxtDiagCores')
$TxtDiagGpu             = $win.FindName('TxtDiagGpu')
$TxtDiagGpuDriver       = $win.FindName('TxtDiagGpuDriver')
$TxtDiagProfileName     = $win.FindName('TxtDiagProfileName')
$TxtDiagReason          = $win.FindName('TxtDiagReason')

# Slot elements
$Slot1Border = $win.FindName('Slot1Border'); $Slot1Name = $win.FindName('Slot1Name'); $Slot1Status = $win.FindName('Slot1Status')
$Slot2Border = $win.FindName('Slot2Border'); $Slot2Name = $win.FindName('Slot2Name'); $Slot2Status = $win.FindName('Slot2Status')
$Slot3Border = $win.FindName('Slot3Border'); $Slot3Name = $win.FindName('Slot3Name'); $Slot3Status = $win.FindName('Slot3Status')
$Slot4Border = $win.FindName('Slot4Border'); $Slot4Name = $win.FindName('Slot4Name'); $Slot4Status = $win.FindName('Slot4Status')

# Initialize diagnostics view
$TxtDiagCpu.Text = "CPU: $($diag.HostCpu)"
$TxtDiagCores.Text = "Cores: $($diag.PhysicalCores) Physical / $($diag.LogicalThreads) Logical Threads"
$TxtDiagGpu.Text = "GPU: $($diag.GpuName)"
$TxtDiagGpuDriver.Text = "Driver Version: $($diag.GpuDriver)"
$TxtDiagProfileName.Text = "Profile: $($diag.ProfileName)"
$TxtDiagReason.Text = $diag.Recommendation
$HeaderGpuText.Text = "$($diag.GpuName) (-gpu host)"
$HostStatsHeader.Text = "Host: $($diag.HostRamGB) GB RAM ($($diag.FreeRamGB) GB Free) | $($diag.LogicalThreads) Threads"

$ramOptions = @('512 MB (Ultra-Light)', '768 MB (Recommended for 4x)', '1024 MB (Standard)', '1536 MB (High)', '2048 MB')
$coreOptions = @('1 Core (Optimized 30 FPS - Recommended)', '2 Cores (Dual-Core)')

$RamBox.ItemsSource = $ramOptions
$RamBox.SelectedIndex = 1  # 768 MB

$CoresBox.ItemsSource = $coreOptions
$CoresBox.SelectedIndex = 0 # 1 Core

function Set-Status($msg, $color = '#9AA0A6') {
  $Status.Text = $msg
  $Status.Foreground = $color
}

function Refresh-Grid {
  $live = Get-LiveSerials
  $insts = @(Get-Instances)
  $rows = $insts | ForEach-Object {
    $isRunning = $live -contains $_.Serial
    $stateStr = if ($isRunning) { '🟢 RUNNING' } else { '⚪ STOPPED' }
    [pscustomobject]@{
      StateDisplay = $stateStr
      DisplayName  = $_.DisplayName
      Name         = $_.Name
      Index        = $_.Index
      RamMb        = "$($_.RamMb) MB"
      Cores        = "$($_.Cores)"
      Port         = $_.Port
      Serial       = $_.Serial
      Overlay      = $_.Overlay
      Mac          = $_.Mac
      Proxy        = $_.Proxy
      IsRunning    = $isRunning
      RawRamMb     = $_.RamMb
      RawCores     = $_.Cores
    }
  }
  $Grid.ItemsSource = $rows
  $TxtInstanceName.Text = Get-NextInstanceName

  # Update Slot Map Preview
  $updateSlot = {
    param($slotIdx, $border, $nameEl, $statEl)
    $it = $rows | Where-Object { $_.Index -eq $slotIdx } | Select-Object -First 1
    if ($it) {
      $nameEl.Text = "Slot $slotIdx : $($it.DisplayName)"
      if ($it.IsRunning) {
        $statEl.Text = "🟢 RUNNING (Port $($it.Port))"
        $statEl.Foreground = '#98C379'
        $border.Background = '#1E3A25'
      } else {
        $statEl.Text = "⚪ STOPPED (Port $($it.Port))"
        $statEl.Foreground = '#888888'
        $border.Background = '#28282B'
      }
    } else {
      $nameEl.Text = "Slot $slotIdx : Unassigned"
      $statEl.Text = "⚪ Not Provisioned"
      $statEl.Foreground = '#555555'
      $border.Background = '#202022'
    }
  }

  & $updateSlot 1 $Slot1Border $Slot1Name $Slot1Status
  & $updateSlot 2 $Slot2Border $Slot2Name $Slot2Status
  & $updateSlot 3 $Slot3Border $Slot3Name $Slot3Status
  & $updateSlot 4 $Slot4Border $Slot4Name $Slot4Status

  if (Test-Path $Golden) {
    $GoldenStatus.Text = "Golden Master: $([math]::Round((Get-Item $Golden).Length/1MB,0)) MB (QCOW2 Differential Active)"
    $GoldenStatus.Foreground = '#98C379'
  } else {
    $GoldenStatus.Text = "Golden Master: MISSING ($Golden)"
    $GoldenStatus.Foreground = '#E06C75'
  }
}

function Prompt-RenameInstance {
  $sel = $Grid.SelectedItem
  if (-not $sel) { Set-Status 'Select an instance to rename.' '#E06C75'; return }
  $curName = $sel.Name
  $curDisplay = $sel.DisplayName
  $newName = [Microsoft.VisualBasic.Interaction]::InputBox("Enter new display name for instance '$curName':`n(Works even while the instance is running)", "Rename Instance", $curDisplay)
  if ($newName -and $newName.Trim() -ne '' -and $newName -ne $curDisplay) {
    $newName = $newName.Trim()
    $dir = Join-Path $AvdHome "$curName.avd"
    if (Test-Path $dir) {
      @{ Alias = $newName } | ConvertTo-Json | Set-Content -Path (Join-Path $dir 'alias.json')
      Set-Status "Renamed $curName to '$newName'." '#98C379'
      Refresh-Grid
    }
  }
}

$Grid.Add_MouseDoubleClick({ Prompt-RenameInstance })

function Prompt-SetProxy {
  $sel = $Grid.SelectedItem
  if (-not $sel) { Set-Status 'Select an instance to configure proxy.' '#E06C75'; return }
  $curProxy = if ($sel.Proxy -match 'Direct') { '' } else { $sel.Proxy }
  $newProxy = [Microsoft.VisualBasic.Interaction]::InputBox("Enter Proxy address for $($sel.DisplayName) (e.g. 127.0.0.1:1080 or http://user:pass@ip:port):`n(Leave blank for direct LAN connection)", "Proxy Configuration", $curProxy)
  if ($newProxy -ne $null) {
    $dir = Join-Path $AvdHome "$($sel.Name).avd"
    $proxyFile = Join-Path $dir 'proxy.txt'
    if ($newProxy.Trim() -ne '') {
      Set-Content -Path $proxyFile -Value $newProxy.Trim()
      Set-Status "Configured proxy for $($sel.DisplayName): $($newProxy.Trim())" '#98C379'
    } else {
      Remove-Item $proxyFile -Force -EA SilentlyContinue
      Set-Status "Removed proxy for $($sel.DisplayName) (Direct LAN restored)." '#98C379'
    }
    Refresh-Grid
  }
}

function Provision-Instance([string]$Name, [int]$RamMb, [int]$CpuCores, [string]$ProxyVal) {
  $dir = Join-Path $AvdHome "$Name.avd"
  $ini = Join-Path $AvdHome "$Name.ini"
  $templateDir = Join-Path $AvdHome 'dofus-template.avd'

  Set-Status "Creating AVD $Name..." '#569CD6'
  if (-not (Test-Path $ini)) {
    if (Test-Path $templateDir) {
      Copy-Item -Path $templateDir -Destination $dir -Recurse -Force
      @(
        'avd.ini.encoding=UTF-8'
        "path=$dir"
        "path.rel=avd/$Name.avd"
        'target=android-29'
      ) | Set-Content -Path $ini -Encoding UTF8
    } else {
      if (-not (Test-Path $Avd)) { throw "avdmanager not found at $Avd" }
      cmd /c "echo no | `"$Avd`" create avd -n $Name -k `"system-images;android-29;default;x86_64`" -f 2>&1" | Out-Null
    }
  }

  $cfg = Join-Path $dir 'config.ini'
  if (Test-Path $cfg) {
    $extra = @(
      "avd.name=$Name", "avd.id=$Name",
      "hw.gpu.enabled=yes", "hw.gpu.mode=host",
      "hw.ramSize=$RamMb", "hw.cpu.ncore=$CpuCores",
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
      if ($cur -match "^\s*$([regex]::Escape($k))\s*=") {
        $cur = $cur -replace "^\s*$([regex]::Escape($k))\s*=.*", $e
      } else {
        $cur += $e
      }
    }
    Set-Content -Path $cfg -Value $cur
  }

  # QCOW2 differential overlay via hard-link
  if (Test-Path $Golden) {
    $emuQcow2 = Join-Path $dir 'userdata-qemu.img.qcow2'
    $rawStub  = Join-Path $dir 'userdata-qemu.img'
    $targetQcow2 = Join-Path $dir 'userdata.qcow2'
    Remove-Item $emuQcow2 -Force -EA SilentlyContinue
    Remove-Item $rawStub  -Force -EA SilentlyContinue
    Remove-Item $targetQcow2 -Force -EA SilentlyContinue

    try {
      New-Item -ItemType HardLink -Path $rawStub -Target $Golden -Force | Out-Null
    } catch {
      Copy-Item $Golden $rawStub -Force
    }

    Push-Location $dir
    & $QemuImg create -f qcow2 -b "userdata-qemu.img" -F qcow2 "userdata-qemu.img.qcow2" | Out-Null
    Pop-Location

    try {
      New-Item -ItemType HardLink -Path $targetQcow2 -Target $emuQcow2 -Force | Out-Null
    } catch {
      Copy-Item $emuQcow2 $targetQcow2 -Force
    }
  }

  $serial = New-RandomSerial
  $androidId = New-AndroidId
  @{ Serial = $serial; AndroidId = $androidId } | ConvertTo-Json | Set-Content -Path (Join-Path $dir 'identity.json')

  if ($ProxyVal -and $ProxyVal.Trim() -ne '') {
    Set-Content -Path (Join-Path $dir 'proxy.txt') -Value $ProxyVal.Trim()
  }

  $sysProp = @"
ro.product.brand=samsung
ro.product.manufacturer=samsung
ro.product.model=SM-A515F
ro.product.name=a51nsxx
ro.product.device=a51
ro.build.flavor=a51nsxx-user
ro.build.type=user
ro.build.tags=release-keys
qemu.hw.mainkeys=1
gsm.sim.state=READY
gsm.sim.operator.numeric=20801
gsm.sim.operator.alpha=Orange
gsm.network.type=LTE
net.dns1=1.1.1.1
net.dns2=8.8.8.8
"@
  Set-Content -Path (Join-Path $dir 'system.prop') -Value $sysProp
  Set-Status "Provisioned $Name with QCOW2 overlay & Samsung profile (serial=$serial, cores=$CpuCores)." '#98C379'
}

# ------------------------------------------------------------------- Button Handlers
$win.FindName('BtnRefreshDashboard').Add_Click({ Refresh-Grid })
$win.FindName('BtnRename').Add_Click({ Prompt-RenameInstance })
$win.FindName('BtnSetProxy').Add_Click({ Prompt-SetProxy })

$win.FindName('BtnCreate').Add_Click({
  $name = $TxtInstanceName.Text.Trim()
  if (-not $name) { Set-Status 'Please enter an instance name.' '#E06C75'; return }
  $r = 768
  if ($RamBox.SelectedItem -match '(\d+)') { $r = [int]$matches[1] }
  $c = if ($CoresBox.SelectedIndex -eq 0) { 1 } else { 2 }
  $prx = $TxtProxy.Text.Trim()

  try {
    Provision-Instance -Name $name -RamMb $r -CpuCores $c -ProxyVal $prx
    Refresh-Grid
  } catch {
    Set-Status "Creation failed: $($_.Exception.Message)" '#E06C75'
  }
})

$win.FindName('BtnLaunchSingle').Add_Click({
  $sel = $Grid.SelectedItem
  if (-not $sel) { Set-Status 'Select an instance from the table to launch.' '#E06C75'; return }
  if ($sel.IsRunning) { Set-Status "Instance $($sel.DisplayName) is already running." '#E5C07B'; return }

  Set-Status "Launching $($sel.DisplayName) ($($sel.Name))..." '#569CD6'
  $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'instances.ps1'),
               '-AvdName', $sel.Name, '-Port', "$($sel.Port)", '-RamMb', "$($sel.RawRamMb)", '-Cores', "$($sel.RawCores)", '-NoWait')
  if ($sel.Proxy -notmatch 'Direct') { $argList += @('-Proxy', $sel.Proxy) }
  Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -WindowStyle Minimized
  Start-Sleep -Seconds 2
  Refresh-Grid
  Set-Status "Instance $($sel.DisplayName) launched on port $($sel.Port)." '#98C379'
})

$win.FindName('BtnStopSingle').Add_Click({
  $sel = $Grid.SelectedItem
  if (-not $sel) { Set-Status 'Select an instance from the table to stop.' '#E06C75'; return }
  if (-not $sel.IsRunning) { Set-Status "Instance $($sel.DisplayName) is not running." '#E5C07B'; return }

  & $Adb -s $sel.Serial emu kill 2>$null | Out-Null
  Set-Status "Shutdown signal sent to $($sel.DisplayName)." '#98C379'
  Start-Sleep -Seconds 2
  Refresh-Grid
})

$win.FindName('BtnDelete').Add_Click({
  $sel = $Grid.SelectedItem
  if (-not $sel) { Set-Status 'Select an instance row to delete.' '#E06C75'; return }
  if ($sel.IsRunning) { Set-Status 'Stop that instance before deleting it.' '#E5C07B'; return }
  $ans = [System.Windows.MessageBox]::Show(
    "Delete instance $($sel.DisplayName) ($($sel.Name))? This will permanently remove its QCOW2 overlay and data.",
    "Confirm Delete",
    [System.Windows.MessageBoxButton]::YesNo,
    [System.Windows.MessageBoxImage]::Warning
  )
  if ($ans -eq [System.Windows.MessageBoxResult]::Yes) {
    Remove-Item (Join-Path $AvdHome "$($sel.Name).avd") -Recurse -Force -EA SilentlyContinue
    Remove-Item (Join-Path $AvdHome "$($sel.Name).ini") -Force -EA SilentlyContinue
    Set-Status "Deleted $($sel.DisplayName)." '#98C379'
    Refresh-Grid
  }
})

$win.FindName('BtnLaunchFarm').Add_Click({
  $inst = @(Get-Instances)
  if ($inst.Count -eq 0) { Set-Status 'No instances provisioned. Click Provision Instance first.' '#E06C75'; return }
  
  Set-Status "Launching farm ($([math]::Min(4, $inst.Count)) instance(s))..." '#569CD6'
  
  $n = [math]::Min(4, $inst.Count)
  $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $StartFarm, '-Count', "$n", '-RamMb', "768", '-Cores', "1", '-AutoBoot', '-EdgeToEdge', '-Force')
  $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -PassThru -WindowStyle Minimized
  $p | Wait-Process -Timeout 120 -EA SilentlyContinue
  
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

$win.FindName('BtnAuditSingle').Add_Click({
  $sel = $Grid.SelectedItem
  if (-not $sel -or -not $sel.IsRunning) { Set-Status 'Select a running instance to audit.' '#E06C75'; return }
  $auditScript = Join-Path $PSScriptRoot 'run-spoof-audit.ps1'
  Start-Process powershell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', "`"$auditScript`"", '-Serial', $sel.Serial)
  Set-Status "Launched spoof audit for $($sel.DisplayName)." '#98C379'
})

$win.FindName('BtnAuditAll').Add_Click({
  $live = Get-LiveSerials
  if (-not $live) { Set-Status 'No active instances to audit. Launch farm first.' '#E06C75'; return }
  $targetSerial = $live[0]
  $auditScript = Join-Path $PSScriptRoot 'run-spoof-audit.ps1'
  Start-Process powershell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', "`"$auditScript`"", '-Serial', $targetSerial)
  Set-Status "Launched comprehensive spoof audit on $targetSerial." '#98C379'
})

# Quick Navigation & Sidebar Toggle handlers
function Get-TargetSerials {
  $sel = $Grid.SelectedItem
  if ($sel -and $sel.IsRunning) {
    return @($sel.Serial)
  }
  return Get-LiveSerials
}

$win.FindName('BtnNavBack').Add_Click({
  $targets = Get-TargetSerials
  if (-not $targets -or $targets.Count -eq 0) { Set-Status 'No running instance to send Return/Back key.' '#E5C07B'; return }
  foreach ($s in $targets) {
    & $Adb -s $s shell input keyevent 4 2>$null | Out-Null
  }
  Set-Status "Sent Return (Back) keyevent to $($targets -join ', ')." '#98C379'
})

$win.FindName('BtnNavHome').Add_Click({
  $targets = Get-TargetSerials
  if (-not $targets -or $targets.Count -eq 0) { Set-Status 'No running instance to send Home key.' '#E5C07B'; return }
  foreach ($s in $targets) {
    & $Adb -s $s shell input keyevent 3 2>$null | Out-Null
  }
  Set-Status "Sent Home keyevent to $($targets -join ', ')." '#98C379'
})

$win.FindName('BtnNavRecents').Add_Click({
  $targets = Get-TargetSerials
  if (-not $targets -or $targets.Count -eq 0) { Set-Status 'No running instance to send Tabs/Recents key.' '#E5C07B'; return }
  foreach ($s in $targets) {
    & $Adb -s $s shell input keyevent 187 2>$null | Out-Null
  }
  Set-Status "Sent Tabs (App Switch) keyevent to $($targets -join ', ')." '#98C379'
})

$win.FindName('BtnNavSettings').Add_Click({
  $targets = Get-TargetSerials
  if (-not $targets -or $targets.Count -eq 0) { Set-Status 'No running instance to open Settings.' '#E5C07B'; return }
  foreach ($s in $targets) {
    & $Adb -s $s shell am start -a android.settings.SETTINGS 2>$null | Out-Null
  }
  Set-Status "Opened Settings on $($targets -join ', ')." '#98C379'
})

$script:sidebarsHidden = $true
$win.FindName('BtnToggleSidebars').Add_Click({
  . (Join-Path $PSScriptRoot 'layout.ps1')
  $live = Get-LiveSerials
  if (-not $live) { Set-Status 'No running instances found.' '#E5C07B'; return }
  $pids = @()
  foreach ($s in $live) {
    $port_ = [int]($s -replace 'emulator-','')
    $p = Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" -EA SilentlyContinue |
         Where-Object { $_.CommandLine -match "-port\s+$port_\b" } | Select-Object -First 1
    if ($p) { $pids += $p.ProcessId }
  }
  if ($pids.Count -eq 0) { Set-Status 'Could not map instances to emulator processes.' '#E06C75'; return }

  if ($script:sidebarsHidden) {
    Show-EmulatorToolbar -LauncherPids $pids
    $script:sidebarsHidden = $false
    Set-Status "Restored side toolbars on $($pids.Count) instance(s)." '#569CD6'
  } else {
    Hide-EmulatorToolbar -LauncherPids $pids
    $script:sidebarsHidden = $true
    Set-Status "Hidden side toolbars on $($pids.Count) instance(s) (Full Screen Edge-to-Edge)." '#98C379'
  }
})

# Toggle Window Borders handler (Bordered Titlebar vs Edge-to-Edge Canvas)
$script:bordersVisible = $false
$win.FindName('BtnToggleBorders').Add_Click({
  . (Join-Path $PSScriptRoot 'layout.ps1')
  $live = Get-LiveSerials
  if (-not $live) { Set-Status 'No running instances found.' '#E5C07B'; return }
  $pids = @()
  foreach ($s in $live) {
    $port_ = [int]($s -replace 'emulator-','')
    $p = Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" -EA SilentlyContinue |
         Where-Object { $_.CommandLine -match "-port\s+$port_\b" } | Select-Object -First 1
    if ($p) { $pids += $p.ProcessId }
  }
  if ($pids.Count -eq 0) { Set-Status 'Could not map instances to emulator processes.' '#E06C75'; return }

  if ($script:bordersVisible) {
    foreach ($pid_ in $pids) {
      $w = Get-RenderWindow -LauncherPid $pid_ -TimeoutSec 3
      if ($w) { Set-WindowBorderless -hWnd $w.Handle }
    }
    $script:bordersVisible = $false
    Set-Status "Switched to seamless borderless mode (0 black bars)." '#98C379'
  } else {
    foreach ($pid_ in $pids) {
      $w = Get-RenderWindow -LauncherPid $pid_ -TimeoutSec 3
      if ($w) { Set-WindowWithBorders -hWnd $w.Handle }
    }
    $script:bordersVisible = $true
    Set-Status "Restored standard window borders with native title bars." '#569CD6'
  }
})

# Reduce All & Retile All handlers
$win.FindName('BtnReduceAll').Add_Click({
  . (Join-Path $PSScriptRoot 'layout.ps1')
  $live = Get-LiveSerials
  if (-not $live) { Set-Status 'No running instances to minimize.' '#E5C07B'; return }
  $count_ = 0
  foreach ($s in $live) {
    $port_ = [int]($s -replace 'emulator-','')
    $p = Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" -EA SilentlyContinue |
         Where-Object { $_.CommandLine -match "-port\s+$port_\b" } | Select-Object -First 1
    if ($p) {
      $w = Get-RenderWindow -LauncherPid $p.ProcessId -TimeoutSec 3
      if ($w) { Reduce-EmulatorWindow -hWnd $w.Handle; $count_++ }
    }
  }
  Set-Status "Reduced (minimized) $count_ running emulator window(s)." '#98C379'
})

$win.FindName('BtnRetileAll').Add_Click({
  $win.FindName('BtnRelayout').RaiseEvent((New-Object System.Windows.RoutedEventArgs([System.Windows.Controls.Button]::ClickEvent)))
})

# Helper for per-slot actions (3 Legend Tweaks)
function Invoke-SlotLegendAction([int]$SlotIdx, [string]$Action) {
  . (Join-Path $PSScriptRoot 'layout.ps1')
  $port = 5554 + 2 * ($SlotIdx - 1)
  $serial = "emulator-$port"
  $p = Get-CimInstance Win32_Process -Filter "Name='qemu-system-x86_64.exe'" -EA SilentlyContinue |
       Where-Object { $_.CommandLine -match "-port\s+$port\b" } | Select-Object -First 1
  if (-not $p) {
    Set-Status "Slot $SlotIdx ($serial) is not currently running." '#E5C07B'
    return
  }
  $win = Get-RenderWindow -LauncherPid $p.ProcessId -TimeoutSec 3
  if (-not $win) {
    Set-Status "Could not find render window for Slot $SlotIdx." '#E06C75'
    return
  }
  switch ($Action) {
    'Reduce' {
      Reduce-EmulatorWindow -hWnd $win.Handle
      Set-Status "Slot $SlotIdx ($serial) reduced (minimized)." '#98C379'
    }
    'Maximize' {
      $rects = Get-FarmLayout -Count 4 -EdgeToEdge
      $r = $rects[$SlotIdx - 1]
      Maximize-EmulatorWindow -hWnd $win.Handle -X $r.X -Y $r.Y -W $r.W -H $r.H
      Set-Status "Slot $SlotIdx ($serial) toggled Maximize / Tile." '#98C379'
    }
    'Exit' {
      Exit-EmulatorWindow -hWnd $win.Handle -Serial $serial
      Set-Status "Closed Slot $SlotIdx ($serial)." '#98C379'
      Start-Sleep -Seconds 2
      Refresh-Grid
    }
  }
}

# Wire up Slot 1-4 Legend Buttons
1..4 | ForEach-Object {
  $idx = $_
  $btnRed = $win.FindName("BtnSlot${idx}Reduce")
  $btnMax = $win.FindName("BtnSlot${idx}Max")
  $btnExt = $win.FindName("BtnSlot${idx}Exit")
  if ($btnRed) { $btnRed.Add_Click([scriptblock]::Create("Invoke-SlotLegendAction -SlotIdx $idx -Action 'Reduce'")) }
  if ($btnMax) { $btnMax.Add_Click([scriptblock]::Create("Invoke-SlotLegendAction -SlotIdx $idx -Action 'Maximize'")) }
  if ($btnExt) { $btnExt.Add_Click([scriptblock]::Create("Invoke-SlotLegendAction -SlotIdx $idx -Action 'Exit'")) }
}

# Auto-Tune Profile button handler
$autoTuneAction = {
  $ans = [System.Windows.MessageBox]::Show(
    "Apply Auto-Tune profile (768 MB RAM, 1 Core, 30 FPS vsync, Host GLES passthrough) to all instances?`n`nReason: $($diag.Recommendation)",
    "Auto-Tune Confirmation",
    [System.Windows.MessageBoxButton]::YesNo,
    [System.Windows.MessageBoxImage]::Information
  )
  if ($ans -eq [System.Windows.MessageBoxResult]::Yes) {
    Get-ChildItem $AvdHome -Filter 'dofus-*.avd' -Directory | ForEach-Object {
      $cfg = Join-Path $_.FullName 'config.ini'
      if (Test-Path $cfg) {
        $c = Get-Content $cfg
        $c = $c -replace '^\s*hw\.ramSize\s*=.*', 'hw.ramSize = 768'
        $c = $c -replace '^\s*hw\.cpu\.ncore\s*=.*', 'hw.cpu.ncore = 1'
        $c = $c -replace '^\s*qemu\.vsync\s*=.*', 'qemu.vsync = 30'
        Set-Content -Path $cfg -Value $c
      }
    }
    Set-Status "Auto-tune profile applied across all instances (768 MB RAM, 1 Core per instance)." '#98C379'
    Refresh-Grid
  }
}
$win.FindName('BtnHeaderAutoTune').Add_Click($autoTuneAction)
$win.FindName('BtnApplyAutoTuneProfile').Add_Click($autoTuneAction)

$win.FindName('BtnHeaderUninstall').Add_Click({
  $uninstExe = Join-Path $RepoRoot 'uninstall.exe'
  $uninstScript = Join-Path $PSScriptRoot 'uninstall.ps1'
  if (Test-Path $uninstExe) {
    Start-Process $uninstExe
    $win.Close()
  } elseif (Test-Path $uninstScript) {
    Start-Process powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-STA','-File',"`"$uninstScript`"")
    $win.Close()
  }
})

# Context Menu handlers
$win.FindName('CtxLaunch').Add_Click({ $win.FindName('BtnLaunchSingle').RaiseEvent((New-Object System.Windows.RoutedEventArgs([System.Windows.Controls.Button]::ClickEvent))) })
$win.FindName('CtxStop').Add_Click({ $win.FindName('BtnStopSingle').RaiseEvent((New-Object System.Windows.RoutedEventArgs([System.Windows.Controls.Button]::ClickEvent))) })
$win.FindName('CtxRename').Add_Click({ Prompt-RenameInstance })
$win.FindName('CtxProxy').Add_Click({ Prompt-SetProxy })
$win.FindName('CtxDelete').Add_Click({ $win.FindName('BtnDelete').RaiseEvent((New-Object System.Windows.RoutedEventArgs([System.Windows.Controls.Button]::ClickEvent))) })
$win.FindName('CtxAudit').Add_Click({ $win.FindName('BtnAuditSingle').RaiseEvent((New-Object System.Windows.RoutedEventArgs([System.Windows.Controls.Button]::ClickEvent))) })

try {
  Refresh-Grid
  Set-Status 'Ready. Use Farm Dashboard to launch, or Hardware tab to review laptop compatibility.'
  $win.ShowDialog() | Out-Null
} catch {
  $errLog = Join-Path $RepoRoot 'gui-error.log'
  $errMsg = "GUI Error: $($_.Exception.Message)`r`n`r`nStack Trace:`r`n$($_.ScriptStackTrace)"
  [System.IO.File]::WriteAllText($errLog, $errMsg)
  [System.Windows.MessageBox]::Show($errMsg, "Dofus Farm Manager Error", [System.Windows.MessageBoxButton]::OK, [System.Windows.MessageBoxImage]::Error)
}
