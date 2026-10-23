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

REM The embedded interpreter, same lookup the other scripts use. Everything below is Python
REM because the installer ships Python and no Node.
set "PY=%~dp0runtimes\python\python.exe"
if not exist "%PY%" (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [FAIL] no python found: runtimes\python\python.exe is missing and python is not on PATH.
    exit /b 2
  )
  set "PY=python"
)

echo.
echo === DofusFarm network ===
echo root: %CD%
echo.

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

REM The pool manager is Python, on purpose: the installer ships an embedded interpreter and no
REM Node, so a Node-based pool could never refresh anything on an installed copy. That is the
REM whole reason the file used to decay to one dead entry.
if /i "%MODE%"=="--daemon" goto :net_daemon

echo [ .. ] pool: one pass (fetch, screen, deep-check, rewrite proxies.txt)
"%PY%" -u "dofus-network\ai\pool_runner.py" --batch 800
goto :report

REM The label has to live outside the if-block: `goto` into a parenthesised block is not
REM something cmd.exe handles, and it fails in a way that looks like the loop simply never runs.
:net_daemon
echo [ .. ] pool daemon: leave this window open, Ctrl+C to stop
:net_loop
"%PY%" -u "dofus-network\ai\pool_runner.py" --batch 800
"%SystemRoot%\System32\ping.exe" -n 601 127.0.0.1 >nul 2>&1
goto :net_loop

:report
echo.
echo === what actually works ===
"%PY%" -u "dofus-network\ai\test_egress.py"

echo.
echo Done. A PASS above means a client can launch; fail_closed refuses otherwise.
endlocal
