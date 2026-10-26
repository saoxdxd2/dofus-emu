# DofusFarm

A Windows host that runs **Dofus Touch** as a real browser client, relays every
packet through a local proxy that never leaks your address, and drives the game
with a small language model plus a deterministic planner — including an
autonomous loop that chooses its own actions and labels the outcome.

Nothing here is affiliated with Ankama. It is a personal automation project: the
client is the real one, the account is yours, and everything it does is something
a human could do by hand. Use it at your own risk, on accounts you are willing to
lose.

---

## Table of contents

1. [What it is, precisely](#1-what-it-is-precisely)
2. [Quick start](#2-quick-start)
3. [Architecture at a glance](#3-architecture-at-a-glance)
4. [Repository map](#4-repository-map)
5. [The host application](#5-the-host-application)
6. [The client, and how it is disguised](#6-the-client-and-how-it-is-disguised)
7. [The updater (and why updates used to break everything)](#7-the-updater-and-why-updates-used-to-break-everything)
8. [The network layer](#8-the-network-layer)
9. [The AI stack](#9-the-ai-stack)
10. [The agent loop and the second agent](#10-the-agent-loop-and-the-second-agent)
11. [Data: what is recorded and where](#11-data-what-is-recorded-and-where)
12. [Build and release](#12-build-and-release)
13. [Configuration reference](#13-configuration-reference)
14. [Troubleshooting](#14-troubleshooting)
15. [Tests and self-checks](#15-tests-and-self-checks)
16. [Known limits](#16-known-limits)
17. [Documentation index](#17-documentation-index)
18. [Third-party components](#18-third-party-components)

---

## 1. What it is, precisely

Five things, in one process tree:

| # | thing | runs as | what it does |
|---|---|---|---|
| 1 | **Host** (`DofusFarm.exe`) | WinForms, one process | instance grid, AI panel, settings, shortcuts, logs |
| 2 | **Client** (WebView2) | one child window per instance | the real Dofus Touch bundle, served from disk, running in Edge's engine |
| 3 | **Relay** (`dofus-net-proxy.exe`) | one child process per instance | HTTP/CONNECT proxy on `127.0.0.1:8880+n`, asset cache, egress |
| 4 | **Brain** (`dofus-network/`) | Python, spawned on demand | encoder policy, planner, knowledge base, agent loop |
| 5 | **Sentinel** (`networkhealth/sentinel.py`) | Python, port 8890 | long-lived health/telemetry service |

The client is not an emulator. There is no Android, no APK, no QEMU in the
default build: the Cordova bundle is served over a local origin (`dofus.local`),
Edge renders it, and a shim makes the browser look like an Android phone.

### What it deliberately will not do

* **Buy on the market.** `hdv.buy` is in `FORBIDDEN_TOOLS`. Spending money on a
  listing is irreversible and there is no undo.
* **Invent a price.** A listing without an explicit integer price is refused
  before it reaches the client.
* **Count a listing as revenue.** Putting an item on the market is controllable;
  *someone buying it* is not. A successful `hdv.list` is recorded as
  `listing_open` with an expected fill time, never as kamas earned.
* **Dial direct when the proxy is down.** Fail-closed is the default, at launch
  and at dial time. `direct` is a mode you have to choose on purpose.

---

## 2. Quick start

### Install (end user)

1. Run `setup.exe` (≈288 MB). It unpacks the runtime, the client bundle, the
   relay, the proxy binaries and the Python agent into the install folder.
2. Launch `DofusFarm.exe`.
3. **Settings → Network**: leave the mode on **Auto**. Press **Test**; you want
   `active: …` and at least one healthy candidate.
4. **Instances → Add slot**, then **Launch**.
5. First launch takes a while: the client bundle is verified, the relay starts,
   the profile is created under `%LOCALAPPDATA%\DofusFarm\webview2_profiles\`.

### Build from source (developer)

```bat
compile.bat            :: Build.exe + DofusFarm.exe + uninstall.exe + setup.exe
compile.bat --include-legacy-emulator   :: adds the stripped Android path (~1.2 GB)
```

Requirements: .NET Framework 4.x (or any .NET SDK for Roslyn), Go 1.21+ for the
relay, Node (only for the build-time patch engine), Python 3.11 for the brain.
The builder picks Roslyn automatically when a .NET SDK is installed and falls
back to the legacy C# 5 compiler otherwise.

---

## 3. Architecture at a glance

```
                        ┌──────────────────────────────────────────┐
                        │ DofusFarm.exe (WinForms)                 │
                        │  Instances | AI | Settings | Shortcuts   │
                        └───────┬───────────────┬──────────────────┘
                                │               │
             one per instance   │               │  spawns on demand
                    ┌───────────▼──────┐   ┌────▼───────────────────┐
                    │ WebView2ClientForm│   │ run_autopilot.py       │
                    │  CoreWebView2     │   │  agent.autopilot       │
                    │  + mobile-disguise│   │  agent.verifier        │
                    └───────────┬──────┘   └────┬───────────────────┘
                                │ HTTP proxy    │ HTTP directives
                    ┌───────────▼───────────────▼───────────────────┐
                    │ dofus-net-proxy.exe  :8880+n                  │
                    │  routing, asset cache, egress, fail-closed     │
                    └───────────┬───────────────────────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │ ProxySupervisor (C#)  │  picks + keeps egress
                    └───────────┬───────────┘
                                │
              ┌─────────────────┼──────────────────┐
              │                 │                  │
      userspace WireGuard   HTTP/SOCKS proxy   (never) direct
      bin\warp\wireproxy    proxies.txt
```

### Ports

| port | owner | purpose |
|---|---|---|
| 8880 + slot | relay | HTTP/CONNECT proxy the client is pointed at |
| 8890 | Sentinel | health/telemetry HTTP service |
| 8891 | Combat AI | `POST /v1/combat/decide` (solver → ranker → live policy) |
| 8893 | directive bridge | `POST /directive` → client tool calls (fallback; the live port is read from `bridge_port.txt`, seen at 8894 on a multi-slot box) |
| 8895 | agent HTTP | `python -m agent.serve` — stdlib front for the agent layer |
| 40000 | tunnel | local SOCKS5 (userspace WireGuard or system WARP) |
| 5554, 5556, … | legacy Android | emulator console ports (only with `--include-legacy-emulator`) |

(8892 was the old ModernBERT stance server — retired; combat moved to 8891. Full
topology and entry points: `dofus-network/docs/CURRENT.md` §1.)

---

## 4. Repository map

```
/
├─ DofusFarm.exe              host (built)
├─ dofus-net-proxy.exe        relay (built from scripts/dofus-net-proxy.go)
├─ setup.exe                  installer (payload ≈288 MB)
├─ uninstall.exe
├─ compile.bat                one-click build
├─ network.ini                egress configuration (see §13)
├─ ai_settings.ini            AI panel state + shortcuts
├─ proxies.txt                upstream proxy pool, one per line
├─ accounts.txt               optional login:password pairs per slot
├─ manual-login.flag          disables auto-login / guest seeding
├─ no-client-update.flag      disables the bundle updater
├─ asset_cache/               relay cache (assets, .meta, proxy.log)
├─ builder/                   Build.cs → Build.exe (the whole pipeline)
├─ desktop/                   C# host sources (20.6k lines)
│  ├─ mainform.cs             shell, grid, nav, launch paths
│  ├─ pages.cs                Settings / Shortcuts / Logs pages
│  ├─ aipanel.cs              AI page + AiSettings
│  ├─ farmengine.cs           engine: instances, relays, tunnels, proxies
│  ├─ proxysupervisor.cs      egress selection + failover
│  ├─ netsettings.cs          network.ini model
│  ├─ clientbundle.cs         transactional updater + runtime patcher
│  ├─ WebView2ClientForm.cs   the client window (3.1k lines)
│  ├─ WebView2Backend.cs      instance lifecycle
│  ├─ agentbridge.cs          directive HTTP bridge
│  ├─ prioritygovernor.cs     CPU/RAM arbitration across instances
│  ├─ watchdog.cs             hang detection
│  ├─ hardware.cs             device profiles (Samsung/Xiaomi/…)
│  ├─ installer.cs            setup.exe logic
│  └─ client/www/             the served client bundle
├─ scripts/
│  ├─ dofus-net-proxy.go      the relay (3.3k lines)
│  ├─ mobile-disguise.js      injected into the client page
│  └─ *.go, *.py              protocol probes, proxy tools, data extraction
├─ tools/
│  ├─ patch-engine.js         build-time bundle patches
│  ├─ make-patch-rules.py     extracts them → patch-rules.json/.txt
│  └─ apply-patch-rules.py    reference implementation of the runtime patcher
├─ bin/warp/                  wireproxy, wgcf, warp-plus + configs
├─ bin/webview2/              Edge WebView2 bootstrapper
├─ runtimes/                  embedded Python (3.11) + win-x64/x86 helpers
├─ dofus-network/             the brain (see §9)
│  ├─ networkhealth/          RTT/loss forecaster + sentinel (port 8890)
│  ├─ networkhealth_artifacts/  one full research run, 27 files, ~100 MB (not shipped)
│  ├─ models/                 encoder, ONNX exports, verifier baseline
│  ├─ trajectories/           play + synthetic + agent step logs
│  └─ docs/                   all design docs (see §17)
└─ dofus-network/docs/        all design docs (see §17)
```

---

## 5. The host application

### Window shell

A single window with a top nav strip and five pages. Pages are built once and
toggled, not recreated: the grid keeps its selection and the AI panel keeps its
process handle.

| page | what it holds |
|---|---|
| **Instances** | toolbar + instance grid. Nothing else competes for rows. |
| **AI** | mode, target, policy, dry-run, goals, step budget, quick actions, output |
| **Settings** | login flags, network card, folders, AI reset |
| **Shortcuts** | one row per action; press a key to rebind |
| **Logs** | live tail of `DofusFarm.log` and `autopilot.jsonl` |

Pages that show live state refresh when opened (`ShowPage` → `ReloadState`), not
on a hidden timer — a hidden page polling the disk is pure waste.

### Instance model

Each instance is a row with a name, a slot, a port, RAM/cores, an optional
per-instance proxy, and a phone profile. Slot *n* maps to:

```
relay port  = base_port (default 8880) + n
sdk port    = 5554 + 2n          (legacy Android only)
profile dir = %LOCALAPPDATA%\DofusFarm\webview2_profiles\<instance>
```

### Shortcuts

Virtual-key integers in `ai_settings.ini`, applied through `ProcessCmdKey` so
they work anywhere in the window. Defaults: `F5` start, `F6` stop, `F7` send
order, `F12` emergency stop, `F8` toggle dry run. A key can only mean one thing:
the editor refuses duplicates instead of silently shadowing.

### The live AI indicator

The AI state is visible from **every page**, not only from the AI page:

* a pill in the nav strip, right-aligned, updated once a second:
  `AI running - step 42 - hdv.list advanced (model)`, or `AI idle`, or
  `AI stopped - last step 42`;
* the nav button **AI** turns green while the loop is alive.

It is green only when the child process is alive; the text comes from the last
line of `autopilot.jsonl`, not from the process. That distinction matters: a live
process with a stalled loop and a dead process look identical from the outside,
and the log is the thing that actually advances.

The AI page additionally streams the loop's stdout line by line
(`OutputDataReceived`, `PYTHONUNBUFFERED=1`), so the reason for each step is
visible as it happens rather than at the end of the run.

### Logging

* `DofusFarm.log` — one line per event, `[HH:mm:ss] message`. Rotated to `.1`
  once past 10 MB at startup (guarded by the single-instance mutex).
* `asset_cache/proxy.log` — relay log (`-quiet` sends it here instead of stdout).
* `%LOCALAPPDATA%\DofusFarm\autopilot.jsonl` — one JSON object per agent step.
* `%LOCALAPPDATA%\DofusFarm\verdicts_human.jsonl` — human corrections.

Startup always logs one line that answers "why is nothing loading":

```
[NET] egress: auto: supervisor picks | relay 8880+slot | fail closed
```

### Process hygiene

* Single instance via `Global\DofusFarmManager_SingleInstance_Mutex_2026`; a
  second launch focuses the first window instead of starting a second host.
* `--bundle-selftest` runs headless, writes `bundle-selftest.txt`, exits 0/1.

---

## 6. The client, and how it is disguised

### What is served

`desktop/client/www` is the Cordova bundle: `index.html`, `cordova.js`,
`js/*` shims, `build/script.js` (5.4 MB, the whole game), `build/styles-native.css`.
It is served under the virtual origin `https://dofus.local/`, which the relay
remaps to the real login host for sockets (§8).

### The shim layer

The bundle expects a phone. The shim provides just enough of one:

* **Cordova** — `cordova.js` plus `CordovaFileSystem`, `CordovaFileCache`,
  `AppLoader`, `AppLoadingProgress`, `autoupdate`.
* **Adjust SDK** — `Proxy`-based no-op stubs. The client calls methods we do not
  ship (`setLogLevel`, `Adjust.create`), so a hand-written method list goes stale
  immediately; a Proxy answers anything.
* **`scripts/mobile-disguise.js`** — injected before the game boots. It
  overrides the device fingerprint (UA, WebGL vendor/renderer, screen metrics,
  serial, Android ID), installs the recorder, the FarmBot and the tool handlers,
  and adds the HUD.

### The HUD

A small pill in the game window:

* label `DofusFarm AI: ON/OFF` — click toggles the fight AI (same as `F2`);
* **drag it anywhere**; the position is saved per instance in
  `localStorage["__dofusfarm_hud_pos"]` and clamped into view on resize;
* **▶ Auto** posts `{"type":"AI_REQUEST","action":"start_auto"}` to the host,
  which opens the AI page and starts the loop. The page cannot spawn processes;
  the host does.

### Messages from the page to the host

| type | meaning |
|---|---|
| `CONSOLE_LOG` | console line, forwarded to the host log |
| `PLAY_LOG` | one human action, written verbatim to the play log |
| `FARMBOT_STATE` | fight AI on/off |
| `AI_REQUEST` | start the autonomous loop |
| `TOGGLE_FULLSCREEN` | window command |
| `diagnostics_report` | capability probe (WebGL, storage, UA) |
| `open_oauth` / `close_oauth` | Google login window |
| `webauth_exchange` | guest token exchange |
| `ping` | liveness |

### Login paths

1. **Guest** — `POST https://auth.ankama.com/token/create-guest`, exchanged for
   a session. Guests are wiped by client updates and rate-limited per IP; a 403
   from CloudFront means the guest quota for that address is exhausted, not that
   the code is broken.
2. **Real account** — manual login (`manual-login.flag`) or Google OAuth through
   the popup window. In manual mode the host injects nothing and clears any
   stored guest, because a persisted guest identity otherwise overwrites the real
   session on the next reload.

### Known black-screen causes (all fixed, kept here as history)

| cause | symptom | fix in place |
|---|---|---|
| `styles-native.css` missing | blank page, no JS error | bundle validation (§7) |
| updater deleted `script.js` | blank page, no bundle at all | download to staging, commit after validation |
| `AdjustConfig` returning `{}` | boot exception | Proxy stubs |
| `appVersion` in the wrong query position | `SOCKET_LOST` | version param emitted last |
| patches lost after an update | login error / black screen | runtime re-patch (§7) |

---

## 7. The updater (and why updates used to break everything)

The bundle is **not** shipped inside the APK: Ankama serves `manifest.json` and
the client downloads `script.js` at runtime. That is also why a cached copy goes
stale silently, and why "the update broke it again" was a recurring failure.

### The actual root cause

`script.js` is **patched at build time** by `tools/patch-engine.js` (11 patches:
touch input, map-transition watchdog, Adjust removal, WebGL fingerprint, logger
silencing…). The old runtime updater replaced `script.js` with the server's **raw**
copy — so the first time the server published a new build, every patch vanished.
Login failed or the page came up black, and nothing in the logs pointed at the
cause.

### The updater is now a transaction

```
manifest.json
   ↓
download changed files → client/www/staging-<ticks>/
   ↓
apply tools\patch-rules.txt to the staged script.js
   ↓
validate the staged tree (size, not HTML, css non-empty, index.html)
   ↓
commit each file with a .prev copy, then drop .prev
   ↓
write client_version.json
```

Any failure at any step discards the staging tree and **keeps the working
bundle**. Nothing is deleted before its replacement exists.

* `build/script.js.bak` always holds the **unpatched** original, so a rebuild
  starts from a clean file.
* **Self-heal** on launch: if the live bundle is unusable (missing, < 1 MB, or
  starts with `<`), it is restored from `.bak`; if there is no backup the log
  says so instead of showing a black screen.
* `no-client-update.flag` disables all of it (Settings → "Do not update…").

### One description of a patch

`tools/make-patch-rules.py` extracts the rules from `patch-engine.js` into:

* `tools/patch-rules.json` — readable, used by the Python reference applier;
* `tools/patch-rules.txt` — base64 lines, parsed in ~20 lines of C#.

The build-time JS engine and the runtime C# patcher read the **same** rules.
Proof, not trust:

```bat
DofusFarm.exe --bundle-selftest
:: rules: 13
:: matched: 0, 1, 2, 3, 6, 7, 8, 9, 10, 11
:: skipped: 4: end marker missing …
:: RESULT: IDENTICAL
```

`IDENTICAL` means: applying the shipped rules to the unpatched backup reproduces
the live patched bundle character for character. If a future client build moves
the code a rule targets, that rule is skipped, the count is logged, and the
bundle is still adopted (with a warning) — a degraded patch set still runs the
game, which is better than refusing an update the server requires.

---

## 8. The network layer

Three layers, each with one job.

### 8.1 The relay — `dofus-net-proxy.exe`

A single Go binary. It is an HTTP proxy with `CONNECT` support that also caches
the game's static assets.

**Routing.** The client resolves its socket against the page origin, so it asks
for `dofus.local:443` — a name that exists only inside the WebView2 mapping.
The relay rewrites it:

```
dofus.local:443  →  dt-proxy-production-login.ankama-games.com:443
```

**Egress.** Two upstream shapes:

* `socks5://host:port` — the destination is resolved **locally** before the
  handshake. Cloudflare's SOCKS cannot resolve Ankama's game domains (measured:
  CONNECT by name → code 0x04, by IP → 0x00).
* `http://host:port` (optionally with credentials) — plain `CONNECT`.

**Fail-closed.** If the upstream dies, dials are refused with an explicit error.
They are **not** retried direct. `-allow-direct` is the only opt-out and it logs
every time it fires.

**Cache.** `asset_cache/` holds every static asset plus a `.meta` sidecar, a
hard-linked mirror at `files/<host>/<path>`, a negative cache for deterministic
403/404s, neighbour-map prefetch, and an optional graph crawler.

**Mods.** `<cache>/mods/rules.json` rewrites/blocks/patches CDN responses on the
fly; the unpatched originals stay in the cache.

### 8.2 The supervisor — `ProxySupervisor` (C#)

One owner for "how does traffic leave". In `auto` mode it:

1. builds candidates — userspace tunnel, configured upstream, every entry in
   `proxies.txt`;
2. probes each **for real** (fetch `https://api.ipify.org`, record the exit IP
   and latency);
3. **rejects any candidate whose exit IP equals this machine's own address** —
   a transparent proxy is not a proxy;
4. ranks: local tunnel first, then lowest latency;
5. re-tests every 45 s and fails over;
6. on a switch, rebuilds the running relays **on the same ports**, so a live
   instance reconnects instead of showing "Server busy" until you relaunch.

A bound SOCKS port proves nothing: `wireproxy` binds **before** the WireGuard
handshake completes, so an ISP that drops WARP's UDP looks healthy to a port
check. The tunnel is therefore probed with a real SOCKS5 `CONNECT`.

### 8.3 Modes

| mode | what runs | touches Windows routing? |
|---|---|---|
| `auto` (default) | supervisor picks | no |
| `wireproxy` | `bin\warp\wireproxy.exe` → SOCKS5 | no |
| `warp` | installed Cloudflare WARP via `warp-cli` | **yes, all of it** |
| `custom` | `upstream=` or `proxies.txt` round-robin | no |
| `direct` | nothing | no, and your IP is exposed |

System WARP installs an adapter and rewrites DNS for the whole PC. When its
tunnel drops, the machine loses its normal internet too — that is the "cloudflared
ripped my WiFi" failure. **Settings → Repair my network** runs
`warp-cli disconnect`, `sc stop CloudflareWARP` (it keeps leak-protection filters
active while "disconnected"), flushes DNS, then probes `auth.ankama.com` and
`1.1.1.1:443` and reports.

### 8.4 Measured on the reference machine

Maroc Telecom, 2026-10-22 — and the result **changed within the same day**, which
is the whole argument for `auto`:

| time | UDP to Cloudflare | what worked |
|---|---|---|
| 13:35 | dropped outright — WireGuard handshake never answered, MASQUE/QUIC timed out, even DNS to `1.1.1.1` over UDP failed | only a TCP proxy (`proxies.txt`) |
| 14:41 | normal — handshake completes, exit `104.28.220.131` | the userspace tunnel |

`wireproxy`, `warp-plus` (WireGuard *and* Psiphon modes) and `usque` (MASQUE)
were all tested while it was blocked; none of them could carry a byte. Nothing in
the code changed between the two rows — the supervisor simply re-probed and
switched. A hard-coded "use WARP" or "use the pool" would have been wrong in one
of those two states.

---

## 9. The AI stack

Two budgets govern everything: **RAM** (≈150 MB for the whole AI side) and
**latency** (a decision has to fit between two game ticks).

### 9.1 The encoder (tool policy)

A 24.9 M-parameter encoder that maps `(goal text, observation)` to
`{tool, args}`. No decoder: 55–62 ms instead of 17.7 s for the same task.

| property | value |
|---|---|
| vocab / seq | 4096 / 256 tokens |
| width / layers / heads | 512 / 7 / 8 (FFN 2048) |
| params | 24,874,131 |
| artifacts | `model.pt` (95 MB), `encoder.onnx` (95 MB), `encoder_int8.onnx` (25.4 MB) |
| train / val | 21,984 / 3,065 examples |
| best tool accuracy | **0.9391** |
| best call accuracy | **0.6980** (exact match of tool + every argument) |
| early stop | on **call**, not tool — tool saturates at 0.94 while call still moves |
| training cost | 3,000 steps, 987 s on the reference GPU |

INT8 is what ships: 25.4 MB, ~62 ms per decision, bit-identical to fp32 on the
release gate.

> **What actually runs (`--policy`).** The shipped default is **`rule`** — the
> deterministic `QuestPlanner`, no model loaded. `--policy encoder` loads the 24.9M
> encoder above. `--policy planner` loads a *different, much smaller* head:
> `models/tool_policy.onnx` (42 features → 21 tool logits, 49,365 params, val
> accuracy **0.8599**), where the ONNX does the `argmax` but the planner still owns
> argument filling and safety gates. `models/verifier_head.onnx` is **not** loaded
> by a hand-typed CLI (`--verifier-model` defaults to `None`), but the **GUI
> auto-passes it** (`aipanel.cs:705-708`), so a normal launch *does* load it — and
> its action-label accuracy is unmeasured (read-only queries are never model-labelled).
> See `dofus-network/docs/CURRENT.md` §5
> and `KNOWN_GAPS.md` G6. "Tool policy" and "encoder" are not one model.

### 9.2 The planner (tier 2)

An AND/OR search over the game database, costing every branch in **minutes**:

```
acquire(item, qty)
    OR buy    → kamas            (children: the farm that earns them)
    OR farm   → monsters dropping it   (gate: level ≤ player + 20)
    OR craft  → AND over ingredients   (recursive, cycle-guarded, depth 3)
    OR quest  → quest-locked fallback
```

`kamas_per_minute(player_level)` is the pivot: the best
`sum(dropped value × drop rate) × kills/min` over every monster you can beat.
Once that exists, buying and farming are comparable and `min()` does the rest —
nobody writes "if it is expensive, farm first".

A level-1 character asking for a level-200 item gets a real answer: *buy it in
369 minutes by farming Boufton Pâlichon at 113 kamas/min*, and the rejection line
(*crafting would take 3,318 minutes*) is the part a human actually wants.

### 9.3 The knowledge base

40.5 MB of JSON becomes **3.0 MB on disk / 5.3 MB in RAM** as compact typed
arrays plus one UTF-8 name blob — 23× less memory than `json.load`, because the
JSON route materialises a dict per record and a string object per field.

### 9.4 Everything else

* `planning/economy.py` — cost model, player state, kamas/min.
* `planning/bandit.py` — Thompson sampling over strategies.
* `planning/schedule.py` — runs a plan, watches the clock, replans on
  `level_changed`, `cheaper_branch`, `drop_rate_drift`, `overrun`, `stale`.
* `planning/trace.py` — residual percentiles, verified vs unverified.
* `combat/` — damage calculator, tactical engine, recursive reasoner (tier 1,
  untrained).
* `navigation/` — walkability and map graphs.

### 9.5 NetworkHealth — the network forecaster, and its artifacts

A second, much smaller model that answers a different question: **is the path to
the server degrading?** Not "is the game slow" — the client cannot tell you that
reliably — but "is this slot's route getting worse, and should the host fail
over". It is a causal TCN (`networkhealth/model.py::NetworkHealthTCN`, 52,816
parameters, 0.20 MB) with per-horizon heads for log-RTT, RTT variance, jitter,
loss and a degradation logit, plus one anomaly score shared across horizons.

Runtime pieces:

| file | role |
|---|---|
| `networkhealth/collectors/` | pull public ping measurements from the RIPE Atlas API |
| `networkhealth/clean.py` | schema/timestamp/plausibility checks, dedup, failure preservation |
| `networkhealth/features.py` | windowing (`seq_len`), `TARGET_BLOCK` layout |
| `networkhealth/splits.py` | time + series split with purge/gap, so no probe is on both sides |
| `networkhealth/baselines.py` | persistence / moving average / EWMA / seasonal naive |
| `networkhealth/training.py`, `evaluation.py` | train, evaluate, write the report |
| `networkhealth/inference.py` | `MultiInstanceNetworkHealth` — one model, many slots |
| `networkhealth/bridge.py` | polls relays on 8880…8883, emits forecasts and failover alerts |
| `networkhealth/sentinel.py` | the singleton service on `127.0.0.1:8890` |
| `networkhealth/provenance.py` | sha256 of every input and output, environment fingerprint |

#### `dofus-network/networkhealth_artifacts/`

The **output of one full run** (2026-10-17), 27 files, ~100 MB. It is a research
artifact, not runtime data: `builder/build.cs` deliberately excludes this
directory from the installer payload. It is kept because every number in the
report can be traced back through the manifests.

| file | what it is |
|---|---|
| `REPORT.md` | the human summary: split, model vs baselines, cost, caveats |
| `raw.jsonl` (27.6 MB) | 156,748 observations as fetched, one JSON object per line |
| `raw_provenance.json` | RIPE Atlas source, 10 measurement IDs, exact 7-day window, per-measurement row counts, quality counters |
| `clean.jsonl` (33.5 MB) | the same rows after cleaning |
| `clean_report.json` | what cleaning did: 156,748 in / 156,748 out, 0 dropped, **171 failures preserved**, 51 groups with an interval change |
| `features.npz` (13.1 MB) | tensors: 143,049 examples, 103 series, 66 distinct probes, `seq_len` 128, horizons 1/3/6 |
| `baselines.json` | the four naive baselines on the same rows, in the same `log1p(RTT)` space |
| `baseline_split_indices.json` | exactly which rows went to which side, so the baseline split is reproducible |
| `model.pt` (2.1 MB) | trained checkpoint |
| `evaluation.json` | split summary, training history, model vs baselines, cost, checkpoint sha256 |
| `evaluation (1).json` | an earlier run kept for comparison (the parenthesised name is a Windows copy artifact, not a convention) |
| `live_telemetry.json` | the sentinel's live snapshot: per slot `port / online / rtt_ms / degradation_probability / loss_probability / residual_z_score / route_anomaly` |
| `manifest_collect.json` | stage manifest: parameters (7 days, 10 measurements, 6 workers, 10 QPS), environment (Python 3.15, torch 2.14.1+cpu, 8 cores), input/output sha256 |
| `manifest_clean.json`, `manifest_prepare.json`, `manifest_baselines.json`, `manifest_train.json` | one per stage, same shape, each pointing at the previous stage's sha256 |
| `cache/measurement_<id>_<start>_<stop>.jsonl` | raw per-measurement downloads, so a rerun does not re-query RIPE |

#### What the numbers say

| horizon (samples ahead) | model log-MAE | persistence | moving avg | EWMA | seasonal naive |
|---|---|---|---|---|---|
| 1 | **0.4654** | n/a | n/a | n/a | n/a |
| 3 | **0.2296** | n/a | n/a | n/a | n/a |
| 6 | **0.2684** | n/a | n/a | n/a | n/a |

The baselines show `n/a` because they were computed on an earlier split
(`baselines.json`: mode `time`, 3,397 train / 872 valid, 7 shared series), while
the model run used `time_and_series` (1,585 / 146, **0 shared series**, 2,625
purged). Comparing them directly would be dishonest, so the report leaves the
cells empty rather than mixing two splits. Cost: **2.163 ms median** (p95 2.718
ms) on CPU, 0.20 MB of weights.

#### Caveats, as written in the report

* Public RIPE Atlas ping data **cannot characterise the game client** — different
  paths, different traffic. Local authorised telemetry is required for that
  (Phase 5 in `PLAN.md`).
* The anomaly head is a **suspicion score**, not an attack classification.
* 171 failures are preserved in `clean.jsonl` rather than dropped: a timeout is
  data, and removing it teaches the model that outages do not happen.

### 9.6 Training pipeline

`prepare_encoder_sft.py` → `build_core_sft.py` → `train_from_scratch.py` /
`thx_finetune.py` → `export_encoder.py` (ONNX + INT8) → `release_gate.py`.
The gate refuses an export whose INT8 output differs from fp32.

Training runs offline on Kaggle; `--resume` continues from a checkpoint, and
`planned_steps` is corrected on resume so the schedule does not restart.

---

## 10. The agent loop and the second agent

### 10.1 Tools (21, closed vocabulary)

Runtime order is `data/capabilities.json`. `hdv.buy` is **not** in the vocabulary —
buying is irreversible and is banned at runtime too (`FORBIDDEN_TOOLS`).

| tool | backend | tool | backend |
|---|---|---|---|
| `knowledge.search` | local | `inv.sell` | directive |
| `quest.list` | local | `hdv.scan` | directive |
| `knowledge.remember` | local | `hdv.list` | directive |
| `knowledge.forget` | local | `chat.say` | directive |
| `fight.plan` | local | `state.snapshot` | directive |
| `fight.estimate` | local | `safe.stop` | directive |
| `nav.goto` † | directive | `fight.state` | directive |
| `nav.follow` | directive | `quest.advance` | directive |
| `farm.start` | directive | `farm.stop` | directive |
| `fight.join` | directive | `fight.pass` | directive |
| `inv.list` | directive | | |

† `nav.goto` is a directive, but its name→map resolution is answered by the
Sentinel on `127.0.0.1:8890`.

`directive` means: the host posts JSON to `127.0.0.1:8893/directive` (fallback —
the live port comes from `bridge_port.txt`), the page's handler performs it and
reports back.

### 10.2 The loop

`agent/autopilot.py`: **observe → choose → guard → execute → settle → verify →
decide**. The `settle` stage (see `CURRENT.md` §3) re-observes after a
world-changing tool so a walk in flight is not scored `noop`.

* No wall-clock budget and no interval by default (`steps=0` = unlimited). It
  stops when the goals are met, when it stops making progress, or when you stop it.
* Stop paths: `Ctrl-C`, the `--stop-file`, or the `AUTOPILOT_STOP` file.
* On exit it calls `safe.stop` so the character is not left mid-loop.
* Every step is appended to `autopilot.jsonl` with the observation before and
  after, the call, the latency, and the label.

### 10.3 The second agent (verifier)

`agent/verifier.py` labels what the first agent did, from three sources in
priority order:

| source | when | what it is |
|---|---|---|
| **human** | a reviewer wrote a verdict | `verdicts_human.jsonl`, wins over both |
### Knowledge is data, not weights

The model is not a database. Facts live in `agent/knowledge_store.py` — an
append-only log with provenance, confidence and a delete path — and the model
learns *when to write to it*, not what to memorise:

| tool | what it does |
|---|---|
| `knowledge.search` | look something up in the DofusDB cache |
| `knowledge.remember` | record a discovery (`npc_map`, `monster_drop`, `price`, `route`, …) |
| `knowledge.forget` | delete a fact that turned out to be wrong |

Rules that keep it small and honest: only discoveries go in (never a copy of the
cache), every fact carries its source (human > observed > derived > guess), a
weaker source can only *dispute* a stronger one, three disputes drop the fact, and
unused facts expire after 45 days. The planner reads the store, so a learned
`npc_map` changes the next plan with no retraining:

```
before: [quest.advance]
after : [nav.goto, quest.advance]      # after remembering where NPC 6404 is
```

| **model** | `--verifier-model` points at an artifact | the head is `verifier_head.onnx` (+ a `verifier_head.json` report); the GUI passes it automatically (`aipanel.cs:705-708`) |
| **rule** | always | the baseline the model has to beat — and the fallback for read-only tools, which the model is never allowed to label |

The loop loads a model only when the GUI passes `--verifier-model` and `onnxruntime`
is present; otherwise it labels with rules. The shipped head is `verifier_head.onnx`
(its `verifier_head.json` reports the training metrics). Its shipped validation score
is 1.0, which proves the wiring and nothing else: those labels were produced by the
rules from the same features, so the model is learning the rule function, not the
game. A real number needs live or human labels — see `KNOWN_GAPS.md` G6.

Each label is `verdict` (advanced / noop / failed / blocked) + `residual_s` +
`drift` + `source`. Drift is three explainable contradictions: kamas dropped
without a purchase, the map changed on a non-movement tool, the observation lost
fields it had before.

**The label changes what happens next.** `agent/controller.py` is the second agent
proper: it owns the objective, the progress, a per-cause strategy table and the
failure memory, and it returns a decision the loop must obey —

| decision | when | effect on the actor |
|---|---|---|
| `continue` | step advanced | ask the policy as usual |
| `retry` | first failure of a tool | same call once more |
| `recover` | second failure | a concrete different call (`state.snapshot`) |
| `replan` | `not_mapped`, 3 failures of one tool, or two **fresh** drifts | host/scheduler asked to replan |
| `abort` | no recovery left | the goal stops |

Failure **causes** are separated (`wrong_target`, `stale_state`, `incomplete`,
`invalid_assumption`, `plan_wrong`) because each needs a different response, and
drift on a stale or identical observation is not counted as evidence.

The rule baseline is deliberate: waiting for a dataset inverts the dependency.
The loop has to run and fail first, and the labels fall out of it — every
rule/model/human disagreement is a training sample.

```bash
python -m agent.regression                         # 206 checks, offline (loop + verifier + tools + dialog + prompt/mob + recovery + waits + dialog labels + dialog repeats; delegates node JS + bundle resilience --check + go relay suites)
python -m advisor selftest                         # 122 checks, offline (knowledge repo only)
python -m agent.verifier --stats
python -m agent.verifier --label 98106503 4 failed "the listing never appeared"
python run_autopilot.py --goals-file goals.txt --steps 0
```

> The old `python -m agent.smoke_verifier` gate (the loop/verifier contract checks)
> was removed 2026-10-25. It is partly rebuilt as `agent.regression`; `advisor
> selftest` covers the item/set/spell repository. Neither replaces a live session.
> See `dofus-network/docs/KNOWN_GAPS.md` G8.

---

## 11. Data: what is recorded and where

| path | content | volume |
|---|---|---|
| `dofus-network/trajectories/*.jsonl` | human play + synthetic tool calls | 14 files, 35,585 rows |
| `%LOCALAPPDATA%\DofusFarm\autopilot.jsonl` | agent steps with labels | grows per run |
| `%LOCALAPPDATA%\DofusFarm\verdicts_human.jsonl` | human corrections | one line per review |
| `<install>/dofus-network/trajectories/play_<instance>.jsonl` | every human action in the client | per session, rotates at 64 MB |
| `asset_cache/` | every static asset + `.meta` + `proxy.log` | ~9,700 files |
| `dofus-network/data/cache/*.json` | DofusDB extract (items, monsters, quests) | 40.5 MB |
| `dofus-network/models/` | checkpoints, ONNX exports, `verifier_head.{onnx,json,pt}` | 4 KB – 95 MB |
| `dofus-network/data/verifier_sft/` | built verifier corpus (train/val + stats) | a few hundred KB |

The recorder logs **everything a human does** — including how a new account is
created — because the policy has to learn from play, not from descriptions of play.

---

## 12. Build and release

`compile.bat` compiles `builder/Build.cs` into `Build.exe`, then runs it:

| step | what happens |
|---|---|
| 0 | compile the Go relay (skipped if `go` is absent; the shipped exe is reused) |
| 0b | apply `patch-engine.js` to the client bundle (11 patches) |
| 0c | sync client shims into both distribution trees |
| 1 | `DofusFarm.exe` (Roslyn if available, else legacy csc) |
| 2 | `uninstall.exe` |
| 3 | `setup.exe`: stage the payload, compile with the embedded zip as a resource |

**Payload** (≈288 MB): host, relay, client bundle, `bin/warp`, `bin/webview2`,
`tools/patch-rules.txt`, `runtimes/python`, `dofus-network` (models included),
`network.ini`, `proxies.txt`, `accounts.txt`.

The legacy Android path (SDK + system image, ≈950 MB) is **excluded** by default;
`--include-legacy-emulator` puts it back. Nothing in the UI can select it — the
engine selector was removed.

---

## 13. Configuration reference

### `network.ini`

| key | default | meaning |
|---|---|---|
| `mode` | `auto` | `auto` / `wireproxy` / `warp` / `custom` / `direct` |
| `socks_port` | `40000` | local SOCKS5 port the tunnel binds |
| `upstream` | *(empty)* | custom mode: `socks5://…` or `http://…`; empty = `proxies.txt` |
| `base_port` | `8880` | relay port for slot *n* is `base + n` |
| `fail_closed` | `1` | `0` allows direct when nothing is healthy — **exposes your IP** |
| `extra_args` | *(empty)* | raw flags appended to the relay command line |
| `wireproxy_conf` | *(empty)* | blank = `bin\warp\wireproxy.conf` |
| `log_path` | *(empty)* | blank = `<install>\asset_cache\proxy.log` |

### `ai_settings.ini`

`mode`, `target_slot`, `policy`, `dry`, `steps` (0 = unlimited), `goals`,
`order`, and `shortcut.<action>` as virtual-key integers.

### Relay flags

```
-port 8880            listen port
-host 127.0.0.1       bind address
-upstream <url>       socks5:// or http:// upstream
-config <file>        ini defaults for every flag below
-dns <ip>             custom resolver
-cache <dir>          asset cache directory
-quiet                no console output (log goes to <cache>/proxy.log)
-log <file>           explicit log path
-allow-direct         fall back to direct when the upstream dies (dangerous)
-token <token>        require a bearer token from clients
-serial / -uuid / -model / -manufacturer / -ua / -gl-vendor / -gl-renderer
                      values interpolated into the disguise script
-texture-size <n>     GL max texture size
-referer <url>        Referer/Origin for CDN fetches
-assets-url <url>     base URL for precaching/crawling
-precache-maps <spec> map IDs to warm ("1-20000" or "7442,10354")
-precache-workers <n>
-prefetch-neighbours / -prefetch-radius / -prefetch-tiles-depth / -prefetch-workers
-crawl <seeds> / -crawl-max / -crawl-rate / -crawl-tiles
-mods-dir <dir>       rules.json location
-login-host <host>    target of the dofus.local remap
```

### `client-version.ini` (optional)

Pins the version the page announces, without a rebuild:

```ini
app=3.15.0
code=3015000
build=1.75.2
```

Without it the version is resolved from the installed bundle
(`DynamicVersionResolver`), and if that fails the field is left alone rather than
filled with a stale constant. `mobile-disguise.js` never invents a version.

### Environment

| variable | effect |
|---|---|
| `DOFUS_FARM_HOME` | alternate install root for SDK/proxy lookup |
| `ANDROID_SDK_ROOT` / `ANDROID_HOME` | legacy Android only |
| `LOCALAPPDATA` | profiles, logs, autopilot/human labels |

---

## 14. Troubleshooting

| symptom | most likely cause | what to do |
|---|---|---|
| "Server busy" / nothing loads | no healthy egress | Settings → Network → **Test**; read the per-candidate reasons |
| Instance does not start at all | fail-closed with no candidate | same as above; or switch to **custom** and press **Use proxies.txt** |
| Tunnel "listening but not passing traffic" | ISP drops WARP's UDP | use `custom` + a live proxy, or system WARP (which uses 443) |
| WiFi died after enabling WARP | system WARP took over routing/DNS | Settings → **Repair my network**, then stay on `auto`/`wireproxy` |
| Black screen, no error | bundle broken or unpatched | check `[UPDATER]` lines; `DofusFarm.exe --bundle-selftest` |
| Login fails right after a client update | patches not applied | the updater now re-applies them; verify the `[UPDATER] patched staged bundle` line |
| Guest login 403 | guest quota for your IP | real account, or wait |
| `SOCKET_LOST` | malformed `appVersion` param | version param must be emitted last |
| Relay port already in use | stale `dofus-net-proxy.exe` | the engine kills the one bound to that port on launch |
| Two hosts fighting | single-instance mutex | the second launch focuses the first |
| AI loop exits immediately | no goals / stop file present | type goals; delete `AUTOPILOT_STOP` |

---

## 15. Tests and self-checks

**Client bundle patcher** (C#, headless — proves the runtime rules equal the build):

```bat
DofusFarm.exe --bundle-selftest
type bundle-selftest.txt
```

**Live AI pill** (C#, headless — formats the last step the loop wrote):

```bat
DofusFarm.exe --ai-status-selftest
type ai-status-selftest.txt
::   idle form   : AI stopped - last step 42
::   running form: AI running - step 42 - hdv.list advanced (model)
::   no log      : AI idle
::   first step  : AI running - waiting for the first step
```

**Relay** (`scripts/` holds several tools, each with its own `main`, so the test
files have to be named explicitly):

```bat
cd scripts
go test dofus-net-proxy.go dofus_net_proxy_bench_test.go dofus_net_proxy_equiv_test.go dofus_net_proxy_relay_test.go
```

**Agent** (offline: no game, no network, no torch):

```bat
cd dofus-network
python -m agent.regression
python -m advisor selftest
```

> The previous three-command gate — `agent.smoke_verifier`, `agent.selftest`,
> `networkhealth.selftest` — no longer exists; all three were removed 2026-10-25.
> `agent.regression` (206 checks) rebuilds the loop/verifier contract (verdicts,
> nested-alias changes, the claim-proof, tool-vocabulary equality, the `hdv.buy`
> ban, dialog question tie-break, prompt/named-mob fidelity, the verifier-model
> guard, the recovery layer's no-repeat/terminate invariant, the failure-cause
> labels, absent-client rescue, repair credit, idle waits, dialog outcome labels, the baseline report itself and the settle window) and
> delegates the JS
> page suites to `node` **and the Go relay tests to `go`**; `advisor selftest` (122 checks)
> covers the knowledge repository. Neither covers the C#
> `--bundle-selftest`, the combat/data suites, or a live session — see
> `dofus-network/docs/KNOWN_GAPS.md` G8.

---

## 16. Known limits

* **One account per slot.** No multi-account on one identity; the host claims
  accounts so two instances cannot use the same login.
* **Guest accounts die on update.** They are server-side; the update wipes them.
* **The pool is public proxies.** They are slow, shared, and die often. `Scan
  proxies.txt` prunes them; nothing here makes a free proxy reliable.
* **`hdv.buy` is disabled.** Deliberate, see §1.
* **The verifier head is not a learned improvement yet.** The GUI auto-loads
  `verifier_head.onnx`, but its 1.0 accuracy is circular (rule labels on rule
  features), so it clones the rules rather than adding signal; read-only tools are
  never model-labelled regardless. See `KNOWN_GAPS.md` G6.
* **Drift prints `REPLAN`; it does not yet call the scheduler.** One line of
  wiring, needs a live plan to mean anything.
* **Two models in one process measured at 95.3 MB** (`ai/measure_rss.py`), so the
  137 MB design budget holds — but only on this machine, with these artifacts.
* **Legacy Android path is unmaintained** and excluded from the default build.

---

## 17. Documentation index

| file | what is in it |
|---|---|
| `dofus-network/docs/CURRENT.md` | **the running-system map** — services/ports, the 7-stage loop, think-token pacing, model inventory, combat pipeline, the 21 tools |
| `dofus-network/docs/KNOWN_GAPS.md` | **honest limits** — no keep-alive (INACTIVITY), no equip tool, dialog text unread, no vision, forecaster dark, live fight unverified, tests gone |
| `dofus-network/docs/PLAN.md` | the authoritative plan and phase list (single status source) |
| `dofus-network/docs/PROJECT_LOG.md` | every decision (D1–D21) with the falsifier |
| `dofus-network/docs/PLANNER_DESIGN.md` | tier architecture, RAM budget, why no token CoT (note: `hdv.buy` recommendation is now forbidden) |
| `dofus-network/docs/SECOND_AGENT.md` | the verifier: design and how it was meant to be tested (test gate removed — see KNOWN_GAPS G8) |
| `dofus-network/docs/AGENT2_DESIGN.md` | earlier second-agent design (superseded by AGENT3_PLAN) |
| `dofus-network/docs/AGENT3_PLAN.md` | the current second-agent plan (design, not shipped) |
| `dofus-network/docs/FAILURE_TO_SUPERVISION.md` | how kicks/refusals/regressions become labels, data and tests |
| `dofus-network/docs/PROTOCOL_NOTES.md` | captured Dofus messages and field layouts |
| `dofus-network/docs/PIPELINE.md` | data pipeline end to end |
| `dofus-network/docs/CAPABILITY_REGISTRY.md` | the tools and their contracts as data (live count is 21) |
| `dofus-network/docs/EXPERIENCE_LEARNING.md` | experience/replay design |
| `dofus-network/docs/LATENT_AND_ONLINE_LEARNING.md` | latent bus and online updates |
| `dofus-network/docs/ARCH_REASSESSMENT.md` | the architectural argument (reasoning; status column superseded) |
| `dofus-network/docs/README.md` | the docs map with reading order by question |

*Deleted 2026-10-26 as obsolete:* `AI_ANALYSIS.md`, `BENCH_30M.md`,
`PRETRAIN_GAPS.md`, `KAGGLE_RUNS.md`, `MORNING.md`. The one live item from
`PRETRAIN_GAPS.md` (the Sentinel still importing torch) is now `KNOWN_GAPS.md` G5.

---

## 18. Third-party components

| component | licence | where |
|---|---|---|
| `wireproxy` | GPL-3.0 | `bin/warp/wireproxy.exe` |
| `wgcf` | MIT | `bin/warp/wgcf.exe` |
| `warp-plus` | MIT | `bin/warp/warp-plus.exe` |
| Edge WebView2 runtime | Microsoft EULA | installed by `bin/webview2` |
| Python runtime | PSF | `runtimes/python` |
| Dofus Touch client bundle | Ankama, all rights reserved | downloaded at runtime, not redistributed |

The Dofus Touch bundle is fetched from Ankama's own servers at run time and
patched locally; it is not shipped in this repository or in the installer.
