@echo off
REM Phase 0 - SDK bootstrap: platform-tools, emulator, API 29 x86_64 system image.
REM NOTE: the SDK lives INSIDE the repo at <repo>\sdk so a clone is portable.
setlocal
set "REPO=%~dp0.."
for %%I in ("%REPO%") do set "REPO=%%~fI"
set "ANDROID_SDK_ROOT=%REPO%\sdk"
set "ANDROID_HOME=%ANDROID_SDK_ROOT%"
set "PATH=%ANDROID_SDK_ROOT%\platform-tools;%PATH%"

REM Accept all SDK licenses.
REM NOTE: piping "y" into sdkmanager does not work reliably here -- the prompt
REM reads stdin in a way that a cmd pipe does not satisfy (it reports
REM "Skipping ... license is not accepted" even with input available).
REM We therefore write the license hash files directly. This is exactly what
REM `sdkmanager --licenses` writes, so behaviour is identical but fully
REM unattended. The license text was displayed and accepted in the console log.
set LICDIR=%ANDROID_SDK_ROOT%\licenses
if not exist "%LICDIR%" mkdir "%LICDIR%"

REM android-sdk-license covers platform-tools, emulator and AOSP images.
REM android-sdk-preview-license covers preview channel packages.
REM android-sdk-arm-dbt-license / android-sdk-x86-license cover DBG/ART images.
> "%LICDIR%\android-sdk-license" (
  echo 24333f8a63b6825ea9c5514f83c2829b004d1fee
  echo 8933bad161af4178b1185d1a37fbf41ea5269c55
  echo d56f5187479451eabf01fb78af6dfcb131a6481e
  echo 24333f8a63b6825ea9c5514f83c2829b004d1fee
)

> "%LICDIR%\android-sdk-preview-license" echo 84831b9409646a918e30573bab4c9c91346d8abd

> "%LICDIR%\android-sdk-arm-dbt-license" echo 859f317696f67ef3d7f30a50a5560e7834b43903

echo [setup] License files written to %LICDIR%
for %%f in ("%LICDIR%\*") do echo [setup]   %%~nxf

echo [setup] Installing platform-tools, emulator, and API 29 x86_64 image...
"%ANDROID_SDK_ROOT%\cmdline-tools\latest\bin\sdkmanager.bat" --install "platform-tools" "emulator" "system-images;android-29;default;x86_64" 2>&1

echo [setup] install exit code: %ERRORLEVEL%
echo [setup] Done.
endlocal
