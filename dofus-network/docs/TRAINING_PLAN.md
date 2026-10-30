# TRAINING_PLAN — one shared learned brain (≤30M total / ≤10M active), the harness as its gym

**User direction (2026-10-27).** The pipeline is tools and harness; the intelligence must be
learned. Max **30M total shared params / 10M active** (the earlier 70M and 400MB ceilings were
withdrawn as unnecessary); it is **shared across instances**,
with **MoE / per-instance agents** handling each client while the shared model does the thinking.
Kaggle **GPU is free** for training. Train on the harness **first**, then remove the hardcoded
decisions **component by component** — never before a measured win. End state: unattended Full
Auto that can do the whole game — quests, mobs, dungeons — and keeps adapting.

## Refined direction v2 (2026-10-27) — components 1–4, no fifth expert

The user chose components **1–4** and rejected the 100–135M dialog expert. The budget is no longer
the point: **TRM-style recursive, weight-shared computation** is the hypothesis to test (“bigger is
not always better”), the language model only needs **command/entity understanding** (not fluency),
and the loop must **learn online from the game** (NPCs, quests, dialogs, fights) instead of only
imitating a teacher.

**The clue.** The game is a self-labeling environment: `QuestObjectiveValidatedMessage`,
`QuestStepValidatedMessage`, quest completion, XP/loot, dialog close/advance and fight results are
outcome labels produced by the server. The 49K tool policy already matches the 85.7% teacher, so the
missing intelligence is not width — it is **outcome-driven adaptation** over a small, fast reasoner.
TRM is the architectural hint: share weights across a variable number of recursive steps and spend
compute only when the decision is hard, instead of paying for a dense 100M forward on every step.

**Deployed shape (target):**
- **Reflex core** ≤5M, recursive (TRM-style, weight-shared depth, halting head): tool + structured
  args + verifier. Adaptive steps: 1–2 for routine steps, 8–16 for dialogs/bosses/uncertainty;
  latency bounded per decision.
- **Command/entity encoder** 3–10M: intent + slots over French/English commands and NPC/quest text
  (quest id, NPC, item, map, verb). Not a fluent LM; the output is a structured parse.
- **Combat actor** ~0.2M + solver: unchanged; mechanics and live fights are the gate.
- **Per-instance adapters** 0.5–2M each, chosen by a tiny router: the shared core thinks; adapters
  specialize for the instance/class/quest chain. Only adapters update online; the shared core is
  retrained offline.
- **No 100–135M expert.** Total shared ≤30M, active ≤10M, INT8; the 400MB budget is not needed for
  v2 and is reserved for replay/embeddings only if a benchmark justifies it.

**Online learning (the part the earlier plan lacked).** Shadow mode → replay buffer → guarded
updates:
1. The model proposes, the harness/rules act; every `(state, proposal, rule, outcome)` is logged.
2. Outcomes come from the game itself (validation/step/quest, XP/loot, dialog end, fight result);
   reads never count as success.
3. Per-instance adapters update from a replay buffer with a small learning rate and a rollback
   guard; the shared core is distilled offline on Kaggle from the accumulated buffer.
4. Active learning: low confidence or rule disagreement triggers a read/measurement, not a guess.
5. Safety/legality/recovery stay harness checks; online updates can only rank legal actions.

## Decision: base model

The 24.9M `encoder30_v15` is the **teacher/labeler and fallback**, not the deployed shape. Distill it
(and the 49K tool policy) into the recursive core + command encoder; benchmark TRM-style recursion
against a small transformer/MLP on the same held-out outcomes and keep the best per component. No
generic >30M pretrained model; the domain checkpoint has the better prior. Deploy INT8 and measure
p50/p95 with 12 instances sharing the core.

## Who decides what (target end state)

| Decision | Today | Target | Remove the rule when |
|---|---|---|---|
| tool choice | 49K policy (opt-in) | shared trunk head | beats teacher on held-out **outcome** labels + shadow session |
| tool arguments | rules (`args_for_tool`) | structured args head | legal under the schema, no worse than rules, shadow-clean |
| dialog reply | token-overlap heuristic | shared trunk head | beats the ranker on real reply→outcome labels |
| verifier verdict | rules (+ gated head) | outcome-trained head | beats rule baseline on held-out real labels, ECE sane |
| fight policy | PPO actor / page fallback | PPO actor + shared high-level plan | compute-matched, side-swapped, live fight win |
| safety, legality, recovery, knowledge | tools/harness | **stays tools** | never — the model proposes, the harness gates |

