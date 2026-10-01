# dofus-emu

Custom Android virtual-instance farm for running **Dofus Touch** (an HTML5/WebGL
Cordova app) as multiple concurrent instances on a single x86_64 Windows host.

Status: **first guest booted successfully.** All four Phase 0 gates run; Gate 4
measured. Remaining work is installing the game + a real Chromium WebView and
re-measuring with the game loaded.

---

## Phase 0 results (measured on this host)

First boot: **`Boot completed in 49441 ms`**, `Windows Hypervisor Platform
accelerator is operational`.

| Gate | Result |
|---|---|
| 1. WebView provider | **PARTIAL** â€” image ships real Chromium **74.0.3729.185** (`com.android.webview`, targetSdk 29) and it is the preferred provider. Chrome 74 (2019) is old for a modern WebGL game; a newer WebView should still be sideloaded. |
| 2. WebView recency | **74.0.3729.185** â€” see note above. |
| 3. `-gpu host` | **PASSED.** `GLES: Google (Intel), Android Emulator OpenGL ES Translator (Intel(R) UHD Graphics), OpenGL ES 2.0 (4.5.0 - Build 30.0.101.2079)` â€” real Intel driver, **no SwiftShader / llvmpipe**. `qemu.gles=1`, `ro.hardware.egl=emulation`. |
| 4. Memory floor | **Measured at 1536 MB**, see below. |

> Correction: I previously described the AOSP WebView as a bare *stub*. It is
> actually a real Chromium 74 build, and `dumpsys webviewupdate` reports it as
> the preferred provider. This is friendlier than predicted â€” but 74 predates
> most modern WebGL2-era web games, so upgrading it is still worth doing.

### Gate 4 memory (1536 MB guest, WebView shell loaded, game NOT installed)

```
MemTotal:   2,040,548K    (guest sees ~2 GB at -memory 1536)
AnonPages:    899,572K    <- the number that must fit
MemAvailable: 782,940K    <- mostly reclaimable page cache, NOT headroom
SwapTotal:  1,530,404K    (emulator's built-in swapfile)
```

Per-process PSS:

| Process | PSS |
|---|---|
| `org.chromium.webview_shell` | 80,809K |
| `zygote64` | 68,759K |
| `com.android.webview:sandboxed_process0` | 65,841K |
| `com.android.launcher3` | 59,564K |
| `zygote` | 44,129K |
| `surfaceflinger` | 28,519K |
| `com.android.webview:webview_service` | 16,379K |
| `webview_zygote` | 15,805K |

**WebView stack â‰ˆ 178 MB before the game loads.** `launcher3` (58 MB) disappears
once `DofusLauncher` becomes the HOME app â€” the first measurable win from our
own build. Host-side `qemu-system-x86_64` working set was ~1.9 GB.

This supports the earlier estimate that **768 MB is not viable** and that the
floor is ~1.5 GB; the game itself has not been loaded yet, so re-run
`bench-memory.ps1` with the APK installed to set the production value.


---

## Quick start

Double-click **`INSTALL.bat`** as Administrator. It installs everything,
creates the AVDs, and reports whether a reboot is needed. Then:

```powershell
.\scripts\verify-install.ps1             # diagnose any machine (read-only)
.\scripts\instances.ps1                  # start one instance
.\scripts\instances.ps1 -Count 2         # multiple (delegates to start-farm.ps1)
```

For a 4-instance farm (needs ~8 GB free RAM; see capacity note):

```powershell
.\install.ps1 -Count 4 -RamMb 1536
.\scripts\cluster-manager.ps1 -Action Provision -Count 4
```

---

## Locked architecture

| Decision | Value | Why |
|---|---|---|
| Target OS | **Android 10 (API 29), x86_64** | Oldest API still getting a reasonably current WebView |
| Graphics | **`-gpu host`** (no SwiftShader) | Rasterization on the host Intel UHD iGPU |
| Instance RAM | **measured** (default 1536 MB) | Gate 4 sets the floor; 768 MB is below it |
| Device profile | **coherent**, non-impersonating | Consistency prevents Cordova/WebView hardware errors |
| Emulator | 37.3.2.0, `-accel on` | `-accel` accepts only on/off/auto; `hvm` is rejected |

