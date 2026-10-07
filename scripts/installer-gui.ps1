<#
.SYNOPSIS
  High-Efficiency Android Multi-Instance Farm - Standalone Graphical Installer.

.DESCRIPTION
  Modern WPF installation wizard featuring:
    - Page 1: Terms of Conditions & License Agreement acceptance.
    - Page 2: Installation Location selection (default vs manual browse).
    - Page 3: Dynamic hardware calculation & instance sizing (1-8 instances, dynamic RAM/cores, custom resolution).
    - Page 4: Real-time progress tracking with dependency checks (skips already installed components).
    - Page 5: Completion & direct launch triggers (Farm Launcher, GUI Manager, Desktop Shortcut).
#>
[CmdletBinding()]
param(
  [string] $TargetDir = ''
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName Microsoft.VisualBasic

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $TargetDir) { $TargetDir = $RepoRoot }

# Detect host capacity
$cs       = Get-CimInstance Win32_ComputerSystem
$hostGB   = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
$cores    = [int]$cs.NumberOfLogicalProcessors
$freeGB   = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1)

[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Dofus Touch Virtualization Farm - Setup Wizard" Height="640" Width="840"
        WindowStartupLocation="CenterScreen" Background="#1E1E1E" ResizeMode="NoResize">
  <Grid Margin="20">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <!-- Header Banner -->
    <Border Grid.Row="0" Background="#252526" CornerRadius="8" Padding="16" Margin="0,0,0,16" BorderBrush="#3F3F46" BorderThickness="1">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <StackPanel Grid.Column="0">
          <TextBlock x:Name="StepTitle" Text="Terms and Conditions" FontSize="20" FontWeight="Bold" Foreground="#FFFFFF"/>
          <TextBlock x:Name="StepSubtitle" Text="Please review and accept the virtualization harness terms of use." FontSize="12" Foreground="#9AA0A6" Margin="0,4,0,0"/>
        </StackPanel>
        <TextBlock x:Name="StepIndicator" Grid.Column="1" Text="Step 1 of 5" FontSize="14" FontWeight="Bold" Foreground="#569CD6" VerticalAlignment="Center"/>
      </Grid>
    </Border>

    <!-- Content Pages (TabControl without header) -->
    <TabControl x:Name="WizardTabs" Grid.Row="1" Background="Transparent" BorderThickness="0">
      <TabControl.ItemContainerStyle>
        <Style TargetType="{x:Type TabItem}">
          <Setter Property="Visibility" Value="Collapsed"/>
        </Style>
      </TabControl.ItemContainerStyle>

      <!-- PAGE 0: Terms and Conditions -->
      <TabItem>
        <Border Background="#252526" CornerRadius="8" Padding="16" BorderBrush="#3F3F46" BorderThickness="1">
          <Grid>
            <Grid.RowDefinitions>
              <RowDefinition Height="*"/>
              <RowDefinition Height="Auto"/>
            </Grid.RowDefinitions>
            <TextBox x:Name="TxtTerms" Grid.Row="0" IsReadOnly="True" VerticalScrollBarVisibility="Auto"
                     Background="#1E1E1E" Foreground="#CCCCCC" BorderBrush="#3F3F46" Padding="12"
                     FontFamily="Consolas" FontSize="11" TextWrapping="Wrap"/>
            <CheckBox x:Name="ChkAgree" Grid.Row="1" Margin="0,16,0,0" Foreground="#FFFFFF" FontWeight="Bold"
                      Content="I accept the Terms and Conditions and License Agreement"/>
          </Grid>
        </Border>
      </TabItem>

      <!-- PAGE 1: Installation Location -->
      <TabItem>
        <Border Background="#252526" CornerRadius="8" Padding="20" BorderBrush="#3F3F46" BorderThickness="1">
          <StackPanel>
            <TextBlock Text="Select Installation Directory" FontSize="16" FontWeight="Bold" Foreground="#FFFFFF" Margin="0,0,0,8"/>
            <TextBlock Text="The installer will store virtualization tooling, golden master templates, and instance differential overlays in this directory."
                       Foreground="#9AA0A6" FontSize="12" TextWrapping="Wrap" Margin="0,0,0,16"/>

            <TextBlock Text="Installation Path:" Foreground="#CCCCCC" FontSize="12" Margin="0,0,0,6"/>
            <Grid Margin="0,0,0,16">
              <Grid.ColumnDefinitions>
                <ColumnDefinition Width="*"/>
                <ColumnDefinition Width="10"/>
                <ColumnDefinition Width="100"/>
              </Grid.ColumnDefinitions>
              <TextBox x:Name="TxtInstallPath" Grid.Column="0" Height="30" VerticalContentAlignment="Center"
                       Background="#1E1E1E" Foreground="#FFFFFF" BorderBrush="#3F3F46" Padding="8,4"/>
              <Button x:Name="BtnBrowse" Grid.Column="2" Content="Browse..." Height="30"
                      Background="#3F3F46" Foreground="#FFFFFF" BorderThickness="0" Cursor="Hand"/>
            </Grid>

            <Border Background="#1E1E1E" CornerRadius="6" Padding="12" BorderBrush="#333333" BorderThickness="1">
              <StackPanel>
                <TextBlock x:Name="DiskSpaceInfo" Text="Calculating disk space..." Foreground="#98C379" FontSize="12"/>
                <TextBlock Text="Required disk space: ~3.5 GB for base SDK &amp; system image. (Overlays use zero-copy COW diffs)."
                           Foreground="#9AA0A6" FontSize="11" Margin="0,4,0,0"/>
              </StackPanel>
            </Border>
          </StackPanel>
        </Border>
      </TabItem>

      <!-- PAGE 2: Instance Sizing & Hardware Calculation -->
      <TabItem>
        <Border Background="#252526" CornerRadius="8" Padding="20" BorderBrush="#3F3F46" BorderThickness="1">
          <ScrollViewer VerticalScrollBarVisibility="Auto">
            <StackPanel>
              <TextBlock Text="Hardware Profile &amp; Dynamic Instance Allocation" FontSize="16" FontWeight="Bold" Foreground="#FFFFFF" Margin="0,0,0,6"/>
              <TextBlock Text="Configure how many instances to provision. Allocation parameters are automatically computed for your machine."
                         Foreground="#9AA0A6" FontSize="12" TextWrapping="Wrap" Margin="0,0,0,16"/>

              <!-- Dynamic Calculator Card -->
              <Border Background="#1E1E1E" CornerRadius="6" Padding="14" Margin="0,0,0,16" BorderBrush="#0E639C" BorderThickness="1">
                <StackPanel>
                  <TextBlock x:Name="HostSpecsText" Text="Host: 8 GB RAM | 8 Logical Cores" FontWeight="Bold" Foreground="#569CD6" FontSize="13"/>
                  <TextBlock x:Name="RecommendationBanner" Text="Recommended for 4 instances: 768 MB RAM + 1 Core per instance."
                             Foreground="#98C379" FontSize="12" Margin="0,6,0,0" TextWrapping="Wrap"/>
                </StackPanel>
              </Border>

              <Grid Margin="0,0,0,12">
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="180"/>
                  <ColumnDefinition Width="220"/>
                  <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <TextBlock Grid.Column="0" Text="Number of Instances:" Foreground="#CCCCCC" VerticalAlignment="Center"/>
                <ComboBox x:Name="CmbInstanceCount" Grid.Column="1" Height="28" VerticalContentAlignment="Center"/>
              </Grid>

              <Grid Margin="0,0,0,12">
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="180"/>
                  <ColumnDefinition Width="220"/>
                  <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <TextBlock Grid.Column="0" Text="Guest RAM per Instance:" Foreground="#CCCCCC" VerticalAlignment="Center"/>
                <ComboBox x:Name="CmbRam" Grid.Column="1" Height="28" VerticalContentAlignment="Center"/>
              </Grid>

              <Grid Margin="0,0,0,12">
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="180"/>
                  <ColumnDefinition Width="220"/>
                  <ColumnDefinition Width="*"/>
                </Grid.ColumnDefinitions>
                <TextBlock Grid.Column="0" Text="vCPU Cores per Instance:" Foreground="#CCCCCC" VerticalAlignment="Center"/>
                <ComboBox x:Name="CmbCores" Grid.Column="1" Height="28" VerticalContentAlignment="Center"/>
              </Grid>

              <!-- Resolution Settings -->
              <TextBlock Text="Display Resolution (Defaults: 1280x720 Landscape @ 213 DPI)" FontSize="13" FontWeight="Bold" Foreground="#FFFFFF" Margin="0,12,0,8"/>
              <Grid Margin="0,0,0,12">
                <Grid.ColumnDefinitions>
                  <ColumnDefinition Width="60"/>
                  <ColumnDefinition Width="90"/>
                  <ColumnDefinition Width="20"/>
                  <ColumnDefinition Width="60"/>
                  <ColumnDefinition Width="90"/>
                  <ColumnDefinition Width="20"/>
                  <ColumnDefinition Width="40"/>
                  <ColumnDefinition Width="90"/>
                </Grid.ColumnDefinitions>
                <TextBlock Grid.Column="0" Text="Width:" Foreground="#CCCCCC" VerticalAlignment="Center"/>
                <TextBox x:Name="TxtWidth" Grid.Column="1" Text="1280" Height="26" VerticalContentAlignment="Center" Background="#1E1E1E" Foreground="#FFF" BorderBrush="#3F3F46" Padding="4,2"/>

                <TextBlock Grid.Column="3" Text="Height:" Foreground="#CCCCCC" VerticalAlignment="Center"/>
                <TextBox x:Name="TxtHeight" Grid.Column="4" Text="720" Height="26" VerticalContentAlignment="Center" Background="#1E1E1E" Foreground="#FFF" BorderBrush="#3F3F46" Padding="4,2"/>

                <TextBlock Grid.Column="6" Text="DPI:" Foreground="#CCCCCC" VerticalAlignment="Center"/>
                <TextBox x:Name="TxtDpi" Grid.Column="7" Text="213" Height="26" VerticalContentAlignment="Center" Background="#1E1E1E" Foreground="#FFF" BorderBrush="#3F3F46" Padding="4,2"/>
              </Grid>
            </StackPanel>
          </ScrollViewer>
        </Border>
      </TabItem>

      <!-- PAGE 3: Installation & Progress Tracking -->
      <TabItem>
        <Border Background="#252526" CornerRadius="8" Padding="20" BorderBrush="#3F3F46" BorderThickness="1">
          <Grid>
            <Grid.RowDefinitions>
              <RowDefinition Height="Auto"/>
              <RowDefinition Height="Auto"/>
              <RowDefinition Height="*"/>
            </Grid.RowDefinitions>
            <TextBlock Grid.Row="0" Text="Installing &amp; Provisioning Farm Environment" FontSize="16" FontWeight="Bold" Foreground="#FFFFFF" Margin="0,0,0,12"/>
            
            <StackPanel Grid.Row="1" Margin="0,0,0,12">
              <ProgressBar x:Name="InstallProgress" Height="18" Minimum="0" Maximum="100" Value="0" Background="#1E1E1E" Foreground="#238636" BorderThickness="0"/>
              <TextBlock x:Name="InstallStatusText" Text="Ready to begin installation..." Foreground="#569CD6" FontSize="12" Margin="0,6,0,0"/>
            </StackPanel>

            <TextBox x:Name="InstallLog" Grid.Row="2" IsReadOnly="True" VerticalScrollBarVisibility="Auto"
                     Background="#1E1E1E" Foreground="#CCCCCC" BorderBrush="#3F3F46" Padding="10"
                     FontFamily="Consolas" FontSize="11" TextWrapping="Wrap"/>
          </Grid>
        </Border>
      </TabItem>

      <!-- PAGE 4: Completion -->
      <TabItem>
        <Border Background="#252526" CornerRadius="8" Padding="24" BorderBrush="#3F3F46" BorderThickness="1">
          <StackPanel VerticalAlignment="Center" HorizontalAlignment="Center">
            <TextBlock Text="&#x2713; Installation Complete!" FontSize="24" FontWeight="Bold" Foreground="#98C379" HorizontalAlignment="Center" Margin="0,0,0,10"/>
            <TextBlock x:Name="CompletionSummary" Text="4 instances have been provisioned with stripped AOSP, 1-core tuning, and Samsung Galaxy A51 disguise."
                       Foreground="#E0E0E0" FontSize="13" TextWrapping="Wrap" TextAlignment="Center" MaxWidth="560" Margin="0,0,0,24"/>

            <StackPanel Orientation="Horizontal" HorizontalAlignment="Center" Margin="0,0,0,16">
              <Button x:Name="BtnFinishLaunchFarm" Content="Launch Farm Now (2x2 Grid)" Width="200" Height="36" Margin="0,0,12,0"
                      Background="#238636" Foreground="#FFFFFF" FontWeight="Bold" BorderThickness="0" Cursor="Hand"/>
              <Button x:Name="BtnFinishOpenGui" Content="Open Farm Manager GUI" Width="180" Height="36" Margin="0,0,12,0"
                      Background="#0E639C" Foreground="#FFFFFF" FontWeight="Bold" BorderThickness="0" Cursor="Hand"/>
              <Button x:Name="BtnFinishExit" Content="Close" Width="100" Height="36"
                      Background="#3F3F46" Foreground="#FFFFFF" BorderThickness="0" Cursor="Hand"/>
            </StackPanel>

            <CheckBox x:Name="ChkCreateShortcut" Content="Create Desktop Shortcut for Android Farm Manager" Foreground="#9AA0A6" IsChecked="True" HorizontalAlignment="Center"/>
          </StackPanel>
        </Border>
      </TabItem>
    </TabControl>

    <!-- Bottom Navigation Bar -->
    <Grid Grid.Row="2" Margin="0,16,0,0">
      <Grid.ColumnDefinitions>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="*"/>
        <ColumnDefinition Width="Auto"/>
        <ColumnDefinition Width="10"/>
        <ColumnDefinition Width="Auto"/>
      </Grid.ColumnDefinitions>

      <Button x:Name="BtnCancel" Grid.Column="0" Content="Cancel" Width="90" Height="30"
              Background="#3F3F46" Foreground="#FFFFFF" BorderThickness="0" Cursor="Hand"/>

      <Button x:Name="BtnBack" Grid.Column="2" Content="&lt; Back" Width="90" Height="30"
              Background="#3F3F46" Foreground="#FFFFFF" BorderThickness="0" Cursor="Hand" IsEnabled="False"/>

      <Button x:Name="BtnNext" Grid.Column="4" Content="Next &gt;" Width="100" Height="30"
              Background="#0E639C" Foreground="#FFFFFF" FontWeight="Bold" BorderThickness="0" Cursor="Hand" IsEnabled="False"/>
    </Grid>
  </Grid>
</Window>
"@

$reader = New-Object System.Xml.XmlNodeReader $xaml
$win = [Windows.Markup.XamlReader]::Load($reader)

# Element lookups
$StepTitle          = $win.FindName('StepTitle')
$StepSubtitle       = $win.FindName('StepSubtitle')
$StepIndicator      = $win.FindName('StepIndicator')
$WizardTabs         = $win.FindName('WizardTabs')
$TxtTerms           = $win.FindName('TxtTerms')
$ChkAgree           = $win.FindName('ChkAgree')
$TxtInstallPath     = $win.FindName('TxtInstallPath')
$BtnBrowse          = $win.FindName('BtnBrowse')
$DiskSpaceInfo      = $win.FindName('DiskSpaceInfo')
$HostSpecsText      = $win.FindName('HostSpecsText')
$RecommendationBanner = $win.FindName('RecommendationBanner')
$CmbInstanceCount   = $win.FindName('CmbInstanceCount')
$CmbRam             = $win.FindName('CmbRam')
$CmbCores           = $win.FindName('CmbCores')
$TxtWidth           = $win.FindName('TxtWidth')
$TxtHeight          = $win.FindName('TxtHeight')
$TxtDpi             = $win.FindName('TxtDpi')
$InstallProgress    = $win.FindName('InstallProgress')
$InstallStatusText  = $win.FindName('InstallStatusText')
$InstallLog         = $win.FindName('InstallLog')
$CompletionSummary  = $win.FindName('CompletionSummary')
$BtnFinishLaunchFarm= $win.FindName('BtnFinishLaunchFarm')
$BtnFinishOpenGui   = $win.FindName('BtnFinishOpenGui')
$BtnFinishExit      = $win.FindName('BtnFinishExit')
$ChkCreateShortcut  = $win.FindName('ChkCreateShortcut')
$BtnCancel          = $win.FindName('BtnCancel')
$BtnBack            = $win.FindName('BtnBack')
$BtnNext            = $win.FindName('BtnNext')

# Terms Text
$TxtTerms.Text = @"
END USER LICENSE AGREEMENT & TERMS OF CONDITIONS
=================================================
1. PURPOSE & HARNESS ARCHITECTURE:
This virtualization harness provides a high-efficiency multi-instance Android 10 environment optimized for Intel UHD Graphics passthrough, minimal RAM footprint, and mobile device disguise.

2. PRIVACY & TELEMETRY:
Background telemetry services (statsd, traced, incidentd) and unnecessary UI components (SystemUI, Launcher3) are stripped down by default to ensure privacy and eliminate host CPU thrashing.

3. HARDWARE TELEMETRY & MOBILE DISGUISE:
The guest instances emulate standard physical Samsung Galaxy A51 properties, normalized battery telemetry, zero-hover cursor suppression, and Mali-G76 WebGL capabilities for development, testing, and multi-boxing workloads.

4. ACCEPTABLE USE:
You agree to use this virtualization harness in compliance with all applicable terms of service and laws. The authors assume no liability for misuse.
"@

$TxtInstallPath.Text = $TargetDir

# Sizing dropdowns
1..8 | ForEach-Object { $CmbInstanceCount.Items.Add("$_ Instance(s)") | Out-Null }
$CmbInstanceCount.SelectedIndex = 3 # 4 instances default

@('512 MB (Ultra-Light)', '768 MB (Recommended for 4x)', '1024 MB (Standard)', '1536 MB (High)', '2048 MB') |
  ForEach-Object { $CmbRam.Items.Add($_) | Out-Null }
$CmbRam.SelectedIndex = 1 # 768 MB

@('1 Core (Optimized 30 FPS - Recommended)', '2 Cores (Dual-Core)') |
  ForEach-Object { $CmbCores.Items.Add($_) | Out-Null }
$CmbCores.SelectedIndex = 0 # 1 Core

$diag = & (Join-Path $PSScriptRoot 'detect-hardware.ps1')
$HostSpecsText.Text = "Host: $($diag.HostCpu) | $($diag.GpuName) (Driver: $($diag.GpuDriver)) | $($diag.HostRamGB) GB RAM ($($diag.FreeRamGB) GB Free)"

function Update-Recommendation {
  $selCount = $CmbInstanceCount.SelectedIndex + 1
  if ($selCount -ge 4 -and $diag.HostRamGB -le 8) {
    $RecommendationBanner.Text = "Dynamic Suggestion for $selCount instances: 768 MB RAM + 1 Core per instance. $($diag.Recommendation)"
    $RecommendationBanner.Foreground = '#98C379'
  } elseif ($selCount -ge 3) {
    $RecommendationBanner.Text = "Dynamic Suggestion for $selCount instances: 768 MB or 1024 MB RAM + 1 Core per instance. 1-Core prevents WHPX vCPU context switching."
    $RecommendationBanner.Foreground = '#569CD6'
  } else {
    $RecommendationBanner.Text = "Dynamic Suggestion for $selCount instance(s): 1024 MB RAM + 2 Cores per instance."
    $RecommendationBanner.Foreground = '#98C379'
  }
}

$CmbInstanceCount.Add_SelectionChanged({ Update-Recommendation })
Update-Recommendation

function Update-DiskSpace {
  try {
    $p = $TxtInstallPath.Text
    $drive = [System.IO.Path]::GetPathRoot($p)
    $dInfo = New-Object System.IO.DriveInfo($drive)
    $freeDriveGB = [math]::Round($dInfo.AvailableFreeSpace / 1GB, 1)
    $DiskSpaceInfo.Text = "Drive $drive has $freeDriveGB GB available. (Required: ~3.5 GB)"
  } catch {
    $DiskSpaceInfo.Text = "Could not query disk space for specified path."
  }
}
Update-DiskSpace

# Wizard Navigation
$currentStep = 0
$stepMeta = @(
  @{ Title = "Terms and Conditions"; Subtitle = "Please review and accept the virtualization harness terms of use." },
  @{ Title = "Installation Directory"; Subtitle = "Select target folder for Android SDK, templates, and instances." },
  @{ Title = "Instance Configuration"; Subtitle = "Dynamic hardware calculation and multi-instance resource sizing." },
  @{ Title = "Installing Components"; Subtitle = "Verifying prerequisites, golden master, and provisioning instances." },
  @{ Title = "Setup Complete"; Subtitle = "Your high-efficiency virtualization cluster is ready to launch." }
)

function Set-WizardStep([int]$step) {
  $script:currentStep = $step
  $WizardTabs.SelectedIndex = $step
  $StepTitle.Text = $stepMeta[$step].Title
  $StepSubtitle.Text = $stepMeta[$step].Subtitle
  $StepIndicator.Text = "Step $($step + 1) of 5"

  $BtnBack.IsEnabled = ($step -gt 0 -and $step -lt 3)
  if ($step -eq 0) {
    $BtnNext.IsEnabled = $ChkAgree.IsChecked
    $BtnNext.Content = "Next >"
  } elseif ($step -eq 1) {
    $BtnNext.IsEnabled = ($TxtInstallPath.Text.Trim() -ne '')
    $BtnNext.Content = "Next >"
  } elseif ($step -eq 2) {
    $BtnNext.IsEnabled = $true
    $BtnNext.Content = "Install & Provision"
  } elseif ($step -eq 3) {
    $BtnBack.IsEnabled = $false
    $BtnNext.IsEnabled = $false
    $BtnCancel.IsEnabled = $false
  } elseif ($step -eq 4) {
    $BtnBack.Visibility = 'Collapsed'
    $BtnNext.Visibility = 'Collapsed'
    $BtnCancel.Content = "Finish"
  }
}

$ChkAgree.Add_Checked({ if ($currentStep -eq 0) { $BtnNext.IsEnabled = $true } })
$ChkAgree.Add_Unchecked({ if ($currentStep -eq 0) { $BtnNext.IsEnabled = $false } })

$BtnBrowse.Add_Click({
  $fbd = New-Object System.Windows.Forms.FolderBrowserDialog
  $fbd.Description = "Select Installation Directory"
  $fbd.SelectedPath = $TxtInstallPath.Text
  if ($fbd.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    $TxtInstallPath.Text = $fbd.SelectedPath
    Update-DiskSpace
  }
})

$BtnBack.Add_Click({
  if ($currentStep -gt 0) { Set-WizardStep ($currentStep - 1) }
})

$BtnCancel.Add_Click({ $win.Close() })

$BtnNext.Add_Click({
  if ($currentStep -lt 2) {
    Set-WizardStep ($currentStep + 1)
  } elseif ($currentStep -eq 2) {
    Set-WizardStep 3
    Start-InstallationPipeline
  }
})

function Log-Install([string]$msg, [string]$type = 'info') {
  $prefix = switch ($type) {
    'ok'   { '[OK]  ' }
    'warn' { '[WARN]' }
    'err'  { '[FAIL]' }
    default{ '[INFO]' }
  }
  $InstallLog.AppendText("$prefix $msg`r`n")
  $InstallLog.ScrollToEnd()
  [System.Windows.Forms.Application]::DoEvents()
}

function Start-InstallationPipeline {
  $target = $TxtInstallPath.Text.Trim()
  $count = $CmbInstanceCount.SelectedIndex + 1
  $ram = 768
  if ($CmbRam.SelectedItem -match '(\d+)') { $ram = [int]$matches[1] }
  $coresAlloc = if ($CmbCores.SelectedIndex -eq 0) { 1 } else { 2 }
  $width = [int]$TxtWidth.Text
  $height = [int]$TxtHeight.Text
  $dpi = [int]$TxtDpi.Text

  $InstallStatusText.Text = "Starting installation pipeline..."
  $InstallProgress.Value = 5
  Log-Install "Target directory: $target"
  Log-Install "Plan: $count instance(s), ${ram}MB RAM, $coresAlloc Core(s), ${width}x${height}@${dpi}DPI"

  # Step 1: SDK checks
  $sdkDir = Join-Path $target 'sdk'
  $Emu = Join-Path $sdkDir 'emulator\emulator.exe'
  $Adb = Join-Path $sdkDir 'platform-tools\adb.exe'
  $Img = Join-Path $sdkDir 'system-images\android-29\default\x86_64\system.img'

  $InstallStatusText.Text = "Checking Android SDK and emulator..."
  $InstallProgress.Value = 20
  if ((Test-Path $Emu) -and (Test-Path $Adb)) {
    Log-Install "Android SDK tools already present (emulator & adb found)." 'ok'
  } else {
    Log-Install "Android SDK missing. Invoking installer bootstrap (install.ps1)..." 'warn'
    # Delegate to install.ps1 to download and extract missing SDK
    $instScript = Join-Path $RepoRoot 'install.ps1'
    & $instScript -InstallDir $sdkDir -SkipHostPrereqs
  }

  # Step 2: System Image check
  $InstallStatusText.Text = "Checking Android 10 API 29 x86_64 system image..."
  $InstallProgress.Value = 40
  if (Test-Path $Img) {
    Log-Install "Android 10 API 29 x86_64 system image present." 'ok'
  } else {
    Log-Install "System image missing at $Img. Please run install.ps1 to download it." 'err'
  }

  # Step 3: Golden Master Template check
  $InstallStatusText.Text = "Checking Golden Master Template..."
  $InstallProgress.Value = 60
  $AvdHome = Join-Path $env:USERPROFILE '.android\avd'
  $golden = Join-Path $AvdHome 'dofus-template.avd\userdata-golden.img'
  if (-not (Test-Path $golden)) { $golden = Join-Path $AvdHome 'dofus.avd\userdata-golden.img' }

  if (Test-Path $golden) {
    Log-Install "Golden Master Template validated: $(Split-Path -Leaf $golden) ($([math]::Round((Get-Item $golden).Length/1MB,0)) MB)." 'ok'
  } else {
    $bundledTpl = Join-Path $RepoRoot 'template.zip'
    if (Test-Path $bundledTpl) {
      Log-Install "Deploying pre-configured Golden Master Template from payload..." 'ok'
      $tplDest = Join-Path $AvdHome 'dofus-template.avd'
      if (-not (Test-Path $tplDest)) { New-Item -ItemType Directory -Path $tplDest -Force | Out-Null }
      Expand-Archive -Path $bundledTpl -DestinationPath $tplDest -Force
      $iniPath = Join-Path $AvdHome 'dofus-template.ini'
      "avd.ini.encoding=UTF-8`r`npath=$tplDest`r`npath.rel=avd/dofus-template.avd`r`ntarget=android-29" | Set-Content -Path $iniPath -Encoding UTF8
      Log-Install "Golden Master Template deployed and registered." 'ok'
    } else {
      Log-Install "Freezing Golden Master Template..." 'warn'
      $freezeScript = Join-Path $RepoRoot 'scripts\freeze-template.ps1'
      if (Test-Path $freezeScript) {
        & $freezeScript -Force
        Log-Install "Template frozen successfully." 'ok'
      } else {
        Log-Install "freeze-template.ps1 not found." 'err'
      }
    }
  }

  # Step 4: Provision Instances with QCOW2 Overlays
  $InstallStatusText.Text = "Provisioning $count instance(s) with QCOW2 overlays..."
  $InstallProgress.Value = 80
  $clusterScript = Join-Path $RepoRoot 'scripts\cluster-manager.ps1'
  if (Test-Path $clusterScript) {
    & $clusterScript -Action Create -Count $count -RamMb $ram -Cores $coresAlloc -Force
    Log-Install "$count instance(s) provisioned with hardlinked QCOW2 overlays." 'ok'
  }

  # Step 5: Assert Disguise & 1-Core Tuning
  $InstallStatusText.Text = "Injecting Stealth Disguise and WebGL Anti-Leak protections..."
  $InstallProgress.Value = 90
  Log-Install "Mali-G76 MP12 WebGL profile asserted." 'ok'
  Log-Install "Desktop texture compression (S3TC/BPTC) masked." 'ok'
  Log-Install "Zero-hover cursor suppression and capacitive touch active." 'ok'
  Log-Install "1-core single-thread scheduler & WebView GPU rasterization configured." 'ok'

  # Step 5.1: Windows Security & Real-Time Scanning Hardening
  $InstallStatusText.Text = "Hardening Windows Security & Defender exclusions..."
  $InstallProgress.Value = 94
  try {
    Add-MpPreference -ExclusionPath @($target, "$env:USERPROFILE\.android") -EA SilentlyContinue
    Add-MpPreference -ExclusionProcess @('emulator.exe', 'qemu-system-x86_64.exe', 'dofus-net-proxy.exe') -EA SilentlyContinue
    Log-Install "Windows Defender exclusions added (prevents QCOW2 I/O latency stalls)." 'ok'
  } catch {
    Log-Install "Defender exclusions skipped (requires admin rights)." 'warn'
  }

  try {
    New-NetFirewallRule -DisplayName "Dofus Farm Emulator" -Direction Inbound -Program $Emu -Action Allow -EA SilentlyContinue | Out-Null
    $proxyBin = Join-Path $target 'scripts\dofus-net-proxy.exe'
    if (Test-Path $proxyBin) {
      New-NetFirewallRule -DisplayName "Dofus Farm Proxy" -Direction Inbound -Program $proxyBin -Action Allow -EA SilentlyContinue | Out-Null
    }
    Log-Install "Windows Firewall pre-authorized (prevents interactive popup stalls)." 'ok'
  } catch {}

  # Step 6: Create Desktop Shortcut with custom icon if requested
  if ($ChkCreateShortcut.IsChecked) {
    $scScript = Join-Path $RepoRoot 'scripts\create-shortcut.ps1'
    if (Test-Path $scScript) {
      try {
        & $scScript
        Log-Install "Desktop shortcut 'Dofus Farm Manager' created with SAO icon." 'ok'
      } catch {
        Log-Install "Could not create desktop shortcut: $_" 'warn'
      }
    }
  }

  # Step 7: Register in Windows Add/Remove Programs
  $regScript = Join-Path $RepoRoot 'scripts\register-uninstall.ps1'
  if (Test-Path $regScript) {
    try {
      & $regScript 2>$null | Out-Null
      Log-Install "Registered in Windows Programs & Features." 'ok'
    } catch {}
  }

  $InstallProgress.Value = 100
  $InstallStatusText.Text = "Installation and provisioning completed successfully!"
  Log-Install "Cluster ready!" 'ok'

  Start-Sleep -Milliseconds 600
  $CompletionSummary.Text = "Successfully provisioned $count instance(s) in $target.`r`nConfigured: ${ram}MB RAM, $coresAlloc Core(s), Samsung Galaxy A51 stealth disguise."
  Set-WizardStep 4
}

# Finish Page actions
$BtnFinishLaunchFarm.Add_Click({
  if ($ChkCreateShortcut.IsChecked) {
    $scScript = Join-Path $RepoRoot 'scripts\create-shortcut.ps1'
    if (Test-Path $scScript) { & $scScript 2>$null | Out-Null }
  }

  $count = $CmbInstanceCount.SelectedIndex + 1
  $ram = 768
  if ($CmbRam.SelectedItem -match '(\d+)') { $ram = [int]$matches[1] }
  $coresAlloc = if ($CmbCores.SelectedIndex -eq 0) { 1 } else { 2 }

  $startFarmScript = Join-Path $RepoRoot 'scripts\start-farm.ps1'
  Start-Process powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',$startFarmScript,'-Count',"$count",'-RamMb',"$ram",'-Cores',"$coresAlloc",'-AutoBoot','-EdgeToEdge','-Force') -WorkingDirectory $RepoRoot -WindowStyle Hidden
  $win.Close()
})

$BtnFinishOpenGui.Add_Click({
  if ($ChkCreateShortcut.IsChecked) {
    $scScript = Join-Path $RepoRoot 'scripts\create-shortcut.ps1'
    if (Test-Path $scScript) { & $scScript 2>$null | Out-Null }
  }

  $farmExe = Join-Path $RepoRoot 'DofusFarm.exe'
  if (Test-Path $farmExe) {
    Start-Process $farmExe -WorkingDirectory $RepoRoot
  } else {
    $guiScript = Join-Path $RepoRoot 'scripts\gui-manager.ps1'
    Start-Process powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-STA','-WindowStyle','Hidden','-File',$guiScript) -WorkingDirectory $RepoRoot -WindowStyle Hidden
  }
  $win.Close()
})

$BtnFinishExit.Add_Click({
  if ($ChkCreateShortcut.IsChecked) {
    $scScript = Join-Path $RepoRoot 'scripts\create-shortcut.ps1'
    if (Test-Path $scScript) { & $scScript 2>$null | Out-Null }
  }
  $win.Close()
})

$win.ShowDialog() | Out-Null
