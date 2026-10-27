# DofusFarm — Full Project Resume

**Snapshot date:** 2026-10-27 (late session)
**Repository:** `C:\Users\sao\Documents\dofus-emu`
**HEAD at snapshot:** `0990695` (plus an uncommitted working tree — see §14)
**Purpose of this file:** one document that explains what the project is, how every
piece works, what is proven, what is only offline, what is broken, and what to do
next. It is written so a new session (human or agent) can resume without reading
10,000 lines of docs first.

> Everything here is measured from the code, the docs, or the live logs unless it is
> explicitly labelled *unknown*, *offline*, or *prediction*. Where two documents
> disagree, the newest document wins; the known stale numbers are listed in §22.

---

## 0. TL;DR

DofusFarm runs **Dofus Touch** as the real Cordova/WebView2 client on Windows,
routes every packet through a local Go relay that owns a pinned egress, and drives
the game with a Python agent. The agent is a hybrid:

- a deterministic planner and rule loop that **acts** today,
- a learned command parser that is **shadow-only** (84.6% vs 53.8% exact on held-out
  phrasings, but it does not act),
- a learned tool policy / foresight ensemble that is **opt-in** (`--policy planner`),
- a combat solver + learned PPO actor that is **wired but has never taken a proven
  live turn**.

The current measured reality on the live server (2026-10-27, four instances):

- the client logs in, stays connected (AFK keep-alive now pins the client's own
  inactivity timer), captures its full quest catalogue, and **does** complete some
  tutorial objectives;
- across the latest four per-instance logs: **10 objectives total** (dofus-01 +1,
  dofus-02 +8, dofus-03 0, dofus-05 +1), **0 quests completed**, 17/8/3/3 manual
  interventions, and recover/replan dominates the step budget;
- the most recent run tail is a long **wait** ("the policy had no action to take",
  28–384 consecutive passes; three instances above 280), not a crash;
- the first end-to-end fight, the first equip, and the first live shadow-parser
  session are still unproven.

The project's own rule is the right summary: **the harness is not an AI. The harness
supplies data and tools; the AI makes decisions.** Today the decision layer is mostly
still rules; the model work is being promoted one measured component at a time.

---

## 1. What this project is

A personal automation project for **Dofus Touch** (Ankama). It is not an emulator:
there is no Android, no APK, no QEMU in the default build. The real Cordova bundle is
served from disk under the virtual origin `https://dofus.local/`, Edge WebView2 renders
it, and an injected script disguises the browser as a phone and exposes a tool API.

Five things run in one process tree:

| # | thing | runs as | job |
|---|---|---|---|
| 1 | **Host** (`DofusFarm.exe`) | WinForms, one process | instance grid, AI panel, settings, shortcuts, logs |
| 2 | **Client** (WebView2) | one child window per instance | the real Dofus Touch bundle, served from disk |
| 3 | **Relay** (`dofus-net-proxy.exe`) | one child process per instance | HTTP/CONNECT proxy, asset cache, egress |
| 4 | **Brain** (`dofus-network/`) | Python, spawned on demand | planner, encoder policy, verifier, agent loop, combat brain |
| 5 | **Sentinel** (`networkhealth/sentinel.py`) | Python, port 8890 | health/telemetry + `nav.goto` name→map resolution |

### What it deliberately will not do

- **Buy on the market.** `hdv.buy` is absent from the 21-tool vocabulary and is
  additionally vetoed by `FORBIDDEN_TOOLS` (`agent/autopilot.py`).
- **Invent a price.** A listing without an explicit integer price is refused, never
  guessed.
- **Count a listing as revenue.** A listing is `listing_open`; only a completed sale
  (which only the server can confirm) is income.
- **Dial direct when the proxy is down.** Fail-closed is the default at launch and at
  dial time; `direct` must be chosen explicitly.
- **Automate account creation / evade bans.** Guest accounts are used where available;
  the project does not build signup bypasses or ban-evasion systems.

**Risk statement, as the project states it:** the account is yours, everything the bot
does is something a human could do by hand, and use is at the user's own risk on
accounts they are willing to lose. Nothing is affiliated with Ankama.

---

## 2. Where it stands right now

### 2.1 Last measured live state (2026-10-27; last rows written ~20:01)

Four clients were running and all four have in-world sessions in the logs.
`tools/session_report.py` over each per-instance log (timestamps inside the report
are UTC):

| instance | sessions | wall | steps | ok/fail | objectives | quests completed | maps | manual interventions | recoveries (retry/recover/replan) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| dofus-01 (Brruh) | 8 | 137.1 min | 829 | 792/37 | **+1** | 0 | 8 | 17 | 52/449/228 |
| dofus-02 (Myaixd) | 6 | 151.9 min | 858 | 837/21 | **+8** | 0 | 7 | 8 | 16/282/139 |
| dofus-03 (Bruuh) | 5 | 105.1 min | 463 | 453/10 | **0** | 0 | 5 | 3 | 20/278/145 |
| dofus-05 (Bruih) | 4 | 100.1 min | 423 | 414/9 | **+1** | 0 | 4 | 3 | 17/251/130 |

Other readouts from the same logs:

- **Page build:** `2026-10-27l` dominates (686/701/393/389 rows), with older `h`/`g`
  rows. The current source is `2026-10-27q`; those logs **predate** the latest page
  fixes, so they do not test them.
- **Knowledge / quest catalogue:** 1,663 / 1,717 / 926 / 839 observed objectives;
  **0 not in store, 0 collisions**. The client catalogue held 834 / 868 / 473 / 425
  quests and 18,348 / 19,096 / 10,406 / 9,350 objectives. This is G29's capture
  working live.
- **Identity:** bound 12 / 10 / 10 / 9, drift 0. Character names are stable per slot.
- **Accept rows:** none yet — "no explicit accept rows and no dialog carried
  start_quest". G33's accept fix is newer than these page builds.
- **Command shadow:** not requested in any of these runs ("no status/row").
- **Run tail:** the last rows are waits, not errors:
  `{"kind":"wait", ..., "reason":"the policy had no action to take", "consecutive":28..384}`.
  The per-instance stop files were written at 20:01; the root `AUTOPILOT_STOP` at
  22:32. Diagnosing this long wait is an open live item.

### 2.2 What is proven live

- The page loads, the client logs in, and the game runs.
- Directives reach the client and come back (the bridge is alive; port from
  `bridge_port.txt`, currently 8893).
- `quest.advance` has produced verified progress (`advanced`), and a later six-step
  run advanced a quest through the NPC reply, changed maps, and moved a character
  from **level 1 / 3 objectives to level 2 / 6 objectives** with `present: true`
  keep-alive and no disconnect (AI_ARCHITECTURE §9.4).
- The AFK kick is the client's own timer and the keep-alive now reaches it:
  measured `marks 347 / age_ms 1 / inactive false / via setInterval-owner` after
  3 minutes on two driven clients (G1).
- The client version gate works: the updater moved the bundle from 1.74.9 to
  **1.74.10**, re-patched 23/23 rules (1/1 core), re-stamped asset URLs, and the guest
  press then reached the world (AI_ARCHITECTURE §11.1).
- Multi-instance addressing works: `target_instance=all` returned 4 envelopes on four
  open clients; comma lists and single ids behave as documented (§11.2).
- The asset cache serves ~99% of requests from disk (268 hits / 4 misses measured).
- The egress pin holds for a session; a session is one address.

### 2.3 What is proven only offline

- **Command parser**: 84.6% exact vs 53.8% rule on 26 held-out base phrasings
  (intent 96.2% vs 69.2%); per-row test intent 100% vs 56.5%, exact 65.2% vs 34.8%.
  Shadow only, default off; no live shadow session has run.
- **Tool policy**: 49,365 params, val 0.8599; paired simulator benchmark +1.25 quests
  at level 60 (11/12 seeds) and +1.50 at 800 steps (7/8). Simulation only; the live
  default is still `rule` at the CLI level (this machine's GUI is configured
  `policy=planner`, see §16).
- **Foresight**: 31,865 params; harness `gain_capture` 0.9679 vs teacher 0.9660 on the
  v2 corpus (stored artifact metrics on a later corpus: 0.9553 vs 0.9433); top-1
  0.8641; move AUC 0.7489; zero flats called promising. **Live transfer fails**
  (move AUC ≈0.5; artifact 0.336 on 223 rows; ECE 0.92; 100% of rows outside the
  harness range). Capacity sweep 31K → 4.4M params did not fix it; coverage is the
  bottleneck.
- **Tree ensemble**: 23 leaves, gain capture 0.97905 vs base 0.97734 (+0.00171),
  top-1 0.91137 vs 0.90591 (+0.00546), ahead in 3/5 folds. Harness-only; active only
  under `--policy planner --ensemble act`.
- **Combat**: solver, damage calculator, 50 solver tests, 1,980 spell levels
  validated, learned actor answers 18 of 64 own turns with a cast at p50 3.3 ms.
  **No live fight has been proven.**
- **Verifier**: rules are the authority. The neural head is gated off unless a
  `verifier_head.validation.json` contract with live/human labels, n ≥ 60 and
  `above_baseline: true` exists. No such contract exists, so normal launches run
  rules-only.

### 2.4 What is not working / unproven

- No complete live fight (`fight.join` → placement → turns → `GameFightEndMessage`).
- No live equip verb (G2); the first recorded quest chain dies on "Go and equip your
  new items."
- No vision or image model (G4).
- Network forecaster is dark: the Sentinel still imports torch, and
  `networkhealth_artifacts/model.pt` is absent (G5).
- Live fight calibrator absent (`models/fight_calibrator.json`), so `p_win` is null
  and unattended fights are refused by design.
- No live command-shadow session, so the parser cannot yet be promoted.
- Identity-by-account binding is shipped and offline-verified, but a live relogin
  proof is still owed (G9).
- The last live run's long "no action" wait is unexplained in the current docs and
  should be diagnosed first.
- Test coverage is still split: `agent.regression` (732 checks) + 20 JS suites +
  Go relay tests + C# selftests, but the C# bundle selftest, combat/data suites, and
  the full release build are not inside one gate (G8).

---

## 3. Architecture

### 3.1 Data flow

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

### 3.2 Ports

| port | owner | purpose |
|---|---|---|
| `8880 + slot` | relay | HTTP/CONNECT proxy the client is pointed at |
| `8890` | Sentinel | health/telemetry HTTP service + `nav.goto` name→map |
| `8891` | Combat AI | `POST /v1/combat/decide` (solver → ranker → live policy) |
| `8893` (fallback) | directive bridge | `POST /directive` → client tool call; the real port is in `%LOCALAPPDATA%\DofusFarm\bridge_port.txt` (currently 8893; 8894 has been seen on a multi-slot box) |
| `8895` | agent HTTP | `python -m agent.serve` — stdlib front for the agent layer |
| `40000` | tunnel | local SOCKS5 (userspace WireGuard or system WARP) |
| `5554, 5556, …` | legacy Android | emulator console ports (only with `--include-legacy-emulator`) |

**8892 is retired** (old ModernBERT stance server). Nothing should dial it.

### 3.3 Port facts that have burned time

- The host does **not** start the combat brain. `TryStartSingletonSentinel` starts the
  Sentinel with the embedded interpreter, which has no `jax`. Since 2026-10-26 the
  autopilot ensures the brain itself (`Autopilot._ensure_brain`, `--brain auto|off`),
  probes for an interpreter that can import it, and records a `kind:"brain"` row.
- A bound port is not a working tunnel. `wireproxy` binds before the WireGuard
  handshake completes; probes must do a real SOCKS5/CONNECT.
- Loopback never goes through the machine proxy: `agent/bridge.py` builds a
  `ProxyHandler({})` opener. A 502 from loopback previously looked like a dead bridge.
- `run_autopilot.py` is a script, not `python -m agent.autopilot`, because the
  embedded Python's `_pth` cannot resolve `..\dofus-network` from the install root.

---

## 4. Repository map and size

