# Combat AI — completed work and next plan

Updated 2026-10-24. This document covers the combat-data, damage-math, and live fight-loop work. Network and quest behavior outside this scope remains governed by `STATUS.md` and `PLAN.md`.

## Current state

The project now has an authoritative class-spell dataset, a corrected damage calculator, an autonomous fight driver connected to `fight.join`, and a read-only board tool plus a turn solver built on client-exact geometry. Offline verification passes. **A complete live client fight has not yet been verified**, so autonomous combat is integrated but not yet declared proven in the game.

## What was completed

### 1. Official class-spell database

Built `dofus-network/data/cache/official_class_spells.json` from the official Dofus Touch class encyclopedia supplied by the user.

- 15 classes.
- 22 class spells per class.
- 330 unique spells.
- 1,980 spell levels.
- 2,040 parsed normal/critical damage lines.
- 204 parsed healing lines.
- AP, range, casting restrictions, cooldowns, target limits, area of effect, descriptions, and source URLs retained.
- Dynamic mechanics remain in the raw effect data instead of being misrepresented as fixed damage.
- All 1,980 levels pass structural validation and solver-adapter conversion.

Reproducible tooling:

- `dofus-network/tools/extract_official_spells.js`
- `dofus-network/tools/build_official_spell_database.py`
- `dofus-network/data/official_spell_repository.py`

The 17 common spells were not present on the supplied class pages, so none were invented or added from guessed sources. Official encyclopedia IDs also differ from parts of the local 1.74.9 client cache; the datasets remain separate rather than silently mixing versions.

### 2. Correct combat mathematics

Updated `dofus-network/combat/damage_calculator.py` and its tests.

The implemented order is:

1. Scale the base roll by the relevant characteristic and Power.
2. Add generic, elemental, or critical flat bonuses.
3. Subtract fixed resistance.
4. Apply percentage resistance.
5. Subtract armor.

Additional corrections:

- Integer arithmetic avoids floating-point floor errors in damage and resistance calculations.
- Player percentage resistance is capped at 35%; monsters and summons are not incorrectly capped.
- Critical fixed resistance applies only to critical hits.
- Current-client critical chance uses base critical percentage plus the Critical Hit stat, clamped to 1–100 when a base chance exists.
- Healing scales with Intelligence and Heals, not Damage and Critical Damage.
- Healing effects no longer inflate offensive rotation damage.

These corrections are prerequisites for trustworthy lethal, time-to-kill, and action-ranking calculations.

### 3. Live combat-loop integration

Updated `scripts/mobile-disguise.js`.

- A successful direct attack enables `window.__FarmBot`.
- The asynchronous walk-then-attack path enables FarmBot only when its attack succeeds.
- The asynchronous attack result is recorded in `window.__agentLastFight`.
- Calling `fight.join` while already fighting returns the current fight state instead of attacking another group.
- Fighter turns are accepted only when the actor ID matches the player; missing identity no longer means “every fighter is me.”
- The unused class-specific `aoeSpellId` configuration was removed.
- Placement, ready, casting, movement, and turn completion continue to use the client's own combat APIs and real spell descriptors.

Updated `dofus-network/agent/planner.py`.

- When `in_fight` is true, the planner requests `state.snapshot` rather than issuing quest, travel, or another fight directive.

Updated `dofus-network/agent/autopilot.py`.

- Combat waiting clears repeated-action history.
- The quest-stall clock is refreshed during combat.
- A fight cannot cause the active quest to be retired as “stuck.”
- Combat observations use a one-second cadence instead of the ordinary fast loop interval.

### 4. Regression coverage

Added combat integration checks to `dofus-network/agent/smoke_verifier.py`.

They pin:

- Planner non-interference during combat.
- No quest retirement or repeated-call abandonment while a fight runs.
- The slower combat polling cadence.
- FarmBot enablement after a successful attack.
- Asynchronous attack outcome recording.
- Re-attack protection.
- Safe fighter identity handling.
- Removal of the obsolete class-specific AoE setting.
- `fight.state` being a zero-argument, declared, client-backed directive whose handler
  performs no writes and exposes the board fields the solver needs.
- `fight.state` staying out of the width-locked verifier head.
- `ToolExecutor.fight_state()` dispatching with no arguments and unwrapping the bridge
  payload.
