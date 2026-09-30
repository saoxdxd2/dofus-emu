@echo off
REM ===========================================================================
REM  Dofus Touch Instance Farm - one-click installer
REM
REM  Double-click this file. It will ask for Administrator rights (required to
REM  enable the Windows hypervisor), then run install.ps1.
REM
REM  If it reports that a reboot is needed, reboot and double-click again.
REM ===========================================================================
setlocal
cd /d "%~dp0"

echo.
echo   Dofus Touch Instance Farm - installer
echo   ===================================
echo.
echo   This will:
echo     - enable the Windows Hypervisor Platform (needs a reboot)
echo     - install the Android SDK + API 29 x86_64 system image
echo     - create the instance AVDs
echo.
echo   Roughly 900 MB will be downloaded.
echo.

REM Prefer PowerShell 5.1 (present on every Windows 10/11 box) over pwsh.
set PSEXE=powershell.exe
where pwsh.exe >nul 2>&1 && set PSEXE=pwsh.exe

%PSEXE% -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set RC=%ERRORLEVEL%

echo.
if %RC%==0 (
  echo   Installer finished.
) else (
  echo   Installer exited with code %RC%.
)
echo.
pause
endlocal
