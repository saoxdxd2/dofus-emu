# apks/

Drop APKs here. `bench-memory.ps1` and `instances.ps1` auto-detect them by
name — no path arguments needed.

| Filename | Purpose |
|---|---|
| `dofustouch.apk` | the game (required for Gate 4 to measure real load) |
| `webview.apk` | optional newer Chromium WebView; the image already ships 74.0.3729.185 |
| `DofusLauncher.apk` | optional; built by `scripts\build-launcher.bat` |

Expected names (first match wins):

```
apks\dofustouch.apk
apks\*.apk          (if exactly one game APK is present)
```

Anything in this folder is ignored by git (see `.gitignore`) so the APKs are
not redistributed by accident.

## Note on the WebView

The API 29 x86_64 image already contains a real Chromium WebView
(`com.android.webview`, version **74.0.3729.185**, targetSdk 29) and
`dumpsys webviewupdate` reports it as the preferred provider. It is not a stub.

74 is from 2019, which predates much of the modern web platform. If the game
misbehaves, place a newer `webview.apk` here. It must be built for **x86_64**
(the `arm64-v8a` builds will not install on this image) and must declare
`minSdkVersion <= 29`.