- `fight.plan` being a declared, local, read-only capability whose search knobs are
  declared and whose result is computed from a live board read.
- `fight.plan` staying out of the width-locked verifier head.
- Planning issuing exactly one board read and no cast, move, or turn-end call.

### 5. Client-exact combat geometry and turn solver

Added `dofus-network/combat/battle_solver.py` and `dofus-network/combat/test_battle_solver.py`.

Geometry and rules are ported from the 1.74.9 client bundle, not guessed:

- Cell ID to map point: `t = id % 14 - id // 28`, point `(t + 19, t + id // 14)`; all
  560 points are unique.
- Fight walkability: `(cell.l & 5) === 1` (the client's own `isWalkable` in fight mode).
- Fight movement is cardinal only, one MP per step. The client's fight reachable-zone
  walker calls `getNeighbourCells(cell, false)`. The `sqrt(2)` diagonals in the bundle
  belong to the overworld pathfinder and are not legal fight moves.
- Line of sight is the client's Bresenham variant. Actors block every line cell except
  the target, and every line cell must carry sight bit 2, target cell included.
- Spell ranges are rings, not filled areas: diamond by default, cross for
  `castInLine`, diagonals for `castInDiagonal`, over `minRange..range`.
- Effect IDs come from the client action table: 96–100 damage
  water/earth/air/fire/neutral, 91–95 the same as steal, 108/143 heal, 81 heal.
- Effect rolls mirror `getMinMaxDamageFromRawEffect`: `diceNum` is the minimum,
  `diceSide` the maximum, and a zero `diceSide` means a fixed roll.

Fighter and spell facts are read from the live `fight.state` board: cells, HP/AP/MP,
shields, percentage and fixed resistances per element, critical resistance, the
controlled character's totals via `getTotalStat()`, spell casts per turn and per
target, cast interval, castability, range/LOS flags, and normal/critical effects.

The solver:

- Generates legal move-and-cast nodes only when AP, MP, range, LOS, occupancy,
  castability, per-turn/per-target limits, cast interval, and cell restrictions permit.
- Prices damage and healing through `DofusDamageCalculator`, including the validated
  resistance order, the 35% player cap, shields, critical rolls, and critical chance.
- Runs a one-turn beam search that rewards effective damage, lethal kills, and effective
  healing, and caps damage at the target's remaining HP.
- Prefers not to spend MP when the target is already in range.
- Reports unmodelled utility/area spells in `notes` rather than treating them as no-op
  damage spells.
- Returns a plan only; it never casts, moves, ends a turn, or sends a packet.

`fight.plan` exposes this through the tool contract. It reads the board with
`fight.state` and unwraps the bridge payload in one place, `ToolExecutor.fight_plan()`.

Known limits, deliberately not approximated:

- Only direct single-target damage, steal, and heals are scored. Buffs, traps, glyphs,
  push/pull, AP/MP removal, and area-of-effect footprints are not planned yet.
- `max_cast_per_fight` is exposed by the client but the solver has no fight-long cast
  count, so it is not enforced.
- Team sides come from `isFighterOnUsersTeam`; this needs confirmation on a live fight.
- Critical chance is treated as a percentage, which matches the client field name and
  the calculator's `crit_rate_percent`, but has not been compared on a live cast.
- The 35% player resistance cap comes from the official damage tutorial; the client
  bundle also defines `MAX_RESISTANCE: 50`. This needs live verification before the
  cap is changed.
- No live board has been exercised yet, so every number here is an offline result.

## Verification results

| Check | Result |
|---|---:|
| Agent smoke verifier | 217 checks, 0 failed |
| Root regression suite | 60 checks, 0 failed |
| Combat and data unit tests | 60 tests passed |
| — of which battle solver | 50 tests passed |
| Battle solver self-check | Passed |
| `node --check scripts/mobile-disguise.js` | Passed |
| Official database conversion | 1,980 / 1,980 levels passed |

These are offline results. They verify code behavior and data consistency, not a real fight against the live server.

## Next plan

### Phase 1 — prove one complete live fight

Run one real fight after the user logs in, then inspect the client and autopilot records.

Acceptance criteria:

1. `fight.join` attacks exactly one group and enables FarmBot.
2. Placement and ready complete without manual protocol messages from the planner.
3. Only the player's turns trigger the driver.
4. The driver casts or moves using legal, currently owned spells and finishes its turn.
5. The fight reaches `GameFightEndMessage` without another `fight.join` or quest directive interfering.
6. The quest is not retired while combat is active.
7. After combat, normal quest planning resumes.
8. The network session retains its pinned egress; none of the anti-kick guards are relaxed.

If this fails, record the exact stage—attack, placement, ready, turn start, cast, movement, turn end, or fight end—and fix that stage before expanding the AI.

### Phase 2 — expose a complete fight-state tool

Implemented as the read-only `fight.state` capability (`FIGHT_STATE` directive):

- Turn owner, controlled fighter ID, fight round, turn age, and fight/preparation flags.
- Every fighter's cell, team, alive state, HP/max HP, AP/max AP, MP/max MP, shield, level,
  summoner, monster/summon/companion/boss flags, buff count, and primitive states.
- The controlled character's actual spell list with level, AP cost, ranges, line/diagonal,
  LOS and cell restrictions, per-turn/per-target casts, cooldowns, current casts,
  castability, and normal/critical effect facts.
- No writes: the handler contains no packet send, cast, move, turn end, or enable call.
- A Python accessor, `ToolExecutor.fight_state()`, returns the board without recording a
  step and without duplicating the bridge's response unwrapping.

Deliberately left to Phase 3 because they are computed, not observed — now implemented in
`combat/battle_solver.py` (section 5):

- Reachable cells and paths, line of sight between every pair, area-of-effect footprints,
  and enumerating valid targets.
- Resistances and their caps, damage prediction, and exposure/utility scores.
- The previous action's predicted-vs-observed outcome. *Still pending: this needs a live
  fight, so it remains the one Phase 3 item not yet built.*

Use live 1.74.9 descriptors for execution. Use the official encyclopedia data as a separate reference and training resource; do not assume their IDs or versions match.

`fight.state` is kept out of the verifier's `TOOL_ORDER` on purpose: that one-hot vector is
width-locked at 33 features by the current ONNX head, and appending a class would make a
working model fail on a shape mismatch. `fight.plan` has the same restriction. The planner
therefore still polls `state.snapshot` while `in_fight`; switching it to `fight.state` or
`fight.plan` is a Phase 3 change that must be made after the board is exercised on a live
fight.

### Phase 3 — connect exact legal-action generation and search

Partially implemented; see section 5 for what exists and its limits.

Done:

- Generate only casts, movements, and passes that the current state permits, using
  client-ported geometry and limits.
- Calculate expected damage, healing, lethal, and AP/MP efficiency.
- One-turn beam search over move-and-cast sequences.
- Expose the plan as the read-only `fight.plan` tool.

Remaining:

- Execute the selected legal plan through the client (`fight.move`/`fight.cast` or the
  existing FarmBot driver) and re-plan when the server state disagrees.
- Compare predicted and observed damage to calibrate the world model.
- Model area-of-effect footprints, buffs, traps, push/pull, and AP/MP removal as explicit
  mechanics instead of leaving them in `notes`.
- Use a deeper exact search for easy fights and broader lookahead for harder fights.
- Move the planner off `state.snapshot` once live board reads are proven.

A specialist model may rank solver-generated plans later, but it must not invent spell IDs, cells, or illegal actions.

### Phase 4 — build combat training data and a specialist ranker

After live mechanics and outcome logging are reliable:

1. Record state, legal plans, chosen plan, predicted outcome, observed outcome, and fight result without logging credentials or tokens.
2. Generate large combat datasets in simulation and evolutionary/lookahead search on Kaggle or Colab GPU/TPU.
3. Filter useful trajectories into supervised plan-ranking data.
4. Train a small specialist ranker to approximate expensive search quickly.
5. Benchmark it against the heuristic and exact solver on held-out fights before promotion.
6. Keep heavy evolutionary search and training remote; live Dofus is for validation and real outcome collection, not brute-force training.

### Phase 5 — expand coverage without hard-coded quest solutions

- Extract the 17 common spells from an authoritative linked source.
- Add weapons, equipment, sets, pets, mounts, consumables, monsters, and bosses as versioned data sources become available.
- Model boss mechanics as reusable combat rules and observable states, not per-quest scripted solutions.
- Keep quest planning generic: provide details and legal tools, and let the AI solve the objective.

### Live quest loop — diagnosis of 2026-10-24

The run after the combat work got stuck. The log named two failures, and neither was a
planning-quality problem: both were wrong contracts.

**1. A DiscoverMap `parameter0` is a text id, not a map id.** The live snapshot showed
quest 3841, objective 13541, `QuestObjectiveDiscoverMap`, status 2,
`params.parameter0 = 698953`. The planner read that as a destination and sent
`nav.goto {map_id: 698953}` nine times; the client answered `map 698953 is not adjacent
to 147063812`. 698953 is in no map index and the CDN answers 403.

The KB settles the field's meaning: all 765 type-4 objectives that have a destination
store it in `map_id` (135425–237240320), and `parameter0` never equals it; the 108
without `map_id` carry `parameter0` values in the map-name text-id range
(54845–1192014), none of which is a map. DofusDB's `QuestObjectiveDiscoverMapData` says
the same: `mapId` is the place, `parameter0` feeds the "Découvrir la carte : X" text.

Fixed:

- `features.required_map` and `planner.args_for_tool` read only `map_id`.
- A DiscoverMap objective with no `map_id` is a generic discovery: the planner walks an
  exit (`nav.goto {direction}`), preferring a neighbour outside the routing graph.
- `_travel_call` no longer sends an id for a target that is neither in the graph nor
  one of the current map's exits; it probes an exit instead.
- The bounds still hold: exploration that never changes map retires the objective after
  `CLAIM_ACTION_LIMIT`; a map change resets the bound.

**2. The dialog handler is `gui.npcDialogHandler`, not always `gui.npcDialogUi`.**
Every open dialog failed with `in a dialog but npcDialogUi is unavailable`. On 1.74 the
handler instance receives the reply list and holds `_uiReplyHandler`; the view may be
absent. `quest.advance` now accepts either object (including a `replyHandler` object),
and `debug.talk_state` reports which objects exist.

**Consequence for training.** The Kaggle toolkit was a snapshot of the old contract,
and its KB is DofusDB (Dofus 2.x), not Dofus Touch: the live objective 13541 is a
DiscoverMap, while the KB's objective 13541 belongs to another quest and is type 0.
A production run before the Touch data exists would scale that mismatch.

Status of the four-step sequence:

1. **Done (2026-10-24).** The fixed `planner.py`/`features.py`/`observe.py` contract is
   in `kaggle/toolkit`; the sim now uses `map_id` only, reports map exits, accepts
   `nav.goto {direction}`, completes generic DiscoverMap objectives by entering an
   unvisited map, and uses `resource` for gathering. The toolkit ships a union map
   graph: real Touch maps plus the DofusDB ids the KB names, so the simulator does not
   lose 91 discovery quests to a version mismatch. Dataset version published.
   Local pre-flight on 4 workers: 4,381 rows, all 18 tools represented, `nav.goto`
   30.5%; a 48.8K-parameter policy reached 87.95% validation accuracy, +10.7 points
   over the teacher, 98.5% gain capture, macro-F1 0.838, ECE 0.054. This is tool-choice
   learnability, not live proof.
   The live log confirms the learned policy was active (`tool_policy.onnx`, 18 tools,
   42 features) and issued `nav.goto {'map_id': 698953}` nine times; the new regression
   forces a learned `nav.goto` through the exit rule, and makes a learned walk to a
   known NPC use the stored `npc_map`. Smoke: 228/228 live, 68/68 toolkit.
2. **Pending — the fidelity blocker.** Harvest Touch-specific quest/objective/monster/
   spell rows from the live client with read-only directives, instead of DofusDB ids.
   This needs the client restarted so `debug.questdb` is loaded; the user signs in.
3. **Done — v4 corpus trained and installed.** GPU kernel v2
   completed 188,972 rows (170,072 train / 18,900 val); validation 90.08% vs teacher
   82.04% (+8.04), ECE 0.008. Capacity is flat: 8K params 89.61%, 48.6K 90.05%, 1.1M
   90.08% (spread +0.0047), so the 1.1M sweep winner was not worth 4.3 MB. That run
   used a stale 41-feature toolkit, so its ONNX was never installed.
   Kernel v3 (`tajdinenizar/thx-02-tool-policy-42f`, build `2026-10-24c`) finished:
   1606 s, 145,732 rows (131,158 / 14,574), 42 features, h192 **48,786 params**,
   validation 0.8224 vs teacher 0.7054 (+0.1169), gain capture 0.960, macro-F1
   0.2143, ECE 0.0642, 196,822-byte ONNX.
   **The first benchmark of that candidate was invalid**, and the cause was in our
   instruments, not in either policy. `sim/run.py` reported the *pre-action*
   observation to `note_result`, so a successful quest start looked like a server
   that ignored it and the planner retired live quests; and `world.py` resolved
   `farm.start` / `fight.join` / named `nav.goto` targets only against the oldest
   active quest, so correct calls for another active quest failed and a retired quest
   kept poisoning the world. Teacher scored 13.6 quests with a 283-step `fight.join`
   wrong-map loop. Fixed by `_start_waits` (the first report after a start cannot be
   evidence of failure), post-action `world.state()` for feedback, `_target_in_active`
   for multi-quest targeting, and featurising the objective the planner actually chose
   (`current_objective`, exposed for `sim/tool_choice.py`) instead of the world's
   oldest. Teacher re-benchmark: 81.8 quests, zero stalls.
   Corrected 12-seed paired benchmark (100–111, level 60, 400 steps, noise +
   travel-cost + nearest): teacher 81.67 (sd 1.70), installed 82.33 (sd 1.31),
   candidate 82.92 (sd 1.11). Candidate − installed **+0.58** (8/12 wins, sd 0.64).
   At level 20 it lost 0/8 (candidate − installed −0.25); at level 100 it won 2/8
   (+0.12). Not a consistent improvement, so **the v3 ONNX was not installed**; the
   installed 42-feature h192 head (16,261 rows, validation 0.8428, hash `5a6e…`)
   remains.
   Dataset v4 (travel-cost corpus, post-action feedback, multi-quest targets,
   chosen-objective featurisation) was then published (`Ready`, 10,100,668 bytes)
   and kernel version 2 completed in 1606 s: **155,199 rows (139,676 / 15,523)**,
   validation 0.8632 vs teacher 0.8494 (+0.0137), gain capture 0.960, ECE 0.0346,
   same 48,786 params / 196,822 bytes. The higher teacher agreement (0.849 vs
   0.705) is the instrument fix, not an easier corpus.
   Paired benchmark (seeds 100–111, 400 steps, noise + travel-cost + nearest): at
   level 20 candidate 72.75 vs installed 72.08 and teacher 71.75 (+0.67, 8/12
   wins); at level 60 candidate 83.58 vs installed 82.33 and teacher 81.67 (+1.25,
   11/12 wins); at level 100 candidate 83.58 vs installed 82.50 (+1.08, 11/12
   wins). At 800 steps (8 seeds) candidate 140.0 quests / 193.4 objectives vs
   installed 138.5 / 189.6 and teacher 137.9 / 187.1 (+1.50 quests, +3.8
   objectives, 7/8 wins). Level 100 repeats level 60 because quest availability
   saturates at 60, so it is the same world, not a harder setting.
   The v4 ONNX is installed at `models/tool_policy.onnx` (copy in
   `data/tool_candidate_v4/`), the previous 16k model is archived at
   `kaggle/runs/installed-16k-2026-10-24/`, `NNPlanner` loads 42 features and 18
   tools, and the live smoke is 228/228. This is the first learned head promoted on
   a consistent paired win. It is still trained on the DofusDB structural KB, not
   the production Touch model.
4. **After (3).** Scale to TPU evolution/self-play with ML filtering into supervised
   rows. Live Dofus stays validation-only.

The v4 run buys a clean, travel-cost pipeline and a structural baseline, and the
installed head is now its artifact. The remaining fidelity risk is (2):
map/NPC/quest ids from DofusDB do not name the same objects in Touch, so the model's
choices should be watched, not trusted, until the Touch rows exist.

## Non-negotiable constraints

- Do not weaken the network egress/session guards while improving combat.
- Do not hard-code per-quest solutions.
- Do not mix official and live-client spell IDs without an explicit version mapping.
- Do not count offline tests as live combat proof.
- Do not train by unattended brute force against the real server.
- Do not read or log credentials, tokens, salts, or `kaggle.json`; the user enters credentials personally.
- No commits or pushes unless the user explicitly requests them.
