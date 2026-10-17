$emu = (Resolve-Path "sdk\emulator\emulator.exe").Path
$adb = (Resolve-Path "sdk\platform-tools\adb.exe").Path

$argsList = "-avd dofus-05 -port 5564 -gpu host -no-snapshot-load -no-boot-anim -no-audio -accel on -skip-adb-auth -no-metrics -no-passive-gps -qemu -smp 1 -m 1024"

Write-Host "Starting emulator with args: $argsList"
$proc = Start-Process -FilePath $emu -ArgumentList $argsList -PassThru

Write-Host "Waiting for device on 5564..."
& $adb -s emulator-5564 wait-for-device

$sw = [System.Diagnostics.Stopwatch]::StartNew()
while ($sw.Elapsed.TotalSeconds -lt 40) {
    $done = & $adb -s emulator-5564 shell getprop sys.boot_completed
    if ($done -and $done.Trim() -eq "1") { break }
    Start-Sleep -Seconds 1
}

$c = & $adb -s emulator-5564 shell "grep -c processor /proc/cpuinfo"
$m = & $adb -s emulator-5564 shell "grep MemTotal /proc/meminfo"

& $adb -s emulator-5564 emu kill
Start-Sleep -Seconds 2

Write-Host "Result processors: $($c.Trim())"
Write-Host "Result MemTotal: $($m.Trim())"
