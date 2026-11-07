@echo off
cd /d "%~dp0"
echo ====================================================
echo   DOFUS FARM  OPTIONAL AI TIER SETUP
echo   (training, export and the optional torch policies)
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
REM setup.exe installs requirements.txt (the runtime tier the product drives with). This script adds
REM requirements-ai.txt on top: whatever tools/shipped_deps.py puts in that tier, listed with the
REM per-package sizes and reasons in the file itself. Enumerating the packages here is how this line
REM went stale when the tier gained onnxruntime, tokenizers, scikit-learn and joblib -- read the file.
echo [1/2] Making sure the runtime tier is present...
"%PY_EXE%" -m pip install -r requirements.txt --no-warn-script-location -q
if errorlevel 1 (
  echo [ERROR] The runtime tier failed to install. Nothing else here will work; check the
  echo         network connection or the proxy settings in network.ini and run this again.
  pause
  exit /b 1
)
echo [2/2] Installing the AI tier (the packages listed in requirements-ai.txt)...
"%PY_EXE%" -m pip install -r requirements-ai.txt --no-warn-script-location
if errorlevel 1 (
  echo [ERROR] The AI tier failed to install. Training/export and the optional torch
  echo         policies will not run; the Sentinel and the rest of the product do.
  pause
  exit /b 1
)
echo.
echo [SUCCESS] AI tier installed - the torch forecaster can load a checkpoint when one exists.
echo Note: the encoder and learned-command paths also need their weight files, and those are not in
echo setup.exe any more (models/encoder30_v15/, models/command_encoder30/, models/tool_policy.onnx,
echo models/verifier_head.onnx, models/lexical_policy/policy.joblib). They stay in the repo; copy the
echo one you want to run into the installed tree, then set the flag that turns it on.
pause
