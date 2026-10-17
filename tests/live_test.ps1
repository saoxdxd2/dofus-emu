# Comprehensive Live Test for Standalone Dofus Touch WebView2 Client
$ErrorActionPreference = "Stop"

Write-Host "=========================================================="
Write-Host "   DOFUS TOUCH LIVE COMPREHENSIVE BENCHMARK & RUNTIME TEST"
Write-Host "=========================================================="

$repoRoot = "C:\Users\sao\Documents\dofus-emu"
$farmExe = Join-Path $repoRoot "DofusFarm.exe"
if (-not (Test-Path $farmExe)) {
    Write-Error "DofusFarm.exe not found."
    exit 1
}

[System.Reflection.Assembly]::LoadFrom($farmExe) | Out-Null
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$backend = New-Object DofusFarm.Engine.WebView2Backend
$reason = ""
$isSup = $backend.IsSupported([ref]$reason)
Write-Host "1. Runtime Compatibility: Supported = $isSup"
if (-not $isSup) {
    Write-Error "WebView2 not supported: $reason"
    exit 1
}

$backend.Initialize()

$instId = "live-test-01"
$profileDir = Join-Path $env:TEMP "dofus_live_test_profile"
if (Test-Path $profileDir) { Remove-Item -Recurse -Force $profileDir -ErrorAction SilentlyContinue }

$options = New-Object DofusFarm.Engine.ClientLaunchOptions
$options.InstanceId = $instId
$options.DisplayName = "Live Test Game Client"
$options.UserDataDirectory = $profileDir
$options.EntryUrl = "https://dofus.local/standalone.html"
$options.Profile = [DofusFarm.Engine.DeviceCatalog]::GetProfile("galaxy-a51")
$options.DisguiseScript = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\mobile-disguise.js"))

Write-Host "2. Launching Standalone Game Client..."
$backend.Start($options)

# Pump UI events and wait for WebView2 initialization
$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt 4) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

$form = $backend.GetForm($instId)
if ($null -eq $form) {
    Write-Error "Failed to obtain client form handle."
    exit 1
}

Write-Host "3. Querying In-Engine JavaScript & WebGL State..."
# Evaluate script inside the live DOM
$scriptTask = $form.ExecuteScriptAsync(@"
(function() {
    var gl;
    try {
        var c = document.createElement('canvas');
        gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    } catch(e) {}
    var ext = gl ? gl.getExtension('WEBGL_debug_renderer_info') : null;
    return JSON.stringify({
        url: window.location.href,
        title: document.title,
        readyState: document.readyState,
        device: window.device || null,
        appInfo: window.navigator ? window.navigator.appInfo : null,
        platform: navigator.platform,
        userAgent: navigator.userAgent,
        vendor: gl ? gl.getParameter(gl.VENDOR) : null,
        renderer: gl ? gl.getParameter(gl.RENDERER) : null,
        unmaskedVendor: (gl && ext) ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null,
        unmaskedRenderer: (gl && ext) ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
        maxTextureSize: gl ? gl.getParameter(gl.MAX_TEXTURE_SIZE) : 0,
        hasGui: !!window.gui,
        domLength: document.body ? document.body.innerHTML.length : 0,
        scriptsLoaded: document.getElementsByTagName('script').length,
        hasAnkamaRoot: !!document.getElementById('root') || !!document.querySelector('.app') || !!document.body
    });
})();
"@)

# Wait for script task to resolve while pumping UI loop
while (-not $scriptTask.IsCompleted) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 25
}

$jsResultRaw = $scriptTask.Result
$jsResult = $jsResultRaw | ConvertFrom-Json | ConvertFrom-Json

