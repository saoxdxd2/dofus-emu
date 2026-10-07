<#
.SYNOPSIS
  Compiles native Windows binaries setup.exe and DofusFarm.exe with embedded sao_image icon.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$Ico = Join-Path $RepoRoot 'app_icon.ico'

if (-not (Test-Path $Ico)) {
  & (Join-Path $PSScriptRoot 'build-icon.ps1')
}

# 1. Compile setup.exe
$setupCs = @"
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

namespace DofusSetup
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string scriptPath = Path.Combine(baseDir, "scripts", "installer-gui.ps1");
                if (!File.Exists(scriptPath))
                {
                    scriptPath = Path.Combine(baseDir, "installer-gui.ps1");
                }

                if (!File.Exists(scriptPath))
                {
                    MessageBox.Show("Could not find installer-gui.ps1 in: " + baseDir, "Setup Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
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
                MessageBox.Show("Failed to launch setup wizard: " + ex.Message, "Setup Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}
"@

$tmpSetup = Join-Path $env:TEMP 'Setup.cs'
[System.IO.File]::WriteAllText($tmpSetup, $setupCs)
$outSetup = Join-Path $RepoRoot 'setup.exe'

Write-Host "Compiling setup.exe..." -ForegroundColor Cyan
& $Csc /nologo /target:winexe "/win32icon:$Ico" "/out:$outSetup" /reference:System.Windows.Forms.dll $tmpSetup
if ($LASTEXITCODE -eq 0) {
  Write-Host "  [OK] Compiled: $outSetup ($((Get-Item $outSetup).Length) bytes)" -ForegroundColor Green
} else {
  Write-Error "Failed to compile setup.exe"
}

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

# 3. Refresh desktop shortcut
$scScript = Join-Path $PSScriptRoot 'create-shortcut.ps1'
if (Test-Path $scScript) {
  & $scScript -TargetExe $outFarm
}
