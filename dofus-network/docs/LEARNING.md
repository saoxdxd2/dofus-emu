# Learning — how outcomes become behaviour

These four documents all answer one question: **what does this system learn from, and what is
allowed to change as a result?** They were separate files, cited by each other, and a reader had
to hold four statuses in their head to answer it once.

Each section below is the original document, verbatim, with its headings demoted one level. Only
this introduction, the contents list and the section headings are new. If a section and
`CURRENT.md` disagree about what runs today, `CURRENT.md` wins.

## Contents

1. [Experience learning — every parameter moves from real outcomes](#1-experience-learning-every-parameter-moves-from-real-outcomes)
2. [Failure to supervision — kicks and refusals become labels](#2-failure-to-supervision-kicks-and-refusals-become-labels)
3. [Facts versus code — what belongs in a constant and what must be learned](#3-facts-versus-code-what-belongs-in-a-constant-and-what-must-be-learned)
4. [Latent reasoning and online weight updates](#4-latent-reasoning-and-online-weight-updates)

---


## 1. Experience learning — every parameter moves from real outcomes

> From `EXPERIENCE_LEARNING.md`, merged 2026-11-07, text unchanged.
> How outcomes become posteriors without a reward model.

The rule this document implements:

> Nothing in the agent is a constant someone guessed. Every quantity the game can
> measure is a **posterior** that moves from real outcomes, and updating it costs
> nothing — so the experience *is* the training.

That is not a slogan here. Measured on this machine: **5.5 µs per observation**,
**~1.6 KB of state per parameter**, no GPU, no replay buffer, no batch step.

---

### 1. Why conjugates instead of gradient descent

An update is a handful of float adds against sufficient statistics. There is no
training run to pay for, and no way to "forget" by training on a bad batch — the
posterior is a summary of everything observed, with exponential forgetting
expressed in **days**, not steps.

| quantity | posterior | state | updated by |
| --- | --- | --- | --- |
| drop rate | Beta(α, β) | 2 floats + n | every farm: drops / kills |
| kills per minute | Gamma(count, minutes) | 2 floats + n | every farm: kills / session length |
| any duration | log-normal (n, Σlog x, Σlog²x) | 3 floats + n | travel, craft, buy |
| market price | log-normal + level prior | 3 floats + n | every `hdv.scan` |
| strategy correction | Normal over log-ratio | 3 floats + n | every completed step |

Recency is handled by scaling the sufficient statistics by
`0.5 ** (Δt / half_life)` before each update, so week-old experience fades out of
a world where prices move and level bands shift. That is the only learning rate
in the system, and it is expressed in days:

| parameter | half-life | why |
| --- | --- | --- |
| market price | 3 days | listings churn constantly |
| kills/minute | 45 days | gear and level change slowly |
| drop rate | 180 days | game constants, effectively fixed |
| strategy correction | 21 days | meta and routes shift with patches |

Cold start is handled by shrinkage: the estimate is
`(n · observed + k · prior) / (n + k)`, so with no data you get the prior and
with lots of data you get the measurement. Nothing is ever undefined, and nothing
claims more confidence than it has — `confidence()` is reported next to every
number the planner prints.

---

### 2. What must never be learned online

Three things stay hard-coded, because an online update on them means an
irreversible action can teach the wrong lesson:

1. **Feasibility** — a level-1 character cannot fight a level-200 monster. This is
   a gate, not a cost penalty.
2. **Safety** — `safe.stop`, spend limits, session caps. Never a learned policy.
3. **The tool schema** — 28 tools (see `CURRENT.md` §7), typed args. The model does
   not get to invent one, however much evidence it thinks it has.

Everything else is fair game.

---

### 3. Time estimation is a learned correction, not a formula

The planner's deterministic estimate is systematically wrong in ways the cache
cannot express: farming takes longer than the model thinks (walking, respawns,
deaths), crafting is faster when the ingredients are already in the bag. So the
ranker (`planning/bandit.py`) learns, per (level band, strategy):

```
ratio             = log(observed_minutes / predicted_minutes)
correction        = exp(posterior mean of ratio)
corrected_minutes = predicted * correction
```

One number per arm does **calibration**. Thompson sampling over its *uncertainty*
does **exploration** — an arm nobody has measured has a wide posterior, so it
occasionally wins the draw and gets measured. Both behaviours come from the same
three floats, and `choose()` is microseconds over ≤4 branches.

Three rails keep exploration from being expensive:

| rail | default | what it prevents |
| --- | --- | --- |
| `max_ratio` | 3.0 | an arm whose *corrected* estimate is 3x worse is never sampled |
| `exploration_ratio` | 4.0 | a never-tried arm may be sampled only if its raw estimate is within 4x of the best — the price of finding out, paid once |
| `min_samples` | 5 | an arm is not trusted below 5 observations, so it keeps getting trials; without this, a systematically-biased branch can never collect the evidence to correct itself |

The third rail is the one that matters most, and it was found by testing: with
only `max_ratio`, a branch the model was 3x wrong about was filtered away forever
and never learned.

---

### 4. Proof: two closed loops

`python -m planning.learn_demo` runs a hidden world whose physics differ from the
priors, feeds every outcome back, and reports what the agent learned.

#### A. Strategy learning — discover the cheap craft

The model thinks crafting is 3x worse than buying. In truth the ingredients are
already owned, so crafting is 4x cheaper.

```
before : buy   368.6m raw   alts {'craft': 1098.3}
after  : craft 1098.3m raw, 279.7m calibrated   alts {'buy': 368.6}
-> strategy flipped from buy to craft
-> settled on the truly-best arm (craft) from episode 52 onward

    window    hit    regret  err(corr)  err(raw)
  first 20    10%      84.6       0.93      0.91
   last 20   100%       0.0       1.03      0.26

corrections learned vs truth:
  craft  learned x 0.26   truth x 0.25   converged
```

`err(raw)` is what the model would still be saying without experience: off by
4x. `err(corr)` is the calibrated estimate: off by 3%.

#### B. Parameter learning — the drop rate was a lie

The model assumes a 6.25% drop rate; the truth is 2%. Every farm estimate is
silently 3x too optimistic until the posterior moves.

```
before : farm  90.7m raw   alts {'buy': 11.1}
after  : buy   27.8m raw, 27.7m calibrated   alts {'farm': 702.3}
-> strategy flipped from farm to buy
-> settled on the truly-best arm (buy) from episode 5 onward

    window    hit    regret  err(corr)  err(raw)
  first 20    80%      74.8       1.21      1.41
   last 20   100%       0.0       1.02      1.02

drop rate  0.0625 (prior) -> 0.0286 -> 0.0230 (truth 0.0200)
farm estimate 90.7m -> 298.7m as the drop rate corrected
```

Note what actually changed: not just the choice, but the **honesty of the
number**. The farm estimate moved 90.7 → 298.7 minutes because the drop rate
prior was wrong by 3.1x, and the planner stopped recommending a farm that was
never going to pay off.

---

### 5. Two speeds, and why most experience costs no training

This is the answer to "smart enough to not cost us the training":

```
fast loop   per observation, online, O(1), 5.5 us
            posteriors + ranker corrections
            fixes ESTIMATION error  -> free, forever, no GPU

slow loop   per N episodes, offline, batched, ~2 h on a T4
            encoder fine-tune on real trajectories
            fixes LANGUAGE error    -> only when the language actually changed
```

Most errors a bot makes are estimation errors: the plan was right, the number was
wrong. Those are fixed by the fast loop at zero cost. The slow loop is only for
**language** errors — a phrasing the encoder has never seen, a new tool, a new
language pattern — and those accumulate slowly.

Trigger the slow loop on evidence, not on a schedule:

| trigger | condition | why |
| --- | --- | --- |
| volume | ≥500 verified new episodes since the last checkpoint | enough new phrasing to matter |
| drift | `gold` call accuracy drops ≥3 points on a fresh sample | the world changed |
| capability | a new tool or language was added | the encoder cannot know it |

Guardrails for the slow loop, because retraining is the one place you can lose
ground:

* **replay ratio** — mix the `core_sft_v4` corpus in at ≥50%; never train on only
  recent data (catastrophic forgetting is the default outcome).
* **gate before shipping** — the new checkpoint must beat the old one on `val`
  (frame-held-out) *and* `gold`, and must not lose more than 2 points after INT8.
* **versioned checkpoints** — keep the previous one; rollback is one file copy.

The cost accounting: the fast loop runs on every observation for free, so the
slow loop only has to run when the *language* genuinely changed — which is rare.
That is why "the experience is the training" is not a compromise here; it is the
cheaper path.

---

### 6. Data hygiene — simulated outcomes must never teach the model

An early version of this system let a `--simulate` run write fake farm results
(200 kills, 0 drops) into the production experience log. That would have taught a
false drop rate for the rest of the bot's life.

Every event now carries a `source`, and the store accepts only `{"live", "manual"}`
by default:

```python
Event(kind="beta", key="drop:31631", value=0.02, weight=900, source="live")
```

* simulated and test events are counted in `store.rejected_events`, not applied;
* the append-only log (`data/kb/experience.jsonl`) is the **source of truth**;
  `store.replay()` rebuilds every posterior from it, so a corrupted snapshot is
  recoverable and every estimate is auditable down to the observation that moved it;
* `store.summary()` prints every parameter with mean, p05/p95 and sample count —
  "why does the bot think this farm is 300 minutes" has an answer.

---

### 7. What the executor must report

The learning loop is only as good as the outcome data. Each tool result should
carry these fields (the ones the planner can already use):

| field | used for | example |
| --- | --- | --- |
| `ok` | replan trigger | `true` |
| `source` | accept/reject the observation | `"live"` |
| `data.kills` | drop rate + throughput | `47` |
| `data.drops` | drop rate | `2` |
| `data.minutes` | throughput, correction | `18.4` |
| `data.kamas` | realised kamas/minute | `2150` |
| `data.price` + `item_id` | market price posterior | `41200` |

`ScheduleManager.report()` is the single place that turns a result into
posteriors — there is exactly one learning path, so there is exactly one place to
audit.

---

### 8. Measured costs

| | value |
| --- | --- |
| update cost | **5.5 µs** per observation |
| state per parameter | ~1.6 KB (a few floats + object overhead) |
| a realistic economy (≈600 params) | **~1 MB** |
| planning stack incl. store | 6.0 MB |
| plan latency (warm) | 1.6 ms p50 |

Against the 150 MB agent budget this is noise. The interesting comparison is with
the alternative: retraining the encoder on every session costs ~2 h of T4 and
risks forgetting, to fix errors that are almost always estimation errors — which
the fast loop fixes in microseconds.

---

### 9. Gates

1. **G1** — 100 live farm sessions; drop-rate posteriors move off the prior and
   `kills_per_minute` posterior confidence exceeds 0.5 for the three main farms.
2. **G2** — the ranker's corrections on `val` episodes reduce mean absolute
   log-error of the time estimate by ≥30% versus the raw model.
3. **G3** — `store.rejected_events` stays at 0 in production (proof that no
   simulated data leaked in).
4. **G4** — the slow loop runs at most once per 500 episodes, and the new
   checkpoint beats the old one on both `val` and `gold` before shipping.
---

### Measured state, 2026-11-06: two learners, one of them connected

Rechecking this file against the code found that "online learning" names two different things, and
only one of them is in the live loop.

| learner | where | persisted | in the loop |
|---|---|---|---|
| repair-strategy stats (per cause:strategy attempts/successes) | `agent/controller.py` | `%LOCALAPPDATA%/DofusFarm/controller/<key>.json` | **yes**, on every step |
| conjugate posteriors (Beta/Gamma/log-normal/normal, EW decay) | `planning/experience.py` | `data/kb/experience.jsonl` + `posteriors.json` | **no** -- nothing constructs it |

#### The loop's learner was keyed on a uuid, so it never accumulated

`Controller` named its state file after the *session*, and a session id is a fresh uuid per run.
The directory held **803 files, 378 of them a single step**, and `load()` therefore found nothing on
almost every start: the brain was write-only. The mechanism itself is fine -- one session that ran
long enough reached 3,582 steps with 2,761 attempts on a single strategy -- so the defect was purely
the key.

Fixed: `state_key` (the instance, e.g. `dofus-04`) decides the file; the session id stays in the
payload for provenance. Proven live, two consecutive runs on the same instance:

```
run 1  session 66cb5583   incomplete:retry {n: 1, ok: 0}
run 2  session 0d91ad39   incomplete:retry {n: 2, ok: 0}   <- loaded run 1 and incremented
```

`tools/test_online_learning_wiring.py` pins it: a new session id must not mean a new brain, and two
instances must not share one.

#### The conjugate store was correct but never constructed

Two defects, both silent:

* `replay()` promises to rebuild every posterior from the log, but `_apply` returned early on an
  unknown key, so a fresh store replaying its own log rebuilt **nothing** while still counting the
  events. Fixed by stamping the prior into the event and creating the posterior on replay.
* `agent/serve.py` assembled the stack as `ScheduleManager(Planner(), Economy())`. `Economy` requires
  a `CostModel` and `Planner` requires `kb, cost, economy`, so both raised `TypeError`, and a broad
  `except` logged "schedule wiring failed" and carried on without objective context. Fixed with one
  `build_schedule_stack()` in `planning/schedule.py`, which is now the only place that knows the
  constructor order.

Consequence, unchanged by the fixes: **nothing in the live loop feeds the store.** `data/kb/` has no
`experience.jsonl` because no code path that records has ever run in production. The live loop's
labels land in `trajectories/autopilot.jsonl` and the controller's stats, not in the posteriors.

#### Live session, same day

The client was launched, the bridge came up, and the loop ran against it:

| check | result |
|---|---|
| bridge reachable / clients | yes / 1 |
| `state.snapshot` round trip | 18 ms, `connected: true`, `in_world: true`, L1 `Myaixdx`, map 147062786 |
| 3 real steps | `farm.start ok noop` x3 (22-128 ms), verdicts `noop` x3, labels from `rule` x3 |
| world drop mid-run | the client left the world, the loop pressed a guarded guest login, resumed after **6.1 s** |
| replan | `quest.advance {quest_id: 3841, npc_id: 3888}` -- the tutorial NPC the page named |
| contract | all 22 directive tools have a client handler; the 6 local ones (`quest.list`, `knowledge.*`, `fight.estimate`, `fight.plan`) must not be sent to the bridge |

`farm.start` returning `noop` is correct here and not a defect: a level-1 tutorial character has no
harvestable node on that map, so the guard's refusal is the right answer.

#### What would connect the two

One producer, at the end of each step in `autopilot`: map the verdict to a Beta posterior per
(tool, cause) and, when a farm step actually harvests, a Gamma posterior per (monster, level band).
Both labels already exist in the step record; the store is O(1) per observation. Until that exists,
"online learning" in this repo means the controller's repair table, and the conjugate store remains
a well-tested library with no caller.


---


## 2. Failure to supervision — kicks and refusals become labels

> From `FAILURE_TO_SUPERVISION.md`, merged 2026-11-07, text unchanged.
> How kicks, refusals and regressions become gate labels, data and tests.

Status: active, implemented 2026-10-24. Status lives in `CURRENT.md` (running system)
and `resume.md` (session handoff); the data path lives in `PIPELINE.md`. This file is the
argument and the contracts.

The simulator can generate a million clean episodes. It cannot generate the one
thing that is worth more: the server saying *no*. A kick is expensive, rare and
unambiguous, and it already carries the action that caused it. The plan is to
stop paying for the same lesson twice.

---

### 1. The three loops

| loop | runs on | produces | consumed by | must never |
|---|---|---|---|---|
| **live agent** | client + harness | guard telemetry, kick signatures, labelled outcomes | gate, repair set, eval set | evolve weights or explore actions to "see what happens" |
| **corpus** | CPU, minutes | counterfactual preference rows `(x, gains, margin)` | tool-policy pretraining | pretend it is the real server |
| **evolution / search** | **remote GPU/TPU (Kaggle or Colab) only** | hard worlds, filter thresholds, candidate policies | distillation into frozen ONNX | touch a live Dofus session, or run long on a laptop |

**Hard rule: evolution and neural search run on remote GPU/TPU (Kaggle or Colab).** They take hours
of wall-clock, they are stochastic and unreproducible on CPU, and a search loop
pointed at the real server is indistinguishable from a bot farm. The live loop
only *serves* frozen artifacts; there are no online weight updates. Local runs
are limited to short corpus generation and training sanity checks.

---

### 2. Live failures become labels

#### 2.1 Kicks → claim signatures

Measured on 14 attributed kicks: `QuestObjectiveValidationMessage` is followed by
`serverDisconnecting` after a **median of 230 ms** (max 479 ms). Nothing else in
the session predicts the kick as closely.

- `ai/safety_examples.py` scans `trajectories/*.jsonl` and writes:
  - `data/safety/gate_examples.jsonl` — one row per validation: `outcome`
    (`kick` / `ok`), `map_age_ms`, `quest_sync_age_ms`, `gap_ms`. Both classes
    are present, so it can train a gate later.
  - `data/safety/kick_signatures.json` — the `quest/objective` pairs that were
    followed by a disconnect (currently 4 pairs from 54 validations).
- `WebView2Backend` seeds that list into `window.__agentKnownKickSignatures`
  before the page loads.
- The page also learns live: `window.__agentRecordKick` writes the pair to
  `localStorage` when a disconnect follows a validation by ≤ 3 s, so an
  objective that kicked once is not claimed again in a later session.
- The guard refuses `known_kick_signature`; the planner falls back to the
  objective's own action (travel / fight / talk) instead of claiming it, and
  keeps taking that action while `note_result` reports it accepted. It retires
  the quest only after three rejected actions in a row, or ten actions without a
  map change. A single refusal is not evidence that the quest is unfinishable,
  and retiring on it would lose exactly the quest the guard protected.

This is **not a quest list**. The store only holds pairs the server itself
rejected, and live memory keeps growing. Type-level refusals
(`objective_server_counted`: discover-map, fight-monster, dungeon, …) remain the
generic rule; the pair memory covers objectives whose type the client cannot
classify, without guessing.

#### 2.2 Refusals → preconditions

Every attempt logs one line:

```
[AgentGuard] claim refused quest=3841 objective=13499 reason=known_kick_signature
```

The trajectory captures console output, so refusals become gate features and
repair examples instead of disappearing.

#### 2.3 Regressions → assets

| regression | permanent asset |
|---|---|
| window resize re-read the boot viewport | patch rule 12 in `tools/patch-engine.js`, shipped in `patch-rules` |
| model/featuriser width drift | `features` in the ONNX metadata; `NNPlanner` warns once when it cannot match |
| corpus workers dropped `--travel-cost` / `--ordering` | flags verified in the worker argv |
| guard reasons drifting | `node tools/test_claim_guard.js` |
| harness behaviour | `python -m advisor selftest` (the old `agent/smoke_verifier.py` gate was removed 2026-10-25 — `KNOWN_GAPS.md` G8) |

#### 2.4 Accepted but inert → bounded attempts

Two calls return `ok` without changing the world; both used to loop forever:

- **quest start** — the client reports `QuestStartRequestMessage` as sent, but only
  the server's `QuestStartedMessage` activates the quest. The client now remembers
  the send and reports `pending`, then `not_activated`; the planner counts
  observations of `quests_active`, so three starts that never appear there retire
  the quest. A repeated call also retires the quest it *names*, not the first
  active quest (the old autopilot blamed 3841 for a loop on 584).
- **fight gate** — `playerData.lifePoints` is stale 0 in world (a live session
  read 55/55 while every `fight.join` said "0 HP"). The gate now reads
  `playerData.isAlive()` and the selected character's characteristics, and the
  snapshot exposes `alive` / `life_state` so a real death is visible.

Rule refusals (`objective_server_counted`, unmet quest prerequisites) are class
`refused` in the controller, not `unknown`: they never get a retry.

---

### 3. Promotion gates

- **Tool policy**: must beat the teacher in the travel-cost world on quests and
  objectives — not on validation accuracy. Promoted 2026-10-24 (v4, 155,199 rows):
  83.6 vs the teacher's 81.7 quests at 400 steps (12 paired seeds, level 60) and
  140.0 vs 137.9 at 800 steps (8 seeds); at level 20, 72.8 vs 72.1. It also beat
  the previous 16,261-row model on 11/12 seeds at level 60 and 8/12 at level 20.
  A 145,732-row candidate was rejected because it lost every seed at level 20; a
  candidate must beat the installed model consistently across levels, not only on
  average.
- **Safety gate model** (stage 2): must not raise false refusals on held-out
  sessions compared with the rules-only guard, and must keep repeated kicks at
  zero. A gate that is merely accurate is not enough; the cost of refusing a
  legal claim is paid by every quest.
- **Verifier head**: stays unpromoted until `blocked` has support in the
  evaluation set.

---

### 4. Falsifiers

- If pair memory refuses legal claims on held-out sessions (a network drop was
  blamed on the claim), add expiry (30 days or one clean session) or demote the
  pair to "perform the action first".
- If 50 live validations after the guard produce zero kicks, stop growing the
  gate; keep the deterministic guard.
- If a learned gate cannot lower false refusals versus rules-only on held-out
  sessions, it is not promoted.

---

### 5. Current state

- Implemented: claim guard (15 test cases), live kick memory, claim telemetry,
  historical extractor, host-side seeding.
- Measured: 54 validations, 143 disconnects, 14 attributed, 4 signatures;
  attribution median 230 ms.
- Next: rebuild, relaunch, label one live session, compare repeated kicks per
  session against the 10 repeated claims on `4278/13986` in the old logs.


---


## 3. Facts versus code — what belongs in a constant and what must be learned

> From `LEARNED_FACTS.md`, merged 2026-11-07, text unchanged.
> The rulebook behind the menu-window and surface-label conversion.

Written 2026-11-07 after a session in which almost every fix was "add a rule". The rulebook grew to
eight facts in Python and four in the page, every one of them a place the agent could be wrong in a
new way, and none of them something the client had refused to tell us.

The distinction that matters is not "model vs rules". It is **could the client have answered this?**

### Three kinds of constant, and only one of them is debt

| kind | example | verdict |
|---|---|---|
| **protocol marker** | `position: 63` means not equipped; `-1` means remove; `ObjectSetPositionMessage` | keep. The page exposes numbers and message names; there is nothing to discover |
| **search budget** | `DIALOG_STALL_LIMIT = 4`, `QUEST_DATA_WAIT_LIMIT = 3`, `MAX_CONSECUTIVE_WAITS = 40` | keep. These are how long to try, not claims about the game |
| **game fact we asserted** | `_UI_WINDOWS = {"Bag": "equipment"}`, `"inventaire"` → Bag, `type_id == 0` never claimable | **debt.** The client publishes all three |

The test is simple: *if the page changed the name tomorrow, would the constant be wrong?* A protocol
marker is stable because it is the interface. A game fact is a guess about one build.

### The pattern: observe → store → seed

A fact that can be observed should be observed once, persisted, and adopted on the next start. The
project already has both halves: `agent/knowledge_store.py` (`remember` / `find`, append-only, keyed
by `(kind, key)`) and the objective ledger.

Worked example, 2026-11-07 — `menu_window`:

1. **Observe.** `ui.tap` already returns `open_before` and `open_windows`. The pair is one diff.
   `Autopilot._learn_menu_window` refuses to learn unless exactly one window appeared, because a
   press that opened two did not name the pair.
2. **Store.** `store.remember("menu_window", menu, window, source="observed")`.
3. **Seed.** `Autopilot._seed_menu_windows()` at the start of `run()` calls
   `policy.load_menu_windows(...)`, so the first press of the next run is already informed.

`QuestPlanner._UI_WINDOWS_SEED` keeps the one pair that was measured, so a fresh machine is not
blind. It is a starting point, not the answer: `menu_window_for()` reads the instance table.

Tests: `tools/test_menu_window_learning.py` (8 checks, driven through the real `Autopilot` and the
real `KnowledgeStore`), delegated into the gate as `test_menu_window_learning_suite`.

### Second conversion: a refusal becomes a fact (`server_counted_types`)

`_generate` used to skip the claim when `type_id == 0`, which is a claim about a type we may not
have met. The page already says which types it completes: *"objective 13531
(QuestObjectiveFreeForm) is completed by the server; perform the action instead of claiming it"*.
That sentence names the type, so it is evidence about **every** objective of that type.

- `ObjectiveLedger.server_counted_types` starts **empty**; the first refused claim adds one.
- The trigger is `SERVER_COUNTED_HINT = "completed by the server"` — the page's words, not ours.
- It is persisted in the ledger file, so a later session does not re-learn it.
- `record(..., reason=...)` carries the refusal; `Autopilot` passes `Step.error`, the field the
  refusal actually lands in.

Cost of being honest: the first claim of each type is now spent rather than skipped, once, ever.
Tests: `tools/test_objective_ledger.py` — offered until refused, generalises to an unseen objective
of the same type, an unrelated refusal teaches nothing, survives a reload, another type is still
claimed.

### Third conversion: the client's own surface names (`surface_label`)

The French→key table (`"inventaire"` → `{"menu": "Bag"}`) had the same defect one level
down. `state.snapshot.menu_icons` now publishes `[{key, label}]` from the page's own menu
bar, so the label is observed data rather than a translation someone typed.

1. **Observe.** Every `state.snapshot` carries `menu_icons`; `ui.icons` now returns the same
   list (one scanner, two readers).
2. **Store.** `Autopilot._learn_surface_labels` records `surface_label/<label>` →
   menu key for every icon that has both; a label shorter than three characters is refused
   because it would match prose by accident.
3. **Seed.** `Autopilot._seed_surface_labels()` calls `QuestPlanner.load_surface_labels`
   before the first plan. `_ui_surface` checks the learned labels first, then the English
   seed (`_UI_SURFACES_SEED`) that keeps a fresh machine working.

Tests: `tools/test_menu_window_learning.py::SurfaceLabelLearning` (7 checks) drives the
real `Autopilot` and `KnowledgeStore`; the gate asserts the French literal is gone and that
producer and reader both exist.

### What is still hardcoded, in leverage order

1. **`possible_positions == []` → one attempt.** Reclassified: the empty list is the
   client's own answer, so reading it is not an asserted game fact. The one-attempt bound
   is a **search budget** (how long to try), which the table at the top says to keep. The
   rank-never-exclude rule still allows a build that publishes an empty list for something
   wearable to succeed on a later pass.

### What is not the answer

Putting a model in the decision loop. Every failure that produced these constants was mechanical: a
disabled button, a stale `object_uid`, a marker position, a dialog page that never changed. No model
would have found them; looking at the client did, in one probe each. The lesson is **ask the client**,
not *use a bigger model*.

The corollary is the honest cost: a constant is verifiable in a 30-second test and a learned fact is
not. That asymmetry is exactly how a rulebook happens, so the check on any new constant is the table
above, not "does the test pass".


---


## 4. Latent reasoning and online weight updates

> From `LATENT_AND_ONLINE_LEARNING.md`, merged 2026-11-07, text unchanged.
> Judgement on latent reasoning, agent-to-agent channels, online updates.

Decision document. Every proposal below is judged on four axes: **what it buys**,
**what it costs in RAM/CPU**, **what can go wrong silently**, and **whether the
repo can measure the gain**. A proposal that cannot be measured here is rejected,
because this project's whole discipline is that an unmeasured claim is a guess.

Context that constrains every answer: two agents inside 200 MB/instance, one
30M encoder at 25 MB INT8 and 60 ms/call, a symbolic planner at 1.6 ms/plan, and
a conjugate-posterior learning loop at 5.5 µs/observation. Anything that costs
more than those numbers has to buy something they cannot.

---

### 1. The claim under test: thinking without writing tokens

Two independent lines of evidence say the tokens are not where the reasoning has
to live.

**TRM — "Less is More: Recursive Reasoning with Tiny Networks"**
(Jolicoeur-Martineau, Samsung SAIL Montréal, Oct 2025). A 2-layer, 7M-parameter
network that recurses on a latent state, with no chain-of-thought tokens and no
RLHF, beats models three orders of magnitude larger on Sudoku-Extreme, Maze-Hard
and ARC-AGI. It is the successor to HRM (Hierarchical Reasoning Model), which
used two networks recursing at different frequencies — the h/L cycle this repo
already mirrors in `combat/recursive_reasoner.py` and the planned L-cycle model.

**Coconut — "Training Large Language Models to Reason in a Continuous Latent
Space"** (Hao et al., Meta + UCSD, arXiv:2412.06769, Dec 2024). Instead of
emitting reasoning tokens, the model feeds its last hidden state back as the next
input embedding, so the "thought" is a continuous vector. Reported result: equal
or better reasoning with fewer tokens, because the breadth of a continuous state
replaces the serial depth of a token chain.

The counter-evidence is real and should be read before committing:
**"Do Latent Tokens Think? A Causal and Adversarial Analysis of
Chain-of-Continuous-Thought"** (2025) finds that latent tokens can carry
information the output head never learned to use — the model can appear to reason
in latent space while the answer is still decided by the last few tokens. That is
the failure mode to guard against: *latent reasoning that nothing checks is
indistinguishable from no reasoning at all.*

**What this means for us.** The encoder is text→action. It has no decoder and
needs none: the "thought" is the pooled 512-d vector, and the action head is a
cosine against 16 capability anchors. That is already a latent decision — we are
not adding latent reasoning, we are already doing it. The open question is
therefore narrower and more useful: **what else can that vector carry, and who
should read it?**

---

### 2. The agent-to-agent channel

#### Proposal: a shared latent bus, not text

Two agents in one process do not need to talk in tokens. The first agent already
produces, per step: a 512-d pooled vector, the chosen capability id, the validated
args, the plan node it came from, and the observation. The second agent's job
(§5) is to judge that step. Handing it the pooled vector plus a 12-dim progress
vector costs nothing — no tokenizer, no decoding, no latency.

| channel | cost per message | lossiness | debuggable |
|---|---|---|---|
| text (today's logs) | ~20 tokens ≈ 15 ms decode | entity names survive, *intent* does not | yes |
| latent vector (512-d fp16) | ~1 KB, 0 ms | intent survives, names do not | no, unless logged |
| both (latent + text sidecar) | 1 KB + the log line we already write | neither | yes |

**Decision: both.** The latent bus is the control channel; the JSONL log stays the
audit channel. This is the same split the repo already uses for plans —
`plan.render()` is for humans, the plan tree is for the machine — and it costs
one `tolist()` per step.

**What it buys:** the second agent can be conditioned on *why* the first chose a
call, not just what it chose. A critic reading `"hdv.scan {item: X}"` cannot tell
whether the model was confident or guessing; a critic reading the pooled vector
and the anchor margin can.

**What it costs:** the two agents must share an encoder version. If they diverge,
the vector means something different to each and the failure is silent. Mitigation:
stamp every latent message with the checkpoint hash, and have the second agent
refuse on mismatch — the same discipline `networkhealth/contract.py` already
applies to its normaliser.

**Rejected variant: a learned communication channel** (each agent learns its own
encoder/decoder pair, as in emergent-communication work). It cannot be debugged,
it cannot be trained without a reward that is itself unmeasured, and the two
agents already share a trunk. Not worth a megabyte.

---

### 3. The user-facing response — the hard half

The user's constraint is correct and it is the constraint that kills the naive
answer: **a bot that answers in broken language is worse than one that says
nothing.** "No tokens" works for agent→agent; it does not work for agent→human.

Three options, judged:

| option | latency | RAM | language quality | can hallucinate a price? |
|---|---|---|---|---|
| **A. template renderer** from the plan tree | <1 ms | 0 | fixed but correct, 4 languages | no — it prints numbers it has |
| **B. small decoder (8–12M)** | ~200 ms | ~25 MB | good for seen phrasings, degrades on new ones | yes |
| **C. external LLM (DeepSeek/Qwen)** | 400 ms–2 s | 0 local | best | yes, and it costs money per turn |

**Decision: A by default, B only for free-form chat, C never in the loop.**

The reasoning is not "A is cheaper". It is that **the bot's user-facing statements
are almost always structured facts**: "I am at 12,400 kamas/hour, 3 hours from
your target", "this fight is 62% winnable, I would need 4 more levels", "I cannot
buy that: the cap is 50k and it costs 120k". A template over the plan tree renders
all three, in four languages, with the numbers coming from the posteriors — so it
cannot claim a number it does not have. A decoder can, and a decoder that
hallucinates a price in a market bot is a financial bug, not a style bug.

The template layer is also where the **keyword feedback** the user asked for
lives: `IMPOSSIBLE`, `POSSIBLE <pct>`, `BLOCKED <reason>`, `DONE <n>`. Those are
four tokens the *scheduler* emits, not the model — the model never has to learn to
say them, which is exactly why they will always be right.

B is worth building only when a real user asks something the templates cannot
express. Until then it is 25 MB of unmeasured risk.

---

### 4. Online weight updates — four proposals, one accepted

The user asked for a judgement on MoE, first-pass weight optimisation,
evolutionary weight search, and real-time weight improvement. In order of how
much I would trust each:

#### 4.1 Test-time training / fast weights — **accept, narrowly**

NeurIPS 2025 was dominated by this: update a small set of "fast" weights at
inference so learning and inference happen together, with memory O(1) in context
length instead of growing a KV cache.

The catch is catastrophic forgetting plus **unverifiable drift**: if the weights
move on every episode, no two runs are comparable and a bad batch teaches a
lesson you cannot roll back. For a bot that spends kamas, that is unacceptable at
the trunk.

Where it *is* acceptable: the **L-cycle residual model** (~1M params, 4 MB),
whose entire output is a bounded multiplicative correction on a time estimate. If
it drifts, the worst case is "the bot thinks this farm takes 20% longer" — it
cannot make the bot buy the wrong item. Fast weights there are a real win because
the label (predicted vs actual minutes) arrives for free on every step.

**Verdict: fast weights in the L-cycle only, clamped to [0.5, 2.0], with the
previous weights kept and rolled back if a held-out window degrades.** Not in the
encoder.

#### 4.2 Evolutionary / population weight search — **reject**

Evolution needs many full evaluations per generation. One farm episode is minutes;
a population of 20 over 100 episodes is ~33 hours of wall-clock for one
generation, on content that changes as the player levels. The gradient signal here
(residuals from the planner's own predictions) is free and immediate. Evolution is
the right tool when the objective is non-differentiable and evaluation is cheap —
neither holds.

#### 4.3 MoE — **defer, with a named trigger**

The repo already measured this: a sparse top-2 MoE on the 30M trunk adds ~30 MB of
INT8 weights for capacity that 21k training rows cannot feed. `PLANNING.md`
§7 says don't; the evidence has not changed.

The trigger to revisit is specific: **one dense trunk demonstrably underfits two
domains at once** — i.e. adding the chat/critic task makes tool accuracy drop
while both tasks' losses fall. Then MoE is the right answer, because the two
experts want different features. Not before.

#### 4.4 First-pass weight optimisation (better init) — **accept, cheaply**

This is the one with the best ratio: no runtime cost at all, only a better starting
point. Concretely, initialise the 30M encoder from the *previous* checkpoint
(`--resume`, now implemented) and spend the saved steps on the epochs that were
being cut short. v11 stopped at 2,400 of 8,244 planned steps; a continuation at
lr 3e-5 is strictly cheaper than a scratch run and starts from 0.685 call instead
of 0.20.

**Verdict: this is what the current run does. It is the only "online" change that
is free at inference.**

---

### 5. The second agent — scope

The user's framing: it organises the schedule, tracks what the first agent does,
and covers moderation, drift detection and self-critique — the things the first
cannot afford.

That is one model with one input and four outputs, and the input is already
computed:

```
input   (12-dim, exists today in ScheduleManager)
        elapsed/expected, kamas_rate obs/exp, drops/kills vs prior, level_gap,
        steps_done/total, replans, deaths, hp_avg, inventory_pressure,
        price_drift, age_minutes, strategy_id
        + 512-d pooled vector from the first agent (the latent bus, §2)

outputs
        verdict     continue | replan | block | escalate   (4-way)
        residual    multiplicative correction on minutes    (scalar, clamped)
        drift       is the world different from the model's belief?  (score)
        critique    which argument of the last call was least supported (16+1 way)
```

Size: ~1M params, <5 MB fp32, sub-millisecond. It is the L-cycle the design
already specified, plus a verdict head.

**Why it can learn without labels:** every output has a free label.
`residual` is predicted-vs-actual minutes. `verdict` is checkable after the fact —
did the step succeed within 1.5× its estimate? `drift` is the same residual
distribution shift. `critique` is only trainable once the outcome says which
argument was wrong, which is why it comes last.

**Order of building:** residual → verdict → drift → critique. Each is useful
alone, and each one's labels are generated by the previous one.

---

### 6. What I would build, in order

1. **Latent bus** (1 day): stamp the pooled vector on every step record; second
   agent reads it. Zero new model.
2. **Template renderer** (§3A): the four keyword verdicts + the plan-tree
   sentences, in en/fr/es/de. This is what makes the bot speakable.
3. **L-cycle with fast weights** (§4.1): 1M params, residual head first.
4. **Second agent verdict head** on top of (3).
5. **MoE only if** the trigger in §4.3 fires.

Everything else — evolutionary search, a learned inter-agent code, a decoder in
the control loop — is rejected above with the reason.

---

### 7. Open questions for the user

1. **Language for the template layer**: do the four languages need different
   *tone* (formal/informal), or is one register per language enough?
2. **Keyword vocabulary**: `IMPOSSIBLE` / `POSSIBLE <pct>` / `BLOCKED <reason>` /
   `DONE <n>` — is that the whole set, or does the scheduler need more?
3. **Fast-weight rollback window**: how many episodes of held-out evidence before
   a drift is trusted enough to roll back? (Default I would pick: 50.)


---
