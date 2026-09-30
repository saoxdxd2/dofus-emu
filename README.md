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