## Data factory

1. **Counterfactual tool/args corpus** (`sim/tool_choice.py`): fork the world per candidate tool and
   label by objective gain — an outcome label, not "what the teacher did". Episode-held-out splits.
2. **Sim trajectories** (`sim/run.py`, `sim/rollout.py`): quests, maps, groups, noise/stale reads,
   travel cost, dungeons; broad curricula for every quest/mob type.
3. **Live logs**: `trajectories/*.jsonl`, `tools/dialog_labels.py`, `fight_outcomes.jsonl`,
   `observed_quests.jsonl`, step rows; the only source of real outcomes.
4. **Client data at runtime** (licence-clean): active quest graph, monster/spell/dungeon tables the
   client loads; no DofusDB re-sync (its licence forbids AI use).
5. **Combat self-play** (existing JAX env): GPU, 12/6/6 normal builds, no Temporix.

## Phases and gates

0. **Baseline** — measure the exact checkpoints (49K policy, teacher, 24.9M encoder) on held-out
   episodes and latency; record numbers, no claims from memory.
1. **Data** — generate ≥100k counterfactual rows + an episode-held-out split; verify label variance
   across all 21 tools; freeze the split before training.
2. **Shared trunk SFT** — fusing `_observation_text` with numeric objective features; heads for tool
   + structured args. Gate: held-out **outcome** accuracy above the teacher, params ≤30M total / ≤10M active, p95 within
   the 4 s page budget.
3. **Dialog** — train reply selection on real reply→outcome labels; gate above the ranker, then a
   shadow session.
4. **Verifier** — labels from action→next-state deltas; gate above the rule baseline with honest
   ECE; only then can the GUI load it.
5. **Combat** — PPO/PBT/self-play, compute-matched and side-swapped; first live fight win is the gate.
6. **MoE / per-instance agents** — adapters/LoRA per instance, shared trunk; measure interference
   and latency with 12 instances before enabling.
7. **Latent context bus (after the core works)** — encode the structured observation + command once
   into a latent/KV context shared by the reflex, dialog and verifier heads, and keep a per-instance
   KV cache across steps. Cross-instance sharing (a shared quest/dungeon/world latent bus) comes
   after a single-instance benchmark. It must be bounded, authenticated, decoded to a short audit
   gist and validated; it never bypasses legality/safety/recovery. Motivation: dense latent
   communication across heterogeneous agents (Chen et al., arXiv 2606.13594; 4B–14B models, 2–3×
   lower compute than text) and its attack surface (Huzaifa et al., arXiv 2609.39788: latent
   channels bypass text filters; attack compliance 27.9%→76.9%).
8. **Shadow mode** — the model proposes, rules gate; log disagreements, outcomes and latency;
   replace one component at a time and keep the fallback.
9. **Continual learning** — live outcome buffer → periodic fine-tune → nightly held-out eval;
   knowledge stays in the pipeline so game content changes do not require retraining.

## Pre-registered first worker, and the calibration it must beat (2026-10-30)

**The worker.** One shared state encoder (the existing relational `mechanism_vector` block) feeding
per-call-form outcome heads, where a form is the promise the call makes: `nav.goto:step`,
`nav.goto:at`, `quest.talk`, `quest.reply`, `quest.accept`, `tap.interactive`, `inv.equip`,
`fight.join`, `farm.start`, `safe.stop`. It predicts `claim_met` (label_version 3: a refused call is
a miss) and only ever ranks legal actions; the rule still acts when the head is absent or unsure.

**Why a count calibration had to be measured first.** The loop's gate is a counter:
`repeat_streak_norm >= 2` vetoes the third identical attempt. A learned head is only worth building
if it beats the best *count* gate at the same veto budget — the counter is cheap, interpretable and
already shipping in shadow. `python tools/live_outcome_probe.py --calibration --data <fold>` fits
p(met | form, streak bucket) with a Beta prior on train only, chooses the veto threshold on train,
and scores on the frozen fold at the counter's own number of vetoes, so no gate can win by vetoing
more calls. Constants: `PROMOTION_RETENTION = 0.95`, `PROMOTION_MIN_COUNTER_VETOES = 10`,
`PROMOTION_DELTA_POINTS = 0.05` in the probe.

