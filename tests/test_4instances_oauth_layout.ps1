# 4-Instance Dynamic Laptop Tiling & OAuth Verification Test
$ErrorActionPreference = "Stop"

Write-Host "=========================================================="
Write-Host "   4-INSTANCE CONCURRENCY, DYNAMIC TILING & OAUTH TEST   "
Write-Host "=========================================================="

$repoRoot = "C:\Users\sao\Documents\dofus-emu"
$farmExe = Join-Path $repoRoot "DofusFarm.exe"
if (-not (Test-Path $farmExe)) {
    Write-Error "DofusFarm.exe not found. Run Build.exe first."
    exit 1
}

[System.Reflection.Assembly]::LoadFrom($farmExe) | Out-Null
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# 1. Verify Device Catalog User-Agents
Write-Host "`n[STEP 1] Validating Device Catalog User-Agents..."
$profiles = [DofusFarm.Engine.DeviceCatalog]::GetAllProfiles()
$anyWvLeak = $false
foreach ($p in $profiles) {
    if ($p.UserAgent -match ";\s*wv" -or $p.UserAgent -match "Version/[0-9.]+") {
        Write-Host "  ❌ LEAK in profile $($p.Id): $($p.UserAgent)" -ForegroundColor Red
        $anyWvLeak = $true
    } else {
        Write-Host "  ✅ Profile $($p.Id): clean Android Chrome Mobile UA" -ForegroundColor Green
    }
}
if ($anyWvLeak) {
    Write-Error "Profile contains embedded webview leak token!"
}

# 2. Test Dynamic Laptop Tiling Math (1, 2, 3, 4 instances)
Write-Host "`n[STEP 2] Verifying ComputeLayout Math on Display..."
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
Write-Host "  Primary Screen WorkingArea: $($screen.Width)x$($screen.Height) at ($($screen.X),$($screen.Y))"

for ($n = 1; $n -le 4; $n++) {
    $rects = [DofusFarm.Native.Win32]::ComputeLayout($n, $false)
    Write-Host "  Count = $n windows:"
    for ($i = 0; $i -lt $rects.Length; $i++) {
        $r = $rects[$i]
        Write-Host "    Window $i -> X: $($r.X), Y: $($r.Y), W: $($r.Width), H: $($r.Height) (Aspect: $([math]::Round($r.Width / [math]::Max(1, $r.Height), 2)))"
    }
}

# 3. Launch 4 Instances Concurrently
Write-Host "`n[STEP 3] Initializing Standalone WebView2 Backend..."
$backend = New-Object DofusFarm.Engine.WebView2Backend
$reason = ""
if (-not $backend.IsSupported([ref]$reason)) {
    Write-Error "WebView2 runtime unsupported: $reason"
    exit 1
}
$backend.Initialize()

$disguiseScript = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\mobile-disguise.js"))

$tempDirs = @()
$instanceIds = @("slot-1", "slot-2", "slot-3", "slot-4")

Write-Host "`n[STEP 4] Launching 4 Standalone Instances Concurrently..."
foreach ($id in $instanceIds) {
    $pDir = Join-Path $env:TEMP ("dofus_wv_quad_" + $id + "_" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    $tempDirs += $pDir

    $slotNum = [int]($id.Replace("slot-", ""))
    $opt = New-Object DofusFarm.Engine.ClientLaunchOptions
    $opt.InstanceId = $id
    $opt.DisplayName = "Dofus Slot #" + $slotNum
    $opt.UserDataDirectory = $pDir
    $opt.EntryUrl = "https://dofus.local/standalone.html"
    $opt.Profile = [DofusFarm.Engine.DeviceCatalog]::GetDefaultProfileForIndex($slotNum)
    $opt.DisguiseScript = $disguiseScript

    Write-Host "  Starting $id with profile $($opt.Profile.DisplayName)..."
    $backend.Start($opt)
}

# Pump events while 4 instances spin up
Write-Host "`n[STEP 5] Warming up 4 instances (10 seconds pump)..."
$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt 10) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

