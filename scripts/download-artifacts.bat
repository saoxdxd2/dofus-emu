@echo off
REM Download SDK artifacts with aria2c, 16 parallel streams per file.
REM
REM Why aria2c instead of curl: single-stream curl managed only ~220-400 KB/s
REM from this CDN and the transfer died mid-flight more than once. The Google
REM repository endpoints advertise "Accept-Ranges: bytes", so 16-stream
REM segmented download is far faster and resumes at block granularity.
REM
REM Why not `sdkmanager --install`: it throttled to ~1%% per 5 minutes with a
REM 0-byte temp file while curl hit 540 KB/s against the same CDN. We fetch the
REM same archives directly and unpack them into the SDK layout ourselves.
REM
REM URL NOTE: the <url> elements in repository2-1.xml are BARE FILENAMES resolved
REM against https://dl.google.com/android/repository/ . The emulator archive is
REM NOT under repository/emulator/ - that path returns HTTP 404 and yields a
REM 1449-byte HTML error page that later fails to unzip. Verified: /emulator/=404,
REM repo root=200.
REM Optional 1st argument overrides the SDK target directory:
REM   download-artifacts.bat  [C:\some\sdk\path]
setlocal
if not "%~1"=="" set "SDK=%~1"
if not defined SDK set SDK=C:\android-sdk
set DL=%TEMP%\dl
if not exist "%DL%" mkdir "%DL%"

set ARIA=C:\ProgramData\chocolatey\bin\aria2c.exe
if not exist "%ARIA%" set ARIA=aria2c
set BASE=https://dl.google.com/android/repository

REM -x16 16 connections per file, -s16 max 16 splits, -c continue a partial
REM file, --file-allocation=none so a partial does not get preallocated to full
REM size (which would make a later resume look complete).
set AOPTS=-x16 -s16 -k1M -c --file-allocation=none --console-log-level=warn --auto-file-renaming=false --allow-overwrite=true

echo [dl] 1/3 platform-tools (8MB)...
"%ARIA%" %AOPTS% -d "%DL%" -o "platform-tools.zip" "%BASE%/platform-tools_r37.0.1-win.zip"
if errorlevel 1 goto :dlfail
echo [dl] 2/3 emulator (438MB)...
"%ARIA%" %AOPTS% -d "%DL%" -o "emulator.zip" "%BASE%/emulator-windows_x64-16433917.zip"
if errorlevel 1 goto :dlfail
echo [dl] 3/3 API 29 x86_64 system image (658MB)...
"%ARIA%" %AOPTS% -d "%DL%" -o "sysimg.zip" "%BASE%/sys-img/android/x86_64-29_r08-windows.zip"
if errorlevel 1 goto :dlfail

echo [dl] Final sizes:
for %%f in ("%DL%\platform-tools.zip" "%DL%\emulator.zip" "%DL%\sysimg.zip") do echo [dl]   %%~nxf = %%~zf bytes

REM Sanity-check sizes BEFORE unpacking. Exact expected sizes come from the
REM Content-Length of each URL (verified against the archive):
REM   platform-tools_r37.0.1-win.zip  =   8044989
REM   emulator-windows_x64-16433917.zip = 459420448
REM   x86_64-29_r08-windows.zip     =  689676765
REM Earlier notes guessed "~1GB" for the system image; it is actually 658 MB.
REM Exact sizes must match: these ARE the Content-Length values, so any
REM deviation means truncation or an error page, not "close enough".
call :checksize "%DL%\platform-tools.zip" 8044989    || goto :dlfail
call :checksize "%DL%\emulator.zip"      459420448  || goto :dlfail
call :checksize "%DL%\sysimg.zip"        689676765  || goto :dlfail

REM --------------------------------------------------------------- unpacking
REM LAYOUT IS THE WHOLE TRICK: each archive already contains a single
REM top-level directory matching its install location:
REM   platform-tools.zip -> platform-tools/
REM   emulator.zip       -> emulator/
REM   sysimg.zip         -> x86_64/     (NOT system-images/...)
REM So all three are extracted into the SDK ROOT and their own folder names
REM place the files. Extracting the emulator zip directly into
REM %SDK%\emulator instead would nest it as emulator\emulator\... and
REM then fail the layout check below. For the system image the x86_64/ dir is
REM then moved into system-images\android-29\default\x86_64\, which is the
REM path the AVD config (image.sysdir.1) expects.
echo [dl] Unpacking archives into %SDK%...
if exist "%SDK%\platform-tools" rmdir /s /q "%SDK%\platform-tools"
if exist "%SDK%\emulator" rmdir /s /q "%SDK%\emulator"
if exist "%SDK%\system-images\android-29\default\x86_64" rmdir /s /q "%SDK%\system-images\android-29\default\x86_64"
if exist "%SDK%\x86_64" rmdir /s /q "%SDK%\x86_64"

powershell -NoProfile -Command "Expand-Archive -Path '%DL%\platform-tools.zip' -DestinationPath '%SDK%' -Force"
if errorlevel 1 goto :unpackfail
powershell -NoProfile -Command "Expand-Archive -Path '%DL%\emulator.zip' -DestinationPath '%SDK%' -Force"
if errorlevel 1 goto :unpackfail
powershell -NoProfile -Command "Expand-Archive -Path '%DL%\sysimg.zip' -DestinationPath '%SDK%' -Force"
if errorlevel 1 goto :unpackfail

REM sysimg extracts to %SDK%\x86_64 ; move it to the AVD-expected location.
if not exist "%SDK%\x86_64" goto :unpackfail
mkdir "%SDK%\system-images\android-29\default" 2>nul
move /Y "%SDK%\x86_64" "%SDK%\system-images\android-29\default\x86_64" >nul
if errorlevel 1 goto :unpackfail

echo [dl] Verifying unpacked layout...
set FAILED=0
if exist "%SDK%\emulator\emulator.exe" (echo [dl]   OK  emulator\emulator.exe) else (echo [dl]   MISSING emulator\emulator.exe & set FAILED=1)
if exist "%SDK%\platform-tools\adb.exe" (echo [dl]   OK  platform-tools\adb.exe) else (echo [dl]   MISSING platform-tools\adb.exe & set FAILED=1)
if exist "%SDK%\system-images\android-29\default\x86_64\system.img" (echo [dl]   OK  system.img) else (echo [dl]   MISSING system.img & set FAILED=1)
if not "%FAILED%"=="0" goto :unpackfail
echo [dl] Unpack verified.
echo [dl] Done.
endlocal
exit /b 0

:unpackfail
echo [dl] UNPACK FAILED - the archives above are truncated. Re-run this script;
echo [dl] `curl -C -` resumes from the partial file rather than restarting.
endlocal
exit /b 1

:dlfail
echo [dl] DOWNLOAD FAILED. Note: an earlier version used a wrong emulator URL
echo [dl] (repository/emulator/... instead of the repo root) which returns 404
echo [dl] and produced a 1449-byte HTML file. The URLs are now correct.
endlocal
exit /b 1

:checksize
REM %1=file %2=expected exact byte count. Returns 1 if missing or mismatched.
if not exist "%~1" (
  echo [dl] MISSING %~1
  exit /b 1
)
set SZ=0
for %%A in ("%~1") do set SZ=%%~zA
if not "%SZ%"=="%~2" (
  echo [dl] SIZE MISMATCH %~nx1 = %SZ% bytes, expected %~2
  exit /b 1
)
exit /b 0



