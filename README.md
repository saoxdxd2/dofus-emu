# dofus-emu

Custom Android virtual-instance farm for running **Dofus Touch** (an HTML5/WebGL
Cordova app) as multiple concurrent instances on a single x86_64 Windows host.

## Downloads & Code Signing
- **Downloads**: Download the latest release from the [GitHub Releases page](https://github.com/saoxdxd2/dofus-emu/releases).
- **Code Signing**: Free code signing provided by the [SignPath Foundation](https://signpath.org).

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

> **CORRECTION (supersedes the paragraph above).** The "768 MB is not viable /
> floor is ~1.5 GB" claim was **wrong and is withdrawn.** It rested on reading
> `MemAvailable 691 MB` as headroom, but `MemAvailable` is largely *reclaimable
> page cache* — Linux fills spare RAM with cache, so a large value proves
> nothing. The measurement also came from a completely untrimmed guest
> (`ro.config.low_ram` unavailable, Launcher3 resident, zram off).
>
> The floor is **undetermined**. Run `bench-memory.ps1` with the game installed
> to establish it; the harness now reports `AnonPages` (must fit), page cache
> separately (reclaimable), the guest's real `MemTotal` (which exceeds
> `-memory`; `-memory 1536` yields `MemTotal 2,040,548K`), and pressure events
> (`lowmemorykiller` / `oom-kill` / game death) as the real signal.


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

## Verified results (2026-10-01, this host)

HP 250 G8 · i5-1035G1 · 8 GB · Intel UHD · API 29 x86_64 · `-gpu host`

| | Value |
|---|---|
| Boot time | ~47 s (`Boot completed in 46849 ms`), WHPX acceleration |
| GPU | `GLES: Google (Intel), Android Emulator OpenGL ES Translator (Intel(R) UHD Graphics), OpenGL ES 3.0 (4.5.0)` — hardware, no SwiftShader |
| **Production floor** | **1024 MB** (locked into `instances.ps1` / `start-farm.ps1`) |
| 768 MB | Viable only with the UI strip. `SwapFree` falls to ~20 MB of 564 MB (~96% saturated), so no headroom for texture churn. |
| 1024 MB | Game + stripped OS fit in uncompressed RAM; zRAM stays a safety net. |

Measured at 1024 MB, game running, full trim applied:

```
MemTotal      1,009,896 kB      MemAvailable   340,040 kB
AnonPages       335,820 kB      SwapFree       416,936 kB / 757,416 kB
```

Game chain ≈ **278 MB** (Chromium sandbox ~146 MB + app ~92 MB + webview_zygote).

### The single biggest win: stripping the UI layer

Before this, a trimmed 768 MB guest still died at launch:

```
lowmemorykiller: Reclaimed 0kB, cache(142992kB) and free(202624kB)
-reserved(68980kB) below min(221184kB) for oom_adj 900
Process com.ankama.dofustouch (pid 4566) has died
```

Three processes were consuming **~135 MB of PSS that a fullscreen immersive
WebGL game never draws**:

| Process | PSS |
|---|---|
| `com.android.systemui` | 56,136 kB |
| `com.android.launcher3` | 53,968 kB |
| `com.android.inputmethod.latin` | 25,369 kB |

After `pm disable-user` on all three plus `pkill systemui`, the game runs
stably at 768 MB and comfortably at 1024 MB. **SurfaceFlinger is deliberately
left running** (~21 MB): stopping it tears down the display transport adb rides
on, and the guest drops "offline" for the rest of the trim sequence.

---

## Golden image: one trim, N instances

Everything instance-scoped lives in `/data`, not in the read-only
`system.img`, so it can be captured once:

```
pm disable-user  ->  /data/system/users/0/package-restrictions.xml
settings put      ->  /data/system/users/0/settings_secure.xml
device_config put ->  /data/system/users/0/device_config.xml
appops set        ->  /data/system/users/0/appops.xml
HOME selection    ->  /data/system/users/0/package-restrictions.xml
installed APKs    ->  /data/app/*
dexopt artifacts  ->  /data/dalvik-cache/*
```

```powershell
# once
.\scripts\make-golden-image.ps1 -RamMb 1024      # -> userdata-golden.img (~80 MB qcow2)
```

New instances copy that file over their `userdata-qemu.img.qcow2` before first
boot. `cluster-manager.ps1 -Action Create` does this automatically.

Two subtleties that are easy to get wrong:

- **Capture the overlay, not the raw disk.** A running emulator writes to
  `userdata-qemu.img.qcow2` and leaves `userdata-qemu.img` as an untouched
  factory image. Copying the raw file yields a "golden image" that is silently
  empty. `make-golden-image.ps1` flattens overlay + backing with
  `qemu-img convert` instead.
- **The output must be qcow2, not raw.** The emulator's bundled `qemu-img` has a
  32-bit write path and fails with `Input/output error` at exactly byte
  2147483648 (2 GiB) when emitting raw. The flattened image is therefore a
  self-contained qcow2 of ~80 MB rather than a 6 GB raw disk.

**What the golden image cannot capture** — kernel/init runtime state, gone on
every reboot, so `scripts\boot-instance.ps1` re-applies it in a few seconds:

1. zRAM + `vm.swappiness` / `vm.page-cluster` / `vm.vfs_cache_pressure`
2. init service stops (statsd, traced, traced_probes, incidentd, rild,
   cameraserver, drmserver)
3. anything under `/sys`

That split is the whole design: **the slow, fragile half is captured once;
the fast, stable half runs per boot.** Verified — a cold boot from the golden
image brought up Dofus Touch focused on `MainActivity` with 22 packages
disabled and **zero trim commands**.

---

## Multi-instance farm

```powershell
# 1. provision (one AVD per instance, each seeded from the golden image)
.\scripts\cluster-manager.ps1 -Action Create -Count 2 -RamMb 1024 -Cores 2

# 2. launch + auto-arrange
.\scripts\start-farm.ps1 -Count 2
```

Each instance gets its **own AVD** (`dofus-01`, `dofus-02`, ...), its own
console/adb port pair and a stable MAC:

| Instance | Console | adb serial | MAC |
|---|---|---|---|
| dofus-01 | 5554 | emulator-5554 | 52:54:00:00:00:01 |
| dofus-02 | 5556 | emulator-5556 | 52:54:00:00:00:02 |
| dofus-03 | 5558 | emulator-5558 | 52:54:00:00:00:03 |
| dofus-04 | 5560 | emulator-5560 | 52:54:00:00:00:04 |

Separate AVDs are mandatory — two emulators sharing one AVD share one
`/data` and will corrupt each other.

### Adaptive window layout

`scripts\layout.ps1` places the windows with Win32 `MoveWindow`:

| Count | Layout |
|---|---|
| 1 | single maximised window |
| 2 | side by side |
| 3 | two on top, one full-width below |
| 4 | clean 2x2 quad |
| 5+ | even grid, tiles fill the work area exactly |

Geometry comes from the **host** at runtime (`Screen.WorkingArea`), so it adapts
to any monitor instead of assuming a resolution. Re-apply at any time with the
GUI's **Re-layout** button or `Set-FarmLayout`.

> The emulator is launched minimised. Windows must be restored
> (`SW_RESTORE`) before `MoveWindow`, otherwise the call succeeds but the window
> stays parked at `-32000,-32000` and the farm looks like it never launched.

### Which window to move

The emulator exposes **two visible top-level windows**, and only one is the game:

| Process | Class | What it is |
|---|---|---|
| `emulator.exe` | `ConsoleWindowClass` | the **log console** — hidden automatically |
| `qemu-system-x86_64.exe` | `Qt...QWindowIcon` ("Android Emulator - …") | the **device** — this is what gets tiled |

`Get-RenderWindow` walks the launcher process tree and picks the Qt window.
Using `MainWindowHandle` instead returns the console, which is why an earlier
version resized the log window and left the game untouched.

### Everything is derived from the host

No hardcoded machine constants. `start-farm.ps1` reads RAM and CPU from
`Win32_ComputerSystem` and derives guest RAM, cores and a sustainable instance
count:

- **guest RAM**: 1024 MB on small hosts, 1536 MB on 16 GB, 2048 MB on 32 GB+
- **cores**: `min(4, (hostCores - 2) / instances)`, leaving the host headroom
- **count**: bounded by free RAM (after a 2.5–4 GB host reserve) and by CPU

Override anything explicitly, or use `-Auto` to clamp to what the host can
sustain. The same capacity logic drives the GUI's instance hint.

### Verified

Two instances (`dofus-01`, `dofus-02`) booted concurrently from the golden
image and stayed stable: both with the game focused on `MainActivity`, 22
packages disabled each, windows placed at `0,0 681x768` and `685,0 681x768`.

---

## CPU budget

```powershell
.\scripts\cpu-budget.ps1 -Serial emulator-5554 -Measure
```

### Verified ABI

The game runs **natively on x86_64** — `oat/x86_64`, `primaryCpuAbi=null`, and
no `libndk_translation` / houdini packages installed. There is no ARM→x86
translation layer contributing overhead.

### Frame cap

`qemu.vsync = 30` (and `hw.lcd.vsync = 30`) is honoured. Confirmed in the guest:

```
dumpsys SurfaceFlinger -> VSYNC period: 33333333 ns   (= exactly 30 FPS)
```

Dofus Touch is turn-based isometric, so a 60 FPS loop redraws an unchanged
scene. Note `debug.sf.fps` / `debug.choreographer.fps` are **debug properties
and are ignored on a user build** — `cpu-budget.ps1` reports that rather than
pretending the `setprop` did something.

### Measured, on 1 vCPU

| Config | Host CPU |
|---|---|
| 2 vCPUs (before) | **215%** |
| 1 vCPU `-smp 1` + audio off + vsync 30 | **~115% of the qemu process** |

Guest-side on 1 vCPU: `100%cpu 25%user 4%nice 61%sys 11%idle`, with the top
consumer at ~3% (`zygote64`). The game itself is *not* CPU-bound.

**Remaining bottleneck: kernel (`sys`) time is ~50-60% of the single vCPU.**
That is the emulator's host/guest boundary — every frame crosses the virtio
pipe and is translated by QEMU's TCG, and the glpipe/gfxstream translator
calls back into the host. It is not JavaScript, not WebView raster and not an
ARM translation layer. With WHPX the vCPU work is hardware-accelerated, but
the I/O and GL translation paths still consume guest kernel time.

`-cpu host` is **not usable** here: it prevents WHPX from attaching and the
guest never comes up (no adb device). It is exposed as `-HostCpu` but defaults
off for that reason.

### What the budget script does

1. verifies the ABI is native x86_64 (and flags a regression)
2. stops the audio service
3. ignores `RUN_IN_BACKGROUND` for `com.android.phone`
4. reports the vsync cap (applies on next boot)
5. writes `/data/local/tmp/webview-command-line` with
   `--enable-gpu-rasterization --ignore-gpu-blocklist`
6. confirms the host GLES renderer

`--enable-zero-copy` is deliberately omitted: it only applies where a working
dma-buf path exists and is ignored elsewhere, so including it would look
configured while doing nothing.

---

## Bootstrap a clean clone

```bat
setup.bat
```

Downloads Google's `commandlinetools-win`, installs `platform-tools`,
`emulator` and `system-images;android-29;default;x86_64` into `<repo>\sdk`
(live progress + ETA), writes the SDK license hashes, builds the golden image
and runs `verify-install.ps1`. Flags: `-SkipGolden`, `-SkipVerify`,
`-ForceDownload`. Re-running is safe — each stage is skipped if satisfied.

GUI alternative: `.\scripts\gui-manager.ps1` — instance table with live state,
RAM tier picker, Create/Delete, Launch Farm, Stop All, Re-layout.

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

### Low-RAM is applied at runtime instead

Since `ro.config.low_ram` is unavailable, the savings come from runtime
configuration (`patch-system.ps1`, and per-level in `bench-memory.ps1`):

| Setting | Command | Verified |
|---|---|---|
| Cached process cap | `device_config put activity_manager max_cached_processes 2` | reads back `2` |
| Animations | `settings put global {window,transition,animator}_*_scale 0` | reads back `0` |
| ART heap cap | `setprop dalvik.vm.heapgrowthlimit 192m` | reads back `192m` |
| Bloat packages | `pm disable-user` on printspooler, wallpaper livepicker, dreams.basic | disabled |
| Home app | `DofusLauncher` replaces Launcher3 | installed, HOME set |
| zram | `apply-zram.sh` (lz4, swappiness 70) | `/dev/block/zram0` active in `/proc/swaps` |

> `cmd activity set-process-limit` **does not exist** on API 29 — it answers
> `Unknown command` (confirmed against `cmd activity help` on this image). The
> supported Android 10 mechanism is the `device_config` overlay above.
>
> `device_config` is a **volatile** overlay: it survives a normal reboot but is
> reset by a factory reset / `-wipe-data`. `bench-memory.ps1` therefore re-applies
> the entire trim block on every level, since each level boots with `-wipe-data`.

### `DofusLauncher` had a HOME restart loop (fixed)

Worth recording because it *inverted* the low-RAM goal. The first working build
replaced Launcher3 and memory went **up**. Cause: this activity is registered as
HOME, and **when a HOME activity calls `finish()`, ActivityManager immediately
starts the default HOME again — the same activity — forever.** Measured 576
retries in 15 s, driving the process to ~130 MB RSS.

Fix: on a **failed** launch, do not `finish()`; stay resident but inert, so HOME
is already satisfied and nothing re-triggers. On a **successful** launch,
`finish()` as before. Verified after the fix: **1 retry instead of 576.** This
path is only reachable when the game APK is absent, so it degrades quietly
instead of pegging the CPU.

Baseline vs trimmed (1536 MB guest, no game installed):

| | Used RAM | Notes |
|---|---|---|
| Untrimmed | 1,256,428K | `launcher3` resident at 55,686K |
| Trimmed | 1,265,356K | Launcher3 gone, but launcher resident (no game yet) |

These are **not** comparable as a saving yet: without the game installed the
launcher stays resident by design (per the fix above). The real comparison
requires `dofustouch.apk` installed, which is what Gate 4 is for.


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


