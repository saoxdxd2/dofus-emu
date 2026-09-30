# dofus-emu

Custom Android virtual-instance farm for running **Dofus Touch** (an HTML5/WebGL
Cordova app) as multiple concurrent instances on a single x86_64 Windows host.

Status: **installer complete and tested; a reboot is required before the first
guest can boot** (hypervisor activation).

---

## Quick start

Double-click **`INSTALL.bat`** as Administrator. It installs everything,
creates the AVDs, and reports whether a reboot is needed. Then:

```powershell
.\scripts\instances.ps1                  # start one instance
.\scripts\verify-install.ps1             # diagnose any machine (read-only)
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

HP 250 G8 · Windows 10 IoT LTSC 19044 · **i5-1035G1 (4C/8T)** · **8 GB RAM** ·
Intel UHD iGPU. Java 25 works with `sdkmanager` 12.0.

---

## Phase 0 gates

1. **WebView availability** — AOSP ships only a *stub* WebView; a real Chromium
   WebView must be sideloaded. *(unverified)*
2. **WebView recency** — record the installed version. *(unverified)*
3. **`-gpu host` WebGL2** — **PASSED**: emulator logs
   `Found physical GPU 'Intel(R) UHD Graphics', apiVersion 1.3.212`.
   Runtime WebGL2 inside WebView is still unverified.
4. **Memory floor** — *(pending first successful boot)*

---

## Host prerequisites (the real blockers)

**1. Hypervisor — requires a reboot.** Without it the emulator exits with
`x86_64 emulation currently requires hardware acceleration!`

```powershell
dism /online /enable-feature /featurename:HypervisorPlatform /all /norestart
dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
```

The installer does this automatically and detects whether a reboot is needed.

**2. Wi-Fi band steering — optional but a large speedup.** A Realtek card
band-steering onto 2.4 GHz cost **423 KB/s** (measured). Setting
`Roaming Aggressiveness = 65` moved the association to 5 GHz and gave
`802.11ac / channel 48 / 433.3 Mbps` instead of `802.11n / channel 13 / 72.2`.

```powershell
Set-NetAdapterAdvancedProperty -Name 'Wi-Fi-2' `
  -RegistryKeyword 'RegROAMSensitiveLevel' -RegistryValue 65
Restart-NetAdapter -Name 'Wi-Fi-2'
```

> Correction: `PreferBand` accepts only **0, 1, 2** and is already `2`
> ("5G first") by default — no change needed. An earlier suggestion to set it to
> `3` was wrong. Likewise `Wireless Mode` has no "all modes" entry: driver value
> `7` already means a/b/g/n/ac, but the UI displays the nearest named option,
> "6. 802.11a/b/g".

---

## Installer architecture

`install.ps1` is idempotent — re-running skips anything already installed.

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
matters: single-stream `curl` managed only 220–400 KB/s and repeatedly died
mid-transfer, while `sdkmanager` throttled to ~1% per 5 minutes with a 0-byte
temp file.

Note the URL format: the `<url>` elements in Google's manifests are **bare
filenames** resolved against `https://dl.google.com/android/repository/`. The
emulator archive is *not* under `repository/emulator/` — that path returns 404
and yields a 1.4 KB HTML error page that fails to unzip.

| Artifact | Bytes |
|---|---|
| `platform-tools_r37.0.1-win.zip` | 8,044,989 |
| `emulator-windows_x64-16433917.zip` | 459,420,448 |
| `x86_64-29_r08-windows.zip` | 689,676,765 |

---

## Corrections made to the original spec

- **"Android Go (Low-RAM) Mode"** — Go Edition was Android 4.4 only. The real,
  still-honoured flag is `ro.config.low_ram=true`.
- **"Native ARM on an x86 CPU"** — no ARM hardware here. Native ARM means 10–50x
  software translation. x86_64 + a universal APK needs no binary-translation
  layer at all.
