# Dofus Farm: Master Index

## Architectural Overview
Dofus Farm is transitioning from an Android QEMU emulator farm toward a Windows-native Microsoft Edge WebView2 client architecture, maintaining dual-backend capability during migration.

```
+-----------------------------------------------------------------------------------+
|                           Dofus Farm Desktop App                                  |
|                             (desktop/mainform.cs)                                 |
+-----------------------------------------------------------------------------------+
                                         |
                               [IClientBackend Interface]
                                         |
                 +-----------------------+-----------------------+
                 |                                               |
     [⚡ WebView2Backend]                             [🤖 EmulatorBackend]
     (WebView2ClientForm.cs)                           (FarmEngine.cs / QEMU)
                 |                                               |
+---------------------------------+             +---------------------------------+
| Microsoft Edge WebView2 Core    |             | Android AOSP 10 Ranchu Guest    |
| ANGLE Direct3D 11 WebGL 1.0/2.0 |             | libGLESv2 / QEMU x86_64 Host    |
| ~328 MB WS / 165 MB Commit      |             | ~884 MB WS / 760 MB Commit      |
+---------------------------------+             +---------------------------------+
                 \                                               /
                  +----------------------+----------------------+
                                         |
                            [scripts/dofus-net-proxy.exe]
                            Port 8880 Local HTTP/WS Proxy
                            1,918 req/s | 2.98 ms latency
```

---

## Documentation Index
- [`PROJECT_MEMORY.md`](file:///c:/Users/sao/Documents/dofus-emu/PROJECT_MEMORY.md) — Architectural invariants, engine choices, memory baselines, and security models.
- [`TASK_MEMORY.md`](file:///c:/Users/sao/Documents/dofus-emu/TASK_MEMORY.md) — Implementation log, benchmarks, test commands, and open work items.
- [`webview2_migration_and_compatibility_report.md`](file:///C:/Users/sao/.gemini/antigravity/brain/c949f713-df27-4cee-a43f-ff3a883d0c89/webview2_migration_and_compatibility_report.md) — Formal migration milestone 1 assessment report.

---

## Core Components
- **UI & Lifecycle**: [`desktop/mainform.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/mainform.cs)
- **Backend Abstraction**: [`desktop/IClientBackend.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/IClientBackend.cs)
- **WebView2 Backend**: [`desktop/WebView2Backend.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/WebView2Backend.cs) & [`desktop/WebView2ClientForm.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/WebView2ClientForm.cs)
- **Emulator Backend**: [`desktop/EmulatorBackend.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/EmulatorBackend.cs) & [`desktop/farmengine.cs`](file:///c:/Users/sao/Documents/dofus-emu/desktop/farmengine.cs)
- **Network Proxy**: [`scripts/dofus-net-proxy.go`](file:///c:/Users/sao/Documents/dofus-emu/scripts/dofus-net-proxy.go)
- **Packaging & Compilation**: [`builder/build.cs`](file:///c:/Users/sao/Documents/dofus-emu/builder/build.cs) & [`compile.bat`](file:///c:/Users/sao/Documents/dofus-emu/compile.bat)
- **Standalone Game Client Bundle**: [`desktop/client/www/`](file:///c:/Users/sao/Documents/dofus-emu/desktop/client/www/) (`standalone.html`, `build/script.js`, `build/styles-native.css`, `js/cordova-standalone-shim.js`)
- **Automated Tests**: [`tests/test_webview2.ps1`](file:///c:/Users/sao/Documents/dofus-emu/tests/test_webview2.ps1), [`tests/test_dofus_webview2.ps1`](file:///c:/Users/sao/Documents/dofus-emu/tests/test_dofus_webview2.ps1), [`tests/test_ankama_inspection.ps1`](file:///c:/Users/sao/Documents/dofus-emu/tests/test_ankama_inspection.ps1), [`tests/test_sandbox_install.ps1`](file:///c:/Users/sao/Documents/dofus-emu/tests/test_sandbox_install.ps1) & [`scripts/benchmark_proxy.go`](file:///c:/Users/sao/Documents/dofus-emu/scripts/benchmark_proxy.go)
