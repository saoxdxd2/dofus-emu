param(
    [string]$Slot = "dofus-05",
    [int]$Ram = 1024,
    [int]$Cores = 1
)

Write-Host "=========================================================="
Write-Host "   LIVE TEST: PROVISIONED INSTANCE ($Slot)               "
Write-Host "=========================================================="

[System.Reflection.Assembly]::LoadFrom((Resolve-Path ".\DofusFarm.exe").Path) | Out-Null
$engine = New-Object DofusFarm.Engine.FarmEngine

$engine.add_OnLog([Action[string]]{ param($msg) Write-Host "[ENGINE] $msg" })

$idx = [int]$Slot.Substring(6)
$port = 5554 + 2 * ($idx - 1)
$serial = "emulator-$port"

Write-Host "Target Serial: $serial (Port $port)"
$avdDir = Join-Path $engine.AvdHome "$Slot.avd"

# Delete cold_run_completed.flag to verify fresh pass
$coldFlag = Join-Path $avdDir "cold_run_completed.flag"
if (Test-Path $coldFlag) { Remove-Item $coldFlag -Force }

Write-Host "Launching instance $Slot via FarmEngine..."
$sw = [System.Diagnostics.Stopwatch]::StartNew()

$engine.LaunchInstance($Slot, $port, $Ram, $Cores, "")

$adb = $engine.AdbPath

# 1. Wait for boot completed
$bootCompleted = $false
$pollSw = [System.Diagnostics.Stopwatch]::StartNew()
while ($pollSw.Elapsed.TotalSeconds -lt 90) {
    $res = & $adb -s $serial shell getprop sys.boot_completed 2>$null
    if ($res -and $res.Trim() -eq "1") {
        $bootCompleted = $true
        break
    }
    Start-Sleep -Milliseconds 500
}

Write-Host "`nBoot Completed: $bootCompleted in $($pollSw.Elapsed.TotalSeconds.ToString('F1'))s"

# 2. Wait for guest configuration pass (cold_run_completed.flag written)
$configSw = [System.Diagnostics.Stopwatch]::StartNew()
while ($configSw.Elapsed.TotalSeconds -lt 30) {
    if (Test-Path $coldFlag) {
        Write-Host "Guest configuration pass completed in $($configSw.Elapsed.TotalSeconds.ToString('F1'))s"
        break
    }
    Start-Sleep -Milliseconds 500
}

Start-Sleep -Seconds 2

Write-Host "`n--- LIVE VERIFICATION CHECKS ---"

# 1. CPU Cores check
$cpuinfo = & $adb -s $serial shell "grep -c processor /proc/cpuinfo"
Write-Host "Active vCPUs in guest: $($cpuinfo.Trim()) (Target: 1)"

# 2. Memory check
$meminfo = & $adb -s $serial shell "grep -E 'MemTotal|MemFree|MemAvailable' /proc/meminfo"
Write-Host "Memory info:`n$meminfo"

# 3. CPU Idle / Load check
$topOut = & $adb -s $serial shell "top -b -n 1 | head -n 8"
Write-Host "Top Output:`n$topOut"

# 4. Check Dofus Touch status
$dofusPid = & $adb -s $serial shell "pidof com.ankama.dofustouch"
Write-Host "Dofus Touch PID: $dofusPid"

# 5. Check sanitize_session.flag (should be removed after sanitization)
$flagExists = Test-Path (Join-Path $avdDir "sanitize_session.flag")
Write-Host "sanitize_session.flag present: $flagExists (Target: False)"

# 6. Check package count disabled
$pkgCheck = & $adb -s $serial shell "pm list packages -d | wc -l"
Write-Host "Disabled bloat packages count: $($pkgCheck.Trim())"

Write-Host "`n--- STOPPING INSTANCE CLEANLY ---"
$engine.StopInstance($serial, $Slot)
Start-Sleep -Seconds 2
Write-Host "Instance stopped. Test finished."
