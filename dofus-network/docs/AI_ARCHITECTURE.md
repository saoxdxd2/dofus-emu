# AI_ARCHITECTURE — the canonical map of the learned agent (shipped vs target)

Written 2026-10-27. This document is the single source of truth for **what the AI is**: which
components exist, what each one decides, where it plugs into the code, and what evidence a
component must produce before it may replace a rule. `TRAINING_PLAN.md` holds the training
programme, `ARCH_REASSESSMENT.md` and `AGENT2_DESIGN.md` hold the reasoning that led here, and
`KNOWN_GAPS.md` holds the open defects; when they disagree with this file about *structure*, this
file wins.

Everything below is labelled **shipped**, **shadow**, or **target**. Nothing is claimed as live
that was only measured offline. Gate at time of writing: `python -m agent.regression` →
**922 checks / 0 failed**; mutation driver `tools/mutate_cause_gate.py` → **329 mutations**,
command batch 19/19 caught.

## 0. The division of labour (permanent)

| Layer | Owner | Examples | Can a model ever own it? |
|---|---|---|---|
| **Legality** | code | tool registry (`agent/features.py:TOOLS`, 25 tools), arg schema (`agent/schema.py`), `args_for_tool` | never |
| **Safety / recovery** | code | stop file, kick/disconnect recovery, `_farm_allowed` guard, repeat/no-op guards | never |
| **Knowledge** | data | quest graph, map graph, client-reported observed quests | never (it is content, not a decision) |
| **Decision** | model | what to do next, which tool, which dialog reply, whether the world moved | yes — and it must, that is the whole project |

The model may only ever **rank legal candidates**. A proposal the harness rejects is logged as a
refusal, and refusals are supervision (`docs/FAILURE_TO_SUPERVISION.md`), not noise.

**Process model (2026-10-27).** The host starts **one** `run_brain.py` daemon on
`127.0.0.1:8895`; the daemon runs one `Autopilot` thread per instance and shares the
Python interpreter and process-wide models/knowledge. The old per-instance
`run_autopilot.py` child path remains as a fallback. See `BRAIN_DAEMON.md`.

**Tool vocabulary (2026-10-27).** The legal registry is **25 tools**: the original 21 plus
`hdv.buy`, `inv.equip`, `hdv.prices` and `tap.interactive`. The shipped 49K tool policy has
**21 trained logits**; the four research tools are rule-only until a new head is trained. The
legal registry and the learned head are separate contracts, so adding a capability does not
silently change the model's output width.

## 1. Shipped inventory (measured, this machine)

| # | Component | Artifact | Size | Decides | Status |
|---|---|---|---|---|---|
| 1 | Domain language trunk | `models/encoder30_v15` (frozen) | **24.9M** params | hidden states for command/entity text | shipped, teacher + encoder |
| 2 | Command heads | `models/command_encoder30/heads.pt` | **0.29M** | intent (13), action, BIO slot spans | shipped, **shadow-only parser** |
| 3 | Routed per-instance adapter | `heads_adapter*.pt` + `router.json` (cosine, thr 0.95) | 0.29M | the 16 recorded user goals | shipped, base **43/43** unchanged |
| 4 | Deployable runtime form | `command_encoder_int8.onnx` + `command_onnx.json` + `tokenizer.json` | **25,813,919 B** | same as 2+3, torch-free via `agent/command_onnx.py` | shipped, in `setup.exe` (100.1 MiB, 513 entries) |
| 5 | Shadow instrument | `agent/command_shadow.py`, `tools/command_shadow_report.py` | — | did the model and the rule agree | shipped (`--command-shadow auto`, default off) |
| 6 | Replay instrument | `tools/command_replay.py` | — | did the parsed action's tool get executed, did the session advance | shipped: 71 groups / 5,721 steps, 46 advanced / 25 stalled |
| 7 | Tool policy (opt-in) | `ai/train_tool_policy.py`, `--policy` | 49K MLP | which tool (21 trained logits; 25 legal tools) | shipped, opt-in, not the default path |
| 8 | Combat actor | JAX PPO `dofus-network/models/encounter_selfplay.pkl`, `combat/brain_service.py` | ~0.2M | per-turn fight action | shipped + auto-start probe; **never taken a live turn** |
| 9 | Live evidence runner | `tools/live_evidence.py` | — | refuses to claim anything without a client window | shipped; live bridge reachable, evidence accumulates per run |
| 10 | Tool ensemble | `models/tool_stack.json`, `agent/ensemble.py`, `ai/train_stacker.py` | 21 KB, 23 leaves | which tool: policy + foresight + K-means context | shipped, gate-passed, active only under `--policy planner` |