```
/
├─ DofusFarm.exe               host (built, 548 KB)
├─ dofus-net-proxy.exe         relay (built, 8.15 MB)
├─ setup.exe                   installer (105.7 MB on disk; payload 100.1 MiB, 513 entries)
├─ uninstall.exe
├─ compile.bat                 one-click build
├─ network.ini                 egress configuration
├─ ai_settings.ini             AI panel state + shortcuts
├─ proxies.txt                 upstream proxy pool (public; unreliable)
├─ accounts.txt                optional login:password pairs (sensitive)
├─ manual-login.flag           disables auto-login / guest seeding
├─ no-client-update.flag       disables the bundle updater
├─ bundle-selftest.txt         last C# patch-equality result
├─ ai-status-selftest.txt      last AI-pill formatting result
├─ asset_cache/                relay cache (assets, .meta, proxy.log) — 82 MB
├─ builder/                    Build.cs → Build.exe (the whole pipeline)
├─ desktop/                    C# host sources — 22,148 lines
│  ├─ mainform.cs              shell, grid, nav, launch paths (3,737)
│  ├─ pages.cs                 Settings / Shortcuts / Logs (1,082)
│  ├─ aipanel.cs               AI page + AiSettings (1,588)
│  ├─ farmengine.cs            instances, relays, tunnels, proxies (3,143)
│  ├─ proxysupervisor.cs       egress selection + failover (878)
│  ├─ netsettings.cs           network.ini model (290)
│  ├─ clientbundle.cs          transactional updater + runtime patcher (548)
│  ├─ WebView2ClientForm.cs    the client window (3,420)
│  ├─ WebView2Backend.cs       instance lifecycle (596)
│  ├─ agentbridge.cs           directive HTTP bridge (624)
│  ├─ prioritygovernor.cs      CPU/RAM arbitration (642)
│  ├─ watchdog.cs              hang detection (282)
│  ├─ hardware.cs              device profiles (218)
│  ├─ installer.cs             setup.exe logic (2,746)
│  ├─ uninstaller.cs           (530)
│  └─ client/www/              the served client bundle (~15 MB)
├─ scripts/
│  ├─ dofus-net-proxy.go       the relay (3,471 lines)
│  ├─ mobile-disguise.js       injected page agent (7,834 lines — the game adapter)
│  ├─ proxy_pool.js            verify/quarantine/scrape/write pool
│  ├─ harvest_proxies.js       append-only candidate harvester
│  └─ *.go, *.py               probes, proxy tools, data extraction
├─ tools/
│  ├─ patch-engine.js          build-time bundle patches
│  ├─ make-patch-rules.py      extracts them → patch-rules.json/.txt
│  ├─ apply-patch-rules.py     Python reference applier
│  ├─ apply-map-resilience.js  map-transition watchdog
│  ├─ test_*.js                20 page/JS suites (see §17)
│  └─ js-error-finder.js       bundle checker (currently red — G11)
├─ bin/warp/                   wireproxy, wgcf, warp-plus + configs
├─ bin/webview2/               Edge WebView2 bootstrapper
├─ runtimes/                   embedded Python 3.11 + win-x64/x86 helpers
├─ dofus-network/              the brain — 76,699 Python lines (excluding toolkit mirror)
│  ├─ agent/                   loop, planner, verifier, controller, tools, recorder
│  ├─ ai/                     training, serving, ONNX export, command model
│  ├─ combat/                 damage math, solver, live policy, duels, encounter
│  ├─ planning/               cost model, AND/OR planner, bandit, schedule
│  ├─ advisor/                read-only gear/spell/market advisor
│  ├─ knowledge/              canonical SQLite spine builder
│  ├─ navigation/             world/quest/resource graphs
│  ├─ networkhealth/          RTT/loss forecaster + Sentinel
│  ├─ sim/                    symbolic world + counterfactual corpora
│  ├─ pipeline/               THX-01 training/serving path (legacy-ish)
│  ├─ models/                 checkpoints + ONNX exports (~350 MB)
│  ├─ data/                   corpora, cache, knowledge DB (~289 MB)
│  ├─ trajectories/           25 JSONL logs (~257 MB)
│  └─ docs/                   18 design/status documents
├─ client/www/                 older bundle tree (not served; see CLEANUP)
├─ dofus-emu-cleanup-2026-10-25/  historical cleanup snapshot
├─ kaggle/                     networkhealth + tpu_selfplay kernels
├─ trajectories/               live policy decisions + sim fight outcomes
└─ resume.md                   this document
```

**Line counts:** C# 23,064 (desktop + builder), Go/JS 13,516, Python 76,699
(excluding `kaggle/toolkit` mirror), docs ~10,500 in the core set.

**Disk:** data 289 MB, trajectories 257 MB, models ~350 MB, asset cache 82 MB,
client 15 MB, installer 105.7 MB.

---

## 5. Host application (C# WinForms)

### 5.1 Window shell

One window, top nav, five pages. Pages are built once and toggled, not recreated:
the grid keeps its selection and the AI panel keeps its process handle.

| page | contents |
|---|---|
| **Instances** | toolbar + instance grid |
| **AI** | mode, target, policy, dry-run, goals, step budget, quick actions, live output |
| **Settings** | login flags, network card, folders, AI reset |
| **Shortcuts** | one row per action; press a key to rebind |
| **Logs** | live tail of `DofusFarm.log` and `autopilot.jsonl` |

Pages refresh when opened (`ShowPage` → `ReloadState`), not on a hidden timer.

### 5.2 Instance model

Each row has a name, slot, port, RAM/cores, optional per-instance proxy, and a phone
profile. Slot *n* maps to:

```
relay port  = base_port (default 8880) + n
sdk port    = 5554 + 2n          (legacy Android only)
profile dir = %LOCALAPPDATA%\DofusFarm\webview2_profiles\<instance>
```

Accounts are claimed so two instances cannot use the same login.

### 5.3 Shortcuts

Virtual-key integers in `ai_settings.ini`, applied through `ProcessCmdKey` so they
work anywhere in the window. Defaults: `F5` start, `F6` stop, `F7` send order,
`F8` toggle dry run, `F12` emergency stop. A key can only mean one thing; the editor
refuses duplicates.

### 5.4 Live AI indicator

Visible from every page: a nav pill updated once a second (`AI running - step 42 -
hdv.list advanced (model)`, `AI idle`, `AI stopped - last step 42`) and a green AI
nav button while the child process is alive. Green means the process exists; the text
comes from the last `autopilot.jsonl` line, because a live process with a stalled loop
and a dead process look identical from outside.

The AI page streams the loop's stdout line by line (`PYTHONUNBUFFERED=1`).

### 5.5 Logging

- `DofusFarm.log` — one line per event, `[HH:mm:ss] message`; rotated at 10 MB.
- `asset_cache/proxy.log` — relay log.
- `%LOCALAPPDATA%\DofusFarm\autopilot.jsonl` — one JSON object per agent step.
- `%LOCALAPPDATA%\DofusFarm\autopilot.<instance>.jsonl` — per-instance loop logs
  (two loops on one JSONL interleave two characters).
- `%LOCALAPPDATA%\DofusFarm\verdicts_human.jsonl` — human corrections.
- Startup always logs one line answering "why is nothing loading":
  `[NET] egress: auto: supervisor picks | relay 8880+slot | fail closed`.

### 5.6 Process hygiene

- Single instance via `Global\DofusFarmManager_SingleInstance_Mutex_2026`; a second
  launch focuses the first.
- `--bundle-selftest` runs headless, writes `bundle-selftest.txt`, exits 0/1.
- `--ai-status-selftest` runs headless, writes `ai-status-selftest.txt`.
- `Activate()` retries because a minimized WebView2 reports no window; this was a
  real click-verification bug.

### 5.7 Multi-instance addressing (2026-10-27)

