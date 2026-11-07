# Entity-pointer architecture — target design for the shared brain

**Status:** target design, 2026-10-27. No entity-pointer model has been trained.
The shipped decision path is still the deterministic planner plus the 49K
21-logit tool policy and the 23-leaf stack. This document defines what replaces
them and which gates a replacement must pass.

## 1. What is missing today

The current action space is a 25-tool vocabulary, but the learned tool policy is a
42-float -> 21-logit classifier. It cannot:

- read a quest step like *"Bring 4 Gobball Wools to NPC X or buy them, equip your
  sword, and defeat the dungeon keeper"* and decompose it;
- point at a live map object (the barrel behind the inn, the NPC in front of it,
  the quest item on the ground);
- represent an unmet precondition as a child goal (buy / farm / equip / walk /
  click);
- choose a dialog reply by meaning rather than by a token-overlap heuristic.

The measured consequence is in `BASELINE.md`: 4.5% of world-changing steps moved
the world, zero quest objectives completed across ~4,300 steps, and the talk path
is where the run loses most of its points.

## 2. Target shape

Three components in one shared process (the brain daemon on `:8895`), one copy of
the weights:

```
live state (WebView2 page)
   │  state.snapshot: entities + quest text + inventory + prices + dialog
   ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 1. Universal Entity-Graph Encoder — 80M INT8 (~80 MB)               │
│    [Self] [Quest text] [NPCs] [Mobs] [Interactives] [Inventory]     │
│    [Dialog replies] [HDV prices] -> one token per live entity       │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ entity keys/values
        ┌──────────────────────┴───────────────────────┐
        ▼                                              ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────┐
│ 2. Pointer Decoder — 40M INT8        │  │ 3. Combat & Boss Core — 30M  │
│    (Verb, @Entity_Pointer)           │  │    INT8                      │
│    Score(e_i)=Q_quest·K_e_i^T        │  │    560-cell board, multi-    │
│    never a blind id                  │  │    element, traps, pushback, │
│                                      │  │    boss state-locks          │
└──────────────────────────────────────┘  └──────────────────────────────┘
```

**Budget:** 120–200M INT8 total, ~150 MB static; 4 instances target ~980 MB total.
The 80M/40M/30M split is a starting allocation, not a measured optimum. The
current 24.9M text trunk is the teacher/labeler and can be distilled into the
encoder; it is not the deployed shape.

## 3. Entity schema

`state.snapshot` already carries most of this. The pointer model needs one compact,
stable array per entity kind, each row with a live pointer id:

```json
{
  "self": {"id": 4434089, "cell": 322, "hp": 120, "max_hp": 120,
           "ap": 6, "mp": 3, "level": 3, "kamas": 0},
  "quest": {"quest_id": 3841, "step_id": 2, "objective_id": 13541,
            "type_id": 4, "text": "Parler à ...", "params": {},
            "map_id": 147063812},
  "npcs": [{"actor_id": 12345, "template_id": 3888, "name": "…", "cell": 263,
            "dialog_available": true}],
  "interactives": [{"element_id": 4491, "skill_uid": 552, "cell": 300,
                    "kind": "door|chest|resource|quest"}],
  "mobs": [{"group_id": -596, "main_mob_id": 4192, "level": 1, "cell": 303}],
  "dialog": {"open": true, "npc_id": 3888,
             "replies": [{"id": 0, "text": "…", "is_quest_offer": false}]},
  "inventory": {"items": [{"gid": 123, "qty": 4, "object_uid": 987}],
                "equipped": [{"slot": 0, "gid": 456}],
                "presets": [{"preset_id": 1, "name": "…", "items": 6}]},
  "prices": {"gid": 123, "avg": 400, "listings": [{"uid": 315540, "price": 100,
                                                   "quantity": 1}]}
}
```

Rules:

- **Pointers are live ids only.** An entity the page cannot read is absent, not
  invented. `null`/missing means the pointer head must abstain.
- **Reads are not actions.** The encoder may attend to any entity; the decoder's
  verb must be a legal action from the 25-tool vocabulary.
- **A pointer is a reference, not a prediction.** The page resolves the id against
  the same live object it reported; a stale id is refused and re-observed.

## 4. Action space