Measured held-out numbers that justify keeping these artifacts: 26 held-out base phrasings —
**84.6% exact command vs 53.8% rule**, intent **96.2% vs 69.2%**
(`models/command_encoder30/metrics_eval.json`). On the 7 real user goals, the routed adapter maps
**7/7** to the expected tool and would change the planner's *goal mode* on **0/7** — the parser's
value is inside quest mode (list → run), not in routing.

## 2. The problem this architecture exists to solve

Not "the model is too small". The measured failure is that **actions do not change the world**:

* of 1,277 world-changing steps in the recorded baseline, **58 moved anything (4.5%)** —
  `quest.advance` 32/972, `nav.goto` 26/222, `farm.start` 0/59, `fight.join` 0/24;
* `objectives_completed` is `0` in **every** one of 8,526 real observations, across 172.5 min;
* 25 of 71 replay groups stalled, and the taxonomy says these are **short failures, not read
  loops** — so a better parser cannot fix them.

The 49K tool policy is trained on the harness's counterfactual forks and does rank tools by real
progress (`sim/tool_choice.py` stores `gains` and `curves` per candidate), but it is a **state →
single-tool classifier**. That shape has three consequences, and they are the reason for §3:

1. it cannot answer "would *this* candidate move *this* state?" for a candidate the teacher never
   picked in a similar state — so it cannot beat the rule where the rule is wrong;
2. it has no calibrated notion of "nothing will work here, so this is the moment to look, replan,
   or escalate", which is exactly the stall the logs show;
3. its supervision lives entirely in the harness. There is no row anywhere that trains or tests it
   against a **real** observed outcome, so the sim-to-live gap is unmeasured.

## 3. Chosen method: candidate-conditioned foresight, with adaptive escalation

The architecture bet for v2 is a **learned progress model** (a small world model restricted to the
observable delta the client can actually report), used to *rank legal candidates*, plus an explicit
escalation decision. Chosen after a literature pass; the transplants and why:

| Source (2026) | What it does | What this codebase takes |
|---|---|---|
| **DynaWeb** (arXiv 2601.22149) | a model predicts environment state changes; the policy trains on imagined rollouts mixed 50/50 with real trajectories; binary task-completion reward | the **forward model as a first-class component**, and the discipline of mixing real logged outcomes with generated ones instead of trusting either alone |
| **Imagine-then-Plan / ITP** (arXiv 2601.08955) | adaptive lookahead horizon chosen from goal-vs-task progress; conditioning on imagined futures gained +7 (training-free) to +24 (learned horizon head) SoPR, and beat fixed/random horizons on the success-per-cost curve | an **escalation head**: 1 cheap pass for routine steps, deeper lookahead/replanning when the predicted curves are all flat. Compute is spent on hard states, which is the same thesis TRM argues with depth |
| **HiMAC** (arXiv 2603.00977) | separates macro planning (blueprints) from micro execution and assigns credit per level; +16% over a flat-group advantage baseline | a **macro/micro split with separate acceptance gates**: macro = which quest/objective and which goal mode, micro = which tool now. A bad micro step may not poison the macro credit — this is why the shared intent head failed when specialised per instance |
| **TRM / Less is More** (arXiv 2510.04871) | weight-shared recursion, 7M params, strong on ARC | **rejected as the core shape**: recursion was already tested twice on this corpus (see §6). Kept only as the *halting* idea inside the escalation head |
| **RWML** (arXiv 2602.05842), RLxF (ICML 2026 workshop) | agents that anticipate action consequences from world feedback rather than human labels | the label rule: **outcomes come from the server's own validation traffic**, never from the rule that proposed the action |

**Shape of the component** (this is the part that is new, and it is deliberately tiny):

```
observation ──► agent.features.objective_features() ──► 42 floats        (already exists, shared
                                                       + candidate tool one-hot (21)   by sim+live)
                                   │
                                   ▼
                     foresight net  ≈ 0.4–2.0M (2-layer MLP over [state ⊕ tool ⊕ tool⊙state])
                                   │
              ┌────────────────────┼─────────────────────┐
              ▼                    ▼                     ▼
     gain_hat[1..DEPTH]      move_prob            flat_state flag
   (expected objective    (P(the observable     (all candidates flat →
    curve per horizon)     world changes))       escalate: read / replan /
                                                   re-observe / abstain)
```

* **Trained on harness forks** (`data/tool_sft`, 25,023 train / 2,782 val rows) whose `gains`
  (per candidate) and `curves` (per depth) are already recorded — so the target is a *fact about
  the world*, not the teacher's opinion, and the row weights already exist (`margin`, `trivial`).
* **Tested on real outcomes** (`tools/build_live_transitions.py`, §4) — the sim-to-live transfer
  number is the headline metric, and it is honest only because labels there come from observable
  deltas between consecutive logged observations, not from `ok`/`error`.
* **Deployed as a ranker, shadow first**: for each legal candidate, log `argmax model` vs
  `chosen rule` vs `did it move`. It replaces a rule only per §5.

