<#
.SYNOPSIS
  Compiles native Windows binaries setup.exe and DofusFarm.exe with embedded sao_image icon.
#>
[CmdletBinding()]
param(
  [switch] $CreateShortcut
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$Ico = Join-Path $RepoRoot 'app_icon.ico'
$SetupIco = Join-Path $RepoRoot 'installer_icon.ico'

if (-not (Test-Path $Ico)) {
  & (Join-Path $PSScriptRoot 'build-icon.ps1')
}
if (-not (Test-Path $SetupIco)) {
  & (Join-Path $PSScriptRoot 'build-installer-icon.ps1')
}

# 1. Compile standalone setup.exe and DofusFarmSetup.exe with embedded payload
& (Join-Path $PSScriptRoot 'build-standalone-installer.ps1')

# 2. Compile DofusFarm.exe
$farmCs = @"
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

namespace DofusFarmLauncher
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string scriptPath = Path.Combine(baseDir, "scripts", "gui-manager.ps1");
                if (!File.Exists(scriptPath))
                {
                    MessageBox.Show("Could not find gui-manager.ps1 in: " + baseDir, "Launcher Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }

                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "powershell.exe";
                psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File \"" + scriptPath + "\"";
                psi.WorkingDirectory = baseDir;
                psi.UseShellExecute = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;

                Process.Start(psi);
            }
            catch (Exception ex)
            {
                MessageBox.Show("Failed to launch Farm Manager: " + ex.Message, "Launcher Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}
"@

$tmpFarm = Join-Path $env:TEMP 'FarmLauncher.cs'
[System.IO.File]::WriteAllText($tmpFarm, $farmCs)
$outFarm = Join-Path $RepoRoot 'DofusFarm.exe'

Write-Host "Compiling DofusFarm.exe..." -ForegroundColor Cyan
& $Csc /nologo /target:winexe "/win32icon:$Ico" "/out:$outFarm" /reference:System.Windows.Forms.dll $tmpFarm
if ($LASTEXITCODE -eq 0) {
  Write-Host "  [OK] Compiled: $outFarm ($((Get-Item $outFarm).Length) bytes)" -ForegroundColor Green
} else {
  Write-Error "Failed to compile DofusFarm.exe"
}

# 3. Compile uninstall.exe
$uninstallCs = @"
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

namespace DofusUninstall
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string scriptPath = Path.Combine(baseDir, "scripts", "uninstall.ps1");
                if (!File.Exists(scriptPath))
                {
                    scriptPath = Path.Combine(baseDir, "uninstall.ps1");
                }

                if (!File.Exists(scriptPath))
                {
                    MessageBox.Show("Could not find uninstall.ps1 in: " + baseDir, "Uninstall Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }

                string extraArgs = string.Empty;
                if (args != null && args.Length > 0)
                {
                    foreach (string arg in args)
                    {
                        if (arg.Equals("/quiet", StringComparison.OrdinalIgnoreCase) || arg.Equals("-quiet", StringComparison.OrdinalIgnoreCase))
                        {
                            extraArgs += " -Quiet";
                        }
                        else if (arg.Equals("/purge", StringComparison.OrdinalIgnoreCase) || arg.Equals("-purgedata", StringComparison.OrdinalIgnoreCase))
                        {
                            extraArgs += " -PurgeData";
                        }
                    }
                }

                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "powershell.exe";
                psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -STA -File \"" + scriptPath + "\"" + extraArgs;
                psi.WorkingDirectory = baseDir;
                psi.UseShellExecute = true;

                Process p = Process.Start(psi);
                if (p != null) p.WaitForExit();
            }
            catch (Exception ex)
            {
                MessageBox.Show("Failed to launch uninstaller: " + ex.Message, "Uninstall Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}
"@

$tmpUninstall = Join-Path $env:TEMP 'Uninstall.cs'
[System.IO.File]::WriteAllText($tmpUninstall, $uninstallCs)
$outUninstall = Join-Path $RepoRoot 'uninstall.exe'

Write-Host "Compiling uninstall.exe with classic installer icon..." -ForegroundColor Cyan
& $Csc /nologo /target:winexe "/win32icon:$SetupIco" "/out:$outUninstall" /reference:System.Windows.Forms.dll $tmpUninstall
if ($LASTEXITCODE -eq 0) {
  Write-Host "  [OK] Compiled: $outUninstall ($((Get-Item $outUninstall).Length) bytes)" -ForegroundColor Green
} else {
  Write-Error "Failed to compile uninstall.exe"
}

# 4. Refresh desktop shortcut (only if requested)
if ($CreateShortcut) {
  $scScript = Join-Path $PSScriptRoot 'create-shortcut.ps1'
  if (Test-Path $scScript) {
    & $scScript -TargetExe $outFarm
  }
}