The decoder emits `(verb, @pointer)` from eight client primitives, mapped onto the
25 legal tools:

| primitive | pointer | tool mapping |
|---|---|---|
| `tap.npc` | NPC contextual id | `quest.advance {npc_id}` |
| `tap.reply` | reply id/text | `quest.advance {reply/reply_text}` |
| `tap.interactive` | element id + skill uid | `tap.interactive` |
| `move.map` | map id / exit direction | `nav.goto` |
| `item.use_or_equip` | object uid / preset id | `inv.equip` (preset), item-use path |
| `hdv.transact` | listing uid / gid + price | `hdv.buy`, `hdv.list`, `inv.sell` |
| `fight.engage` | group id | `fight.join` |
| `chat.send` | channel + text | `chat.say` |

The pointer head scores each candidate entity against the quest/objective text:

```
Score(e_i) = Q_quest · K_e_i^T
```

If the objective says "click the barrel behind the inn" and the live
`interactives` row has `element_id=4491`, the head selects `tap.interactive(4491)`
without a per-quest Python rule.

## 5. Sub-goal stack

Each instance gets a small stack (target 2 KB) in the brain daemon. The top is the
active micro-goal; the parent is never lost.

```
acquire(Item_X, 4)
  ├─ if inventory.count(Item_X) >= 4: pop
  ├─ if kamas >= hdv_price(Item_X): PUSH buy(Item_X, max_price)
  ├─ else if monster_drops(Item_X): PUSH farm(monster, 1)
  └─ else: PUSH navigate(npc/map) then interact
```

Precondition examples:

| parent | unmet precondition | child pushed |
|---|---|---|
| `quest.advance` talk step | NPC not on this map | `nav.goto {map/npc}` |
| `quest.advance` fight step | no group visible | `nav.goto {direction}` then `fight.join` |
| "equip your new items" | item in inventory, not worn | `inv.equip {preset_id}` |
| "bring 4 X" | inventory X < 4 | `hdv.buy` (if affordable) or `farm.start` |
| dialog open | reply list | `tap.reply` / `quest.advance {reply_text}` |

A child pops only when the observable state says it advanced. A failed child is
retried under the controller's existing rules; it does not retire the parent quest.

This is the intended fix for the 28–384-pass "policy had no action to take" waits:
the wait was a parent with an unmet precondition and no decomposition, not a model
that needed more width.

## 6. Training data

| source | content | labels |
|---|---|---|
| live `trajectories/*.jsonl` | entity rows + step rows | objective/map/dialog/inventory deltas; reads excluded |
| `data/observed_quests.jsonl` | client quest catalogue | objective type/params/text |
| `data/transitions/` | `(obs_before, tool, args, obs_after, moved)` | moved/progress from observable deltas |
| `sim/tool_choice.py` | counterfactual forks | per-candidate gain/curve; harness only |
| `combat/fight_outcomes.jsonl` | fight claims/outcomes | win/loss, damage, turns |

The pointer dataset is built by pairing each live observation with the legal
entity rows and the action the harness actually took; the model is trained to rank
the chosen pointer above the alternatives, and to predict `moved`/`progress` from
the candidate. A read is `moved=false, was_read=true`; it can never earn success.

## 7. Kaggle training plan

Training is the last phase, after the implementation gates pass. It runs on Kaggle
GPU (T4/P100), not Colab and not TPU unless a measured speedup justifies TPU.

1. Dataset: `thx-02-entity-pointer` containing the repo snapshot, `data/tools_sft`,
   `data/commands`, `data/transitions`, observed quests and the entity schema.
2. Notebook: interactive GPU notebook, not a queued batch push.
3. Stage A: distill the 24.9M text trunk into the 80M entity encoder on the
   entity/quest text rows.
4. Stage B: train the 40M pointer decoder on pointer-ranking + move/progress
   prediction; freeze the encoder, then unfreeze for a short joint pass.
5. Stage C: train the 30M combat core in `combat/encounter/` with the stratified
   four-bucket plans.
6. Export INT8 ONNX; record params/FLOPs/p50/p95 in the run manifest.
7. Copy `encoder_int8.onnx` / pointer heads / combat core into
   `models/shared_brain/`; the daemon loads one copy.

Acceptance gates:

