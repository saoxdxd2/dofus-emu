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