### Host

HP 250 G8 Â· Windows 10 IoT LTSC 19044 Â· **i5-1035G1 (4C/8T)** Â· **8 GB RAM** Â·
Intel UHD iGPU. Java 25 works with `sdkmanager` 12.0.

---

## Known limitations (verified, not assumed)

### `ro.config.low_ram` cannot be set on this image

This is the Android-Go-style flag from the original spec, and it is **not
reachable at runtime here**. It is in the `ro.*` namespace, so `setprop` fails
with *Access denied*, and every route into `/system/build.prop` was tried:

| Approach | Result |
|---|---|
| `-writable-system` + `adb remount` | Emulator logs `System image is writable`, but the **guest then hangs at boot** — adb stays `device offline`, qemu CPU near-idle (288 s CPU over ~5 min). Reproduced at 1536 MB and again at 1024 MB with 4.3 GB free RAM, so not memory pressure. |
| `emulator -prop ro.config.low_ram=true` | Boots normally, but `getprop ro.config.low_ram` is **empty** afterwards — the emulator does not inject arbitrary `ro.*` values into this image. |
| `mount -t overlay` over `/system` from adb root | `mount: 'overlay'->'/mnt': Invalid argument` — the upper dir needs an SELinux label adb's context cannot set. |

Root cause: this image is **system-as-root**. `/` is a read-only ext4 (`dm-2`)
containing `/system`, `/product` and `/vendor`, so `build.prop` is not writable
live.

**Consequence:** a permanent `ro.config.low_ram` requires an **offline edit of
`system.img`** (unpack → edit `build.prop` → repack). `patch-system.ps1`
therefore applies only the runtime-settable `dalvik.vm.*` properties, which is
where most of the saving actually is, and reports `ro.config.low_ram` as unset
rather than pretending otherwise.

### The 768 MB / 1.5 GB question is still open

The earlier "1.5 GB floor" claim was **methodologically wrong** and has been
withdrawn. It rested on `MemAvailable 691 MB`, but `MemAvailable` is largely
*reclaimable page cache* — Linux fills spare RAM with cache, so it says nothing
about headroom. The measurement was also taken on a completely untrimmed guest.

`bench-memory.ps1` has been rewritten to fix this. It now auto-detects APKs
from `apks\`, applies what trimming is actually possible, and reports:

- **`AnonPages`** — anonymous memory, which genuinely has to fit
- **page cache separately** — reclaimable, so it must not count as "used"
- **the guest's real `MemTotal`** — not the requested `-memory`, because the
  emulator reports more than asked (`-memory 1536` → `MemTotal 2,040,548K`)
- **pressure events** (`lowmemorykiller`, `oom-kill`, game death) — the real
  floor signal, rather than a RAM counter

The floor is then the smallest tested level whose status is `OK`.

Note the last real numbers, for reference (1536 MB, untrimmed, WebView shell,
no game): `AnonPages 899,572 kB`, `MemAvailable 782,940 kB`, `SwapTotal
1,530,404 kB`.

---

## Host prerequisites (the real blockers)

**1. Hypervisor â€” requires a reboot.** Without it the emulator exits with
`x86_64 emulation currently requires hardware acceleration!`

```powershell
dism /online /enable-feature /featurename:HypervisorPlatform /all /norestart
dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
```

The installer does this automatically and detects whether a reboot is needed.

**2. Wi-Fi band steering â€” optional but a large speedup.** A Realtek card
band-steering onto 2.4 GHz cost **423 KB/s** (measured). Setting
`Roaming Aggressiveness = 65` moved the association to 5 GHz and gave
`802.11ac / channel 48 / 433.3 Mbps` instead of `802.11n / channel 13 / 72.2`.

```powershell
Set-NetAdapterAdvancedProperty -Name 'Wi-Fi-2' `
  -RegistryKeyword 'RegROAMSensitiveLevel' -RegistryValue 65
Restart-NetAdapter -Name 'Wi-Fi-2'
```