`desktop/agentbridge.cs` resolves `target_instance` as one id, a comma-separated
list, or `all` (actually-open windows from `GetActiveInstanceIds()`). `RunOne(...)`
answers per instance: one addressee keeps the old envelope; several get
`{"targets": "...", "results": [...]}`. The stale-name fallback ("dofus-05 is not
active; using dofus-01") is limited to a single addressee.

`desktop/aipanel.cs` selects through a checklist (`TargetSlots`, persisted as
`target_slots=1,3`; "All instances" is exclusive) and starts **one autopilot child
per selected instance**, each with its own log. Idle training and the UI flip wait
for the last child, not the first.

Measured live: `target_instance=all` → 4 envelopes; `dofus-01,dofus-02` → 2;
`target_slot=3` → the old single shape, unchanged.

---

## 6. The client and its disguise

### 6.1 What is served

`desktop/client/www` is the Cordova bundle: `index.html`, `cordova.js`, `js/*`
shims, `build/script.js` (~5.4 MB, the whole game), `build/styles-native.css`.
It is served under `https://dofus.local/`, which the relay remaps to the real login
host for sockets. The current bundle carries `window.buildVersion="1.74.10"`; the
updater moved it from 1.74.9 on 2026-10-27.

`client/www/` is an older tree that is not served; `CLEANUP.md` documents why it is
kept but not used.

### 6.2 The shim layer

The bundle expects a phone. The shim provides just enough:

- **Cordova** — `cordova.js`, `CordovaFileSystem`, `CordovaFileCache`, `AppLoader`,
  `AppLoadingProgress`, `autoupdate`.
- **Adjust SDK** — `Proxy`-based no-op stubs. The client calls methods we do not ship;
  a Proxy answers anything, so the stub does not go stale.
- **`scripts/mobile-disguise.js`** — injected before the game boots. It overrides the
  device fingerprint (UA, WebGL vendor/renderer, screen metrics, serial, Android ID),
  installs the recorder, the FarmBot, the tool handlers, and the HUD.

**Only one copy exists.** `scripts/mobile-disguise.js` is the source the host injects
(`WebView2Backend.cs` reads it from `<exe dir>/scripts/mobile-disguise.js`). Editing
it needs no rebuild; a page reload applies it. The two duplicated copies were deleted
and a check fails if a second copy appears.

### 6.3 HUD

A small pill in the game window:

- label `DofusFarm AI: ON/OFF` — click toggles the fight AI (same as F2);
- draggable; position saved per instance in `localStorage["__dofusfarm_hud_pos"]`;
- **▶ Auto** posts `{"type":"AI_REQUEST","action":"start_auto"}` to the host, which
  opens the AI page and starts the loop. The page cannot spawn processes.

### 6.4 Page → host messages

| type | meaning |
|---|---|
| `CONSOLE_LOG` | console line, forwarded to the host log |
| `PLAY_LOG` | one human action, written verbatim to the play log |
| `FARMBOT_STATE` | fight AI on/off |
| `AI_REQUEST` | start the autonomous loop / request host state |
| `TOGGLE_FULLSCREEN` | window command |
| `diagnostics_report` | capability probe (WebGL, storage, UA) |
| `open_oauth` / `close_oauth` | Google login window |
| `webauth_exchange` | guest token exchange |
| `ping` | liveness |

### 6.5 Login paths

1. **Guest** — `POST https://auth.ankama.com/token/create-guest`, exchanged for a
   session. Guests are wiped by client updates and rate-limited per IP. A 403 from
   CloudFront means the guest quota for that address is exhausted, not that the code
   is broken.
2. **Real account** — manual login (`manual-login.flag`) or Google OAuth through the
   popup. In manual mode the host injects nothing and clears any stored guest,
   because a persisted guest identity otherwise overwrites the real session on reload.

`session.login` drives the login screen through the game's own handler:
`gui.loginScreen._playGuestBtn` registers a `tap` listener, so the tool emits that
event (a synthetic DOM click is ignored because `isTrusted:false`). It never writes
credentials, never calls `dofus.setCredentials`/`dofus.login`, and never opens an
OAuth popup.

### 6.6 Known black-screen causes (all fixed, kept as history)

| cause | symptom | fix |
|---|---|---|
| `styles-native.css` missing | blank page, no JS error | bundle validation |
| updater deleted `script.js` | blank page, no bundle | download to staging, commit after validation |
| `AdjustConfig` returning `{}` | boot exception | Proxy stubs |
| `appVersion` in the wrong query position | `SOCKET_LOST` | version param emitted last |
| patches lost after an update | login error / black screen | runtime re-patch |

---

## 7. The updater and the patch engine

The bundle is **not** shipped inside the APK: Ankama serves `manifest.json` and the
client downloads `script.js` at runtime. That is why a cached copy goes stale silently
and why "the update broke it again" was a recurring failure.

### 7.1 Root cause

`script.js` is patched at build time by `tools/patch-engine.js`. The old runtime
updater replaced `script.js` with the server's **raw** copy, so every patch vanished
on the first server update. Login failed or the page came up black, and nothing in
the logs pointed at the cause.

### 7.2 The updater is now a transaction

```
manifest.json
   ↓
download changed files → client/www/staging-<ticks>/
   ↓
apply tools\patch-rules.txt to the staged script.js
   ↓
validate only the staged files (size, not HTML, CSS non-empty, index.html if present)
   ↓
commit each file with a .prev copy, then drop .prev
   ↓
write client_version.json
```

Any failure discards staging and **keeps the working bundle**. Nothing is deleted
before its replacement exists.

- `build/script.js.bak` always holds the unpatched original.
- Self-heal on launch: if the live bundle is unusable (missing, <1 MB, or starts with
  `<`), it is restored from `.bak`.
- `no-client-update.flag` disables all of it.
- Adoption is refused when no core patch matched.
- One update transaction at a time (`lock (_gate)`) because `Launch All` fires one
  updater per instance at the same `script.js`.
- The asset `?v=` is re-stamped **after** the swap.

### 7.3 One description of a patch

`tools/make-patch-rules.py` extracts rules from `patch-engine.js` into:

- `tools/patch-rules.json` — readable, used by the Python reference applier;
- `tools/patch-rules.txt` — base64 lines, parsed in ~20 lines of C#.

The build-time JS engine and the runtime C# patcher read the same rules. Proof is
`DofusFarm.exe --bundle-selftest`:

```
rules: 23
matched: 0, 1, 2, 3, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31
skipped: 6..12: needle missing
patched 5365150 chars, live 5365150 chars
RESULT: IDENTICAL
```

`IDENTICAL` means applying the shipped rules to the unpatched backup reproduces the
live patched bundle character for character. If a future build moves the code a rule
targets, that rule is skipped and the bundle is still adopted (a degraded patch set
still runs the game).

### 7.4 The 1.74.10 update (2026-10-27)

Two measured bugs had prevented the updater from ever succeeding:

1. **A reused `WebClient` loses its headers.** .NET applies `wc.Headers[...]` to the
   first request and empties the collection afterwards, so the second request went out
   with `UserAgent = null`; the origin answered 403 without the mobile UA. Fixed by
   setting the UA inside `GetWebRequest`.
2. **`Validate(staging)` demanded `index.html`.** A manifest update carries only
   `build/*`, so every successful download was rejected. Fixed to judge only staged
   files.

Live evidence: `patched staged bundle: 23/23 rules, 1/1 core`, `2 file(s) updated and
patched`, `asset URLs re-stamped: ?v=5e0ce3abcf`, `client_version.json` written, and
the footer changed from `Build v1.74.9` to `Build v1.74.10`, after which the same
guest press reached the world.

**Rule for the future:** a 403 from Ankama is evidence about *our request* until
proven otherwise. Reproduce with two clients (curl and .NET, fresh and reused) before
blaming the network.

---

## 8. Network layer

Three layers, each with one job.

### 8.1 Relay — `dofus-net-proxy.exe` (Go)

An HTTP proxy with `CONNECT` support that also caches the game's static assets.

**Routing.** The client resolves its socket against the page origin, so it asks for
`dofus.local:443`, a name that exists only inside the WebView2 mapping. The relay
rewrites it:

```
dofus.local:443  →  dt-proxy-production-login.ankama-games.com:443
```

**Egress.** Two upstream shapes:

- `socks5://host:port` — destination resolved locally before the handshake.
  Cloudflare's SOCKS cannot resolve Ankama's game domains (measured: CONNECT by name
  → code 0x04; by IP → 0x00).
- `http://host:port` (optionally with credentials) — plain CONNECT.

**Fail-closed.** If the upstream dies, dials are refused with an explicit error. They
are not retried direct. `-allow-direct` is the only opt-out and it logs every time.

**Cache.** `asset_cache/` holds every static asset plus a `.meta` sidecar, a
hard-linked mirror at `files/<host>/<path>`, a negative cache for deterministic
403/404s, neighbour-map prefetch, and an optional graph crawler. Measured 268 hits /
4 misses (~99% served from disk); ~9,700 files, 82 MB.

**Mods.** `<cache>/mods/rules.json` rewrites/blocks/patches CDN responses on the fly;
unpatched originals stay in the cache.

**Key flags:** `-port`, `-host`, `-upstream`, `-config`, `-dns`, `-cache`, `-quiet`,
`-log`, `-allow-direct`, `-token`, `-serial/-uuid/-model/-manufacturer/-ua/-gl-vendor/
-gl-renderer`, `-texture-size`, `-referer`, `-assets-url`, `-precache-maps`,
`-precache-workers`, `-prefetch-neighbours/-radius/-tiles-depth/-workers`, `-crawl`,
`-crawl-max`, `-crawl-rate`, `-crawl-tiles`, `-mods-dir`, `-login-host`.

### 8.2 Supervisor — `ProxySupervisor` (C#)

One owner for "how does traffic leave". In `auto` mode it:

1. builds candidates — userspace tunnel, configured upstream, every entry in
   `proxies.txt`;
2. probes each for real (fetch `https://api.ipify.org`, record exit IP and latency);
3. **rejects any candidate whose exit IP equals this machine's own address** — a
   transparent proxy is not a proxy;
4. ranks: local tunnel first, then lowest latency;
5. re-tests every 45 s and fails over;
6. on a switch, rebuilds the running relays on the same ports, so a live instance
   reconnects instead of showing "Server busy" until relaunch.

A bound SOCKS port proves nothing: `wireproxy` binds before the WireGuard handshake
completes. The tunnel is probed with a real SOCKS5 `CONNECT`.

### 8.3 Modes

| mode | what runs | touches Windows routing? |
|---|---|---|
| `auto` (default) | supervisor picks | no |
| `wireproxy` | `bin\warp\wireproxy.exe` → SOCKS5 | no |
| `warp` | installed Cloudflare WARP via `warp-cli` | yes, all of it |
| `custom` | `upstream=` or `proxies.txt` round-robin | no |
| `direct` | nothing | no, and your IP is exposed |

System WARP installs an adapter and rewrites DNS for the whole PC. When it drops, the
machine loses normal internet too. **Settings → Repair my network** runs
`warp-cli disconnect`, `sc stop CloudflareWARP`, flushes DNS, then probes
`auth.ankama.com` and `1.1.1.1:443` and reports.

`warp` is gated behind `allow_system_warp=1`; without it the engine refuses and falls
back to the local tunnel.

### 8.4 The one-address pin (anti-kick)

Three identities matter to the server, and all three are pinned:

| identity | where it lives | pinned by |
|---|---|---|
| exit IP | relay `lastGood` | chosen once per live session; replaced only after the session is gone |
| device serial + Android ID | `asset_cache/identity/<instance>.txt` | `LoadInstanceIdentity` |
| browser fingerprint | derived from the same seed (`mobile-disguise.js`) | same file (seed = `serial:uuid:model`) |

Four holes were found and fixed:

1. **Relay failed over mid-session.** The first guard only refused to move while
   `connsActive > 0`, which drops to zero during a reconnect. The rule is now about
   the session: while a game tunnel is moving bytes, the pin never moves; after
   60 s with no bytes, the session is gone and a new address is allowed.
2. **Background traffic moved the exit.** Edge/Skype telemetry won a race and
   re-pinned the egress. `isGameDestination()` now lets only `dofus.local` and Ankama
   hosts establish the pin; everything else races around it.
3. **Concurrent first dials picked different proxies.** `pinMu` makes the first game
   dial decide; the rest wait. The first dial uses the supervisor's pick.
4. **A hop that accepts CONNECT then carries nothing trapped the session.** Registration
   is no longer activity; a game tunnel is proven by the first byte from the far side.
   The client's ClientHello is held, sent to the pinned hop, and a hop that answers
   nothing is replaced before the tunnel carries the session. The page retries the
   login `config.json` GET twice (network errors and 5xx only).

`setPinnedUpstream` writes only when the pin is nil, so one session has exactly one
address. `recoverPin` is the only writer that can replace a pin, and it runs only when
no game tunnel has moved bytes for 60 s. The log says `session egress re-pinned` so it
is not mistaken for a move; `exit changed` no longer exists.

**2026-10-27 refinement:** page HTTPS traffic (`config.json`, `primus.js`) was counted
as a live game connection because `isGameDestination()` covers every Ankama host, so
it pinned a dead egress for 60 s. Now only the real socket (`dofus.local` /
`dofus.localhost`) counts as a live session; page HTTPS still uses the pin but cannot
preserve it. Liveness requires bytes **received from the server**, not a client write
into a half-open socket. If every hop is in the 30 s death cooldown, `pinCandidates`
retries the chain. The page suppresses only the exact "Server busy" alert and reloads
after 1.5 s, with a three-reload budget.

### 8.5 What the line actually does (measured)

Maroc Telecom, 2026-10-22, and the result changed within the same day:

| time | UDP to Cloudflare | what worked |
|---|---|---|
| 13:35 | dropped outright — WireGuard handshake never answered, MASQUE/QUIC timed out, even UDP DNS to 1.1.1.1 failed | only a TCP proxy (`proxies.txt`) |
| 14:41 | normal — handshake completes, exit `104.28.220.131` | the userspace tunnel |

`wireproxy`, `warp-plus` (WireGuard and Psiphon modes) and `usque` (MASQUE) were all
tested while blocked; none could carry a byte. Nothing in the code changed between
the rows — the supervisor re-probed and switched. A hard-coded "use WARP" or "use the
pool" would have been wrong in one of those states.

Other measured facts:

- Public proxy lifetime: **11 alive at t=0, 0 alive at t=60 s**.
- Cache hit ~1 ms vs proxy round trip ~600 ms (~600×).
- CONNECT through a public proxy ~3–4 RTT (~1.6 s at 400 ms).
- Pool: 25 entries, 1–5 healthy at any moment.
- A 4-deep chain (`http→socks5→socks5→socks5`) survived a dead first proxy.

**Consequence:** a session cannot outlive its exit. The pin makes the address constant
*for a session*; it cannot make it persist *across* sessions.

### 8.6 CloudFront / CDN facts (not bugs)

- `auth.ankama.com` is behind CloudFront's WAF. A datacenter/proxy exit can get
  `403 Request blocked / Generated by cloudfront` before Ankama sees credentials. The
  popup names the cause and prints the Request ID; the log emits `[OAUTH-BLOCKED]`.
  A residential/mobile TCP egress for the whole session is the verified shape; a phone
  hotspot on mobile data works.
- `dofustouch.cdn.ankama.com/maps/*.json` returns 403 for a missing key (S3
  `AccessDenied`), identical on a direct connection. 16 map JSONs returned 200 and 234
  returned 403 on identical paths. Not the proxy, not the relay.
- Historical "skype telemetry moved the exit" is fixed; `config.edge.skype.com` is no
  longer able to establish or move the pin.

---

## 9. AI architecture

### 9.1 The division of labour (permanent)

| layer | owner | examples | can a model own it? |
|---|---|---|---|
| **Legality** | code | tool registry (21), arg schema, `args_for_tool` | never |
| **Safety / recovery** | code | stop file, kick/disconnect recovery, `_farm_allowed`, repeat/no-op guards | never |
| **Knowledge** | data | quest graph, map graph, client-reported observed quests | never (content, not decision) |
| **Decision** | model | what next, which tool, which dialog reply, whether the world moved | yes — that is the project |

The model may only **rank legal candidates**. A rejected proposal is a refusal, and
refusals are supervision (`FAILURE_TO_SUPERVISION.md`), not noise.

### 9.2 The agent loop (7 stages)

`agent/autopilot.py::_run()`:

1. **observe** — `state.snapshot` (pure read, no wire traffic) then `flatten()` so the
   planner sees flat keys (`map_id`, `level`, `inventory…`).
2. **choose** — a policy picks a tool for the current objective.
3. **guard** — `validate_call()` (schema) + `FORBIDDEN_TOOLS` veto + kick-signature
   guard.
4. **execute** — the directive is posted to the bridge.
5. **settle** — for world-changing tools, re-observe until the state changes.
6. **verify** — label `advanced` / `noop` / `failed` / `blocked`.
7. **decide** — `continue` / `retry` / `recover` / `replan` / `abort`; on the third
   repeat failure, `ThinkingLayer` proposes an untried, grounded alternative.

`max_steps=0` means unlimited (Full Auto); `steps_per_goal=8` is only the Order mode
budget. No wall-clock budget by default. Stops on goals met, no progress, `Ctrl-C`,
`--stop-file`, or `AUTOPILOT_STOP`. On exit it calls `safe.stop`.

Every step is appended with the observation before and after, the call, latency, and
label. `summary()` reports waits, keep-alive, identity, group refusals, braid rows,
etc.

### 9.3 Think-token pacing

The loop does not act the instant a call returns. A walk that returns `ok` has only
*started* the walk; judging it immediately scored every move as `noop` and retried an
action already in flight. Three timers fix that:

- **`--think` (default 0.25 s)** — after any world-changing action, the next decision
  is not taken before then. Read-only/listing tools are exempt.
- **`SETTLE_TOOLS`** (`{quest.advance, nav.goto, fight.join, farm.start}`) — after
  `ok`, `_settle()` re-observes every ~400 ms up to `settle_s` (default 3.0 s) and
  judges the first observation that differs.
- **quiet window** — `quiet_s = min(0.8, max(0.4, think_s + 0.3))`: settled once the
  state is stable for at least that long.

`_action_complete` is the predicate. This is a dwell + settle re-observe, not a fixed
sleep.

### 9.4 Policy modes

`--policy` choices: `rule` / `encoder` / `planner`; **CLI default `rule`**.

- **rule** — deterministic `QuestPlanner` only. No model, fully inspectable. The
  shipped default.
- **planner** — `NNPlanner` hybrid: the ONNX `tool_policy.onnx` does `argmax` over 21
  tool logits, but the deterministic planner still owns argument filling and safety
  gates. A wrong model argmax cannot produce an illegal call. `--ensemble act|shadow|
  off` controls the 23-leaf decision-tree stack over policy + foresight + K-means state
  context. The default `act` is honoured only when `models/tool_stack.json` carries a
  passed cross-validation gate.
- **encoder** — the 24.9M text encoder via `ai/encoder_serve.py`; `--model-dir`
  defaults to `models/encoder30_v15`.

**Measured learned-vs-rule face-off (2026-10-27):** `policy_faceoff.py --seeds 30
--steps 300` (noise + travel cost): learned wins **21, ties 9, loses 0**, mean
**66.10 vs 64.87 quests (+1.23, ~1.9%)**. At 600 steps / 20 seeds: wins 17, ties 3,
loses 0 (+1.90 quests). Simulation-only; the shipped default remains `rule` until a
live benchmark justifies switching. The learned planner is a distillation of the same
teacher, so it cannot exceed it by much.

### 9.5 The 21 tools (closed vocabulary)

Runtime order is `data/capabilities.json`:

```
knowledge.search  nav.goto    nav.follow   quest.list    quest.advance
farm.start        farm.stop   fight.join   fight.pass    inv.list
inv.sell          hdv.scan    hdv.list     chat.say      state.snapshot
safe.stop         knowledge.remember  knowledge.forget   fight.state
fight.plan        fight.estimate
```

`hdv.buy` is deliberately absent and additionally vetoed at runtime. Older docs that
say 16 or 18 tools are stale; the schema is 21.

Tool backends:

- **local** — `knowledge.search`, `quest.list`, `knowledge.remember`,
  `knowledge.forget`, `fight.plan`, `fight.estimate`.
- **directive** — `nav.goto`, `nav.follow`, `farm.start`, `farm.stop`, `fight.join`,
  `fight.pass`, `inv.list`, `inv.sell`, `hdv.scan`, `hdv.list`, `chat.say`,
  `state.snapshot`, `safe.stop`, `fight.state`, `quest.advance`.

`directive` means: the host posts JSON to `127.0.0.1:<bridge port>/directive`; the
page's handler performs it and reports back. The bridge port is read from
`bridge_port.txt` first; 8893 is only a fallback.

### 9.6 Deterministic planner and knowledge

`agent/planner.py` is the teacher. It reads the quest graph and produces legal tool
calls. Key behaviors:

- objective types and their required tool;
- `quest_id` as an entity reference;
- `DiscoverMap` uses only `map_id` (a `parameter0` is a text id, not a map);
- generic discovery walks an exit and prefers an unvisited neighbour;
- a tutorial gate blocks crossings/NPCs/cells the scenario forbids
  (`tutorial_blocked`);
- a stall retires a quest *in that situation*; a changed step/map/tutorial reopens it;
- permanent retirements (no tool can express the objective) are separate;
- named mobs stay named; a quests prompt does not farm unless the objective is a
  delivery;
- G31: only client-reported quests may be started;
- G33: a dialog offering a quest is accepted by `quest.advance {quest_id, accept:true}`
  only when the offer matches.

**Knowledge stores — two deliberately separate things:**

1. `knowledge.CanonicalStore` — static reference, built into
   `data/knowledge/dofus_knowledge.sqlite` (43 MB): 1,976 quests, 2,225 steps, 15,543
   objectives, 2,781 achievements, 9,173 achievement objectives, 2,000 monsters,
   6,472 NPCs, 21,554 items. All rows `source=dofusdb_cache`, `verified_live=0`.
2. `agent.knowledge_store.KnowledgeStore` — mutable discovery log with provenance,
   confidence, dispute, expiry (45 days), and a delete path. Only discoveries go in
   (never a copy of the cache). Sources are human > observed > derived > guess; a
   weaker source can only dispute a stronger one; three disputes drop a fact.

The planner reads the store, so a learned `npc_map` changes the next plan with no
retraining: `[quest.advance]` → `[nav.goto, quest.advance]`.

**Compact knowledge base:** 40.5 MB JSON → 3.0 MB on disk / 5.3 MB in RAM as compact
typed arrays plus one UTF-8 name blob (23× less memory than `json.load`).

**Observed quest catalogue (G29):** the page now publishes the client's own quest
catalogue (`quests_catalog` rows); `tools/ingest_observed_quests.py` persists one row
per objective to `data/knowledge/observed_quests.jsonl`, and the runtime loads it via
a stat-keyed cache. Live logs show 834–868 quests and 9,350–19,096 objectives per
character, 0 collisions. This is the replacement for the DofusDB-id mismatch (live
quest ids start at 3,841; the old cache stops at 2,546).

**DofusDB licence:** downloaded data/cache and the knowledge store are under a No-AI
licence. No re-syncs; capture from the client instead.

### 9.7 Command parser (offline, shadow-only)

`ai/train_command_encoder.py` freezes the 24.9M domain trunk and trains **0.29M heads**
(user-span adapter, intent, action conditioned on intent, BIO slot spans) on
`data/commands`: **1,619 train / 20 val / 23 test**, 13 intents.

Measured (`models/command_encoder30/metrics_eval.json`):

- 26 held-out base phrasings: **84.6% exact command vs 53.8% rule**; intent **96.2% vs
  69.2%**; slot exact 84.6% vs 69.2%.
- Per-row, including entity-swapped copies: test **intent 100% vs 56.5%**, exact
  **65.2% vs 34.8%**; val exact 70% vs 35%.
- Remaining base failures: Spanish knowledge query with a held-out entity
  (`busca Bouftou`), `all` for `arrête tout` / `para todo`, accented `d'équipe` span.
- A flat 0.25M word-level BiGRU overfits (train ~100%, held-out 30–45%) and is kept
  only as a negative baseline.

**It is not acting.** `parse_command` still defaults to the rule. `--command-shadow
auto` logs one `command_shadow` row per goal with both parses, agreement, and whether
the model would override; fail-open when torch/the artifact is absent. `--command-shadow`
is default off. `tools/command_shadow_report.py` compares over recorded logs:
7 real goals / 5,721 uses, 2 override goals (4,300 uses); the dominant "do all the
quests" is rule `quest/list` vs model `quest/run` at 0.958. `tools/session_report.py`
prints a shadow witness (rows, agreement, overrides). `tools/command_replay.py` pairs
goals with executed steps: 71 groups / 5,721 steps.

**Replay results:** the objective counter is flat, but 46/71 groups advanced by the
page verdict (25 stalled). Of the 10 groups the model would override, 9 advanced and
9 executed `quest.advance`. The model would change the planner's goal mode on **0/7**
real goals (6/7 agree; the only high-confidence overrides are quest list→run inside
the same mode). `intent_tool` now maps the parse to a registered tool; 9/10 override
groups executed the model's tool (`quest.advance`) versus 11/61 non-override groups
executing the rule's `quest.list`.

The 25 stalled groups are **24 short failures + 1 fight-start**, no read loops
(advanced groups are long, 65% reads). So the parser is not the stall fix.

**Routed per-instance adapter:** `router.json` (cosine, threshold 0.95) sends the 16
recorded user goals to fine-tuned heads and everything else to the base. All 7 real
goals map to the expected tool; the rule now routes `can you play the game ?` to Full
Auto (7/7 mode agreement, 5 quest action overrides / 5,717 uses). Base 43/43, adapter
16/16, 10-prompt canary row-for-row identical. Rollback: remove `router.json` or
restore `heads_base.pt`. The adapter guard includes `canary_accepts`, so future
per-instance updates cannot change list/advance/stop/sell/travel/follow.

**Deployable form:** `command_encoder_int8.onnx` (25,813,919 B, one graph with both
head sets) + `command_onnx.json` + tokenizer, loaded by `agent/command_onnx.py`.
INT8 parity is 0/59 effective rows; the torch-free wrapper matches torch on 0/76
texts. The FP32 graph/torch heads are excluded from the installer. `live_ready`
reports the ONNX shadow + router ready; the client bridge is the only blocker, so no
live session has used the hook yet.

**Rejected:** `plz/pls` + auto/farm data pass (fixed live prompts but cost three
held-out base rows: test exact 84.6% → 61.5%; reverted); shared intent+action and
intent-only fine-tunes (lost base rows 25/22 → 21/18 even with 160 rehearsal rows);
pooled whole-utterance token adapter (tags got worse).

### 9.8 Foresight and the ensemble

The v2 architecture bet is a **learned progress model** restricted to the observable
delta the client can report, used to rank legal candidates, plus an explicit
escalation decision when all candidates look flat.

Shape:

```
observation → objective_features() → 42 floats
            + candidate tool one-hot (21)
            → 2-layer MLP (~0.4–2.0M target; v1 is 31,865)
            → gain_hat[1..DEPTH], move_prob, flat_state flag
```

Training labels come from harness forks: `data/tool_sft` rows carry per-candidate
`gains`, per-depth `curves`, `margin`, `trivial`. The target is a fact about the
world, not the teacher's opinion. Live transfer is tested on real logged transitions,
with `moved` defined only from observable deltas, reads labelled `moved=false,
was_read=true`.

**Foresight v1 measured** (`ai/train_foresight.py` + `agent/foresight.py`, 31,865
params, 51 s CPU, JSON arrays + numpy, no torch/ONNX):

- Harness v2 corpus (17,039 train / 2,349 val): `gain_capture` **0.9679 vs teacher
  0.9660**; top-1 0.8641; move AUC 0.7489; 0 of 282 true flats called promising.
- Numpy-vs-torch parity: gain 0.0, move logit 1.2e-7.
- Stored v1 artifact metrics on a later corpus: 0.9553 vs 0.9433, top-1 0.8171,
  move AUC 0.965, 6,649 ranked states.
- **Live transfer fails:** move AUC ≈0.5 (artifact 0.3363 on 223 rows), ECE 0.92,
  predicted gains in the tens of thousands, 100% of rows outside harness range. Two
  causes: a zero-variance z-score bug (fixed; `std < 1e-3` treated as 1.0) and the
  harness not covering the live state distribution (level 3 / 0 kamas / no accepted
  quest vs a 20–100 curriculum).

**Capacity sweep (Kaggle GPU, matched compute 4,000 steps, batch 1,024, lr 3e-4):**

| hidden | params | harness capture | harness move AUC | live move AUC | live progress AUC | live ECE |
|---|---:|---:|---:|---:|---:|---:|
| 128 | 31,865 | 0.9779 | 0.9149 | **0.4125** | 0.5903 | 0.8238 |
| 512 | 321,401 | 0.9781 | 0.8909 | **0.2958** | 0.6330 | 0.8179 |
| 2048 | 4,428,665 | 0.9813 | 0.8803 | **0.4299** | 0.6729 | 0.7830 |

139× the parameters moved harness capture +0.0034 and made harness move-AUC worse,
while live move-AUC stayed under chance. **Capacity is not the bottleneck; coverage
is.**

**Coverage gap measured** (`data/tool_sft_v3`, 366 real states; 92% featurised):

| feature | harness range | live range | verdict |
|---|---|---|---|
| `quest_level` | 0.005–0.500 | **0.000 only** | harness never starts with no accepted quest |
| `kamas` | 0.500–1.000 | **0.000–0.042** | harness never starts poor |
| `n_params` | 0.333–0.667 | 0.000–0.667 | harness has no zero-parameter objective |
| `active_count` | 0.200–1.000 | 0.200–0.400 | partly covered |
| `last_tool` | 0.059–0.588 | 0.000–0.824 | live is wider |
| `level` | 0.005–0.500 | 0.005–0.090 | covered — the level ladder worked |

100% of rows remain outside the harness range; the missing diversity is the **initial
state** (wallet, accepted-quest state, zero-parameter objectives), not the level
ladder. Until `sim/world.py` seeds those, gate 2 must not be claimed.

**Move head trained on real labels** (`ai/train_move_head.py`, 12,281 params, 1.6 s):
val AUC moved 0.8942 / progress 0.8416; test AUC moved **0.685**, progress 0.508;
base rate 6.05% moved on test. This is the honest live-label model; it is not wired
into the acting policy.

**Tool ensemble** (`ai/train_stacker.py` → `models/tool_stack.json`,
`agent/ensemble.py`): stacks the 49K ONNX tool policy (`policy_logp`, `policy_rank`)
and the 31K foresight net (`gain_z`, `gain_rank`, `move_logit`, `curve_z`) plus four
K-means state-cluster distances. Three meta-learners are fitted on identical inputs:
linear listwise, depth-5 CART (23 leaves), and 40-round gradient boosting. Whole-
episode 5-fold CV on `data/tool_sft_v3/val` (1,385–1,500 states/fold, 107 episodes):

| ranker | gain capture | top-1 vs fork winner |
|---|---:|---:|
| tool policy (base) | 0.97734 | 0.90591 |
| foresight (base) | 0.97696 | 0.90429 |
| linear stack | 0.97663 | 0.90407 |
| gradient boosting (307 leaves) | 0.97735 | 0.90567 |
| **decision tree (23 leaves)** | **0.97905** | **0.91137** |

The tree is ahead of both bases in 3/5 folds: +0.00171 capture, +0.00546 top-1. It
passes the activation gate and is used by `--policy planner --ensemble act`; `shadow`
logs the choice, `off` disables it. The deterministic planner still fills arguments
and owns every safety guard. **Caveat:** harness-outcome evidence only; the live
transition corpus has 1,827 actions (14.0% moved, 1.5% progressed), so gate 2 is
unclaimed.

### 9.9 Verifier and controller (the second agent)

`agent/verifier.py` labels each step from three sources in priority order:

| source | when | what |
|---|---|---|
| **human** | a reviewer wrote a verdict | `verdicts_human.jsonl`, wins over both |
| **model** | `--verifier-model` points at an artifact | `verifier_head.onnx`; the GUI auto-loads only with a validation contract |
| **rule** | always | the baseline the model must beat; fallback for read-only tools |

Each label is `verdict` (`advanced` / `noop` / `failed` / `blocked`) + `residual_s` +
`drift` + `source`. Drift is three explainable contradictions: kamas dropped without
a purchase, the map changed on a non-movement tool, the observation lost fields it
had before.

`agent/controller.py` owns the objective, progress, a per-cause strategy table, and
failure memory. It returns a decision the loop must obey:

| decision | when | effect |
|---|---|---|
| `continue` | step advanced | ask the policy as usual |
| `retry` | first failure of a tool | same call once more |
| `recover` | second failure | a concrete different call (`state.snapshot`) |
| `replan` | `not_mapped`, 3 failures of one tool, or two fresh drifts | host/scheduler asked to replan |
| `abort` | no recovery left | the goal stops |

Failure causes are separated: `wrong_target`, `stale_state`, `incomplete`,
`invalid_assumption`, `plan_wrong`, plus `unknown`/`unavailable`. Drift on a stale or
identical observation is not counted.

**Verifier head status:** four artifacts exist (`verifier_head.{json,onnx,pt}` +
`_int8.onnx`), 33 features / 4 classes. The reported 1.0 validation accuracy is
circular (rule labels on rule features). Since 2026-10-27 the GUI auto-loads it only
if `models/verifier_head.validation.json` exists with `labels: live|human`, `n >= 60`,
`above_baseline: true`. No such contract exists, so normal launches run rules-only.
Read-only tools are never model-labelled regardless. An explicit `--verifier-model`
still overrides for an experiment.

### 9.10 Model inventory (what is actually loaded)

| artifact | size / shape | in the loop? | evidence |
|---|---|---|---|
| `models/tool_policy.onnx` + `.json` | 42 features → **21** logits, 49,365 params, val **0.8599** | yes, only under `--policy planner` | `planner_nn.py` argmax over 21 |
| `models/encoder30_v15/` | 24,874,131-param text encoder; `encoder_int8.onnx` 25.4 MB | yes, under `--policy encoder` | `ai/encoder_serve.py` |
| `models/command_encoder30/` | 24.9M frozen + 0.29M heads + routed adapter; INT8 ONNX 25.8 MB | shadow only | `agent/command_shadow.py`, `command_onnx.py` |
| `models/verifier_head.onnx` | 33-feature / 4-class | **gated off** | no validation contract |
| `models/foresight_v1/`, `foresight_live_v1/` | 31,865 + 12,281 params, JSON | off (measured, not wired) | `agent/foresight.py` |
| `models/tool_stack.json` | 21 KB, 23 leaves | with `--policy planner --ensemble act` | `agent/ensemble.py` |
| `models/encounter_selfplay.pkl` | PPO/PBT actor ~0.2M | yes, inside `combat_ai_server` | `live_policy.py` |
| `models/encounter_pbt.pkl`, `duel_value_v4_0.pkl` | combat experiments | optional | benchmarks |
| `networkhealth_artifacts/model.pt` | RTT/loss forecaster | **absent** | Sentinel falls back to telemetry |

No vision/image encoder exists anywhere. `encoder30_v15` is text-only; the
"vision inspector" in the Sentinel is regex over text.

### 9.11 Parameter budget (target architecture)

| piece | params | active per decision |
|---|---:|---:|
| frozen domain trunk | 24.9M | 24.9M (text rows only) |
| command heads (+ adapter) | 0.29M + 0.29M | one branch, routed |
| foresight net (target) | ≤2.0M | ≤2.0M |
| combat actor | ~0.2M | 0.2M |
| **total shared** | **≤30M** | **≤10M active** for the non-text path |

The trunk is only paid for on text (a prompt or NPC text). A pure state→tool decision
never runs it, keeping 12 instances inside the p95 budget. The earlier 70M and 400 MB
ceilings were withdrawn as unnecessary. No 100–135M dialog expert (rejected by the
user).

### 9.12 Data factory

| dataset | producer | rows | labels | limits |
|---|---|---:|---|---|
| `data/commands` | `tools/build_command_dataset.py` | 1,619 / 20 / 23 | intent, action, BIO slots | 26 held-out base phrasings; near-copies excluded |
| `data/tool_sft` | `sim/tool_choice.py` | 25,023 / 2,782 (v1); v2/v3 variants | per-candidate gains/curves/margin/trivial | harness world |
| `data/tool_sft_v3` | regenerated 2026-10-27 | 14 MB | same | low-level ladder still not the coverage gap |
| `trajectories/*.jsonl` | live pages | 4,785 rows here; 73,081 wider | none until transitions | 4.5% move rate; `objectives_completed` was 0 |
| `data/transitions/` | `tools/build_live_transitions.py` | 1,827 actions | moved/progress | 14.0% moved, 1.5% progressed |
| `data/auto_live/` | `tools/auto_live.py` | unattended session output | outcomes | new, uncommitted |
| `data/command_adapters/user_goals.jsonl` | hand-labelled live prompts | 16 | intent/action | per-instance, may never enter base train |

**Live transitions (target, being built):** `(obs_before, tool, args, obs_after,
moved)` from consecutive `kind:"step"` rows in one session, keyed by instance and ts.
`moved` is the OR of observable deltas only: map, x/y, dialog presence/reply_count/
reply_id, objective `stepId`/`finishedCount`/parameter counters, `in_fight`, group
ids, inventory/kamas. Reads are `moved=false, was_read=true` so a model can never earn
credit for looking.

**Harness integrity findings (2026-10-27):**

1. The counterfactual factory was generating **zero rows** and exiting 0. Cause: G31
   only lets the planner start a client-reported quest, and the harness reported
   nothing (no `dialog` block), so every curriculum id was skipped. Fix (sim side
   only; guard not weakened): `sim/world.py` publishes the offer (`offer_here()`,
   `observe()` emits `{"open": true, "start_quest": id, "npc_id": …}`), and
   `place_at()` starts episodes where an offer exists. `_parallel()` now fails loudly
   on a zero-row run. Gated by `test_counterfactual_factory_alive`.
2. `QuestPlanner.focus` meant two things: an objective type (0–17) for the harness,
   and a quest id for the offer guard. The guard now consults `focus` only when it is
   not a member of `OBJECTIVE_TYPES`.
3. Feature-width drift: the Oct-22 `tool_sft` corpus was 41 features; runtime is 42.
   `ai/train_foresight.py` infers the width, refuses a ragged file, and refuses a
   stale width with the regeneration command. The regenerated corpus is
   `data/tool_sft_v2/`; the Oct-22 corpus is kept.

### 9.13 Training and Kaggle

- Pipeline: `prepare_encoder_sft.py` → `build_core_sft.py` → `train_from_scratch.py`
  / `thx_finetune.py` → `export_encoder.py` (ONNX + INT8) → `release_gate.py`. The
  gate refuses an export whose INT8 output differs from fp32.
- Training runs offline on Kaggle (account `tajdinenizar`; credential in
  `~/.kaggle/kaggle.json`, never logged). `--resume` continues from a checkpoint and
  corrects `planned_steps` so the schedule does not restart.
- Interactive GPU notebook (T4×2/P100) is preferred; the toolkit is synced by
  `tools/sync_kaggle_toolkit.py`. TPU is used only when a measured speedup justifies
  it.
- Keep outputs small (a 134 MB output broke `kaggle kernels output` twice; the
  artifact is the model).
- Encoder training: 3,000 steps, 987 s on the reference GPU; early stop on **call**,
  not tool (tool saturates at ~0.94 while call still moves).

---

## 10. Combat AI

### 10.1 Data and damage math

- Official class-spell database: 15 classes, 22 spells per class, 330 unique spells,
  1,980 spell levels, 2,040 parsed normal/critical damage lines, 204 healing lines.
  AP, range, restrictions, cooldowns, target limits, AoE, descriptions, source URLs
  retained. All 1,980 levels pass structural validation and solver-adapter conversion.
- Built from the official encyclopedia supplied by the user; the 17 common spells
  were not on those pages and were **not invented**. Official IDs differ from parts
  of the 1.74.9 local cache; the datasets remain separate.
- `combat/damage_calculator.py` order: scale base roll by characteristic + Power →
  add flat bonuses → subtract fixed resistance → apply percentage resistance →
  subtract armor. Integer arithmetic; player percentage resistance capped at 35%;
  monsters/summons not incorrectly capped; critical fixed resistance only on crits;
  crit chance = base + Critical Hit stat clamped 1–100; healing scales with
  Intelligence/Heals, not Damage/Crit Damage; healing no longer inflates offensive
  rotation damage.

### 10.2 Solver (`combat/battle_solver.py`)

Geometry and rules ported from the 1.74.9 client, not guessed:

- cell id → map point: `t = id % 14 - id // 28`, point `(t + 19, t + id // 14)`; all
  560 points unique;
- fight walkability: `(cell.l & 5) === 1`;
- fight movement cardinal only, one MP per step (the bundle's `sqrt(2)` diagonals are
  overworld pathfinding, not legal fight moves);
- line of sight: client Bresenham variant; actors block every line cell except the
  target; every line cell needs sight bit 2;
- spell ranges are rings: diamond by default, cross for `castInLine`, diagonals for
  `castInDiagonal`, over `minRange..range`;
- effect IDs: 96–100 damage water/earth/air/fire/neutral, 91–95 steal, 108/143 heal,
  81 heal;
- rolls mirror `getMinMaxDamageFromRawEffect`: `diceNum` min, `diceSide` max, zero
  `diceSide` = fixed.

The solver generates legal move-and-cast nodes only when AP, MP, range, LOS,
occupancy, castability, per-turn/per-target limits, cast interval, and cell
restrictions permit. It prices damage/healing through `DofusDamageCalculator`,
including the resistance order, 35% player cap, shields, crit rolls, crit chance.
It runs a one-turn beam search (width 16) rewarding effective damage, lethal kills,
and effective healing, caps damage at remaining HP, prefers not to spend MP when
already in range, and reports unmodelled utility/area spells in `notes` instead of
treating them as no-op damage. It returns a plan only; it never casts, moves, ends a
turn, or sends a packet.

Known limits: only direct single-target damage/steal/heals are scored; buffs, traps,
glyphs, push/pull, AP/MP removal, AoE footprints are not planned; `max_cast_per_fight`
is exposed but not enforced; team sides need live confirmation; crit chance and the
35% vs bundle's `MAX_RESISTANCE: 50` need live verification.

`fight.plan` exposes the solver through the tool contract. `fight.state` is a
zero-argument, read-only directive with the board fields the solver needs. Both are
kept out of the verifier's width-locked `TOOL_ORDER` because that one-hot vector is
33 features; appending a class would break the current ONNX head.

### 10.3 Live pipeline (`:8891`)

Three stages, each optional, fixed order — the solver defines legality, learned parts
only rank among legal plans:

1. `battle_solver.py` — exact beam search over legal turn plans (width 16).
2. `plan_value.py::rank_plans()` — a learned ranker that reorders the solver's legal
   plans; it cannot invent an illegal one.
3. `live_policy.py` — the PPO/PBT actor, realised onto solver-legal plans, served as
   `POST /v1/combat/decide`.

The actor is `models/encounter_selfplay.pkl` (~0.2M), started by the autopilot through
`combat/brain_service.py` (`--brain auto|off`). The loop records a `kind:"brain"` row
(`started`/`reused`/`no_interpreter`/`port_held`) so "who decided the turns" is a
field, not an assumption.

### 10.4 Measured status

- Offline: 50 battle-solver tests passed; 60 combat/data tests; solver self-check.
- All 156 recorded `fight.state` boards initially failed the actionability contract
  because the page read characteristic-sheet field names (`max_hp`, `ap`, `max_ap`,
  `mp`, `max_mp`, `initiative`) that are not in the served bundle. The page now reads
  `effectiveMaxLifePoints`, `actionPoints`/`maxActionPoints`,
  `movementPoints`/`maxMovementPoints`, `initiative` for monsters and the character;
  the gate pins that no name outside the bundle can be read again (G25).
- With the missing max values supplied, all 64 of the model's own turns become
  actionable and the trained actor answers **18 of 64 with a cast** at p50 3.3 ms —
  inside the page's 4 s fetch budget. Offline measurement on real geometry.
- **No live fight has been proven.** `executor.py` records `live_calibrated: false`;
  `models/fight_calibrator.json` is absent; `fight.join`/`farm.start` were essentially
  never reached in the recorded history because runs stalled on talk objectives.
- The fallback fight planner (`_planFightAction`) is what the server has actually seen.
  It is now behaviorally tested (10 checks): targets lowest-HP enemy, respects min
  range, filters unaffordable/refused spells, ignores buffs/heals as attacks, moves
  toward the nearest enemy with the client pathfinder, passes with no MP.
- `fight.estimate` gives turns-to-kill, incoming damage, expected HP loss, missing
  inputs, and `p_win` (null until ≥20 resolved live fights with wins and losses; ECE
  gate 0.10). Unattended fights are refused by design while `p_win` is null.

### 10.5 Ladder and self-play

- `combat/duel.py` — 1v1 duels on real map cells with real class kits.
- `combat/normal_build_duel.py` — symmetric level-200 normal builds, 12/6/6 caps,
  Temporix excluded.
- `combat/encounter/` — batched JAX env (AP/MP, ranges, resists, heals, initiative,
  team turn order), shared policy/value head, PPO self-play, scripted first opponent,
  sim-label exporter.
- `kaggle/tpu_selfplay/` — two-phase TPU kernel (heuristic bootstrap, then self-play)
  and a self-contained dataset builder.
- 64-row CPU smoke produced a silver calibrator at ECE 0.0299, which proves the
  pipeline but is **not** a live-calibrated probability. Sim rows carry
  `source: "sim"`; the default calibrator must remain a live artifact.
- A specialist ranker may rank solver-generated plans later, but it must not invent
  spell IDs, cells, or illegal actions.

---

## 11. Supporting packages

### 11.1 `advisor/` — read-only character advisor

Joins the live character report, the official class-spell DB, the Touch item/set dump,
and the client's price cache. Picks an element from spells the character can actually
cast, allocates grades under the client-derived point cap, scores gear and set
acquisition (buy/craft/farm), and ranks market buy/sell signals.

Entry points:

```bash
python -m advisor capture
python -m advisor report
python -m advisor selftest     # 122 checks, knowledge repo only
```

It never spends points, equips, buys, or sells. Live validation on a logged-in
character is still pending.

### 11.2 `planning/` — cost model and search

- `economy.py` — everything converted to minutes; `kamas_per_minute(player_level)` is
  the pivot: best `sum(dropped value × drop rate) × kills/min` over monsters you can
  beat. Buying and farming become comparable; `min()` does the rest.
- `planner.py` — AND/OR search over the game database:
  `acquire(item,qty)` → buy (kamas) OR farm (drops, level ≤ player+20) OR craft (AND
  over ingredients, recursive, cycle-guarded, depth 3) OR quest fallback.
- `bandit.py` — Thompson sampling over strategies.
- `schedule.py` — runs a plan, watches the clock, replans on `level_changed`,
  `cheaper_branch`, `drop_rate_drift`, `overrun`, `stale`.
- `trace.py` — residual percentiles, verified vs unverified.
- `experience.py`, `graph_memory.py`, `outcome.py`, `thinking_cache.py`, `kb.py`.

A level-1 character asking for a level-200 item gets a real answer: *buy it in 369
minutes by farming Boufton Pâlichon at 113 kamas/min*, with the rejection line
(*crafting would take 3,318 minutes*).

### 11.3 `navigation/`

`world_graph.py` (walkability/map graphs), `quest_graph.py`, `resource_graph.py`,
`nlp_intent_engine.py`. `nav.goto` name→map resolution is answered by the Sentinel.

### 11.4 `networkhealth/` — RTT/loss forecaster

Causal TCN `NetworkHealthTCN`, 52,816 params, 0.20 MB, per-horizon heads for log-RTT,
RTT variance, jitter, loss, degradation logit, plus one anomaly score shared across
horizons.

Pipeline: `collectors/` (RIPE Atlas), `clean.py` (schema/timestamp/plausibility,
dedup, failure preservation), `features.py` (windowing, `TARGET_BLOCK`), `splits.py`
(time + series split with purge/gap), `baselines.py` (persistence/MA/EWMA/seasonal
naive), `training.py`/`evaluation.py`, `inference.py`
(`MultiInstanceNetworkHealth`), `bridge.py` (polls relays 8880–8883), `sentinel.py`
(port 8890), `provenance.py` (sha256 of every input/output).

**Historical measured artifacts (RIPE run 2026-10-17):** 156,748 observations, 143,049
examples, 103 series, 66 probes, seq 128, horizons 1/3/6; log-MAE 0.4654 / 0.2296 /
0.2684; 2.163 ms median (p95 2.718 ms). Baselines were computed on a different split,
so the report leaves cells `n/a` rather than mixing splits. The report warns that
public RIPE data cannot characterise the game client.

**Local run (Phase 5):** 11,066 observations / 12 series, 0.5 s cadence, split by
series; log-MAE h1 **0.1464**, live forecast 56.3 ms vs actual 63.3 ms. The RIPE
checkpoint was 0.3597 and failed on fast pings (log-gap feature out of distribution).

**Current status: dark.** `sentinel.py` imports torch (no torch in the shipped
runtime); `networkhealth_artifacts/model.pt` is absent; the architecture changed from
BatchNorm to GroupNorm(1,C) and SEQ_LEN 128→32, so old checkpoints are refused rather
than loaded with `strict=False`. Treat the network verdict as telemetry, not
prediction (G5).

### 11.5 `sim/` — the offline harness

`world.py` (symbolic world; noise, map preconditions, priced travel), `tool_choice.py`
(counterfactual tool corpus, fork per candidate), `decisions.py` (repair corpus),
`run.py` (planner | nn | random | rule), `rollout.py`.

The harness is the gym, not proof: it is built from the teacher's own rules, so it can
teach noise but not persistent failure. It is exhausted as a source of *repair*
learning; its remaining value is counterfactual tool/args data and curriculum coverage.

### 11.6 `pipeline/` — THX-01 path

`train_thx01.py`, `serve_thx01.py`, `benchmark_thx01.py`, `dofus_eval.py`,
`data_filter.py`, `kaggle_encoder.py`. The THX-01 lane was killed as a deployment
path; its benchmarks are superseded. Do not revive without a new measured reason.

---

## 12. Measured results (the numbers that matter)

### 12.1 Live baseline before the 2026-10-26 fixes (`BASELINE.md`)

```
trajectory view : 4 sessions, 57.0 min wall, 4012 steps, 3982 ok, 30 failed, objectives +0, maps 9
autopilot view  : 7 sessions, 40.6 min wall, 4287 steps, 3770 ok, 517 failed, objectives +0, maps 15
                  recoveries: recover 523, replan 407, retry 21
combined span   : 8 sessions, 172.5 min total, longest single session 125.1 min
verdicts        : advanced 3063, noop 707, failed 493, blocked 24
productive view : of 1,277 world-changing steps, 58 moved it (4.5%)
                  quest.advance 32/972 (3.3%)   nav.goto 26/222 (11.7%)
                  farm.start 0/59              fight.join 0/24
                  (3,005 of 3,063 advanced verdicts are state.snapshot reads)
decision causes : none 3063, unknown 455, plan_wrong 381, incomplete 361, unavailable 26
disconnects     : INACTIVITY 5106, (in_world:false with no reason) 142
failed by tool  : quest.advance 504, fight.join 48, nav.goto 25, state.snapshot 15, farm.start 3
```

Farm counters: of 9,164 rows carrying `farm_session`, 8,797 show `fights: 0`; the
maximum ever reached was `{"fights":2,"wins":0,"losses":2,"kills":0,"xp":0,
"drop_count":0,"drops":{},"kamas":2,"minutes":0}`. Kamas went 418 → 0 (peak 1,773):
net -418. `farm.start` succeeded 78/78 with identical args
(`{monster: 698229, kill_count: 100, auto_sell: false}`), i.e. accepted but inert.

This baseline predates every 2026-10-26 fix. It is a valid *before* picture and must
not be compared against anything except a re-run of the same commands.

### 12.2 Latest live sessions (after most fixes, page build `2026-10-27l`)

See §2.1 for the per-instance table. Totals across the four logs: 2,573 steps,
2,496 ok, 77 failed, **10 objectives**, **0 quests completed**, 31 manual
interventions, 105 retry / 1,260 recover / 642 replan. This is the newest honest
picture; the page build predates `q`, so it does not test the newest accept/keep-alive
changes.

### 12.3 Encoder (text policy)

| checkpoint | val tool | val call | gold tool | gold call |
|---|---:|---:|---:|---:|
| v11 | 92.9% | 68.5% | 87.3% | 71.8% |
| **v15** | **93.9%** | **69.8%** | **94.4%** | **77.5%** |

INT8: 25.4 MB, 62 ms/call, zero accuracy delta vs fp32 on the serving path.

### 12.4 Tool policy

49,365 params, hidden 192, depth 2, val **0.8599**, teacher agreement 0.857, lift
+0.003, macro-F1 0.2001 (common-class 0.5252), ECE 0.0304, best epoch 50. Per-class
recall is strong for the four common tools (`nav.goto` 0.825, `quest.advance` 0.956,
`farm.start` 0.847, `fight.join` 0.989) and zero for never-selected tools.

Paired simulator benchmark (corrected instruments, noise + travel cost + nearest):
at level 20 72.75 vs 72.08 installed (+0.67, 8/12 wins); level 60 83.58 vs 82.33
(+1.25, 11/12); level 100 repeats level 60 because quest availability saturates; at
800 steps 140.0 vs 138.5 (+1.50, 7/8 wins). The model is installed at
`models/tool_policy.onnx`; the previous 16k model is archived. Still DofusDB-
structural, not Touch-fidelity.

### 12.5 Combat

- Official DB: 330 spells / 1,980 levels.
- Solver: 50 tests, 1,980/1,980 levels convert.
- Live policy over 64 recorded turn boards: 18 casts, p50 3.3 ms.
- Live fight: **not proven**; calibrator absent; `live_calibrated: false`.
- The root `trajectories/live_policy_decisions.jsonl` has 14 rows (latest fight 289,
  round 0, my_id 4434089), but the baseline's first 8 rows did not come from the
  page; treat the file as instrument data, not a win rate.

### 12.6 NetworkHealth

See §11.4. Best honest local number: log-MAE h1 0.1464 on this line, 12 series,
0.5 s cadence. Not currently served.

### 12.7 Knowledge / data size

40.5 MB JSON → 3.0 MB disk / 5.3 MB RAM; SQLite spine 43 MB; observed quest catalogue
9k–19k objectives per character; asset cache 99% hit rate.

---

## 13. Open gaps (G1–G34)

This is the project's own gap register, condensed. The full evidence is in
`dofus-network/docs/KNOWN_GAPS.md` (2,294 lines). Read the numbered entry before
touching the area.

| # | gap | status |
|---|---|---|
| G1 | AFK disconnect is the client's own 5/10-minute timer; keep-alive never reached it | **fixed 2026-10-27, measured live** (capture of the tracker via `setInterval` owner; marks/age/inactive now in every snapshot) |
| G2 | No equip verb; mechanism identified | read-only inventory/presets shipped; `InventoryPresetUseMessage` candidate; **live capture + verb still owed** |
| G3 | Dialog question text now consulted | interim heuristic; talk path is where the run loses most points |
| G4 | No vision/image encoder | open by design |
| G5 | Network-health forecaster dark | torch import + missing checkpoint; ONNX port + retrain owed |
| G6 | Verifier head wired but unmeasured/circular | GUI auto-load gated on a live/human validation contract; no contract → rules-only |
| G7 | Live combat unverified | open; fallback planner now behaviorally tested; brain auto-start wired |
| G8 | Test gate was deleted; partially restored | `agent.regression` 732 checks; C# selftest/combat/data/release still outside one gate |
| G9 | Identity keyed by slot, not account | fail-closed binding shipped 2026-10-27, offline-verified; live relogin proof owed |
| G10 | `kaggle/toolkit/` drifts with no generator | generator shipped; dataset re-upload owed |
| G11 | `js-error-finder.js` red (9 bundle checks) | checker vs runtime-patch mismatch |
| G12 | Map-transition watchdog | pipeline closed 2026-10-26; rule executed offline; live stall owed |
| G13 | Claim that ended the connection re-sent 287 times | fixed (retry bound survives scene reset) |
| G14 | 455 repairs labelled `unknown` | fixed (decision rows log game error; cause classifier covers the phrasings) |
| G15 | No client window → 27 doomed directives | fixed (client-absent rescue) |
| G16 | Recovery table certifying itself (348/348) | fixed |
| G17 | 480 accepted claims scored noop because settle stopped too early | fixed (settle window; `_start_waits`) |
| G18 | 70% of steps were reads; 3,005 scored success | fixed (waits are `kind:"wait"`, cost no step/verdict/budget) |
| G19 | Repair queued for one goal dispatched under the next goal's label | fixed |
| G20 | Dialog ranker's primary signal decided nothing; no outcome recorded | fixed (dialog rows with outcome) |
| G21 | Dialog row could not say who ended the conversation | fixed (`ended_by`, `via`, `reply_id`) |
| G22 | Same reply asked 87 times on the same page | fixed (re-ask bound) |
| G23 | Recorded corpus cannot label a dialog choice | fixed instrument (`tools/dialog_labels.py` reconstructs reply→server outcome) |
| G24 | Nothing started the combat brain | wired 2026-10-26 (auto-start probe; `kind:"brain"` rows) |
| G25 | Live board not actionable — 156/156 snapshots rejected | fixed field names; gate pins bundle membership |
| G26 | `objectives_completed` 0 while a claimable objective sat at index 1 | fixed (scan the whole ready list) |
| G27 | Free-form `parameter0` planned as a monster | fixed (free-form plans nothing; scan moves on) |
| G28 | Every quest-progress number structurally 0 | fixed (`stepId` position in `dbQuest.stepIds`; completed list from client) |
| G29 | Quest cache ids stop at 2,546; live ids start at 3,841 | observed catalogue shipped; live population working (0 collisions); full store merge owed |
| G30 | Knowledge store resolved live objective ids to another game's objectives | fixed (`objective_id_collision` instead of foreign text) |
| G31 | 366 live quest-start requests named a quest the game never showed | fixed guard (session-scoped client-reported set; never-reported starts must be 0) |
| G32 | Quest offer is a link in the NPC text; page deleted it | read half fixed; accept half G33 |
| G33 | `quest.advance` cannot accept while a dialog is open | fixed 2026-10-27, offline-verified; live proof owed |
| G34 | An ignored action looked retryable (119× talk, 120× crossing) | fixed 2026-10-27, measured live (receipts + tutorial hold; 45 steps, max 4–9 repeats) |

**Unnumbered but real:**

- The last live run's long "policy had no action to take" wait (28–384 passes; three
  instances above 280).
