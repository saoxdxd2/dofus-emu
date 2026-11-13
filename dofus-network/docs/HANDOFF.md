# HANDOFF — read this before any other file

**Updated:** 2026-11-11. **Scope:** everything a new session needs in order to act without
re-deriving this project from 20,000 lines of prose.

This document exists because the repository's knowledge is real but scattered across `resume.md`
(6,005<!--f:resume_lines--> lines), `docs/KNOWN_GAPS.md` (8,060<!--f:known_gaps_lines-->), `docs/ARCH_REASSESSMENT.md`
(6,768<!--f:arch_lines-->) and five daily memory logs — and a new reader cannot tell which of those is
authoritative for which question, nor what the private vocabulary means. Read this file first; it
routes, it does not replace. The three sizes are rendered from the files themselves
(`tools/doc_facts.py`), because the hand-typed versions had all drifted by 2026-11-13.

---

## 0. What this project is, in five sentences

DofusFarm runs **Dofus Touch** (Ankama) on Windows as the *real* Cordova bundle inside Edge
WebView2, served from disk under the virtual origin `https://dofus.local/`, with an injected script
that disguises the browser as a phone. It is **not** an emulator: no Android, no APK, no QEMU.
Every packet goes through a local Go relay that owns a pinned egress and an asset cache. A Python
agent (`dofus-network/`) drives the game through a 28-tool API and is the thing being built; the
host (`DofusFarm.exe`, C# WinForms) is a window manager and log viewer. The operator is one person,
the output is not redistributed, and the project's own rule is the correct summary: **the harness is
not an AI — the harness supplies data and tools, the AI makes decisions.**

---

## 1. State of the project, honestly

The agent **acts live**: it logs in, stays connected, captures the quest catalogue, finished the
tutorial, completes objectives in the 4260/4262/4264 chains, fights, and equips items.

It does **not** finish quests end-to-end unattended. No `QuestCompletedMessage` row exists in any
capture; `tools/session_report.py`'s "quests completed" column is a delta of a page counter whose
two views disagree. Treat it as a hint.

The binding constraints, in order:

1. **Account risk.** Two permanent guest bans are on record. This is the fleet's real ceiling and no
   amount of model work moves it.
2. **Volume of lived outcomes.** `agent/gate.py`'s veto has produced zero shadow rows. Nothing
   actually vetoes yet.
3. **Quest progress**, not combat. Combat decisions are now worth ~0.011 of an HP bar per turn
   (§6 below); 100 quest advances cost ≈9,692 actions ≈ four days of sessions.

Combat is the most *measured* part of the system and the least *valuable* part. That inversion is
deliberate — it is where the instruments are good enough to settle arguments — but do not mistake
measurement density for importance.

---

## 2. Verify it yourself before you trust any of it

Run from `dofus-network/`. These are the six commands that turn this document from claims into
facts. Expected output is stated so a mismatch is visible.

```bash
cd dofus-network
python -m agent.regression                      # the gate: 2,448<!--f:gate_checks--> checks, exit 0 = green
python scratch/chain_reachability.py            # which rankers are actually reached
python tools/sync_kaggle_toolkit.py             # 288 sync, 8 pinned, 0 drift/missing
python -m combat.duel_value --evaluate scratch/dv_rank_v3.pkl --bench-games 200
```

The two `python -m unittest` lines are in the table below rather than the block, because
`test_docs_command_blocks_state_their_directory` resolves every `-m` argument as a *repository*
module and `unittest` is stdlib — the block stays inside what that rule can check, instead of
teaching the rule an exception.

| # | command | expect |
|---|---|---|
| 1 | `python -m agent.regression` | **2,448<!--f:gate_checks--> checks**. Slow — measured **10m10s** on 2026-11-11 (it runs a mutation sweep). Exit 0 = green. |
| 2 | `python -m unittest combat.test_live_policy` | 36 tests, OK (~0.3 s) |
| 3 | `python -m unittest combat.test_duel_value combat.test_battle_solver combat.test_side_swap` | 102 tests, OK (~38 s) |
| 4 | `python scratch/chain_reachability.py` | `hp_choice_stats {'asked': 1}` on the adjacent board — proves the shipped ranker is reached |
| 5 | `python tools/sync_kaggle_toolkit.py` | `288 sync, 8 pinned, 0 drift/missing`. Any drift means a mirrored file was edited without re-syncing. |
| 6 | `python -m combat.duel_value --evaluate scratch/dv_rank_v3.pkl --bench-games 200` | a `benchmark` JSON block. Reproduces the −0.1463 duel result. |

### The gate's state on 2026-11-11, and what it cost to learn

Two full runs, both recorded:

| run | result | what it was |
|---|---|---|
| 18:05 | 2,187 checks, **5 failed** | the true starting state, never reported before this session |
| 18:29 | 2,220 checks, **1 failed** | after the three code fixes; the one left was a stale tagged number |
| 18:42 | 2,220 checks, **0 failed**, exit 0 | **confirmed green**, 11m28s. Logs: `scratch/gate_full_2026-11-11{,b,c}.log` |

The five failures of the first run, and what each actually was:

| failure | diagnosis | fixed |
|---|---|---|
| `the checked-in Kaggle toolkit matches the harness: ['agent/regression.py']` | the mirror predated this session's edits. The check is doing its job. | re-synced |
| `test_brain_autostart ran to completion: KeyError: 'url'` | **stale test seam, not a broken brain.** `brain_is_up` was moved to `opener_for(target).open(...)` on 2026-11-11 to stop a loopback probe going through `HTTP_PROXY` (measured `WinError 10054` via `HTTP_PROXY=http://127.0.0.1:52004`). The test still patched `urllib.request.urlopen`, intercepted nothing, and died. Now patches `bs.opener_for`. | fixed, 28 checks restored |
| `test_group_refusal_routing ran to completion: AttributeError: '_fight_summary'` | **the shim fell behind `summary()`.** `Autopilot.summary` grew a `self._fight_summary()` read; the hand-built `SimpleNamespace` shim did not have it. Second time this shim has broken this way. | fixed |
| `a healthy brain is named by its checkpoint ... {'model': 'encounter_ppo_pbt_actor'}` | same stale seam as row 2 — a consequence, not an independent defect | fixed with it |
| `every tagged number equals its measurement: resume.md says 2,213, measured 2,216` | 16 citations of a gate count that had drifted. This is the tag system working: `<!--f:gate_checks-->` is machine-read from `data/last_gate_run.json`. | all updated to 2,220 |

**The lesson worth carrying:** three of the five were *the instrument drifting from the code it
measures*, not the code being wrong. A gate whose failures are stale tests is worse than no gate,
because it trains the reader to ignore red.

**2,448<!--f:gate_checks--> is a tagged number.** If you change the gate, re-run it and update every
`<!--f:gate_checks-->` site; `test_docs_quote_measurements_by_tag` will tell you which ones drifted,
by file and line. Do not type the number by hand anywhere else.

---

## 3. The private vocabulary — this is why the docs are unreadable otherwise

| term | means | where |
|---|---|---|
| **solver** | `combat/battle_solver.py`. Deterministic beam search over one turn; returns `TurnPlan` candidates ranked by a weighted score. It is the incumbent and the teacher. | `battle_solver.py` |
| **plan** / `TurnPlan` | One turn's action list plus `final_hp` — the solver's *own projection* of every fighter's HP after that plan. | `battle_solver.py` |
| **the residual** | The rule "pick the candidate with the largest projected HP differential", i.e. `argmax(sum(my HP share) − sum(their HP share))`. Called *residual* because the learned model was built as a correction on top of it. It is now the **incumbent**: the bar to beat, not the solver. | `live_policy._hp_choice` |
| **`dv_rank_vN`** | Iterations of the learned ranker, `scratch/dv_rank_v{1,2,3}.pkl`. v3 is the best of them and **loses** to the residual. | `scratch/` |
| **regret** | `best truth − truth of the chosen plan`, in HP bars. "Truth" is a **rollout**: apply the plan, play the rest out with a fixed policy, read the final HP differential. Not agreement with the teacher. | `scratch/rank_by_truth.py` |
| **HP bar** | One fighter's full HP, normalised to 1.0. "+0.0113" means 1.13% of one bar. | everywhere |
| **`boards_differed`** | How many boards the two policies actually disagreed on. **Ties are not evidence**; a p-value over 600 boards of which 121 differed is a statement about 121. | `duel_value.evaluate` |
| **G-numbers** | Entries in `docs/KNOWN_GAPS.md`. `G98`/`G99`/`G100` are each used **twice**; quote the date with the number. | `KNOWN_GAPS.md` |
| **§L.x** | Subsections of **section L** in `ARCH_REASSESSMENT.md` (L.1–L.43). `§XI`/`§XII`/`§XIII` are top-level roman sections of the same file. Two numbering schemes, one file. | `ARCH_REASSESSMENT.md` |
| **the gate** | ⚠️ **Ambiguous, always disambiguate.** `agent/regression.py` = the 2,448<!--f:gate_checks-->-check test gate. `agent/gate.py` = a shadow-only repeat-streak veto. Different things. | both |
| **the ladder** | `agent/hunting.py`'s engagement ladder: `join` → `travel`, gated by a *learned risk budget* (total monster level ÷ own level, raised by wins, lowered by losses). | `hunting.py` |
| **toolkit mirror** | `kaggle/toolkit/` — a byte-copy of selected files for Kaggle. Editing a mirrored file without re-running the sync leaves the copy stale; `test_kaggle_toolkit_sync` catches it. | `tools/sync_kaggle_toolkit.py` |
| **`<!--f:name-->`** | A **fact tag**. The number immediately before it is machine-checked against a named measurement, e.g. `2,220<!--f:gate_checks-->` is verified against `data/last_gate_run.json`. ~55 tags exist (`tools`, `gate_checks`, `live_steps`, `fight_head_wins`, …). `test_docs_quote_measurements_by_tag` reports any drift **by file and line**. Never type such a number without its tag, and never edit the number without re-running the measurement. | `agent/regression.py` |
| **asked / fired / changed** | Per-branch counters on `LivePolicy`. A branch that never ran and a branch that ran and agreed are different findings, and only the counters separate them. | `value_choice_stats`, `hp_choice_stats` |
| **`value_ready`** | Whether the observation contract carries the enemy's spellbook. Gates `_value_choice`. ⚠️ It is assigned **only on the actor path** (`live_policy.py:967`), which `_solver_turn` returns before reaching — so on the solver path it is always stale. | §6 of `COMBAT_POLICY_PLAN.md` |

---

## 4. Which document is authoritative for what

**Rule: a number is trusted only from the section that measured it, with its date.** `resume.md` §0
is a summary, and summaries drift. When §0 and the measuring section disagree, the measuring section
wins and §0 gets corrected — that exact failure happened on 2026-11-11 and is why this rule is
written down.

| question | authoritative file | caveat |
|---|---|---|
| What runs, on what port, wired how | `docs/CURRENT.md` | ports/paths read from code **2026-10-26**; re-verify before relying |
| What is broken or unproven | `docs/KNOWN_GAPS.md` | G98–G100 collide; quote dates |
| Live status, this session, what changed | `resume.md` §0 + §2 | a summary — prefer the measuring section |
| What the AI is; shipped vs opt-in vs frozen | `docs/AI_ARCHITECTURE.md` | 2026-11-07 |
| **Why** a decision was made, and its falsifier | `docs/ARCH_REASSESSMENT.md` (newest section wins), `docs/PROJECT_LOG.md` (D1–D24) | `ARCH_REASSESSMENT`'s status column is superseded by `CURRENT.md` |
| What to do next about combat | `docs/COMBAT_POLICY_PLAN.md` | six sections, written 2026-11-11 |
| What to do next about anything else | `resume.md` §2.4 | |
| Wire format of an action | `docs/PROTOCOL_NOTES.md` | written from captured traffic, never from the bundle |
| Rules of engagement | `rules.md`, `AGENTS.MD` | `rules.md` is project-specific and short; read it |
| Daily history | `.workbuddy-ai/memory/YYYY-MM-DD.md` | append-only, one per working day |

`docs/README.md` is the longer map of the twenty documents in `docs/`, with a row per file and a
"still current?" column. It is the second read, not the first: it assumes the vocabulary in §3.
It was brought up to date on 2026-11-11 (three documents had been added without a row, and its
"sixteen documents" count said sixteen where there are twenty).

### 4.1 How to read a document that is longer than you want to read

None of the big three is meant to be read end to end, and on 2026-11-13 each gained a generated
index so that it does not have to be:

| document | how to enter it | what the index gives you |
|---|---|---|
| `docs/KNOWN_GAPS.md` | its `## Index` table, 121 rows | every entry's claim and the verdict its heading records; `—` means no verdict was recorded. Jump to the entry, ignore the rest. |
| `docs/ARCH_REASSESSMENT.md` | its `## Index of sections`, 35 rows | one line per section, in the file's own two numbering schemes (`A`–`K`, `N.8`, `XIII`). |
| `resume.md` | its `## Contents` table, 25 rows | one line per section; §21 is a dated amendment log, newest near its top. |

The indexes are generated from the headings by `dofus-network/tools/docs_navigable.py`, so they
cannot drift from the sections below them. Closed gaps whose own heading said `(fixed ...)` moved,
verbatim, to `docs/archive/KNOWN_GAPS_closed.md` — a gap register's job is what is *not* true yet,
and the register keeps a one-line row for each closed one so the pointer survives.

**The read budget, stated so it can be checked:** a session that reads HANDOFF plus the three
indexes has read about 600 lines and knows what each document contains. Reading the three
documents whole is 6,005<!--f:resume_lines--> + 8,060<!--f:known_gaps_lines--> + 6,768<!--f:arch_lines--> lines, and is only
correct when the task is to audit one of them.

---

## 5. Traps that each cost a session

Every row has a date and a number, because "be careful" has never prevented anything.

| trap | the number that exposes it |
|---|---|
| **The paired statistic is the SUM, not the difference.** Both games of a side-swapped pair report *a*'s own margin; `m1 − m2` is seat bias and zero by construction. | The old estimator printed `p = 1.0` always and manufactured a null result. Fixed 2026-11-11. |
| **Both games of a pair must start from the same board.** `make_duel` draws engagement cells from the rng, so advancing the stream between calls silently compares two different boards. | Pass an explicit `board_seed` and `board_seed + 1`. |
| **Regressing an absolute return spends capacity on the board offset.** | 93.5% of target variance is between-board; the offset cancels when ranking. Fixed with McFadden conditional logit. |
| **Standardising per board turns a flat board into a confident target.** Use a fixed temperature in return units. | A flat board's plans differ only by continuation noise (std 0.0107 against a within-board 0.0941). |
| **Worker thread oversubscription.** Every worker imports jax; each XLA pool spins its own threads. | 65% CPU across 9 processes. Fix: `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `XLA_FLAGS=--xla_cpu_multi_thread_eigen=false intra_op_parallelism_threads=1`, set **before** the Pool. |
| **`DofusFarm.exe` running locks the C# build.** | Close the host before rebuilding, or the build silently keeps the old binary. |
| **"The branch never ran."** A ranker that is never reached looks identical to one that is reached and agrees. | `_value_choice` read `asked = 0` across 3,964 duel calls and 135 live boards. Always add a counter. |
| **One function, two call sites, two different gates.** `_value_choice` is called at `live_policy.py:708` (gated on an empty `matching` list) and `:753` (gated on `_value_ready`). §L.22's "it is not the readiness gate" is about the first. | Conflating them produced a wrong cause in `resume.md` on 2026-11-11. |
| **A branch's flag can be assigned after the branch returns.** `_value_ready` is written at `:967`, below the solver-first early return. | So on the solver path it is always the previous turn's value. |
| **Never compare numbers across estimators.** | `dv_rank_v2` = −0.2659 unpaired and −0.2646 paired; the residual = +0.0229 unpaired and +0.0113 paired. Same policies, 2.7× apart. |
| **Don't trust the wall clock.** A run killed at 3m37s was believed to be 7 minutes in and half-done. | Read `time`, not impression, before killing a job. |
| **`git status` is always dirty here** (30+ modified files). That is the norm; do not "clean" it. | |

---

## 6. What changed most recently (newest first)

| date | finding | the number | where it is recorded |
|---|---|---|---|
| 2026-11-11 | **The gate's red was mostly the instrument drifting from the code**, not broken code: a stale `urlopen` patch, a shim missing `_fight_summary`, a stale mirror, a drifted tagged count | 2,187 checks / 5 failed → 2,220 / 0 | §2 above; `scratch/gate_full_2026-11-11*.log` |
| 2026-11-11 | **The duel benchmark could never have produced a non-null result** — it reported the pair difference instead of the sum | `p = 1.0` always | `ARCH_REASSESSMENT.md` §XIII |
| 2026-11-11 | The absolute-value model's failure was an **estimand** error, fixed with conditional logit | regret 0.0250 → 0.0210 → 0.0189 | §XIII |
| 2026-11-11 | **Shipped: HP-differential re-ranking** (`_hp_choice`, default on) | +0.0113 HP bar, 104/17, t = 5.4, CI [+0.0072, +0.0154] over 600 paired duels | `live_policy.py`, §XIII |
| 2026-11-11 | The **learned ranker loses** to that rule; cause is off-policy value error, not capacity | −0.1463 (7 better / 160 worse) | `COMBAT_POLICY_PLAN.md` §2 |
| 2026-11-11 | Re-ranking the solver's candidates is worth **at most ~1% of a bar** | regret ceiling 0.0209 (k=6) → 0.0295 (k=30); blind 0.0626 | §XIII, plan §1 |
| 2026-11-06 | Bosses can summon; summoning is worth less than an attack | | `ARCH_REASSESSMENT.md` §XII |
| 2026-11-06 | The party stopped being an abstraction; a ruler was hiding the real number | | §XI |

---

## 7. What to do next

`docs/COMBAT_POLICY_PLAN.md` is the only plan with a mechanism and an acceptance criterion. Its
short form: make the value on-policy (P1.1–P1.4), duel it against **the residual** rather than the
solver, at most three rounds, acceptance = CI excluding zero **and positive**.

Before starting it, read its §4. The honest position is that the next hour is worth more in the quest
loop than in the per-turn planner, and the plan says so itself.

---

## 8. Non-negotiables (`rules.md`, condensed)

- 28 tools, authoritative in `data/capabilities.json`, asserted by `test_tool_vocabulary`. Never add
  `FORBIDDEN_TOOLS`, never delete a capability, never add a "will not do" section or a disclaimer to
  markdown. Measured/unmeasured labels and known-gap evidence are required and are not disclaimers.
- **Before adding a feature, clean the code you touch** — the stray import, the dead branch, the
  stale number in a string. Say what you cleaned, so the next reader can tell cleanup from
  regression. Keep the cleanup inside the touched scope.
- **A decision records its falsifier** — the observation that would overturn it.

---

## 9. Keeping this log surface alive

The failure mode this document was written to fix is not "no documentation" — it is **documentation
that is confidently wrong**. Four rules, each of which was broken here within one session:

1. **When you change a number, re-run its measurement.** A `<!--f:tag-->` makes this cheap; use it.
2. **When you change a code seam, change the test that patches it.** Two suites died this way
   (`urlopen` → `opener_for`; `summary()` grew `_fight_summary`). A test that patches a seam which
   no longer exists passes *silently* — these only failed because they raised.
3. **When you add a document, add its row.** `docs/README.md` had three files with no row and a
   count that had drifted; the map was lying about its own contents.
4. **Daily memory logs are append-only, one per working day**, in `.workbuddy-ai/memory/`. Distil
   anything older than 30 days into `MEMORY.md` and delete the original.
   ⚠️ `2026-10-22.md` is **2,723 lines** — it is the only log of that size, it predates this rule,
   and it should be distilled rather than read. As of 2026-11-11 it is 20 days old.

The one thing not to do: rewrite a log to look consistent. When this session found `resume.md` §0
carrying two mutually exclusive residuals, the fix was to mark the superseded one as *superseded and
not a result*, not to delete it — a log that admits its errors is more useful than one that has none.
- **Numbers are measured or labelled.** Anything unmeasured says so, in those words.
- **Capture before coding.** A protocol note is written from traffic, never from reading the
  minified bundle.
