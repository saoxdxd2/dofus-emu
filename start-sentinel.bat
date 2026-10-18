@echo off
cd /d "%~dp0"
title Dofus Farm AI Sentinel Daemon
set "PY_EXE="
if exist runtimes\python\python.exe set "PY_EXE=runtimes\python\python.exe"
if not defined PY_EXE if exist python\python.exe set "PY_EXE=python\python.exe"
if not defined PY_EXE set "PY_EXE=python"
echo Starting Singleton AI Sentinel on http://127.0.0.1:8890 using %PY_EXE%...
"%PY_EXE%" dofus-network\networkhealth\sentinel.py
pause