- Manual interventions are now instrumented (trusted input events only) but the latest
  logs count 3–17 per instance; unattended Full Auto is not yet zero-intervention.
- `watchUpstream` flapping every 10 s is cosmetic (the flag is written, never read to
  gate a dial).
- WARP showing `000` is expected on this line (UDP blocked), not a regression.

---

## 14. Current uncommitted work (working tree at snapshot)

`git status` shows **36 files changed, +23,967 / −45,064** versus HEAD `0990695`, plus
untracked files. This is the newest work and is **not committed** (the project's rule:
no commits or pushes unless the user asks).

**Modified (highlights):**

- `desktop/*.cs` — client backend, WebView2 form, bridge, AI panel, client updater,
  main form, program, win32, farm engine.
- `desktop/client/www/build/script.js` and `standalone.html` — 1.74.10 bundle and
  shims.
- `desktop/client/www/client_version.json` — new.
- `dofus-network/agent/autopilot.py`, `executor.py`, `features.py`, `planner.py`,
  `planner_nn.py`, `regression.py` — G34 hold, command/ensemble integration, fixes.
- `dofus-network/combat/fight_estimate.py` — latest estimator work.
- `dofus-network/sim/world.py`, `sim/tool_choice.py` — offer publication and harness
  integrity fixes.
