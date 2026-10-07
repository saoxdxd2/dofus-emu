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
$TargetExe = Join-Path $RepoRoot 'setup.exe'

if (-not (Test-Path $InstallerIco)) {
  & (Join-Path $PSScriptRoot 'build-installer-icon.ps1')
}

Add-Type -AssemblyName System.IO.Compression.FileSystem

# 0. Seal Code Integrity Hashes
Write-Host "Generating SHA-256 Code Integrity Manifest..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot 'generate-integrity-manifest.ps1')

# 1. Prepare Compressed Payload Archive of all scripts and assets
Write-Host "Packaging installer payload archive..." -ForegroundColor Cyan
$payloadZip = Join-Path $env:TEMP "DofusPayload.zip"
$stageDir = Join-Path $env:TEMP "dofus_stage_$([System.IO.Path]::GetRandomFileName())"
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
Copy-Item (Join-Path $RepoRoot "scripts") (Join-Path $stageDir "scripts") -Recurse -Force
Copy-Item (Join-Path $RepoRoot "install.ps1") $stageDir -Force -EA SilentlyContinue
Copy-Item (Join-Path $RepoRoot "INSTALL.bat") $stageDir -Force -EA SilentlyContinue
Copy-Item (Join-Path $RepoRoot "installer_icon.ico") $stageDir -Force -EA SilentlyContinue
Copy-Item (Join-Path $RepoRoot "app_icon.ico") $stageDir -Force -EA SilentlyContinue
Copy-Item (Join-Path $RepoRoot "DofusFarm.exe") $stageDir -Force -EA SilentlyContinue
Copy-Item (Join-Path $RepoRoot "uninstall.exe") $stageDir -Force -EA SilentlyContinue
Copy-Item (Join-Path $RepoRoot "setup.bat") $stageDir -Force -EA SilentlyContinue

# Include game APK bundle
if (Test-Path (Join-Path $RepoRoot "apks")) {
  Write-Host "Packaging game APK bundle..." -ForegroundColor Gray
  Copy-Item (Join-Path $RepoRoot "apks") (Join-Path $stageDir "apks") -Recurse -Force
}

# Include Golden Master Template (pre-installed, pre-trimmed) if available
$repoTpl = Join-Path $RepoRoot 'template.zip'
$avdTpl = Join-Path $env:USERPROFILE '.android\avd\dofus-template.avd'
if (Test-Path $repoTpl) {
  Write-Host "Packaging pre-configured Golden Master Template from repo (~40MB)..." -ForegroundColor Gray
  Copy-Item $repoTpl (Join-Path $stageDir "template.zip") -Force
} elseif (Test-Path $avdTpl) {
  Write-Host "Packaging pre-configured Golden Master Template (~40MB)..." -ForegroundColor Gray
  Compress-Archive -Path "$avdTpl\*" -DestinationPath (Join-Path $stageDir "template.zip") -Force
}

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

[assembly: AssemblyTitle("Dofus Farm Setup")]
[assembly: AssemblyDescription("Dofus Touch Virtualization Farm Standalone Setup Wizard")]
[assembly: AssemblyCompany("Dofus Farm Project")]
[assembly: AssemblyProduct("Dofus Farm Virtualization Setup")]
[assembly: AssemblyCopyright("Copyright © 2026")]
[assembly: AssemblyFileVersion("1.0.0.0")]
[assembly: AssemblyVersion("1.0.0.0")]

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
                psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File \"" + scriptPath + "\"";
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;
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

$manifestSource = @"
<?xml version="1.0" encoding="utf-8"?>
<assembly manifestVersion="1.0" xmlns="urn:schemas-microsoft-com:asm.v1">
  <assemblyIdentity version="1.0.0.0" name="DofusFarmSetup"/>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges xmlns="urn:schemas-microsoft-com:asm.v3">
        <requestedExecutionLevel level="requireAdministrator" uiAccess="false" />
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <!-- Windows 10 and Windows 11 -->
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}" />
    </application>
  </compatibility>
</assembly>
"@

$csFile = Join-Path $env:TEMP "StandaloneSetup.cs"
$manifestFile = Join-Path $env:TEMP "StandaloneSetup.manifest"
Set-Content -Path $csFile -Value $csSource -Encoding UTF8
Set-Content -Path $manifestFile -Value $manifestSource -Encoding UTF8

# 3. Compile DofusFarmSetup.exe with embedded payload & icon & UAC manifest
Write-Host "Compiling standalone DofusFarmSetup.exe with embedded payload & UAC manifest..." -ForegroundColor Cyan

$cscArgs = @(
  "/target:winexe",
  "/platform:anycpu",
  "/optimize+",
  "/win32icon:$InstallerIco",
  "/win32manifest:$manifestFile",
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
  Write-Host "  [OK] Successfully compiled standalone installer: $TargetExe ($([math]::Round((Get-Item $TargetExe).Length/1MB, 1)) MB)" -ForegroundColor Green
} else {
  Write-Host "  [FAIL] Compilation failed with exit code $LASTEXITCODE" -ForegroundColor Red
}

Remove-Item $csFile -Force -EA SilentlyContinue
Remove-Item $manifestFile -Force -EA SilentlyContinue
Remove-Item $payloadZip -Force -EA SilentlyContinue
