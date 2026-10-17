$emu = (Resolve-Path "sdk\emulator\emulator.exe").Path
$adb = (Resolve-Path "sdk\platform-tools\adb.exe").Path

Write-Host "=========================================================="
Write-Host "   TESTING MICRO-ANDROID BLACKBOX MODE (HEADLESS)        "
Write-Host "=========================================================="

# Clean locks
$avdDir = Join-Path $env:USERPROFILE ".android\avd\dofus-05.avd"
Remove-Item (Join-Path $avdDir "*.lock") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $avdDir "hardware-qemu.ini") -Force -ErrorAction SilentlyContinue

$argsList = "-avd dofus-05 -port 5562 -gpu swiftshader_indirect -no-window -no-skin -no-snapshot-load -no-boot-anim -no-audio -accel on -skip-adb-auth -no-metrics -no-passive-gps -prop qemu.hw.mainkeys=1 -qemu -smp 1 -m 1024"

Write-Host "Launching Headless Blackbox: emulator.exe $argsList"
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $emu -ArgumentList $argsList -PassThru

Write-Host "Waiting for device on emulator-5562..."
& $adb -s emulator-5562 wait-for-device

while ($sw.Elapsed.TotalSeconds -lt 90) {
    $res = & $adb -s emulator-5562 shell getprop sys.boot_completed
    if ($res -and $res.Trim() -eq "1") { break }
    Start-Sleep -Seconds 1
}

Write-Host "Blackbox Boot Completed in $($sw.Elapsed.TotalSeconds.ToString('F1'))s"

# Quick setup & start Dofus Touch
& $adb -s emulator-5562 shell "settings put global window_animation_scale 0; settings put global transition_animation_scale 0; settings put global animator_duration_scale 0; input keyevent 224; wm dismiss-keyguard; am start -n com.ankama.dofustouch/.MainActivity"

Start-Sleep -Seconds 5

# Check PID
$dofusPid = & $adb -s emulator-5562 shell "pidof com.ankama.dofustouch"
Write-Host "Dofus Touch PID inside Blackbox: $dofusPid"

# Check CPU & RAM
$meminfo = & $adb -s emulator-5562 shell "grep -E 'MemTotal|MemFree|MemAvailable' /proc/meminfo"
Write-Host "Guest Memory:`n$meminfo"

$topOut = & $adb -s emulator-5562 shell "top -b -n 1 | head -n 8"
Write-Host "Guest Top:`n$topOut"

# Check CDP remote devtools socket
$sockets = & $adb -s emulator-5562 shell "cat /proc/net/unix | grep devtools"
Write-Host "CDP Sockets:`n$sockets"

# Forward CDP port to 9562
& $adb -s emulator-5562 forward tcp:9562 localabstract:webview_devtools_remote_$($dofusPid.Trim())

try {
    $json = (Invoke-WebRequest -Uri "http://127.0.0.1:9562/json" -TimeoutSec 5).Content
    Write-Host "CDP Devtools Targets Available:"
    Write-Host $json
} catch {
    Write-Host "CDP HTTP probe: $($_.Exception.Message)"
}

Write-Host "`nStopping Headless Blackbox..."
& $adb -s emulator-5562 emu kill
Start-Sleep -Seconds 2
Write-Host "Done!"
