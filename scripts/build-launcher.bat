@echo off
REM Build DofusLauncher.apk without Gradle.
REM Gradle is not installed here and a full Gradle/AGP setup would pull several
REM hundred MB plus a daemon, which is disproportionate for a single-class,
REM zero-resource APK. We build with the SDK's own tools instead:
REM   aapt2  : link the manifest into an APK (no resources to compile)
REM   javac  : compile the one activity
REM   d8     : dex to classes.dex
REM   zipalign, apksigner - align and sign with a debug key
REM
REM Output: launcher\build\DofusLauncher.apk
setlocal enabledelayedexpansion
set SDK=%~dp0..\sdk
set L=%~dp0..\launcher
set OUT=%L%\build
set TMP=%OUT%\tmp

if not exist "%OUT%" mkdir "%OUT%"
if not exist "%TMP%" mkdir "%TMP%"
if exist "%TMP%\classes" rmdir /s /q "%TMP%\classes"

REM Resolve the installed build-tools directory dynamically. Do NOT hardcode a
REM version here: an earlier revision mixed an auto-detected %BT% with a
REM hardcoded build-tools\34.0.0 path, which breaks as soon as another version
REM is installed.
set BT=
for /d %%d in ("%SDK%\build-tools\*") do set BT=%%d
if "%BT%"=="" (
  echo [build] No build-tools found in %SDK%\build-tools
  echo [build] Install with:  sdkmanager "build-tools;34.0.0"
  exit /b 1
)
echo [build] build-tools: %BT%

REM android.jar provides compile-time API stubs.
set AJ=%SDK%\platforms\android-29\android.jar
if not exist "%AJ%" (
  echo [build] Missing %AJ%
  echo [build] Install with:  sdkmanager "platforms;android-29"
  exit /b 1
)
echo [build] android.jar: %AJ%

REM ---------------------------------------------------------------- 1. link
REM There is no res\ directory (the launcher references only framework themes),
REM so `aapt2 compile` is skipped entirely - an earlier revision invoked it on a
REM non-existent directory, which just produced a confusing stderr message.
echo [build] 1/4 linking manifest...
"%BT%\aapt2.exe" link -o "%OUT%\unsigned.apk" -I "%AJ%" --manifest "%L%\src\main\AndroidManifest.xml" --min-sdk-version 24 --target-sdk-version 29
if errorlevel 1 (
  echo [build] aapt2 link FAILED
  exit /b 1
)

REM ---------------------------------------------------------------- 2. javac
where javac >nul 2>&1
if errorlevel 1 (
  REM NOTE: no closing parenthesis inside this echo. Batch treats an unquoted
  REM ")" inside a parenthesised block as the block terminator, which closed
  REM the if early and produced the misleading ". was unexpected at this time."
  echo [build] javac not on PATH - a JDK is required. Set JAVA_HOME or add it to PATH.
  exit /b 1
)
echo [build] 2/4 compiling java...
REM Use --release rather than -source/-target: JDK 25 removed support for source
REM level 8, which failed before the fallback could run. Release 8 is retried
REM below in case 11 is unavailable on an older JDK.
REM
REM android.jar must be on the CLASSPATH here. --release cannot be combined
REM with -bootclasspath (javac: "option --boot-class-path cannot be used
REM together with --release"), and without either one the Android classes are
REM invisible, producing "cannot find symbol: class Intent / class Log".
set AJC=%AJ%
javac --release 11 -cp "%AJC%" -d "%TMP%\classes" "%L%\src\main\java\com\dofusemu\launcher\DofusLauncherActivity.java"
if errorlevel 1 (
  echo [build]   retrying with --release 8 ...
  javac --release 8 -cp "%AJC%" -d "%TMP%\classes" "%L%\src\main\java\com\dofusemu\launcher\DofusLauncherActivity.java"
)
if errorlevel 1 (
  echo [build] javac FAILED
  exit /b 1
)

REM -------------------------------------------------------------------- 3. d8
REM d8 --output must be a DIRECTORY (or .jar/.zip). A .dex path fails with
REM "Invalid output ... must be a .zip or .jar archive or an existing directory".
REM
REM Do NOT call d8.bat. Its final line is `call "%java_exe%" ...`, and that CALL
REM propagates control back out of the calling script - the parent stops dead
REM right after "3/4 dexing" with no error and rc unset. Invoke the R8/D8 jar
REM directly with java instead, which returns normally.
REM Working dir is the build dir so every argument is short and space-free.
echo [build] 3/4 dexing...
set D8JAR=%BT%\lib\d8.jar
if not exist "%D8JAR%" (
  echo [build] d8.jar not found at %D8JAR%
  exit /b 1
)
set JAVAEXE=java.exe
if defined JAVA_HOME if exist "%JAVA_HOME%\bin\java.exe" set JAVAEXE=%JAVA_HOME%\bin\java.exe
pushd "%TMP%"
"%JAVAEXE%" -Xmx1024M -cp "%D8JAR%" com.android.tools.r8.D8 ^
  --lib %AJ:\=/% --min-api 24 --output . ^
  classes\com\dofusemu\launcher\DofusLauncherActivity.class
set D8RC=%ERRORLEVEL%
popd
if not "%D8RC%"=="0" (
  echo [build] d8 FAILED rc=%D8RC%
  exit /b 1
)
if not exist "%TMP%\classes.dex" (
  echo [build] d8 produced no classes.dex
  exit /b 1
)

REM Add classes.dex into the APK produced by aapt2.
REM Locate `jar` robustly. Two traps here:
REM   1. JAVA_HOME is usually UNSET, so "%JAVA_HOME%\bin\jar.exe" degrades to
REM      "\bin\jar.exe" and fails.
REM   2. Deriving the JDK from `where javac` is NOT enough - on this box javac
REM      resolves to Oracle's "javapath" SHIM (C:\Program Files\Common
REM      Files\Oracle\Java\javapath\javac.exe), a redirector that has no jar.exe
REM      beside it.
REM So: prefer JAVA_HOME, then scan the real JDK install dirs, then PATH.
set JAR=
if defined JAVA_HOME if exist "%JAVA_HOME%\bin\jar.exe" set JAR=%JAVA_HOME%\bin\jar.exe
if not defined JAR (
  for %%j in (javac.exe) do set JAVACPATH=%%~$PATH:j
  REM strip the trailing javac.exe to get the JDK bin directory
  if defined JAVACPATH for %%d in ("!JAVACPATH!") do set JAVABIN=%%~dpd
  if defined JAVABIN if exist "!JAVABIN!jar.exe" set JAR=!JAVABIN!jar.exe
)
if not defined JAR if exist "C:\Program Files\Java\jdk-25.0.2\bin\jar.exe" set JAR=C:\Program Files\Java\jdk-25.0.2\bin\jar.exe
if not defined JAR for /d %%d in ("C:\Program Files\Java\jdk-*") do if exist "%%d\bin\jar.exe" set JAR=%%d\bin\jar.exe
if not defined JAR (
  echo [build] could not locate jar.exe - cannot add classes.dex
  echo [build] set JAVA_HOME to a real JDK, e.g. "C:\Program Files\Java\jdk-25.0.2"
  exit /b 1
)
echo [build]   adding classes.dex with %JAR% ...
pushd "%TMP%"
"%JAR%" uf "%OUT%\unsigned.apk" classes.dex
if errorlevel 1 (
  popd
  echo [build] jar failed to add classes.dex
  exit /b 1
)
popd

