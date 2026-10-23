# Cleanup: ground truth, then removal

Written 2026-10-23, during server maintenance. Every claim below was verified against
the code or the running app's log — none of it is an assumption, because assumptions are
what made this codebase hard to debug.

## The architectural rule this cleanup enforces

> The harness is not an AI. The harness supplies data and tools; the AI makes decisions.

Concretely, and this is what the tree should say at a glance:

| layer | path | responsibility |
|---|---|---|
| transport | `desktop/agentbridge.cs`, `desktop/WebView2*.cs` | HTTP → page → JS, and back |
| game adapter | `scripts/mobile-disguise.js` | the ONLY code that touches the game |
| harness | `dofus-network/agent/` | observe, execute, verify, loop |
| knowledge | `dofus-network/planning/`, `data/kb` | static world data |
| AI | `dofus-network/ai/` | policies and training |
| offline harness | `dofus-network/sim/` | the same loop, no game |

Anything that is none of those is debris.

## Findings, with evidence

### 1. The client script executes TWICE on every page load

`DofusFarm.log`, every load, same pattern:

```
[AgentBridge] directive bus ready
[FarmBot] Bound to connection manager.
[x2] [FarmBot] Bound to connection manager.     <- the second execution
```

Two transports are both active for the WebView2 client:

- `desktop/WebView2ClientForm.cs:803` → `AddScriptToExecuteOnDocumentCreatedAsync(_options.DisguiseScript)`,
  where `DisguiseScript` is read from `scripts/mobile-disguise.js` (`WebView2Backend.cs:113-140`).
- `desktop/client/www/standalone.html:46` → `<script src="js/mobile-disguise.js?v=...">`.

So `FarmBot.init()` runs twice, which starts two `waitForConnection` timers, which bind
the connection manager twice. Two bots, two tick loops, one HUD. This is a bug, not a
style issue, and it is invisible unless you count the log lines.

### 2. Three identical copies of a 3135-line script

```
scripts/mobile-disguise.js              3135  (the one the host injects)
desktop/client/www/js/mobile-disguise.js 3135  (the one standalone.html loads)
client/www/js/mobile-disguise.js        3135  (loaded by the second www tree)
```

They were identical because `builder/build.cs:262-267` (`TrySyncClientShims`) copied
`scripts/mobile-disguise.js` over both www copies on every build — not because anyone
kept them in sync by hand. So the drift risk was the opposite of what it looked like:
edit the copy under `desktop/`, run the builder, and your change is silently reverted.
Three files that look like three sources of truth, where only one survives a build.

### 3. `client/www/` is not served, but it is not dead either

The runtime resolves `desktop\client\www` first and falls back to `client\www`
(`desktop/clientbundle.cs:79-84`, `desktop/WebView2Backend.cs:150-152`,
`desktop/WebView2ClientForm.cs:686-688`), so `client/www` is never the served tree.

But it is the **master** for one file: `TrySyncClientShims` copies
`client/www/js/autoupdate.js` → `desktop/client/www/js/autoupdate.js`, and copies the two
shims the other way. It is a half-master in a bidirectional sync, which is its own smell
— but it means deleting the tree would break the autoupdate master. It stays.

What does deserve attention: the two trees hold **different game bundles**
(`build/script.js` 5,403,643 vs 5,397,352 bytes). A divergent copy of the game sits in the
repo, one fallback away from being served. Recorded, not touched.

### 4. `kaggle/toolkit/` is a published dataset, not a stale fork

It looks exactly like a dead duplicate of `agent/` + `sim/` (28 files, already drifted:
`autopilot.py` 912 vs 779, `planner.py` 545 vs 470). Nothing imports it.

It is not dead. Its own `dataset-metadata.json` declares the Kaggle dataset
`tajdinenizar/thx-02-sim-toolkit` (8.2 MB), and `kaggle/tool/kernel-metadata.json:11`
lists that dataset under `dataset_sources`. `kaggle/tool/run.py` mounts it and runs
`sim/tool_choice.py` and `ai/train_tool_policy.py` from inside it. Deleting it would
remove the staging copy for a dataset a live kernel depends on.

It has no generator in the repo, which is the real problem: it is a hand-assembled
upload payload that will keep drifting from the harness it was copied from. Left in
place, flagged in `dofus-network/archive/README.md`.

### 5. Superseded data and binaries — and one correction

- `data/core_sft_v3.bak/` (7.8 MB) — md5-identical to `core_sft_v3/` on all three files.
  Removed.
- `data/core_sft_v4.bak/` (4.0 MB) — **not** a duplicate: 3,290 val rows against 3,073 in
  the live copy, and `docs/ARCH_REASSESSMENT.md:34` names it the only surviving copy.
  Kept. It is also untracked, so deleting it would be unrecoverable.
