[System.Reflection.Assembly]::LoadFrom((Resolve-Path ".\DofusFarm.exe").Path) | Out-Null
$engine = New-Object DofusFarm.Engine.FarmEngine

Write-Host "=========================================================="
Write-Host "   DOFUS FARM MULTI-INSTANCE CONCURRENCY & DYNAMIC RAM    "
Write-Host "=========================================================="
Write-Host ("AvdHome: " + $engine.AvdHome)
Write-Host ("Golden Master: " + $engine.GoldenMasterPath)
Write-Host ("Golden Overlay: " + $engine.GoldenOverlayPath)
Write-Host ("Dynamic RAM (requested 1024): " + $engine.GetDynamicRamMb(1024) + " MB")
Write-Host ("Dynamic RAM (requested 2048): " + $engine.GetDynamicRamMb(2048) + " MB")
Write-Host ("Dynamic RAM (requested 0):    " + $engine.GetDynamicRamMb(0) + " MB")
Write-Host ("Dynamic Cores:               " + $engine.GetDynamicCores())
Write-Host ("Recommended Instances:       " + $engine.RecommendedInstanceCount())
Write-Host "----------------------------------------------------------"

# Ensure clean slate
Write-Host "Ensuring clean slate..."
$engine.StopInstance("emulator-5554", "dofus-01")
$engine.StopInstance("emulator-5556", "dofus-02")
Start-Sleep -Seconds 2

# Launch dofus-01 and dofus-02 concurrently
Write-Host "Launching dofus-01 (port 5554, 1024 MB, 1 vCPU)..."
$engine.LaunchInstance("dofus-01", 5554, 1024, 1, "DIRECT")

Start-Sleep -Seconds 2

Write-Host "Launching dofus-02 (port 5556, 1024 MB, 1 vCPU)..."
$engine.LaunchInstance("dofus-02", 5556, 1024, 1, "DIRECT")

Write-Host "Waiting for instances to boot and achieve ADB readiness..."
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$ready01 = $false
$ready02 = $false
$t01 = 0
$t02 = 0

$adbPath = Join-Path $engine.SdkRoot "platform-tools\adb.exe"

while ($sw.Elapsed.TotalSeconds -lt 150 -and (-not $ready01 -or -not $ready02)) {
    if (-not $ready01) {
        $state01 = ((& $adbPath -s emulator-5554 get-state 2>$null) | Out-String).Trim()
        if ($state01 -eq "device") {
            $boot01 = ((& $adbPath -s emulator-5554 shell "getprop sys.boot_completed" 2>$null) | Out-String).Trim()
            if ($boot01 -eq "1") {
                $ready01 = $true
                $t01 = [math]::Round($sw.Elapsed.TotalSeconds, 1)
                Write-Host (" [OK] dofus-01 fully booted (sys.boot_completed=1) at " + $t01 + "s")
            }
        }
    }
    if (-not $ready02) {
        $state02 = ((& $adbPath -s emulator-5556 get-state 2>$null) | Out-String).Trim()
        if ($state02 -eq "device") {
            $boot02 = ((& $adbPath -s emulator-5556 shell "getprop sys.boot_completed" 2>$null) | Out-String).Trim()
            if ($boot02 -eq "1") {
                $ready02 = $true
                $t02 = [math]::Round($sw.Elapsed.TotalSeconds, 1)
                Write-Host (" [OK] dofus-02 fully booted (sys.boot_completed=1) at " + $t02 + "s")
            }
        }
    }
    Start-Sleep -Seconds 2
}

if (-not $ready01 -or -not $ready02) {
    Write-Host ("Warning: Boot timeout reached after 150s (ready01=" + $ready01 + ", ready02=" + $ready02 + ")")
}

Write-Host "`nWaiting 10s for guest background stabilization & game launch..."
Start-Sleep -Seconds 10

Write-Host "`n=========================================================="
Write-Host "               CONCURRENCY BENCHMARK RESULTS              "
Write-Host "=========================================================="

foreach ($inst in @(@{Name="dofus-01"; Port=5554; Serial="emulator-5554"; BootTime=$t01},
                    @{Name="dofus-02"; Port=5556; Serial="emulator-5556"; BootTime=$t02})) {
    Write-Host ("--- INSTANCE: " + $inst.Name + " (" + $inst.Serial + ") ---")
    Write-Host ("  ADB Device Ready: " + $inst.BootTime + "s")
    
    # Read guest MemTotal
    $meminfo = ((& $adbPath -s $inst.Serial shell "cat /proc/meminfo" 2>&1) | Out-String).Trim()
    $memTotal = 0
    if ($meminfo -match "MemTotal:\s+(\d+)\s+kB") {
        $memTotal = [math]::Round([int64]$Matches[1] / 1024, 0)
    }
    Write-Host ("  Guest Reported MemTotal: " + $memTotal + " MB (Expected ~1024 MB)")
    
    # Read guest CPU count
    $cpuOnline = ((& $adbPath -s $inst.Serial shell "cat /sys/devices/system/cpu/online" 2>&1) | Out-String).Trim()
    $cpuGrep = ((& $adbPath -s $inst.Serial shell "grep -c processor /proc/cpuinfo" 2>&1) | Out-String).Trim()
    Write-Host ("  Guest CPU Online:        " + $cpuOnline + " | Grep count: " + $cpuGrep + " (Expected 1)")

    # Read running package
    $fgApp = ((& $adbPath -s $inst.Serial shell "dumpsys activity activities | grep -E 'mResumedActivity|topResumedActivity'" 2>&1) | Out-String).Trim()
    $hasDofus = $fgApp -match "com\.ankama\.dofustouch"
    Write-Host ("  Dofus Touch Foreground:  " + ($(if ($hasDofus) { "[PASS] Active" } else { "[PENDING/BACKGROUND]" })))
}

Write-Host "----------------------------------------------------------"
Write-Host "HOST RESOURCE & SCHEDULING (QEMU processes):"
$qemuProcs = Get-Process -Name "qemu-system-x86_64" -ErrorAction SilentlyContinue
foreach ($p in $qemuProcs) {
    $wsMB = [math]::Round($p.WorkingSet64 / 1MB, 1)
    $pmMB = [math]::Round($p.PrivateMemorySize64 / 1MB, 1)
    $pri = $p.PriorityClass
    $cim = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $p.Id) -ErrorAction SilentlyContinue
    $cmd = if ($cim) { $cim.CommandLine } else { "N/A" }
    Write-Host ("  PID " + $p.Id + ": WorkingSet = " + $wsMB + " MB | Private = " + $pmMB + " MB | Priority = " + $pri)
    Write-Host ("    CLI: " + $cmd)
}

Write-Host "`nStopping instances..."
$engine.StopInstance("emulator-5554", "dofus-01")
$engine.StopInstance("emulator-5556", "dofus-02")
Start-Sleep -Seconds 3
Write-Host "Test completed cleanly."
