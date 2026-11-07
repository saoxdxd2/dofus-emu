@echo off
cd /d "%~dp0"
title Dofus Farm AI Sentinel Daemon
set "PY_EXE="
if exist runtimes\python\python.exe set "PY_EXE=runtimes\python\python.exe"
if not defined PY_EXE if exist python\python.exe set "PY_EXE=python\python.exe"
if not defined PY_EXE set "PY_EXE=python"
REM The Sentinel is torch-free since 2026-11-07: the THX-01 ModernBERT classifier was removed and
REM the forecaster loads its checkpoint lazily. It needs only the runtime tier, so no AI-tier
REM preflight is required. Training, export and the optional torch policies still use that tier.
echo Starting Sentinel on http://127.0.0.1:8890 using %PY_EXE%...
"%PY_EXE%" dofus-network\networkhealth\sentinel.py
pause
