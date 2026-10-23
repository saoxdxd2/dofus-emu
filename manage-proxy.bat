@echo off
REM manage-proxy.bat - look after the egress from inside the install, after setup.exe is done.
REM
REM start-network.bat brings the egress up. This keeps it up: what is configured, what is
REM actually working, add a proxy you paid for, refresh the pool, or start over.
REM
REM Everything it touches lives in this folder. Nothing is installed system-wide and no
REM Windows routing, DNS or proxy setting is read or written.
REM
REM   manage-proxy.bat                      status: config, pool size, tunnel, what works
REM   manage-proxy.bat test                 probe every candidate now
REM   manage-proxy.bat rank                 ping each candidate and rank by stability
REM   manage-proxy.bat watchdog [seconds]    keep the pool alive; hunt when nothing works
REM   manage-proxy.bat add <url>            test a proxy, then save it if it passes
REM   manage-proxy.bat add <url> --replace  save it as the only entry
REM   manage-proxy.bat refresh              harvest new candidates, then one pool pass
REM   manage-proxy.bat reset                forget the pool bookkeeping and start clean
REM   manage-proxy.bat logs                 show the tail of both logs

setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PY=%~dp0runtimes\python\python.exe"
if not exist "%PY%" (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [FAIL] no python found: runtimes\python\python.exe is missing and python is not on PATH.
    exit /b 2
  )
  set "PY=python"
)

set "CMD=%~1"
if "%CMD%"=="" set "CMD=status"

if /i "%CMD%"=="status" goto :status
if /i "%CMD%"=="test"   goto :test
if /i "%CMD%"=="rank"   goto :rank
if /i "%CMD%"=="watchdog" goto :watchdog
if /i "%CMD%"=="add"    goto :add
if /i "%CMD%"=="refresh" goto :refresh
if /i "%CMD%"=="reset"  goto :reset
if /i "%CMD%"=="logs"   goto :logs
echo unknown command: %CMD%
echo run "manage-proxy.bat" with no arguments for the list.
exit /b 2

REM ---------------------------------------------------------------------------
:status
echo.
echo === egress, as configured ===
REM Counting and config reading live in python on purpose: a batch file that shells out to
REM `find` breaks the moment someone runs it from Git Bash, where `find` is GNU find and the
REM command walks the whole drive instead of counting a file. PATH is the caller's, not ours.
"%PY%" "dofus-network\ai\pool_status.py"

if exist "network.ini" (
  for /f "usebackq tokens=1,2 delims==" %%A in ("network.ini") do (
    if /i "%%A"=="socks_port" set "SOCKS_PORT=%%B"
  )
)
if not defined SOCKS_PORT set "SOCKS_PORT=40000"

echo.
echo === the tunnel ===
netstat -ano | findstr /r /c:"127.0.0.1:%SOCKS_PORT% .*LISTENING" >nul 2>nul
if errorlevel 1 (
  echo   socks5 127.0.0.1:%SOCKS_PORT% : not listening
  echo   ^(the app starts it on demand; start-network.bat starts it now^)
) else (
  echo   socks5 127.0.0.1:%SOCKS_PORT% : listening
)

echo.
echo === what actually works ===
"%PY%" "dofus-network\ai\test_egress.py"
echo.
echo next: manage-proxy.bat add ^<url^>   if you have a proxy you trust
exit /b 0

REM ---------------------------------------------------------------------------
:test
"%PY%" "dofus-network\ai\test_egress.py"
exit /b %ERRORLEVEL%

REM ---------------------------------------------------------------------------
:rank
REM Ping every candidate and rank it. The pool's deep check answers "does this open a tunnel
REM right now", which is necessary and not sufficient — a proxy that answers once and then
REM stalls ends a session. This measures loss and jitter over time, which is the difference.
echo.
echo ranking the pool by measured stability ...
"%PY%" "dofus-network\ai\score_proxies.py" --samples 34 --interval 0.4 --timeout 2
exit /b %ERRORLEVEL%

REM ---------------------------------------------------------------------------
:watchdog
REM The app already auto-uses the pool: ProxySupervisor re-reads proxies.txt every 45 s,
REM probes every entry in parallel, and switches to the best healthy one, logging
REM "active egress -> ...". What it cannot do is refill the file. Public proxies die within
REM hours, so without this the pool empties and every launch refuses with "no healthy egress".
REM
REM This is the other half of "auto": every WATCH_SECONDS, ask the same question the app asks
REM (does any candidate carry a tunnel right now) and, if the answer is no, go and find more.
set "WATCH_SECONDS=300"
if not "%~2"=="" set "WATCH_SECONDS=%~2"
set /a WATCH_FAILS=0

where node >nul 2>nul
if errorlevel 1 (
  echo [FAIL] node is not on PATH; the watchdog needs it to hunt.
  exit /b 2
)

