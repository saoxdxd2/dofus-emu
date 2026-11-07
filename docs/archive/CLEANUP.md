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

## Round three: the proxy, the player's situation, and fights

### The proxy: your instinct was right, one hop off

The relay itself is fine. Live telemetry from the relay on 8884:

```
rtt 164.8 ms (proxy's own average 97.7), connections 54 total, 0 failed, 0 active
```

54 connections, zero failures. The transport is not dropping anything.

The proxy problem is on the **agent's side of it**. This machine has

```
HTTP_PROXY=http://127.0.0.1:20056
HTTPS_PROXY=http://127.0.0.1:20056
```

set machine-wide (a VPN or local proxy client). `urllib.request.urlopen` honours it, and
every place the harness talks to *this machine* used urlopen:

| caller | what it fetched | result |
|---|---|---|
| `agent/executor.py` | `http://127.0.0.1:8894/directive` | every directive went through the proxy |
| `agent/bridge.py` | the reachability probe | same |
| `ai/probe_client.py`, `ai/verify_live.py`, `ai/idle_trainer.py` | bridge | same |
| `networkhealth/bridge.py` | relay `/__telemetry` | same |

A loopback request routed through an external proxy is a 502 waiting to happen — that is
exactly what a probe of `127.0.0.1:8890` returned before the fix. Every one of those calls
now uses a direct opener (`agent.bridge.opener_for`, `local_opener`), and a check pins it:
a loopback opener has no proxies, a remote one still honours the environment.

### The player's situation: the planner could not see the map

`state.snapshot` returns a nested document. The planner reads flat keys. On a live client:

```
map_id             -> None
level              -> None
quests_done        -> None
inventory_capacity -> None
```

It could not tell **which map it was on**, so every `required_map` comparison failed and
travel decisions were made blind. It could not see its own bag, so a delivery objective
always believed it lacked the item and planned to go and farm it again.

`agent/observe.py` now normalises once, on the way in: `position.map_id` → `map_id`,
`character.level` → `level`, `inventory.items[{gid, qty}]` → an id lookup. The nested keys
are untouched, so nothing that already read them breaks. The guard extracts the keys the
planner actually reads from `planner.py` and fails if `flatten()` does not supply one —
seven keys today, and a new read in the planner is covered without anyone remembering.

### Fights: the agent has never attempted one

Across **697 recorded steps**, the tool histogram is:

```
quest.advance  404
state.snapshot 264
quest.list      25
knowledge.search 3
```

`fight.join`, `farm.start`, `fight.pass`: **zero**. Not "fights fail" — fights are never
reached. The current quest's first objective is a talk, the talk never completes, so the
plan never advances to the kill step where a fight would be chosen.

That also means the fight code added earlier (`GameRolePlayAttackMonsterRequestMessage`
after walking to the group) has never run against the game. It is unverified, and it should
be described that way until a fight actually happens. The blocker is the talk objective,
not the combat handler.

### The network-health model cannot be used as shipped

`networkhealth_artifacts/model.pt` does not load against the current code:

```
Unexpected key(s) in state_dict: h_cycle.blocks.0.norm1.running_mean, ...
```

The checkpoint was trained with `BatchNorm`; `networkhealth/trm_moe.py` now uses
`GroupNorm(1, C)`, which has no running statistics. `SEQ_LEN` also changed from 128 (the
shape of `features.npz`) to 32. Loading it with `strict=False` would leave half the
normalisation unaccounted for and print a confident number from weights the architecture
no longer matches — worse than refusing.

So `ai/diagnose_network.py` uses the model when it loads and falls back to the relay's own
telemetry when it does not, saying which. To make the forecast available again, retrain on
the existing artifacts (`python -m networkhealth train`); that is a separate job, and the
telemetry verdict is already useful without it.

One caveat about the tool: an idle relay reports its **last** RTT, not a live one. Run it
while a client is connected, or the samples are flat by construction.

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

## Round four: the tree itself, 2026-11-03 11:39

