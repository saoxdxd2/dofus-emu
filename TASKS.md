# Task backlog — restored snapshot (2026-11-06)

Source: a tracker dump pasted into the session on 2026-11-06 containing **39 Waiting**
and **14 In progress** entries (53 total). Only IDs, titles and statuses were supplied —
no descriptions, acceptance criteria, owners or due dates. Original IDs are preserved
below and are prefixed in the titles of the session task list.

This file is a **backlog, not a status source**. `dofus-network/docs/CURRENT.md` is the
running-system map and `resume.md` the session handoff; `dofus-network/docs/PLAN.md` was the
status source through 2026-10-26 and is now historical;
`dofus-network/docs/KNOWN_GAPS.md` holds open defects with evidence;
`dofus-network/docs/ARCH_REASSESSMENT.md` holds the reasoning. Where this snapshot and
those documents disagree, the documents are newer and should win after an explicit owner
decision — do not silently certify a task.

---

## Project analysis (context for the backlog)

### What the system is

DofusFarm is an offline/private research testbed that runs the real Dofus Touch
Cordova/WebView2 client and drives it unattended. It is a hybrid of a deterministic
planner/rule loop (acts today), shadow-only learned heads, an executed combat solver plus
move head, and a set of weight-space-programming (WSP) experiments that try to reproduce
teacher behaviour by editing network weights directly. The design target is an actor plus
strategist inside roughly 200 MB per instance with no cloud at runtime.

| layer | path | responsibility |
|---|---|---|
| desktop host | `desktop/` (`installer.cs`, `WebView2Backend.cs`, `BootState.cs`, `clientbundle.cs`) | windows, installer, page build, boot canary |
| game adapter | `scripts/mobile-disguise.js` | the only code that touches the game |
| transport | `scripts/dofus-net-proxy.go`, `desktop/proxysupervisor.cs` | HTTP/CONNECT relay, asset cache, pinned fail-closed egress |
| agent | `dofus-network/agent/` | observe -> choose -> guard -> execute -> label -> decide |
| knowledge | `dofus-network/knowledge/`, `dofus-network/planning/` | quest/monster/item data, quest graph |
| combat | `dofus-network/combat/`, `dofus-network/kaggle/` | solver, duel benchmarks, grid teacher, WSP gates |
| evidence | `C:/Users/sao/Documents/dofus-evidence` (outside VCS) | corpora, checkpoints, control reports |
| gates | `tools/test_*.py`, `tools/test_*.js`, `dofus-network/agent/regression.py` | source checks, mutation sweeps, payload audit |

### Current state, measured from the docs touched 2026-11-06

- HEAD `404cfe2`, working tree clean; the last six commits are documentation-only,
  mostly §XII.15-XII.16c of `ARCH_REASSESSMENT.md` (party spell-target allocation).
  **Correction, 2026-11-06 18:59:** the tree was *not* clean at that time — 9 files were
  modified and uncommitted (+115/−111), including a `LayerNorm` numerical fix in
  `dofus-network/combat/encounter/model.py` and docstring honesty edits that rename the
  `wsp_program_*` tools from "WSP" to "supervised ridge regression". The "clean" line
  describes 18:29, not the snapshot.
- The all-fight joint HP-capped overlay wins 3,521/4,096 party boards, but independent
  raw teacher scoring retains 98.02% of that gain; joint minus raw is +0.1953 pp with a
  paired 95% CI crossing zero. So a general need for cross-ally coordination is **not**
  established, and no weight edit has reproduced the allocation. Nothing is promoted or
  installed (`PLAN.md` "Party scoring handoff").
- The old 298,318-row label export contained 279,999 post-terminal rows; its replacement
  has 18,319 active decisions. Seeds 701/901 have been reused and are no longer fresh
  holdouts. Any selected weight edit needs genuinely unused boards.
- The live-blocking items are operational, not architectural: `tools/live_ready.py`
  reports FAIL egress (`127.0.0.1:40000` not listening), WARN client, FAIL page build.
  No live session has used the command-shadow hook.
- Rule vocabulary is 28 tools (`data/capabilities.json`); the shipped tool policy has 21
  trained logits, so the seven newest tools are rule-only. `rules.md` forbids
  `FORBIDDEN_TOOLS` and deleting capabilities.
- The evidence directory outside version control has no backup; one disk event takes the
  whole evidence base (`CURRENT.md` §9).

### Where the five workstreams live