> Correction: `PreferBand` accepts only **0, 1, 2** and is already `2`
> ("5G first") by default â€” no change needed. An earlier suggestion to set it to
> `3` was wrong. Likewise `Wireless Mode` has no "all modes" entry: driver value
> `7` already means a/b/g/n/ac, but the UI displays the nearest named option,
> "6. 802.11a/b/g".

---

## Installer architecture

`install.ps1` is idempotent â€” re-running skips anything already installed.

| Stage | What it does |
|---|---|
| 1. Host prereqs | VT-x check, enables hypervisor features, tunes Wi-Fi |
| 2. SDK | cmdline-tools, licenses, then `download-artifacts.bat` for the rest |
| 3. AVDs | `avdmanager create avd` + writes `hw.gpu.mode=host` into config.ini |
| 4. Verify | component presence + `emulator -accel-check` |
| 5. Summary | reports failures and whether a reboot is required |

### Things the installer does that a naive install misses

- **Writes `emulator\package.xml`.** The official emulator zip ships
  `source.properties` but *not* `package.xml`, which `sdkmanager` would normally
  generate. Installing by unzipping leaves the SDK scanner unaware of the
  emulator, and `avdmanager` fails with
  `Error: "emulator" package must be installed!`. The XML must match the SDK
  schema exactly (full namespace list + a `<license>` node) or it is rejected as
  `Invalid package.xml`.
- **Writes SDK license files directly.** Piping `y` into `sdkmanager`'s prompt
  does not work reliably on Windows; it reports "license is not accepted" even
  with input available.
- **Verifies archive byte sizes exactly.** A truncated download previously
  produced a confusing downstream failure.
- **Extracts archives into the SDK *root*.** Each zip contains its own
  top-level folder (`emulator/`, `platform-tools/`, `x86_64/`), so extracting
  into a pre-made target directory nests them incorrectly. The system image's
  `x86_64/` folder is then moved into `system-images\android-29\default\`.

### Downloads

`download-artifacts.bat` uses **aria2c with 16 parallel streams** per file. This
matters: single-stream `curl` managed only 220â€“400 KB/s and repeatedly died
mid-transfer, while `sdkmanager` throttled to ~1% per 5 minutes with a 0-byte
temp file.

Note the URL format: the `<url>` elements in Google's manifests are **bare
filenames** resolved against `https://dl.google.com/android/repository/`. The
emulator archive is *not* under `repository/emulator/` â€” that path returns 404
and yields a 1.4 KB HTML error page that fails to unzip.

| Artifact | Bytes |
|---|---|
| `platform-tools_r37.0.1-win.zip` | 8,044,989 |
| `emulator-windows_x64-16433917.zip` | 459,420,448 |
| `x86_64-29_r08-windows.zip` | 689,676,765 |

---

## Corrections made to the original spec

- **"Android Go (Low-RAM) Mode"** â€” Go Edition was Android 4.4 only. The real,
  still-honoured flag is `ro.config.low_ram=true`.
- **"Native ARM on an x86 CPU"** â€” no ARM hardware here. Native ARM means 10â€“50x
  software translation. x86_64 + a universal APK needs no binary-translation
  layer at all.
- **zRAM "compresses the Android framework 2:1"** â€” it does not. Framework
  pages are file-backed and clean, so they never reach swap. `zram0`'s backing
  store is also the guest's own RAM, so a 512 MB zram is not free memory â€” it is
  a compressed overflow area.
- **`swappiness 100`** â€” causes reclaim thrash. Set to **70**.
- **Deleting `SystemUI.apk` / IME from `/system/app/`** â€” risks boot loops and
  dead text input. Use `pm disable-user` for the app table.
