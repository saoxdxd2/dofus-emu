@echo off
cd /d "%~dp0"
title Dofus Farm THX-01 ModernBERT Combat Decision AI Server
echo ==========================================================
echo    DOFUS TOUCH THX-01 COMBAT DECISION AI SERVER (PORT 8892)
echo ==========================================================
echo.
set "PY_EXE="
if exist runtimes\python\python.exe set "PY_EXE=runtimes\python\python.exe"
if not defined PY_EXE if exist python\python.exe set "PY_EXE=python\python.exe"
if not defined PY_EXE where python >nul 2>nul && set "PY_EXE=python"
if not defined PY_EXE (
    echo [ERROR] Python runtime not found!
    pause
    exit /b 1
)

echo Using Python runtime: %PY_EXE%
echo Starting THX-01 ModernBERT Combat AI Server on http://127.0.0.1:8892...
"%PY_EXE%" dofus-network\combat\combat_ai_server.py 8892
pause
