@echo off
REM Detached wrapper so the Gate 4 benchmark cannot be killed by a tool timeout.
REM
REM NOTE: passing -Levels 768,1024,1536 via `-File` does NOT work. PowerShell's
REM -File hands the token to the script as a single string, so [int[]] conversion
REM fails with "Input string was not in a correct format". Start-Process
REM -ArgumentList is no better - it splits on the comma. Using -Command with an
REM explicit array expression works, because the values are parsed as real ints.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "& '.\bench-memory.ps1' -Levels @(768,1024,1536) -SettleSec 60"