- **`am start -n` as the "home" mechanism** â€” `am` is a shell tool, and
  `SurfaceFlinger` + `WindowManager` hold the display buffers regardless of the
  foreground app. `DofusLauncher` is a real `CATEGORY_HOME` activity, worth tens
  of MB, not the whole compositor.
- **768 MB per instance** â€” below the floor for a WebGL page; the Chromium
  renderer alone typically wants 200â€“400 MB.
- **4 instances on this host** â€” 4 x 1.5 GB does not fit in 8 GB alongside
  Windows. Realistic ceiling is **2 instances**.
- **`ro.*` is read-only after boot** â€” `setprop ro.config.low_ram true` fails
  with *Access denied*, and zygote reads the value at startup, so it must be in
  `/system/build.prop` before boot. `patch-system.ps1` handles this via
  `-writable-system` + `adb remount`; a reboot activates it.

---

## Layout

```
INSTALL.bat               double-click entry point (self-elevates)
install.ps1               full installer (idempotent, 5 stages)
config/avd/               reference AVD definition
launcher/                 DofusLauncher: CATEGORY_HOME activity that starts the game
scripts/
  verify-install.ps1      read-only environment diagnostic
  download-artifacts.bat  aria2c 16-stream SDK download + unpack
  setup-sdk.bat           SDK bootstrap (licenses + package list)
  instances.ps1           instance runner + Gate 1/2/3 probes
  patch-system.ps1        writes ro.config.low_ram to build.prop via remount
  apply-profile.ps1       low-RAM + zRAM + identity, per instance
  apply-zram.sh           guest-side zRAM (lz4, swappiness 70)
  bench-memory.ps1        Gate 4 memory floor measurement
  cluster-manager.ps1     multi-instance coordinator
  start-farm.ps1          simple multi-instance launcher
  measure-fps.ps1         per-instance CPU/RSS sampling
  build-launcher.bat      Gradle-free APK build (aapt2 + d8)
```

---

## Host GPU bridging (how `-gpu host` works)

QEMU exposes a virtual GPU. With `-gpu host` the emulator loads the host's
**actual OpenGL driver**, and the guest EGL/GLES stack issues GL calls through
the emulator's GLES bridge into that driver, so they execute on the Intel iGPU.

Verified on this host via `dumpsys SurfaceFlinger`:

```
GLES: Google (Intel), Android Emulator OpenGL ES Translator
      (Intel(R) UHD Graphics), OpenGL ES 2.0 (4.5.0 - Build 30.0.101.2079)
```

| Mode | Where GL runs | Use here |
|---|---|---|
| `host` | host iGPU via real driver | **yes** |
| `swiftshader_indirect` | host CPU, multithreaded | no — CPU-bound |
| `guest` | software GLES inside guest | no — slowest |
| `off` | none | no — WebGL will not init |
| `angle_indirect` | *removed* (emulator 33+) | n/a |

`virgl` is **not** applicable — that is QEMU/KVM + `virtio-gpu` for Linux
guests (Waydroid-style), not the Android Emulator.

**Caveat:** offloading rasterization does not move the game's main thread off
the CPU. Chromium's renderer still runs JS, layout and draw submission on CPU.
Expect a real but partial win — not 4 instances at low load.

---

## Out of scope

Detection-evasion and anti-cheat circumvention are not implemented. That
includes spoofing a specific retail OEM's `ro.build.fingerprint` /
`ro.product.*` to impersonate that device, masking emulator artifacts
(`/dev/qemu_pipe`, `/dev/vboxguest`, `/dev/goldfish*`) or removing `su`
*in order to avoid being fingerprinted*, and adding sensor "jitter"
specifically so a security engine cannot spot a constant reading.

The coherent-profile approach is also the *correct* engineering answer: claiming
an ARM SoC while running x86_64 on an Intel driver is internally inconsistent,
and that inconsistency is precisely what makes Cordova/WebView throw
unsupported-hardware errors.


