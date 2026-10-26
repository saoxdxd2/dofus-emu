# done.md — golf campaign ledger

Updated 2026-10-26 16:40 local. Checkpoint commit: `b800049` (`golf: checkpoint wave 1 artifacts`).

This is the live ledger for the "everything, byte by byte" pass. The rule is simple: a file
counts as done only when a golfed copy exists under `golf/`, its behavior contract is intact,
and it has at least a syntax/compile gate. Originals are never edited.

> **Rollback note.** At ~16:34 the workspace rolled back and deleted the untracked `golf/`
> outputs (and the prior commit `5dd0c1f` removed three tracked ones). Wave 1 was recovered
> from agent scratch files under `%TEMP%\opencode\` (`bs_min.py`, `executor_golf_final.py`,
> `golffinal\dofus-net-proxy.go`, `golf_backup.py`) and committed as `b800049` so this cannot
> happen silently again. A separate automation is also editing the tree and committing
> (`5dd0c1f` added 39 lines to `agent/planner.py`); the planner pass is re-running against
> that current source, not the stale pre-rollback bytes.

## Status

Byte counts are LF-normalized blob bytes (CRLF excluded) so they match `git` and the agent
reports. `Δ` is against the working-tree source at pass time.

| state | file | source → golf | Δ | lines | gate |
|---|---|---:|---:|---:|---|
| done | `dofus-network/advisor/spells.py` | 37,738 → 31,495 | **-16.5%** | 1007 → 858 | AST + prior fuzz; replacement pass rejected |
| done | `dofus-network/ai/encoder_policy.py` | 50,268 → 40,784 | **-18.9%** | 1179 → 980 | AST; replacement pass rejected |
| done | `dofus-network/combat/battle_solver.py` | 68,673 → 47,113 | **-31.4%** | 1590 → 1039 | AST + 3 fuzz suites, 0 fails |
| done | `dofus-network/networkhealth/sentinel.py` | 35,528 → 23,183 | **-34.7%** | 822 → 316 | AST + 9,000 differential cases |
| done | `dofus-network/agent/executor.py` | 66,572 → 43,464 | **-34.7%** | 1524 → 1065 | AST; recovered artifact, fuzz re-run queued |
| done | `scripts/dofus-net-proxy.go` | 109,295 → 80,998 | **-25.9%** | 3410 → 3235 | `gofmt` + `go test ./...` green (0.09 s) + ~5.8M diff cases |
| in flight | `dofus-network/agent/planner.py` | 84,190 → ~33,000 | ~-61% | 1707 → ~763 | re-running on current source incl. dialog exhaustion |
| in flight | `dofus-network/combat/fight_estimate.py` | 62,428 → ~40,971 | ~-34% | 1531 → ~737 | re-running after rollback |

**Committed total (6 files):** 368,074 → 267,037 bytes, **-101,037 (-27.4%)**; 9,532 → 7,493
lines, **-2,039 (-21.4%)**. Including the two in-flight expectations: **-173,669 bytes
(-33.7%)** over 8 files.

## Rejected passes (reverted, not retained)

The rollback deleted the staging copies, so these are recorded by diff rather than kept as
files:

- **`encoder_policy` agent rewrite** — 40,784 → 40,864 bytes (+80) while calling `tok_emb`
  twice and computing the `pooled @ field_weights.t()` projection twice instead of reusing
  `emb`/`projection`. Cleaner lines, more FLOPs. Reverted to the prior artifact. This is why
  `BRIEF.md` now has a performance tie-break clause.
- **`spells` agent rewrite** — 31,495 → 31,481 bytes (-14) by deleting the hot-path alias
  `_CALC = DofusDamageCalculator.calculate_spell_damage`, trading a per-spell attribute walk
  for 64 bytes. Not worth it; reverted.

## Verification commands actually run

```powershell
# Python syntax (all six committed artifacts)
Get-ChildItem golf -Recurse -File -Include *.py | ForEach-Object {
  python -c "import ast,sys;ast.parse(open(sys.argv[1],encoding='utf-8').read())" $_.FullName
}
# Go: copy golfed relay + module + equiv tests into a scratch dir
go test ./...        # ok github.com/dofus-emu/dofus-net-proxy 0.092s
```

Agent-side evidence for the recovered files: battle_solver `self_check` + helper/board/edge
fuzz (0 fails); sentinel 9,000 differential cases + 78 endpoint diffs; relay ~5.8M scratch
differential cases plus the repo's 60k official fuzz, `go vet` clean.

## Scope and exclusions (deliberate, not skipped)

| excluded | reason |
|---|---|
| `client/`, `desktop/client/` | vendored Cordova/WebView2 runtime; generated `www/build/script.js` is a 5.4 MB minified game bundle |
| `client|desktop/client/www/plugins/**` | third-party plugins (`cordova-plugin-purchase/store.js` is 500 KB, ChromeVox 843 KB) |
| `dofus-network/kaggle/toolkit/**` | packaging mirror of `agent/`, `sim/`, `combat/`; canonical copy is `dofus-network/` |
| `runtimes/`, `bin/`, `obj/`, caches, `asset_cache/`, `proxy_cache/`, `trajectories/` | binaries, generated data, caches |
| `dofus-network/models/**`, `*.onnx`, `*.pth` | model artifacts, not source |

Remaining first-party queue is roughly 220 files: ~190 Python under `dofus-network/`
(including `agent/autopilot.py`, `agent/regression.py`, `sim/world.py`, `agent/schema.py`,
`ai/tool_policy.py`, `planning/planner.py`), 6 Go files under `scripts/`, 26 C# files under
`desktop/` + `builder/build.cs`, and first-party JS (`scripts/mobile-disguise.js`,
`proxy_pool.js`, `harvest_proxies.js`, ...). `scripts/mobile-disguise.js` is the game adapter
and therefore highest risk; it goes last, behind a JS syntax + replay gate.

## Next wave

Wave 2 launched at 16:45 (background, one agent per file): `agent/autopilot.py`,
`agent/regression.py`, `sim/world.py`, `agent/schema.py`. Their outputs land under `golf/`
and get committed only after the AST/fuzz gate passes.

1. Finish `planner.py` and `fight_estimate.py` re-runs; append their rows and commit.
2. Python brain: `ai/tool_policy.py`, `planning/planner.py`, then the remaining `agent/`,
   `planning/`, `ai/`, `combat/` modules.
3. Go remainder: `proxy_pool`/`fetch-proxies`/`android-micro-auth`/`benchmark_proxy`
   (equivalence tests where they exist).
4. C# desktop (largest: `desktop/farmengine.cs`, `WebView2ClientForm.cs`, `mainform.cs`,
   `installer.cs`, `aipanel.cs`, `pages.cs`).
5. JS adapter last.
