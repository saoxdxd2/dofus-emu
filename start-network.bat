@echo off
REM start-network.bat - bring the egress up, in this project, with nothing installed system-wide.
REM
REM The pieces were all here and none of them were wired together:
REM   scripts\proxy_pool.js      extracts, tests and rewrites proxies.txt (the pool)
REM   bin\warp\wireproxy.exe     the local tunnel, bound to socks_port only
REM   dofus-network\ai\test_egress.py   asks each candidate the only question that matters
REM
REM Usage:
REM   start-network.bat            one pass: tunnel up, pool refreshed, then report
REM   start-network.bat --daemon   keep the pool running in this window (recommended)
REM   start-network.bat --report   only report what works, change nothing
REM
REM Nothing here installs anything. wireproxy is a userspace binary in bin\warp and it
REM binds a local SOCKS5 port; Windows routing, DNS and the WiFi are untouched.

setlocal EnableExtensions
cd /d "%~dp0"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=--once"

echo.
echo === DofusFarm network ===
echo root: %CD%
echo.

REM ---- 1. node, for the pool manager -------------------------------------------
where node >nul 2>nul
if errorlevel 1 (
  echo [FAIL] node is not on PATH. The pool manager needs it.
  echo        Install Node.js, or run the pool from a machine that has it, and copy
  echo        proxies.txt here.
  goto :report
)

REM ---- 2. the tunnel ------------------------------------------------------------
set "SOCKS_PORT=40000"
if exist "network.ini" (
  for /f "usebackq tokens=1,2 delims==" %%A in ("network.ini") do (
    if /i "%%A"=="socks_port" set "SOCKS_PORT=%%B"
  )
)

if /i "%MODE%"=="--report" goto :pool

netstat -ano | findstr /r /c:"127.0.0.1:%SOCKS_PORT% .*LISTENING" >nul 2>nul
if not errorlevel 1 (
  echo [ OK ] tunnel already listening on 127.0.0.1:%SOCKS_PORT%
  goto :pool
)

if not exist "bin\warp\wireproxy.exe" (
  echo [WARN] bin\warp\wireproxy.exe is missing; skipping the tunnel.
  goto :pool
)
if not exist "bin\warp\wireproxy.conf" (
  echo [WARN] bin\warp\wireproxy.conf is missing; skipping the tunnel.
  echo        Run bin\warp\wgcf.exe register then wgcf generate to create one.
  goto :pool
)

echo [ .. ] starting the tunnel on 127.0.0.1:%SOCKS_PORT%
start "wireproxy" /min "bin\warp\wireproxy.exe" -c "bin\warp\wireproxy.conf"
timeout /t 3 /nobreak >nul
netstat -ano | findstr /r /c:"127.0.0.1:%SOCKS_PORT% .*LISTENING" >nul 2>nul
if errorlevel 1 (
  echo [FAIL] the tunnel did not come up. UDP to the WARP endpoint may be blocked on this
  echo        line - check network.ini's notes. The pool is still worth running.
) else (
  echo [ OK ] tunnel listening on 127.0.0.1:%SOCKS_PORT%
)

:pool
if /i "%MODE%"=="--report" goto :report
if /i "%MODE%"=="--daemon" (
  echo [ .. ] pool daemon: leave this window open, Ctrl+C to stop
  node "scripts\proxy_pool.js"
  goto :report
)

echo [ .. ] pool: one pass (re-verify approved, scrape if stale, deep-check, rewrite proxies.txt)
node "scripts\proxy_pool.js" --once

:report
echo.
echo === what actually works ===
if exist "runtimes\python\python.exe" (
  "runtimes\python\python.exe" "dofus-network\ai\test_egress.py"
) else (
  where python >nul 2>nul && (
    python "dofus-network\ai\test_egress.py"
  ) || echo [WARN] no python found; run dofus-network\ai\test_egress.py yourself
)

echo.
echo Done. A PASS above means a client can launch; fail_closed refuses otherwise.
endlocal
