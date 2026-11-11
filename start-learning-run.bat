@echo off
REM start-learning-run.bat - one learning session, in one of the two memory arms.
REM
REM Usage:
REM   start-learning-run.bat replay  [minutes] [goal] [instance]
REM   start-learning-run.bat control [minutes] [goal] [instance]
REM
REM   replay   the store records and answers when the policy is silent (default behaviour)
REM   control  the store still records, but never answers -- the A/B control arm
REM
REM Both arms write their own log under trajectories\learning-<arm>-<stamp>.jsonl and
REM label themselves in the session_end row, so tools\memory_ab.py can tell them apart
REM without anyone remembering which file was which.
REM
REM The goal defaults to a farm prompt on purpose: fights are the binding constraint for
REM the combat corpus, and a quests prompt may only farm to gather a delivery item.

setlocal EnableExtensions
cd /d "%~dp0dofus-network"

set "ARM=%~1"
if "%ARM%"=="" set "ARM=replay"
set "MINUTES=%~2"
if "%MINUTES%"=="" set "MINUTES=90"
set "GOAL=%~3"
if "%GOAL%"=="" set "GOAL=farm the mobs on this map"
set "INSTANCE=%~4"
REM The app names its slots dofus-01, dofus-02, ... and ai_settings.ini targets slot 1.
if "%INSTANCE%"=="" set "INSTANCE=dofus-01"

if /i "%ARM%"=="replay" goto :arm_ok
if /i "%ARM%"=="control" goto :arm_ok
echo [ERROR] unknown arm "%ARM%": use replay or control
exit /b 2
:arm_ok

REM The embedded runtime is what the other scripts use; a system Python works too.
set "PY=%~dp0runtimes\python\python.exe"
if not exist "%PY%" set "PY=python"

REM One log per arm, rotated by the loop itself. A date stamp built from %DATE% is
REM locale-dependent (it produced "202601Tu" on this machine), and the loop already
REM names rotated files with a real timestamp.
set "LOG=trajectories\learning-%ARM%.jsonl"
set "STOP=%CD%\AUTOPILOT_STOP.%ARM%"

REM A leftover stop file from the previous session ends the run on its first check and
REM looks exactly like "the agent did nothing". Clearing it is not optional.
if exist "%STOP%" del /q "%STOP%"

set "EXTRA="
if /i "%ARM%"=="control" set "EXTRA=--no-memory-replay"

echo ==========================================================
echo   LEARNING RUN  arm=%ARM%  minutes=%MINUTES%  instance=%INSTANCE%
echo   goal: %GOAL%
echo   log:  %LOG%
echo   stop: echo anything ^> "%STOP%" to stop early
echo ==========================================================

"%PY%" "tools\run_autopilot.py" ^
    --goal "%GOAL%" ^
    --instance "%INSTANCE%" ^
    --log "%LOG%" ^
    --stop-file "%STOP%" ^
    --memory "data\learned_memory.json" ^
    --brain auto ^
    --minutes %MINUTES% ^
    --steps 0 ^
    %EXTRA%

echo.
echo run finished. next:
echo   python tools\memory_ab.py
echo   python tools\seed_memory.py
endlocal