| code | workstream | primary paths |
|---|---|---|
| A | Weights / language layer / ~200 MB | `tools/read_weights.py`, `tools/run_distill_controls.py`, `kaggle/`, `NLP/`, `dofus-network/kaggle/`, `dofus-network/docs/AGENT3_PLAN.md` |
| B | WSP / combat policy / teacher heads | `dofus-network/agent/weight_space.py`, `dofus-network/combat/`, `tools/wsp_*.py`, `tools/test_wsp_*.py`, `ARCH_REASSESSMENT.md` §XII |
| C | Boss fights / economy / live data | `dofus-network/combat/boss_benchmark.py`, `dofus-network/combat/encounter/`, `dofus-network/advisor/market.py`, `tools/mech_input_price.py`, `tools/party_leverage.py` |
| D | Host / installer / patch pipeline | `desktop/`, `tools/patch-engine.js`, `tools/patch-rules.json`, `tools/apply-patch-rules.py`, `tools/payload_audit.py`, `tools/shipped_deps.py`, `docs/archive/CLEANUP.md` |
| E | Navigation / evidence integrity | `dofus-network/agent/planner.py`, `plan_stack.py`, `explore.py`, `dofus-network/tools/freeze_planner_replay.py`, `trajectories/`, `KNOWN_GAPS.md` crossing sections |

### Reviewer notes on this snapshot

1. **#152 conflicts with the docs.** It is "In progress" here, but `PLAN.md:459` and
   `ARCH_REASSESSMENT.md` §XII.16c ("Task #152 is complete") mark it complete; those
   files were written at 18:29 today. The snapshot is likely a few minutes/hours stale.
   Keep it in progress until the owner reconciles, rather than auto-closing it.
2. **#151 is absent**, which is consistent with the docs calling it complete.
3. **#150, #142, #77, #127 match `PLAN.md`**: #150 open, #142 needs a representability /
   numerical trace, #77 and #127 wait for weight-policy acceptance and live capture.
4. **#40 is phrased as a measured result** ("substantial accuracy loss, small size
   saving") yet sits in Waiting. `PLAN.md:462` explicitly warns that older tasks whose
   titles read as closed but whose status is pending have not been silently certified.
   Treat it as "verify and record the result", not as an implementation item.
