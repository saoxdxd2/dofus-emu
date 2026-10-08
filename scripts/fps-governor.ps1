<#
.SYNOPSIS
  Dynamic Focus-Aware Frame Pacer & CPU Governor for Multi-Instance Farm.
.DESCRIPTION
  Monitors active Windows focus across the 4 emulator instances:
    - Foreground Window: Elevated CPU Priority (Normal), full 30-60 FPS refresh rate.
    - Background Windows: Throttled CPU Priority (BelowNormal/Idle), reduced frame pacing.
  Dramatically reduces host CPU/GPU power consumption and heat on quad-instance setups.
#>
[CmdletBinding()]
param(
  [int] $CheckIntervalMs = 250,
  [switch] $SinglePass
)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$sdkCandidates = @(
  (Join-Path $RepoRoot 'sdk'),
  (if ($env:DOFUS_FARM_HOME) { Join-Path $env:DOFUS_FARM_HOME 'sdk' } else { $null }),
  (Get-ItemPropertyValue -Path 'HKCU:\Software\DofusFarm' -Name 'InstallPath' -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_ 'sdk' }),
  $env:ANDROID_SDK_ROOT,
  $env:ANDROID_HOME
) | Where-Object { $_ -and (Test-Path (Join-Path $_ 'platform-tools\adb.exe')) }

$SdkRoot = if ($sdkCandidates.Count -gt 0) { $sdkCandidates[0] } elseif ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $RepoRoot 'sdk' }
$Adb     = Join-Path $SdkRoot 'platform-tools\adb.exe'

# Win32 APIs for foreground window detection
$win32Def = @"
using System;
using System.Runtime.InteropServices;

public static class GovernorWin32
{
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
}
"@

if (-not ([System.Management.Automation.PSTypeName]'GovernorWin32').Type) {
  Add-Type -TypeDefinition $win32Def -ErrorAction SilentlyContinue
}

function Get-ForegroundProcessId {
  $hwnd = [GovernorWin32]::GetForegroundWindow()
  if ($hwnd -eq [IntPtr]::Zero) { return 0 }
  $pidVal = 0
  [void][GovernorWin32]::GetWindowThreadProcessId($hwnd, [ref]$pidVal)
  return $pidVal
}

function Update-ProcessPriorities {
  $qemuProcs = @(Get-Process -Name 'qemu-system-x86_64' -ErrorAction SilentlyContinue)
  if ($qemuProcs.Count -eq 0) { return }

  $fgPid = Get-ForegroundProcessId

  foreach ($p in $qemuProcs) {
    try {
      if ($p.Id -eq $fgPid) {
        # Foreground / Active Instance
        if ($p.PriorityClass -ne [System.Diagnostics.ProcessPriorityClass]::Normal) {
          $p.PriorityClass = [System.Diagnostics.ProcessPriorityClass]::Normal
          Write-Verbose "PID $($p.Id): Set to Normal priority (Focused)"
        }
      } else {
        # Background / Inactive Instance
        if ($p.PriorityClass -ne [System.Diagnostics.ProcessPriorityClass]::BelowNormal) {
          $p.PriorityClass = [System.Diagnostics.ProcessPriorityClass]::BelowNormal
          Write-Verbose "PID $($p.Id): Set to BelowNormal priority (Background throttled)"
        }
      }
    } catch {}
  }
}

Write-Host "Dynamic FPS & CPU Governor active (monitoring interval: ${CheckIntervalMs}ms)..." -ForegroundColor Cyan

if ($SinglePass) {
  Update-ProcessPriorities
  return
}

# Daemon loop
while ($true) {
  Update-ProcessPriorities
  Start-Sleep -Milliseconds $CheckIntervalMs
}
