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

echo [1/2] Compiling Build.exe from builder\Build.cs...
%CSC% /nologo /target:exe /r:System.IO.Compression.FileSystem.dll /out:Build.exe builder\Build.cs
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to compile Build.exe
    exit /b %ERRORLEVEL%
)

echo [2/2] Running Build.exe to generate DofusFarm.exe, uninstall.exe, and setup.exe...
Build.exe %*
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Build failed
    exit /b %ERRORLEVEL%
)

echo.
echo Build finished successfully!
endlocal