**Measured 2026-10-30 on the rebuilt v3 corpus (frozen folds, segment-clustered paired CIs).**

| fold | rows train/test | counter: vetoed, kept met, retained | calibrated at the counter's budget | delta CI |
|---|---|---|---|---|
| temporal | 3,644 / 1,031 (base 0.577) | 168 (16%), 0.678, 0.983 | kept 0.611, retained 0.886 | (−0.121, 0.012) |
| hash (`transitions_v2`) | 3,568 / 709 (base 0.271) | 280 (40%), 0.434, 0.969 | kept 0.431, retained 0.964 | (−0.038, 0.044) |

Per form on the temporal fold: `nav.goto:step` test is 165/178 met (93%) after 17% in train — the
fold's nav mix shifted, and no train-fitted threshold is usable; its calibration ECE is 0.184
(hash fold 0.031). `nav.goto:at` is 0/12 met in train and 238/336 in test (ECE 0.676): a form that
did not exist when the gate was fitted. `quest.talk` counter vetoes 5 calls of which 2 met while the
calibrated gate vetoes 25 of which 16 met — no gain. `quest.reply` is the one form where the
calibrated gate is better at its own threshold (temporal kept 0.67 vs 0.55, CI 0.093..0.143, but
retains 0.896 < 0.95; hash kept 0.61 vs 0.52, CI 0.062..0.119), yet its counter budget is 9 and 8
vetoes, below the pre-registered comparison floor of 10.

**Result: the count calibration does not beat the counter at equal budget on either fold.** Its
apparent wins at its own threshold come from vetoing 1.5–3.7× more calls. The counter stays the
operating rule; nothing new is promoted, and no worker is trained on this corpus.

**Then the only place left to win: inside the counter's kept calls (`--worker`, measured 2026-10-30).**
The state block (the 24 relational mechanism features, streaks excluded) was fitted per form on
train and scored on the frozen fold. Headroom is AUC on the rows the rule keeps; the extension layer
removes the 5% and 10% lowest-scored kept calls on top of the counter, and is judged by
`_extension_ok`: removed set ≤10% successes, ≥5 removals, kept AUC ≥0.55, and a paired segment CI
excluding 0. Temporal fold: `quest.talk` kept AUC **0.472**, `quest.reply` **0.398**,
`nav.goto:step` 0.645 but its kept set is 175 rows with 165 met — the extensions remove calls that
**100% met** (CI −0.056..0.0 and −0.094..0.0). `quest.other` shows 0.707 kept AUC on 43 kept rows
with 2 positives, below any evidence floor. Hash fold: `quest.talk` looks promotable (5% extension
removes 5 calls, 0 met, kept 0.443→0.465, CI 0.004..0.052; 10% removes 10, 0 met, CI 0.013..0.098),
but the temporal fold inverts it and `nav.goto:at`/`:step` extensions remove half to three-quarters
successes. **No form passes on the frozen fold and the rolling cuts, so no worker ships** — the same
within-period pattern that killed the encoder route (G57). The state features simply do not order
outcomes out of time; more architecture cannot fix a null signal. What would change the answer:
live volume (per form ≥400 met rows across ≥3 start periods) and randomization strong enough to vary
state within a form (the epsilon arm), not capacity.

**The bar the worker must clear before it may replace anything** (on the frozen temporal fold,
then on ≥3 of the 4 rolling-origin cuts, with the rule as fallback):
1. at the counter's veto budget, kept success rate ≥ counter + 5 points, paired segment-clustered
   95% CI excluding 0 — the same arithmetic `--calibration` reports as
   `safe_at_counter_budget.delta_vs_counter`;
2. successes retained ≥ 0.95 (a gate that buys rate by discarding wins is rejected);
3. one head per form, and only forms with ≥400 `claim_met` rows over ≥3 distinct start periods are
   eligible; today's folds do not meet that for any form, so the binding constraint is **live
   data**, not capacity.
4. fail open: absent artifact, unseen form, or a non-finite score means the rule acts.

