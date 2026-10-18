@echo off
cd /d "%~dp0"
echo ====================================================
echo   DOFUS FARM AI SENTINEL PYTHON DEPENDENCY SETUP   
echo ====================================================
set "PY_EXE="
if exist runtimes\python\python.exe set "PY_EXE=runtimes\python\python.exe"
if not defined PY_EXE if exist python\python.exe set "PY_EXE=python\python.exe"
if not defined PY_EXE where python >nul 2>nul && set "PY_EXE=python"
if not defined PY_EXE (
  echo Python not found! Please download and install Python 3.11+:
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)
echo Using Python runtime: %PY_EXE%
echo Installing required AI packages (torch, transformers, thx01)...
"%PY_EXE%" -m pip install -r requirements.txt --no-warn-script-location
echo.
echo [SUCCESS] All Python AI libraries installed!
pause
