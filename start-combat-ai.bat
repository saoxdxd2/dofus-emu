@echo off
cd /d "%~dp0"
title Dofus Live Combat Policy API (PPO/PBT actor)
echo ==========================================================
echo    DOFUS LIVE COMBAT POLICY API (PORT 8891)
echo ==========================================================
echo.
set "PY_EXE="
rem The client runtime may not have JAX. Search the usual Windows Python
rem locations explicitly instead of relying on the Explorer PATH.
for %%P in (
    python
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "python\python.exe"
    "runtimes\python\python.exe"
) do (
    if not defined PY_EXE (
        %%~P -c "import jax" >nul 2>nul && set "PY_EXE=%%~P"
    )
)
if not defined PY_EXE (
    echo [ERROR] No Python runtime with JAX found.
    echo Install JAX into a Windows Python, or run:
    echo   python dofus-network\combat\combat_ai_server.py 8891
    pause
    exit /b 1
)

set "CKPT=%~1"
if not defined CKPT set "CKPT=dofus-network\models\encounter_selfplay.pkl"
echo Using Python runtime: %PY_EXE%
echo Checkpoint: %CKPT%
echo Starting the live PPO/PBT combat policy API on http://127.0.0.1:8891...
"%PY_EXE%" dofus-network\combat\combat_ai_server.py 8891 --checkpoint "%CKPT%"
pause
