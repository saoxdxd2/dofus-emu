# Planner design — intelligence per megabyte of RAM

> **Status note (2026-10-26).** The planning/economy/bandit reasoning below stands,
> but one recommendation is **reversed by policy**: this doc treats `hdv.buy` as the
> "single highest-value harness change". It is not — buying on the market is now
> deliberately **forbidden** (`FORBIDDEN_TOOLS`, `agent/autopilot.py:77`) because a
> purchase is irreversible. The `buy` branch is out of scope by design; use
> `hdv.scan`/`hdv.list`/`inv.sell` (supply side) only. See `CURRENT.md` §7 for the
> real 21-tool schema.

Constraint, restated: **RAM, not disk.** One game instance gets ~90 MB; the whole
AI side gets 70–150 MB for two agents. Inside that budget the agent must still be
smart enough to answer "I am level 1, get me a level-200 item" with a plan that
would actually work — farm the best kamas/minute spot, buy it, or take the quest
chain and level up first.

Everything below is measured on this machine with the repo's own embedded runtime
(Python 3.11.9, torch 2.14.1+cpu).

---

## 1. The measurement that changes the design

The knowledge base — not the model — was the budget killer:

| what | RSS |
| --- | --- |
| `json.load` of `data/cache/*.json` (40.5 MB of JSON) | **+121.5 MB** (3.0× overhead) |
| total process after loading the cache | **135.9 MB** |
| compact typed-array KB (`planning/kb.py`) | **+5.3 MB** |
| …on disk | 3.0 MB (0.25 MB arrays + 2.7 MB name blob) |

Same queries, **23× less RAM**: `json.load` turns every record into a dict and
every string into an object; the compact KB keeps `int32/int16` arrays and one
UTF-8 blob, and decodes a name only when it is printed.

So the 150 MB budget was never going to hold a model *plus* a JSON-loaded world.
With the compact KB, the entire planning stack — knowledge, cost model, economy
state, and a live plan tree — costs **6.0 MB** and plans in **1.6 ms** (p50, warm;
~400 ms once, to build the monster→item index).

---

## 2. Three kinds of chain of thought, and what each costs

"As smart as possible for its size" is really a question about *where* to put the
thinking. There are three options and they differ by two orders of magnitude:

| kind | mechanism | RAM | latency | fits 150 MB? |
| --- | --- | --- | --- | --- |
| **token CoT** | decoder writes reasoning tokens | 390 MB weights + KV cache growing with context | 17.7 s/decision (measured) | no |
| **latent CoT** | TRM recursion in a 24-dim latent, ACT halts at 1–8 steps | 0.6 MB (150k params) | sub-ms | yes |
| **symbolic CoT** | explicit AND/OR plan tree, printed and auditable | ~0 MB | 1.6 ms | yes |

The mistake would be to spend the budget on token CoT. A decoder that "thinks out
loud" costs 390 MB and 17 seconds to produce a worse answer than a graph search
that costs nothing.

**Decision: intelligence lives in the symbolic layer (planning) and the latent
layer (tactics). Token CoT is not used at all.** If a user asks "why?", the answer
is rendered from the plan tree — no generation required.

---

## 3. Architecture, five tiers

```
tier 4  chat / moderation encoder      8M int8     ~16 MB   (optional 2nd agent)
tier 3  tool policy encoder           30M int8     ~64 MB   goal -> {tool, args}
tier 2  planner + cost model          0 params      6 MB    level1 -> level200 routing
tier 1  latent reasoner (TRM-ACT)     150k fp32   0.6 MB    combat stance / value
tier 0  deterministic engines           0 params      0 MB    damage calc, graphs, rules
```

Tier 0 already exists and is good (`combat/damage_calculator.py`,
`combat/tactical_engine.py`, `navigation/*_graph.py`). Tier 1 exists
(`combat/recursive_reasoner.py`, untrained). Tier 3 exists and is being trained.
**Tier 2 is what was missing** — and it is the tier that answers the user's
question.

Measured budget for the whole thing:

| component | RAM |
| --- | --- |
| Python + numpy + ONNX Runtime (shared baseline) | 45.9 MB |
| 30M tool encoder, INT8, seq 256 | 63.8 MB |
| compact knowledge base | 5.3 MB |
| planner + economy + plan tree | 2.0 MB |
| L-cycle residual model (1M fp32) | 4.0 MB |
| **subtotal — one agent + full brain** | **121 MB** |
| + 8M chat encoder INT8 (second agent) | 16 MB |
| **total — two agents** | **137 MB** |