- `dofus-network/data/tool_sft_v3/{train,val}.jsonl` — regenerated corpus.
- `dofus-network/docs/CURRENT.md`, `KNOWN_GAPS.md` — latest status.
- `dofus-network/tools/live_ready.py`, `mutate_cause_gate.py`.
- `scripts/dofus-net-proxy.go`, `scripts/dofus_net_proxy_equiv_test.go` — pin fixes.
- `scripts/mobile-disguise.js` — keep-alive capture, group scan, page build q, login,
  quest accept, tutorial hold.
- `tools/test_*.js` — new/updated gates.
- `trajectories/live_policy_decisions.jsonl` — 6 more rows.

**Untracked (new):**

- `dofus-network/agent/ensemble.py` — candidate-level stack.
- `dofus-network/agent/foresight.py` — foresight runtime.
- `dofus-network/ai/train_foresight.py`, `train_move_head.py`, `train_stacker.py`.
- `dofus-network/ai/{pool_runner,pool_status,score_proxies,test_egress}.py` — restored
  network tools.
- `dofus-network/data/{auto_live,transitions}/` — unattended session data and live
  transitions.
- `dofus-network/docs/AI_ARCHITECTURE.md` — the new canonical AI map.
- `dofus-network/kaggle/foresight/` — capacity-sweep kernel.
- `dofus-network/tools/{auto_live,build_live_transitions,game_telemetry_scraper,host_control}.py`.
- `tools/test_boot_alert.js`, `test_geometry_watch.js`, `test_session_login.js`.
- `desktop/client/www/client_version.json`, `oauth-login.flag.paused`,
  `dofus-net-proxy.exe~`.

