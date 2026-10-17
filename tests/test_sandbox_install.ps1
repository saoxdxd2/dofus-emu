$ErrorActionPreference = "Stop"
$testDir = Join-Path $env:TEMP "dofus_sandbox_test"
if (Test-Path $testDir) {
    Remove-Item -Recurse -Force $testDir
}

Write-Host "Testing standalone setup.exe silent install to: $testDir"
$setupPath = Join-Path (Get-Location) "setup.exe"
$proc = Start-Process -FilePath $setupPath -ArgumentList "/silent", "/target", "`"$testDir`"" -PassThru -Wait
Write-Host "Setup Process Exit Code: $($proc.ExitCode)"

if ($proc.ExitCode -ne 0) {
    Write-Error "Setup failed with exit code $($proc.ExitCode)"
}

Write-Host "`nDeployed Files in Sandbox:"
Get-ChildItem -Recurse $testDir | Select-Object -First 30 FullName | ForEach-Object { Write-Host " - $($_.FullName)" }

# Verify required files
$required = @(
    "DofusFarm.exe",
    "uninstall.exe",
    "dofus-net-proxy.exe",
    "Microsoft.Web.WebView2.Core.dll",
    "Microsoft.Web.WebView2.WinForms.dll",
    "WebView2Loader.dll",
    "client\www\standalone.html",
    "client\www\build\script.js",
    "client\www\build\styles-native.css"
)

$missing = @()
foreach ($req in $required) {
    $p = Join-Path $testDir $req
    if (-not (Test-Path $p)) {
        $missing += $req
    }
}

if ($missing.Count -gt 0) {
    Write-Error "Missing deployed files: $($missing -join ', ')"
} else {
    Write-Host "`n[SUCCESS] All standalone files deployed cleanly!"
}

# Clean up sandbox
Remove-Item -Recurse -Force $testDir
Write-Host "Sandbox cleaned up successfully."