The installer's own byte ledger moved to ARCH_REASSESSMENT.md §L. This round is about the working
tree, which had never been measured. Scanning 52,242 files:

| category | files | bytes |
|---|---|---|
| `__pycache__` / `.pyc` | 14,573 | 323.3 MB |
| `.bak`/`.orig`/`.tmp`/`.err`/`.log`/`~` litter | 114 | 22.1 MB |
| `bin/` + `obj/` | 3 | 2.7 MB |
| `.ruff_cache` | 90 | 0.1 MB |
| `.pytest_cache` | 4 | 0.0 MB |

**Deleted (regenerable, by definition): 1,797 `__pycache__` directories, 323.3 MB, plus the three
linter/test caches.** This is not only space. A `.pyc` whose source changed but whose timestamp
comparison still favours it shadows the source, which is the same class of bug as the stale
`Build.exe` that once made a "rebuild" ship the previous builder (§L). One gate run and one build
after the deletion had already regrown 199 directories / 28.9 MB, which is the honest shape of this:
the tree was carrying bytecode from every interpreter and every edit since, not from today.

**Moved, not deleted, to `%LOCALAPPDATA%\Temp\dofus-emu-cleanup-20261103T113937`:** `.tmp/`
(131 files, 44.8 MB of old screenshots and probe output — zero references in any `.py`, `.js`,
`.cs`, `.bat` or `.md`), `proxies_backup_395.txt` (sha256 `f6e0c58a4e52aa43`, **byte-identical** to
`proxies_backup_premanager.txt`, which `scripts/proxy_pool.js` does reference, so the duplicate was
the one with no reader), `network.ini.bak-20261026` and `build_warp3.log` (no references). Anything
that is not regenerated by running the project gets moved, so the decision is reversible.