**Deleted:** `oauth-login.flag` (replaced by the paused marker).

The last commits are the "golf" optimization pass and the command-model commit. The
golf staging tree was deleted in `54ecd23`; only the encoder span-pruning merge
survived. The ledger claims 7 files at −28.4% bytes / −25.5% lines, with the planner
expected at −61%; a FLOP-doubling encoder pass was rejected. Do not resurrect the
staging tree unless you intend to re-run its gates.

---

## 15. Build and release

`compile.bat` compiles `builder/Build.cs` into `Build.exe`, then runs it:

| step | what happens |
|---|---|
| 0 | compile the Go relay (skipped if `go` is absent; the shipped exe is reused) |
| 0b | apply `patch-engine.js` to the client bundle |
| 0c | sync client shims into both distribution trees |
| 1 | `DofusFarm.exe` (Roslyn if available, else legacy csc) |
| 2 | `uninstall.exe` |
| 3 | `setup.exe`: stage the payload, compile with the embedded zip as a resource |

**Payload:** host, relay, client bundle, `bin/warp`, `bin/webview2`,
`tools/patch-rules.txt`, `runtimes/python`, `dofus-network` (selected models, INT8
command model, tool policy, encounter selfplay), `network.ini`, `proxies.txt`,
`accounts.txt`. Current payload **100.1 MiB / 513 entries**; `setup.exe` on disk
105.7 MB.

**Excluded from the payload:** `networkhealth_artifacts/`, training corpora, model
`encoder30_v15/model.pt` and fp32 `encoder.onnx`, fp32 command ONNX, verifier `.pt`,
`models/encounter_pbt.pkl`, `models/archive`, `_tune`, `kaggle/toolkit` duplicates.

