<#
.SYNOPSIS
  Compiles the True Standalone Single-File Windows Installer (DofusFarmSetup.exe).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$InstallerIco = Join-Path $RepoRoot 'installer_icon.ico'
$TargetExe = Join-Path $RepoRoot 'DofusFarmSetup.exe'
$SetupExe = Join-Path $RepoRoot 'setup.exe'

if (-not (Test-Path $InstallerIco)) {
  & (Join-Path $PSScriptRoot 'build-installer-icon.ps1')
}

Add-Type -AssemblyName System.IO.Compression.FileSystem

# 1. Prepare Compressed Payload Archive of all scripts and assets
Write-Host "Packaging installer payload archive..." -ForegroundColor Cyan
$payloadZip = Join-Path $env:TEMP "DofusPayload.zip"
Remove-Item $payloadZip -Force -EA SilentlyContinue

# Create clean staging directory for payload
$stageDir = Join-Path $env:TEMP "dofus_stage_$([System.IO.Path]::GetRandomFileName())"
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
Copy-Item (Join-Path $RepoRoot "scripts") (Join-Path $stageDir "scripts") -Recurse -Force
Copy-Item (Join-Path $RepoRoot "install.ps1") $stageDir -Force -EA SilentlyContinue
Copy-Item (Join-Path $RepoRoot "INSTALL.bat") $stageDir -Force -EA SilentlyContinue
Copy-Item (Join-Path $RepoRoot "installer_icon.ico") $stageDir -Force -EA SilentlyContinue
Copy-Item (Join-Path $RepoRoot "app_icon.ico") $stageDir -Force -EA SilentlyContinue

Compress-Archive -Path "$stageDir\*" -DestinationPath $payloadZip -Force
Remove-Item $stageDir -Recurse -Force -EA SilentlyContinue

$payloadSize = (Get-Item $payloadZip).Length
Write-Host "  [OK] Payload compressed: $([math]::Round($payloadSize/1KB, 1)) KB" -ForegroundColor Green

# 2. C# Standalone Bootstrap Source Code
$csSource = @"
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;

namespace DofusStandaloneSetup
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            string tempDir = null;
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string scriptPath = Path.Combine(baseDir, "scripts", "installer-gui.ps1");

                // If not running directly inside the repo, extract embedded payload
                if (!File.Exists(scriptPath))
                {
                    tempDir = Path.Combine(Path.GetTempPath(), "DofusSetup_" + Process.GetCurrentProcess().Id);
                    if (Directory.Exists(tempDir))
                    {
                        Directory.Delete(tempDir, true);
                    }
                    Directory.CreateDirectory(tempDir);

                    Assembly asm = Assembly.GetExecutingAssembly();
                    using (Stream s = asm.GetManifestResourceStream("DofusPayload.zip"))
                    {
                        if (s != null)
                        {
                            string zipPath = Path.Combine(tempDir, "payload.zip");
                            using (FileStream fs = new FileStream(zipPath, FileMode.Create, FileAccess.Write))
                            {
                                s.CopyTo(fs);
                            }
                            ZipFile.ExtractToDirectory(zipPath, tempDir);
                            try { File.Delete(zipPath); } catch {}
                            scriptPath = Path.Combine(tempDir, "scripts", "installer-gui.ps1");
                        }
                    }
                }

                if (!File.Exists(scriptPath))
                {
                    MessageBox.Show(
                        "Unable to unpack setup components.\nPlease ensure you have permission to write to your temp directory.",
                        "Dofus Farm Setup",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error);
                    return;
                }

                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = "powershell.exe";
                psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -STA -File \"" + scriptPath + "\"";
                psi.UseShellExecute = true;
                Process p = Process.Start(psi);
                if (p != null)
                {
                    p.WaitForExit();
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show("Installer runtime error:\n" + ex.Message, "Dofus Farm Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            finally
            {
                if (tempDir != null && Directory.Exists(tempDir))
                {
                    try { Directory.Delete(tempDir, true); } catch {}
                }
            }
        }
    }
}
"@

$csFile = Join-Path $env:TEMP "StandaloneSetup.cs"
Set-Content -Path $csFile -Value $csSource -Encoding UTF8

# 3. Compile DofusFarmSetup.exe with embedded payload & icon
Write-Host "Compiling standalone DofusFarmSetup.exe with embedded payload..." -ForegroundColor Cyan

$cscArgs = @(
  "/target:winexe",
  "/platform:anycpu",
  "/optimize+",
  "/win32icon:$InstallerIco",
  "/resource:$payloadZip,DofusPayload.zip",
  "/reference:System.Windows.Forms.dll",
  "/reference:System.Drawing.dll",
  "/reference:System.IO.Compression.dll",
  "/reference:System.IO.Compression.FileSystem.dll",
  "/out:$TargetExe",
  $csFile
)

& $Csc $cscArgs
if ($LASTEXITCODE -eq 0) {
  Write-Host "  [OK] Successfully compiled: $TargetExe ($((Get-Item $TargetExe).Length) bytes)" -ForegroundColor Green
  # Also copy/overwrite setup.exe so it serves as the same standalone installer
  Copy-Item $TargetExe $SetupExe -Force
  Write-Host "  [OK] Synchronized: $SetupExe ($((Get-Item $SetupExe).Length) bytes)" -ForegroundColor Green
} else {
  Write-Host "  [FAIL] Compilation failed with exit code $LASTEXITCODE" -ForegroundColor Red
}

Remove-Item $csFile -Force -EA SilentlyContinue
Remove-Item $payloadZip -Force -EA SilentlyContinue
