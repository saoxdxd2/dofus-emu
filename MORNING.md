# Morning — what to run, in order

Everything below is committed. The client-side changes need **one page reload**; the C#
changes need a **rebuild**. Nothing needs a fresh login.

**One caveat before anything else:** `Build.exe` is still the 2026-10-22 binary and contains
the old `TrySyncClientShims`, so it recreates two unused copies of `mobile-disguise.js` on
every build. They are not loaded by any page — ignore them, or rebuild `Build.exe` from
`builder/build.cs` to stop it.

## 1. One click to load the new agent script

- **Rebuild** (`compile.bat`), start the app, then press **Reload client** in the AI panel, or
- **Without rebuilding:** restart the app and launch an instance as usual.

The page reload is what makes every JS fix take effect. `ai/verify_live.py` tells you whether
it worked — its first check after the bridge is `agent script is current`.

## 2. The check that matters

```bash
cd dofus-network
python ai/verify_live.py
```

It prints one line per primitive: **PASS / FAIL / SKIP**, with the reason. Read the first
FAIL — the later checks depend on it. SKIP means "could not run", never "works".

Expected shape once things are right:

```
[PASS] bridge answers                     port 8894
[PASS] a client window is open            instances=1
[PASS] agent script is current            2026-10-23b
[PASS] snapshot returns state             24 fields
[PASS] character is in the world          Aiismine level 18 on map 150736645 cell 342
[PASS] the character has health           120/240
[PASS] talk opens a dialog                npc template 447 (actor 1234567)
[PASS] a dialog reply is accepted         replied
[PASS] the talk chain reports its stage   replied 0 replies  (in_dialog=False, reply_handler=True)
[PASS] the character moves                cell 342 -> 305 (ok)
[PASS] NPC actors are visible             3 found
[PASS] monster groups are visible         0 found
```

The talk-chain line is the new one and it is the useful one. `walk -> talk -> dialog ->
reply` is four steps across two messages, and when it breaks every failure looks the same
from outside: "nothing advanced". The client now records which stage it reached —
`walking_then_talking`, `talked`, `dialog_open_no_replies`, `replied`, `npc_not_on_map` — so
the line says where the chain stopped instead of leaving you to guess.

Related: the handler no longer replies into a dialog whose reply list has not arrived.
`isInDialog` flips when the dialog opens; `replyIds` is filled a moment later, and replying
in that gap sent index 0 into a list that did not exist.

## 3. If health is 0/0

`hp 0/0` means the character is dead (or the stats are not loaded in this client state). A
dead character can walk but cannot talk, so revive first. `verify_live` flags this before the
talk check for exactly that reason.

## 4. Then the agent — and this time, leave the window alone

Press the in-game badge (the AI button) or the panel's **Start**. The goal is already
`plz do all the quests`, policy is pinned to the planner, steps are unlimited.

**Do not close the client window, and do not press the instance's stop/toggle in the grid
while the loop runs.** Six sessions ended because the window closed, and the log now says
`reason=UserClosing` — a click, not a kick. Let it run for at least 60 seconds.

Watch `%LOCALAPPDATA%\DofusFarm\autopilot.jsonl` and the panel log. What you want to see:

- `quest.advance ... ok advanced` — **this now works.** `advanced` means the observation
  really changed. A claim that lands shows up as `advanced` because `ready_objectives` is
  part of the state now.
- `[autopilot] the client is gone: … stopping` when the window closes — the loop says so
  instead of retiring a quest and claiming "goals exhausted".
- `[autopilot] done: <reason>` at the end, and the reason should be one you can explain.

And the two lines to send me if it stops early:

```
[dofus-05] closing: reason=… requested_by=…
[autopilot] done: …
```

`requested_by=the player (window close)` means you closed it.
`requested_by=WebView2Backend.Stop @ WebView2Backend.cs:283` means the app did, and that
call site is the bug.

## 5. If the relay ever looks guilty

```bash
python ai/diagnose_network.py --seconds 20     # run it WHILE connected
```

It reads the live relay's own `/__telemetry` (RTT, connections, failures) and reports a
verdict. An idle relay reports its *last* RTT, so sampling with no client connected measures
nothing. The network-health model itself cannot load right now (BatchNorm checkpoint against
a GroupNorm model); the tool says so and falls back to telemetry.

## 5b. If no client launches at all

That is almost always the **egress**, not the emulator:

```
WebView2 dofus-01: not started - auto mode found no healthy egress (tunnel blocked, pool dead)
```

`fail_closed=1` means the app refuses to launch rather than fall back to a direct connection,
which would expose this machine's address to the game. Ask the egress instead of guessing:

```bash
python ai/test_egress.py
```

One line per candidate — the local tunnel and every entry in `proxies.txt` — each tested the
only way that matters: can it open a tunnel to the game's login host.

