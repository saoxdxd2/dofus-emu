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
client/www/js/mobile-disguise.js        3135  (loaded by the dead www tree)
```

All three had to be patched together by hand for weeks. That is the "old code mixed with
new code" the user kept hitting: patch two, forget the third, and the running page is a
different program than the one on disk.

### 3. `client/www/` is never served

Every resolver prefers `desktop\client\www` and only falls back to `client\www`:

- `desktop/clientbundle.cs:79-84`
- `desktop/WebView2Backend.cs:150-152`
- `desktop/WebView2ClientForm.cs:686-688`

`desktop/client/www` exists, so the fallback never triggers. Worse, the two trees hold
**different game bundles** (`build/script.js` 5,403,643 vs 5,397,352 bytes) — a divergent
copy of the game sitting in the repo, ready to be served by accident.

The Go proxy does not serve either tree (`scripts/dofus-net-proxy.go` has no `www` root),
so nothing else depends on it.

### 4. `kaggle/toolkit/` is a frozen fork of the harness

46 files, `agent/` + `sim/` duplicated, already drifted:

| file | agent/ | toolkit/ |
|---|---|---|
| autopilot.py | 912 | 779 |
| planner.py | 545 | 470 |
| verifier.py | 590 | 562 |

Nothing imports it (no `kaggle.toolkit` reference outside itself) and no script generates
it. It is a snapshot someone took for a notebook and never refreshed.

### 5. Superseded data and binaries

- `dofus-network/data/`: `core_sft_v3.bak` (7.8M), `core_sft_v4.bak` (4.0M),
  `core_sft_v4.v8meas` (3.3M) — superseded variants kept beside the live ones.
- `setup.exe.pre-snapshot.bak` (67M), `DofusFarm.exe.pre-recorder.bak`,
  `DofusFarm.exe.pre-snapshot.bak`, `proxies.txt.bak` — hand-rolled backups that git
  already provides.
- Empty directories: `benchmarks/`, `data/processed/`, `data/raw/`,
  `models/dofus-brain-native/`, `kaggle/runs/thx-02-trm-repair-v2-99k-rows-k-12/`.

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