**Left alone, with the reason:** `bin/` (45.9 MB — the source of the `wgcf.exe`/`wireproxy.exe` the
payload ships, and the operator's credential-bearing configs that §L keeps *out* of the installer);
`DofusFarm.log` (8.25 MB — the host's own log target, written by `desktop/Program.cs`, not litter);
`proxies_raw.txt` (2.36 MB evidence); `_obs_05.json` (cited in `resume.md`, so it is an audit anchor);
`asset_cache/` (§ above: do not delete); `trajectories/` and the Kaggle mirror (live capture and the
training kit).

**Verified after the deletion:** `python -m agent.regression` 1802 checks, 0 failed, and
`tools/payload_audit.py setup.exe --gate` PASS at **104,955,392 B / 512 files — the same byte count
as before**, which is the proof that nothing the installer needs was touched.

**Approved and deleted after that, with the operator's word:** the two FP32 ONNX exports --
`models/encoder30_v15/encoder.onnx` (99,719,562 B) and `models/command_encoder30/command_encoder.onnx`
(98,985,565 B), **189.5 MB**. They had no reader anywhere: `grep -rl encoder.onnx` returns only
`ai/export_encoder.py`, `ai/export_command_onnx.py` and the Colab script, i.e. the things that *write*
them. The graphs that the product loads are the int8 siblings (25,813,919 B and 25,437,833 B), which
`payload_audit.REQUIRED` insists on, and the checkpoints they derive from stay (`model.pt` 99,633,271 B,
`heads_base.pt`, `heads_adapter.pt`). Re-export is one command each:

```
python -m ai.export_encoder --checkpoint models/encoder30_v15/model.pt \
                            --out models/encoder30_v15/encoder.onnx     # int8 sibling derives from --out
python -m ai.export_command_onnx --base models/command_encoder30/heads_base.pt \
                                 --adapter models/command_encoder30/heads_adapter.pt \
                                 --out models/command_encoder30/command_encoder.onnx
```

`models/` went 480.7 -> 291.2 MB. The installer came out at **the same 104,955,392 B / 512 files**
and the gate still reports 1802 checks, 0 failed -- so the deletion is measured, not assumed: a
byte-identical artifact is the evidence that no shipped path ever read those files.

**Still the operator's call, with sizes measured this pass:** `dofus-network/trajectories/` 438.5 MB
(live capture; excluded from the payload, feeds the corpus builders), `dofus-network/data/` 363.8 MB
(`transitions*`, `entity_pointer`, `combat_grid` training shards -- all payload-excluded),
`runtimes/python` 871.2 MB (the app runtime *with* site-packages; only the bare interpreter ships, so
this is what lets the AI run on this box at all), `asset_cache/` 191.0 MB (see the section above: do
not delete), `bin/` 45.9 MB (source of the shipped tunnel tools and the credentials §L keeps out).

## 2026-11-06: documentation reconciliation and reversible cache cleanup

Updated the living `dofus-network/docs/PLAN.md`, `CURRENT.md` and `ARCH_REASSESSMENT.md` with
active-only labels and the same-board allocation controls. Independent raw teacher pair scoring
wins 1,770/2,048 versus joint allocation 1,774 and NN 1,572; this is not a new weight result.
The current summary no longer claims that failed imitation proves coordination is required or that
an all-fight overlay certifies an opening-only ceiling. Historical root status documents remain historical.

Moved **22 ignored `__pycache__` directories, 468 files, 16,004,936 bytes (15.26 MiB)** out of the
project into `C:/Users/sao/AppData/Local/Temp/dofus-emu-cache-cleanup-20261106-4bz_03q_/`.
This reduces workspace clutter, not total disk usage: nothing was permanently deleted.
`manifest.json` there records original relative paths, byte sizes, SHA-256 hashes and completed moves.
Every moved file was an untracked, ignored `.pyc` with an existing sibling source `.py`; symlinks,
junctions and tracked files were excluded. Every quarantined file was verified against its original hash.
To restore, move a recorded directory back only if its original destination does not exist; do not
overwrite newly generated caches. The quarantine is temporary and contains no unique research evidence.

Preserved source, datasets, checkpoints, research evidence, trajectories, logs, credentials, runtime
installations and live asset/proxy caches. The two pre-existing network-status JSON modifications were
not edited or reverted. No Git staging, commits, model promotion, installer rebuild or process stop.
Verification: all four allocation tests pass after cleanup with bytecode writing disabled; control wins
and paired intervals independently recomputed from stored per-board winners; no tracked files deleted.
The full application/regression and installer gates were not rerun for this docs/cache-only change.

### Follow-up: numerical-code cleanup and stale reasoning

Reconciled `ARCH_REASSESSMENT.md` XII.12-XII.16 and the earlier takeover summary in `CURRENT.md`:
failed imitation does not prove coordination is required, a best-single-head comparison is not
super-additivity, and the historical score/history proxies require active-row audits. The two
`wsp_program_value.py` / `wsp_program_target.py` tools now describe their fits as supervised ridge
regression rather than direct numerical WSP. Their algorithms and CLI behavior are unchanged.

`combat/encounter/model.py` now computes the centered layer-normalization input once instead of
twice. No performance gain is claimed. Three permanent regression checks pin token-wise scale/bias,
finite constant-token output and input preservation. Before cleanup, the 12 existing model/checkpoint
checks passed on NumPy and JAX; afterwards all 15 pass on each. Old/new normalization fixtures and
eight-observation full forward passes are bit-identical on both backends, including JAX JIT.
A constant-output normalization mutant is caught on both backends. The 15 encounter/network tests,
10 target-tool tests and three documentation command/fence checks also pass.

No further files were deleted or moved, and tests ran with bytecode generation disabled. The separate
Kaggle mirror currently has 16 differing/missing entries, including an older model implementation;
it was inspected but not overwritten or published. Existing `TASKS.md` and network-status changes
were left untouched. This pass did not run the full application, installer or live-client gates,
and made no commits, checkpoint edits or policy promotions.


## 2026-11-07: the THX-01 lane leaves the tree, and surface labels are learned

The docs killed the THX-01 deployment lane on 2026-10-26, but the working tree still
carried an untracked copy of it: `dofus-network/pipeline/` (8 files) and
`dofus-network/ai/thx_policy.py` (1,158 lines). `HEAD` already held the replacement,
`ai/guard_policy.py`, and a torch-free Sentinel; the stale copies were what the tree
showed. This pass removes them and finishes the de-hardcoding the same docs asked for.

### Removed, with the evidence

- `dofus-network/pipeline/` — `benchmark.py`, `benchmark_thx01.py`, `data_filter.py`,
  `dofus_eval.py`, `kaggle_encoder.py`, `serve_thx01.py`, `train_thx01.py`,
  `__init__.py`. No importer, no test, no entry point; the docs already said "do not
  revive without a new measured reason". Quarantined byte-for-byte at
  `%LOCALAPPDATA%\Temp\dofus-emu-thx-removal-20261107\` with a SHA-256 manifest;
  recoverable from `git show 457e250^:dofus-network/pipeline/<file>` until that
  unreachable parent is garbage-collected.
- `dofus-network/ai/thx_policy.py` — converted in place to `ai/guard_policy.py`, the
  model-free regex/entity guards the release gate already scores. `ThxPolicy`, the
  `thx01` import, the HF default path and the benchmark CLI are gone; `GuardPolicy`
  keeps `choose()` and `_arguments()`. `release_gate.py` and `agent/serve.py` (the
  `--policy thx` branch and `--thx-*` flags) were updated.
- `networkhealth/sentinel.py` — the duplicated `TinyRecursiveReasoner` and the
  `SingletonDecisionAgent` THX/ModernBERT loader are gone, along with the module-scope
  `torch` imports and the `models/dofus-thx01` path that never existed. The heuristics
  that already ran every call are now `MessageClassifier`; the health payload reports
  `classifier: heuristic`. `python -c "import sys; sys.modules['torch']=None; import
  networkhealth.sentinel"` succeeds, which was `KNOWN_GAPS.md` G5's torch half.
- `ai/__init__.py` — the lazy exports for `dofus_native_model` / `train_from_scratch` /
  `DofusDomainTokenizer` pointed at modules that do not exist; removed.
- `requirements-ai.txt`, `tools/shipped_deps.py`, `builder/Build.cs` — `thx01` removed
  from the AI tier and the builder fallback; `start-sentinel.bat` no longer preflights
  torch; `install-python-ai.bat` now says the tier is for training/export, not the
  Sentinel. `tools/shipped_deps.py --check` passes with every root in `CORE_ROOTS`.
- `dofus-network/kaggle/toolkit/` — synced with `python -m tools.sync_kaggle_toolkit
  --write`; the toolkit's `ai/thx_policy.py` was removed too (it was tracked, so
  `git show HEAD:dofus-network/kaggle/toolkit/ai/thx_policy.py` restores it), and
  `agent/selftest.py` now imports `ai.guard_policy`. The toolkit-only sampler was
  still unpacking the old 4-column knowledge tuple; it now reads the 5-column shape
  (`episodes.py`, `synthetic_tools.py`). The guard-helper section passes; the
  remaining five selftest failures are pre-existing toolkit drift (synthetic tool
  coverage, a gazetteer with no local KB, reason extraction, the sentinel-down
  probe), not this removal. Re-upload the dataset before a kernel consumes it.

### De-hardcoded

- **Surface names.** `QuestPlanner._UI_SURFACES` (including the French→key row) became
  `_UI_SURFACES_SEED`; the page publishes `state.snapshot.menu_icons = [{key,label}]`,
  `Autopilot._learn_surface_labels` stores `surface_label/<label>`, and
  `_seed_surface_labels()` adopts it on the next run. `_ui_surface` matches the
  client's labels before the English seed. `ui.icons` now uses the same scanner.
  Seven new checks in `tools/test_menu_window_learning.py` drive the real Autopilot and
  KnowledgeStore; the gate also asserts the French literal is gone and both producer
  and reader exist.
- **Evidence paths.** The machine-specific `C:\Users\sao\...` paths in
  `combat/test_touch_source.py`, `combat/encounter/mechanics.py` and six research
  tools now read `DOFUS_EVIDENCE` (default `~/Documents/dofus-evidence`) and
  `DOFUS_BOARDS`. No absolute user path remains in tracked `.py`/`.js`/`.cs`/`.bat`
  outside the Kaggle mirror and the embedded runtime.
- `possible_positions == []` is reclassified in `docs/LEARNED_FACTS.md` as a search
  budget plus the client's own answer, not an asserted game fact.

### Verified

- `python -m agent.regression` → **2,132 checks, 0 failed** (offline; includes the
  delegated JS, Go, C# and mirror checks). The count matches the persisted
  `data/last_gate_run.json`; when another process runs the gate in the same
  workspace, an in-flight tag check can read that run's record instead — the two
  delegated timing checks that flaked under that contention pass standalone
  (hardening 27/27, brain lifecycle 9/9).
- `python -m ai.release_gate` → guard baseline 63.7 / 41.7 on `val`, 73.2 / 73.2 on
  `gold` (tool / call); the model-free baseline now keeps the arguments it can read
  instead of dropping the whole call when a number needed the model.
- `tools/shipped_deps.py` → PASS; runtime tier `numpy, requests`, AI tier without
  `thx01`.
- `tools/test_menu_window_learning.py` → 15 checks, 0 failed.
- `node --check scripts/mobile-disguise.js` → OK; `py_compile` over every changed
  Python file → OK.
- `kaggle/toolkit` selftest → guard-helper section all PASS; five unrelated
  pre-existing failures remain, listed above.
- `agent.serve --policy` was a one-choice no-op with no callers; the switch and
  the dead `policy = None` were removed.

No commit, publish, checkpoint edit or live-client run was made by this pass. The
`457e250` header commit was created by the environment at 09:08 during the session, not
by this cleanup.


## 2026-11-07 (later): structure pass -- docs and repository maps reconciled; dead training notebooks removed

- `dofus-network/docs/README.md` claimed "Fifteen documents" and listed 17; the directory has 23
  design docs. The map now lists all 23, names `CURRENT.md` + `resume.md` as the status sources
  (not `PLAN.md`), corrects CURRENT's tool count to 28 and CAPABILITY_REGISTRY's stale note, and
  adds reading-order entries for the AI map, the baseline, wiring and learned facts.
- Root `README.md` section 4 now maps the whole brain (agent, ai, advisor, combat, planning,
  navigation, net, sim, knowledge, networkhealth, models, data, benchmarks, colab, kaggle, tools,
  trajectories, docs) instead of four folders, removes the duplicated `docs/` line, marks
  `manual-login.flag` optional, and updates trajectories to the measured 39 files / 256,733 rows
  (2026-11-07); the desktop C# and relay line counts were re-measured at 23,919 and 3,500.
  Section 17 adds the eight unlisted current docs and a table for the root-level
  docs, marking `now.md`, `STATUS.md`, root `PLAN.md` and `COMBAT_STATUS_AND_PLAN.md` historical.
- Removed the two Colab notebooks for the dead training lanes:
  `colab/Fine_Tune_Dofus_THX01.ipynb` (THX-01, killed 2026-10-26) and
  `colab/train_dofus_brain_t4.ipynb` (imports `dofus_native_model.py`, `train_from_scratch.py`
  and `pipeline.py`, all absent). Quarantined with SHA-256 at
  `%LOCALAPPDATA%\Temp\dofus-emu-dead-notebooks-20261107\`; recover with
  `git show HEAD:dofus-network/colab/<name>`.
- Deleted the untracked `dofus-network/.gate_run.log` (64,150 B, 2026-10-30 gate output;
  reproducible).
- Grouped the nine benchmark/eval JSON that sat loose in `dofus-network/docs/` under
  `docs/benchmarks/`; the one live citation (`ARCH_REASSESSMENT.md`, eval_v8_v4val_full)
  was updated. No code or gate path read them in place.
- Removed ten tracked, zero-reference `*_out.txt` scratch outputs from `scripts/` (2026-10-20
  command logs: artifact extraction, proxy fetches, spell/osa dumps). Recover any with
  `git show HEAD:scripts/<name>`.
- Fixed a stale live reference: README's RSS caveat cited `ai/measure_rss.py`, which left the
  tree on 2026-10-25; the sentence now points at the surviving measurement table
  (`docs/SECOND_AGENT.md` section 7) and the `git show af58537^:dofus-network/ai/measure_rss.py`
  recovery.
- The four root status snapshots (`now.md`, `STATUS.md`, root `PLAN.md`,
  `COMBAT_STATUS_AND_PLAN.md`) and `docs/PLAN.md` now carry historical banners in their own
  headers. `CURRENT.md`, `PROJECT_LOG.md`, `FAILURE_TO_SUPERVISION.md` and `TASKS.md` no longer
  name `PLAN.md` as the live status source; `CURRENT.md` + `resume.md` are the pair everywhere.
  The older work-list pointer in `PROJECT_LOG.md` section 6 now names `CURRENT.md` section 6.
- Six docs that the edits left LF-only (`STATUS.md`, root `PLAN.md`,
  `COMBAT_STATUS_AND_PLAN.md`, `docs/CURRENT.md`, `docs/FAILURE_TO_SUPERVISION.md`,
  `docs/PLAN.md`) were normalized to the repository's CRLF checkout convention
  (`core.autocrlf=true`); content is unchanged.

Verified: the full `python -m agent.regression` after the reorganization reports
**2,132 checks, 0 failed** (and writes the green record); `tools/doc_facts --check` passes; the
three documentation gate checks run directly (20 checks, 0 failed); markdown link audit finds no
broken local link; `sync_kaggle_toolkit --check` reports 230 sync / 8 pinned / 0 drift. No
commit, publish or dataset upload by this pass.


## 2026-11-07 (tool pipeline): one owner for the tool facts, gate-checked ties

- `agent/schema.py` now owns the semantic classes as `ToolSpec` fields (`read_only`, `moves`,
  `lists`), written by `save_capabilities` and parsed by `_load_capabilities`; the seed sets are
  applied by name after `_BUILTIN_TOOLS`. `data/capabilities.json` was regenerated so all 28 rows
  carry the flags, and its cp1252 mojibake in the note was repaired in the same pass.
- `agent/verifier.py` no longer keeps its own name lists: `READ_ONLY_TOOLS`, `MOVE_TOOLS` and
  `LISTING_TOOLS` are derived from the loaded schema, and the legacy `farm.step` / `travel.zaap`
  names are gone. `agent/autopilot.py` imports `LISTING_TOOLS` from the verifier instead of
  re-declaring it.
- `agent/features.py` documents the frozen `/17.0` divisor on the last-tool feature (the shipped
  head was trained on that scale and on the 21-label list; changing either without retraining
  shifts every serving input). `ai/train_tool_policy.py`, `agent/features.py` and the shipped
  `models/tool_policy.json` sidecar now agree on the same 21-label order, and the gate proves it.
- `test_tool_vocabulary` grew from 11 to 18 checks: `hdv.prices` is read-only, the registry's
  classes equal the seed's, the verifier derives from the registry, the read-only set is the nine
  non-mutating tools, the movers are exactly `farm.start`/`nav.goto`, every `TOOL_ORDER` name is a
  schema tool, the trainer's label order equals the featuriser's, and the shipped policy's labels
  equal the featuriser's and are all legal tools.
- Follow-on corrections: `ai/build_verifier_sft.py` synthetic drift cases use `farm.start` (was
  the removed `farm.step`); `ai/train_cause_head.py` and `agent/constraint_corpus.py` say
  28/registry-wide instead of 25; `docs/AI_ARCHITECTURE.md` and `docs/EXPERIENCE_LEARNING.md` say
  28 tools.
- Verified: full `python -m agent.regression` **2,140 checks, 0 failed**; `sync_kaggle_toolkit`
  230 sync / 8 pinned / 0 drift; `tools/doc_facts --render` + `--check` green. No commit by this
  pass -- the working-tree commits `63d0efa` / `64e2008` were made concurrently by the external
  WorkBuddy process, not by this cleanup.