echo.
echo watchdog: checking every %WATCH_SECONDS%s, hunting when nothing works.
echo leave this window open. Ctrl+C to stop.
echo.
:wd_loop
set /a WATCH_FAILS+=0
"%PY%" "dofus-network\ai\test_egress.py" >nul 2>&1
if errorlevel 1 (set /a WATCH_FAILS+=1) else (set /a WATCH_FAILS=0)

REM One failure is not an outage. Public proxies flap, and a watchdog that hunts on a single
REM bad sample spends its life re-scraping instead of watching. Two in a row is a real gap.
if !WATCH_FAILS! GEQ 2 (
  echo [%TIME%] no working exit twice in a row - hunting
  node "scripts\harvest_proxies.js" --once
  node "scripts\proxy_pool.js" --once --batch 800
  "%PY%" "dofus-network\ai\test_egress.py" >nul 2>&1
  if errorlevel 1 (
    echo [%TIME%] still nothing after a hunt; will try again in %WATCH_SECONDS%s
    set /a WATCH_FAILS=0
  ) else (
    echo [%TIME%] recovered - the app picks it up within 45s, no restart needed
    set /a WATCH_FAILS=0
  )
) else (
  if !WATCH_FAILS! EQU 1 (
    echo [%TIME%] one failed check - watching, not hunting yet
  ) else (
    echo [%TIME%] egress ok
  )
)
REM Absolute paths on purpose: a batch file inherits the caller's PATH, and when that caller is
REM Git Bash these resolve to GNU tools that reject Windows flags. Same trap as `find` in the
REM status command, and the same reason the counting moved to python.
REM
REM ping instead of timeout: timeout.exe refuses to run when stdin is not a console ("input
REM redirection is not supported"), which is exactly the case when this is piped to a log.
set /a PING_N=%WATCH_SECONDS%+1
"%SystemRoot%\System32\ping.exe" -n !PING_N! 127.0.0.1 >nul 2>&1
goto :wd_loop

REM ---------------------------------------------------------------------------
:add
set "ENTRY=%~2"
if "%ENTRY%"=="" (
  echo usage: manage-proxy.bat add http://user:pass@host:port
  echo        manage-proxy.bat add socks5://host:port --replace
  exit /b 2
)
if /i "%~3"=="--replace" (set "REPLACE=1") else (set "REPLACE=")

echo.
echo testing %ENTRY% before saving it ...
"%PY%" "dofus-network\ai\test_egress.py" --proxy "%ENTRY%"
if errorlevel 1 (
  echo.
  echo not saved. A proxy that fails here fails the app's own gate too, and saving it
  echo would only make the next launch fail closed with no record of why.
  exit /b 1
)

if defined REPLACE (
  echo # proxies.txt - one entry, added by manage-proxy.bat> proxies.txt
  echo %ENTRY%>> proxies.txt
  echo saved as the only entry in proxies.txt
) else (
  echo %ENTRY%>> proxies.txt
  echo appended to proxies.txt
)

if exist "proxy_pool_state.json" (
  echo note: proxy_pool_state.json still lists the old approvals. Run
  echo       manage-proxy.bat reset if the pool keeps rejecting this entry.
)
echo.
echo the launcher re-reads proxies.txt within 10 seconds; no restart needed.
exit /b 0

REM ---------------------------------------------------------------------------
:refresh
where node >nul 2>nul
if errorlevel 1 (
  echo [FAIL] node is not on PATH; the harvester and pool need it.
  exit /b 2
)
echo.
echo === harvest ===
node "scripts\harvest_proxies.js" --once
echo.
echo === pool, one pass ===
node "scripts\proxy_pool.js" --once
echo.
"%PY%" "dofus-network\ai\test_egress.py"
exit /b 0

REM ---------------------------------------------------------------------------
:reset
echo.
echo This forgets which proxies were approved, quarantined or dead, so the pool
echo re-tests everything from scratch. proxies.txt and proxies_raw.txt are kept.
choice /c YN /n /m "Reset the pool bookkeeping? [Y/N] "
if errorlevel 2 (
  echo cancelled.
  exit /b 0
)
if exist "proxy_pool_state.json" del /q "proxy_pool_state.json"
echo pool bookkeeping cleared.
exit /b 0

REM ---------------------------------------------------------------------------
:logs
echo.
echo === asset_cache\proxy.log (the relay, last 20) ===
if exist "asset_cache\proxy.log" (
  powershell -NoProfile -Command "Get-Content -Path 'asset_cache\proxy.log' -Tail 20"
) else (
  echo   no relay log yet
)
echo.
echo === proxy_pool.log (the pool, last 12) ===
if exist "proxy_pool.log" (
  powershell -NoProfile -Command "Get-Content -Path 'proxy_pool.log' -Tail 12"
) else (
  echo   the pool has not run yet
)
exit /b 0