5. **Implementation has outrun the backlog in places.** `tools/test_boot_canary.js`,
   `tools/compat_probe.js`, `tools/apply-patch-rules.py`, `tools/payload_audit.py` and
   `CURRENT.md` lines 195-199 already describe boot-canary, compat-shim, patch-rule and
   installer-payload work (#89, #90, #94, #125). Those Waiting entries are most likely
   *verification/gating* tasks, not greenfield work — confirm before re-implementing.
6. **Dangling references**: #104 refers to #108, which is not in this snapshot; #28 refers
   to "three queued verifications" that are not enumerated. Both need their source context
   recovered before they can be actioned.
7. No acceptance criteria were supplied with any title. Before starting a task, derive a
   falsifiable gate from the corresponding `test_*.py` / `test_*.js` file and from
   `KNOWN_GAPS.md`; this project's invariant is that every number in a doc is measured.

---

## Waiting — 39 tasks

| ID | Task | Workstream |
|---|---|---|
| #19 | Cost model, sparse-store capacity, store-vs-weights gate | A |
| #20 | Build language layer as a table; verify against 16.37 bar | A |
| #21 | Give G3 statistical power: 200+ sentences per direction | A |
| #22 | Prototype and gate residual-quantised staircase layer | A |
| #26 | Run FLORES chrF++ evaluation across stripped models | A |
| #27 | Push repaired ladder to Kaggle; run recovery pass | A |
| #28 | Run three queued verifications when the machine is free | A |
| #29 | Fix ternary-lattice LAMBADA scorer; rerun both arms | A |
| #35 | Merge adjacent blocks into eight weight sets; recovery-train and score | A |
| #39 | Test degradation at approximately 200 MB total system RAM | A |
| #40 | FFN narrowing result: substantial accuracy loss, small size saving | A |
| #41 | Resolve prose fidelity gap, which remains dtype-independent | A |
| #43 | Rebaseline original and int8 weights on multilingual LAMBADA | A |
| #53 | Certify policy versus encoder on approximately 250 unseen frames | B |
| #59 | Decide remaining repository cleanup | D |
| #66 | Stop requesting combat advice when nothing can be advised | B |
| #77 | Decide whether installed fights should use the trained policy | B |
| #85 | Forward-pass mutation and cross-backend verification | B |
| #89 | Boot canary, bad-build flag, and rejection of incorrectly patched builds | D |
| #90 | Decide compatibility-shim packaging; rebuild and gate installer | D |
| #91 | Measure and reduce per-window WebView2 cost | D |
| #93 | Test rebuilt host against a stale brain | D |
| #94 | Make four self-embedding patch rules idempotent | D |
| #95 | Confirm and extend clean-room symbolic override results | B |
| #96 | Decide whether advanced must mean a crossing actually completed | E |
| #97 | Verify crossing bound on a genuinely refusing border live | E |
| #103 | Correct replay corpus counting: six captures are copies | E |
| #104 | Buildings navigation diagnosis; follow-up to #108 | E |
| #107 | Detect repeated four-call cycles missed by the current guard | E |
| #110 | Final gate for counters and loop-level crossing harness | E |
| #122 | Design grid-ranker loss with usable solver-score scaling | B |
| #123 | Resolve pipeline versus cheap weight-edit gap with statistical power | B |
| #125 | Prevent installer from shipping accounts, proxies, or egress IP | D |
| #127 | Capture party composition and enemy policy from live fights | C |
| #131 | Plan boss fights from dungeon/boss data and verify outcomes | C |
| #132 | Synchronize stale Kaggle dataset before boss retraining | C |
| #134 | Ground economy objective in real kamas values or label it a proxy | C |
| #135 | Reward verified item collection rather than victory | C |
| #148 | Add player-side summons for a two-sided summon experiment | C |

## In progress — 14 tasks

| ID | Task | Workstream |
|---|---|---|
| #24 | Two-axis evaluation: language retention versus knowledge removal | A |
| #25 | Produce and verify S1+S2 stripped artifact | A |
| #51 | Unseen-frame holdout; real player utterances still missing | E |
| #52 | Gazetteer coverage report | E |
| #87 | WSP tool-policy editability; Phase 3 remains gated | B |
| #92 | Periodic, visible brain freshness checks and stop/rebuild fixes | D |
| #120 | Obtain grid-teacher boards with genuinely distinct plans | B |
| #130 | Boss-fight WSP; teacher-in-environment experiment | C |
| #138 | Establish value head as refusal signal, not one-step Q | B |
| #139 | Measure combined pre-fight gate acceptance | B |
| #140 | Correct preflight routing and outcome verification | B |
| #142 | Mechanic-aware replacement for spell-agnostic target head | B — **trace done 2026-11-06** (§XII.16d): no bias coordinate carries the decision; the missing input is HP-capped credit |
| #150 | Measure joint team-turn selection in the opening window | B |
| #152 | Isolate allocation's coordination benefit | B |

Workstream counts: A 15, B 15, C 7, D 8, E 8 = 53.

## Session log

### 2026-11-06 — #142 / #123 trace (party allocation)

`PLAN.md` step 2 ("trace the disagreements through the arrays before authoring edits") is done and
recorded as `ARCH_REASSESSMENT.md` §XII.16d. Measured on 18,319 active decisions / 2,048 boards,
with the replay proven identical to the label export row for row:

- pair agreement with the allocator: network 29.36%, script 91.33%; spell differs 60.41%, target
  25.50%.
- the failure mode is **spreading the basic cast across spells 1 and 2** (spell 0 kept on 40.9% of
  the rows that want it; the script keeps 77.35%).
- no bias edit exists: max-margin feasibility infeasible at **−9.18** logits. Best out-of-sample
  spell reproduction 80.17% vs a constant's 76.48% (+3.7), target table +1.50.
- 28.63% of rows demand different spells from identical integer-rounded logits.

Consequence: the next weight trace is not "dynamic spell-target scoring" — the target axis is
already 73-75% right. The missing input is HP-capped credit, i.e. #142, not a coordinate write.

### 2026-11-06 — weight read (WSP): three exact zeros

Read the checkpoint's numbers directly (`tools/read_weights.py`, then the arrays):

- `t_bias` (5x8) is **40 exact zeros**; a forward pass gives `max |target[s] - target[0]| = 0.0`, so
  the target head ranks targets once and reuses that ranking for every spell (#142, literally).
- spell bias max abs **1.451e-3**; spell columns not collapsed (cosines 0.004-0.195).
- `blind = (mechanics, summons, pool)` zeroes 32 of 81 actor inputs and 4 of 13 globals, and
  `read_weights.py --init-seed 1` reports `blinded_input_l2_from_init: 0.0` — those encoder rows are
  bit-identical to initialization.
- Paired unblind test (same weights, same 18,319 states): spell agreement 39.59% → **36.31%**,
  3,714 changes, 915 right / 1,516 wrong. Unmasking injects frozen noise, it does not restore
  knowledge.
- All 27 party checkpoints on disk are blind; the only visible arm (`mech_swarm_visible_s1.pkl`) is
  solo. **No party policy has ever been trained with the mechanics visible.**

Unrun experiment that follows: `python -m combat.encounter.train --data
touch-data/encounters_boss_swarm_s4_2048.npz --out models/mech_swarm_party_visible_s1.pkl
--blind "" --iters 20 --seed 1`.

### 2026-11-06 — the experiment ran: +33.4 points of spell agreement

`train.py` could not build a party arm at all: `Config` was constructed without `allies`, so it was
always 1 and every checkpoint is a solo arm relabelled at evaluation. That is also where `t_bias`'s
40 zeros came from — the shape is `[spells, n_actors]`, so re-instantiating at 8 actors created it
fresh. Added `--allies`/`--enemies` plus a guard that refuses a corpus whose actor slots disagree.

Trained on Kaggle 2xT4 (60 iters, 170 s, `--blind ""`, 8-actor party corpus) and evaluated on the
same 18,319 paired states:

| arm | spell agrees | target agrees | pair |
|---|---:|---:|---:|
| accepted (blind weights, blind input) | 39.59% | 74.50% | 29.36% |
| accepted weights, full input | 36.31% | 73.60% | 27.10% |
| trained party arm, mechanics visible | **73.03%** | 74.55% | **55.12%** |

Spell axis: **+33.44 points** from training alone — the blindness was the cause and no weight write
could have recovered it. Target axis: +0.05, because the trained `t_bias` is max 0.0095 against an
attention term spanning 116 logits — #142 needs the interaction term, not more iterations. The arm
wins less than the accepted one (52.9% vs 77.4% on the same corpus), so it is a diagnostic.

Artifacts: `models/mech_swarm_party_visible_s1.pkl` (sha `358e4e69…`), `models/party_visible_s1.json`,
`models/party_allocation_trace_s317_433_v4.{json,arrays.npz}`, `kaggle/party_visible{,_kernel}/`.
Kaggle: dataset `tajdinenizar/dofus-party-visible-kit`, kernel
`tajdinenizar/dofus-party-visible-train`.

### 2026-11-06 — how good is it (wins, not agreement)

1,024 boards per corpus, greedy vs scripted, paired: **scripted 87.9% / 86.9%**, accepted arm
**77.4% / 76.1%** (−10.45 / −10.84 pts, CIs exclude zero), trained-visible arm **52.8% / 53.5%**.

Head swaps say where the gap is: putting the accepted `move` into the trained arm changes nothing
(+0.10 / +0.20, CI crosses zero); putting the trained spell/target head into the accepted arm costs
1–2 points (−2.05 / −1.07) and buys 33 points of agreement. So the 23-point loss is the **trunk**
(60 iterations from scratch), and the spell head is nearly free — not a trade.

The checkpoint actually in the loop is different again: `dofus-network/models/encounter_selfplay.pkl`
— solo, `blind=None`, 4,096 boards, last eval 72.4% vs heuristic, never executed against the client.

Bottom line: nothing beats the scripted rule; the best learned arm is ten points short, and tonight's
improvement is in agreement, not outcomes.

### 2026-11-06 — online learning recheck + live tests

Two learners exist; only one is connected.

- **Controller repair stats** (`agent/controller.py`) — in the loop, but the state file was named
  after the session uuid, so it never accumulated: **803 files, 378 holding one step**. Fixed with
  `state_key` = instance; proven live (run 1 `{n:1}`, run 2 `{n:2}` across different sessions).
- **Conjugate posteriors** (`planning/experience.py`) — correct, tested, and **never constructed by
  the loop**: `data/kb/experience.jsonl` does not exist. Two silent defects fixed: `replay()` dropped
  every event for an unknown key (a fresh store rebuilt nothing while counting them), and
  `serve.py --schedule` raised `TypeError` twice over behind a broad `except` — now one
  `build_schedule_stack()`.
- Tests: `tools/test_online_learning_wiring.py` (8 checks). Advisor selftest still 122/122.

Live session (client launched, bridge up, 1 client): `state.snapshot` 18 ms, character L1 `Myaixdx`
on map 147062786; 3 real steps `farm.start ok noop`; **world drop mid-run, recovered in 6.1 s** via a
guarded guest login; replan to `quest.advance {quest_id: 3841, npc_id: 3888}`. Contract check: all 22
directive tools have client handlers; the 6 local ones must not be sent to the bridge.

Still missing: a producer that writes verdicts into the conjugate store. Docs: `EXPERIENCE_LEARNING.md`
"Measured state, 2026-11-06".

### 2026-11-06 — why the quest run stopped moving (G98)

Read session `fd24a682` (20:24-21:06, dofus-04): 405 rows, 24 `quest.advance`, 23 `advanced`, 8
objectives, one fight — then **306 waits** ("the policy had no action to take", max 40 consecutive)
and `blocked` at the end. The ready objective was `13507` "Ouvrez votre inventaire depuis le menu"
(40 of 46 samples) with **2 items in the bag and 0 equipped**: the step wants the item worn, and
`inv.equip` was never called (the earlier dofus-02 session used it 82 times).

Three causes: the UI surface table was English-only while the client says "inventaire"; the `equip`
route is only reachable through a quest-graph child and `kb.quest(3841)` is **None** (tutorial quests
are not in the KB); the dialog reply sent `{quest_id, reply: 0}` without `dialog.npc_id`, which the
page refuses.

Fixed: French aliases, `inv.equip` after a spent Bag press, `npc_id` on the reply. Replayed on the
session's own stuck observation: `ui.tap Bag` ×2 → `inv.equip {object_uid: 144784272}`. Also fixed
the executor dialling a stale bridge port after the host fell through to 8894/8896.

Tests: `tools/test_tutorial_dialog_reply.py` (4), `tools/test_bridge_port_follow.py` (3). Gate
2,109 checks, 0 failed (tagged numbers in `resume.md`/`README.md` reconciled to the new measurements,
Kaggle toolkit re-synced, three mutation anchors retargeted).

### 2026-11-06 — the confirmation layer: an objective ledger

The three existing layers answer "did something happen" (verifier over `STATE_KEYS`, which already
includes `ready_objectives`/`objectives_completed`; the controller's cause→repair table; the
planner's skip bound). None of them recorded **which hypothesis was tried against which objective
and what it proved** — the give-up trigger was a wait count, which is G98's 306 waits.

New: `agent/objective_ledger.py` — one record per (quest, objective), persisted per instance.
`confirm()` is the confirmation layer: an objective moves only if it leaves `ready_objectives`,
`objectives_completed` rises, or the current objective changes; a world change that leaves those
alone is a failed hypothesis. `noop` gets 3 tries, `failed` refutes at once. Blocked objectives keep
their evidence and re-open on a map/level/bag change instead of being retired for the run.

Wired into `_next_real_action`, the path the wait escalation already calls; it can only add a
candidate, and a ledger fault is caught. Replaying session `fd24a682` through it, the bag objective
reaches `inv.equip {object_uid: 144784272}` at attempt 7 instead of 306 waits.

Not wired: the quest model. `kb.quest(3841)` is `None`, so "find the map" objectives still end
blocked — the step→precondition mapping is a knowledge-build job. Design: `docs/OBJECTIVE_LEDGER.md`.
Tests: `tools/test_objective_ledger.py` (6).

Live validation blocked: `dofus-04`'s guest account now reports `ui.popup.accessDenied` (permanent
ban), so the loop correctly refuses to start. Needs a slot reset or another account.

Artifacts (outside VCS): `party_allocation_trace.py`, `party_allocation_trace.replay_v1.py`,
`test_party_allocation_trace.py` (14 checks), `models/party_allocation_trace_s317_433_v1.json` and
`.arrays.npz`. Report SHA-256 `500d3dc1…`, arrays `2edf1267…`.

## How this was saved

- Durable snapshot: this file (`TASKS.md`) — **the only surviving record.** The session task list
  described here did not survive: the session that created it ended, and a later session found the
  task list empty. Recreating 53 entries as tool state would duplicate this file without adding a
  durable fact, so it was not recreated. `TASKS.md` is the backlog of record.
- ~~Session task list: one entry per row, titled with the original ID prefix~~ — lost, see above.
  `Waiting` maps to `pending`, `In progress` maps to `in_progress`. Each description
  records the original ID, captured status, workstream and the relevant code/doc paths.
  The task tool assigns its own numeric IDs, so the original IDs live in the titles.
- Nothing was closed, reprioritised, or rewritten. In particular #152 keeps its captured
  "In progress" status despite the docs calling it complete (see reviewer note 1).

### 2026-11-06 — wiring audit: the suspicion was right about two layers

Checked by construction sites and live-log evidence (`docs/WIRING_AUDIT.md`). What a real GUI run
enables is exactly: `--goals-file --steps --interval --policy [--minutes] [--dry-run]
[--no-fight-boards] [--verifier-model] --stop-file --log [--instance]`. Everything else is a default.

- **Wired and working**: planner (rules), verifier (rules; model gated by design), controller
  (persisted per instance), plan_stack, knowledge store (all 6 local tools exercised), combat brain
  (partial — `brain: reused, encounter_ppo_pbt_actor`).
- **Had no caller**: the conjugate store (`ExperienceStore` appeared **0** times in
  `agent/autopilot.py`; reachable only from `serve.py --schedule`, which nothing launches) — now
  credited from `_credit_pending` and saved on exit; and the objective ledger (new).
- **Still unreachable**: `--explore` (default 0.0, never passed by `aipanel.cs`) so the
  learnable-failure corpus cannot grow from a real run; `--command-shadow` off; `--ensemble` needs
  `--policy planner` while the GUI defaults to `rule`.

So a normal session is rules-only by design, and two of the learned layers had no producer at all.

Tests: `tools/test_experience_producer.py` (4). Gate 2,109 checks, 0 failed. Next: pass `--explore`
from the app (C# + rebuild), then run one session and confirm
`%LOCALAPPDATA%/DofusFarm/posteriors.json` appears with `repair:*` keys.

### 2026-11-06 23:40-00:15 — closing the loop: the store now has a reader, the ledger runs live

The conjugate store had a writer and **no reader** (`repair:*` was written by `_note_experience` and
never read). Fixed: `BetaRate.observed()` recovers (hits, misses) from alpha/beta with the prior
removed, `Controller.seed_from_store()` adopts them, and `main` seeds the repair table at startup.
`tools/test_experience_producer.py` now has 6 checks, including "a new session adopts what the last
one learned" and "seeding keeps the larger evidence".

The ledger was wired but unreachable in practice: it sat behind the 40-pass escalation, so a stalled
step still sat still first. It is now consulted on the **first** wait (`wait x0` in the log), and the
`run_autopilot` entry point prints `ledger: <tool> <args> instead of waiting`.

Live session (dofus-04, planner policy) — first real evidence for both layers:

- `%LOCALAPPDATA%/DofusFarm/experience.jsonl` + `posteriors.json` now exist: 5 events, 4 `repair:*`
  posteriors, e.g. `plan_wrong:replan` alpha 3 (1 success).
- `objectives.dofus-04.json` exists; the ledger proposed `quest.advance {objective_id: 13507}` (the
  client refused — the server completes it), then `ui.tap {menu: Bag}`, then `inv.equip`.
- **`inv.equip` ran for the first time ever** and failed with the client's own words: "equipItem sent
  nothing and the item is not usable; pass slot to send ObjectSetPositionMessage" → `KNOWN_GAPS.md`
  G99 (needs a slot; the three client fallbacks all miss on the tutorial items).

Fixed live, from the log: a hypothesis was credited by *any* step with the same tool name, so the
planner's `quest.advance` (ask an NPC) scored `advanced` for the ledger's `quest.advance` (claim).
Now the tool **and** the hypothesis's own arguments must match.

Gate 2,109 checks, 0 failed. Next: G99 (resolve the item slot), then `--explore` from the app.

### 2026-11-07 03:20-04:10 — G99 solved, and the earlier diagnosis was wrong

`KNOWN_GAPS.md` G99.2 said the equip slot "is not derivable from any source on this machine" and that
only a client-side DOM probe could fix it. **Both halves of that were measurement errors**, now
recorded as G99.3:

1. **The uid was stale, not the slot missing.** The loop kept sending `object_uid: 144784272`; the
   live uids were `158323185/158323186` after the account was reset. The client answered "sent
   nothing ... pass slot", which names a slot and meant a missing item. With a live uid the same call
   reports `possible_positions: [1]` and `via: inventoryManager.equipItem` -- the template published
   the slot all along and the bag row dropped it.
2. **The button was disabled, not the wrong verb.** `ui.icons` said every icon `disabled: true`
   because the equipment window was already open, and the client disables a menu icon while its
   window is open. Close it, and `menuIconBag` becomes the only enabled icon; press it and the tap
   returns `changed: true`.

Live proof: after closing + pressing + equipping, `quest_steps["3841"]` moved 4547 -> 4974, the bag
objective left `ready_objectives`, quest 4260 ("Talk to Ayin Spyr") opened, and a single session took
the character from tutorial step 8 to step 13 with no stall.

Fixed: bag rows publish `type_id`/`type_name`/`position`/`possible_positions` (script, unverified live
-- the running client still has the old copy); `_next_equip_uid` ranks a declared slot first and never
excludes an item; `_worn_slot_for` falls back to the declared position; the planner skips the press
when the target window is already open and closes it instead (`_UI_WINDOWS`, only `Bag -> equipment`
measured); the ledger refutes a hypothesis whose premise left the world (`stale`); and the ledger
never claims a `QuestObjectiveFreeForm`, which the page completes server-side.

Also fixed in the same session, both found in one eight-step run: the dialog reply builder in
`_dialog_call` omitted `npc_id` (the tutorial path had already been fixed, so the two disagreed), and
two of eight steps were spent on that refusal.

Tests: `tools/test_tutorial_dialog_reply.py` 6, `tools/test_objective_ledger.py` 8. Gate 2,109
checks, 0 failed. New probe helper: `dofus-evidence/live_probe.py` (one directive, unwrapped, with a
distinct exit code for "client said no" vs "no client").

### 2026-11-07 04:10-05:05 — the chain actually runs, and three more defects it exposed

One live session after the G99 fixes carried the character from tutorial step 8 to step 13 and opened
quest 4260; the next took it to map 145490435, talked, equipped, talked again -- 5 advanced of 8
steps. Getting there exposed three more defects, all fixed and tested:

1. **The dialog reply loop** (`KNOWN_GAPS.md` G100). Eight steps of `quest.advance {reply: 0}`, each
   accepted with a fresh `reply_id`, none advancing the objective. `_dialog_asks` is keyed on the
   dialog *message*, so a conversation that shows a new page per answer never trips
   `DIALOG_ASK_LIMIT`. Added `DIALOG_STALL_LIMIT = 4`: replies for the same `(npc, objective)` with
   no objective change close the dialog and hand the step back to the planner, which is the only
   place an "equip, then talk" objective can be worked.
2. **The reply builder in `_dialog_call` dropped `npc_id`** -- two of eight steps were spent on "quest
   is active but no npc_id was given". The tutorial path had already been fixed, so the two builders
   disagreed.
3. **`inv.equip` reported success while nothing was worn** (G99.4). `equipItem` emitted
   `ObjectSetPositionMessage {position: 63}` (the "not equipped" marker) and the handler counted the
   send as success, so two of four recruit-set pieces never went on. It now checks `equippedItems`
   and falls back to the direct message with the template's declared position.

Also: the ledger refutes a hypothesis whose premise left the world (`stale`), never claims a
`QuestObjectiveFreeForm` (the page completes those server-side), and the planner skips a menu press
when its window is already open instead of pressing a disabled icon.

Tests: `tools/test_tutorial_dialog_reply.py` 7, `tools/test_objective_ledger.py` 8,
`tools/test_equip_slot_inference.js` 12 (its fake now calls `window.dofus.sendMessage`, which is the
path the handler instruments), `tools/test_inv_unequip.js` 13. Gate 2,109 checks, 0 failed.

Still unverified live: every 2026-11-07 script change -- the running client holds the copy it loaded
at launch. A restart is what would exercise the bag rows' `possible_positions` and the equip outcome
check against the real page.

### 2026-11-07 05:05-06:20 — the "then talk" half, and what a restart is still owed

"Equip the Albueran recruit set, then talk to Ayin Spyr" is two objectives in one string and only the
first had ever been exercised. Driving `choose` on the live shape gave: debris, debris again, then
`quest.advance {quest_id: 4260, npc_id: 3888}` -- so the talk half works, but the quest object cost
two steps. It declares nowhere to go, so one refusal is the whole answer; `_next_equip_uid` now gives
a declared-nowhere item one attempt and everything else two. New test:
`test_the_second_half_of_an_equip_then_talk_objective_is_reached`.

Checked and ruled out: there is no way to load an edited `mobile-disguise.js` into the running
client. `MaintainCdpSession` reads the file once and re-injects that captured string on every page
load, `debug.reload` says so in its own note, and the bridge has no instance-restart route
(`StopInstance` is GUI-only). So all four 2026-11-07 script changes stay JS-suite-verified; the next
instance start is what exercises them. The friction itself (re-read on navigation, cached copy as
fallback) is a `farmengine.cs` change needing `compile.bat` -- not done unattended against a running
client.

Tests: `tools/test_tutorial_dialog_reply.py` 8. Gate 2,109 checks, 0 failed.

### 2026-11-07 07:15-07:45 — de-hardcoding, starting with the one I added last night

The user's read was right: several of the night's fixes were game facts asserted in Python
(`_UI_WINDOWS = {"Bag": "equipment"}`, the French surface phrases, `type_id == 0`). The rule is now
written down in `docs/LEARNED_FACTS.md`: a protocol marker or a search budget may be a constant, a
fact the client publishes must be observed and stored.

First one converted, end to end:

- `QuestPlanner._UI_WINDOWS` -> `_UI_WINDOWS_SEED` (the single measured pair, a starting point) plus
  an instance table with `menu_window_for` / `learn_menu_window` / `load_menu_windows` / `menu_windows`;
  `fork()` carries it.
- `Autopilot._learn_menu_window` learns from the step itself -- `ui.tap` already reports `open_before`
  and `open_windows`, so it is one diff. It refuses to learn unless exactly one window appeared, and
  it ignores a press that changed nothing (a disabled icon).
- `Autopilot._remember_menu_window` persists to the knowledge base as `menu_window/<key>`;
  `_seed_menu_windows()` in `run()` adopts them, so the first press of the next run is informed.

Tests: `tools/test_menu_window_learning.py`, 8 checks through the real `Autopilot` and the real
`KnowledgeStore` (a successful press teaches and stores; a disabled press, two new windows and a
different tool teach nothing; a fresh planner adopts what was stored; a rename overwrites). Delegated
into the gate as `test_menu_window_learning_suite`, which also asserts the literal is gone and that
producer and reader both exist.

Remaining, in the doc's order: surface phrases from the client's own labels (`debug.dom` can read
them), `type_id == 0` -> a recorded refusal in the ledger, and the `possible_positions == []` bound.

Gate 2,122 checks, 0 failed.

### 2026-11-07 09:00-10:00 — THX-01 lane removed, surface labels learned

`dofus-network/pipeline/` (8 files) and the untracked `ai/thx_policy.py` are gone; the
model-free guards live in `ai/guard_policy.py`, which `HEAD` already had. The Sentinel's
THX/ModernBERT classifier and module-scope torch import are gone, so it starts in the
runtime tier; `thx01` left `requirements-ai.txt`, the builder fallback and
`tools/shipped_deps.py`. The kit's `ai/thx_policy.py` was removed as well and its
selftest now imports `ai.guard_policy`; the mirror was synced. `QuestPlanner` now learns the client's own `menu_icons` labels instead
of carrying a French→key table, and the evidence paths read `DOFUS_EVIDENCE` /
`DOFUS_BOARDS` instead of a user directory. Gate **2,132 checks, 0 failed**. No commit or
dataset upload by this pass; see `docs/archive/CLEANUP.md` for the quarantine path and recovery.


### 2026-11-07 10:30-10:35 -- structure pass: docs/repo maps reconciled, dead notebooks removed

`dofus-network/docs/README.md` now lists all 23 design docs (it claimed fifteen and omitted six
current ones), names `CURRENT.md` + `resume.md` as the status sources, and corrects the tool
count to 28. Root README section 4 maps all 18 brain packages and the measured trajectory count
(39 files / 256,733 rows, 2026-11-07; desktop C# and the relay re-measured at 23,919 and
3,500 lines); section 17 adds the eight unlisted docs and a root-docs
table marking the October status snapshots historical. Removed the two Colab notebooks for the
dead THX-01 and DofusBrain lanes (quarantined with SHA-256; `git show` recovery recorded) and
the untracked `.gate_run.log`. The nine benchmark/eval JSON that sat loose in `docs/` moved
to `docs/benchmarks/` (the ARCH_REASSESSMENT citation updated), and ten unreferenced
`*_out.txt` scratch outputs left `scripts/`. The five historical status docs now carry
banners, and the live docs no longer name `PLAN.md` as the status source. Full gate after the
reorganization: **2,132 checks, 0 failed**; doc gate checks: 20 checks,
0 failed; doc tags pass; toolkit sync 0 drift. No commit or dataset upload.


### 2026-11-07 12:00-12:40 -- tool pipeline: schema owns the classes, gate proves the ties

`agent/schema.py` carries `read_only`/`moves`/`lists` per tool (the seed applies them by name,
`save_capabilities` writes them, `_load_capabilities` reads them), and `data/capabilities.json`
was regenerated for all 28 rows. `agent/verifier.py` now derives `READ_ONLY_TOOLS`/`MOVE_TOOLS`/
`LISTING_TOOLS` from that registry; the last local copies were removed from `agent/autopilot.py`
(which imports them) and the dead `farm.step`/`travel.zaap` names from the SFT builder. The
gate's `test_tool_vocabulary` (18 checks) fails on seed/registry class drift, an unknown
`TOOL_ORDER` name, or any disagreement between the trainer's 21-label order, the featuriser's
`LEARNED_TOOLS` and the shipped `models/tool_policy.json`. Gate **2,140 checks, 0 failed**;
toolkit 0 drift; doc tags rendered and checked. No commit or dataset upload by this pass.


### 2026-11-07 12:45-13:15 -- tool pipeline pass 2: every label set tied to its consumer

`ai/train_entity_pointer.py` derives its candidate tools from `agent.schema.tool_names()` instead
of a 25-name tuple (the builder already gates coverage over all 28, so the three later tools were
unlabelable). The seed and `data/capabilities.json` were reconciled on three parameter shapes
(`farm.start.restart` into the registry, `inv.equip.remove` into the seed, `inv.sell.quantity`
default 1) and the gate now compares every parameter shape. `test_tool_vocabulary` (26 checks) also
pins the command model's intents/actions/tags, the encoder sidecar's labels, the tool policy's
feature width, and a client handler for every directive tool; each check was mutation-tested. The
stale cause head (features 2118 = 25-tool axis) is labelled `retired` and the gate enforces the
label. Full gate **2,149 checks, 0 failed**; toolkit 0 drift; doc tags pass. No commit by this
pass.