Write-Host "   - Current URL:          $($jsResult.url)"
Write-Host "   - Document Title:       $($jsResult.title)"
Write-Host "   - Document ReadyState:  $($jsResult.readyState)"
Write-Host "   - Engine GUI Initialized: $($jsResult.hasGui)"
Write-Host "   - Game DOM Tree Size:   $($jsResult.domLength) chars"
Write-Host "   - Scripts in DOM:       $($jsResult.scriptsLoaded)"
Write-Host "   - Device Model:         $($jsResult.device.model) ($($jsResult.device.platform) $($jsResult.device.version))"
Write-Host "   - Platform:             $($jsResult.platform)"
Write-Host "   - WebGL GPU Vendor:     $($jsResult.vendor) (Unmasked: $($jsResult.unmaskedVendor))"
Write-Host "   - WebGL GPU Renderer:   $($jsResult.renderer) (Unmasked: $($jsResult.unmaskedRenderer))"
Write-Host "   - Max Texture Size:     $($jsResult.maxTextureSize)"

Write-Host "`n4. Measuring Real-Time Performance Over Sustained Run (5 seconds)..."
$samples = @()
$browserPid = $form.BrowserProcessId
$mainPid = [System.Diagnostics.Process]::GetCurrentProcess().Id

$sw.Restart()
while ($sw.Elapsed.TotalSeconds -lt 5) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 500

    $allWv = Get-Process -Name "msedgewebview2" -ErrorAction SilentlyContinue
    $totalWvRamMb = 0
    if ($allWv) {
        $totalWvRamMb = ($allWv | Measure-Object -Property WorkingSet64 -Sum).Sum / 1MB
    }
    $mainProc = Get-Process -Id $mainPid
    $mainRamMb = $mainProc.WorkingSet64 / 1MB

    $samples += [PSCustomObject]@{
        ElapsedSec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        HostRamMb = [math]::Round($mainRamMb, 1)
        WebView2RamMb = [math]::Round($totalWvRamMb, 1)
        TotalRamMb = [math]::Round($mainRamMb + $totalWvRamMb, 1)
    }
}

$latest = $samples[-1]
Write-Host "   - Host Manager RAM:     $($latest.HostRamMb) MB"
Write-Host "   - WebView2 Runtime RAM: $($latest.WebView2RamMb) MB"
Write-Host "   - Total Client Footprint: $($latest.TotalRamMb) MB (vs 1,200 MB for Android emulator!)"
Write-Host "   - Efficiency Gain:      $([math]::Round((1 - ($latest.TotalRamMb / 1200)) * 100, 1))% RAM reduction"

Write-Host "`n5. Capturing Live Client Screenshot..."
$artifactDir = "C:\Users\sao\.gemini\antigravity\brain\c949f713-df27-4cee-a43f-ff3a883d0c89"
$screenshotPath = Join-Path $artifactDir "live_client_screen.png"

try {
    $shotTask = $form.CaptureScreenshotAsync($screenshotPath)
    while (-not $shotTask.IsCompleted) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 25
    }
    if (Test-Path $screenshotPath) {
        $imgSize = (Get-Item $screenshotPath).Length
        Write-Host "   - Captured direct compositor screenshot ($imgSize bytes): $screenshotPath"
    }
} catch {
    Write-Host "   - Screenshot capture note: $($_.Exception.Message)"
}

Write-Host "`n6. Graceful Shutdown & Cleanup..."
$backend.Stop($instId)

$sw.Restart()
while ($sw.Elapsed.TotalSeconds -lt 2) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

$backend.Dispose()

$lingering = (Get-Process -Name "msedgewebview2" -ErrorAction SilentlyContinue).Count
Write-Host "   - Lingering msedgewebview2 processes: $lingering"
if (Test-Path $profileDir) { Remove-Item -Recurse -Force $profileDir -ErrorAction SilentlyContinue }

Write-Host "`n=========================================================="
if ($lingering -eq 0 -and $jsResult.vendor -eq "ARM" -and $jsResult.platform -eq "Linux armv8l") {
    Write-Host "   >>> [PASS] LIVE TEST COMPLETED SUCCESSFULLY! <<<"
} else {
    Write-Host "   >>> [WARNING] Review test findings above <<<"
}
Write-Host "=========================================================="