**Legacy Android path** (SDK + system image, ~950 MB) is excluded by default;
`--include-legacy-emulator` puts it back. Nothing in the UI can select it; the engine
selector was removed.

**Requirements:** .NET Framework 4.x (or any .NET SDK for Roslyn), Go 1.21+, Node
(build-time patch engine only), Python 3.11 for the brain.

---

## 16. Configuration reference

### 16.1 `network.ini` (current on disk)

Current values: `mode=auto`, `socks_port=40000`,
`upstream=socks5://127.0.0.1:40000`, `base_port=8880`, `fail_closed=1`,
`extra_args=`, `wireproxy_conf=`, `log_path=`, `exit_proxy=` (empty).

| key | default | meaning |
|---|---|---|
| `mode` | `auto` | `auto` / `wireproxy` / `warp` / `custom` / `direct` |
| `socks_port` | `40000` | local SOCKS5 port the tunnel binds |
| `upstream` | empty | custom mode: `socks5://…` or `http://…`; empty = `proxies.txt` |
| `base_port` | `8880` | relay port for slot *n* is `base + n` |
| `fail_closed` | `1` | `0` allows direct when nothing is healthy — **exposes your IP** |
| `extra_args` | empty | raw flags appended to the relay command line |
| `wireproxy_conf` | empty | blank = `bin\warp\wireproxy.conf` |
| `log_path` | empty | blank = `<install>\asset_cache\proxy.log` |
| `exit_proxy` | empty | a proxy you own, for a sticky session address |
| `allow_system_warp` | unset | required before `warp` may rewrite system routing |

**Note:** the current `mode=auto` with a local `upstream` is a deliberate combination;
`auto` still tests every candidate, and `EnsureTunnel` brings up the userspace tunnel
when `custom` names our own tunnel port. The AI_ARCHITECTURE §11.1 measurement that
said `mode=custom` describes the moment of that bug; the file has since been set back
to `auto`.

### 16.2 `ai_settings.ini` (current on disk)

```
mode=auto
target_slot=1
target_slots=1,2,3,4
policy=planner
dry=0
steps=0
goals=do all the quests
order=
shortcut.start_auto=116
shortcut.stop_all=117
shortcut.send_order=118
shortcut.emergency=123
shortcut.toggle_dry=119
```

This machine therefore launches with the **hybrid planner** policy, not the CLI
default `rule`. Full Auto is `steps=0`, no wall-clock budget.

### 16.3 `client-version.ini` (optional)

Pins the version the page announces without a rebuild:

```ini
app=3.15.0
code=3015000
build=1.75.2
```

Without it the version is resolved from the installed bundle
(`DynamicVersionResolver`); if that fails the field is left alone rather than filled
with a stale constant. `mobile-disguise.js` never invents a version.

`app_version.json` currently reports `appVersion 3.14.3`, `versionCode 3014003`,
`buildVersion 1.74.9`, source `GooglePlayStore`, last checked 2026-10-27T20:52Z.
That is the **Play-store check**, not the served bundle; `build/script.js` carries
`1.74.10`, which is what the client uses.

### 16.4 Environment

| variable | effect |
|---|---|
| `DOFUS_FARM_HOME` | alternate install root for SDK/proxy lookup |
| `ANDROID_SDK_ROOT` / `ANDROID_HOME` | legacy Android only |
| `LOCALAPPDATA` | profiles, logs, autopilot/human labels |

### 16.5 Relay flags

See §8.1. `dofus-net-proxy.exe -config network.ini` reads `port`, `host`, `upstream`,
`dns`, `cache`, `quiet`, `log`, `allow_direct`, `referer`, `mods_dir` as defaults.

---

## 17. Running it and testing it

### 17.1 Install / launch

1. Run `setup.exe`; it unpacks the runtime, client bundle, relay, proxy binaries, and
   Python agent.
2. Launch `DofusFarm.exe`.
3. **Settings → Network**: leave mode **Auto**; press **Test**; you want `active: …`
   and at least one healthy candidate.
4. **Instances → Add slot**, then **Launch**.
5. First launch verifies the bundle, starts the relay, and creates the profile under
   `%LOCALAPPDATA%\DofusFarm\webview2_profiles\`.

### 17.2 Build from source

```bat
compile.bat
compile.bat --include-legacy-emulator
```

### 17.3 Tests and self-checks

**C# headless:**

```bat
DofusFarm.exe --bundle-selftest
type bundle-selftest.txt

