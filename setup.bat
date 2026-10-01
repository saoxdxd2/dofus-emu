@echo off
REM One-line bootstrap for a clean clone: downloads the SDK into <repo>\sdk,
REM installs platform-tools + emulator + the API 29 x86_64 image, and builds
REM the golden userdata image.
REM
REM   setup.bat                 full bootstrap
REM   setup.bat -SkipGolden     environment only, no golden image
REM   setup.bat -SkipVerify     skip the final sanity check
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
set RC=%ERRORLEVEL%
if not "%RC%"=="0" (
  echo.
  echo [setup] FAILED with exit code %RC%.
  pause
)
endlocal & exit /b %RC%
