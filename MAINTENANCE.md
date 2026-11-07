# MAINTENANCE — repository hygiene record

Dated record of structural work on this repository: what was removed, what was rewritten, and
how to undo it. Append a new section per pass; do not rewrite past entries.

---

## 2026-11-07 — history rewrite, size reclaim, docs consolidation

### Why

The repository had three separate problems, all visible from `git status` and `du -sh .git`:

1. **`.git` was 910 MB** for a tree of 3,964 files. A single 760 MB blob
   (`sdk.7z.old-payload`) was held alive only by 54 editor checkpoint refs under
   `refs/cline/checkpoints/*` — snapshots taken by Cline, never meant to be pushed, never
   referenced by any commit on `main`.
2. **109 of 295 commits had the message `.`** (or `@`-prefixed noise). 36% of the history
   was unreadable: no scope, no verb, no way to find when a change landed.
3. **The repository root held five status documents that each declared themselves
   historical** — 645 KB of competing narrative in the directory a reader looks at first.

### What was done

**1. Backup first.** A complete bundle including every ref was written before any change:

```
git bundle create ../dofus-emu-backup-2026-11-07.bundle --all   # 899 MB
git bundle verify ../dofus-emu-backup-2026-11-07.bundle         # "records a complete history"
```

A branch `backup/pre-clean-2026-11-07` was also left at the pre-rewrite tip (`84b0035`). It was
deleted later the same day — see step 8.

**2. Orphaned refs dropped.** 54 `refs/cline/checkpoints/*` refs deleted, plus a stale
`refs/original/refs/heads/main` (a 2026-10-22 `filter-branch` leftover, 48 commits not in
`main`) and the merged branch `cline/2f02b`. Then `git reflog expire --expire=now --all` and
`git gc --prune=now`.

> **Cost, stated plainly:** Cline can no longer restore its checkpoint snapshots for this
> repo. They were never in `main` and were never pushed. The bundle that held them has since been
> deleted, so those snapshots are gone — nothing depended on them.

**3. History rewritten — messages and squash only, never content.** A tree-preserving
rebuilder walked the graph in topological order, dropped each run of junk commits, and
re-parented their children onto the last meaningful commit. Because every commit carries a
complete tree, no diff was replayed, so content could not drift.

| | before | after |
|---|---|---|
| commits on `main` | 295 | **186** |
| junk subjects (`.` / `@…`) | 111 | **0** |
| `.git` on disk | 910 MB | **176 MB** |
| files in tree | 3,964 | **3,964** |

Verification — both are required before trusting a rewrite:

```
git rev-parse main^{tree}        # d15f6fe…  (identical before and after)
git diff --stat <old-main> main  # empty
git ls-tree -r --name-only main | wc -l   # 3964 == 3964
```

Author and committer identity, dates and messages of every surviving commit were preserved
verbatim; only the dropped commits lost theirs. `@`-prefixed subjects had the decoration
stripped rather than being dropped.

**4. Docs consolidated.** Five self-declared-historical documents moved from the repository
root to `docs/archive/`, with an index at `docs/archive/README.md`:

```
STATUS.md  PLAN.md  now.md  CLEANUP.md  COMBAT_STATUS_AND_PLAN.md   →  docs/archive/
```

Thirteen cross-references in `README.md`, `resume.md`, `TASKS.md`,
`dofus-network/docs/README.md` and `dofus-network/docs/ARCH_REASSESSMENT.md` were updated to
the new paths. Nothing was deleted; content is byte-identical.

Root now holds only: `README.md`, `resume.md`, `TASKS.md`, `rules.md`, `MAINTENANCE.md`.

**5. Second history pass, same day.** Six more commits squashed and five reworded — see
`docs/COMMIT_HISTORY.md` Part 3. 190 → 184 commits, tree identical.

The five reworded ones matter more than the six squashed ones: `documentation update` was a
423-file, −835,963-line restructure, and `Update farmengine.cs` was 233 files and +133,806 lines.
Squashing those would have attributed the work to a neighbour and produced a diff that lied.
A commit's *message* being useless is not evidence its *content* was.

**6. Two more documents archived.** `dofus-network/docs/PLAN.md` and `AGENT2_DESIGN.md` — both
self-declared superseded — moved to `docs/archive/dofus-network/`, with index rows updated.

**7. `KNOWN_GAPS.md` identifier collisions recorded, not fixed.** `G98`, `G99` and `G100` are
each used twice. Renumbering would silently repoint roughly ten existing citations, so a note at
the top of the file states the collision and how to disambiguate by date.

**8. Local refs purged so the old history is invisible everywhere.** GitHub Desktop shows every
local branch, tag and stash — a clean remote is not enough. The backup branch, the `v2.3` tag and
the stash were all still holding pre-rewrite commits, and the branch was visible in the branch
list. After this step the only refs are `main`, `origin/main` and `origin/HEAD`, and
`git rev-list --all --not main` returns **0**. `.git` went 180 MB → **164 MB**.

### Open items from this pass

- **Published 2026-11-07.** `git push --force-with-lease origin main` succeeded
  (`4cb9c53…5dc642e`, forced update). `origin/main` now holds 187 commits and is in sync with
  local. Any other clone of this repository must be re-cloned or reset — its old objects are
  gone from the remote. GitHub warned once that
  `sdk-stripped-backup/emulator/qemu/windows-x86_64/qemu-system-x86_64-headless.exe` is
  54.6 MB, above the 50 MB advisory; it is not an error and no LFS migration was done.