Inside 150 MB, with 13 MB of slack. A leaner variant swaps the 30M encoder for a
15M one (~32 MB), landing at **~90 MB** and leaving 60 MB for a larger L-cycle
model. Which one ships should be decided by measuring both on `core_sft_v4`, not
by preference — see §7.

---

## 4. The planner: why "farm the best kamas spot, then buy" needs no rule

`planning/planner.py` searches an AND/OR graph derived entirely from the cache:

```
acquire(item, qty)
    OR  buy    -> kamas                       (children: the farm that earns them)
    OR  farm   -> monsters that drop it       (hard gate: level <= player + 20)
    OR  craft  -> AND over every ingredient   (recursive, cycle-guarded, depth 3)
    OR  quest  -> quest-locked fallback       (no price, no drop, no recipe)
```

Every branch is converted to **minutes** by `planning/economy.py`:

```
buy    = (price - kamas) / kamas_per_minute(player_level)
farm   = ceil(qty / drop_rate) * kill_minutes(monster, player_level)
craft  = sum(ingredient costs) + craft time
```

`kamas_per_minute` is the key quantity: the best `sum(dropped item value × drop
rate) × kills/min` over every monster the player can actually beat. Once that
number exists, buying and farming are directly comparable, and `min()` does the
rest. Nobody writes "if the item is expensive, farm kamas first" — it falls out.

Real output, level-1 character, level-200 item:

```
goal           : 1x Griffe de la Déchireuse (item 31631, level 200)
player         : level 1, 0 kamas
best farm at level 1: Boufton Pâlichon at 113 kamas/min

$ buy 1x Griffe de la Déchireuse  (369m, 41,627k)  [uncalibrated, conf 45%]
  * farm Boufton Pâlichon for 41,627 kamas  (369m, 0k)
      . 113 k/min at level 1, short 41,627k
  rejected:
    - craft 1x Griffe de la Déchireuse (9 ingredients) (3,318m)
  missing tools: hdv.buy
```

That is the requested behaviour, and the rejection line is the part a user
actually wants: *crafting would take 55 hours, so no.* With `--equip` the plan
prepends `^ reach level 200 (now 1)` and keeps the acquisition subtree under it.

Two corrections the demo forced, both worth keeping:

* **NPC prices are not market prices.** Only 54% of items have a non-zero price
  and the median non-zero price is **1 kama**. The cost model now trusts a cache
  price only when it exceeds a level-based prior, and `price_source()` reports
  `market` / `npc` / `estimated` for every node.
* **A level-1 character cannot kill a level-200 monster.** Feasibility is a hard
  gate (`monster.level > player.level + 20` → branch removed), not a cost penalty.
  Without it the search "farms" content the character would die to.

---

## 5. Where the h/L cycles go

Your dual-cycle TRM-MoE idea maps onto the planner exactly, at two time scales:

| cycle | horizon | input | output | where |
| --- | --- | --- | --- | --- |
| **h_cycle** (micro) | one turn | 24-dim combat state | stance, halt, value | `combat/recursive_reasoner.py` — exists, untrained |
| **L_cycle** (macro) | hours–days | plan progress vector | "on track?" + time multiplier | new, 1M params |
| **fusion gate** | — | both | escalate micro → macro? | 2-way router, ~0 params |

The L-cycle input is a 12-dim vector the schedule manager already has:
`elapsed/expected`, `kamas_rate_observed/expected`, `drops/kills vs prior`,
`level_gap`, `steps_done/steps_total`, `replans_this_plan`, `deaths`, `hp_avg`,
`inventory_pressure`, `price_drift`, `age_minutes`, `strategy_id`.

Its job is not to plan — the search does that. Its job is to **correct the cost
model**: predict a multiplicative residual on the deterministic estimate. That is
a small, well-posed regression with labels the system generates for free.

**This is the training story for tier 2.** Every executed step logs
`(context, predicted_minutes, actual_minutes)`; `Economy.record_run()` already
writes it. After a few hundred runs the residual model beats the hand priors, and
after a few thousand it can replace them. No labelling, no teacher model, no GPU.

---

## 6. Schedule manager

`planning/schedule.py` runs the plan one tool call at a time and replans when
reality disagrees. Triggers, in priority order:

| trigger | condition | why |
| --- | --- | --- |
| `level_reached` | level crossed the item gate | new farms and gates unlocked |
| `cheaper_branch` | fresh plan is ≥30% cheaper, or a different strategy | e.g. player now has the kamas |
| `drop_rate_drift` | 3× expected kills, no drops | Beta posterior updated from the run |
| `overrun` | a step took > 1.5× its estimate | the model is wrong about this content |
| `stale` | plan older than 30 min | prices and levels move |

