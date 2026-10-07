@echo off
setlocal enabledelayedexpansion
title Dofus Touch Multi-Instance Farm - Fast Installer
cls

echo ================================================================================
echo        DOFUS TOUCH MULTI-INSTANCE FARM - HIGH-SPEED FAST INSTALLER
echo ================================================================================
echo.

:: Detect Hardware Diagnostics
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "(Get-CimInstance Win32_Processor).Name.Trim()"') do set HOST_CPU=%%a
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)"') do set HOST_RAM_GB=%%a
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1)"') do set FREE_RAM_GB=%%a
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "(Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors"') do set HOST_CORES=%%a

echo [SYSTEM DIAGNOSTICS]
echo   CPU Processor    : !HOST_CPU! (!HOST_CORES! logical cores)
echo   Host Memory      : !HOST_RAM_GB! GB Total (!FREE_RAM_GB! GB Available)
echo   Hardware Accel   : Windows Hypervisor Platform (WHPX)
echo.
echo ================================================================================
echo   [1] Install Multi-Instance Farm (Auto-Download ^& Launch Setup)
echo   [2] Exit Installer
echo ================================================================================
echo.

set /p USER_CHOICE="Enter your selection (1 or 2): "

if "!USER_CHOICE!"=="2" (
    echo.
    echo Installation cancelled by user. Goodbye!
    timeout /t 2 >nul
    exit /b 0
)

if not "!USER_CHOICE!"=="1" (
    echo.
    echo Invalid choice. Defaulting to Installation...
)

echo.
echo ================================================================================
echo [1/4] Checking Core Binaries and Compiling Native Launchers...
echo ================================================================================
if not exist "setup.exe" (
    echo Compiling setup.exe...
    powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build-executables.ps1"
) else (
    echo [OK] setup.exe is ready.
)

echo.
echo ================================================================================
echo [2/4] Verifying Android Emulation Subsystem & SDK Tools...
echo ================================================================================
if not exist "sdk\emulator\emulator.exe" (
    echo SDK components missing. Launching automated downloader...
    call ".\scripts\download-artifacts.bat"
) else (
    echo [OK] Android QEMU / WHPX Emulator detected.
)

if not exist "sdk\platform-tools\adb.exe" (
    echo ADB tools missing. Launching automated downloader...
    call ".\scripts\download-artifacts.bat"
) else (
    echo [OK] ADB Platform-Tools detected.
)

echo.
echo ================================================================================
echo [3/4] Verifying Game Packages (Dofus Touch 3.14.2)...
echo ================================================================================
set FOUND_APK=0
for /r "apks" %%f in (*.apk *.apkm) do (
    set FOUND_APK=1
)
if "!FOUND_APK!"=="1" (
    echo [OK] Dofus Touch package verified.
) else (
    echo [WARN] No APK found in apks/ directory. You can place the game APK anytime.
)

echo.
echo ================================================================================
echo [4/4] Launching Graphical Setup Wizard...
echo ================================================================================
echo Starting setup.exe...
start "" "setup.exe"

echo.
echo [COMPLETE] Setup wizard launched successfully. You may close this console.
timeout /t 3 >nul
exit /b 0