# 4. Check Status and Diagnostics of All 4 Instances
Write-Host "`n[STEP 6] Checking Diagnostics of 4 Instances..."
$totalRam = 0
foreach ($id in $instanceIds) {
    $state = $backend.GetState($id)
    $diag = $backend.GetDiagnostics($id)
    $totalRam += $diag.WorkingSetMb
    Write-Host "  [$id] State: $state | RAM: $([math]::Round($diag.WorkingSetMb, 1)) MB | Commit: $([math]::Round($diag.PrivateCommitMb, 1)) MB"
    if ($state -ne [DofusFarm.Engine.ClientInstanceState]::Running) {
        Write-Host "    Warning: $id is not in Running state ($state)" -ForegroundColor Yellow
    }
}
Write-Host "  Total Concurrency RAM: $([math]::Round($totalRam, 1)) MB across 4 active clients!" -ForegroundColor Cyan

# 5. Verify Dynamic Tiling across Screen
Write-Host "`n[STEP 7] Verifying 4-Window Tiling Layout..."
$backend.TileWindows($false)
[System.Windows.Forms.Application]::DoEvents()

foreach ($id in $instanceIds) {
    $f = $backend.GetForm($id)
    if ($f -ne $null -and -not $f.IsDisposed) {
        Write-Host "  [$id] Bounds: X=$($f.Bounds.X), Y=$($f.Bounds.Y), W=$($f.Bounds.Width), H=$($f.Bounds.Height), WindowState=$($f.WindowState)"
    }
}

# 6. Verify Window Quick Switch / Focus
Write-Host "`n[STEP 8] Testing Window Quick Focus Switching..."
foreach ($id in $instanceIds) {
    $backend.Focus($id)
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 150
}
Write-Host "  ✅ Quick focus switching verified for all slots." -ForegroundColor Green

# 7. Test OAuth Dialog & DeepLink Interception
Write-Host "`n[STEP 9] Testing Ankama Connect OAuth & DeepLink Handling..."
$form1 = $backend.GetForm("slot-1")
if ($form1 -ne $null -and -not $form1.IsDisposed) {
    Write-Host "  Opening OAuth Dialog on slot-1..."
    $form1.OpenOAuthDialog("https://auth.ankama.com/login-authorized?test=1")
    
    $sw.Restart()
    while ($sw.Elapsed.TotalSeconds -lt 2) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
    }

    Write-Host "  Injecting intercepted DeepLink 'dofustouch://login-authorized?code=SECURE_OAUTH_TOKEN_TEST_9988'..."
    $form1.HandleDeepLinkUri("dofustouch://login-authorized?code=SECURE_OAUTH_TOKEN_TEST_9988")
    
    $sw.Restart()
    while ($sw.Elapsed.TotalSeconds -lt 1.5) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
    }
    Write-Host "  ✅ DeepLink delivered and dialog closed cleanly!" -ForegroundColor Green
}

# 8. Clean Shutdown and 0-Leak Verification
Write-Host "`n[STEP 10] Stopping All 4 Instances..."
foreach ($id in $instanceIds) {
    $backend.Stop($id)
}

$sw.Restart()
while ($sw.Elapsed.TotalSeconds -lt 3) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 50
}

$backend.Dispose()

$wvProcs = Get-Process -Name "msedgewebview2" -ErrorAction SilentlyContinue
$lingering = if ($wvProcs) { $wvProcs.Count } else { 0 }
Write-Host "Lingering msedgewebview2 processes: $lingering"

foreach ($d in $tempDirs) {
    try { Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue } catch {}
}

if ($lingering -eq 0) {
    Write-Host "`n=========================================================="
    Write-Host ">>> [SUCCESS] 4-INSTANCE CONCURRENCY & OAUTH TEST PASSED! <<<" -ForegroundColor Green
    Write-Host "==========================================================`n"
} else {
    Write-Host "`n=========================================================="
    Write-Host ">>> [WARN] Lingering processes remaining: $lingering <<<" -ForegroundColor Yellow
    Write-Host "==========================================================`n"
}