DofusFarm.exe --ai-status-selftest
type ai-status-selftest.txt
```

**Relay:**

```bat
cd scripts
go test dofus-net-proxy.go dofus_net_proxy_equiv_test.go
```

**Agent (offline: no game, no network, no torch):**

```bat
cd dofus-network
python -m agent.regression       :: 732 checks, 0 failed (latest)
python -m advisor selftest       :: 122 checks
```

**Mutation driver (proves the checks are non-vacuous):**

```bat
python tools/mutate_cause_gate.py
:: 329 mutations; 349 anchors resolve exactly once; MUT_ONLY=<batch> for one batch
```

**JS page suites (20 files):** `tools/test_boot_alert.js`, `test_dialog_close.js`,
`test_dialog_links.js`, `test_fight_budget.js`, `test_fight_planner.js`,
`test_fight_state_fields.js`, `test_geometry_watch.js`, `test_group_scan.js`,
`test_intervention_instrument.js`, `test_inventory_equip.js`, `test_keep_alive.js`,
`test_npc_actor.js`, `test_page_build.js`, `test_quest_accept.js`,
`test_quest_catalog.js`, `test_quest_progress.js`, `test_session_login.js`,
`test_transition_watchdog.js`, plus `js-error-finder.js` and `auth-bypass-suite.js`.
`agent.regression` delegates several of them to `node`.

**Live preflight:**

```bat
cd dofus-network
python tools/live_ready.py --log trajectories/default.jsonl
python tools/live_ready.py --check-installer
```

`FAIL page build` means the last client recorded an older `agent_build` than the repo
(currently `2026-10-27q`); restart the app fully, because a reload keeps the old
injected page. `FAIL stop file` means a leftover `AUTOPILOT_STOP` would end the run on
its first step. `WARN installer` means `setup.exe` is older than the page;
`WARN client`/`WARN brain` mean the app or combat brain is not up yet.

**One-command live evidence:**

```bat
python tools/live_evidence.py
```

Preflight → bounded autopilot with `--command-shadow auto` → combined session/shadow/
replay report + autopilot summary. `--report-only` re-renders an existing log.

### 17.4 Manual commands

```bat
cd dofus-network
python run_autopilot.py --goals-file goals.txt --steps 0
python -m agent.autopilot --goal "do all the quests" --steps 20 --command-shadow auto
python -m agent.serve                 :: port 8895
python sim/run.py --policy planner --level 60 --steps 400 --noise --travel-cost
python tools/policy_faceoff.py --seeds 30 --steps 300
python tools/session_report.py <log1> <log2>
python tools/recovery_report.py <log1> <log2>
python tools/dialog_labels.py
python tools/brain_readiness.py
python tools/group_scan.py
python tools/policy_over_record_boards.py
python tools/ingest_observed_quests.py --coverage
python tools/quest_advance_shapes.py <log>
```

**Reading a run:** every step is one line
`[autopilot] <n> <tool> <ok|FAIL> <verdict> -> <args>`. `advanced` means the world
changed; `noop` means the call succeeded and nothing moved; `failed` means it did not
run. A run ending with `done: the client window closed` lost its window, not its mind.

### 17.5 Kaggle

Account `tajdinenizar`; credential at `~/.kaggle/kaggle.json` (never read or logged by
the agent). Private datasets: `thx-02-sim-toolkit` (code + KB), `thx-02-tool-corpus`.
Keep output small; the artifact is the model. TPU = instant interactive notebook,
not a queued batch push.

---

## 18. Troubleshooting

| symptom | most likely cause | what to do |
|---|---|---|
| "Server busy" / nothing loads | no healthy egress, or a dead login pin | Settings → Network → **Test**; read per-candidate reasons; see §8.4 |
| Instance does not start at all | fail-closed with no candidate | same as above; or switch to **custom** and press **Use proxies.txt** |
| Tunnel "listening but not passing traffic" | ISP drops WARP's UDP | use `custom` + a live proxy, or system WARP (443) |
| WiFi died after enabling WARP | system WARP took routing/DNS | Settings → **Repair my network**; stay on `auto`/`wireproxy` |
| Black screen, no error | bundle broken or unpatched | check `[UPDATER]`; `DofusFarm.exe --bundle-selftest` |
| Login fails right after a client update | patches not applied | updater should re-apply; verify `[UPDATER] patched staged bundle` |
| Guest login 403 | guest quota for your IP | real account, or wait |
| `SOCKET_LOST` | malformed `appVersion` param | version param must be emitted last |
| Relay port already in use | stale `dofus-net-proxy.exe` | the engine kills the one bound to that port on launch |
| Two hosts fighting | single-instance mutex | the second launch focuses the first |
| AI loop exits immediately | no goals / stop file present | type goals; delete `AUTOPILOT_STOP` |
| Long waits "policy had no action" | planner returned None (tutorial hold, no startable quest, or exhausted objective) | inspect the last step/dialog rows; this is the newest open live symptom |
| `Server busy` after a network change | a page fetch kept a dead pin alive | see §8.4; page now reloads with a 3-reload budget |

---

## 19. History, decisions, and rejected paths

### 19.1 Timeline (condensed)

- **2026-10-17 → 10-21** — client bundle, WebView2 host, relay, proxy pool, first
  encoder training (v11/v15), knowledge base, simulator.
- **2026-10-22** — network rewrite: `ProxySupervisor`, `auto` mode, fail-closed,
  transactional client updater, patch rules, one-owner egress. Measured that WARP's
  UDP is blocked on this line.
- **2026-10-23** — live plumbing night: seven interfaces used wrongly; the agent was
  blind, then unable to act. First `quest.advance ok advanced`; the simulator was
  declared exhausted as a learning source. Window-close question instrumented.
- **2026-10-24** — combat DB and solver; client-exact geometry; quest observation
  contract (groups, dialogs); knowledge spine (SQLite); v4 tool policy promoted after
  paired benchmark; dialog/quest plan.
- **2026-10-25** — the whole Python test suite was deleted (`smoke_verifier` and the
  rest); the live baseline was captured. This is why `agent.regression` exists.
- **2026-10-26** — the big gap-fix day: G13–G33, command parser dataset/heads,
  observed quests, keep-alive investigation, combat brain auto-start, board field fix,
  map resilience, identity binding. The baseline's `unknown` repairs and read-only
  "advanced" inflation were root-caused.
- **2026-10-27** — command adapter + INT8 ONNX; foresight/ensemble/move head; GPU
  capacity sweep; AFK keep-alive fixed live; G34 tutorial hold; multi-instance
  addressing; client 1.74.10 update; first live tutorial run with a fight join,
  quest advance, map change and level 2/6 objectives. Page build q.
- **Golf pass (2026-10-26/27)** — an isolated byte-optimization campaign in `golf/`;
  ledger claims 7 files at −28.4% bytes, planner expected −61%; a FLOP-doubling
  encoder pass was rejected; the staging tree was deleted after the encoder span-
  pruning merge. Only that merge survives.

### 19.2 Decisions D1–D24 (the important ones)

| # | decision | why |
|---|---|---|
| D1 | Encoder, not decoder | 55–62 ms vs 17.7 s for the same task |
| D2 | Early stop on **call**, not tool | tool saturates ~0.94 while call still moves |
| D3 | Anchors refreshed once per epoch | a 50-step clock made targets non-stationary |
| D4 | `price_rule=fixed` without a price is dropped, not guessed | 459/800 val calls rejected over one optional field |
| D5 | Resume, not restart | starts from 0.685 call instead of 0.20 |
| D6 | Latent bus + text sidecar | intent in latent, audit in text |
| D7 | User-facing language from templates, not a decoder | a decoder can hallucinate a price |
| D8 | Fast weights only in the L-cycle | bounded time correction; drift cannot cause a bad purchase |
| D9 | No evolutionary weight search | a farm episode is minutes; one generation ≈33 h |
| D10 | MoE deferred | +30 MB INT8 for a corpus too small |
| D11 | HDV through the in-game UI, not an API | UI is the precise source |
| D12 | Auto-update the client bundle at launch | a cached copy silently goes stale |
| D13 | Patches fail soft; a match >60 KB is refused | one stale boundary used to abort everything |
| D14 | Every unsupported native SDK gets a Proxy stub | client adds calls we do not ship |
| D15 | Drive the client's own path/tap | reimplementing duplicates obstacles and edges |
| D16 | Egress is a setting, never an assumption | system WARP rewrote the whole PC's routing |
| D17 | No wall-clock/step budget in the loop | a goal-driven loop ends when goals are met |
| D18 | One proxy supervisor owns egress; `auto` default | three places used to disagree |
| D19 | A bound SOCKS port is not a working tunnel | wireproxy binds before the handshake |
| D20 | Verifier ships as rules, not a model | the loop must run and fail to produce labels |
| D21 | Updater re-applies build-time patches | raw replacement erased every patch |
| D22 | Agent 2 is a controller, not a commentator | a verdict must change the next action |
| D23 | Labels weighted by source | human 1.0 / model 0.6 / rule 0.3 |
| D24 | Client version resolved, never hardcoded | a disguise announcing the wrong version is visible |

### 19.3 Rejected paths (do not rebuild without new evidence)

- **TRM-style recursion as the trunk** — tested twice (K=12, deep supervision) in two
  worlds; no gain over k=1. Kept only as the halting/escalation idea.
- **First failure → retry** — 2.3× worse than continuing in the simulator; 0/9 retries
  worked live because they retried an absent client.
- **Soft targets from the fork gain distribution** — +0.0003 gain, ECE 0.176 vs 0.012.
- **Smoother fork score (`--score cummean`)** — made ambiguity and quests worse.
- **Learned tool policy as a replacement for the teacher under priced travel** — the
  early model lost 19% (spurious `nav.goto`); later instrument fixes and the v4 corpus
  produced a consistent paired win, which is why it is opt-in now. Do not cite the old
  "learned loses" number without the instrument correction.
- **More data** — 32k → 90k rows: +0.002 val, −0.9 simulator quests.
- **Capacity** — 138× params for +0.0045 accuracy (tool policy); 139× for +0.0034
  capture (foresight); capacity is not the bottleneck.
- **100–135M dialog expert** — rejected by the user; the budget is not the bottleneck.
- **Shared-head specialisation** — lost held-out base rows; routed adapter won.
- **`plz/pls` data pass** — cost held-out rows; reverted.
- **Warm tunnel pool** — measured worse than nothing (20/25 s vs 5.5 s); do not revive.
- **`fasthttp`/`gnet` for the relay** — the bottleneck is the wire, not the CPU.
- **Listening before exit validation** — would let the first request leave on a
  different address.
- **Rewriting `WebView2ClientForm.cs`** — no feature upside, real risk.
- **Automated account signup** — CAPTCHA + CloudFront, and the one thing that
  plausibly gets accounts banned. Real accounts in `accounts.txt`, one per slot.

---

## 20. Non-negotiable invariants

1. **Drive the client, do not reimplement it.** Pathfinding, line of sight, map
   edges, and the fight state machine are the game's.
2. **Never connect direct.** The proxy fails closed; no upstream, no traffic.
3. **Capture before coding.** A verb without recorded traffic is a guess.
4. **Fail soft on the client, hard on privacy.** A stale patch may degrade; an IP leak
   must stop the instance.
5. **Numbers in docs are measured.** Anything unmeasured is labelled.
6. **A listing is supply, not revenue.** `listing_open` != kamas earned.
7. **Nothing irreversible runs unattended.** Buying stays human.
8. **A bound port is not a working tunnel.** Probe with a real CONNECT.
9. **An update is a transaction.** Never delete a live file before its replacement
   exists.
10. **Fail closed covers launching.** If egress cannot be wired and the mode is not
    `direct`, the client does not open.
11. **An outage must not require a relaunch.** A 40 s tunnel gap costs 40 s.
12. **Text is an observation, not a program.** The model proposes `reply_text`; the
    client resolves it to a reply id; the handler sends it.
13. **Unknown is not zero.** A missing level/price/monster input stays null and forces
    an abstain.
14. **Reference is not training.** Papycha's content signal forbids AI training;
    guides are RAG with attribution, never labels.
15. **The planner may estimate; only the server decides.** A win probability is a
    falsifiable claim; the outcome row is the answer.
16. **Every head abstains when its inputs are missing.** An abstain is a logged, valid
    decision; a guess is not.
17. **The model ranks legal candidates only.** Safety, legality, recovery, and
    knowledge stay in code/data.
18. **No commits or pushes unless the user explicitly requests them.**

---

## 21. What to do next (ordered by unblocking power)

This reconciles the three live plans (`docs/PLAN.md`, `AI_ARCHITECTURE.md`,
`TRAINING_PLAN.md`). The order below is the one that avoids repeating the project's
own history: prove the loop, fix the first hard stop, then train on real outcomes.

### P0 — diagnose and finish the live loop

1. **Explain the long "policy had no action to take" wait** seen at the end of the
   latest four logs (28–384 consecutive waits; three instances above 280). Read the
   last step/dialog/wait rows,
   the quest gate, and the tutorial hold. This is the immediate live blocker and is
   not yet in `KNOWN_GAPS.md`.
2. **Run one full session with page build `2026-10-27q`** (full app restart, not a
   reload). Acceptance: `session_report` shows the `q` build, `accept:` rows when a
   dialog offers a quest, `objectives +N` with N > 0, no `INACTIVITY`, no manual
   intervention, and `keep_alive.present` true.
3. **Prove one complete live fight** (G7): `fight.join` attacks one group, placement/
   ready complete, only the player's turns drive, a legal cast/move ends the turn,
   `GameFightEndMessage` arrives, and the quest resumes afterwards. Read the `brain`
   row and `live_policy_decisions.jsonl` `contract.value_ready` to answer "who decided
   the turns".
4. **Add the equip verb** (G2) because the first recorded quest chain dies on
   "Go and equip your new items": read-only inventory/presets shipped; capture the
   live accessor and the drag/preset packet, then add `equip.preset` behind the schema
   gate.
5. **Identity by account** (G9): live relogin proof that account A and B in the same
   slot get distinct `identity/*.txt` and distinct relay `-serial/-uuid`.

### P1 — make the learned parser and foresight real

6. **Run a live command-shadow session** (`--command-shadow auto`) and read
   `command_shadow_report.py` + `command_replay.py`. The parser is deployable but has
   never seen a live prompt. Gate: overrides are legal and advance; no held-out base
   loss.
7. **Fix the simulator's initial-state diversity** (§9.2): seed 0-kamas, no accepted
   quest, zero-parameter objectives; regenerate `tool_sft`; re-read `feature_skew`.
   This is the blocker for any sim→live foresight claim; bigger nets do not fix it.
8. **Re-measure foresight gate 2** (live move AUC, calibration, base rate) on the
   regenerated corpus and real transitions. If it still cannot beat chance, do not
   wire it.
9. **Run unattended sessions** (`tools/auto_live.py`) so outcome labels accumulate
   without a human. The runner already does preflight → verified launch → guest press →
   run; the missing piece is a session that produces non-zero objectives.

### P2 — promote one component at a time

10. **Escalation head + macro/micro split** once the curves are calibrated. The
    foresight net's `flat_state` flag is the seed of the escalation decision.
11. **First rule removal candidate: tool choice under a quest objective** — only after
    held-out outcome + live shadow gates. Keep the fallback.
12. **Dialog policy** — train on real reply→outcome labels (`tools/dialog_labels.py`);
    gate above the ranker, then shadow. The talk path is where the run loses points.
13. **Verifier** — labels from action→next-state deltas; gate above the rule baseline
    with honest ECE; only then may the GUI auto-load it.

### P3 — combat and world coverage

14. **Combat mechanics** — AoE footprints, buffs, traps, push/pull, AP/MP removal;
    predicted-vs-observed damage calibration from live fights.
15. **Fight calibrator** — ≥20 resolved live fights with wins and losses; ECE < 0.10;
    then `p_win` can gate unattended fights.
16. **Combat ranker** — only after the solver plan execution is live-proven; it must
    not invent spell IDs/cells/actions.
17. **Knowledge spine M1** — merge the observed Touch quest catalogue (18k objectives
    per character) into the store; resolve every live objective to typed entities;
    achievements and interior map names.
18. **Persistent exit / proxy model** — a VM or paid static proxy for session
    persistence (auth may still refuse datacenter IPs); train the hop-selection model
    only after it beats the heuristic on held-out sessions (`PLAN.md` #4).
19. **CDN pool question** — compare 1 vs 3 asset egresses; no increase in 403s.

### Explicitly not next

- A larger language model, a 100–135M dialog expert, MoE, or a latent bus. The budget
  is not the bottleneck; coverage and live outcomes are.
- Rewriting the client form or the relay for microbenchmarks.
- A warm tunnel pool.
- Unattended buying or account creation.

---

## 22. Documentation index and freshness

### 22.1 Where to read what

| file | role | freshness |
|---|---|---|
| `dofus-network/docs/CURRENT.md` | running-system map: services/ports, 7-stage loop, pacing, policy modes, model inventory, combat pipeline, 21 tools | **current** (2026-10-27) |
| `dofus-network/docs/AI_ARCHITECTURE.md` | canonical AI map: shipped/shadow/target, component inventory, gates, measured/rejected, build order | **current** (2026-10-27) |
| `dofus-network/docs/KNOWN_GAPS.md` | open defects with evidence (G1–G34) | **current** (2026-10-27) |
| `dofus-network/docs/BASELINE.md` | live baseline before the 2026-10-26 fixes | **historical before-picture** |
| `dofus-network/docs/TRAINING_PLAN.md` | training programme, budget, phases, gates | **current** |
| `dofus-network/docs/PLAN.md` | older living status/ordered work list | partly stale; status superseded by CURRENT/AI_ARCHITECTURE |
| `dofus-network/docs/PROJECT_LOG.md` | decisions D1–D24 with falsifiers | current reasoning, not status |
| `dofus-network/docs/ARCH_REASSESSMENT.md` | architectural argument | reasoning; status columns superseded |
| `dofus-network/docs/AGENT3_PLAN.md` | three-agent/dialog/knowledge design | design, not shipped |
| `dofus-network/docs/AGENT2_DESIGN.md`, `SECOND_AGENT.md` | earlier second-agent designs | historical |
| `dofus-network/docs/FAILURE_TO_SUPERVISION.md` | how failures become labels/tests | current |
| `dofus-network/docs/PROTOCOL_NOTES.md` | captured wire formats | current, grows with captures |
| `dofus-network/docs/PIPELINE.md` | data path corpus→train→serve→log | current, widths superseded by CURRENT |
| `dofus-network/docs/PLANNER_DESIGN.md` | tier architecture, RAM budget, no token CoT | `hdv.buy` example predates the ban |
| `dofus-network/docs/EXPERIENCE_LEARNING.md` | outcomes → posteriors without a reward model | current |
| `dofus-network/docs/LATENT_AND_ONLINE_LEARNING.md` | latent bus/online updates judgement | current decision, not shipped |
| `dofus-network/docs/CAPABILITY_REGISTRY.md` | tools as data | **stale: says 16, schema is 21** |
| `README.md` (root) | high-level overview, quick start, architecture | **partly stale** (see below) |
| `STATUS.md` | 2026-10-23 reasoning + kick history | superseded in part |
| `PLAN.md` (root) | 2026-10-23 next steps | historical |
| `now.md` | long historical record, 2026-10-23 | historical |
| `COMBAT_STATUS_AND_PLAN.md` | combat work + phases | historical; ordering caveat in its header |
| `CLEANUP.md` | cleanup findings (duplicate scripts, dead copies) | historical |
| `resume.md` | this document | **current snapshot** |

### 22.2 Numbers that changed; do not quote the old ones

| stale claim | current truth |
|---|---|
| 16 tools (`STATUS.md`, `CAPABILITY_REGISTRY.md`) | **21 tools** |
| 18 tools (`now.md`, older command notes) | **21 tools** |
| 11 patches (`README.md`) | **23 rules**; 16 matched, 7 skipped on the current bundle |
| 276 checks (`README.md`) | **732 checks** in `agent.regression` (latest) |
| 288 MB `setup.exe` (`README.md`) | **100.1 MiB payload / 105.7 MB on disk** |
| 13 patch rules / 11 active | **23 rules** after 1.74.10 |
| verifier auto-loads on every GUI launch | **gated** on a live/human validation contract; rules-only today |
| "the client update broke everything" | updater is transactional and re-patches; 1.74.10 proven live |
| "WARP works" | WARP's UDP is blocked on this line; `auto`/TCP proxy is the working path |
| "objectives_completed 0" as a model failure | it was a structural instrument bug (G28), fixed; latest logs show gains |
| "the learned policy is not wired" | `--policy planner` is wired; this machine's GUI selects it |
| "the model is 24.9M" alone | 24.9M is the **text trunk**; the command heads are 0.29M, the tool policy 49K, foresight 31K, combat ~0.2M |

---

## 23. Glossary

- **Egress** — how traffic leaves the machine (tunnel, SOCKS/HTTP proxy, or direct).
- **Pin** — the one exit IP a live game session is locked to.
- **Relay** — `dofus-net-proxy.exe`, the local HTTP/CONNECT proxy + asset cache.
- **Bridge** — the host's local HTTP endpoint that turns a directive into a page call.
- **Directive** — one JSON tool call posted to the bridge and executed by the page.
- **Tool** — one of the 21 legal capabilities in `data/capabilities.json`.
- **Step** — one observe→choose→guard→execute→settle→verify→decide iteration.
- **Verdict** — `advanced` / `noop` / `failed` / `blocked`.
- **Drift** — an explainable contradiction between the before/after observation.
- **Gain capture** — share of the best fork's progress a ranker achieves.
- **Fork / counterfactual** — replaying a state with each candidate tool to label the
  outcome without asking the teacher.
- **Harness** — the offline symbolic world (`sim/`) used to generate labels and score
  policies; not proof of live behaviour.
- **SFT** — supervised fine-tuning.
- **INT8 / ONNX** — the shipped quantized/runtime model formats.
- **ECE** — expected calibration error.
- **AUC** — area under the ROC curve; 0.5 is chance.
- **TRM** — Tiny Recursive Model; weight-shared recursion, tested and rejected as the
  trunk here.
- **MoE** — mixture of experts; deferred.
- **Full Auto** — `steps=0`, no wall-clock, autonomous goal loop.
- **Order mode** — one goal for a small step budget.
- **M0–M5** — the milestone ladder in `AGENT3_PLAN.md`: observation contract, dialog,
  fight estimate, strategist merge, TPU self-play.

---

## 24. Final honest summary

The project has solved the hard *plumbing* problems:

- a real client that survives updates and stays disguised;
- a relay that caches, routes, pins one egress, and fails closed;
- a host that launches, logs, and addresses many instances;
- an agent loop that observes, acts, settles, verifies, and recovers;
- instruments that can now see keep-alive, quest progress, dialog outcomes, group
  scans, identity binding, and command shadow;
- a live quest catalogue captured from the client itself.

It has not yet solved the hard *decision* problem:

- zero completed quests in the latest four logs;
- no proven live fight;
- no live equip;
- a learned parser that wins offline but has never acted;
- a foresight model that matches the teacher inside the simulator and loses to chance
  in the world;
- a large share of the step budget still spent on recover/replan and waits.

The next correct move is not a bigger model. It is: **diagnose the current long wait,
run one clean live session on the current page build, prove one fight and one equip,
then let the parser shadow and the outcome labels accumulate.** The project's own
architecture document says it best: capacity is not the bottleneck; coverage and real
outcomes are. That is the sentence to carry into the next session.