- **Tag `v2.3` deleted 2026-11-07 13:25.** It pointed at `18d76be` (2026-10-17, tree
  `cffb2c7`), a commit squashed away in the first pass. A tag outside `main` dragged **60 old
  commits** into every history view — it was the last visible remnant of the old history. The
  exact tree it marked no longer exists as a commit, so retagging would have moved a release
  marker onto different content. Recorded here instead. That commit is now unrecoverable — the
  bundle holding it was deleted. If a `v2.3` marker is ever wanted again, tag a current commit
  and say so in the tag message.
- **`stash@{0}` exported and dropped.** "local changes before merge" (2026-10-21, 98 insertions
  across `bench_encoder.py`, `encoder_policy.py`, `kaggle_train_encoder30.py`, `kb.py`) was
  based on the pre-rewrite history and kept **117 old commits** alive. The diff was written to
  `../dofus-emu-stash-2026-10-21.patch` (10,386 bytes, 4 files) before dropping it:

```
git apply ../dofus-emu-stash-2026-10-21.patch    # if that work is ever wanted back
```

- **Runtime artifacts are tracked and churn every cycle**: `data/net_status.json`,
  `data/last_gate_run.json`, `data/egress_attestation.json`, `data/capabilities.json` are
  rewritten by the running services. `.gitignore` already excludes the proxy-pool state for
  exactly this reason. These four are left tracked because the gates read them; untracking
  them is a separate decision.

### How to undo — **this is no longer possible**

The rewrite was reversible until 2026-11-07 13:29. It is not any more.

In order, and all on the same day: the local backup branch was deleted (it was visible in GitHub
Desktop and showed the old 295-commit history), then the `v2.3` tag and the stash, then **both
backup bundles** — `dofus-emu-backup-2026-11-07.bundle` (899 MB) and
`dofus-emu-backup-2026-11-07b.bundle` (168 MB). The bundles did not go to the Recycle Bin, so
there is no copy left to restore from.

**What that means:** the pre-rewrite history exists in no location — not on GitHub (force-pushed
away), not in this repository, not on disk. `git show` on any sha from
`docs/COMMIT_HISTORY.md` Parts 1 and 3 fails.

**What is still true:** no file content was lost. Each squashed commit's tree was folded into the
commit that replaced it, and the tree is byte-identical to the pre-rewrite tree. The loss is the
per-commit boundary and the ability to diff against it — recorded in `docs/COMMIT_HISTORY.md`.

If a similar rewrite is ever done again, keep the bundle **outside** the working tree and do not
delete it until the result has been lived with for a while.

### 9. History collapsed to a single commit, 2026-11-07 13:36

The 188-commit history was replaced by one root commit holding the same tree:

```
git commit-tree main^{tree}   # → b84ed45, no parents
```

| | before | after |
|---|---|---|
| commits | 188 | **1** |
| `.git` | 164 MB | **68 MB** |
| tree | `ebcd5ed` | `ebcd5ed` (identical) |
| files | 3,967 | 3,967 |

`git diff` between the old head and the new root commit is empty — no content changed. What was
lost is every commit boundary and every per-commit diff; `docs/COMMIT_HISTORY.md` is the record
of what they contained.

The only remaining undo is `../dofus-emu-pre-collapse-2026-11-07.bundle` (156 MB, verified,
holds the 188-commit history). Restore with:

```
git fetch ../dofus-emu-pre-collapse-2026-11-07.bundle 'refs/heads/main:refs/heads/pre-collapse'
git push --force-with-lease origin pre-collapse:main
```

**Delete that bundle only when you are certain.** Once it is gone the history is unrecoverable —
GitHub keeps unreachable objects for a while, but it is not a backup you control.


---

## 2026-11-07 (tool pipeline, pass 2) — every AI label set is tied to its consumer

- `ai/train_entity_pointer.py` no longer carries a hand-kept 25-name candidate tuple; it derives
  `ACTION_TOOLS` from `agent.schema.tool_names()`. The dataset builder already gates coverage over
  all 28 tools, so the three added later (`ui.tap`, `ui.icons`, `shop.buy`) had no candidate and
  their rows were dropped as unlabelable.
- The schema seed and `data/capabilities.json` disagreed on three parameter shapes; resolved toward
  what the client actually reads: `farm.start.restart` added to the registry (`args.restart` is
  read by the bundle handler), `inv.equip.remove` added to the seed (`args.remove` is read), and
  `inv.sell.quantity` documented and defaulted to 1 (the client's `hdv.list` path treats a missing
  quantity as one unit).
- `test_tool_vocabulary` grew to 26 checks: registry parameter shapes equal the seed's; the command
  model's intents equal `agent.commands.INTENTS`; its actions are bridged actions; its tags are
  declared slots; the opt-in 30M encoder's sidecar labels are legal tools; and the shipped tool
  policy's 42-number width equals `features.FEATURE_COUNT`. Each was mutation-tested (remove
  `restart`, rename a tag, insert `bogus.tool`, drop `auto`, rename `safe.stop`, set width 41) and
  the section failed in every case.
- The client is tied too: every schema tool with `backend="directive"` must name a
  `'<tool>': function` handler in `scripts/mobile-disguise.js` (all 22 do; a renamed handler fails
  the gate).
- `models/cause_head.json` is marked `retired` with the arithmetic: features 2118 = 2048 hash +
  25 tools + 8 flags + 5 causes + 32 markers, while the live 28-tool builder is 2121. The gate
  fails if a stale cause head is not labelled retired.
- Verified: full `python -m agent.regression` **2,149 checks, 0 failed**; `sync_kaggle_toolkit`
  230 sync / 8 pinned / 0 drift; doc tags rendered and checked. No commit by this pass.