How those estimates stop being guesses — every parameter is a posterior updated
from real outcomes, and the ranker learns a per-strategy correction: see
**`docs/EXPERIENCE_LEARNING.md`**.

Only the goal and counters persist (`data/kb/schedule_state.json`); the plan is
rebuilt on restart because it costs 1.6 ms and a stale plan is worse than none.

Demonstrated transition, phase 2 of `--simulate`:

```
replanned: {"reason": "cheaper_branch", "before_minutes": 331.8, "after_minutes": 0.0}
next: hdv.scan {"item": "Griffe de la Déchireuse"}  <- buy 1x Griffe de la Déchireuse
```

---

## 7. What is missing, and it is not the model

### Tools the plan needs and the harness does not have

| tool | needed by | status |
| --- | --- | --- |
| `hdv.buy` | every `buy` branch | **missing** — `hdv.scan`/`hdv.list`/`inv.sell` exist, nothing purchases |
| `craft.make` | every `craft` branch | **missing** |
| `char.stats` | level gate, profession gate | **missing** — `state.snapshot` partially covers it |

The planner reports these instead of pretending: `plan.missing_tools()` →
`['hdv.buy']`, and the schedule manager falls back to a preflight `hdv.scan`.
Adding `hdv.buy` is the single highest-value harness change in the whole
roadmap — without it, the smartest plan is unrunnable.

### Data that must be calibrated

| quantity | current state | how it gets fixed |
| --- | --- | --- |
| market prices | 54% of items have a price, median **1 kama** — these are NPC prices | `hdv.scan` results → `Economy.record_price()`, cached in `economy_state.json` |
| drop rates | Beta(2,30) prior, 6.2% | `record_drop_run()` after every farm session |
| kills/minute | 2.5 at parity, bounded curve | overrun trigger + L-cycle residual |
| XP/minute | **absent** — `monsters_full.json` has no xp field | must be measured in-game; level-up branches are flagged `uncalibrated` until then |

The plan *structure* does not depend on any of these; only the ranking does. That
is deliberate — it means the planner is useful on day one and gets sharper with
play, instead of being wrong-but-confident.

### The MoE question

A sparse top-2 MoE on the 30M trunk adds ~30M params = **+30 MB of INT8 weights**
(121 → 151 MB) for capacity that 21k training rows cannot currently feed. Don't.

Note, though, that you already have sparse routing where it is free:

* **branch selection** in the planner *is* a router — 4 experts (buy/farm/craft/
  quest), top-1, chosen by cost, fully interpretable;
* **the h/L fusion gate** is a 2-way router over time scales.

Add a neural MoE only when a single dense trunk demonstrably underfits two
domains. Measure first.

---

## 8. Module map and usage

```
planning/kb.py         compact KB: build() from data/cache, 5.3 MB resident
planning/economy.py    CostModel (minutes) + Economy (prices, drop posteriors, runs)
planning/planner.py    AND/OR search -> Plan tree; render() is the chain of thought
planning/schedule.py   ScheduleManager: tick / report / replan / persist
planning/demo.py       CLI, including the level-1 -> level-200 example
```

```bash
python -c "from planning.kb import KnowledgeBase; print(KnowledgeBase.build())"

python -m planning.demo                    # auto-pick a level-200 item
python -m planning.demo --equip            # include the level gate
python -m planning.demo --item Goultard --kamas 3000000
python -m planning.demo --simulate         # drift detection + replanning
```

Every planned call is validated with `agent.schema.validate_call()` before it is
returned — verified: `farm.start {monster, kill_count}` passes.

---

## 9. Integration and gates

Wire `ScheduleManager.tick()` into the singleton service next to the existing
decision model:

```
POST /plan/submit   {goal, player}       -> plan tree + minutes
GET  /plan/status                        -> step, replans, missing_tools
POST /plan/report   {ok, data}           -> continue | replan | done
```

Then the C# bridge asks for one action at a time instead of one goal at a time.

Gates:

1. **P1** — `hdv.buy` + `craft.make` implemented; `plan.next_action()` returns
   zero `missing_tools` for the 200-item case. *(harness)*
2. **P2** — 100 real farm sessions logged; drop-rate posteriors move off the
   prior and the L-cycle residual beats the hand estimate on held-out runs.
   *(calibration)*
3. **P3** — XP/minute measured in-game; level-up branches stop being
   `uncalibrated`. *(data)*
4. **P4** — 15M vs 30M encoder compared on `core_sft_v4`; ship whichever gives
   the best call accuracy per MB. *(model)*