Re-run `--calibration` after every live session and after every dataset rebuild; the headline is
`safe_at_counter_budget.delta_vs_counter`, not an AUC.

## Kaggle execution

Use an interactive GPU notebook (T4×2/P100); the toolkit is synced by
`tools/sync_kaggle_toolkit.py` and is the dataset source. Checkpoints go to
`models/shared_brain/`, with params/FLOPs/p50/p95 recorded in the run manifest. TPU only if a
measured speedup justifies it. The user runs/pastes notebooks; no queued batch dependency.

**First entity-pointer Kaggle run (2026-10-27, pipeline smoke).** Dataset
`tajdinenizar/thx-05-entity-pointer` (4,017 rows; 3,885 / 45 / 87), kernel
`tajdinenizar/dofus-entity-pointer-v1`, GPU, 8 epochs, hidden 256. Metrics: top-1
**0.3778**, moved AUC **0.4257** (base rate 17.8%), moved ECE 0.259, progress AUC
**null** (zero positives in val). The upload, recursive mount lookup, GPU training and
artifact download all work; the model is not promotable. The corpus is 73% reads,
1,003 action rows, 4 progress labels, and the new tools have no live rows yet. Rebuild
`data/entity_pointer` after a live session on page build `2026-10-27s` before another
training run.

**Observed-text actions-only run (2026-10-28, not promotable).** The same corpus was
enriched with the client's own 22 observed step descriptions (4,016 of 4,017 rows carry
`observed_hint`; dataset `tajdinenizar/thx-08-entity-pointer-observed`, kernel
`tajdinenizar/dofus-entity-pointer-observed-v1`) and trained on action rows only —
**1,003 train / 28 val / 53 test**, 20 epochs, hidden 256. Result: val top-1 **0.0357**,
test top-1 **0.0377**, moved AUC **0.434 / 0.362** (below chance), moved ECE 0.29/0.41,
progress AUC **null** (0 test positives). Adding observed text did not move the
pointer; the limits are the hashed-entity scaffold and 1,003 action rows with 325 moves
and 4 progress labels. Do not rerun this table — the next entity-pointer run requires a
live corpus from `tools/collect_live_data.py` with the new tools exercised.

**Constraint corpus and cause head (2026-10-28, scale-up measured).**
`agent/constraint_corpus.py --generate --out data/constraint_synthetic.jsonl` writes
**2,600** rows (25 tools × 8 causes × 13 template families); the controller routes
**100% cause / 100% action** on it (gate floors 95% / 90%, ≥50 rows per tool).
`ai/train_cause_head.py` trains a hashed-feature MLP on 9 families/cause, calibrates
on 2 and validates on 2 **unseen families per cause**. The MLP alone scores mean **0.81 accuracy / 0.19 ECE** (range 0.7625–0.875) and
fails the ≥0.85 / <0.15 gate. The MLP + cosine-kNN blend (weight chosen on calib)
reported mean **0.855 / 0.089**, but that gate-pass claim is withdrawn: an input-level
audit found 50/400 seed-7 validation vectors identical to fit and calibration vectors.
Reloaded FP32/INT8 scores reproduce; excluding duplicates yields **292/350 = 0.8343**
accuracy. This is a diagnostic on a development set, not independent validation.
The trainer now rejects family/input overlaps or missing causes before training
and export; the existing artifacts remain untouched and unpromoted. The text
cross-product is not an applicability-checked game constraint set. Next: meaningful
precondition cases, source-weighted live failures, a frozen independent split,
then cause/repair training and regret evaluation against always-continue. No new
training run is justified by the old score alone.

**Combat-grid data gate (2026-10-27).** `python -m combat.grid_probe` over the
local corpus reports **156 boards parsed, 64 on the controlled turn, 0 with
positive AP, 0 with more than the pass plan**. The raw boards carry
`my_ap/my_mp = null` (decoded as 0), so the solver cannot produce a cast. Do not
train the 30M 560-cell combat core on this corpus. The required input is one live
fight on page build `2026-10-27s` with a real turn and AP > 0; then rerun the probe,
build the grid tensors, and train compute-matched and side-swapped.