```
  socks5://127.0.0.1:40000  FAIL   2053 ms  connect failed: [WinError 10061] refused
  http://3.211.120.181:443  FAIL    238 ms  HTTP/1.1 403 Forbidden — usually an IP allow-list
```

| what it says | what it means |
|---|---|
| `403 Forbidden` | the proxy's IP allow-list still has your **old** address. A new WiFi/ISP means a new public IP. Ask the provider to allow it, or add `http://user:pass@host:port` |
| `407` | the proxy needs credentials and the entry has none |
| `connection refused` on 40000 | nothing is running `wireproxy`; it binds `socks_port` only, so Windows routing and DNS stay untouched |
| `host unreachable` / timeout | this network drops the path — Cloudflare is a known case here (`1.1.1.1:443` times out), which is why WARP cannot work on this line |

Nothing in the emulator can fix a dead exit. Get one candidate to PASS, then launch.

### One command for all of it

```bash
start-network.bat            # tunnel up, pool refreshed, then report
start-network.bat --daemon   # keep the pool running in this window (recommended)
start-network.bat --report   # only report what works, change nothing
```

It starts `bin\warp\wireproxy.exe` if `socks_port` is not listening, runs
`scripts\proxy_pool.js` (extract → deep-check → rewrite `proxies.txt`), then runs
`ai\test_egress.py`. Nothing is installed system-wide; the tunnel is a userspace binary that
binds one local port.

**On WARP, measured 2026-10-23:** this line blocks Cloudflare. `8.8.8.8:53` answers in 19 ms
(UDP is fine), `1.1.1.1:53` and the WARP endpoint `162.159.192.1:2408` answer nothing.
`wireproxy` starts and reports `Interface state was Down, requested Up, now Up`, then the
handshake never completes, so the SOCKS5 port accepts and times out. WARP is bundled and
correct; the endpoint is unreachable. Do not spend time on it here — the pool is the path.



## What changed today, and why each one mattered

| fix | symptom it removes |
|---|---|
| contract declares the real arguments (`npc_id`, `objective_id`, `reply`, `monster`, `resource`) | **46% of objectives (talk/deliver) and 36.5% (kill) were rejected by the validator** — nothing reached the game |
| `id_or_name` coercion for `farm.start.monster` | it was declared a *string* while the planner sent an int: every kill objective failed type-checking |
| `farm.start` takes `resource`, not `item` | deliver / show-item / soul-stone were rejected as an unknown argument |
| `fight.join` declares `monster` + `group_id`, and resolves a monster to its group | planner sent `monster`, contract said `target`, client read `group_id` — three names, no overlap |
| `ready_objectives` is part of the verifier's state | **claiming an objective scored `noop`**, so the agent's best action was invisible and its progress was discarded |
| settle window after walk/talk/fight tools | a walk was judged in the instant it was issued, so every one scored `noop` |
| `agent/observe.py` flattens the snapshot | the planner saw `map_id: None` and never saw its own inventory |
| loopback calls bypass `HTTP_PROXY` | every directive to `127.0.0.1` went through the VPN proxy — 502s from your own machine |
| a missing client stops the loop | it filed three failures, retired the quest, and reported "goals exhausted" |
| client script runs once, one copy | two `FarmBot.init()`, two bindings of `connectionManager`, two bots on one HUD |
| `closing: reason=… requested_by=…` | every close was logged identically, so a click and a kick looked the same |
| AI panel output mirrored to `DofusFarm.log` | the panel's text box is capped at 24k chars and vanishes on close — crashes left no trace |

Night-of fixes that still hold: `--directive-url` no longer defaults to 8893; the snapshot
reads the real fields; the verifier reads the nested shape; `quest.advance` walks to the NPC,
answers dialogs, and uses `data.staticInfos.npcId` + `contextualId`; `fight.join` sends
`GameRolePlayAttackMonsterRequestMessage`; `nav.goto` drives the client's pathfinder;
unlimited really is unlimited; a read-only success no longer ends the run; stall detection
on `objectives_completed`.

## Still not built, and one thing that is untested

- **A completed talk objective.** The gate in front of everything. The agent claims
  objectives the game already accepts, but walk → dialog → reply → objective finished has
  never happened in one session. That is P0.
- **Fights are untested, not working.** `fight.join` has **never been executed against the
  game** — 0 calls in 697 recorded steps, because fights sit behind the first talk. The
  handler is written and reviewed; it has not run.
- **Kill objectives (36.5%).** Nothing targets a specific monster or counts kills toward an
  objective.
- **Cross-map routing.** `nav.goto` changes one map and walks within a map. There is no
  route across several maps; the client's GPS/zaap path is not wired.
- **A JS error in the patched bundle** (`Cannot read properties of undefined (reading
  'setStyles')`, and `_resizeUi` on resize). Harmless to the agent, but it is a real error in
  the client.
