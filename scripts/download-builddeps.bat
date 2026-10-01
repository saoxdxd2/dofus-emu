@echo off
REM Download + install build-tools and platforms (needed by build-launcher.bat).
REM
REM Why direct download instead of sdkmanager: on this host sdkmanager throttled
REM to ~1%% per 5 minutes with a 0-byte temp file, while aria2c sustains ~700 KB/s
REM across 16 streams.
REM
REM LAYOUT GOTCHA: these archives do NOT contain their final folder names.
REM   build-tools_r34-windows.zip  -> android-14/
REM   platform-29_r05.zip          -> android-10/
REM So each is extracted to the SDK root and then RENAMED into place. Extracting
REM straight into build-tools\34.0.0 would produce build-tools\34.0.0\android-14\.
REM
REM Unlike the emulator, both of these ship a package.xml, so no metadata needs
REM synthesising.
setlocal
set SDK=C:\android-sdk
set DL=%TEMP%\dl2
set BASE=https://dl.google.com/android/repository
if not exist "%DL%" mkdir "%DL%"

if not exist "%SDK%\build-tools\34.0.0\aapt2.exe" (
  if not exist "%DL%\build-tools_r34-windows.zip" (
    echo [deps] downloading build-tools 34.0.0 ...
    "%ProgramData%\chocolatey\bin\aria2c.exe" -x16 -s16 -k1M -c --file-allocation=none --console-log-level=warn -d "%DL%" -o "build-tools_r34-windows.zip" "%BASE%/build-tools_r34-windows.zip"
  )
  if not exist "%SDK%\platforms\android-29\android.jar" (
    echo [deps] downloading platform android-29 ...
    "%ProgramData%\chocolatey\bin\aria2c.exe" -x16 -s16 -k1M -c --file-allocation=none --console-log-level=warn -d "%DL%" -o "platform-29_r05.zip" "%BASE%/platform-29_r05.zip"
  )

  echo [deps] extracting ...
  if exist "%SDK%\build-tools" rmdir /s /q "%SDK%\build-tools"
  if exist "%SDK%\platforms" rmdir /s /q "%SDK%\platforms"

  powershell -NoProfile -Command "Expand-Archive -Path '%DL%\build-tools_r34-windows.zip' -DestinationPath '%SDK%' -Force"
  if errorlevel 1 goto :fail
  powershell -NoProfile -Command "Expand-Archive -Path '%DL%\platform-29_r05.zip' -DestinationPath '%SDK%' -Force"
  if errorlevel 1 goto :fail

  echo [deps] relocating android-14 -^> build-tools\34.0.0 ...
  REM `move` will NOT create intermediate directories, so make them first.
  REM Without this the relocation fails with "The system cannot find the path
  REM specified" even though the extraction succeeded.
  if not exist "%SDK%\build-tools" mkdir "%SDK%\build-tools"
  if not exist "%SDK%\platforms" mkdir "%SDK%\platforms"
  move /Y "%SDK%\android-14" "%SDK%\build-tools\34.0.0" >nul
  move /Y "%SDK%\android-10" "%SDK%\platforms\android-29" >nul
) else (
  echo [deps] already installed
)

echo [deps] verifying ...
if exist "%SDK%\build-tools\34.0.0\aapt2.exe" (echo [deps]   OK  aapt2.exe) else (echo [deps]   MISSING aapt2.exe & goto :fail)
if exist "%SDK%\build-tools\34.0.0\d8.bat"     (echo [deps]   OK  d8.bat)     else (echo [deps]   MISSING d8.bat & goto :fail)
if exist "%SDK%\build-tools\34.0.0\apksigner.bat" (echo [deps]   OK  apksigner.bat) else (echo [deps]   MISSING apksigner.bat & goto :fail)
if exist "%SDK%\platforms\android-29\android.jar" (echo [deps]   OK  android.jar) else (echo [deps]   MISSING android.jar & goto :fail)
echo [deps] Done.
endlocal
exit /b 0
:fail
echo [deps] FAILED
endlocal
exit /b 1