The 30M shape is implemented in `combat/grid_model.py` (**30,766,081** params:
560 cell tokens, 16 fighter tokens, 16 plan tokens, hidden 768, heads 8) and
`combat/test_grid_model.py` passes 3/3. `combat/grid_dataset.py` refuses the
156/156 no-positive-AP corpus rather than writing a pass-only dataset. Training
starts only after `grid_probe` reports `multi_plan > 0`.

**First 30M Kaggle run (2026-10-27, synthetic distillation).** The 156 real maps
were swept over four AP/MP variants, yielding **440 actionable solver-labelled
boards (372/28/40)**; 184 variants stayed pass-only and were dropped. Kernel
`tajdinenizar/dofus-combat-grid-30m-v1`, dataset `tajdinenizar/thx-06-combat-grid`,
GPU, 4 epochs, hidden 768. Result: **val top-1 0.786, test top-1 0.60**, test CE
2.40; `model.pkl` 117 MB saved at `models/combat_grid/`. This is a
solver-distillation model, not a live combat policy: test CE exceeds val CE and
the labels are the solver's plans. It is wired as an opt-in `fight.plan
ranker=grid` (default `solver`, read-only, fails open when the artifact is absent or
the board has no positive AP). The live fight / side-swapped win gate remains.

**Next live step (one command).** With the app, tunnel and clients running on
page build `2026-10-27s`:

```bash
python tools/collect_live_data.py --instances dofus-01,dofus-02 --steps 200
```

It runs `live_ready`, refuses on any FAIL, reuses/starts the single brain daemon,
runs the selected instances with the command shadow, stops them, rebuilds
`data/entity_pointer`, and prints `combat.grid_probe`. Use `--dry-run` to inspect
the request without starting anything.

## Acceptance rules (non-negotiable)

- Held-out **real/outcome** labels, not teacher imitation alone; no 100% scores taken at face value.
- Reads are excluded from success; compute-matched comparisons; equal builds and side-swapped
  combat; 12/6/6 caps.
- A rule is removed only after the model beats it on held-out data **and** survives a live shadow
  session; safety, legality and recovery remain harness checks forever.
- **30M total / 10M active cap** and 12-instance latency measured before enabling a larger trunk.

## Immediate next actions

1. **Command encoder — measured offline, not yet live.** `ai/train_command_encoder.py` freezes
   the 24.9M domain trunk (`models/encoder30_v15`) and trains **0.29M heads** (user-span
   adapter, intent, action conditioned on intent, BIO slot spans) on `data/commands`:
   **1,619 train / 20 val / 23 test**, 13 intents. The builder holds out 26 base phrasings
   (13 val / 13 test), keeps held-out entities disjoint, and rejects eval texts and full-phrase
   near-copies from train; all four properties are gated (922 checks; `MUT_ONLY=command`
   proves the checks, 19/19 caught). Measured by `models/command_encoder30/metrics_eval.json`:
   - 26 held-out base phrasings: **84.6% exact command vs 53.8% rule**; intent **96.2% vs
     69.2%**; slot exact 84.6% vs 69.2%.
   - Per-row, including entity-swapped copies: test **intent 100% vs 56.5%**, exact **65.2% vs
     34.8%**; val exact 70% vs 35%.
   Remaining base failures: Spanish knowledge query with a held-out entity (`busca Bouftou`),
   `all` for `arrête tout` / `para todo`, and the accented `d'équipe` channel span. The flat
   0.25M word-level BiGRU overfits (train ~100%, held-out 30–45% on the earlier corpus) and is
   kept only as a negative baseline. The **rule remains the live parser**: no shadow session
   and no rule removal yet.
2. Build the **TRM-style recursive core prototype** (≤5M) and compare it with the 49K MLP and a small
   transformer on the counterfactual tool corpus: held-out outcome, params, p50/p95, recursion steps.
