# Standalone Automated Test for Real Dofus Touch Web Client running in WebView2
$ErrorActionPreference = "Stop"

Write-Host "=========================================================="
Write-Host "   DOFUS TOUCH REAL CLIENT IN STANDALONE WEBVIEW2 TEST    "
Write-Host "=========================================================="

$repoRoot = "C:\Users\sao\Documents\dofus-emu"
$farmExe = Join-Path $repoRoot "DofusFarm.exe"
if (-not (Test-Path $farmExe)) {
    Write-Error "DofusFarm.exe not found."
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

$instId = "dofus-wv-test"
$profileDir = Join-Path $env:TEMP "dofus_real_client_wv_profile"
if (Test-Path $profileDir) { Remove-Item -Recurse -Force $profileDir -ErrorAction SilentlyContinue }

$options = New-Object DofusFarm.Engine.ClientLaunchOptions
$options.InstanceId = $instId
$options.DisplayName = "Dofus Touch Standalone Test"
$options.UserDataDirectory = $profileDir
$options.EntryUrl = "https://dofus.local/standalone.html"
$options.Profile = [DofusFarm.Engine.DeviceCatalog]::GetProfile("galaxy-a51")
$options.DisguiseScript = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\mobile-disguise.js"))

Write-Host "`nLaunching Standalone Dofus Touch Client Form..."
$backend.Start($options)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
Write-Host "Running game client for 10 seconds..."
while ($sw.Elapsed.TotalSeconds -lt 10) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

$diag = $backend.GetDiagnostics($instId)
Write-Host "`nLive Diagnostics:"
Write-Host ("  Instance: " + $diag.InstanceId)
Write-Host ("  State: " + $diag.State)
Write-Host ("  PID: " + $diag.ProcessId)
Write-Host ("  Working Set: " + [math]::Round($diag.WorkingSetMb, 1) + " MB")
Write-Host ("  Telemetry: " + $diag.Details)

Write-Host "`nShutting down client..."
$backend.Stop($instId)

$sw.Restart()
while ($sw.Elapsed.TotalSeconds -lt 2) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

$backend.Dispose()

$wvProcs = Get-Process -Name "msedgewebview2" -ErrorAction SilentlyContinue
$lingering = if ($wvProcs) { $wvProcs.Count } else { 0 }
Write-Host ("Lingering msedgewebview2 processes: " + $lingering)

try { Remove-Item -Recurse -Force $profileDir -ErrorAction SilentlyContinue } catch {}

if ($diag.State -eq [DofusFarm.Engine.ClientInstanceState]::Running -and $lingering -eq 0) {
    Write-Host "`n>>> [SUCCESS] Dofus Touch Web Client loaded into standalone WebView2 with 0 leaks! <<<`n"
} else {
    Write-Host "`n>>> [RESULT] Run completed (State: $($diag.State), Lingering: $lingering) <<<`n"
}
