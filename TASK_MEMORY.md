# Dofus Farm: Task Memory

## Migration Log: Milestone 2 — CPU Optimization, Blackbox Verification & Full Suite Polish

### 1. CPU Bottleneck Resolution & Native C++ Architecture Evaluation
- **C++ Optimization Analysis**:
  - Investigated whether a custom C++ module is needed.
  - **Verdict**: Custom C++ is **unnecessary and counterproductive** because Microsoft Edge WebView2 is already 100% native C++ (Chromium Blink engine, V8 JIT compiler, ANGLE Direct3D 11 graphics layer). Introducing custom C++ DLLs would add P/Invoke marshalling overhead without providing rendering benefits.
  - **The Real CPU Bottleneck**: The legacy Android emulator ran an entire Linux guest kernel, Android system server, SurfaceFlinger, and Dalvik/ART GC on host vCPUs.
  - **WebView2 CPU Governance Solution**:
    1. **Background Timer Throttling**: Configured `--disable-background-timer-throttling=false --disable-renderer-backgrounding=false` so Chromium automatically slows background timer ticks.
    2. **EcoQoS & Priority Governance** in [`desktop/PriorityGovernor.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/PriorityGovernor.cs):
       - Dynamically detects the active foreground window (`Win32.GetForegroundWindow`).
       - Active instance: browser process & child renderers boosted to `ProcessPriorityClass.AboveNormal` with EcoQoS disabled.
       - Inactive / background instances: throttled to `ProcessPriorityClass.BelowNormal` and Windows EcoQoS enabled via Win32 `SetProcessInformation(ProcessPowerThrottling, PROCESS_POWER_THROTTLING_EXECUTION_SPEED)`.
       - Idle state (when user switches to external application): all WebView2 processes enter low-power background mode.

### 2. Blackbox Verification (100% Closed)
- **Automated Verification** via [`tests/test_webview2.ps1`](file:///c:/Users/sao/Documents/dofus-emu/tests/test_webview2.ps1):
  - Injected [`scripts/mobile-disguise.js`](file:///c:/Users/sao/Documents/dofus-emu/scripts/mobile-disguise.js) on every document created via `AddScriptToExecuteOnDocumentCreatedAsync`.
  - **Live Telemetry Output Recorded**:
    - `gpuVendor`: **"ARM"**
    - `gpuRenderer`: **"Mali-G72 MP3"**
    - `maxTextureSize`: **8192**
    - `userAgent`: **"Mozilla/5.0 (Linux; Android 10; SM-A515F Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36"**
    - `webGl1`: **true**, `webGl2`: **true**
    - `Working Set (UI)`: **99.8 MB**
    - `Lingering Processes`: **0**
    - `Result`: **>>> [PASS] All WebView2 Backend Unit Checks Succeeded! <<<**

### 3. Project Cleanup & File Hygiene
- Removed obsolete test & temp files: `boot_probe.log`, `build.log`, `cache.img.qcow2`, `DofusFarm.log`, `scratch_screen.png`.
- Removed scratch folders: `.clinetmp/` and `.freebuff/`.
- Removed bloated non-essential skins: `sdk/emulator/resources/skins/android-36` (Pixel 10 preview overlays).
- Consolidated all tests in [`tests/`](file:///c:/Users/sao/Documents/dofus-emu/tests/).

### 4. Installer, Uninstaller & Builder Updates
- **[`desktop/installer.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/installer.cs)**: Fixed zero-copy unpacker to use `Path.Combine(targetDir, rel)` for subdirectories, preserving `assets/` and `runtimes/win-x64/` / `runtimes/win-x86/`.
- **[`desktop/uninstaller.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/uninstaller.cs)**: Updated process termination list to include `msedgewebview2` and added recursive cleanup of `%LOCALAPPDATA%\DofusFarm` (`webview2_profiles`).
- **[`builder/build.cs`](file:///c:/Users/sao/Documents/dofus-emu/builder/build.cs)**:
  - Added `IClientBackend.cs` to standalone setup compilation sources.
  - Full release build compiles with zero errors:
    - `DofusFarm.exe`: 251,392 bytes
    - `uninstall.exe`: 17,920 bytes
    - `setup.exe`: 919.6 MB (standalone installer embedding sdk.7z and template.zip)
- **[`compile.bat`](file:///c:/Users/sao/Documents/dofus-emu/compile.bat)**: Verified 1-click execution.

### 5. Milestone 3 — Real Dofus Touch Client Extraction & Standalone WebView2 Game Boot
- **Acquisition of the Real Game Assets**:
  - Located the pre-installed application `com.ankama.dofustouch` on disk in `dofus-01.avd`.
  - Extracted the complete APK: `base.apk` (14.9 MB).
  - Unpacked `assets/www/` into [`desktop/client/www/`](file:///c:/Users/sao/Documents/dofus-emu/desktop/client/www/).
  - Identified the primary game engine bundles:
    - [`build/script.js`](file:///c:/Users/sao/Documents/dofus-emu/desktop/client/www/build/script.js): 5.39 MB compiled HTML5/WebGL game client (`window.isoEngine`, `window.actorManager`, `mapRenderer`).
    - [`build/styles-native.css`](file:///c:/Users/sao/Documents/dofus-emu/desktop/client/www/build/styles-native.css): 1.20 MB styles.
- **Cordova Standalone Shim Implementation**:
  - Authored [`desktop/client/www/js/cordova-standalone-shim.js`](file:///c:/Users/sao/Documents/dofus-emu/desktop/client/www/js/cordova-standalone-shim.js):
    - Mocks native bridge calls (`_cordovaNative.exec`) for `Device`, `AppSettings`, `AppInfo`, `NetworkStatus`, `StatusBar`, `SplashScreen`, `Keyboard`, `browsertab`, and `InAppBrowser`.
    - Returns official Ankama production login/game gateway: `https://dt-proxy-production-login.ankama-games.com`.
    - Triggers `deviceready` cleanly without Cordova prompt dialogs.
  - Authored [`desktop/client/www/standalone.html`](file:///c:/Users/sao/Documents/dofus-emu/desktop/client/www/standalone.html) providing a direct, fast bootstrapper.
- **Virtual Host Integration & Test**:
  - Added native Edge virtual origin mapping in [`desktop/WebView2ClientForm.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/WebView2ClientForm.cs):
    - `https://dofus.local/` maps directly to `desktop/client/www`.
    - Prevents file:// protocol limitations and CORS blocks while securing WebGL/WebSocket execution.
- **Verification via [`tests/test_dofus_webview2.ps1`](file:///c:/Users/sao/Documents/dofus-emu/tests/test_dofus_webview2.ps1)**:
  - Game client loaded into standalone WebView2.
  - Memory consumption: **97.5 MB** (over **90% reduction** compared to the 1,024–1,200 MB needed by Android QEMU).
  - Shutdown: **0 lingering processes**.
  - Blackbox mobile spoofing verified active.

### 6. Milestone 4 — Ankama Device Inspection Deep-Dive & Blackbox Coherence
- **Source Code Audit of Ankama Inspection**:
  - Found the exact device telemetry gatherer in [`build/script.js`](file:///c:/Users/sao/Documents/dofus-emu/desktop/client/www/build/script.js):
    1. `a.prototype.getDeviceInfo()`: Queries `platform`, `deviceMemory`, `hardwareConcurrency`, `maxTouchPoints`, `languages`, `appInfo.build`, and `__proto__` key count.
    2. `a.prototype.getGPUInformation()`: Queries `gl.VENDOR`, `gl.RENDERER`, `gl.VERSION`, `UNMASKED_VENDOR_WEBGL`, `UNMASKED_RENDERER_WEBGL`, and `gl.MAX_TEXTURE_SIZE`.
    3. Embedded FingerprintJS v3.4.0 (`wt`): Inspects `platform` (`ze()`), `videoCard` (`Qe()`), `pdfViewerEnabled` (`Ke()`), `canvas`, `audio`, `touchSupport`, and `userAgentData`.
- **Blackbox Spoofing Enhancements**:
  - Added `pdfViewerEnabled: false` to [`scripts/mobile-disguise.js`](file:///c:/Users/sao/Documents/dofus-emu/scripts/mobile-disguise.js) (mobile WebView standard).
  - Added synchronous `appInfo: { build: '4.3.15', version: '3.14.2', identifier: 'com.ankama.dofustouch' }` on `window.navigator`.
  - Added Chromium switches `--touch-events=enabled --disable-blink-features=AutomationControlled` in [`desktop/WebView2ClientForm.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/WebView2ClientForm.cs).
- **Automated Verification via [`tests/test_ankama_inspection.ps1`](file:///c:/Users/sao/Documents/dofus-emu/tests/test_ankama_inspection.ps1)**:
  - Verified live JSON telemetry extracted directly from the running client:
    - `platform`: `"Linux armv8l"` (100% Mobile Android)
    - `device.platform`: `"Android"` (Samsung SM-A515F, Android 10)
    - `uaData.platform`: `"Android"`, `uaData.mobile`: `true`
    - `gpu.vendor` / `gpu.unmaskedVendor`: `"ARM"`
    - `gpu.renderer` / `gpu.unmaskedRenderer`: `"Mali-G72 MP3"`
    - `gpu.maxTextureSize`: `8192`
    - `pdfViewerEnabled`: `false`
    - `webdriver`: `false`
    - `build`: `"4.3.15"`
    - **Zero leakage of Windows, Win32, or desktop signatures.**

### 7. Milestone 5 — Legacy Emulator Stripping & Ultra-Compact Packaging (921 MB -> 14.9 MB)
- **Decoupled Setup from Legacy Virtualization Bloat**:
  - In [`builder/build.cs`](file:///c:/Users/sao/Documents/dofus-emu/builder/build.cs), modified `BuildStandaloneSetup()` to package standalone WebView2 by default without embedding `sdk.7z` (797 MB) and `template.zip` (153 MB).
  - Legacy emulator bundling is preserved behind an opt-in `--include-legacy-emulator` flag or `INCLUDE_LEGACY_EMULATOR=1`.
- **Installer Modernization**:
  - In [`desktop/installer.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/installer.cs), added native standalone deployment path:
    - Automatically skips `emulator.exe` checks and 5-minute cold-run pre-warm cycles when running in standalone mode.
    - Provisions isolated game slots, device identities, and WebView2 profiles in under 2 seconds.
    - Configures `Software\DofusFarm\DefaultBackend = "WebView2"`.
- **Default Game Client Entry Point**:
  - In [`desktop/WebView2Backend.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/WebView2Backend.cs), auto-detects `client/www/standalone.html` to launch the game client directly instead of the capability probe.
- **Root Cleanup**:
  - Deleted redundant 15.5 MB `com.ankama.dofustouch.apk` from repository root (assets extracted into `client/www/`).
- **File Size & Speed Verification**:
  - `setup.exe`: **14.9 MB** (down from **921.8 MB**, a **98.4% reduction**).
  - Verified automated silent sandbox deployment with [`tests/test_sandbox_install.ps1`](file:///c:/Users/sao/Documents/dofus-emu/tests/test_sandbox_install.ps1) (exit code 0).

### 8. Verification Commands
```powershell
# 1. Full compilation and standalone release packaging (generates 14.9 MB setup.exe)
.\Build.exe

# 2. Automated WebView2 backend test suite (unit and multi-instance)
powershell -ExecutionPolicy Bypass -File .\tests\test_webview2.ps1

# 3. Real Dofus Touch client in standalone WebView2 test
powershell -ExecutionPolicy Bypass -File .\tests\test_dofus_webview2.ps1

# 4. Ankama exact device & WebGL inspection verification
powershell -ExecutionPolicy Bypass -File .\tests\test_ankama_inspection.ps1

# 5. Sandbox setup.exe silent installation test
powershell -ExecutionPolicy Bypass -File .\tests\test_sandbox_install.ps1
```
