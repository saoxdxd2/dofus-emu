@echo off
setlocal
cd /d "%~dp0"
echo ==========================================================
echo    DOFUS TOUCH FARM 1-CLICK COMPILER
echo ==========================================================
echo.

set CSC="C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist %CSC% (
    set CSC="C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
if not exist %CSC% (
    echo [ERROR] .NET Framework 4.0/4.8 csc.exe compiler not found at %CSC%
    exit /b 1
)

REM The relay is a Go program and nothing else in this script builds it. It used to be built by
REM hand, which means a compile.bat after a Go change silently shipped the previous binary -- the
REM packaging step happily bundles whatever dofus-net-proxy.exe it finds. Build it here so one
REM command produces a consistent release.
where go >nul 2>nul
if errorlevel 1 (
    echo [WARN] go is not on PATH; keeping the existing dofus-net-proxy.exe unchanged.
) else (
    echo [1/3] Building dofus-net-proxy.exe from scripts\dofus-net-proxy.go...
    pushd scripts
    go build -ldflags "-s -w" -o ..\dofus-net-proxy.exe dofus-net-proxy.go
    REM `if errorlevel` is evaluated at run time; `%ERRORLEVEL%` inside a block is expanded when
    REM the block is parsed, so it would test the value from before go ran.
    if errorlevel 1 (
        popd
        echo [ERROR] go build failed
        exit /b 1
    )
    popd
)

echo [2/3] Compiling Build.exe from builder\Build.cs...
%CSC% /nologo /target:exe /r:System.IO.Compression.FileSystem.dll /out:Build.exe builder\Build.cs
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to compile Build.exe
    exit /b %ERRORLEVEL%
)

echo [3/3] Running Build.exe to generate DofusFarm.exe, uninstall.exe, and setup.exe...
Build.exe %*
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Build failed
    exit /b %ERRORLEVEL%
)

echo.
echo Build finished successfully!
endlocal