- `data/core_sft_v4.v8meas/` (3.3 MB) — a measurement run cited at
  `docs/ARCH_REASSESSMENT.md:66`. Kept.
- Root backups: `setup.exe.pre-snapshot.bak` (67 MB), `DofusFarm.exe.pre-recorder.bak`,
  `DofusFarm.exe.pre-snapshot.bak`, `proxies.txt.bak`. Removed — but note they were
  **gitignored** (`.gitignore:86: *.bak`), so git never held them. The premise that
  everything here is recoverable was wrong for these four files.
- Empty directories: only `kaggle/runs/thx-02-trm-repair-v2-99k-rows-k-12/` was genuinely
  dead (its emptiness was short-circuiting the `exists()` fallback in
  `kaggle_report.py:132`). `benchmarks/`, `data/processed/`, `data/raw/` and
  `models/dofus-brain-native/` are output paths that scripts create with `mkdir`, so they
  stay.

## Rules for this cleanup

1. **Nothing is deleted without a grep that proves it is unreferenced.** The evidence goes
   in the commit message.
2. **One source of truth per artefact.** If two files must exist for two transports, one
   is generated from the other and a test fails when they diverge.
3. **No behaviour change hidden inside a cleanup.** Bug fixes found along the way are
   listed separately, with the symptom they remove.
4. **Tests stay green.** `dofus-network/agent/smoke_verifier.py` is the gate (103 checks).
5. **Recoverable.** Everything removed is tracked by git, so `git show <sha>:<path>`
   restores it. Where a file had research value, its purpose is recorded in
   `dofus-network/archive/README.md` before deletion.

## What was done

### The client script: one source, one execution

- `scripts/mobile-disguise.js` is now the only copy, with a header saying so and an
  idempotence guard (`window.__DofusFarmAgentLoaded`) so a re-added transport is a no-op
  instead of a second bot.
- The `<script src="js/mobile-disguise.js">` tags are gone from both `standalone.html`
  files, replaced by a comment explaining why they must not come back.
- `builder/build.cs` no longer copies the script into the two www trees.
- Deleted `desktop/client/www/js/mobile-disguise.js` and `client/www/js/mobile-disguise.js`.
- `tools/js-error-finder.js` now reads the script from `scripts/` instead of a path that
  only existed after a build.

Two silent bugs fell out of the same file, both of the "reads a field that does not
exist" class that made this project undebuggable:

- `isUserActor` read `playerData.character.id` and therefore returned `true` for every
  actor — every turn start looked like the player's own, and the turn handler was handed
  a monster id.
- `executeOptimalTurn` read cell, AP, MP, HP and breed from the same missing object, so
  every turn was played as a Cra with cell 250, 6 AP, 3 MP and 400 HP. It now prefers the
  player's own fighter stats (the same source the monster stats already came from) and
  falls back to the real `playerData` fields.

### The harness

- `agent/features.py` now owns the objective-type buckets, `_params()` and the error
  slots; `planner.py`, `sim/world.py` and `sim/decisions.py` import them instead of each
  holding a copy. `agent/` still does not import `sim/`.
- Deleted `tools/diag_serving_drop.py` and `tools/int8_parity.py`: both hardcode a model
  directory (`encoder30_v11`) that does not exist, so neither could run.
- Removed 5 one-off scripts from `ai/` (`ab_scores`, `cost_probe`, `thx_test`,
  `thx_test2`, `thx_test3`) and 60 one-off probe scripts from `scripts/` — the Osamodas
  spell excavation, whose output (`scripts/output/osa_spells.json`) is kept.
- `data/core_sft_v3.bak/` removed (identical duplicate). `kaggle/toolkit/` left in place
  — it is a published dataset payload, not a stale fork.

### Guards added to `agent/smoke_verifier.py`

Six new checks, because every fix above is the kind that silently comes back:

| check | catches |
|---|---|
| the client script guards against a second execution | guard removed |
| exactly one copy of the client script exists | a copy reappears |
| no page loads the client script with a `<script>` tag | transport re-added |
| the builder does not copy the script into www/js | build recreates the copies |
| no harness module imports the simulator | inverted dependency |
| objective buckets and `_params` have one owner | duplicated vocabulary drifts |

**110 checks, 0 failed. Simulator unchanged: 119 quests, 148 objectives.**

## "Running the AI gets me kicked from the server"

Reported after a run: logged in, started the AI, and the session ended. What the logs
actually show, and what was done about it.

### The client never logged a disconnect

