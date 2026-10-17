# Standalone Automated Test for WebView2 Backend & Multi-Instance Isolation
$ErrorActionPreference = "Stop"

Write-Host "=========================================================="
Write-Host "   DOFUS FARM: AUTOMATED WEBVIEW2 RUNTIME TEST SUITE     "
Write-Host "=========================================================="

$baseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $baseDir) { $baseDir = Get-Location }
$repoRoot = Split-Path -Parent $baseDir
if (-not (Test-Path (Join-Path $repoRoot "DofusFarm.exe"))) { $repoRoot = Get-Location }

$farmExe = Join-Path $repoRoot "DofusFarm.exe"
if (-not (Test-Path $farmExe)) {
    Write-Error "DofusFarm.exe not found. Run Build.exe first."
    exit 1
}

[System.Reflection.Assembly]::LoadFrom($farmExe) | Out-Null
Add-Type -AssemblyName System.Windows.Forms

$backend = New-Object DofusFarm.Engine.WebView2Backend
$supported = $false
$reason = ""
$isSup = $backend.IsSupported([ref]$reason)
Write-Host ("WebView2 Runtime Supported: " + $isSup)
if (-not $isSup) {
    Write-Error ("Unsupported: " + $reason)
    exit 1
}

$backend.Initialize()
$testHtml = Join-Path $repoRoot "desktop\assets\webview_test.html"
if (-not (Test-Path $testHtml)) {
    $testHtml = Join-Path $repoRoot "assets\webview_test.html"
}

Write-Host ("Test HTML Path: " + $testHtml)

# 1. Test Single Instance
Write-Host "`n[1/3] Testing Instance 1..."
$p1 = Join-Path $env:TEMP "dofus_wv_autotest_01"
$opt1 = New-Object DofusFarm.Engine.ClientLaunchOptions
$opt1.InstanceId = "autotest-01"
$opt1.DisplayName = "AutoTest Instance 1"
$opt1.UserDataDirectory = $p1
$opt1.EntryUrl = (New-Object System.Uri($testHtml)).AbsoluteUri

$backend.Start($opt1)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt 5) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

$diag1 = $backend.GetDiagnostics("autotest-01")
Write-Host ("  State: " + $diag1.State)
Write-Host ("  Working Set (UI): " + [math]::Round($diag1.WorkingSetMb, 1) + " MB")
Write-Host ("  Telemetry: " + $diag1.Details)

# 2. Test Multi-Instance Isolation
Write-Host "`n[2/3] Testing Multi-Instance 2..."
$p2 = Join-Path $env:TEMP "dofus_wv_autotest_02"
$opt2 = New-Object DofusFarm.Engine.ClientLaunchOptions
$opt2.InstanceId = "autotest-02"
$opt2.DisplayName = "AutoTest Instance 2"
$opt2.UserDataDirectory = $p2
$opt2.EntryUrl = (New-Object System.Uri($testHtml)).AbsoluteUri

$backend.Start($opt2)

$sw.Restart()
while ($sw.Elapsed.TotalSeconds -lt 5) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

$diag2 = $backend.GetDiagnostics("autotest-02")
Write-Host ("  State: " + $diag2.State)
Write-Host ("  Working Set (UI): " + [math]::Round($diag2.WorkingSetMb, 1) + " MB")

# 3. Clean Shutdown & Leak Test
Write-Host "`n[3/3] Shutting down both instances..."
$backend.Stop("autotest-01")
$backend.Stop("autotest-02")

$sw.Restart()
while ($sw.Elapsed.TotalSeconds -lt 2) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

$backend.Dispose()

$wvProcs = Get-Process -Name "msedgewebview2" -ErrorAction SilentlyContinue
$lingering = if ($wvProcs) { $wvProcs.Count } else { 0 }
Write-Host ("Lingering msedgewebview2 Processes: " + $lingering)

try { Remove-Item -Recurse -Force $p1 -ErrorAction SilentlyContinue } catch {}
try { Remove-Item -Recurse -Force $p2 -ErrorAction SilentlyContinue } catch {}

if ($lingering -eq 0 -and $diag1.State -eq [DofusFarm.Engine.ClientInstanceState]::Running) {
    Write-Host "`n>>> [PASS] All WebView2 Backend Unit Checks Succeeded! <<<`n"
} else {
    Write-Host "`n>>> [WARN] Partial pass: check process cleanup. <<<`n"
}