3. **Shadow + replay loop.** The read-only shadow is shipped: `--command-shadow auto` writes one
   `command_shadow_status` row per session and one `command_shadow` row per goal (rule parse,
   model parse, agreement, `would_override`), fail-open when torch or the artifact is absent; the
   rule still acts. A first real prompt already disagrees usefully: "fais les quêtes" is
   `quest/list` by rule and `quest/run` by the model at 0.85. `tools/command_shadow_report.py`
   runs the same comparison over the recorded autopilot logs: 7 distinct real goals / 5,721
   uses, 2 override goals (4,300 uses); the dominant "do all the quests" is rule `quest/list`
   and model `quest/run` at 0.958. The `plz` framing flipped the model to `list`. A train-only
   `plz/pls` + auto/farm pass fixed that in shadow (4 override goals / 5,709 uses) but cost
   three held-out base rows (test exact 84.6% -> 61.5%), so it was rejected and the data
   reverted; the real prompt strings stay out of train. Next: a pass that adds `plz` without
   shifting the non-quest intents. `agent/commands.py` now carries the bridge the planner needs: `intent_tool(intent, action)`
   maps the learned parse to a registered tool (quest run/advance -> quest.advance, list ->
   quest.list, farm start -> farm.start, ...); every value is gated against the tool schema.
   The replay scores it: **9/10 override groups executed the model's mapped tool**
   (`quest.advance`), while only 11/61 non-override groups executed the rule's `quest.list`.
   The model's action is the one the plan actually carries. The stall taxonomy also closes a
   false lead: the 25 stalled groups are **24 short failures + 1 fight-start, with zero read
   loops**; the 46 advanced groups are long (5,587 steps, 65% reads). The recorded stalls are
   short sessions, not read loops, so the parser is not their fix. The next step is a
   **shadow plan selection** that feeds this tool into the planner without acting, while the
   next live session diagnoses the short terminations.
   **First guarded per-instance adapter (2026-10-27).** `ai/finetune_command_adapter.py`
   fits the action head on the 16 recorded user goals with 160 base-train rehearsal rows,
   keeps only a checkpoint that passes `adapter_accepts` (no held-out intent/exact loss), and
   promotes with `heads_base.pt` for rollback. Result: user-prompt tool hits **5 -> 9** (all
   imperative quest phrasings now map to `quest.advance`), all **43 val/test rows** and a
   **10-prompt action canary** unchanged. The real prompts are adapter data now (per-instance);
   the 26 base phrasings remain the held-out gate. The acceptance guard now also includes
   `canary_accepts`: an update may not change any action-critical canary (list/advance/stop/
   sell/travel/follow), so a future per-instance pass cannot buy the user prompts with a
   collateral regression. The **shared intent head could not be
   specialised**: both intent+action and intent-only passes lost held-out base rows
   (intent-only 25/22 -> 21/18) even with 160 rehearsal rows and per-epoch guarding, so both
   were rejected. The accepted shape is a **routed per-instance adapter** (`router.json`,
   threshold 0.95): the base answers every prompt away from the 16 recorded goals, the
   fine-tuned heads answer the exemplars. Verified row-by-row: base **43/43 identical**,
   adapter **16/16**, canary **10/10**; all **7 real goals** map to the expected tool
   (6 override goals / 5,718 uses), including the first genuine routing change:
   `can you play the game ?` -> auto at 0.885. Rollback: remove `router.json` (falls back to
   the action adapter in `heads.pt`) or restore `heads_base.pt`. Then: run a live session
   with the hook, buffer `(prompt, rule, model, outcome)` rows, and guard per-instance
   adapter updates.
   The replay extractor (`tools/command_replay.py`) is shipped: **71 (session, goal) groups /
   5,721 executed steps**. The objective counter is flat (all 8,526 observations say
   `objectives_completed=0`), but the page verdict is not: **46 groups advanced, 25 stalled**.
   Of the 10 groups where the model would override the rule, **9 advanced and 9 executed
   `quest.advance`** — the parser's quest/run action matched the rule's productive plan in
   9/10 groups (observational: the rule executed it). The mode comparison is still the sharper
   boundary: on the 7 real goals the model agrees with `_goal_mode` 6/7 and would change the
   routing **0/7** at threshold; all overrides are the quest action (list -> run) inside the
   same mode. Replacing `_goal_mode` is therefore not the next lever — the parser has to feed
   the action/tool choice **inside** quest mode. `live_ready` now reports the shadow artifact and deps **ready**; the client
   bridge is the only blocker. With DofusFarm open, collect the first live rows with
   `python -m agent.autopilot --goal "do all the quests" --steps 20 --command-shadow auto`
   (or the host's equivalent), then read them with `tools/command_replay.py`.
4. First removal candidate: **tool choice** — only after phase 2 beats the teacher on held-out
   outcomes and survives a live shadow session.
