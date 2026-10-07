@echo off
REM ===========================================================================
REM  Dofus Touch Instance Farm - Graphical Setup Wizard & Installer
REM ===========================================================================
setlocal
cd /d "%~dp0"

echo.
echo   Dofus Touch Virtualization Farm - Launching Setup Wizard...
echo   ============================================================
echo.

set PSEXE=powershell.exe
where pwsh.exe >nul 2>&1 && set PSEXE=pwsh.exe

REM Launch Graphical Installer Wizard
%PSEXE% -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\installer-gui.ps1" %*
set RC=%ERRORLEVEL%

if %RC% NEQ 0 (
  echo.
  echo Setup wizard closed with return code %RC%.
  pause
)
endlocal
