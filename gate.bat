@echo off
REM One command that decides whether the tree is green. Exits nonzero on failure.
REM Runs on the embedded interpreter so it matches what ships.
setlocal
set "PY=%~dp0runtimes\python\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0dofus-network\tools\gate.py" %*
exit /b %ERRORLEVEL%
