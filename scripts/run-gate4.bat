@echo off
REM Final Gate 4 comparison: 768 MB vs 1024 MB vs 1536 MB, full UI strip + zRAM.
REM -Command with @(768,1024,1536) is required: `-File -Levels 768,1024` hands
REM the token over as ONE string and [int[]] conversion fails.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "& '.\bench-memory.ps1' -Levels @(768,1024,1536) -ZramMb 384 -SettleSec 90"
