@echo off
REM Build DofusLauncher.apk without Gradle.
REM Gradle is not installed on this host and a full Gradle/AGP setup would pull
REM several hundred MB plus a daemon, which is disproportionate for a single
REM 1-class, 0-resource APK. We build it directly with the SDK's own tools:
REM   aapt2  -> compile + link resources and the manifest
REM   d8     -> dex the compiled classes
REM   apksigner -> sign with a debug key
REM Output: launcher\build\DofusLauncher.apk
setlocal
set SDK=C:\android-sdk
set L=%~dp0..\launcher
set OUT=%L%\build
set TMP=%OUT%\tmp

if not exist "%OUT%" mkdir "%OUT%"
if not exist "%TMP%" mkdir "%TMP%"

REM Resolve the newest installed build-tools directory.
set BT=
for /d %%d in ("%SDK%\build-tools\*") do set BT=%%d
if "%BT%"=="" (
  echo [build] No build-tools found in %SDK%\build-tools
  echo [build] Install with:  sdkmanager "build-tools;34.0.0" "platforms;android-29"
  exit /b 1
)
echo [build] Using build-tools: %BT%

REM android.jar provides the compile-time API stubs.
set AJ=%SDK%\platforms\android-29\android.jar
if not exist "%AJ%" (
  echo [build] Missing %AJ%
  echo [build] Install with:  sdkmanager "platforms;android-29"
  exit /b 1
)

echo [build] 1/5 compiling resources + manifest...
"%BT%\aapt2.exe" compile --dir "%L%\res" -o "%TMP%\compiled.zip"
if exist "%TMP%\compiled.zip" del "%TMP%\compiled.zip"
REM No res/ dir yet (the launcher needs no resources), so aapt2 compile is a
REM no-op. link the manifest directly instead.
echo [build] 2/5 linking...
"%BT%\aapt2.exe" link -o "%TMP%\base.apk" -I "%AJ%" --manifest "%L%\src\main\AndroidManifest.xml" --min-sdk-version 24 --target-sdk-version 29
if errorlevel 1 exit /b 1

echo [build] 3/5 compiling java...
"%BT%\javac.exe" -version >nul 2>&1
where javac >nul 2>&1
if errorlevel 1 (
  echo [build] javac not on PATH - ensure a JDK is available.
  exit /b 1
)
javac -source 8 -target 8 -bootclasspath "%AJ%" -d "%TMP%\classes" "%L%\src\main\java\com\dofusemu\launcher\DofusLauncherActivity.java" 2>&1
if errorlevel 1 (
  REM javac on JDK 21+ rejects -source 8. Retry with the modern target.
  javac -source 11 -target 11 -bootclasspath "%AJ%" -d "%TMP%\classes" "%L%\src\main\java\com\dofusemu\launcher\DofusLauncherActivity.java" 2>&1
)
if not exist "%TMP%\classes" (
  echo [build] compile produced no classes.
  exit /b 1
)

echo [build] 4/5 dexing...
"%BT%\d8.bat" --lib "%AJ%" --output "%TMP%" "%TMP%\classes\com\dofusemu\launcher\DofusLauncherActivity.class"
if errorlevel 1 exit /b 1

echo [build] assembling unsigned apk...
copy /Y "%TMP%\base.apk" "%OUT%\DofusLauncher-unsigned.apk" >nul
"%SDK%\build-tools\34.0.0\aapt2.exe" version >nul 2>&1
pushd "%TMP%"
"%BT%\aapt.exe" add "%OUT%\DofusLauncher-unsigned.apk" classes.dex 2>nul
popd

echo [build] 5/5 signing (debug key)...
REM Generate a debug keystore if absent, so the build is reproducible on a
REM clean machine without extra setup.
set KS=%USERPROFILE%\.android\debug.keystore
if not exist "%KS%" (
  if not exist "%USERPROFILE%\.android" mkdir "%USERPROFILE%\.android"
  keytool -genkeypair -v -keystore "%KS%" -storepass android -keypass android -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Android Debug,O=Android,C=US" >nul 2>&1
  echo [build] generated debug keystore
)
"%BT%\zipalign.exe" -f 4 "%OUT%\DofusLauncher-unsigned.apk" "%OUT%\DofusLauncher-aligned.apk"
"%BT%\apksigner.bat" sign --ks "%KS%" --ks-pass pass:android --key-pass pass:android --out "%OUT%\DofusLauncher.apk" "%OUT%\DofusLauncher-aligned.apk"
if errorlevel 1 exit /b 1
"%BT%\apksigner.bat" verify "%OUT%\DofusLauncher.apk"
if errorlevel 0 echo [build] OK  %OUT%\DofusLauncher.apk
echo [build] Done.
endlocal