REM ------------------------------------------------------------------ 4. sign
echo [build] 4/4 aligning + signing ...
REM Generate a debug keystore if absent so a clean machine builds unattended.
REM keytool must be resolved from the real JDK: it is NOT on PATH here, and
REM `where javac` finds only Oracle's javapath shim, which has no keytool beside
REM it. Without a working keytool the keystore is silently not created and
REM apksigner then fails with "Failed to load signer" + FileNotFoundException.
set KS=%USERPROFILE%\.android\debug.keystore
if not exist "%USERPROFILE%\.android" mkdir "%USERPROFILE%\.android"
set KEYTOOL=
if defined JAVA_HOME if exist "%JAVA_HOME%\bin\keytool.exe" set KEYTOOL=%JAVA_HOME%\bin\keytool.exe
if not defined KEYTOOL if exist "C:\Program Files\Java\jdk-25.0.2\bin\keytool.exe" set KEYTOOL=C:\Program Files\Java\jdk-25.0.2\bin\keytool.exe
if not defined KEYTOOL for /d %%d in ("C:\Program Files\Java\jdk-*") do if exist "%%d\bin\keytool.exe" set KEYTOOL=%%d\bin\keytool.exe
if not defined KEYTOOL (
  echo [build] could not locate keytool.exe - cannot create a debug keystore
  exit /b 1
)
if not exist "%KS%" (
  echo [build] generating debug keystore ...
  "%KEYTOOL%" -genkeypair -v -keystore "%KS%" -storepass android -keypass android -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Android Debug,O=Android,C=US" >nul 2>&1
)
if not exist "%KS%" (
  echo [build] debug keystore was not created
  exit /b 1
)
echo [build] keystore: %KS%
"%BT%\zipalign.exe" -f 4 "%OUT%\unsigned.apk" "%OUT%\aligned.apk"
if errorlevel 1 (
  echo [build] zipalign FAILED
  exit /b 1
)
"%BT%\apksigner.bat" sign --ks "%KS%" --ks-pass pass:android --key-pass pass:android --out "%OUT%\DofusLauncher.apk" "%OUT%\aligned.apk"
if errorlevel 1 (
  echo [build] apksigner FAILED
  exit /b 1
)
"%BT%\apksigner.bat" verify "%OUT%\DofusLauncher.apk"
if errorlevel 1 (
  echo [build] signature verification FAILED
  exit /b 1
)
echo [build] OK  %OUT%\DofusLauncher.apk
endlocal
exit /b 0