1. Held-out harness gain capture above the 49K policy at matched params/data.
2. Live transition AUC for `moved` and `progress`, with ECE and a stated base rate
   (currently 17.6% moved, 2.7% progressed in the live corpus).
3. A shadow session where the model ranks candidates and the rule still acts.
4. One component replaced at a time, with the fallback kept and the mutation driver
   proving the gate is non-vacuous.
5. Combat: compute-matched, side-swapped, first live fight win before promotion.

**First Kaggle run (2026-10-27, pipeline smoke).** Dataset
`tajdinenizar/thx-05-entity-pointer` (4,017 rows; 3,885 train / 45 val / 87 test),
kernel `tajdinenizar/dofus-entity-pointer-v1`, GPU, 8 epochs, hidden 256. Metrics:
top-1 **0.3778**, moved AUC **0.4257** (base rate 17.8%), moved ECE 0.259,
progress AUC **null** (zero positives in val). This proves the upload, mount,
recursive-path lookup, GPU training, and artifact download path. It is **not** a
promotable model. The blockers are data, not width: 73% of rows are reads, only
1,003 are actions, 4 rows have progress, the new tools have no live rows, and NPC
names are mostly null in the recorded corpus. The next data step is a live session
on page build `2026-10-27s` using `tap.interactive`, `inv.equip` and `hdv.buy`,
then rebuild `data/entity_pointer` and rerun the kernel.

**Observed-text actions-only run (2026-10-28, not promotable).** The client's own
22 objective descriptions were joined into 4,016 / 4,017 rows, replacing generic
quest labels with real "talk/click/fight" wording. Dataset
`tajdinenizar/thx-08-entity-pointer-observed`, kernel
`tajdinenizar/dofus-entity-pointer-observed-v1`, GPU, 20 epochs, hidden 256,
actions only (1,003 train / 28 val / 53 test). Result: val top-1 **0.0357**, test
top-1 **0.0377**, moved AUC **0.434 / 0.362**. It did not learn above the 16-way
chance line; the hashed entity scaffold and tiny action set are the limits. Do not
promote. The next attempt needs a pretrained text/entity encoder and more live
actions, not another epoch on this table.

## 8. Integration points

| point | change |
|---|---|
| `scripts/mobile-disguise.js` | emit the entity arrays above; `tap.interactive`, `hdv.buy`, `inv.equip` are already wired |
| `agent/brain_daemon.py` | one shared encoder/pointer/combat model per process; per-instance sub-goal stacks |
| `agent/features.py` | keep the 42-float contract for the old policy; add the entity tensor path beside it |
| `combat/battle_solver.py` | four-bucket stratified beam; combat core ranks the 16 plans |
| `planning/economy.py` | price/farm time estimates feed `buy` vs `farm` child goals |
| `data/capabilities.json` | 25 legal tools; the pointer decoder may only emit these |

## 9. Phases

1. **Entity snapshot** — add the compact arrays and a server-side schema check.
2. **Sub-goal stack** — `agent/plan_stack.py`, 2 KB per instance, wired to the
   planner but not yet learned.
3. **Pointer dataset** — build from live logs + sim, freeze the split.
4. **80M encoder + 40M decoder** — Kaggle, INT8, gate 1.
5. **Live shadow** — rank entities, rule acts, measure.
6. **Rule removal** — one component at a time.
7. **30M combat core** — stratified plans, compute-matched, live fight gate.
8. **Daemon integration** — one shared copy, 12-instance p95 measured.

## 10. Honest status

- Shipped today: 25 legal tools, entity rows in `state.snapshot` (NPCs, groups,
  interactives, dialog, inventory/presets), the brain daemon, the 49K policy, the
  23-leaf stack, the combat solver, the entity-pointer dataset builder, the
  per-instance sub-goal stack, and a runnable Kaggle training scaffold.
- Target, not shipped: the 80M/40M/30M models, a promotable pointer head, the
  120–200M INT8 budget, the shared WebView2 environment (decided against because
  WebView2 proxy is environment-wide), the 30M combat core.
- The first Kaggle run is a pipeline smoke, not a promotion: top-1 0.378, moved
  AUC 0.426, progress AUC null. The live corpus is still 4.5% world-changing
  success with zero completed quests; this architecture is the proposed fix, not
  evidence that it works.
