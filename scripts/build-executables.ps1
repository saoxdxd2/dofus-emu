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

# 1. Compile DofusFarm.exe
$farmCs = @"
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Dofus Farm Manager")]
[assembly: AssemblyDescription("Dofus Touch High-Efficiency Virtualization Farm Manager")]
[assembly: AssemblyCompany("Dofus Farm Project")]
[assembly: AssemblyProduct("Dofus Farm Manager")]
[assembly: AssemblyCopyright("Copyright © 2026")]
[assembly: AssemblyFileVersion("1.0.0.0")]
[assembly: AssemblyVersion("1.0.0.0")]

namespace DofusFarmLauncher
{
    static class Program
    {
        private const string Salt = "DOFUS_FARM_INTEGRITY_SALT_2026_K7X9Q";

        private static bool VerifyIntegrity(string baseDir)
        {
            string manifestPath = Path.Combine(baseDir, "scripts", "app.integrity");
            if (!File.Exists(manifestPath)) return false;

            try
            {
                string[] lines = File.ReadAllLines(manifestPath);
                string expectedSig = "";
                StringBuilder allHashes = new StringBuilder();

                using (SHA256 sha = SHA256.Create())
                {
                    foreach (string line in lines)
                    {
                        if (line.StartsWith("SIGNATURE|"))
                        {
                            expectedSig = line.Substring("SIGNATURE|".Length).Trim();
                        }
                        else
                        {
                            string[] parts = line.Split('|');
                            if (parts.Length == 2)
                            {
                                string rel = parts[0].Trim();
                                string expectedHex = parts[1].Trim();
                                string fullPath = Path.Combine(baseDir, rel);
                                if (!File.Exists(fullPath)) return false;

                                byte[] bytes = File.ReadAllBytes(fullPath);
                                byte[] hashBytes = sha.ComputeHash(bytes);
                                string hex = BitConverter.ToString(hashBytes).Replace("-", "");
                                if (!hex.Equals(expectedHex, StringComparison.OrdinalIgnoreCase)) return false;

                                allHashes.Append(expectedHex);
                            }
                        }
                    }

                    byte[] saltData = Encoding.UTF8.GetBytes(allHashes.ToString() + Salt);
                    byte[] sigBytes = sha.ComputeHash(saltData);
                    string computedSig = BitConverter.ToString(sigBytes).Replace("-", "");
                    return computedSig.Equals(expectedSig, StringComparison.OrdinalIgnoreCase);
                }
            }
            catch
            {
                return false;
            }
        }

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

                if (!VerifyIntegrity(baseDir))
                {
                    MessageBox.Show(
                        "Security Integrity Alert:\n\nOne or more core system files have been modified or tampered with.\nExecution halted to prevent unauthorized reverse engineering.",
                        "Security Violation",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Stop);
                    return;
                }

                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "powershell.exe";
                psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File \"" + scriptPath + "\"";
                psi.WorkingDirectory = baseDir;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
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
                psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File \"" + scriptPath + "\"" + extraArgs;
                psi.WorkingDirectory = baseDir;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;

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

# 3. Seal Code Integrity Manifest across all compiled binaries and scripts
Write-Host "Sealing Code Integrity Manifest..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot 'generate-integrity-manifest.ps1')

# 4. Compile standalone setup.exe bundling complete payload
Write-Host "Compiling standalone setup.exe with complete payload..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot 'build-standalone-installer.ps1')

# 5. Refresh desktop shortcut (only if requested)
if ($CreateShortcut) {
  $scScript = Join-Path $PSScriptRoot 'create-shortcut.ps1'
  if (Test-Path $scScript) {
    & $scScript -TargetExe $outFarm
  }
}