The game's own disconnect path is:
```js
t.disconnect = function (e) { ... console.info("connectionManager.disconnect: reason=" + e) ... }
```
`console.info` is captured by the host (620 `[WEB-INFO]` lines in the log). **Zero**
`disconnect: reason=` lines, zero `[Gui] disconnected`, zero socket-lost messages. So at
the socket level the client did not observe a drop — which means "kicked" was, at least
in the logged cases, the window closing (`WebView2 client closed by user`, 62 times, which
also fires on app exit and on `Dispose`).

That is not proof there was no kick. It is proof that **the reason was never recorded**, so
every explanation was a guess. Fixed first, so the next occurrence is data:

- `scripts/mobile-disguise.js` wraps `dofus.disconnect` / `disconnectAndReload` and records
  the reason in `window.__agentLastDisconnect`, posts an `AI_DISCONNECT` message, and
  exposes it in `state.snapshot.last_disconnect` and `debug.last_disconnect`.
- `WebView2ClientForm` logs it as `[NET] game connection ended: reason=… (via …)`.
- The autopilot now stops when the client leaves the world mid-run, instead of sending
  directives into a dead session, and reports that as the reason the run ended rather than
  "goals exhausted".

### One provable way the app itself kills a live session

`ProxySupervisor.OnActiveChanged` → `RebuildAllRelays` → for every relay port:
`StopProxyInternal(port)` then start a new proxy on the same port. That drops **every live
socket**, including the game connection, and the reconnect leaves from a different exit IP
— which is exactly what a server treats as a hijacked session.

The supervisor fires whenever a health probe changes its mind (29 switches in the log). The
fix defers the switch while any client is connected:

```
[PROXY] egress would move to <label> but 1 client(s) are connected;
        keeping the current egress until the next launch
```

Deferring costs nothing: the supervisor has already recorded the pick, and the next launch
resolves its egress from the supervisor, so the switch lands on a fresh connection.

### Two logs that sent the investigation the wrong way

- `WebView2ClientForm` announced `Registered visitor credentials injection for [GUEST]…`
  even when manual-login had just *cleared* the guest identity and injected nothing. It now
  says `manual-login: no credentials injected, stored guest identity cleared`.
- `WebView2 client closed by user.` fires on any close, including the app's own
  `Dispose` during shutdown. Not renamed (too many call sites), but the disconnect reason
  above is now the line that distinguishes the two.

### Still unexplained, and worth knowing

Every launch starts the proxy **twice** — 67 launches, 67 duplicates, one `bind: Only one
usage of each socket address` per launch in `asset_cache/proxy.log`. The second start is
`RebuildAllRelays` rebuilding a relay that `EnsureProxyRunning` had just created, because
the supervisor fires again after the first start records the spec. It happens before the
client connects, so it is waste rather than a kick — but it means a relay can be rebuilt
moments after it came up, and that is the same code path that kills a live session.

## Recheck, 07:30 — the AI reaches the game now, and still does nothing

A live session after the contract fix. Progress, and two new defects.

### It is talking to the game

```
[AI] in-game badge requested: start_auto (dofus-05)
[AGENT] state.snapshot -> dofus-05 ok
[AGENT] quest.advance  -> dofus-05 ok        (x ~40)
```

No `refused: schema rejected` anywhere. The contract fix worked: directives reach the
client.

### But every action is scored `noop`

The trajectory says `quest.advance ok, verdict: noop` — over and over, alternating with
`state.snapshot`. The controller retries, recovers, retries, and the loop is killed.

The cause is timing. `quest.advance` walks to the NPC and talks **in the walk callback**, so
it answers `ok` before the character has moved. The loop observed in the same instant, saw
an unchanged cell, and scored a walk in progress as a no-op.

Fixed with a settle window: after a tool in `SETTLE_TOOLS` (`quest.advance`, `nav.goto`,
`fight.join`, `farm.start`) returns ok, the loop re-observes every 400 ms for up to 3 s and
judges the first observation that actually differs. A call that changes nothing still ends
as `noop` — after the wait rather than before it. Two checks pin both halves.

### Why the runs ended `exit -1` with no explanation

`AIPanel.Append()` writes to the panel's text box and **nowhere else**. That box is capped
at 24,000 characters and is gone when the panel closes. The Python child's stdout — and any
traceback on its stderr, which arrives there prefixed `! ` — went into that control only.

So two runs ended `[AI] loop finished (exit -1)` with no output to explain them, not because
the child said nothing but because what it said was never written down. `Append` now mirrors
to the host log as `[ai] …`. Cheapest fix of the session: a crash that leaves no trace is
worse than the crash.

### Machine state before the next run