- **zRAM "compresses the Android framework 2:1"** — it does not. Framework
  pages are file-backed and clean, so they never reach swap. `zram0`'s backing
  store is also the guest's own RAM, so a 512 MB zram is not free memory — it is
  a compressed overflow area.
- **`swappiness 100`** — causes reclaim thrash. Set to **70**.
- **Deleting `SystemUI.apk` / IME from `/system/app/`** — risks boot loops and
  dead text input. Use `pm disable-user` for the app table.
- **`am start -n` as the "home" mechanism** — `am` is a shell tool, and
  `SurfaceFlinger` + `WindowManager` hold the display buffers regardless of the
  foreground app. `DofusLauncher` is a real `CATEGORY_HOME` activity, worth tens
  of MB, not the whole compositor.
- **768 MB per instance** — below the floor for a WebGL page; the Chromium
  renderer alone typically wants 200–400 MB.
- **4 instances on this host** — 4 x 1.5 GB does not fit in 8 GB alongside
  Windows. Realistic ceiling is **2 instances**.
- **`ro.*` is read-only after boot** — `setprop ro.config.low_ram true` fails
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

## Out of scope

Detection-evasion and anti-cheat circumvention are not implemented. That
includes spoofing a specific retail OEM's `ro.build.fingerprint` /
`ro.product.*`, masking emulator artifacts (`/dev/qemu_pipe`, `/dev/vboxguest`,
`/dev/goldfish*`) or removing `su` *in order to avoid being fingerprinted*, and
adding sensor "jitter" specifically so a security engine cannot spot a constant
reading.

The coherent-profile approach is also the *correct* engineering answer: claiming
an ARM SoC while running x86_64 on an Intel driver is internally inconsistent,
and that inconsistency is precisely what makes Cordova/WebView throw
unsupported-hardware errors.




---

## Host GPU bridging (how `-gpu host` works)

QEMU exposes a virtual GPU. With `-gpu host` the emulator loads the host's
**actual OpenGL driver** and the guest EGL/GLES3 stack issues GL calls through
the emulator's GLES bridge into the host driver, which executes them on the
Intel iGPU. Guest GLES3 context -> Intel hardware, no CPU rasterization.

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
the CPU. Chromium's renderer still runs JS, layout, and draw-call submission on
CPU. Expect a meaningful but partial win, not 4 instances at low load.

---

## Layout

```
config/avd/            AVD definition (hw.gpu.mode=host, 2 cores, 1536 MB)
launcher/              DofusLauncher: CATEGORY_HOME activity that starts the game
scripts/
  setup-sdk.bat        SDK bootstrap (licenses + package list)
  download-artifacts.bat  direct CDN download + unpack
  instances.ps1        instance runner + Gate 1/2/3 probes
  bench-memory.ps1     Gate 4 memory floor measurement
  apply-zram.sh        guest-side zRAM setup (lz4, swappiness 70)
```

## Usage

```powershell
# one instance
.\scripts\instances.ps1 -RamMb 1536

# memory floor measurement
.\scripts\bench-memory.ps1 -Levels 768,1024,1536
```

---

## Out of scope

Detection-evasion and anti-cheat circumvention are not implemented here. That
includes spoofing a specific retail OEM's `ro.build.fingerprint` /
`ro.product.*` to impersonate that device, masking emulator artifacts
(`/dev/qemu_pipe`, `/dev/vboxguest`, `/dev/goldfish*`) or removing `su`
*in order to avoid being fingerprinted*, and adding sensor "jitter" specifically
so a security engine cannot spot a constant reading. Per-instance MAC
isolation and zRAM are present as ordinary multi-instance and memory hygiene.

The coherent-profile approach is also the *correct* engineering answer:
claiming an ARM SoC while actually running x86_64 on an Intel driver is
internally inconsistent, and that inconsistency is precisely what makes
Cordova/WebView throw unsupported-hardware errors.