Why not the alternatives that were considered: a **larger LM** (the user's budget is ≤30M total /
≤10M active and fluency is not needed — parsing is already at 84.6% with 0.29M on a frozen trunk);
**recursion as the trunk** (twice measured, no gain, §6); **one flat 21-way classifier per
decision** (existing, and it is the thing this replaces on measured evidence); **learning the
knowledge graph** (content changes with the game; it stays in the pipeline).

## 4. Data contract (what each trainer may read)

| Dataset | Producer | Rows | Labels | Honest limits |
|---|---|---|---|---|
| `data/commands` | `tools/build_command_dataset.py` | 1,619 / 20 / 23 | intent, action, BIO slots | 26 held-out base phrasings; eval texts and near-copies excluded from train |
| `data/tool_sft` | `sim/tool_choice.py` | 25,023 / 2,782 | per-candidate `gains`, `curves`, `margin`, `trivial` | harness world; noise/staleness/travel cost exist but are opt-in flags |
| `trajectories/*.jsonl` | live pages | 4,785 rows here, 73,081 in the wider corpus | none until §4.1 turns them into transitions | real, but sparse: 4.5% move rate; `objectives_completed` always 0 |
| `data/command_adapters/user_goals.jsonl` | hand-labelled from live prompts | 16 | parsed intent/action per real goal | per-instance data; **may never enter base train** |

**4.1 Transitions (target, being built).** `(obs_before, tool, args, obs_after, moved)` from
consecutive `kind:"step"` rows in one session, keyed by `instance` and `ts`. `moved` is the OR of
observable deltas only: `map_id`, `x/y`, dialog presence/`reply_count`/`reply_id`, objective
`stepId`/`finishedCount`/parameter counters, `in_fight`, group ids, inventory/kamas. Reads are
labelled `moved=false, was_read=true` so a model can never earn credit for looking.

## 5. Acceptance gates (in order, no skipping)

1. **Held-out harness outcome**: better `gain_capture` than the 49K policy at matched params and
   matched data, on episode-held-out val rows. A 100%-looking number is treated as a leak until
   the leak check says otherwise.
2. **Sim→live transfer**: AUC of `move_prob` on real logged transitions, plus calibration (ECE),
   plus a stated base rate (4.5%) so the lift is comparable to nothing.
3. **Shadow session**: the ranker runs, the rule acts, and every decision logs candidates,
   predicted gains, and the realised outcome. `live_evidence.py` must report the rows.
4. **One component at a time**: only after 1–3 may a rule be removed, and only the named rule, with
   the fallback kept and the mutation driver proving the gate is non-vacuous.
5. **Adapters** obey the existing guards: `adapter_accepts` (no held-out base loss), `canary_accepts`
   (no action-critical canary may move), rehearsal from base rows, rollback via `heads_base.pt`.
   Routing, not shared-head specialisation — that is the measured lesson of §6.

## 6. Measured and rejected (so nobody redoes it)

* **Shared-head specialisation** — intent+action and intent-only fine-tunes on the 16 real goals
  both lost held-out base rows (25/22 → 21/18) even with 160 rehearsal rows and per-epoch guarding.
  Rejected; replaced by the routed per-instance adapter (base 43/43 identical, adapter 16/16,
  canary 10/10).
* **`plz/pls` + auto/farm data pass** — fixed the live prompts, cost three held-out base rows
  (test exact 84.6% → 61.5%). Rejected and reverted; the real prompt strings stay out of train.
* **TRM-style recursion as the trunk** — tested twice on this corpus, no measurable gain, and the
  reward would be latency on a 4 s page budget. Kept only as the halting/escalation idea.
* **A flat 0.25M word-level BiGRU parser** — train ~100%, held-out 30–45%: overfit. Retained as a
  negative baseline only.
* **Pooled (whole-utterance) token adapter** — faster, but tags got worse; the accepted shape is
  the user-span-only adapter plus cached trunk hidden states.
* **100–135M dialog expert** — rejected by the user; the budget is not the bottleneck.

## 7. Parameter budget

| Piece | Params | Active per decision |
|---|---|---|
| frozen domain trunk | 24.9M | 24.9M (text rows only) |
| command heads (+ adapter) | 0.29M + 0.29M | one branch, routed |
| **foresight net (target)** | ≤2.0M | ≤2.0M |
| combat actor | ~0.2M | 0.2M |
| **total shared** | **≤30M** | **≤10M active** for the non-text path |

The trunk is only paid for on text (a prompt or NPC text). A pure state→tool decision never runs
it, which is what keeps 12 instances per host inside the p95 budget.

## 8. Harness integrity findings (2026-10-27)

Two defects found while building the foresight dataset. Both would have quietly invalidated every
outcome number the plan depends on, and neither was visible from the artifacts on disk.