- `DofusFarm.exe` is not running, but `dofus-net-proxy.exe` (PID 5388) still holds
  127.0.0.1:8884 — an orphan relay that outlived its parent.
- `bridge_port.txt` still says `8894`, where nothing listens.
- 127.0.0.1:8893 is held by PID 16608, unidentified (wmic is blocked in this sandbox).
  Third time this port has been held by something unexplained.
- `Renderer check: no hardware GPU adapter usable - defaulting to SwiftShader (software)
  rendering.` The client is rendering the game on the CPU. Worth attention on its own:
  software rendering plus a Python loop competing for the same cores is how a client stalls
  long enough to miss keep-alives — a disconnect that never reaches the game's own
  disconnect handler.

## Still open, deliberately

- `kaggle/toolkit/` drifts from `agent/` with no generator. It needs an export script, or
  it will be a fourth copy of the harness by next month.
- `sim/rollout.py` imports feature widths from `ai/train_planner_student.py` (26 wide)
  while everything else uses `agent/features.py` (40 wide). Fixing it changes corpus
  width, so it is a separate change.
- `client/www` and `desktop/client/www` hold different game bundles. Harmless today
  because the desktop tree always wins; a hazard the first time it does not.
- `client/www/js/autoupdate.js` is the master and `desktop/client/www/js/` is the copy,
  while the two shims go the other way. One bidirectional sync with no rule about which
  side wins.
- `Build.exe` is a compiled binary from 2026-10-22 and still contains the old
  `TrySyncClientShims`, so it recreates the two www copies on every build. It did exactly
  that at 06:47 while this cleanup was running, from a `scripts/` file edited minutes
  earlier — the drift came back within the hour. The source is fixed; the binary needs
  `compile.bat` to catch up. Until then, the "exactly one copy" check will fail after each
  build, which is the check doing its job.

## `asset_cache/`: do not delete it

181 MB, 9,662 files. It is the proxy's **live HTTP cache**, not build debris:

| consumer | line |
|---|---|
| `desktop/farmengine.cs` | `-cache "asset_cache"` passed to the proxy |
| `desktop/mainform.cs` | `opts.CacheDir = ...\asset_cache` |
| `desktop/WebView2ClientForm.cs` | the WebView2 profile cache root |
| `tools/map-export.js` | reads map data from it |
| `scripts/fetch_maps.py` | `ASSET_ROOT = asset_cache/files/...` |

Deleting it forces the client to re-fetch every asset from Ankama's CDN on the next
launch. That is slower, and for this project more CDN traffic is an account-safety
question rather than a performance one.

It is also not stale: `assetCacheTTL` is 7 days (`scripts/dofus-net-proxy.go:1477`) and
entries past it are removed lazily when requested, so the Oct-18 files are still inside
the window. What it does *not* have is a size cap, so entries that are never requested
again stay forever. If it ever needs shrinking, the safe move is to prune by age (older
than the TTL), never to empty it.

Four log files live inside it and are safe to clear when the app is closed:
`requests.log` (1.5 MB), `prefetch.log` (36 KB), `proxy.log` (24 KB), `cfgtest.log`.

## The second bug class: the contract rejected the plan

Found while answering "the AI still isn't working, should we train it more?" — from the
trajectory log of the user's own run:

```
refused: schema rejected the call (unknown argument(s) for quest.advance: objective_id)
```

The planner and the client agreed with each other. The **contract between them did not**,
and it ran first, so nothing reached the game. Four tools were affected:

| tool | planner sends | contract declared | client reads | result |
|---|---|---|---|---|
| `quest.advance` | `quest_id, npc_id, objective_id` | `quest_id, step_id` | `quest_id, npc_id, objective_id, reply` | every talk and deliver objective rejected (46% of the KB) |
| `farm.start` | `monster: 182` (int) | `monster` as **str** | `parseInt(args.monster)` | every kill objective rejected (36.5%) |
| `farm.start` | `item: 7405` | — | `args.resource` | deliver / show-item / soul-stone rejected |
| `fight.join` | `monster: 447` | `target` | `group_id` | three names, no overlap |

`step_id` was the mirror image: declared, and read by nobody.

Why it survived so long: the existing guard checked that every declared *tool* had a
client handler. It never checked the *arguments*, so the names matched and the parameters
did not. The new check drives every objective type in the knowledge base through
`plan_objective()` and then through `validate_call()` — 22 calls, 0 rejected — and a
second check compares the arguments the client reads against the ones the contract
declares.

This is worth stating plainly because of the diagnosis it invites: a system that refuses
its own plans looks exactly like a model that cannot play. Training more would have
produced a model that had learned to avoid the correct actions.

