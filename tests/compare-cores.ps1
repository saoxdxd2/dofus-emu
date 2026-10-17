$emu = (Resolve-Path "sdk\emulator\emulator.exe").Path
$adb = (Resolve-Path "sdk\platform-tools\adb.exe").Path

# Test 1: With -cores 1 -qemu -smp 1
Write-Host "--- TEST 1: -cores 1 -qemu -smp 1 ---"
$proc1 = Start-Process -FilePath $emu -ArgumentList "-avd dofus-05 -port 5566 -gpu host -no-snapshot-load -no-boot-anim -no-audio -accel on -skip-adb-auth -no-metrics -no-passive-gps -cores 1 -memory 1024 -qemu -smp 1 -m 1024" -PassThru
& $adb -s emulator-5566 wait-for-device
Start-Sleep -Seconds 5
$c1 = & $adb -s emulator-5566 shell "grep -c processor /proc/cpuinfo"
Write-Host "Test 1 processors: $($c1.Trim())"
& $adb -s emulator-5566 emu kill
Start-Sleep -Seconds 3

# Test 2: Only -qemu -smp 1 (without -cores)
Write-Host "`n--- TEST 2: Only -qemu -smp 1 ---"
$proc2 = Start-Process -FilePath $emu -ArgumentList "-avd dofus-05 -port 5568 -gpu host -no-snapshot-load -no-boot-anim -no-audio -accel on -skip-adb-auth -no-metrics -no-passive-gps -qemu -smp 1 -m 1024" -PassThru
& $adb -s emulator-5568 wait-for-device
Start-Sleep -Seconds 5
$c2 = & $adb -s emulator-5568 shell "grep -c processor /proc/cpuinfo"
Write-Host "Test 2 processors: $($c2.Trim())"
& $adb -s emulator-5568 emu kill
Start-Sleep -Seconds 3

# Test 3: Only -cores 1 (without -qemu)
Write-Host "`n--- TEST 3: Only -cores 1 ---"
$proc3 = Start-Process -FilePath $emu -ArgumentList "-avd dofus-05 -port 5570 -gpu host -no-snapshot-load -no-boot-anim -no-audio -accel on -skip-adb-auth -no-metrics -no-passive-gps -cores 1 -memory 1024" -PassThru
& $adb -s emulator-5570 wait-for-device
Start-Sleep -Seconds 5
$c3 = & $adb -s emulator-5570 shell "grep -c processor /proc/cpuinfo"
Write-Host "Test 3 processors: $($c3.Trim())"
& $adb -s emulator-5570 emu kill
Start-Sleep -Seconds 2