1. **The counterfactual factory was generating zero rows.** `sim/tool_choice.py` emitted
   `{"rows": 0}` and exited **0**, so the corpus on disk kept looking like the source of truth.
   Cause: G31 only lets the planner start a quest the running client reported, and the client
   reports an offer through `dialog.start_quest` (G32). The harness reported nothing — its
   observations had no `dialog` block at all — so every curriculum id was skipped as "never
   reported" and `QuestPlanner.choose()` returned `None` on step 1 of every episode.
   Fix (sim side only; **the guard was not weakened**): `sim/world.py` now publishes the offer a
   real client would — `offer_here()` returns the quest an NPC on the current map offers,
   `observe()` emits `{"open": true, "start_quest": id, "npc_id": …}` (and `{"open": false}` when
   nothing is open, the shape the live page actually sends), and `place_at()` lets an episode start
   where an offer exists. `sim/tool_choice.py` places the character on a start map matching the
   episode's focused objective type. `_parallel()` now fails loudly on a zero-row run and keeps
   worker stdout instead of `DEVNULL`, which is what made this invisible for five days.
   Measured after the fix: `--episodes 4 --steps 90 --noise --travel-cost` → **139 rows**,
   tool mix `quest.advance 28.6 / nav.goto 27.3 / fight.join 22.1 / farm.start 19.5`.
   Now gated by `test_counterfactual_factory_alive`, which runs the real generator and asserts
   rows > 0, features == the live width, and at least one row of the focused objective type.

2. **`QuestPlanner.focus` meant two different things, and the offer guard read the wrong one.**
   `_focus_order`/`_by_type` treat `focus` as an **objective type** (0–17 — the only caller is the
   harness, `--focus`); the offer guard compared it against a **quest id**. So with any focus set,
   `focus == offered` was essentially never true, every offer looked forbidden, and the episode
   looped on `state.snapshot` forever. Live runs were unaffected because the autopilot never sets
   `focus` (named targets come from the goal text via `named`), which is why no live log showed it.
   Fix: the guard now consults `focus` only when it is **not** a member of
   `agent.features.OBJECTIVE_TYPES` — i.e. when it can only be a quest id — so a prompt that names
   quest 3841 still refuses an unrelated offer (the existing fidelity check passes) while a
   curriculum type no longer blocks every accept.

3. **Feature-width drift between corpus and runtime.** The `data/tool_sft` corpus on disk was
   written on 2026-10-22 with **41** features; `objective_features` now emits **42**. Any model
   trained across that seam would have reported a meaningless sim-to-live number, so
   `ai/train_foresight.py` infers the width, refuses a ragged file, and refuses a stale width with
   the exact regeneration command. `agent/features.py`'s docstring no longer hard-codes a count.
   The regenerated corpus is `data/tool_sft_v2/` (the Oct-22 corpus is kept, not deleted).

**Consequence for the plan:** step 2 below (train the foresight net) is only meaningful on a corpus
the current harness can actually produce. Finding 1 was the blocker, and the whole
outcome-labelled data factory now has its own gate.

## 9. Foresight v1 — measured (2026-10-27)

`ai/train_foresight.py` + `agent/foresight.py`, **31,865 params**, 51 s of CPU training, exported
as JSON arrays and scored with numpy (no torch, no ONNX runtime, no service).

**On the harness, held-out episodes** (`data/tool_sft_v2`, 17,039 train / 2,349 val states):

