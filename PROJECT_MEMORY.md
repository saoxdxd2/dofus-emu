# Dofus Farm: Project Memory

## Strategic Architecture
1. **Target Runtime Model**:
   - Primary: C# WinForms host (.NET Framework 4.8 / Roslyn C# 6+) running Microsoft Edge WebView2 controls.
   - Graphics: Direct3D 11 via ANGLE, WebGL 1.0 & 2.0 with hardware acceleration.
   - Storage/Profile Isolation: Per-instance user-data directory in `%LOCALAPPDATA%\DofusFarm\webview2_profiles\<instanceId>`.
   - Secondary / Fallback: QEMU x86_64 Android AOSP 10 Ranchu emulator guest with ADB command bridge.

2. **Network Proxy Retention**:
   - `scripts/dofus-net-proxy.go` benchmarked independently at 1,918 req/sec, 2.98 ms average latency, and 15.8 MB peak working set.
   - Retained as a modular caching layer for both WebView2 and Emulator modes.
   - Avoids extra proxy hops; supports SOCKS5 and direct upstream.

3. **Performance Baselines (Measured on Host Intel UHD Graphics)**:
   - Standalone WebView2 (1 instance): ~99.8 MB to ~328 MB working set.
   - Incremental cost (2nd instance): **+101.4 MB to +318.3 MB working set**.
   - Cold boot: **< 1.5 seconds** (vs 18–45 seconds for emulator).
   - Shutdown: 0 lingering background processes.

4. **Security & Boundary Rules**:
   - No arbitrary process execution exposed to web context.
   - Only required C# messaging hooks (`postMessage` / `WebMessageReceived`).
   - Outbound HTTP/WS requests keep TLS validation enabled.
   - Navigation restricted to `http://`, `https://`, and `file://`.

5. **CPU Governance & Mobile Blackbox Invariants**:
   - **No Custom C++ Needed**: WebView2 is already compiled native C++ (Blink/V8/ANGLE).
   - **Host CPU Governance**: `PriorityGovernor` enforces `AboveNormal` priority on the active window's browser and renderers, while background instances are dialed down to `BelowNormal` priority with Windows EcoQoS (`PROCESS_POWER_THROTTLING_EXECUTION_SPEED`) and Chromium background timer throttling.
   - **100% Closed Mobile Blackbox**: `mobile-disguise.js` runs synchronously before document scripts execute (`AddScriptToExecuteOnDocumentCreatedAsync`), enforcing ARM Mali / Qualcomm Adreno GPU vendor/renderer strings, mobile Linux user agent, max texture sizes, touch points, battery, sensor events, and Cordova object stubs.

6. **Standalone Client Assets & Cordova Emulation**:
   - Web Assets location: `desktop/client/www/`.
   - Entry point: `standalone.html` (served via virtual origin `https://dofus.local/`).
   - Core JavaScript Engine: `build/script.js` (5.39 MB) and `build/styles-native.css` (1.20 MB).
   - Remote Gateways: `https://dt-proxy-production-login.ankama-games.com`.
   - Shim: `js/cordova-standalone-shim.js` completely eliminates Android Cordova runtime dependency while preserving all required client interfaces.