| Metric | Model | Teacher (the rules) |
|---|---|---|
| `gain_capture` (share of the best fork's progress) | **0.9679** | 0.9660 |
| top-1 agreement with the fork winner | 0.8641 | — |
| AUC of "the world moves" per candidate | 0.7489 | — |
| flat states it still calls promising | 0 (true flats: 282) | — |

Numpy-vs-torch parity on the exported artifact: gain max diff **0.0**, move-logit diff **1.2e-7**.
So a 32K-parameter candidate-conditioned net reproduces the harness's counterfactual ranking at
the teacher's own level — component 4's "replace a heuristic with a trained head" is demonstrated
offline, on outcome labels, at 0.1% of the parameter budget.

**On the real game, the same model fails, and the reason is measured, not guessed:**
`live_move_auc 0.5`, ECE 0.92, predicted gains in the tens of thousands. Two causes, both now
fixed and both instructive:

1. **A zero-variance normalisation bug.** `objective_features` already scales its outputs, and on a
   20–100 curriculum several features are *constant* (`std = 0`). The trainer's z-score divided by
   that zero, turning constants into inputs of magnitude 10⁶. `std < 1e-3` is now treated as 1.0,
   and the skew meter ignores constant columns.
2. **The harness does not cover the live state distribution.** The corpus ran levels 20–100 while
   the recorded live character is level 3 with 0 kamas, so 100% of real states fell outside the
   range the model was normalised on. The KB contains 39 quests at `level_min = 1`, so the corpus
   is being regenerated across `--levels 1 3 5 10 20 40 60 80 100`
   (`data/tool_sft_v3`) and the transfer is re-measured against it.

`ai/train_foresight.py` now prints `feature_skew` on every run — rows/cells outside the harness
range, `max_abs_z`, `p99_abs_z` and the worst features — because a transfer metric computed on
out-of-range inputs is theatre. **No live claim is made for foresight v1**; the honest status is
"matches the teacher inside the harness, does not yet transfer", and the next number to read is
whether the low-level corpus closes that gap.

### 9.1 Capacity or coverage — settled on Kaggle GPU (2026-10-27, `tajdinenizar/dofus-foresight-capacity-sweep`)

The two explanations for §9's failed transfer implied opposite work, so they were measured against
each other with matched compute (4000 steps, batch 1024, lr 3e-4, seed 0, same data order — only the
width changes):

| hidden | params | harness `capture_model` | harness `move_auc` | live `auc_moved` | live `auc_progress` | live `ece_moved` |
|---|---|---|---|---|---|---|
| 128 | 31,865 | 0.9779 | 0.9149 | **0.4125** | 0.5903 | 0.8238 |
| 512 | 321,401 | 0.9781 | 0.8909 | **0.2958** | 0.6330 | 0.8179 |
| 2048 | 4,428,665 | 0.9813 | 0.8803 | **0.4299** | 0.6729 | 0.7830 |

Read across the rows, not down a column. **139x the parameters move harness capture by +0.0034 and
make harness move-AUC *worse***, while live move-AUC stays under chance in every arm (base rate
0.1756; top-20% precision 0.01 / 0.03 / 0.07). Capacity is therefore not the bottleneck — **coverage
is**, exactly as `feature_skew` said (100% of real rows outside the harness range). The one honest
counter-signal: `auc_progress` rises monotonically with width (0.590 → 0.673), so if a component is
worth scaling later it is the quest-progress head, and it is the scarcest label in the corpus (27
positives corpus-wide, 2.79% base rate) — that combination is a warning against reading the rise as a
win, not evidence for one.

**Decision:** no larger foresight net. The next unit of work is the *state distribution* — a corpus
built across low-level characters and the client's own observed values — plus the two heads that real
labels can already train (move AUC 0.685 on unseen sessions, `ai/train_move_head.py`). A model that
beats chance on harness forks and loses to chance on the world is not a weak model; it is a model of a
different world.

### 9.2 What the coverage gap actually is (measured against `data/tool_sft_v3`, 2026-10-27)

Regenerating the corpus across `--levels 1 3 5 10 20 40 60 80 100` did **not** close it: re-running
`feature_skew` on 366 real logged states (92% of the exported live rows carry a 42-wide vector — the
rest are pairs the builder could not featurise) still reports `rows_outside_harness_range_pct 100.0`,
`cells_outside_range_pct 11.0`, `max_abs_z 6.0`. The reason is visible per column, and it is not the
character's level:

| feature | harness range | live range | verdict |
|---|---|---|---|
| `quest_level` | 0.005 – 0.500 | **0.000 only** | the harness never simulates a character with no quest accepted |
| `kamas` | 0.500 – 1.000 | **0.000 – 0.042** | the harness never simulates a poor character; every synthetic run starts funded |
| `n_params` | 0.333 – 0.667 | 0.000 – 0.667 | the harness never produces a zero-parameter objective (the tutorial's free-form ones) |
| `active_count` | 0.200 – 1.000 | 0.200 – 0.400 | partly covered |
| `last_tool` | 0.059 – 0.588 | 0.000 – 0.824 | live is wider than the harness |
| `level` | 0.005 – 0.500 | 0.005 – 0.090 | **covered** — the level flag worked, it just was not the gap |

So the missing diversity is in the **initial state the harness seeds** (wallet, accepted-quest state,
objective parameter count), not in the level ladder — which is why `--levels` moved `level` and left
the other three untouched. The data work is therefore: let `sim/world.py` start a character with 0
kamas, no accepted quest and zero-parameter objectives, then regenerate and re-read this table. Until
that table reads `rows_outside_harness_range_pct` near 0, a live transfer number from a harness-trained
score is extrapolation, and §5's gate 2 must not be claimed.

**Progress 2026-10-28.** `World(start_kamas=0, random_start_map=True)` and
`sim/tool_choice.py --kamas 0 --random-start-map` can now seed poor, low-level states. A
six-episode sample (`data/tool_sft_lowkamas`) covers `kamas 0.0` and `level 0.005–0.025`
(the old harness was `0.5–1.0` and `0.005–0.5`). Two cells remain uncovered:
`quest_level 0.0` (a no-offer episode yields 0 rows because the planner has no legal action
before a quest is offered) and `n_params 0.0` (the KB has no zero-parameter objective). Neither
is faked; the first real tutorial state remains the source. The corpus must be regenerated after
the client-data/No-AI licence question is resolved, not from a new DofusDB sync.

### 9.3 Tool ensemble — model stacking measured (2026-10-27)

`ai/train_stacker.py` stacks the two learned tool bases: the 49K ONNX tool policy
(`policy_logp`, within-state rank) and the 31K foresight net (`gain_z`, `gain_rank`,
`move_logit`, `curve_z`). Four K-means state-cluster distances are appended as context so the
meta-model can learn different blends for different regions of the game. Three meta-learners are
fitted on identical inputs: a linear listwise stack, a depth-5 CART (23 leaves), and a 40-round
gradient-boosted tree forest. The artifact is `models/tool_stack.json`, scored by
`agent/ensemble.py` with pure Python.

**Whole-episode, 5-fold cross-validation on `data/tool_sft_v3/val` (1,385–1,500 states/fold,
107 episodes):**

| ranker | gain capture | top-1 vs fork winner |
|---|---|---|
| tool policy (base) | 0.97734 | 0.90591 |
| foresight (base) | 0.97696 | 0.90429 |
| linear stack | 0.97663 | 0.90407 |
| gradient boosting (307 leaves) | 0.97735 | 0.90567 |
| **decision tree (23 leaves)** | **0.97905** | **0.91137** |

The tree is ahead of both bases in **3/5 folds**, gain capture **+0.00171**, top-1 **+0.00546**.
That passes the activation gate (mean capture ≥ best base + 0.0005, top-1 no worse by 0.005, and
a majority of folds ahead). `--policy planner --ensemble act` uses it; `shadow` logs the choice,
`off` disables it. The deterministic planner still fills arguments and owns every safety guard.
Linear and boosting are kept in the same artifact/report because boosting is *not* the winner on
this small tabular problem; capacity is not the issue, state context was.

Caveat: this is still harness-outcome evidence. The live transition corpus has only 1,827 actions
(14.0% moved, 1.5% progressed), so §5 gate 2 remains unclaimed; the stack improves which action the
simulator prefers, not a proven live win rate. The telemetry idea in the external harvester is
already implemented by the page recorder (`kind: send/recv`, live observations → transitions), and
any packet dump must be field-scrubbed before it is written to disk because login traffic carries
credentials.

### 9.4 Live tutorial — the two AI blockers found (2026-10-27)

A six-step live run on instance 1 exposed two bugs that no harness metric could show:

1. **The monster-group list was silently empty.** `_listMonsterGroups` referenced `si` without
   defining it while building the row, so the first matched group threw and the observation
   returned `groups: []` with `groups_scan.matched: 1`. The planner then fell back to talking to
   map NPCs. Fix: bind `dofus.local`'s actor `staticInfos` before the row. The next live
   observation returned `group_id -596`, monster **4192**, level 1, cell 303.
2. **Utility spells hid all damage.** `greedy_rotation` ranked Roulette/Clubs above damaging spells
   on the level-1 Ecaflip sheet, spent all 6 AP, and produced `damage_per_turn = 0`. The fight
   guard therefore refused with `player.no_damaging_spell`. Offensive and healing rotations are now
   priced separately; the same live sheet reports **65.8 damage/turn**, water.

After both fixes the same bounded run issued `fight.join` on the Merkaptan group, advanced the
quest through the NPC reply, changed maps, and moved the character from level 1 / 3 objectives to
**level 2 / 6 objectives**, with keep-alive `present: true` and no disconnect. The active 23-leaf
tree stack was recorded on the steps (`ensemble.stack`, `ensemble.policy`, `ensemble.chosen`).

Gates: `tools/test_group_scan.js` now executes the full lister (not only the census head) and
requires a real row; the live loop regression also covers the ensemble field.

## 10. Build order

1. `tools/build_live_transitions.py` — real logs → transitions (§4.1). *(shipped, gated)*
2. `ai/train_foresight.py` — candidate-conditioned heads on `data/tool_sft`, evaluated on
   transitions. Gate 1 then gate 2. *(shipped and measured; gate 2 refused — §9, §9.1)*
2b. **`sim/world.py` initial-state diversity** (§9.2): seed 0-kamas, no-accepted-quest and
   zero-parameter-objective characters, regenerate, and re-read the skew table. This is the current
   blocker for any sim-to-live claim — bigger nets do not fix it (31K → 4.4M params, live AUC still
   under chance).
2c. **Unattended live sessions** (§11) so outcome labels accumulate without a human in the loop.
3. Tool ensemble (`agent/ensemble.py`, `ai/train_stacker.py`) — K-means context + decision-tree
   stack over policy/foresight, cross-validation gated, active only under `--policy planner`;
   `--ensemble off|shadow|act` and fail-open. *(shipped, §9.3)*
4. Escalation head + macro/micro split, once the curves are calibrated.
5. First rule-removal candidate: **tool choice under a quest objective** — only after gates 1–3.
6. ONNX/INT8 export of the foresight net through `ai/export_command_onnx.py`'s pattern, and the
   packaged audit, so the release contains it.

## 11. Unattended operation (measured 2026-10-27)

An outcome-labelled agent is only trainable if somebody can produce sessions. That somebody used
to be a person, so the loop stalled on logins. Three failures had to be measured and closed first;
none of them was the model.

**1. `dofus.local says: Server busy` was a dead egress, not a server problem.** `network.ini` says
`mode=custom` with `upstream=socks5://127.0.0.1:40000`; `FarmEngine.EnsureTunnel` returned early
for any mode that was not `wireproxy`/`warp`/`auto`, so in `custom` **nothing ever bound 40000**.
The relay then refused direct dials (correct, `fail_closed=1`), every asset 502'd, and each booted
client raised a modal alert that freezes the injected thread — six instances, six dialogs, no
directives answered. The relay log is unambiguous: `[UPSTREAM] dial to … via
socks5://127.0.0.1:40000 failed … actively refused`. Two code facts came with it:
`start-network.bat` / `manage-proxy.bat` called four tools (`ai/pool_runner.py`, `pool_status.py`,
`test_egress.py`, `score_proxies.py`) that had been deleted while still referenced, so the operator
path could not even report the failure; and WARP's UDP path is **intermittent** on this line rather
than dead — with wireproxy running, the relay fetched the cache-exempt `primus.js` through it, and
`manage-proxy.bat status` measured 12 of 26 candidates alive. Fix: `EnsureTunnel` now brings up the
userspace tunnel when `custom` names our own tunnel port (same exit, no identity change), the four
pool tools are restored, and the release preflight refuses to launch into an unproven path —
proven means an asset the relay cannot serve from its disk cache, not merely an open port.

**2. Launching is verified, not assumed.** UIA reads took *the first window belonging to the
process*, and the game clients belong to the same process as the panel: with four clients open the
panel's controls disappeared from the tree, and a click that had worked was reported unverified.
`elements()` now selects the window whose title is not an instance, `focus_host()` raises
`"farm manager"` rather than anything containing `farm` (every client title ends in `Dofus Farm`),
and a running-count read re-raises the host first, because a minimised window reports no rectangles
at all. Measured after the fix: `0 / 4 Running → Launch All → verified (2 / 4 and climbing)`.

**3. The login screen is driven through the game's own handler.** A synthetic DOM click on
`.linkGuest` returns ok and changes nothing — the UI framework ignores `isTrusted:false` events, so
the tool used to lie. `gui.loginScreen._playGuestBtn` registers a `tap` listener, so
`session.login` emits **that** event with an event-shaped argument and reports which path ran
(`pressed`, `emit_error`), plus the visible tree and the `tap` handler source so a future reader can
see what a press means. It never writes credentials, never calls `dofus.setCredentials`/`dofus.login`
and never opens an OAuth popup; `target:"guest"` is the path the operator authorised because a saved
Google session needs a browser popup no script may press. The runner calls it only while the page is
`connected` and not yet `in_world`, at most every 25 s, and stops when the world arrives — so an
unattended round is now: preflight → verified launch → press the game's own button → run.

**Gate integrity, same day.** The mutation driver had silently decayed: a shadowed `REPLAY` constant
re-pointed eight `policy_over_record_boards.py` mutations at `command_replay.py` (reported as SKIP,
which reads like a pass), seventeen anchors no longer matched the source they were written against,
one anchor matched four times, and `test_auto_live_guards` was never in the snippet — so four
mutations were "caught" only because the section crashed. All 349 anchors now resolve exactly once,
verified statically before any run, and the driver refuses to be imported (`__name__ != "__main__"`),
because importing it writes broken code into the working tree: a killed run had left
`if False and client_absent(step_error)` in `agent/autopilot.py`, disabling the client-absent
recovery, and it sat there unnoticed until this pass. Gate today: **922 checks, 0 failed**, plus
`tools/test_session_login.js` in the delegated JS suite.

### 11.1 The client version gate (measured 2026-10-27)

`dofus.local says: A new version is available` is not a dialog the host raises. It is the client's
own reaction to being refused by the login server: `ui.popup.accessDenied.badVersion` and the
`INCOMPATIBLE_APP_VERSION` / `INCOMPATIBLE_BUILD_VERSION` reasons in `build/script.js`, with the
`ProtocolRequired` handshake logging `requiredVersion` / `currentVersion` on the socket. The version
the client announces comes from the bundle itself -- `window.buildVersion="1.74.9"` is a literal
inside `build/script.js`, not a host string -- so the popup means exactly one thing: **the local
bundle is older than the one the server publishes**.

The updater that exists to prevent that had never run successfully, for two reasons, both measured
rather than inferred:

1. **A reused `WebClient` loses its headers.** `desktop/clientbundle.cs` used one client for the
   manifest and then every file. .NET applies `wc.Headers[...]` to the *first* request and empties
   the collection afterwards, so the second request goes out with `UserAgent = (null)` -- visible by
   building both requests and printing them, no network needed. The origin answers **403**
   (`server=awselb/2.0`) to a request without the mobile UA and **200** to the identical URL with
   it, which is why the log read `manifest ok / build/script.js failed: (403)` fifty-three times and
   `nothing downloaded; keeping the local bundle` after it. An earlier note in this file attributed
   those 403s to the dead egress; the egress caused the *timeout* lines, not these.
2. **`Validate(staging)` demanded `index.html`.** A manifest update carries only `build/*`, so the
   staged tree never contains a page and every successful download was rejected before adoption.
   `client_version.json` did not exist on this machine at all.

The update path is now: UA set inside `GetWebRequest` (so it cannot be lost), staged validation that
judges only the staged files, adoption refused when no core patch matched, one update transaction at
a time (`lock (_gate)`) because `Launch All` fires one updater per instance at the same `script.js`,
and the asset `?v=` re-stamped *after* the swap so the page stops asking for the bundle it replaced.
Live evidence on 2026-10-27: `patched staged bundle: 23/23 rules, 1/1 core`, `2 file(s) updated and
patched`, `asset URLs re-stamped: ?v=5e0ce3abcf`, `client_version.json` written, and the running
client's footer changed from `Build v1.74.9` to `Build v1.74.10` -- after which the same guest press
that had been refused reached the world.

**Rule for the future:** a 403 from Ankama is evidence about *our request* until proven otherwise.
Reproduce it with two different clients (curl and .NET, fresh and reused) before blaming the
network, and never read `nothing downloaded` as "the server has nothing".

### 11.2 Multi-instance addressing (2026-10-27)

One directive used to reach exactly one client: the AI panel could name one slot, and its
"All instances" entry meant "whichever window the bridge happens to list first" -- a label that
promised a fan-out and delivered a single answer. `desktop/agentbridge.cs` now resolves
`target_instance` as one id, a comma-separated list, or `all` (the windows that are *actually* open,
from `GetActiveInstanceIds()`), and `RunOne(...)` answers per instance: one addressee keeps the
envelope shape it always had, several get `{"targets": "...", "results": [...]}`. The stale-name
fallback ("dofus-05 is not active; using dofus-01") is limited to a single addressee, because
substituting another window under a named instance's key mislabels the answer.

`desktop/aipanel.cs` selects through a checklist dialog (`TargetSlots`, persisted as
`target_slots=1,3`; "All instances" is exclusive), and the loop starts **one autopilot child per
selected instance**, each with its own log (`autopilot.<instance>.jsonl`) because two loops
appending to one JSONL interleave two characters' rows. Idle training and the UI flip wait for the
last child, not the first: the quickest loop finishing would train on half a run and show "idle"
while the others were still playing.

Measured live on 2026-10-27 with three clients in-world and one at the login screen:
`target_instance=all` -> 4 envelopes (Bruuh 147062786, Myaixd 147063812, Brruh 147062786, dofus-05
not in world); `dofus-01,dofus-02` -> 2; `target_slot=3` -> the old single shape, unchanged.

### 11.3 "Server busy" is a dead login pin, not a busy server (measured 2026-10-27)

Four clients showed `dofus.local says Server busy` over a black page while the relay log said:

```
pinned egress ... failed for dt-proxy-production-login.ankama-games.com:443 with
8 live game connection(s); refusing to move the exit IP mid-session (i/o timeout)
```

The page's `config.json` and `primus.js` fetches were registered as "live game
connections" because `isGameDestination()` covers every Ankama host. Those fetches then
kept the dead egress pinned for the 60 s idle window. The client's fatal-boot path calls
`window.alert("Server busy")`, which blocks its own JS thread, so the retry wrapper could
not run. The replacement exit was healthy (the same local SOCKS answered an HTTP 200 to
the login host in 1.28 s) but the relay refused to use it.

Three changes make this permanent:

1. Only the real socket (`dofus.local` / `dofus.localhost`) counts as a live game
   session. Page HTTPS traffic still uses the pinned exit but cannot preserve it.
2. Liveness requires bytes **received from the server**, not a client write into a
   half-open socket. A stalled login handshake therefore cannot fake a session.
3. If every hop is in the 30 s death cooldown, `pinCandidates` retries the chain rather
   than returning `no upstream configured`. The page also suppresses only the exact
   "Server busy" alert and reloads after 1.5 s, with a three-reload budget, so the
   relay's failover can complete.

Gated by `TestGameSessionDestinationSeparatesPageTraffic`,
`TestLoginFetchDoesNotPreserveThePin`, `TestPinCandidatesNeverGivesUpAfterCooldowns` and
`tools/test_boot_alert.js`.
