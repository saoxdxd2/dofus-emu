# KNOWN GAPS — what is not true yet, with the evidence



An honest list of the things the system **cannot** do or **does not** verify

today. Each entry gives the symptom, the code or log evidence, why it matters, and

what closing it would take. This is the counterpart to `CURRENT.md`: that file

maps what runs; this file maps what is missing or unproven.



The rule that generated this file: a gap you can point at in the code is worth

more than a worry. Everything below is pointed at. Anything I could not verify is

labelled *unverified*.



---



## G1. The AFK disconnect is the client's own timer, and the keep-alive never reached it *(fixed 2026-10-27, measured live)*



**Symptom.** A run ends with the game's popup — *"Prolonged inactivity will lead to

automatic disconnection… Show some sign of life (move, send a chat message…)"* —

followed by `AI_DISCONNECT reason=INACTIVITY`. This has looked like a ban. It is not

one, and it is not the server either.



**Cause, read off the served bundle rather than inferred.** The whole countdown lives

in one module-private singleton (module 64 in this build), whose `_checkActivity` runs

every `d=12e4` (2 min):



```

e = Date.now() - this.lastActivityTime + 1e3

e < c(3e5 = 5 min)  -> nothing

otherwise           -> _enterInactiveMode()      // the warning popup

already inactive and e >= l(6e5 = 10 min)

                    -> dofus.disconnect("INACTIVITY")

```



So the drop is **client-initiated**, and the only thing that resets it is that

object's own `recordActivity()` — which the client calls from its tap handler and its

chat submit. The exempt-server list `p` is empty, and `_pingServer` only *follows*

activity (`hasSeenActivity`), it never causes it.



**Why the 2026-10-26 "fix" did nothing.** It called

`window.dofus.connectionManager.recordActivity()`. That method is not on the

connection manager — module 105 is the connection manager, module 64 is the tracker —

so the lookup resolved to `undefined`, the helper returned `false`, and every keep-alive

tick was a silent no-op. Measured on a live client: a depth-3 walk of `window.dofus`

and `window.gui` (877 objects) found **no** object carrying `recordActivity`, and this

bundle exposes no module registry (`webpackJsonp`/`__webpack_require__` count 0), so

there is also no handle to require it by id. The earlier claim that "the server's AFK

timer counts wire traffic" was wrong in both directions: the server is not the one

disconnecting, and a ping would not have helped.



**Fix — capture the tracker instead of naming it.** The tracker hands itself to the

global timer: `window.setInterval(function (e) { e._checkActivity() }, d, this)`. So

the page wraps `window.setInterval` before the game boots and keeps any argument that

has the tracker's shape (`recordActivity` + `_checkActivity` + `lastActivityTime`).

Nothing is invented and nothing is sent by hand: the keep-alive then calls that same

object's own `recordActivity()`, exactly as the client's tap handler does.



Marks now come from three places, in order of how little they depend on state:



* **every directive the page handles** (`__agentDirective`) — a directive arriving *is*

  the automation driving this client, so it needs no flag and cannot go stale. This

  closes the reload hole: `aiRunning` is pushed by the host only when its own answer

  *changes*, so a page that reloaded mid-run came back with `false`, shut the gated

  heartbeat, and lost its session ten minutes later;

* the 20 s gated heartbeat (`enabled || aiRunning`), unchanged, as the backstop for a

  loop that is waiting rather than acting;

* the packet send sites (`sendPacket`, `_cmSend`, `chat.say`), unchanged.



The page also asks the host for its state when its directive bus comes up

(`AI_REQUEST action:'state'`), which the host already answers — so the badge and the

heartbeat gate self-heal after a reload instead of lying until the next push.



**The instrument, because a prevented kick is silent.** The page counts its marks and

publishes them in every snapshot as `keep_alive`:



```

{marks, failed, present, via, last_source, idle_ms, age_ms, inactive}

```



`age_ms` and `inactive` are read from the tracker itself — what the client currently

believes, not what we hope. `present:false` is the case that used to be invisible: a

client where nothing can be marked. `_compact()` carries the record into every step

row, and `Autopilot.summary()` reports the worst of the run (`marks`, `age_max_ms`,

`inactive_steps`, `present_steps`/`steps`).



Measured live on 2026-10-27 with page build `2026-10-27l`, four clients on the same

build, two driven by loops and two left alone: driven clients reached

`marks 347 / age_ms 1 / inactive false / via setInterval-owner` after 3 minutes, i.e.

the client's own countdown was pinned at the start. Guards:

`node tools/test_keep_alive.js` (29 checks, including the capture, the shape rejection

of a non-tracker, and the bundle thresholds re-read at test time) plus the

`keep-alive:` / `afk:` mutation batches.



---



## G2. Equip: inv.equip preset path shipped; direct-UID equip and live proof owed



**Symptom.** When an NPC instructs the player to *equip* something (the referenced

session's "equip equipment" case), the agent used to have no verb for it. The

25-tool vocabulary (`CURRENT.md` §7) now includes `inv.equip`; it applies a saved

inventory preset through `InventoryPresetUseMessage`. Direct `object_uid` equip is

still refused because the served bundle has no proven drag-to-slot packet, and the

preset path has not yet been proven in a live session.



**Mechanism — found in the bundle (`script.js`), not guessed.** Two candidate

paths:

- **`InventoryPresetUseMessage {presetId}`** — a *single packet* that applies a saved

  gear preset (server answers `InventoryPresetUseResultMessage`). This mirrors the

  real UI action "click a preset", so it is the low-risk path and the likely intended

  answer to "equip this gear".

- `ExchangeObjectMoveMessage {objectUID, quantity}` — the crafting/exchange drag

  primitive; it carries a *quantity*, not a clean equipment slot, so it is **not**

  the obvious equip setter and using it for slotting would be a guess.



**Prerequisite blocker (why it is not wired now).** `state.snapshot` exposes an

inventory item only as `{gid, qty}` (`mobile-disguise.js:2865`) — no preset list and

no per-instance `objectUID`/`position` (the `objectUID`/`position` reads at 4028-4033

belong to the read-only advisor, a different path). So the agent cannot currently

produce a correct `presetId` (or a UID) from a fresh observation, and the exact client

field that stores presets is not known offline.



**The "not known offline" half is now resolved — read out of the shipped bundle, not

guessed (2026-10-26).** `client/www/build/script.js` contains, verbatim:



- `window.gui.playerData.inventoryManager` → `.getCorrectInventory(gameMode)`,

  `.getInventory(mode)`, `.getHeroInventory()`, `.getInventoryByOwnerAndType(ownerType, type)`;

  `playerData.inventory` is the `MAIN` shortcut and `labyrinthInventory`/`heroInventory` the others;

- each inventory object owns **`presets = {}`**, **`itemSets = {}`**, **`objects = {}`**,

  **`equippedItems = {}`**, `quantityList`, `isLoaded`, `weight`/`maxWeight`, `_isUsingPreset`,

  and `getCurrentWeapon()` (which is `equippedItems[weapon]`);

- the result handling is an event, not a return value: `handlePresetUsed(e)` emits

  `presetUsed` with `PRESET_USE_OK` / `PRESET_USE_OK_PARTIAL` / `PRESET_USE_ERR_UNKNOWN`

  (and `handlePresetSaved` / `handlePresetUpdated` alongside it);

- the send site is `sendMessage("InventoryPresetUseMessage", {presetId: e})`, guarded by

  `!this.isInFight() || this.blockPreset()` — so a preset swap in fight is refused by the client

  itself, and `disconnect()` clears `presets`/`objects`/`equippedItems`, which is why a stale read

  of any of them must fail closed.

- item instances expose `objectGID`/`gid` **and** `objectUID` (265 `objectUID` sites; the patch

  already reads `it.objectGID != null ? it.objectGID : it.gid` at `mobile-disguise.js:2481`).



Still genuinely live-only, and the reason this is not wired blind: none of

`InventoryItemMoveMessage` / `PlayerMoveItemMessage` / `EquipItemMessage` exists in this bundle

(zero hits), so the packet a real drag-to-slot sends is still unknown — and whether the tutorial's

"Go and equip your new items." is satisfiable at all by a preset/`itemSets` use, versus requiring

that unknown drag packet, is exactly the question the live capture answers. What the static read

buys is that the capture can now be *designed*: dump

`{presets: keys(inventoryManager.getCorrectInventory().presets), itemSets, equippedItems:

slot → gid, objects: [{objectUID, gid, qty}]}` read-only, watch which of them changes when a human

equips one item, and only then pick the verb.



**Plan (do not skip the live step).** (1) read-shape change: expose the four maps above through

`_agentSnapshot` — the field names are no longer a guess, but the *values* still need one live

session to prove the accessor chain resolves on the running build. **done 2026-10-26, read-only:**

`inventory.equipped` (slot → `objectGID`, capped at 24), `inventory.presets` (`preset_id`, `name`,

item count, capped at 12), `inventory.item_sets` (name + item count), `inventory.preset_blocked`

and `inventory.in_fight_inventory` (the two guards the send site checks), plus

`inventory.equipped_entry_keys`. If `pd.inventory` is the manager rather than the per-mode bag it

follows `getCorrectInventory(bag._type)` once. The entry's uid field is deliberately **not** read:

no name for it is proven, so the first worn item's own key list is captured instead and the live

row names it. Covered by `tools/test_inventory_equip.js` (29 checks, the block extracted and run

against stub inventories) with the same bundle-membership guard as G25 — every property name read

here occurs in the served bundle — and 4 mutations that write the failures back into the page.

**Do not wire these into `agent/features.py`'s inventory vector**: the trained policy's input

dimensions are pinned by the tool-vocabulary/feature contract, so adding slots there changes the

model's input width and invalidates the current weights. These keys are for the planner and the

verb, and a feature change is a retraining decision, not a snapshot one.

(2) add an `equip.preset`

capability + a JS handler sending `InventoryPresetUseMessage{presetId}` and verifying via the

`presetUsed` event and the post-state `equippedItems`, behind the same schema gate. (3) validate the

packet and verdict on a live session — never blind, because a wrong `presetId`/state is a

kick-adjacent action. Until (1)-(3) are done, any quest step gated on "equipped X" is a

hard stop, not a delay — and since 2026-10-26 that is no longer hypothetical: the recorded corpus's

first quest chain dies on precisely that instruction (see G3's root cause and G22).



**2026-10-29 (later): the verb exists, and its missing half is shipped.** The direct path is the

bundle's own `ObjectSetPositionMessage {objectUID, position, quantity: 1}` (the `_equip` the page

reproduces), not an unknown drag packet. Live measurements on dofus-05: the tutorial hammer (gid

1512, type 25, `possiblePositions: []`) is genuinely non-equippable — `equipItem` silent,

`ObjectUseMessage` accepted with no change, and `position: 1` moved nothing because slot 1 already

held gid 18844 (type 7, declared position `[1]`): **an occupied slot does not swap**. `inv.equip`

now takes `remove` / `unequip` / `slot: -1` and sends position -1 (build `2026-10-29bd`,

`tools/test_inv_unequip.js`, gate 1,245/0), and the tutorial snapshot publishes the step's

own `ending_conditions` (`FIGHTER_TURN` on the live stop). Live proof of equipping *and* removing is owed: the only two-slot item is gid 18976

(type 29, declared slots 22/23) and the page still bounds an explicit slot to 0..16, so the

proof needs that bound widened to the template's own declared positions; the account is

live again (see resume.md P-1c).



---



## G3. Dialog question text now consulted (interim heuristic, 2026-10-26)



**Live weight, measured 2026-10-26 (see `BASELINE.md`).** Across ~4,300 live steps the

agent completed **zero** quest objectives, `quest.advance` was the single most-failing

tool (504), and 148 of those failures were a null-deref in the dialog *close* path —

fixed the same day and covered by `tools/test_dialog_close.js`. So G3 is not a ranking

curiosity: the talk path is where the run loses every point it earns.



**Symptom (was).** The client exposes the open dialog including the NPC's message

and its reply options — `_agentDialogState()` returns `{open, npc_id, message,

replies:[{index, text, reply_id}], text_ready, ids_ready}`. The agent picked a

reply, but not by *understanding the question*.



**Evidence.** `agent/dialog_policy.py` `rank_dialog()` scored each reply only by

token overlap with the quest context (`_quest_context`). It did **not** read

`dialog["message"]`, so a yes/no or choose-based prompt was answered by "which

reply shares words with my quest notes."



**Fix — interim, done & gated.** `rank_dialog()` now reads the question

(`_question_tokens`) and uses it as a **tie-break only** where objective overlap

leaves two replies equal; a case the objective already decided is decided exactly

as before (verified by `agent.regression`). Ties with no question signal still

abstain.



**Still open.** This is a heuristic because there are **no (question → correct

reply) labels** to train a ranker — talk objectives have never completed, so that

data does not exist yet (criterion 4 trains *where data supports it*). And

end-to-end talk completion is still **live-unverified**: the scorer picks, the

reply-send path posts `NpcDialogReplyMessage`, but no session has proven the

objective retires. Both hang on a live run (#16/#20).



---



## G4. There is no vision / image encoder



**Fact.** `encoder30_v15` is a **text** encoder (24.9M params, `CURRENT.md` §5).

There is no image model in the repo. The Sentinel's "vision inspector" is regex over

text, not pixel input.



**Consequence.** The image-heavy extraction (the Papycha quest material) was **never

trained into any model**, and the AI does not see the screen. It reads structured

`state.snapshot` JSON. That is fine for most quests; it cannot answer anything that

only exists as a picture. *Unverified:* whether any current quest actually requires

image understanding to finish — none reported so far, because the blocker on

dialog/keep-alive comes first.



---



## G5. Network-health forecaster is dark (W4, carried from `PRETRAIN_GAPS.md`)



**Symptom.** `ai/diagnose_network.py` prints a verdict, but it is the relay's own

telemetry, not a forecast.



**Evidence, and this is the one still-live item from the deleted

`PRETRAIN_GAPS.md` (its W4 row):**

- `networkhealth/sentinel.py:25-27` still does `import torch`, `import torch.nn as

  nn`, `import torch.nn.functional as F` — the Sentinel is a torch service, so it

  cannot run in the no-torch shipped runtime.

- `sentinel.py:52` `CHECKPOINT_PATH = .../networkhealth_artifacts/model.pt`, and

  `sentinel.py:325` `network_engine = MultiInstanceNetworkHealth(CHECKPOINT_PATH)

  if CHECKPOINT_PATH.exists() else None`.

- **`model.pt` is absent on this machine** (verified), so `network_engine` is

  silently `None` and `trm_moe` reports false (`sentinel.py:355`). The old

  checkpoint will not load against the current code anyway: the architecture moved

  from `BatchNorm` (running stats in the checkpoint) to `GroupNorm(1, C)` (no

  running stats), and `SEQ_LEN` changed 128 → 32. Loading with `strict=False` would

  print a confident number from weights that no longer match the graph — worse than

  refusing, so it refuses.



**Fix shape.** Two separate jobs: (a) retrain the forecaster on the existing

artifacts (`python -m networkhealth train`) and re-export; (b) port the Sentinel to

the ONNX path (the original W4) so a torch-free runtime can serve it — that was the

unlock for a smaller installer. Until both are done, treat the network verdict as

telemetry, not prediction. *Caveat:* an idle relay reports its **last** RTT, not a

live one — run it while a client is connected or the samples are flat by

construction.



---



## G6. Verifier head is wired by the GUI, not orphaned — and unmeasured *(GUI auto-load gated 2026-10-27, offline-verified; a validated head is still absent)*



**Correction (2026-10-26).** An earlier read of this gap said `verifier_head.onnx`

was "orphaned / never loaded" because `--verifier-model` defaults to `None`. That is

only true for a hand-typed CLI run. The **desktop GUI auto-passes it**:

`aipanel.cs:705-708` prefers `models/verifier_head.onnx` if the file exists and

appends `--verifier-model` to the launch, so a normal launch **does** load the head.

There are four head artifacts on disk: `verifier_head.{json,onnx,pt}` +

`verifier_head_int8.onnx`.



**What is safe (pinned by `agent.regression`).** A loaded model can never mislabel a

**read-only query**: `Verifier.label()` calls `_model_label` only when

`tool not in READ_ONLY_TOOLS` (`verifier.py:416`), so the head's known stale failure

— trained on the old "query = noop" labels, p≈0.63 — cannot fire on a lookup. A

non-baseline artifact is rejected to rules without crashing, and a low-confidence

model falls back to the rule.



**What is NOT settled (the real criterion-4 gap).** For **actions**, a confident

model *does* override the rule (verified: a baseline set to prefer `failed` flips a

true noop `nav.goto` to `failed`, source `model`). The head's own

`models/verifier_head.json` reports **val accuracy 1.0 on 60 examples** — but that

number is **circular**: `SECOND_AGENT.md` documents that those self-check labels are

produced *by the rule verifier from exactly the features the model sees*, so 1.0

measures rule-cloning, not game-correctness (a model that reproduces the rules

cannot beat them). The head also predates the read-only relabeling. So per

"replace hardcoded *wherever data supports it*," the reported accuracy does **not**

support trusting the head over the rule for actions — it adds no measured signal.



**Decision + falsifier.** Keep the rule as authority for actions. **Shipped 2026-10-27:** the GUI

now auto-loads a verifier only when `dofus-network/models/verifier_head.validation.json` exists and

carries `"labels": "live"` or `"human"`, `"n" >= 60`, and `"above_baseline": true`; otherwise the

loop labels with rules (`string verifier = null`). The neural head is preferred, the linear baseline

is the fallback, and an explicit CLI `--verifier-model` still overrides the choice for an experiment.

No contract exists today, so the GUI now runs rules-only — the honest state while the only artifact's

1.0 is rule-cloned. Falsifier that flips this: a contract from a head trained and validated on

held-out *real* step labels above the rule baseline — then the contract unlocks the auto-load and

this gap closes.



**Evidence (offline).** `test_verifier_head_gate` (7 checks) pins the contract file, live/human

labels, the 60-example floor, the above-baseline flag, the all-three condition, the rules default,

and the append behind the gate; four `head:` mutations caught. The release build compiled the

changed C# (`DofusFarm.exe` 540,672 bytes, `setup.exe` 79.0 MB) and the post-build gate is

**550 checks / 0 failed**. **Not verified:** no live/human-labelled head exists yet, so the head

stays off by design.



---



## G7. Live combat is unverified against the real server *(fallback behavior offline-tested 2026-10-27; live fight still owed)*



`CURRENT.md` §6 describes the solver→ranker→live pipeline, but **no fight has been

proven against the client**. Evidence: `agent/executor.py` records

`live_calibrated: false`, and `models/fight_calibrator.json` is absent. Across the

recorded step history the fight tools (`fight.join`, `farm.start`, `fight.pass`)

were essentially never reached, because runs stalled on the *talk* objective before

ever arriving at a kill step. Do not count offline combat tests as live combat

proof.



**G24 is the other half of this gap.** Since 2026-10-26 the loop starts the brain and records

whether it was reachable, so a live fight now answers two separate questions: did the fight

happen (G7) and who decided its turns (G24). Closing G7 without reading the run's `brain` row

and `live_policy_decisions.jsonl`'s `contract.value_ready` would leave the second unanswered.



**A third half: the loop may never have been able to see a group at all (measured 2026-10-26).**

`map.groups` is `[]` in **all 4,782** recorded observations — the same number of rows that made

`fight.join` report "no group on the map" and `farm.start` fail. Before reaching for a cause, the

G25 lesson applies: an empty list is not evidence about the client, only about the read, and the

type name the page filters on (`GameRolePlayGroupMonsterInformations`) does exist in the served

bundle, used the same way by the client's own tooltip and attack paths. So the three causes — no

actor manager, a collection whose actors are named differently, or genuinely group-free maps

(tutorial and town are) — cannot be separated from this corpus, and `_listMonsterGroups` reported

the same `[]` for all three.

It now records which one happened: each call publishes a census (manager reachable, which

collection name was used, actors iterated, actors matched, and a bounded histogram of the actual

actor type names), and `state.snapshot` carries it as `map.groups_scan`, so it lands in the same

observation rows. `python tools/group_scan.py` reads those rows and prints one of three verdicts:

the filter works and the maps were empty (a routing question), the collection is unreachable (a

plumbing fix), or actors were iterated and none matched, in which case the printout names the

types the client really uses — the fix, not a guess. Against the recorded corpus it currently

answers "4,782 observations, none carrying a scan", which is the honest statement that the

question has never been measurable until now.



**The census paid for itself before a single live row existed (2026-10-26).** Reading the served

bundle to name the actors the census would print turned up a second, worse fact: the two handlers

that decide whether a fight is reachable — `fight.join`'s monster match and `farm.start`'s

named-mob branch — enumerated members from `staticInfos.monsters`, which is the shape of the

monster-group **reference database**, while the map listing enumerated the live

`staticInfos.mainCreatureLightInfos` + `staticInfos.underlings`. The client answers this question

for itself with `actorManager.getActorFromMonsterId`, which matches `data.creatureGenericId` or

`data.staticInfos.mainCreatureLightInfos.creatureGenericId` — so a group could be visible on the

map and still be refused as "target monster 447 is not on this map", which is exactly the recorded

refusal. That means "the corpus cannot separate the three causes" was too generous: a fourth cause

existed, *the group was there and the join could not read it*. Both handlers now share one search

(`_findMonsterGroup`) built on one membership read (`_groupMembers`), wider than the client's own

in exactly one respect — a named minion that is not the group's boss still counts, because the

server lets you join it — and numeric-coerced, because the client compares with `===` and a goal

can arrive as a string. A refusal now reports `groups_seen`, `monsters_here` and `actor_path`, so

the next live "not on this map" says whether the goal's id was wrong, the map genuinely had other

monsters, or no member id could be read at all.

The same bundle read killed a guess that had been living in three readers: `actorList` occurs

**zero** times in `script.js` (the manager's constructor initialises `this.actors = {}`), so the

`am.actors || am.actorList` fallbacks were dead branches that would have reported an absent

collection as an empty map. They are gone, and the suite now asserts the absence in both

directions — a name must be in the bundle, and `actorList` must not be in the page.

Covered by `tools/test_group_scan.js` (54 checks: the census and the shared reads, both run

against stub actor managers) and 15 mutations in `tools/mutate_cause_gate.py` (3 census, 7

membership/search, 5 reader), all CAUGHT; the live answer is still one walk through a mob map.



**The consumer half (2026-10-26).** A richer refusal is only worth having if the loop routes

differently on it, and it did not: `agent/controller.py` and `agent/planner.py` classified the

refusal from its *sentence*, and the one repair the planner knew was travel. The page's counts

separate four meanings (`agent/planner.group_refusal_outcome`): the named mob is on another map

(`elsewhere`), this map carries no group at all (`empty_map`), the refusal never reached the

client (`other`), and — the one that must not travel — groups are here but none of their members

could be read (`unread_members`), which is a defect in the client patch. Wandering the world for

that spends a multi-hour run and never finds the mob, so a `unread_members` refusal holds on a

read and lets the loop's no-progress guard decide, rather than issuing world-changing walks.

`fight.join` refusals count as well as `farm.start`, a later success unlatches the defect, and the

run summary now carries `group_refusals` and `group_read_broken` so the two failures are told

apart afterwards. `Autopilot.summary` reads them through a full `getattr` chain because a gate

shim has no runtime — and `summary()` is the last call a run makes, so an `AttributeError` there

would discard the very numbers criterion 5 is measured from; the crash was real, caught on the

first run of the new section, and is now one of its checks. 19 checks, 5 mutations (batch label

`refusal`), gate **312 / 0 failed**, driver at **125 mutations**.



**Measured, from the log rather than from the code.** With both logs read by default, the reader

finds **24 recorded group refusals** in the live agent log — **19 labelled `unknown`, 5 labelled

`plan_wrong`, and not one carrying a repair-relevant cause**. That is the direct evidence that the

loop never knew why it could not join a fight: the page's own join wording matched no bucket in

`cause_of` (the bucket list knew "not on this map" and "no visible group contains", not

"no group on this map contains"), so the strategy table was handed a label that ranks nothing. Two

rows of the fix are prospective by nature: the wording now resolves to `wrong_target`, and a

refusal whose payload says "groups here, no member id read" resolves to `stale_state` — *re-observe,

do not move* — which is the label a learned repair policy can act on differently (criterion 4).

`tools/group_scan.py` prints the refusal histogram next to the census, so one command answers both

halves of G7 about a run. Gate **323 / 0 failed**, driver at **132 mutations**, all `refusal`

mutations CAUGHT (12 of them). Still zero live rows for any of it: the counts come from a client

that has not run the fixed page yet.



**The fallback that has played every live fight turn is now behaviorally tested (2026-10-27).** The

learned brain has never decided a live turn (G24/G25), so `_planFightAction` is the policy the server

actually saw; only its AP budget was tested before. `tools/test_fight_planner.js` extracts the real

target/range/move code and runs it against bundle-shaped spells and enemies: it targets the lowest-HP

enemy even when farther, excludes a spell whose min range misses a closer enemy, filters unaffordable

spells, never offers a spell the client refuses, ignores buffs/heals as attacks, moves toward the

nearest enemy with the client pathfinder when nothing reaches, and passes with no MP. 10 checks and

six `fight:` mutations caught. This closes the "only AP is tested" hole; the live fight — and a win —

is still G7's proof.



**Live turns use the policy, and the turn driver now owns movement (2026-10-28).** A live fight on

build `2026-10-27s` shows the 8891 policy deciding casts (`__agentLastFightTurn.brain` carries

`probability`/`value`), so G24/G25 are stale for this session. Its action space is cast-only: when

nothing is in range it answers `pass`, and executing an out-of-range cast spends the turn with full

MP. The driver now (a) checks the chosen cast with the client's own `_spellReaches` and falls back

to the local planner when it cannot reach, (b) falls back to the local planner on a policy `pass`

while MP remains, and (c) prepends the actor's current cell to `_displayPathInFight`'s path before

`compressPath` — the bundle's `_tapFight` does `w.unshift(c)` for the same reason. Measured live:

before the prepend every move was ignored (`my_cell` stayed fixed and the same cell was re-sent

40 times); after it the actor advanced 145→187→230→270 and each plan differed. `debug.fight_state`

now reports `fallback`, `plan`, `move_probe` and `server_movement` for the next run, so a refused

move is attributable instead of silent.



---



## G8. The test gate is gone — partially restored 2026-10-26



The `agent/smoke_verifier.py` gate (last reported 110 checks) was deleted on

2026-10-25 along with the rest of the Python test suite. It has been **partly

rebuilt**: `python -m agent.regression` now runs **922 checks** against the real

code paths — the verifier rule engine (all four verdicts, the nested `position.*`

aliases, the `ready_objectives` claim-proof, read-only-answered-is-advanced), the

verifier model contract (a baseline json loads; a wrong-kind artifact degrades to

rules; the read-only model-label guard), the 25-tool vocabulary equality

(`schema.TOOLS` == `capabilities.json` == `features.tool_registry()` == 25), the

objective-type buckets, the dialog question tie-break, and

prompt/named-mob fidelity (`_goal_mode` + `Autopilot._farm_allowed`: a quests prompt

never farms unless the objective is a delivery; `_farm_target` +

`QuestPlanner._visible_target`: a named mob stays that mob), plus the recovery

layer's no-repeat/terminate invariant. It **delegates the JS page suites**

(`tools/test_keep_alive.js`, `tools/test_npc_actor.js`) to `node`, **the read-only

bundle-resilience classification** (`tools/apply-map-resilience.js --check`, see G12) to

`node`, **and the Go relay tests** (`go test dofus-net-proxy.go *_test.go`) to `go`. **Combat suites are now delegated into the same gate** (`combat.test_battle_solver`,

`combat.test_grid_model`), and the simulator floor is pinned (119 quests/148 objectives,

max_repeat <= 3) so a silent teacher regression cannot pass. The 40-instance daemon

control plane is gated by `test_brain_scaling_40`. **Still not covered by any

single gate:** the C# `DofusFarm.exe --bundle-selftest`,

and the "full release build" the goal names — those are run separately, and the

loop→live behaviour still needs a real session. `advisor/selftest.py`

(`python -m advisor selftest`, 122 checks) covers the knowledge repository, not the

loop.



The surviving JS checks live in `tools/` (`test_keep_alive.js`,

`test_npc_actor.js`, `js-error-finder.js`, `auth-bypass-suite.js`); the

`tools/test_claim_guard.js` some older docs still cite no longer exists. The

project memory pointer that said "tests live under `dofus-emu-cleanup-2026-10-25`"

is also stale — that folder no longer holds them.



---



## G9. Identity is keyed by slot, not by account *(fail-closed binding shipped 2026-10-27, offline-verified; live relogin proof owed)*



The bridge/instance layer addresses an instance by its **slot → port** mapping

(`bridge_port.txt`, relay port `8880+slot`). Nothing binds a run to the *account*

logged in on that client. On a multi-instance host, "which character did this step"

is answered by which slot, and a slot that reloaded into a different login is

indistinguishable to the harness. Relevant to the "different exit IP looks like a

hijacked session" class of disconnect.



**What shipped.** `snap.character` already carries the client's own `{id, name, level, breed}`.

`Autopilot._check_identity` binds the run to the first real character and compares every later

snapshot: the id wins when both sides have one (a rename is the same account), the name is the

fallback for a build without ids, and an unknown snapshot (character select, an older page, a null

field) is not a change. **Binding happens only in the world**: the client can still hold the last

character's base informations at character selection, and binding to that would turn a legitimate

login into a false drift on the first real snapshot. A different character on the same slot sets the

drift, writes

`{"kind":"identity","event":"drift","bound":…,"seen":…}`, and becomes the run's stop reason;

`_require_world` and the main loop both check, so a relogin into another login ends the session

instead of playing it, and a drift during recovery is reported as identity, not as "client gone".

Level-ups and renames are explicitly not drift.



**Evidence (offline).** `test_identity_binding` (18 checks): bind/log, same id/name, level-up,

rename-on-id, unknown snapshots, id-only and name-only builds, late id fill, the drift row and the

stop reason, a character-select snapshot that must not bind, plus source pins for the loop and the

world wait. Eight `identity:` mutations caught;

gate **558 checks / 0 failed**; `node tools/js-error-finder.js` 23/0.



**Not verified (live).** No recorded relogin has been replayed with a different character on the same

slot; the proof is one live relogin (or a window relaunch into another account) producing a drift row

and a stopped session. Multi-instance step attribution still uses the slot for logs — the guard makes

a wrong-account continuation impossible, it does not make the slot an account id.



---



## G10. `kaggle/toolkit/` drifts with no generator *(generator shipped 2026-10-27; dataset re-upload owed)*



`dofus-network/kaggle/toolkit/` is a hand-assembled copy of `agent/` + `sim/` that

backs the published Kaggle dataset (`tajdinenizar/thx-02-sim-toolkit`) a live kernel

runs. It had **already drifted** (18 files, including `autopilot.py`, `planner.py`, `features.py`,

`schema.py` and `data/capabilities.json`) and there was no export script in the repo to regenerate

it. It is not a fork to delete — deleting it breaks the dataset a kernel depends on.



**Generator corrected 2026-10-28.** The first generator only visited files already

present in the toolkit, so its 25-file check missed **135 stale or absent modules**.

`tools/sync_kaggle_toolkit.py` now mirrors source Python modules under `agent/`,

`sim/`, `ai/`, `knowledge/`, `combat/`, `advisor/` and `tools/`, including source-only

modules, plus the three root Python helpers and `data/capabilities.json`.

Standalone imports exposed a further missing `_lazy_exports.py` dependency, now

included. After adding the lifecycle and coverage tests, the measured matrix is

**164 sync, 8 pinned, 0 drift/missing**.



Toolkit-only legacy modules, downloaded quest/subarea caches, the trimmed map index

and Kaggle metadata are pinned: a write neither deletes nor refreshes them. New

non-code artifacts are excluded. Writes copy only changed/missing files atomically

and record the post-copy manifest; a second write copies nothing unchanged.

`test_kaggle_toolkit_sync` checks source-only dependencies, root helpers, pinned

cache preservation, real-tree drift, and imports in a separate toolkit-cwd process

with Python path environment overrides ignored.



**What remains:** no dataset was uploaded. Before a new training publication,

exclude any downloaded data whose licence disallows AI use and use client-derived

inputs; a current local code mirror is not an updated or training-approved dataset.



---



## G11. `tools/js-error-finder.js` is red (9 bundle checks) — checker vs runtime-patch mismatch



**CLOSED 2026-10-26: 23 passed / 0 failed, exit 0.** The 9 reds were accurate, and they

cleared by fixing the bundle rather than the checker — see G12. The heading and the

analysis below stay because "a suite that is red for true reasons" is the state this

project was in, and the owner table is what turned it into work.



Found while deciding what to delegate into `agent.regression` (criterion 6, "JS page

suites stay green"). Running it: **14 passed, 9 failed, exit 1.** The 9 failures are

all static assertions against the *game bundle* (`TacticalMode._loadAtlas`,

`IsoEngine.launchMapTransition` watchdog, `_loadMapAssets` try/catch/finally,

`Batch._updateSpriteHighlight`, etc.) — none are about the agent adapter. This is

**pre-existing** (no edit this session touched `script.js` or `patch-engine.js`).



Why it reads red: **its premise was checked and was wrong in my earlier note here.**

`js-error-finder` step 8/9 reads `desktop/client/www/build/script.js`, and that file

*is* the effective artifact — `--bundle-selftest` proves it equals

`script.js.bak` + the shipped rules, and `clientbundle.cs:BundleDir()` serves that same

directory. So these are **true alarms about the live bundle**, not a

"checker-greps-a-pre-patch-file" artifact. The resilient guards it wants are genuinely

absent. That is **G12**, root-caused and fixed there.



**A real signal inside the noise — now resolved, see G12.** The self-test also lists

**3 of 13 rules skipped** (`4: end marker missing`, `5: needle missing`,

`51: needle missing`). Settled offline on 2026-10-26: those three are the

map-transition / black-screen resilience block, and the answer is worse than "moved in

a newer client build" — rule 4's `end` needle contains `finally{`, i.e. it is the

rule's *own output*, so it can never match a raw bundle, and rules 5/51 target a

`_guardTimer` string that exists in **neither** bundle copy. See **G12**.



**Breakdown of the 9 reds (verified offline against the live text):**



| check | owning applier | status |

|---|---|---|

| `launchMapTransition` watchdog | apply-map-resilience #5 | missing → fixed by one apply |

| `_resetTransition` graphic→null | apply-map-resilience #4 | missing → fixed by one apply |

| `_saveImageForTransition` `.clear()` guard | apply-map-resilience #10 (added here; `fix-transition-graphic.js` could never reach it) | missing → fixed by one apply |

| `_loadMapAssets` try/catch/finally | apply-map-resilience #9 | missing → fixed by one apply |

| `transformStart` edge-swipe unlock | apply-map-resilience #3 | missing → fixed by one apply |

| `Batch._updateSpriteHighlight` `_longView` guard | **none in repo** → now apply-map-resilience #11 | missing → fixed by one apply |

| `TacticalMode._loadAtlas` try/catch/finally | **none in repo** → now apply-map-resilience #12 | missing → fixed by one apply |

| `TacticalMode._applyThemeToMap` defined-cell/`.l` check | **none in repo** → now apply-map-resilience #13 | missing → fixed by one apply |

| `TacticalMode._applyThemeToMap` guaranteed callback | **none in repo** → now apply-map-resilience #14 | missing → fixed by one apply |



So all 9 go green with the G12 apply. The 4 previously-unowned ones were authored here

against the live raw text — and the interesting part is that their guarded forms *do*

exist, in `client/www/build/script.js` (each of the 4 needles is present exactly once

there and zero times in the served bundle). The guards were written; they were simply

never carried into the bundle generation the app loads, same one-generation-behind

failure as rules 4/5/51.



**Verified offline (no bundle written):** harvesting every literal `scriptCode.includes()`

needle from `js-error-finder` gives 15 assertions against the bundle — **10 absent in the

live bundle, 0 absent after one apply**. The applied temp copy keeps the file's brace

balance identical to live (-27/-27, `#13`+`#14` are the two halves of one `try{`), gains

+2,924 bytes and passes `node --check`. Phrase kept deliberate: that is *every literal

needle satisfied*, which is strong but is not the same as the suite passing, since some of

its assertions are computed rather than literal — so "js-error-finder goes green" is a

prediction to confirm under the task #23 session, not a claim already earned.



**Decision.** `js-error-finder` stays **out** of the green gate, but for a different

reason than I first wrote: it is not a false alarm, it is an *accurate* alarm whose

scope is the game bundle rather than the agent, and every literal needle it checks is now

owned by a resilience rule — so it should go green the moment G12 is applied. Wiring a

bundle-resilience suite into the agent gate would conflate two different failure domains;

keep it as a separate pre-flight over `tools/`.



---



## G12. The live client had no map-transition resilience — a stalled map load locked the screen forever *(pipeline closed 2026-10-26; rule executed offline 2026-10-27; live stall still owed)*



**RESOLVED IN THE PIPELINE 2026-10-26, live behaviour still unproven.** The 12 rules are

now installed by `patch-engine.js` at build time and carried into `patch-rules.txt` for

`ClientBundle.cs`, so the served bundle has the watchdog and it survives a rebuild *and*

an auto-update. Evidence after the rebuild: `script.js` 5,397,421 → 5,400,345 bytes

(exactly +2,924), `node --check` parses, `apply-map-resilience.js --check` →

**`applied 14/14, applicable 0, stale 0`**, `DofusFarm.exe --bundle-selftest` → matched

`0,1,2,3,20-31` / **`RESULT: IDENTICAL`** at 5,365,150 chars on both sides, and

`js-error-finder` went **14 pass / 9 fail → 23 pass / 0 fail** for the first time. What

is *not* earned yet: watching a real stall self-heal inside 3.5 s on a live client —

that is task #23, and offline proof of a guard's presence is not proof of its behaviour.

Everything below is the diagnosis as found, kept because it explains why three

independent safeguards missed it.



Root cause of the "agent went black / stuck and needed a human" class, and the second

reason healthy runs die (after G1's AFK kick). Directly against goal criterion 3

(recover without human intervention).



**Which bundle is live.** `clientbundle.cs:BundleDir()` prefers

`desktop\client\www`, so the served bundle is

`desktop/client/www/build/script.js` (5,362,225 chars) — **not** `client/www/build/script.js`.

Those two copies are different generations, which is why greps disagreed.



**Evidence (counted in each bundle):**



| protection | live `desktop/client/www` | `client/www` |

|---|---|---|

| `_transitionWatchdog` | **0** | 6 |

| `_seriesTimer` (2500 ms series fallback) | **0** | 2 |

| `_loadMapAssets` try/catch/finally | **0** (vanilla comma-ternary tail) | 1 |

| `_guardTimer` (MapRenderer ready timer) | **absent entirely** | 5 |



`_startLoading` live also calls `this.actorManager.userActor.staticAnim()` unguarded

and its series tail dereferences `h.id` with no null check, and the tactical-theme step

is `n.tacticalMode.loadThemeMapForMap(f,i||t,h.cells,e)` with no timeout and no

try/catch — so a null `h` throws inside the series, the callback never runs,

`foreground.lock("loadMap")` is never released and `_isMapLoading` stays `true`.

Nothing in the agent can undo that: the unlock only exists in the callbacks that never

fire, and the read-only snapshot keeps reporting a locked world.



**Why the shipped rules never fix it.** `patch-rules.txt` 4/5/51 are dead needles

(rule 4 self-referential, 5/51 from a client generation that had `_guardTimer`).

Because `--bundle-selftest` treats "needle absent" as a skip and still compares to the

live file, it prints `RESULT: IDENTICAL` and hides all three — a silent no-op looks

like a pass.



**Why the working tool never ran either.** `tools/apply-map-resilience.js` targets the

correct bundle with the correct raw needles, but it is all-or-nothing and its rules #1

and #2 were written before `patch-rules` 1 landed the same handlers — so it aborted on

rule #1 and applied **zero** of the nine protections, including the five that still

matched. `tools/fix-transition-graphic.js` is the same story one step later: its

`_resetTransition` needle is a *post-#4* string, so it can only ever run after the other

tool, and its first abort also blocked its still-valid `_saveImageForTransition` guard.



**Fixed this session (tool only, bundle untouched).** `apply-map-resilience.js` now

carries a `satisfiedWhen` probe per rule, classifies APPLIED / APPLICABLE / STALE,

supports `--check` (writes nothing, non-zero on STALE), skips what is already applied

so it is idempotent, and refuses to write while any rule is stale. Rules #6 and #8 were

re-derived against the real live text (`_runLoadingFadeOutTransition` is the tween form;

`loadThemeMapForMap` is the bare call). #4 now emits the final

`catch(e){}}this._mapSceneTransitionGraphic=null` form (which is what both

`fix-transition-graphic.js` and `js-error-finder` expect, collapsing the two-step chain),

and #10 was added to own the `_saveImageForTransition` `.clear()` guard. #11-#14 were

then authored from the live raw text to own the four guards that had **no patcher at

all** (`Batch._updateSpriteHighlight`, `TacticalMode._loadAtlas`, and the two halves of

the `TacticalMode._applyThemeToMap` safe block — those two share a `group`, so a

half-applied `try{` is classified STALE instead of being written). Rules total 14.

Verified offline:



- `node tools/apply-map-resilience.js --check` → `applied 2/14, applicable 12, stale 0`, exit 0;

- apply into a temp copy → `14/14 APPLIED`, +2,924 bytes, brace balance identical to the

  original file (-27/-27), **`node --check` parses** (the unpatched baseline parses too,

  so that comparison is meaningful);

- re-evaluating `js-error-finder`'s bundle needles against that copy: 10 of its 15 literal

  bundle assertions are currently absent from the live bundle and **all 15 are satisfied

  after one apply**;

- a half-applied group is refused: opening the `#13` `try{` without its `#14` closer is

  classified `STALE / half-applied group "applyTheme" - would emit unbalanced braces`,

  which is what keeps a partial write from corrupting the bundle;

- delegated into `agent.regression` (104 checks, 0 failed), which prints the live

  applied/not-applied state instead of pretending.



**Durability — the real hole, found by running the release build.** `compile.bat` is

green (exit 0, `DofusFarm.exe` 540,160 B, `setup.exe` 78.9 MB) and the rebuild reproduced

`script.js` byte-for-byte, so the bundle the app serves *is* this repo file — the same

directory `BundleDir()` resolves. The build-time patcher is `tools/patch-engine.js`, which

reads `script.js.bak` and writes `script.js`; the runtime patcher `ClientBundle.cs` reads

`patch-rules.txt`, which `make-patch-rules.py` **generates from patch-engine.js**. Neither

knows the resilience edits, so a rebuild — or an auto-update, which replaces `script.js`

with the server's copy and is the entire reason the txt exists — silently reverts them.

Task #23's manual apply is therefore one build deep unless the rules move into the

generator's source.



Dry-run against `.bak` (read-only, nothing written) also corrects something I wrote

earlier on this page: `.bak` is **not** pristine raw. Replaying the shipped `replace`

rules over `.bak` reproduces `script.js` exactly (5,362,226 chars, byte-equal) with

**only rule 0 matching** — rules 1-3 and 6-12 already live inside `.bak`, so they are

no-ops at build time and the +64-byte delta is rule 0's guard alone. Rules 4/5/51 match

nothing anywhere, which is why they read as "skipped" rather than as missing work.



The migration is proven safe: every resilience needle is still unique after the shipped

rules run first, and `live+resilience` is byte-identical to

`.bak + shipped-rules + resilience` (+2,924 bytes, parses under `node --check`). That

equality is precisely what keeps `--bundle-selftest`'s `patched == live` contract intact

once the rules are added to `patch-engine.js` and regenerated into the txt.



**The watchdog rule is now executed offline (2026-10-27), not just grepped.** `tools/test_transition_watchdog.js`

extracts `resil22New`/`resil23New` from `tools/patch-engine.js`, asserts the served bundle carries

both **verbatim**, and runs the real rule with fake timers and a mocked IsoEngine: it arms at

3500 ms; a stalled launch (loading flag set, `foreground.locked` true) releases `loadMap` and

`quickReconnection`, removes the black-screen graphic, refreshes the scene, hides the loading

progress, resets `_isMapLoading`/`_waitingForMessage`, and warns once; a healthy map is not touched;

a relaunch clears the previous timer; and a completed transition's fade-out cancels the watchdog.

That is the *behaviour* of the shipped rule, not a presence check — 15 checks and five `watchdog:`

mutations caught. What is still owed is the live half: watch a real stalled transition self-heal in

3.5 s (and `--bundle-selftest` stay IDENTICAL with the rule set).



**Still open — needs a session, deliberately not done blind.** Run

`node tools/apply-map-resilience.js` and then prove on a real client that (a) a map

transition that never completes self-heals inside 3.5 s, (b)

`node tools/js-error-finder.js` goes from 14/9 to 23/0 — that is the prediction from the

needle sweep above, and if it does not land, the gap is a computed assertion the sweep

could not see, which is exactly the thing worth knowing, and (c) `--bundle-selftest`

still reports IDENTICAL with the new rule set, otherwise the next runtime patch reverts

the work. Also consider making `--bundle-selftest` distinguish

`already-applied` from `dead` skips so this class of silent loss cannot return.



---



## G13. A claim that ended the connection was re-sent 287 times — the scheduler's dead loop



Found by reading the baseline rather than the code. `quest.advance {quest_id: 3841,

objective_id: 14070}` was refused **287 times at a 3.7 s median gap** across two sessions,

each refusal carrying the client's own words: "a previous claim of objective 14070 of quest

3841 **ended the connection**; perform the objective instead". So the agent knew the packet

cost it the session and knocked on that door 287 more times. Criterion (3) says the

scheduler must never dead-loop; this is that failure, measured.



**Why the existing bound did not stop it** — two independent reasons, both of which had to

be fixed:

- `_claim_failures` is cleared on every scene change (`planner.py` scene reset), and a loop

  that walks between maps changes scene constantly. The counter could never reach

  `CLAIM_FAILURE_LIMIT`, so the retire branch was unreachable in practice. The code already

  knew this class existed — a comment cites objective 13493 doing "exactly what a live

  session did, for its whole budget" — and the fix then was a bound that the reset defeats.

- The learning that should have generalised the lesson keys off `obj["type_id"]`, which

  needs the objective to resolve in the knowledge base. An unknown tutorial objective

  resolves to `{}`, `int(None)` raises, the exception is swallowed, and nothing is

  remembered — for the one quest the level-3 character actually had.



**Fix: quarantine the packet, not the quest.** A note containing `ended the connection`

adds the exact `(quest_id, objective_id)` to `QuestPlanner._claim_quarantine`, which is

deliberately *not* cleared by the scene reset or by the not-ready prune, is copied into

forked planners, and routes through the same branch the server-counted types already use

(`attempts = max(attempts, 1)`) — so the objective is still performed by the action it

names, only the packet that cost a connection is withdrawn. No per-quest knowledge is

encoded: the trigger is the client's statement about its own connection, which puts this

in plumbing/safety, not policy. The learned policy still chooses what to pursue.



**Verified:** 6 gate checks (82 total, 0 failed), including a **mutation test** — with the

`_claim_quarantine.add(key)` line removed in a throwaway copy, the set stays empty and the

check fails, so the green is not vacuous. **Not verified:** that a live run now stops at

one refusal instead of 287 — that is #26, and the honest expectation is that

`recovery_report` shows the same error at most a couple of times per objective instead of

hundreds. If it still repeats, the re-issue is coming from a path this did not cover.



---



## G14. 455 repairs were labelled `unknown` — the repair table was being trained on a blank



The baseline's fifth finding said the cause taxonomy "is not yet covering what it is

reacting to". Reading it further turned up a second, worse thing: the first inference I

made from it was wrong, and the log caused the error.



**What the log appeared to say.** All 455 `cause=unknown` decision rows carry `error: null`

and `ok: true` — successful steps with nothing to go on. That reading is an artifact.



**What the code actually does.** `_emit_decision` recorded `result.ok` / `result.error` —

the *transport* result — while the controller was handed `step_ok` / `step_error`, the

*game* outcome (`step_ok = result.ok and game_error is None`). The bridge reports success

for a call the game refused, so every refusal landed in the log as a successful step with no

error text. Pairing each decision row with the `kind:"step"` row that provoked it recovers

the real distribution: all 455 are game-level failures with plain, readable error text —

281 claim refusals, 148 dialog-close TypeErrors, 25 malformed `nav.goto`, 23 fight-blocked

refusals, 27 "no client instance", 3 farm target off-map.



**Two defects, both fixed:**

1. **The row recorded the wrong outcome.** `_emit_decision` now takes the `ok`/`error` the

   controller was given and both call sites pass the game-level values; the

   "policy returned no call" branch, which previously returned a decision the log never saw,

   emits one too. Without this, *any* offline answer to "did that repair work?" reads a field

   that is structurally always null — including the training set for the (cause, strategy)

   table, which is criterion (4)'s data.

2. **`cause_of()` matched none of the phrasings in use.** It had two text families

   (`not_mapped`/`no call`, `unknown`/`not found`/`too far`) and three observation branches

   that all require `ok and b and a`. Measured consequence: `wrong_target`, `stale_state` and

   `invalid_assumption` had **zero** rows in 4,286 live decisions while `unknown` held 455 —

   buckets that imply different responses were never filled, so the Wilson-scored table could

   not rank a recovery for any of them. Added generic phrasing families (a claim the game

   refuses → `invalid_assumption`; the target is not here → `wrong_target`; the client threw

   mid-call → `stale_state`; no client at all → `unavailable`, now a declared cause), plus a

   fall-through that reads the surviving observation when there is no baseline to diff.

   `unknown` now means what it should: nothing was measured **and** nothing was reported.



**One deliberate non-change.** Relabelling a rule refusal as `invalid_assumption` would have

flipped its action from `continue` to `replan`, and `continue` is the measured-correct answer

for a refusal (the planner's fallback is the way forward; replanning on a quests prompt can

bounce the run to a farm and cost prompt fidelity). So `_decide` now treats a refusal as an

invalid assumption *in the label only*, and the action is untouched. `error_class` also stops

mis-ranking our own `refused: ` prefix as a socket hiccup — 23 live fight-blocked refusals were

being retried as if the wire had failed, which costs a step each time and sits on the G7 path.



**Verified:** 22 gate checks (121 total with G15, 0 failed) over the seven verbatim live texts, plus

`s/14070/99/` to pin that the classification is phrasing and not quest ids, and a **6-mutation

test** (`tools/mutate_cause_gate.py`) — every mutation is caught, and the section is green

again after restore. Replaying the whole live baseline through the fixed classifier moves

`unknown` 517 → 0. **Not verified:** that a live run now *recovers better*. This makes the

repair data legible and removes a wasted retry per rule refusal; whether that measurably

raises recovery success is #26, and the cause histogram of the next run is the evidence to

look at.



---



## G15. With no client window at all, the loop ground 27 doomed directives



The baseline records one session where **27 consecutive `state.snapshot` steps** all failed

with `client directive failed: no client instance is running (start one in DofusFarm

first)`, and every one of them got the same reply from the controller: `replan`. Twenty-six

replans in a row, then one `plan_wrong` — the scheduler dead-looping against an environment

that does not exist, which is the exact failure criterion (3) forbids.



**Why the existing recovery did not fire.** `_recover_world` is triggered by `fell_out`,

which is defined as *watching* the world disappear between two snapshots

(`before.in_world is not False and after.in_world is False`). With no page there is no new

snapshot at all: the bridge answers the host's own "no client instance" message and the

observation stays whatever the dead page last said. So the kick detector — the one built for

"the session ended" — cannot see the case where the whole window is gone. And because

`error_class` said `structural`, the controller's answer was a replan, which produces

another directive, which fails the same way: the loop's own retry ladder was the engine of

the grind.



**Fix: treat a missing window as a wait, the same way a missing world is a wait.** The step

branch now checks `client_absent(step_error)` — the host's three phrasings

(`ABSENT_CLIENT_MARKERS`) — *before* the settle poll runs, and calls the existing

`_recover_world(reason, trigger="client_absent")`. That reuses the machinery already

documented for logins: no deadline, ends on the stop file, and ends honestly if the bridge

itself stopped answering (`_bridge_alive`), so a killed host does not park a run forever.

The recovery is now **measured**, not printed: `_recover_world` writes a

`{"kind":"recovery", trigger, reason, recovered, waited_s}` row and the summary carries

`recoveries / recoveries_ok / recovery_wait_s`, which is what criterion (5)'s "recovery

success" needs — previously there was no number for it anywhere.



**A second bug the fix would have created, caught while testing it.** Waiting on

`_require_world` alone is wrong here: with no page the *carried* snapshot still says the

character is standing on a map, so the world check passes on the first pass, the loop

re-dispatches into the same failure, and 27 slow steps become a hot loop. So the presence

read comes first: `agent/bridge.client_count()` reads the host's `GET /agent/health`

(`instances`, no game packet on the wire), and `_wait_for_window` holds until it is

non-zero — ending on a window, on the stop file, or on a host that stopped answering. A

failed probe is `None` (unknown), never `0`, because `0` means "keep waiting" and would

park a run whose host is gone.



**Verified:** 17 gate checks (121 total, 0 failed) — functional, not source-only: the stub

whose world comes back and the one that does not (row, counters, cleared per-step state),

the presence wait against counts of 1 / 0 / None (a zero must keep waiting, with sleeps

intercepted rather than slept), the health parser against a real payload, a proxy HTML body

and an exception, and a **15-mutation test** where every mutation is caught

(`tools/mutate_cause_gate.py`), including the two that no first-draft check noticed. **Not verified:** that a live run now waits instead of

grinding. That is #26, and the specific expectation is that a session which loses its window

shows one `recovery` row with `trigger: client_absent` and *not* a run of 27 identical

failures. The wait duration itself is only knowable on a real run.



---



## G16. The recovery table was certifying itself — 348 successes in 348 tries



Started as "learn the repair choice from the now-clean cause labels" and turned into a

correction of the reward itself. Two signals in the baseline were fictions:



**1. `advanced` is mostly reads.** Of 3,063 `advanced` verdicts, **3,005 are

`state.snapshot`**. The verifier's rule is right on its own terms — a lookup that returns

data succeeded, and scoring reads as `noop` once made every query look like a failure — but

any progress or success metric that counts all `advanced` rows is counting looking, not

doing. The honest number: **58 of 1,277 steps that could change the world moved it (4.5%)**

(`quest.advance` 32/972, `nav.goto` 26/222, `farm.start` 0/59, `fight.join` 0/24). This is

now printed by `recovery_report.py` under *productive-action success* and recorded in

BASELINE.md, because the old headline (`advanced 3063`) made a stalled run look 71%

successful.



**2. A repair was credited with its own probe.** Every `recover` hint this project issues is

`state.snapshot`, and `observe_outcome` scored the repair with the *next* verdict — which is

that snapshot's. So the bandit's reward was `read succeeded`, for every cause: measured over

the live log, `incomplete:recover` was 348/348 and `stale_state:recover` 145/145, while the

same decisions credited against the next real action score **28/348 and 0/145**. A policy

trained on the first number learns "always recover", which is a tautology, not a recovery

strategy — and it would have looked like a 100% recovery success rate in exactly the metric

criterion (5) asks for.



**Fix:** the credit is deferred. `Autopilot._credit_pending(call, verdict)` ignores read-only

steps, ages the held decision out after `PENDING_CREDIT_STEPS` (12) reads so a farm wait

cannot hold a credit forever, and scores the repair against the first step that could have

moved the world. The verifier and its read-only rule are untouched — the bug was in what the

loop fed back, not in how a read is judged.



**Verified:** 6 functional gate checks (127 total, 0 failed) that drive the real method

against a stub loop — a read leaves the table empty and the decision held, 13 reads drop it,

the first action credits `incomplete:recover` once, a failed action credits a failure — plus

**18 mutations** in `tools/mutate_cause_gate.py`, all caught (re-allowing read credit fails

two checks; never aging out fails one; never delivering the credit reddens the section).

**Not verified:** any of the corrected rates live. What this changes is what the next run's

data *means*: from here, `strategies` scores answer "did the run get productive again",

which is the question criterion (4) retrains against and criterion (5) reports.



---



## G17. 480 accepted quest claims were called no-ops because the loop stopped watching too early



This was task #30 — the largest block on `objectives +0` after the labels were cleaned — and

it turned out to be an observation-timing bug, not a quest problem.



**What the log says.** 480 of 972 `quest.advance` steps are `noop`: the bridge reached the

page, the client accepted the packet (`ok: true` on all 480), and the loop recorded no change.

Measuring the steps that follow them: **the noop rows returned in a median 0.80 s** (p90 3.39),

while **the effect they had queued became visible 1.2 s later (median, p90 3.9 s)**. 131 of the

lagged cases appear within 3 s. The trajectory payloads name the mechanism directly — 251 rows

carry `data.via: "talked"` with `in_dialog: false` in the snapshot returned *with* that

directive, and the very next observation says `in_dialog: true`; another 56 say

`"walking to npc then talking"` with `npc_cell - my_cell` of 28 median (max 491: a walk across

maps). The client answers these actions before the walk-and-talk callback has run.



**Why the wait did not cover it.** `_settle` exists precisely for this, with a 6 s window for

`quest.advance`/`nav.goto`. But its early exit was:



        elif changed_keys(before, best) and now - last_change >= quiet_s: return best



`changed_keys(before, best)` compares against the **start of the step**, so any unrelated tick

(AP, HP, a cell drift, an xp number) leaves that branch permanently eligible, and `quiet_s` is

0.55 s at the default `--think`. A walk that had not started yet looks exactly like a quiet

world, so the loop returned at ~0.8 s, called the claim a no-op, and then issued the next

directive into a character that was still walking — the very interruption the docstring warns

about.



**Fix: for a tool whose promise can be checked, only the promise ends the wait.** The quiet

shortcut now applies only when the tool has no completion predicate

(`promised = tool in SETTLE_TOOLS`); `quest.advance` waits for the dialog, the objective change

or the map change, `nav.goto` for the target cell or map, `fight.join`/`farm.start` for the

fight or the auto-fight state. The 6 s / 4 s windows are the existing safety caps, unchanged.

Side finding, recorded so nobody re-adds a timeout here: for a promise-less tool the quiet

branch is **unreachable**, because `_action_complete`'s generic rule *is* `changed_keys` — such

a tool returns on the first check without polling at all, which the gate now asserts.



**Cost, stated rather than hidden.** 85 of the lagged rows never showed an effect within 13

steps, so those claims now consume their full window — roughly +5 s each, ~40 min over a

172-min baseline. Deliberate: a mislabelled no-op does not just sit still, it sends the

controller into the repair ladder (523 recovers and 407 replans in that baseline) and the

policy then re-decides against a world it has already disturbed. The metric that decides

whether the trade was right is productive-action success, not step count.



**Verified:** 4 gate checks (131 total, 0 failed) driving the real `_settle` against an injected

clock and a scripted poll sequence — a dialog that opens on the third poll is the state the step

is judged on; a promise-less tool with a visible change returns with zero polls; a promise that

never lands stays bounded by the window — and they are among the **21 mutations** in

`tools/mutate_cause_gate.py` (treating every tool as promise-less, restoring the old quiet

condition, and cutting the window below the measured latency are each caught).

**Not verified:** that live `noop` rows collapse and the productive rate rises above 4.5% —

that is #26 and it is the number to read there.



**The other half of those rows: what the loop did after a claim refusal (measured 2026-10-26).**

Reading the live agent log instead of the code answered a question the corpus could not: of the

**287** steps refused with "a previous claim … perform the objective instead", **259** of the next

steps were `quest.advance {npc_id}` — and only **8** re-sent the identical call. So the loop *obeyed*

the refusal and still ground for hundreds of steps, because obeying meant the scripted-fight fallback

`_npc_talk_calls`, which rotated the map's NPCs with `npcs[tries % len(npcs)]` and cleared `tries` on

every scene flap. The measured cycle is four NPCs of one map (3920 ×154, 3888 ×89, 3932 ×11, 3968 ×5)

asked over and over for one kill objective — 259 world-changing directives whose answer was already

known, and the reason `objectives` never moved.



**Fix: remember which NPCs were asked, not how many.** `QuestPlanner._npc_asked` maps

(quest, objective, map) to the *set of npc ids* already asked; the fallback picks the nearest

never-asked NPC and returns nothing when there is none, so the caller's real fallthrough runs

(`fight.join` at the monster once the group read works, wait for a respawn on the objective's map,

or explore). A count could be wiped and the rotation would restart on the same face; an identity set

cannot be "reset" into amnesia, which is why the scene-change reset deliberately no longer touches it

— a scene key moving is not a new answer to a question already asked. It reopens for genuine news:

an NPC the map did not show before. Bookkeeping is per objective key, so a different map or objective

starts fresh; a `fork()` inherits the memory. **10 gate checks and 4 mutations** (`rotation`: dropping

the memory, not recording the NPC asked, losing it in the fork, and re-adding the scene-flap wipe — all

CAUGHT), gate **333 / 0 failed**, driver at **136**. No quest, NPC or monster id is in the code; the

numbers above are the evidence, not the rule. Not verified: that a live run stops cycling — read it in

#26 next to the `quest.advance` volume.



---



## G18. 70% of the baseline's "steps" were the loop looking at the world, and 3,005 of them were scored as success



This was task #31 — the largest single distortion left after G16 stopped a read certifying a

*repair*, because it is about a read being an *action*.



**What the log says.** Of 4,287 steps in the autopilot baseline, **3,010 were `state.snapshot`**

— 70%, all of them `source: rule`, **3,005 with verdict `advanced`** and 5 failed. The decisions

around them confirm the shape rather than contradict it: 3,005 of 4,286 decision rows are

`continue` on tool `state.snapshot`, and **2,335 reads were preceded by a `continue` whose own

tool was a read** — a chain of looking, approving the looking, and looking again. Summing the

gap between consecutive reads inside each session: **39.0 of the 47.3 recorded step-minutes

(82%) are time between two reads.**



`state.snapshot` is what every wait in this codebase returns: the planner's dialog grace, the

tutorial gate and the spawn wait, `thinking.py`'s "wait for the NPC or a state change" / "wait

for a map change" / "wait for the target to spawn", the controller's recovery hint, and the

quests-prompt gate that turns a refused `farm.start` into a read. None of them wants a directive;

they want the next pass to look again. Dispatching it as a step did three things at once: it made

the run's success rate mostly counting looking (criterion 2's headline number), it wrote a full

observation row and a step row per poll into the training corpus (criterion 4 — a policy trained

on that learns that looking is the most reliable thing it can do), and it put ~2.7 round trips a

second on a client that had nothing to be asked for (the median gap between steps is 0.374 s, of

which the round trip is 18.2 ms: the pace is ours, not the game's).



**Where the time actually was.** The reads are not one long stall. They come in **671 runs,

median length 1, p90 5, p99 6** — a legitimate wait is short, because it stands between an action

and the state that action promised. Two runs break that shape: 149 passes (56 s) and **1,581

passes (586 s)**, together 57% of all the wait traffic. The long one is the reason this is not

just a tidy-up: 1,576 of its 1,581 reads *succeeded*, so nothing in the loop could call it a

failure — the planner simply had no action left for `do all the quests`, and the run spent ten

minutes looking at a world it was not changing while reporting `advanced` on every pass.



**Fix: a wait costs time, not a step.** The loop now takes `state.snapshot` before the guard and

hands it to `_hold_wait`, which writes a `kind: "wait"` row (goal, reason, how many passes in a

row, whether a dialog was open), sleeps the cadence the dispatched wait had (`WAIT_POLL_S = 0.35`,

chosen so sleep + one read reproduces the measured 0.374 s median, because the planner's own wait

gates count *passes* and a different cadence silently changes how long a scripted pause lasts),

re-reads the state without recording it, and continues. No step row, no verdict, no reward, no

controller call, and nothing charged to the goal's directive budget.



**The bound, because a wait can be as invisible as a step was.** Past

`MAX_CONSECUTIVE_WAITS = 40` passes (~15 s at that cadence, and 6× above the p99 legitimate wait)

the read is dispatched again, so a goal with nothing left in it goes back through the streak,

refusal and stall machinery that already knows how to retire it. That is the same behaviour the

baseline had, at 1/40th the rate: the 1,581-pass run becomes ~39 dispatched reads instead of

1,581, and the G15 dead-client case is now detected on its *first* doomed read rather than

its twenty-seventh. A wait that spans a rescue gets a fresh budget; a wait whose re-read finds the

character out of the world goes to the recovery path with its own trigger (`wait_fell_out`), which

is the check the post-step fall-out used to be the only owner of. The fall-out test itself is now

one function (`fell_out_of_world`) shared by both sites — a second copy drifting away from the

first is how a kick stops being detected.



**The bound does something else, which is the part that matters for criterion (2).** Reaching

40 passes is the strongest signal the loop has that this goal has no action left in it, so on the

way to the overflow read the loop asks `ThinkingLayer.propose` — the component that searches the

current board for an interaction nobody has tried in this scene — and dispatches what it finds.

The first version took *any* answer, and the layer's own fallback candidate is

`state.snapshot` ("refresh the observation"): accepting it reset the streak and doubled the spin,

which the gate caught as `waits 120` for two directives. So only a non-read counts

(`ESCALATE_TRIES = 3` candidates are skipped at most), the proposal still goes through

`self.guard`, and a call the schema does not know costs a refusal row and ends the goal — the

escalation cannot become the new loop, and it cannot smuggle past a safety rule.



**Escalation yield, measured on the baseline rather than asserted.** The 1,581-pass spin covers

1,592 trajectory observation rows in **3 distinct flattened scenes**, and in **3 of 3** the

thinking layer's first two offers are real actions — `fight.join`, then `farm.start` — with its

third offer being `state.snapshot`, the answer the filter now refuses. So on the one run that most

needed it, the escalation is not decoration: the spin becomes ~40 overflow reads plus a pair of

guarded attempts, and a refused `fight.join` ("no monster group on the current map") is a row with

information in it, where 1,581 reads had none. Two honest limits on that reading: the replay calls

`propose` with an empty recent-steps list and no decision, so the live diagnosis and candidate

order will differ; and `farm.start` reached this way is refused by the quests-prompt fidelity gate

(criterion 1, already pinned), so the realistic live sequence is one attempt and then retirement.

This is an offline replay of recorded state — not a live result.



**Cost, stated.** The loop still *asks* the policy every pass, so the CPU cost of an idle spin is

unchanged; what changed is the traffic, the log and the reward. A stronger fix is an explicit

`wait` intent from the planner instead of overloading a read with it — that is a vocabulary change

and it is not done here. Two consequences to watch on the next run: (a) `steps` in every summary

drops to roughly a third and the apparent success rate moves with it — the number to read is

productive-action success, which G16 established at 4.5%; (b) the idle-poll observation rows leave

the training corpus, so `state.snapshot` will be rare among labelled actions at the next retrain.

That is intended — waiting is not an action with a reward — but it does change the action prior,

and a policy that stops trying to wait would be a regression to notice, not to assume.



**Verified:** 34 gate checks (0 failed inside the current 218-check gate) driving the real loop end to end with a stub

runtime: two directives produce exactly two step rows and exactly `2 × 40` wait rows, with two

verifier labels and two controller calls over 82 policy passes — so a wait provably produces no

verdict and no reward — plus the fall-out truth table, the pacing and `--interval` precedence, the

dry-run no-sleep, a directed wait carrying the thinking layer's own sentence as its reason, the

rescue path both succeeding and giving up, and the escalation in both directions (an untried action

is dispatched under the goal that waited, a read is refused as an answer, a schema-unknown proposal

is refused by the guard, and each goal escalates for itself).

`tools/mutate_cause_gate.py` carries **42 mutations**: the 33 already run were each caught, none

missed; the 9 new ones (never escalating, not recording the escalation, accepting a read as an

escalation, and removing the per-goal reset) are **written but not yet run**, because that driver

rewrites `agent/*.py` in the working tree for seconds at a time and someone was editing the same

tree. Re-run it when nothing else is touching the checkout.

**Not verified:** any of it against a client. The numbers above are the *old* behaviour measured on

the baseline; what the new code does live is #26, and the honest expectation there is a `steps`

count near 1,300 for the same wall time, a `waits` figure in the hundreds per session, and a

productive-action rate that stops being padded by reads. One thing it deliberately does *not*

change is G1: the keep-alive is a page-side 20 s timer gated on `enabled || aiRunning`

(`scripts/mobile-disguise.js:1524`), not a by-product of dispatching, so trading 2,335 directives

for 2,335 local pauses cannot starve it — `INACTIVITY` should be unchanged, and if it rises, that

claim is what to re-examine first.



---



## G19. A repair queued for one goal was dispatched under the next goal's label



Found by accident while testing G18's escalation, which is the kind of bug you only see when you

make the loop do something new: a two-goal run escalated `farm.stop` on goal 0, dispatched it,

got `noop`, and the controller forced the same call again as a retry — then goal 0 ended on its

budget and **goal 1 began by printing `agent2 directs: farm.stop` and executing it.** Two step

rows, two `farm.stop` calls, one of them aimed at a goal that never asked for it.



**Why:** `_run`'s per-goal preamble resets the goal's own state (`failures`, the farm wait, the

no-op queue) but never `_forced_call` / `_forced_reason`, which the controller writes as a

*pending directive*. Any goal boundary crossed while one is pending carries it over — and in Auto

mode, where the loop walks a queue of goals, boundaries are crossed constantly. The step row is

logged under the new goal, so the trajectory attributes an action to a goal that did not produce

it; anything trained on that pairing learns the wrong causality, and a prompt-fidelity audit reads

an action under the wrong prompt.



**Fix: the pending directive belongs to the goal that queued it.** The preamble now clears

`_forced_call`, `_forced_reason` and `_consecutive_waits` when a goal starts. This is plumbing, not

policy: nothing is added to the action set, and no goal can inherit work it did not choose.



**Verified:** the G18 section asserts each goal escalates for itself and that the two step rows

belong to `work the 0 goal` and `work the 1 goal` respectively; 0 failed in the current 218-check gate.

**Not verified:** that a live multi-goal run never carries a forced call over — reachable in a

baseline replay, not in an offline gate. A mutation is prepared in

`tools/mutate_cause_gate.py` (removing the three reset lines) and is pending the same quiet-window

run as the G18 mutations.



---



## G20. The dialog ranker's primary signal decided nothing, and nothing recorded whether its choice worked



Found by replaying the recorded corpus through `agent/dialog_policy.py` rather than trusting its

docstring.



**What the corpus says.** The trajectory files hold **1,547 snapshots with a dialog open** —

23 with no replies, 319 with one, 1,205 with two. Running today's `rank_dialog` over all of them:

1,524 `choose`, 23 `wait`, **0 abstain** — so the ranking is not what stopped the loop. But every

one of those 1,524 choices came from the question tie-break (1,204) or from a page with a single

reply (320), and **none from `objective token overlap`, the signal the module calls primary and

"proven": it scored 0.0 on all 1,547 dialogs.** A signal that never fires is not a weak signal, it

is a missing one, and the tie-break is the whole policy in practice.



**The second half, which is the gap.** Pairing the 110 recorded reply choices with the page that

followed them (the post-action snapshot, or the next open-dialog snapshot within 20 s): **90 ended

on the same page, 19 advanced, 1 had nothing after.** `via` says the client really clicked —

`gui.npcDialogHandler._uiReplyHandler` on 108 of them — so the replies were sent and the

conversation mostly did not move, and no log line anywhere joined a choice to its outcome. That is

why criterion (4) ("replace the hardcoded heuristic where data supports it") cannot be satisfied

for dialogs yet: the module's own docstring says there are no `(question -> correct reply)`

labels, and the loop has been producing and discarding them every session.



**Also measured here, so nobody re-fixes a fixed thing:** 1,204 of the 1,547 dialog snapshots are

the **same page** ("Class-Changing Sorcerer", 2 replies) and 262 are one Ayin Spyr page — the

baseline's 1,581-pass spin (G18) sat inside an open dialog. On that recorded state the current

planner returns `quest.advance {quest_id: 3841, reply: 0, reply_text: "Say you want to change

class."}` instead of a read, so the spin was the pre-G3 planner falling through to a snapshot, not

the ranker abstaining. G18 removed the symptom's cost; this entry is about the remaining cause.



**Fix (this pass): write the label down.** `Autopilot._record_dialog` emits one `kind: "dialog"`

row per dialog action — the NPC, the page text, every reply text, which index was asked (or

`close`), **the ranker's own per-reply scores and reason**, and the outcome read from the

after-state (`advanced` / `same page` / `closed` / `unknown`, where unknown means the after-state

carried no dialog block and says nothing). `summary()` carries `dialogs` and `dialog_stalls`, and

`recovery_report.py` prints the outcome census, the per-NPC split, the reason split and the count

of choices where the primary signal actually fired — the number that was 0/1,547 offline.



**Not the fix, stated plainly:** recording is not learning. With one live session this yields the

first labelled set, and the honest next step is a scorer trained on `(page, replies, context

features) -> outcome` that subsumes the tie-break, plus a stall rule that does not re-ask a reply

whose page did not move (a general rule about dialog structure, not about any quest).



**Verified:** 19 gate checks (218 total, 0 failed) — the four outcome cases, features stored with

the pair, no row for a non-dialog step, no row with no dialog open, the call site on the step path

where both states exist, and the report reading every row kind the loop writes (including a

pre-instrument log saying *predates*, not *zero*). 5 more mutations cover it in

`tools/mutate_cause_gate.py` (**56 mutations in the driver; all 56 run and caught, 0 missed,

0 anchor-skipped, on the quiet tree 2026-10-26, and the restored tree re-runs 218/0**).

**Not verified:** that any live reply now advances a conversation — the 90/110 stall figure is a

measurement of the *old* corpus, and the first run with dialog rows is what will say whether it is

a client-path problem, a ranking problem, or a quest that needs an item rather than a reply.



**Answered offline 2026-10-26 — it is the third one.** Reading `trajectories/default.jsonl` next to

the wire log settles which of the three it was, and the answer is not the ranker:



- the page is Ayin Spyr's *"In addition, equipment provides bonus Characteristics…"*, and its

  **one** reply is `"Go and equip your new items."` (`reply_id 41457`, `reply_count 1`,

  `ids_ready`/`text_ready` both true — so no readiness race and no identity mismatch);

- the active objective is `14070`, `type: "QuestObjectiveFreeForm"`, `type_id 0`, text *`Complete

  the "A Chaotic Arrival" quest.`*, `params.parameter0 = 715354`. A free-form objective is

  satisfied by doing the thing, not by answering the NPC, so **no reply could have advanced it**;

- `knowledge.status` for it is `objective_not_in_store`: the KB does not contain quest 3841 at

  all, so the planner could not reason about the objective from data either;

- the click path is correct and demonstrably works — elsewhere in the same log the page advances

  41743 → 41741 — and 110 dispatches all report `via: gui.npcDialogHandler._uiReplyHandler`.

  In `play_dofus-05.jsonl` the wire order inside one 340 s window is

  `send NpcDialogReplyMessage{41457}` → **`recv LeaveDialogMessage`** → `send

  LeaveDialogRequestMessage null` → `send NpcGenericActionRequestMessage`: the *server* ends the

  conversation, 91 cycles of it.



So G3's "talk objectives never complete" is, in the recorded corpus, **G2 in disguise**: the

tutorial hands over equipment and asks the player to wear it, the loop has no equip verb, and it

re-asks the instruction 87 times instead. Two things follow, and neither is a quest-specific

hack: [G21](#g21-the-dialog-row-could-not-say-who-ended-the-conversation-2026-10-26) and

[G22](#g22-the-loop-asked-the-same-reply-on-the-same-page-87-times-2026-10-26). The remaining

client-side defect this trace exposes is cosmetic-to-moderate and still open: calling

`_uiReplyHandler` directly skips the real click path `_sendReply(e)` (script.js:366 disables

`replyElts[e]`, logs the reply to chat, closes the message bubble and clears `replyHandler`), so

a synthetic answer leaves the bubble and the button set live.



---



## G21. The dialog row could not say who ended the conversation *(fixed 2026-10-26, offline-verified)*



**Problem:** G20 records `outcome`, which is a comparison of two page texts. `closed` therefore

covers three different events — we asked to close it, the server ended the conversation after our

answer, or the world moved under us — and `same page` covers "the server ignored us" and "we

never sent anything". The census cannot be read without that split, and the 91-cycle trace above

is exactly the case it hides.



**Fix:** `_record_dialog` now takes the dispatch result and the row carries `reply_id` (the id

that went on the wire), `via` (which object answered), `dispatch_ok`, `error`, `objective_type`,

`page_after`, and `ended_by ∈ {asked, after_reply}`. `after_reply` means the dialog is gone on the

after-state although we asked for a reply and not a close — the server ended it. Rows written

before this still parse: the report defaults the missing fields instead of dropping the row.



**Verified:** 12 gate checks (218 total, 0 failed) — id/handler recorded, no-dispatch-data row

still written, a refused send kept as `dispatch_ok: false` with its reason, `objective_type`

travelling with the row, `page_after` empty-but-open vs closed, `ended_by` for both closers, and

the report printing the "dialog ended by" census, the type ids, the refused-send count and a

`NOTE re-asked N x` line naming the page. Eight mutations cover these fields and their report

lines (dispatch ignored, close misattributed, refused send reported as sent, objective type

dropped, after-page faked from the before-page, `after_source` not recorded, census hidden,

refused send invisible) — **all eight caught** in the 56-mutation run.



---



## G22. The loop asked the same reply on the same page 87 times *(fixed 2026-10-26, offline-verified)*



**Problem:** `_dialog_call` counted only `wait`/`abstain` passes (`_dialog_waits`, the grace that

closes an empty dialog). A `choose` reset the counter to zero — so a page that came back unchanged

was simply asked again, forever. That is the mechanism behind both the 90 s and 110 s stalls and

the 87 identical rows, and it is a criterion-(3) dead loop: no human, no progress, no end.



**Fix:** `DIALOG_ASK_LIMIT = 2` per `(npc_id, page, reply index, objective_id)`. The answer is a

function of the page, so a repeat is evidence, not bad luck: after two identical asks the third

becomes a `close`, and from then on the planner **declines the dialog action** (`_dialog_call`

returns `None`) and records `status: "exhausted"` — which the step note already prints as

`dialog_exhausted: reply already left this page unchanged`. Falling through to the rest of the

planner is the point: the objective needs a verb, not an answer, and the instrumented wait

(G18) then escalates and reports instead of grinding. A fork inherits the spent-page memory.



**Why this is not the hardcoded policy criterion (4) is about:** it keys on dialog structure and

objective identity, names no quest, no NPC and no reply text, and encodes no world knowledge — it

is the loop bound criterion (3) asks for. The negative label it consumes is now in the log:

`outcome: same page` / `ended_by: after_reply` is exactly what a trained dialog scorer should be

fitted on, and when that scorer exists it can decide *which* reply to spend, while this guard

still decides when asking is no longer an action.



**Known limits, stated:** the memory keys on `objective_id`, so an objective that progresses

without changing id keeps the page spent — the escape is the wait/escalation path, and the next

live run says whether that is enough. A legitimately revisit-able page (same objective, state

changed elsewhere) is refused after two unchanged asks. And `_uiReplyHandler` vs `_sendReply` is

still open (G3 note above), so the bubble stays lit after a synthetic answer.



**Verified:** 10 gate checks (218 total, 0 failed) driving the real `_dialog_call`: first ask,

one retry, the third ask turned into a close, the refusal after that, the `exhausted` status,

a different objective re-asking, a page that moved re-asking, and the fork inheriting the memory.

Six mutations cover it (limit raised so the loop returns, refusal falling through to asking, memo

forgetting the objective, memo forgetting the page, fork restarting, re-ask NOTE silenced) —

**all six caught**.



---



## G23. The recorded corpus cannot label a dialog choice at all — measured, not assumed *(2026-10-26)*



`tools/dialog_labels.py` reconstructs `(page, reply sent) -> outcome` from the old trajectory

logs, so the dialog scorer would not have to wait for a live run. It does not work out that way,

and the reason is worth more than the dataset:



```

dialog pairs reconstructed from 16 log file(s): 260

  by action:  {'replied': 112, 'asked': 148}

  by outcome: {'closed': 100, 'same page': 148, 'advanced': 12}

  by objective type: {'QuestObjectiveFreeForm': 258, 'GoToNpc': 1, 'FightMonster': 1}

  pages offering >1 reply: 149 of 260

  of those, the answer moved the page: 0

  choice pairs measured against a fresh read: 0, of which advanced: 0

  pairs where the OBJECTIVE moved afterwards: 1  (... with a real choice: 0)

  148 x  Class-Changing Sorcerer  reply=close  "Hello, Aiai. I'm a travelling sorcerer…"

   88 x  Ayin Spyr               reply=0      "Don't tell me you need help putting on a helmet?"

```



Three things follow.



1. **Criterion (4) for dialogs is blocked on collection, not on modelling.** Of 260 dispatches in

   the entire recorded history of the loop, none is a labelled example of a *choice* that worked.

   A ranker fitted on this set would learn to pick the reply the old heuristic already picked,

   which is imitation wearing a loss function.

2. **The negatives are not sound either.** All 149 multi-reply pairs were paired with an

   observation whose `observation_source` is `carried` (5,158 of 5,510 step rows in the corpus are

   carried; 344 are `call`), so `same page` there may mean "the answer produced a new page and we

   never re-read it". The tool reports `after_source` per pair and the census splits on it instead

   of hiding it.

3. **The wire was checked as the second source, and it agrees.** `wire_outcomes()` reads the

   `PLAY_LOG` streams (13 files) and labels each `send NpcDialogReplyMessage{replyId}` by what the

   server sent back — `QuestObjectiveValidatedMessage` / `QuestStepValidatedMessage` /

   `QuestStepInfoMessage` / `MapNpcsQuestStatusUpdateMessage` count as credited,

   `LeaveDialogMessage` as ended:



   ```

   wire labels: 144 distinct replyId(s) across 13 play log(s)

     outcome per replyId: {'dialog ended': 106, 'quest progress': 33, 'no response recorded': 5}

     pairs joined to a wire label by replyId: 112 of 260

     pairs the server credited with quest progress: 0

   ```



   (Checked separately over the written dataset: the 112 joined rows split `111 dialog ended / 1

   no response`, and the 148 unjoined rows are the close asks, which carry no reply id at all.)

   So the corpus *does* contain 33 reply ids the server credited with quest progress — and **not

   one of them is a reply the agent sent**, and none of them appears in any dialog page recorded in

   the trajectory logs either (0 pages anchor a credited id). The two halves were captured by

   different sessions, so page text and server credit never meet in one record.



**What that means for the plan, stated precisely.** This is a *capture* gap with a known closing

move, not a modelling gap: G21 already writes `page`, every reply text and the chosen `reply_id`

into one row, and the loop runs inside the client that writes `PLAY_LOG`, so on the next live

session the same replyId is joinable within one session — `tools/dialog_labels.py` then produces

(page, alternatives, credited reply) triples directly, which is the ranking supervision. Until that

run exists the honest output of this tool is the census above and the empty positive set; the

dataset it writes is a starting corpus of negatives, not a trainable ranker's data.



Also visible here: **258 of 260 dialog dispatches were made against a `QuestObjectiveFreeForm`

objective** and the two largest repeat blocks are a close-loop (148 × asking the sorcerer to

close, the null-deref path fixed in finding 4 of `BASELINE.md`) and the equip instruction

(88 ×). Neither is a ranking failure, and G22 stops both from running again.



**Status:** the reader is offline and deterministic (2.9 s over the whole corpus); the verdict is a

measurement over the recorded corpus, not a prediction about a live session. Nothing here is live

proof — it says what data exists, and it says the next live run is the first that can produce a

usable positive label.



---



## G24. Nothing started the combat brain, so no fight in the corpus was decided by the learned policy *(wired 2026-10-26, offline-verified)*



**The claim that is not true.** "Fights are played by the trained policy." The page asks the

brain for every turn and the trained policy has answered exactly **zero** live turns.



**What is measured.**



* The page dials `http://127.0.0.1:8891/v1/combat/decide` per turn

  (`scripts/mobile-disguise.js:1854`, URL default at `:2198`). On a fetch error it sets

  `_brainDownAt` and stops asking for 30 s (`:1826`), and the turn goes to `_localTick` —

  the in-page heuristic (`:1846`).

* Over the recorded corpus: **73,081 trajectory rows, 388 of them carrying

  `observation.in_fight`** (all 388 in `trajectories/default.20261026-074102-070180.jsonl`) —

  388 *observations taken while a fight was open*, i.e. every turn the loop watched was watched

  over a board the page was playing by itself. (This is a different instrument from the farm

  counters in `BASELINE.md`, which count fights, not observations.)

* `trajectories/live_policy_decisions.jsonl` holds **8 rows**: one server warm-up (`same: 7`,

  no observation) and seven written today while probing by hand — `fight_id 1234` (a payload

  assembled in the shell: its logged board has `cells len 0`) and `fight_id 99`

  (`in_fight: false, my_id: -1`, which is not a fight at all). **None is a page-supplied turn.**

* **Nothing launches the server.** `8891`/`combat_ai_server` appears exactly once across

  `desktop/ builder/ tools/ scripts/` — the page's own default URL. The host's only Python

  service spawner is `desktop/mainform.cs:342 TryStartSingletonSentinel`, and it starts the

  sentinel.

* **The interpreter the host uses cannot run it anyway.** `combat/live_policy.py` imports jax;

  the bundled `runtimes/python/python.exe` raises `ModuleNotFoundError: No module named 'jax'`.

  This machine's system python has `jax 0.12.0.1`, and the checkpoint

  `dofus-network/models/encounter_selfplay.pkl` is present. A spawn with the wrong interpreter

  exits immediately, which from outside looks identical to "never started" — hence the probe

  rather than a guess.



**What changed.** `combat/brain_service.py` (new, stdlib-only) probes the port, asks `/health`,

picks an interpreter that can actually import the brain, starts it detached and returns one of

`dry_run | reused | port_held | no_interpreter | spawn_failed | not_ready | started`. The loop

calls it once at startup and once per fight start (`agent/autopilot.py` `_ensure_brain`,

`--brain auto|off`, retry floor 300 s, `no_interpreter` and a live brain both final), writes a

`kind:"brain"` row with the interpreter command, and puts the state in the run summary. The gate

section is 28 checks; 20 mutations cover it.



**Why starting it is necessary but not sufficient.** The server was booted by hand and measured:

`/health` → `{"status":"ok","model":"encounter_ppo_pbt_actor"}`, a decide round trip 15 ms

server-side, and it fails closed — a payload with no board returns `ok=false

reason=observation_not_actionable errors=['cells_missing']`. The one payload that *was* accepted

returned `action={"type":"pass"}, reason=policy_pass` with **`contract.value_ready=false`** and

ten warnings: `self_max_ap_missing, self_max_mp_missing, self_initiative_missing,

self_spell_effects_missing:50/51, self_stats_missing, enemy_spells_missing:2,

enemy_stats_not_exposed`. `policy_pass` is a legitimate actor choice

(spell slot == the pass slot, `combat/live_policy.py:333`), not a degraded fallback — but it also

shows the value/solver refinement needs features the fake board did not have.

**That question did not have to wait for a live session**: the loop already recorded 156 real

`fight.state` snapshots, and all 156 are rejected by the same contract. See **G25**, which is now

the blocker.



**Status:** plumbing wired and mutation-covered offline. Criterion (2)'s "fights planned by the

learned policy" and criterion (4)'s "replace the heuristic" are **still owed a live fight**; a

green gate plus a `started` brain row is not a fight played. See G7 and G25.



---



**The fallback that played those turns was planning from invented numbers, and that is now fixed

(2026-10-26).** `_localTick` is what has actually decided every live fight turn so far, and

`_fightContext` built its budget as `stats.actionPointsCurrent != null ? … : 6` /

`… : 3` — the character sheet's mirror, which the client only updates for a controllable fighter

on turn confirm, with literals standing in whenever it had not. Meanwhile the fighter's own stats

bag holds the authoritative `actionPoints`/`movementPoints` for the turn in progress. Worse, the

candidate loop never compared a spell's AP cost to that budget at all: it sorted by cost descending

and cast the most expensive spell that reached, so an 8 AP spell on a 6 AP turn was offered,

refused by the client, and one of a turn's fixed 8 steps gone. Both are now real:

`_fightContext` reads the fighter bag first, then the sheet mirror, then the fighter's own maxima,

and only then the constants — and it carries `ap_source`/`mp_source` out so a live turn says which

one answered instead of silently trusting the guess. The candidate loop skips what the remaining AP

cannot buy, and stands that check aside when AP is an assumption, because an invented budget must

not be able to veto a real spell. `canCastThisSpell` remains the game's own legality gate on top.

Covered by `tools/test_fight_budget.js` (21 checks: the ladder order, zero-is-not-missing, a

throwing accessor falling through, the cost gate, the marker on the constants) plus 4 mutations

that write each regression back into the page. Still live-only: whether the fighter bag is

populated on the running build (`ap_source` answers that), and whether the budget change converts

into won fights.







## G25. The live board the page sends cannot be acted on — 156 of 156 recorded snapshots fail the contract *(measured 2026-10-26)*



**The claim that is not true.** "Once the brain is up, the learned policy decides the fight."

It answers `observation_not_actionable` for the page's own payloads, so the page falls back to

`_localTick` — the same heuristic as today, with an extra HTTP round trip.



**What is measured** (`python tools/brain_readiness.py`, over

`%LOCALAPPDATA%\DofusFarm\fight_boards.jsonl`, 156 snapshots from one real monster fight):



```

actionable=False value_ready=False: 156

errors:   self_max_hp_missing 156, enemy_max_hp_missing 445

warnings: enemy_spells_missing 290, enemy_stats_not_exposed 156, self_initiative_missing 156,

          self_max_ap_missing 156, self_max_mp_missing 156, unmodeled_effects 156, not_my_turn 92

self-fighter fields:

  max_hp: null: 156   ap: null: 156   max_ap: null: 156   mp: null: 156   max_mp: null: 156

  initiative: key absent: 156

```



`hp` *is* filled (185 for the Merkaptan Leader, 140 for its summon) and `level`, `resist_*`,

`permanent_damage_percent` are too, so the per-fighter stats object exists — the max/initiative

keys on it are simply not the ones `fight.state` reads.



**Why the nulls are the patch's, not the client's.** `fight.state` maps each fighter from

`stats.maxLifePoints / stats.actionPointsMax / stats.movementPointsMax`

(`scripts/mobile-disguise.js:5103-5113` after the fix; those reads are the ones that moved), and

those keys are absent on a fighter. Two reads

elsewhere in the same patch already get these numbers from objects it *can* see:

`_agentLife()` prefers `stats.effectiveMaxLifePoints` and treats `maxLifePoints` as the fallback

(`:2587`, added after a live session refused every fight with "0 HP; revive before fighting"), and

`state.my_stats` uses `characteristics.actionPoints.getTotalStat()` (`:5133-5160`) — which is

populated in these very snapshots (`action_points: 6, movement_points: 3`). Initiative is not read

by `fight.state` at all, so no field name is even guessed at here.



**Both halves were knowable offline, and the entry said otherwise.** This gap first read enemy

`max_hp` and per-fighter initiative as having *no proven source* — "those names exist only inside

the running client". That was wrong, and the reason is worth keeping: the recorded payload lacked

the values, and that was treated as evidence about the client. It is only evidence about the read.

The served bundle is a file on disk, and the fighter stats schema is written there literally:



```

... invisibilityState:0, initiative:0, lifePoints:0, maxActionPoints:0,

    effectiveMaxLifePoints:0, maxMovementPoints:0, movementPoints:0, ... shieldPoints:0, summoner:0 ...

```



and the turn machinery confirms the same bag is the one `fight.state` walks:

`i.data.stats.actionPoints = i.data.stats.maxActionPoints` on turn confirm. So one object, one

schema, shared by our fighter and every monster — the enemy side needed no discovery at all.

`fight.state` was reading `maxLifePoints` and `actionPointsCurrent` off it, which are the

*characteristics* sheet's names (a different bag, where `_agentLife()` and `my_stats` correctly get

those numbers), plus `actionPointsMax` and `movementPointsMax`, which occur **nowhere** in the

bundle — those two were never real names, and no fallback can reach them.



**What would close it, in order.**

1. Fill the self fighter from the sheet. **done 2026-10-26 inside `fight.state`:** the character's

   `max_hp` now falls back to `characteristics.effectiveMaxLifePoints` (then `maxLifePoints`) read

   from the `playerData` this handler already holds, and `max_ap`/`max_mp` to the `my_stats` values

   computed a few lines above. A value the client did send is never overwritten, and **no enemy

   field is invented**.

2. Capture the key names instead of guessing them — the same trick the character-sheet reader

   already uses at `:4232`, which is how that reader found its real shape. **done, and widened:**

   `fight.state` now samples all three objects a fighter is made of — `state.stat_keys` (the stats

   bag), `state.data_keys` (`fighter.data`) and `state.fighter_keys` (the fighter itself) — each as

   `{me: [...], enemy: [...]}`, once per fight id per bag, capped at 60 names, and all three land

   in the recorded board rows. `brain_readiness.py` prints them beside the fields it still reports

   missing, which is what turns the two remaining nulls into a lookup rather than a guess:

   `creature_generic_id` was null on all 11 fighters of the recorded fight and `enemy_spells_missing`

   fired 290 times, and neither name is present on the stats bag's schema.

3. Fill enemy max hp and initiative. **done 2026-10-26, from the bundle rather than from a

   guess:** `fight.state` now reads `effectiveMaxLifePoints`, `actionPoints`, `maxActionPoints`,

   `movementPoints`, `maxMovementPoints` and `initiative` off the fighter's own stats bag — the

   six names the schema above actually contains — for every fighter, monster included. `initiative`

   is newly emitted at all; it was never read before. The two characteristics names that really do

   exist (`maxLifePoints`, `actionPointsCurrent`/`movementPointsCurrent`) stay as fallbacks, and the

   two invented ones are gone instead of parked as dead fallbacks.

   The permanent guard is in `tools/test_fight_state_fields.js`: every `stats.<name>` the block

   reads must occur in `desktop/client/www/build/script.js`, which is the check that fails the day

   someone reads a name the client does not have — the two dropped names still trip it, and the

   suite asserts that they do. The emitted keys are cross-checked against

   `REQUIRED` in `tools/brain_readiness.py`, so the producer and the contract cannot be renamed

   apart.

4. Re-run `tools/brain_readiness.py` and expect `actionable=True`. **Cannot be satisfied from the

   recorded corpus**: it still prints 156/156 rejected, because those rows were captured with the

   old reads, and it now says so (`boards carrying the post-fix fields: 0 of 156`, and a verdict

   that distinguishes "this corpus predates the producer fix" from "the blocker is still open").

   The vintage test is `initiative` in the payload, which only the fixed producer emits — a

   property of the data, not a file's mtime. One live fight is what closes this step.



**Why the fill is safe without a live session.** It changes no decision the page makes: every read

comes from the stats bag `fight.state` already walks, and if a name is missing the field stays

`null` and the contract rejects the board exactly as before — a wrong name can now only fail

loudly, because the bundle-membership guard runs in the gate. `node --check` over the whole patch

and a dedicated suite (`tools/test_fight_state_fields.js`, 45 checks: the schema-shaped reads,

zero-is-not-null for a spent turn, the fallback order, no-overwrite, the per-fight cache, the cap,

the emitted-key cross-check against the contract, and the bundle guard with its own

"the guard has teeth" assertion) are offline-verified. Delivery

is not one of them: `desktop/WebView2Backend.cs:121` reads

`<exe dir>/scripts/mobile-disguise.js`, the exe sits at the repo root, so the page picks this

file up on a client reload -- no rebuild, no installer (and `desktop/aipanel.cs:437` says the

same file does nothing until the page reloads).



**How much of this is actually the page's fault — measured.** `tools/policy_over_record_boards.py`

replays the recorded boards through the trained policy with the missing values supplied (an

explicit hypothesis, printed by the tool: enemy `max_hp` := its current hp, self `max_ap/max_mp` :=

the same payload's `my_stats.action_points/movement_points`). Result over the 64 boards that were

the character's own turn:



```

as recorded, contract actionable: {'False': 156}

contract after the fill: {'actionable=True value_ready=False': 64}

reasons: {'policy_direct': 18, 'policy_pass': 46}

actions: {'cast': 18, 'pass': 46}

latency ms: p50=3.3 p95=5.5

```



So the actor is not the problem: given real geometry and its own spell slots it is actionable and

casts on 18 of 64 turns in ~3 ms, well inside the page's 4 s fetch budget. Three consequences:

* **The gate is the payload, not the model.** `actionable` requires zero errors, and the only

  errors are the missing maxima — all of which are now read from the fighter's own stats bag,

  for our character and for every monster alike.

* **Relaxing the contract would not help on its own.** `self_max_hp_missing` is an error too, and

  the page sends `max_hp: null` for the character itself, so a contract change without the JS fill

  still leaves every live board rejected. The page fix comes first.

* **`value_ready` is a separate, softer gap** (it stayed false in all 64): it gates the solver/value

  refinement, not the actor, and its warnings name the enemy-stat features the client does not

  expose at all. Treat it as a model-input task, not a fight-blocker.



Under the fill hypothesis, `pass` on 46 of 64 turns is the actor's own choice on those boards —

read it as "it does not flail", not as "it is right": the hypothesis sets every fighter's max hp

to its current hp, which flattens the remaining-fraction features the encoder was trained on.

A real fight would show a different mix; that difference is one of the things the next live

session measures.



**Status:** the producer is fixed offline against the served bundle's own schema and covered by

`tools/test_fight_state_fields.js` (49 checks, including the bundle-membership guard and the

producer↔contract key cross-check); seven of those defects are pinned by mutations that write the

old bug back into the live page and require the gate to go red. `tools/brain_readiness.py` refuses

to read a stale corpus as a current verdict. **None of it is live proof.** The next live fight must

show the six fields set on every fighter, `actionable=True`, and the three captured key samples in

the same rows; if it shows the same nulls with `initiative` present, the bundle's schema and the

running client disagree, and `stat_keys` is the row that says which one is right. Related: G24

(starting the brain), G7 (a fight proven against the server).



One thing this does **not** buy: `value_ready` stayed false in all 64 replayed own-turn boards and

its warnings (`enemy_stats_not_exposed`, `enemy_spells_missing`) are a different gap — enemy

*offensive* stats, which the fighter bag does not carry at all. The actor becomes usable; the

solver and value refinement stay gated behind that, and it is a model-input task, not a

fight-blocker.



---



## G26. `objectives_completed` was 0 in all 4,286 live rows while a claimable objective sat at index 1 *(fixed 2026-10-26, offline-verified)*



**What is measured** (`%LOCALAPPDATA%\DofusFarm\autopilot.jsonl`, 4,286 decision rows over

11.4 h; also `trajectories/default.jsonl`, 4,012 step rows):



```

objectives_completed            0 in every one of the 4,286 rows

rows with >=1 ready objective   4,286      of which >1 entry: 4,267

ready_objectives[0] was quest 3841's wrapper objective in   4,285 of 4,286

  'Complete the "A Chaotic Arrival" quest.'   2,506 rows

  'Complete the "The Onset of Problems" quest.' 1,778 rows

starved behind it, ready the whole time:

  4260/13900 QuestObjectiveGoToNpc 'Talk to Ayin Spyr'          2,092 rows

  4274/13926 QuestObjectiveGoToNpc 'Talk to Great Jarl Ordyn'   1,768 rows

character level 3 -> 4 over the same window, so the client was not frozen

```



`objectives_completed` is not an invented counter: `finishedCount` occurs 8 times in the served

bundle (`setFinishedCountById`, `o.finishedCount >= 1`, `createFinishedQuestsMap`), and the

objective-status enum in it is `{FINISHED: 0, ACTIVE: 1, CAN_BE_FINISHED: 2}` — which is what the

page already maps (`done: st === 0`, `ready: st === 2`). **It turned out not to be a usable

witness either**: see G28 — an *active* quest has no `finishedCount` property in this build, so

that total is structurally 0 and cannot separate "nothing finished" from "the counter cannot

move". The starvation is therefore proved from the ready list itself, which does not depend on

G28: `ready_objectives` carried more than one entry in 4,267 of 4,286 rows, one fixed wrapper

objective held index 0 in 4,285 of them, and the claim loop read `[:1]` — so the validation

packet for the objectives behind it was never sent, and the replay below shows exactly which

steps that cost.



**The defect.** `QuestPlanner.choose()` claimed `ready_objectives[:1]` — one entry, in whatever

order the client's `getListOfActiveQuestsId()` happened to produce. Two consequences, both

measured above:



1. the head entry was a wrapper whose own action path is a detour (its `parameter0` is not a

   group this map reports — see G27), so the step ended in `state.snapshot` or a walk out an

   exit: 2,763 of 3,582 steps in the newest session were reads issued as the action;

2. once that quest was retired into `_skipped`, the one-entry slice *ended the scan*, so no

   later ready objective was ever seen again for the rest of the session.



**The fix** (`agent/planner.py`): scan `READY_SCAN_LIMIT = 8` entries, and rank them by whether

the plan names the thing it acts on (`names_target`) — a read or "walk out an exit and look"

becomes the fallback rather than the answer. Per-entry bounds are unchanged (`_claim_attempts`,

`CLAIM_FAILURE_LIMIT`, `CLAIM_ACTION_LIMIT`), the honest wait is still returned when nothing

else is ready, and the retirement of one quest no longer ends the scan.



**Verified offline.** Replaying the live observation (wrapper at index 0, `Talk to Great Jarl

Ordyn` at index 1) through the planner: step 4 is `state.snapshot` with a one-entry scan and

`quest.advance {quest_id: 4274, npc_id: 3892}` with the shipped one — or `nav.goto {map_id:

145490435}` when the character is not on that map. `agent/regression.py` now holds

`test_ready_objective_scan()` (16 checks) and `test_session_report_census()` (10 checks); gate

**367 / 0 failed**; `tools/mutate_cause_gate.py` holds **162** mutations and the full sweep on a

quiet tree caught **162/162**, 0 missed and 0 anchor-skipped (one older anchor had gone stale when

the `unread` branch was inserted inside it — a driver `SKIP` is a broken anchor, not a blind spot,

and the sweep is what finds it). `tools/session_report.py` no longer prints a bare

`objectives +0`: it now states

`4,008 step(s) carried a ready objective and none was completed; the head of that list was

"Complete the "A Chaotic Arrival" quest." in 2,230 of them, and 4,006 step(s) had another entry

behind it`, and stays silent when the progress counter was never readable at all.



**Not verified.** That a live run completes an objective. And note that `objectives +N` is *not*

the witness for it until a run whose page build contains the G28 helper is measured — in the corpus

that predates it that counter cannot leave 0 (G28), so a zero there proves nothing either way. What

this fix does prove offline is that the claim step now reaches the objectives behind index 0; the

live readouts to re-read in the next baseline (#26) are the starvation line disappearing from

`tools/session_report.py`, the ready-list head varying between sessions instead of one wrapper

holding it for 4,285/4,286 rows, and `quest.advance {quest_id, npc_id}` calls for NPCs named by

ready objectives that were never asked before.



---



## G27. A free-form objective's `parameter0` was planned as a monster, and the client says it has no monster parameter *(fixed 2026-10-26, offline-verified)*



**What is measured.** The two wrapper objectives that held index 0 for the whole live corpus

carry `type: "QuestObjectiveFreeForm"`, `type_id: 0` and `params: {parameter0: 715354}` /

`{parameter0: 713802}`. `KILL_TYPES = {0, 6, 14, 16}` (`agent/features.py`) routes type 0 through

the kill branch, so that number is handed to `fight.join`/`farm.start` as a monster id — which is

where the recorded `target monster 715354 is not on this map` refusals come from. Searching the

id in every cached table gives nothing:



```

data/cache/monsters_full.json   0 hits   (list of 2,000 monsters, none with id 715354)

data/cache/items_full.json      0 hits   (21,554 items)

data/cache/npcs_full.json       0 hits

data/cache/quests_full.json     0 hits

```



**What the client itself says** (`desktop/client/www/build/script.js`, the objective-parameter

table the quest UI walks to resolve an objective's parameters):



```

QuestObjectiveWinMonsterInDungeonXTimes: [{type:"table", id:"Monsters", hyperlink:"$monster"}]

QuestObjectiveFreeForm:                  [{type:"getText", id:"GetText"}]

QuestObjective:                          [{type:"getText", id:"GetText"}]

```



A monster-bearing objective *declares* a Monsters-table parameter with a `$monster` hyperlink.

`QuestObjectiveFreeForm` declares only text. So `parameter0` on a free-form objective is not a

creature the client can show a group for, and no `groups_seen` census will ever make the fight

order succeed: the assumption is the defect, not the map. The same table is why `direction_hint`

and the dialog ranker read the *text* of these objectives at all.



**The fix** (`agent/features.py`, `agent/planner.py`, `agent/planner_nn.py`). The table above is

now a copy the planner consults: `OBJECTIVE_PARAMETERS` maps each of the client's 17 objective

classes to its declared parameter ids, `MONSTER_PARAMETER_TYPES` is *derived* from it, and

`declares_monster_target(obj)` answers `True` / `False` / `None` — `None` when the observation

carried no class name (0.6 % of the corpus, 174 of 28,389 entries) or a class the client does not

declare, so an older page build cannot silently lose the ability to plan a fight. Both monster

routes (`args_for_tool`'s `fight.join` and `farm.start`, and the `KILL_TYPES` branch of

`plan_objective`) now stop when the declaration says no, and the learned planner drops a call whose

arguments came back empty instead of sending a target-less join. `KILL_TYPES` keeps 0 — it encodes

what an objective *looks like* to the policy, and the feature vector's width is a trained contract —

but it is no longer a targeting rule, and one gate check pins exactly that split.



Why a copy rather than a live read: the table is a closure-local object literal in the bundle, not a

property of any runtime object, so the page cannot reach it (the dead-instrument lesson from G28

cuts both ways — a name in the bundle is not a handle). The copy is therefore *re-derived on every

gate run*: `agent/regression.py` extracts the block out of `desktop/client/www/build/script.js` and

fails unless the class set and the parameter ids match, in the client's order.



**Verified offline.** Replaying the live wrapper objective (`type: QuestObjectiveFreeForm`,

`parameter0: 715354`) through `plan_objective` on a scene whose map holds a group: pre-fix it planned

`quest.advance {quest_id: 3841, npc_id: 3976}` — "no group on this map; ask NPC to start the fight",

the fallback that ground 259 recorded steps — and `args_for_tool` handed 715354 to both `fight.join`

and `farm.start`; shipped it plans nothing, so the ready scan of G26 moves to the objective behind

the wrapper. A declared hunt is unchanged: `QuestObjectiveFightMonster` with `parameter0: 4194` and a

matching group still yields `fight.join {monster: 4194}` from both the rule planner and a stubbed

learned head. New section `test_objective_parameter_table()` (22 checks, including the bundle

cross-derivation and a functional check of the learned planner's argument plumbing) plus the extra

bucket check; gate **389 / 0 failed**; `tools/mutate_cause_gate.py` gained **13 `param:` mutations**,

all caught, and the full **175** sweep on a quiet tree caught all of them.



**Not verified.** That a live run issues fewer doomed hunt orders. And one limit worth stating

before anyone re-trains on this: the fix is keyed on the class name the page reports, and the

knowledge base does not carry one — its 15,543 objectives have `type_id`, `text`, `map_id`,

`coords` and `params`, no `type`. So `declares_monster_target` returns `None` for every KB-derived

objective and this batch therefore does **not** change the synthetic corpus: type 0 — 5,669 of those

15,543 — still gets its `parameter0` read as a monster by `ai/train_planner_student.py:build_dataset`.

Closing that half needs the client's own `type_id`↔class map, and the live corpus names only four of

the twelve types the KB uses (0→FreeForm, 1→GoToNpc, 4→DiscoverMap, 6→FightMonster), so the rest

would be invented. The honest order is G29 (KB coverage in the id space where the page can name the

class) → regenerate → retrain; no training was run in this batch.



**Blast radius, measured before touching it.** `args_for_tool` is the single place that turns an

objective into arguments, and it is shared by the rule planner (`agent/planner.py:158`), the

learned planner (`agent/planner_nn.py:111`), the corpus generator and the replay harness

(`sim/tool_choice.py:244`, `kaggle/toolkit/…`) — its `farm.start`/`fight.join` branches read

`parameter0` as a monster for *any* type, so the invented id is already inside the training data,

not only in the rule path. `KILL_TYPES = {0, 6, 14, 16}` is asserted as a contract by

`agent/regression.py` ("kill types cover {0, 6, 14, 16}"), and `OPEN_KILL_TYPES` derives from it;

the type one-hots in the feature vector are per type id and do not move. So the change was: keep the

objective-type sets as the observation encoding they are, stop *targeting* a monster unless the client

declares a monster parameter, and re-derive the labels afterwards — the first two are shipped and

gated, the third is deliberately not (see *Not verified* above). `kaggle/toolkit/` carries its own

copy of the planner and is tracked separately as G10.



**Next step.** Live measurement only: the `not on this map` refusals for these wrappers should stop,

the "no group on this map; ask NPC to start the fight" fallback should stop being spent on a

free-form wrapper, and `quests completed +N` (G28) should move on the quests that are genuinely

completable. If a live run still shows a text-only objective being hunted, the honest reading is that

the page did not carry `type` for it — 99.4 % of the corpus does, and the 174 that do not keep the

old route by design.



---



## G28. Every quest-progress number the page produced was structurally 0 *(fixed 2026-10-26, offline-verified)*



**What is measured.** Re-read over the corpus the shipped reader uses (the archived live log, the

current live log, `dofus-network/trajectories/default.jsonl`):



```

rows carrying objectives_completed           10,175   distinct values observed: {0}

rows carrying current_objective               9,795   step values observed: {0}

ready-objective entries                      18,594   -> 10 distinct (quest_id, step) pairs,

                                                      0 of them with a nonzero step

quests_active (dict quest_id -> progress)    10,175   e.g. {'3841': 0, '4260': 0}, every value 0

rows carrying quests_finished / quest_steps        0   (both keys are introduced by this fix)

```



A counter whose only observed value across 10,175 rows is 0 is not a statement about the world, it

is a broken instrument. G26's starvation is real, but that table on its own cannot distinguish

"nothing was ever completed" from "nothing was ever *reported*", and criterion (5) needs the second

question answered.



**What the client actually does** (`desktop/client/www/build/script.js` — the served bundle,

5,400,345 bytes after the 2026-10-26 release build re-applied its 11 patches; every claim below is

re-checked against that file by `tools/test_quest_progress.js`). All four

numbers used to be derived from `q.finishedCount`. In the bundle that property is *read* in two

criterion sites and *written* by one function, `setFinishedCountById`, whose only caller is the

`QuestValidatedMessage` handler — and there it runs **after** `removeActiveQuestById`, on the

finished-quests list (`createFinishedQuestsMap` is what assigns `finishedCount:`). An active quest

object therefore never carries the field, `undefined` falls through the `|| 0` defaults, and the

progress was 0 whatever the character did. The general rule this teaches is sharper than the

bundle-membership rule it came from: a name occurring in the bundle is not evidence, because the

name has to be *assigned* somewhere reachable by the object class being read — a dead-instrument

check, and `tools/test_quest_progress.js` now performs it.



The client measures the same quantity differently, and that path *is* maintained on active quests:

`o.dbQuest.stepIds.indexOf(o.stepId)`, with `stepId` written by `QuestStepInfoMessage`,

`QuestStepStartedMessage` and the step-validation assignment `n.stepId = o[a + 1]`.



**The fix** (`scripts/mobile-disguise.js`). A `_questProgress(q)` helper returns

`{index, step_id}` from `dbQuest.stepIds.indexOf(stepId)` — numeric strings coerced, an unknown

step id yielding 0, a missing `dbQuest` yielding 0 — and every quest readout now uses it:

`qs.active[qid] = prog.index`, `qs.steps[qid] = prog.step_id`, `qs.completed += prog.index`,

`qs.current.step = prog.index`. Two readouts come with it: `snap.quest_steps` (the live `stepId`

per quest, which is what a step change actually looks like even when the index cannot be resolved)

and `snap.quests_finished`, the monotone count from the client's own `getListOfFinishedQuestsId()`.

`agent/verifier.py` `STATE_KEYS` gained `quest_steps` and `quests_finished`, so a step advance

counts as "the world moved" instead of being scored `noop` — the failure that made the controller

retire a quest it had just made progress on. `tools/session_report.py` prints

`quests completed +N` from the first→max-last delta of the new counter.



**Verified offline.** `tools/test_quest_progress.js` (32 checks, delegated by

`agent/regression.py`) executes the helper straight out of the page source and pins the behaviour

(middle step → 2, first step → 0, unknown id → 0, and an object poisoned with `finishedCount: 5`

still reporting 2 from `stepIds`), the use sites in the snapshot block, the *absence* of

`finishedCount` from the active-quest readout, and the bundle proof itself (`setFinishedCountById`

= one definition, two occurrences total, write site inside the validate/addFinishedQuest path).

The G28 gate cases in `agent/regression.py` cover `STATE_KEYS` and the `_objective_in` tracker

fallback. Gate **367 checks / 0 failed**, and it was re-run *after* `compile.bat` so the assertions

hold against the regenerated bundle (build exit 0, `setup.exe` 79.0 MB, 11 patches active,

`tools/js-error-finder.js` 23/0). The full mutation sweep on a quiet tree caught **162/162**,

0 missed, 0 anchor-skipped.



**Not verified.** That a live run reports a nonzero step or a completed quest. This fix makes the

question answerable; it does not answer it. The live readouts for the next baseline (#26) are

`quests_active` holding a nonzero value, `quests completed +N` with N > 0, `quest_steps` changing

within a session, and the G26 starvation line disappearing — and they are only meaningful in a run

whose page build contains this helper.



---



## G29. The quest knowledge cache cannot describe any live quest: its ids stop at 2,546, the live ids start at 3,841 *(capture read + observed knowledge shipped 2026-10-27, offline-verified; live population owed)*



**What is measured.** `data/cache/quests_full.json` holds 1,976 quest records whose distinct ids

span **18 … 2,546**, with **0** records above 2,546. Across 18,594 ready-objective reads the live

client names **7** quest ids — 3841, 4260, 4266, 4274, 4278, 4604, 4644 — and **none of them is in

the cache**. The objective ids the same reads carry (13926, 14070, …) are five digits, i.e. also

above the cache's maximum. The `knowledge.status` a live observation carries is the same story from

the other side: over the 8,777 rows the enrichment touches, **5,210** are

`objective_not_in_store` — a miss that is routine, not exceptional.



**Why it blocks the goal.** Criteria (2) and (4) route planning through the knowledge base: the

curriculum the learned policy is trained on, and the lookup that supplies NPC/monster/map ids when

the page cannot. Not one live **quest** id is in it (0/7), so the KB has never described a quest the

character is doing. Its *objective* table does contain 23 of the 26 live objective ids — and that is

worse, not better: those rows belong to other quests of another game, and the loop believed them

(G30, fixed). So `objective_not_in_store` (5,210 of the recorded rows) is the honest subset; the rest

were false hits. `objective_not_in_store` for quest 3841's tutorial objective 14070, recorded in G20,

was never a stale entry — it is a cache whose quest space does not overlap this server's. The sibling

caches tell the same story: `monsters_full.json` holds 2,000 ids in 31…4,064, containing neither the

free-form wrappers' six-digit numbers nor the real monster ids the client resolves through its

Monsters table (4194, 4192). So *absence from these caches is not evidence about what an id is*; only

the client's declared parameter table is (G27). The planner therefore runs on the page's own

objective entries, and where those are thin it guesses.



**What decides it.** `data/sync_full_database.py` built every one of these caches, and it has one

base URL — `https://api.dofusdb.fr`, with `$limit/$skip` pagination and **no game selector** — while

its own docstring claims "Dofus Touch and Dofus 2.x". So the mismatch is cause (a): a single

encyclopedia namespace was fetched for a client that runs in another one. Cause (b) is not supported:

`sync_quests(max_quests=3000)` stopped at 1,976 records, so the cap never fired — whether the source

held more is not measurable from this tree, and it does not matter while the ids do not overlap.



**What is not concluded.** What the Touch quest/objective tables actually contain — they are not on

this machine in any form the KB can read. The client itself resolves them (that is what

`dbQuest`/`dbObjectives` are), so the live page can name the class of an objective it is holding

(99.4 % of the corpus does) but not the whole quest graph. Two honest routes: build the KB from what

the running client reports, objective by objective, as the observed-knowledge store already does for

`npc_map`; or find the Touch encyclopedia source and re-sync against it. Neither is a per-quest

answer, and neither is chosen for you by this document.



**The capture is shipped (2026-10-27), and it is a read rather than a download.** The client carries

the Touch quest graph locally: its database manifest contains `Quests`, `QuestSteps`,

`QuestObjectives`, `QuestObjectiveTypes`, `QuestStepRewards` and `QuestCategory`, and each active

quest object carries its own `dbQuest` (`stepIds`, `nameId`), `dbSteps` (`objectiveIds`, `nameId`,

`descriptionId`) and `dbObjectives` (`typeId`/`_type`, `parameters`, `mapId`, `stepId`, `text`). The

page now publishes that graph:



- `snap.quests_catalog` — up to 12 active quests, each with its id, display name, `step_ids` in the

  client's own order, the live `step_index`/`step_id`, up to 40 steps (name, description,

  `objective_ids`) and up to 40 objectives per step (type id + class name, declared parameters, map,

  step, text, completion). Objectives the client holds but no step lists yet — the table is filled

  asynchronously — are kept in an `unlisted` step, up to 20. **One budget is shared by the whole

  catalogue (60 steps / 80 objectives across all active quests):** a per-quest cap alone allows

  12 × 40 × 40 = 19,200 objective rows in every snapshot, which would bloat the log and the bridge

  payload. Every bound is reported in

  `snap.quest_catalog_status` (`quests`, `steps`, `objectives`, `truncated`), which is the census a

  live run reads first: 0 means the capture found nothing, not that the client has nothing.

- `agent/knowledge_context.py` prefers a matching observed row over the store. Matching is by the

  tracker's quest id **and** objective id, so a shared id cannot resolve to a different quest; the

  block is `status: "observed"`, `objective.source: "observed"`, and carries the client's text, type

  id/class, params, map, step and the quest/step names. The store path is unchanged when the

  catalogue has no row.

- `agent/planner.py` reads the catalogue in `_observed_current`, so an active quest whose

  `step_objectives` is empty is still plannable from the client's own graph; `catalog_objectives`

  returns rows for the named quest only.



This also removes a false lead: the old fallback read `window.gui.databases.QuestObjectives`, which

the bundle never assigns — the manifest name is not a page handle. The capture reads each quest's

own `dbObjectives` instead (the table the page already prefers). The training/prompt renderer

(`agent/tool_dataset._observation_text`) shows a `quest graph: N quest(s), M objective(s)` census and

excludes the raw graph, so the new field cannot crowd `knowledge`, `quests_active` or the build stamp

out of the 1,200-character text used by the encoder/LLM policies.



**Evidence (offline).** `tools/test_quest_catalog.js` 26 checks: bundle membership for `dbQuest`,

`dbSteps`, `dbObjectives`, `objectiveIds`, `descriptionId`, `stepIds`; step order from `stepIds`;

type/params/map/step/text on the rows; the async-only rows; every bound; a throwing quest yields an

`error` row instead of an exception; the snapshot build block is executed and counted; the block

sends nothing. `test_observed_quest_catalog` in `agent/regression.py` pins the precedence (observed

beats a colliding store row), the names, the no-catalogue fallback, the wrong-quest refusal, a

malformed catalogue, and the planner fallback. `test_observed_quest_ingest` pins the flatten/merge

rules, malformed-line tolerance, coverage counts, the persisted lookup, ambiguity and cache

invalidation. Gate **502 checks / 0 failed**; the ten `catalog:` mutations and the ten `ingest:`

mutations were each run as a batch, **10/10 caught**, restored tree re-ran 502/0;

`node tools/js-error-finder.js` 23/0.



**Not verified (live).** No recorded row carries `quests_catalog` yet — the read is newer than the

corpus — so whether a real active quest's `dbSteps`/`dbObjectives` are populated, and with which

text, is the first thing the next snapshot answers; `quest_catalog_status` is the witness.



**The ingest is shipped as a separate artifact (2026-10-27).** `tools/ingest_observed_quests.py`

scans recorded logs for `quests_catalog`, flattens one row per objective (quest/step names, type

id/class, params, map, step, text, completion, session/log/time), merges without letting a thinner

later capture erase a richer row, writes `data/knowledge/observed_quests.jsonl` atomically, and

prints a coverage verdict against the store (`new` / `agrees` / `collision`). The download store is

untouched: observed rows are a separate, licence-clean artifact. `agent/knowledge_context.py` loads

that file through an mtime/size-cached index when a snapshot does not carry the catalogue itself, so

a loop started before the page reload still resolves; an objective id claimed by two quests is

refused unless the tracker names the quest. **Still owed:** one live session to populate the file

(the coverage tool prints "predate the capture" until then), and a decision on merging observed rows

into the SQLite store for training/curriculum — the runtime path and the JSONL do not require that

merge.



**And the download route is closed by the source's own licence, not by convenience.** Checked

2026-10-26: `https://api.dofusdb.fr/` (the base URL `data/sync_full_database.py` uses) now serves its

own root page as **"LPNC-IA 1.0 / NCPUL-AI 1.0 — Non-Commercial Public Use License — No Artificial

Intelligence"**, whose §4.2 forbids using the API's data to train or fine-tune any model, algorithm or

AI system, forbids feeding it to "any AI system, autonomous agent or machine-learning bot", forbids

embedding the API in an AI-driven automated pipeline, and §6 terminates the licence and demands

deletion of the derived data on violation. This project is the prohibited shape already: those caches

build `data/knowledge/dofus_knowledge.sqlite`, which `agent/knowledge_context.py` attaches to every

observation of an autonomous agent, and the same tables fed the tool-policy retraining. Probing for a

Touch namespace found `https://api.dofusdb.fr/touch/quests` → **404** and `/quests` → the same 1,976

Dofus 2 records, so there is no "just re-sync the right game" fix even before the licence question.

Consequences taken here: no further fetches from that host, and the client-side capture above becomes

the only route for G29 — it is licence-clean because the data comes from the software the user is

running. Whether the existing caches and the store built from them stay in the tree is the user's call

(criterion: nothing is deleted on a judgement call), and G31 is the first place where the wrong-game

table was *driving live requests*, so that path is now closed regardless.



---



## G30. The knowledge store resolved live objective ids to *another game's* objectives, and the loop believed it *(fixed 2026-10-26, offline-verified)*



**What is measured.** `data/knowledge/dofus_knowledge.sqlite` (44 MB, built by `knowledge/build.py`

from the same Dofus 2 caches as G29) has no live *quest* — 0/7 of the ids the client names — but its

`quest_objectives` table does contain **23 of the 26** live objective ids. Resolving one of those ids

therefore succeeds, and it succeeds with content from a different quest in a different game:



```

live (quest 4260, objective 13900) -> store quest 2007, type_id 6, "Vaincre x1 {monster,5243} en un seul combat"

   while the client says that objective is QuestObjectiveGoToNpc (type_id 1), parameter0 = 3888

live (quest 3841, objective 13543) -> store quest 1946, type_id 3, "Ramener à {npc,4211} : x1 {item,19425}"

   while the client says QuestObjectiveFightMonster (type_id 6)

distinct live (quest, objective) pairs the store resolved ....... 23

store quest id agreeing with the client's ......................... 0

store type_id disagreeing with the client's ...................... 17 of 23

live rows enriched as `status: "resolved"` on foreign content .... 3,567 of 8,777

```



**Why that is worse than a miss.** `agent/knowledge_context.py` published the block as

`{"status": "resolved", …}` and preferred **the store's** quest id over the tracker's, and

`agent/dialog_policy.py:_quest_context` builds the ranking text from `quest_name`/`step_name`/

`objective.text` *first*, falling back to the client's own text only when the block carries nothing.

So for 41 % of the recorded rows the dialog ranker scored NPC replies against another game's

objective, the dialog row's `quest_id` (the label G20/G23 record and G34 would train on) named the

wrong quest, and a `knowledge.search` answer looked like a hit. Nothing in the loop could tell the

difference, because every field was well-formed.



**The fix.** An identity check, no per-quest data: when the tracker names a quest and the store finds

the same objective id under a different one, the block becomes

`{"status": "objective_id_collision", objective_id, quest_id (the client's), store_quest_id,

store_type_id}` and publishes **no** text, names or parameters — `_quest_context` then falls through

to what the client reported, and `_quest_id` keeps the client's quest. When the two agree (the

Dofus 2 corpus, and any future KB that matches this server), the block resolves exactly as before:

the check is about identity, not about trusting the store less.



**Verified offline.** Over the live corpus, `knowledge.status` is now

`{objective_not_in_store: 5,210, objective_id_collision: 3,567}` with zero `resolved` rows carrying

foreign content, and the agreeing case (`resolve_objective(13900)` under the store's own quest 2007)

still returns its text. Gated by `test_knowledge_identity()` (18 checks: the collision path publishes

no text/names/parameters and keeps both quest ids plus `store_type_id`; the agreeing path still resolves;

the miss path is unchanged; a tracker with no quest id is not called a collision; the ready-objectives

route is guarded too; the cached block is proven keyed per tracker quest; and `dialog_policy.rank_dialog`

is shown choosing the *client's* reply with the block withheld and the *store's* reply with the

pre-fix block hand-built). Covered by 9 `identity:` mutations on `agent/knowledge_context.py` (a new

driver target) plus one on the `autopilot.py` dialog row, all caught.



**Not verified.** What the collision cost a real session — the recorded corpus predates the guard, and

the dialog rows in it were labelled with the wrong quest id, which is one reason G34's labels must be

re-derived after the next live run rather than reused. The fix also does not make the KB *useful* for

live content; it stops it from lying. Coverage is G29 (task #49), and the honest route there remains

the client's own tables.



---



## G31. All 366 live quest-start requests named a quest this game has never shown *(fixed 2026-10-26, offline-verified; the first count of this gap was wrong and is corrected below)*



**What is measured.** Over both live corpora (`%LOCALAPPDATA%\DofusFarm\autopilot.jsonl` plus the

rotated 2026-10-25 file; 11,395 rows, 66 sessions), `quest.advance` rows classified by their **whole**

argument set — the shape that decides which page branch runs:



```

quest.advance rows ...................................... 2,983

  no quest_id at all (bare auto-advance) .................. 1,490

  quest_id + npc_id      -> talk to that NPC .............. 519

  quest_id + objective_id -> QuestObjectiveValidationMessage 348

  quest_id + reply/reply_text -> answer the open dialog .... 112

  quest_id + close       -> leave the dialog .............. 148

  quest_id only          -> the accept-a-quest route ...... 366

    quest id the client never reported, that snapshot ..... 366  (100 %)

    quest id the client never reported, that session ...... 366  (same number)

    distinct refused ids .................................. 16

    top offenders ....... 584 x297, 727 x11, 724 x10, 720 x7, 726 x6

```



**The correction, because it is the more instructive finding.** This section first read "462 of 1,145

start requests". The filter behind it was *any* row carrying a `quest_id` with no `objective_id`, and

1,145 is exactly `talk 519 + reply 112 + close 148 + start 366` — it counted conversations and claims as

accept attempts, so the "683 legitimate starts" line did not exist either: **no** recorded start was

client-confirmed. The absolute cost is smaller than claimed (366 rows, 3.21 % of recorded rows, not 462

rows at 8.08 %), the *share* is worse (100 % of starts, not 40 %), and the traffic the old number

attributed to doomed accepts is really the talk/claim/dialog traffic that G17, G22 and G2 are about.

`tools/quest_advance_shapes.py` is now the single place that classifies a call's shape, `test_quest_advance_shapes`

pins it, and the report line reads from it, so a future pass cannot re-count a conversation as a start.



What it cost, in the same rows:



```

rows in both corpora .................................... 11,395

starts as a share of all rows ........................... 366 = 3.21 %

sessions that sent at least one ......................... 31 of 66

their share of their own rows, mean ..................... 28.1 %

the one session that spent most of itself on it ......... 119 starts

consecutive repeats of the SAME refused id .............. 310

```



One session sent a third of every doomed request in the corpus, and 310 of the 366 were immediate repeats

of the previous refused id — the direct fingerprint of a bound that re-arms itself.



**Why `START_ATTEMPT_LIMIT = 3` did not stop it.** `_note_situation()` clears `_start_failures` and

`_start_waits` whenever the scene key changes and clears `_skipped` whenever the quest key changes —

both correct for *retriable* failures, and together they re-armed the doomed request on every map step.

That is the same defect class as the 259-step NPC re-ask, which was fixed by deliberately *not* clearing

one memory across scenes; the 297 sends of quest 584 are the same mechanism on the accept route.



**The fix.** `client_quest_ids(obs)` reads the four places the client names a quest (the active log, the

finished list, the tracker, the ready entries) and `choose()` unions them into a session-scoped

`self._client_quests`, which the start route consults before sending: a quest id this game has never

shown gets no request and is retired. Nothing names a quest in advance — the set is built from the

observation, so an id the client reports later becomes startable, and `fork()` inherits the set (a twin

that forgot it would re-ask). The refusal is *not* sent to the client at all: the runtime logs

`goal_unresolved` and issues no directive, which is the difference between a wasted request and a

request the server has to reject. Full Auto is unaffected on the productive side — with nothing

client-confirmed to start, `mode == "auto"` still falls through to `farm.start`, while a quests prompt

sends nothing (measured in the gate).



**Verified offline.** `test_client_confirmed_starts()` (13 checks) proves the refusal, the persistence

across four scene changes, the refusal surviving a wiped `_skipped` (so it is the client memory doing

the work, not the retirement cache), that a client-reported quest **is** still requested by id, that a

fork inherits the set, and the two source pins. The pre-G31 sequence in the ready-scan fixture

(`quest.advance {584}` on three consecutive steps) is now `None, None, None` — the same three steps

that used to send three refused requests now send nothing, which is what `alone` asserts.



**Not verified.** The reduction itself is offline arithmetic on rows the old loop wrote; no run has

yet been observed under the guard, so "366 refused requests become `goal_unresolved` steps" is a

prediction, not a measurement. The honour question — *did any start ever work* — now has an answer

from the other corpus, and it is no: the recorded trajectory files (73,131 rows, the ones that carry

the full nested observation) hold **15 true start requests, all quest 584, and not one of them appears

in `quests_active` within the next five steps**; 12 were repeats inside a five-second burst, 5 never

reached a client, and exactly **1** row in the whole corpus shows the packet actually leaving

(`via: "QuestStartRequestMessage"`, 2026-10-26 09:26, a level-3 character whose observation said

`quests_active {}`, `ready_objectives []`, `current_objective null`). That row predates this guard by

twelve hours, so it is not evidence the guard failed — it is evidence the accept route works at the

wire level and the server declined it, which is what G32 is about. The autopilot corpus still cannot

answer honour at all: its `obs_before` carries only 13 keys —

`cell, current_objective, dialog, hp, in_dialog, in_fight, kamas, knowledge, map_id,

objectives_completed, ready_objectives, tutorial, xp` — and **no `quests_active` and no

`quests_done`** (a first pass that read the missing key as an empty dict produced a fake 0/1,131 honour

rate and had to be retracted). The guard's own comparison set is therefore built from the fields the

client does report — the ready entries and the tracker — which is a conservative definition of

"this game has shown me this quest". The G28 page rebuild emits `quests_active`/`quest_steps`/

`quests_finished` on every snapshot, so the next run can answer the honour question directly; the

366 "never reported" verdict is measured against the fields that *are* recorded, and 0 of the 366 can

be reclassified as plausible. And the guard is deliberately a step **back**

in capability: a quests prompt with nothing client-confirmed to start now sends nothing at all, and a

character that genuinely needs to accept a new quest cannot do it from a table that does not describe

this game — the honest route is the NPC dialog offer (G32) plus the G29 capture.

Tasks #51 (shipped), #52 (the counter, now `tools/quest_advance_shapes.py`), #53 (G32).



---



## G32. The quest offer is a link in the NPC's own text, and the page was deleting it *(read half fixed 2026-10-26, offline-verified; the accept half is G33)*



**What the bundle says.** The dialog view parses `/<a\shref=['"](.*?)['"]\s*>(.*?)<\/a>/gi` out of the

question text and hands every href to `_linkHandler`, which splits on `,` and knows exactly three

payloads — `startquest,<questId>` → `QuestStartRequestMessage` then close,

`validateobjective,<questId>,<objectiveId>` → `QuestObjectiveValidationMessage` then close,

`map,<worldX>,<worldY>` → a compass update. So in this client an offer is not a reply button: it is a

**link inside the NPC's text**. The page publishes `dialog.message` through `_agentPlainText`, which

strips every tag — the offer was deleted before the policy could see it, and `quest.advance {quest_id}`

was the guess that filled the gap (366 of them, G31).



**The read half (shipped).** `_agentDialogInfo` now also carries `raw_msg` (the un-stripped text), and

four helpers publish what the client itself can see: `dialog.links` (every payload, parsed with the

client's own anchor regex, kinds `startquest` / `validateobjective` / `map` / `other`, attributed

`from: message | reply` with its `reply_index`, capped at 12), `dialog.start_quest` (the first positive

numeric quest id the page is being *offered*, else null) and `dialog.link_handler` (which object owns

`_linkHandler`, so a future verb can call it with the right `this`). The read block sends nothing —

that is a gate assertion, not a style choice, because a read that also clicks is unreadable.

`tools/test_dialog_links.js` (32 checks) pins bundle membership for each name, **equivalence with the

client's own regex match count** on 7 samples (including the two forms neither matches: attribute before

`href`, and an unquoted href), the payload/kind behaviour, the attribution, the owner detection and the

source pins; 9 `links:` mutations cover it.



**Not verified, and deliberately so.** No recorded row carries `links` or `start_quest` yet — 0 of

9,036 — because the read is newer than the corpus. Whether the Touch server actually puts a

`startquest,<id>` link in the dialogs this character reaches is still the first thing the next live

snapshot has to answer. The accept half is now wired *behind that read* (G33): it sends

`QuestStartRequestMessage` only when the dialog's own parsed `start_quest` equals the id asked for,

and refuses with `quest_not_offered` otherwise, so with no offer the branch is inert and no shape is

invented. A quest/auto run now takes the offer (G33), so the read has a live consumer the moment a

dialog carries one.



---



## G33. `quest.advance` cannot accept a quest while a dialog is open — the accept branch is behind it *(fixed 2026-10-27, offline-verified; live proof owed)*



**What is measured.** Reading `scripts/mobile-disguise.js` against the corpus: the handler's first

statement is `if (pd0 && pd0.isInDialog) { … }`, and inside it only two argument shapes are handled —

`close` (leave the conversation) and everything else, which becomes `var want = parseInt(args.reply) ||

0` — **answer reply index 0**. The `QuestStartRequestMessage` branch is *after* that block, so it is

reachable only when no dialog is open. Two consequences, both structural rather than tuning:



1. A quest the NPC is offering right now — the only place the client renders an offer (G32) — cannot be

   accepted by the verb that accepts quests. Standing in front of the NPC with the dialog open, a

   `quest.advance {quest_id}` answers the first reply instead.

2. `QuestPlanner._tutorial_call` emits exactly that shape when `obs.in_dialog` is true

   (`ToolCall("quest.advance", {"quest_id": qid})`, no reply), so every such step silently means "click

   reply 0". That is a candidate mechanism for the page the loop re-answered 87 times (G22/G3), and it

   is why "which call was this" must be answered from the whole argument set plus the dialog state —

   which is what `tools/quest_advance_shapes.py` now reports (`while_dialog_open`).



The recorded corpus has **0** start-shaped rows sent inside a dialog (15/15 came from outside, G31), so

this is not a measured cost yet — it is the reason the G32 offer cannot simply be routed to the existing

verb, and it must be fixed before the accept half of #53 can be wired.



**The shape of the fix, once one live snapshot proves the offer exists.** Either give the verb an

explicit accept argument that runs *before* the reply path (send `QuestStartRequestMessage{questId}`,

then close through the two already-verified close paths, and refuse unless `dialog.start_quest` equals

the id asked for — so a stale or invented id cannot reach the wire), or call the client's own

`_linkHandler('startquest,<id>')` on the owner `dialog.link_handler` already identifies. The first

reuses the packet and guards that are already gated; the second is more faithful to the client but

reaches into a private prototype. Both keep G31's rule: **the planner may only ask for an id the client

offered**, never a curriculum or KB id.



**What shipped (2026-10-27), the first shape with the offer as its guard.** The page's `quest.advance`

now handles `accept: true` *before* `close` and the reply path. `_agentDialogState().start_quest` — the

client's own `startquest,<id>` payload (G32) — must equal `quest_id`; anything else returns

`quest_not_offered` with both ids and sends nothing. On a match it sends

`QuestStartRequestMessage{questId}`, arms the same 5 s in-flight window the outside-dialog start uses,

closes through the two verified close paths (each in its own try) and reports `closed` from dialog

state, not from the calls. A start-shaped call inside a dialog for a quest the client has never

accepted is refused (`dialog_start_not_accept`) instead of being answered as reply 0; a known quest

keeps reply 0, which is what the tutorial relies on.



The planner takes the offer when it is the quest the plan is on, and — added 2026-10-27 — when the

run is doing quests at all: `_dialog_call` accepts `dialog.start_quest` when it equals

`current_quest` or the ranker's quest, or when the goal mode is `quest`/`auto`, the offered quest is

not already active, a quest id named in the prompt (when there is one) matches, and no different

`focus` is set. It records the id as pending and

client-confirmed, so a quest the server did not honour can be retried outside the dialog. An order

or farm prompt does **not** acquire quests (fidelity), a named focus must match, and an offer for an

already-active quest is left to the ranker. The tutorial accepts when the offer equals its step quest

and otherwise sends an explicit `reply: 0`; the start branch accepts a matching offer or closes

first when a dialog offers something else. Nothing there emits a bare start inside a dialog any

more. `schema.py` declares `accept`, so the validator no longer

rejects the call before the page sees it, and `autopilot._dialog_outcome` now logs

`asked: "accept"` / `ended_by: "accept"` / `accepted_offer`, which is the first live label this path

can produce.



**Evidence (offline).** `tools/test_quest_accept.js` (27 checks: order before the reply path, packet

name and id, both close paths, state-judged close, wrong-id refusal, no-offer refusal, the 5 s

in-flight window, send failure, the start-shaped refusal and the schema pin).

`agent/regression.py` adds `test_quest_accept_planner` (offer read, accept shape, the new-offer rule

for quest/auto prompts, refusal for farm/order/focus/already-active offers, pending and

client-confirmed bookkeeping, close-first fallback, both tutorial shapes, and `validate_call` under

the runtime `data/capabilities.json` registry — the contract that actually gates a live call) and

`test_quest_chain_replay` (7 checks: an NPC offer is accepted, the accepted quest is

client-confirmed, the now-active live quest is planned from the observation rather than retired, the

next offer in the chain is accepted, and farm/named-different prompts leave it alone). Gate

**472 checks / 0 failed** when this batch first landed (the gate has since grown to 580/0 with the

G29 batches and the new-offer rule); the ten G33 `accept:` mutations and the seven `offer:`

mutations in `tools/mutate_cause_gate.py` each ran as a batch, **10/10** and **7/7 caught**,

restored tree re-ran the gate. `node tools/js-error-finder.js` stays 23/0 on the edited page.



**Not verified (live).** No live snapshot has carried `links`/`start_quest` yet (0 of 9,036), so this

branch has never fired against the server; the first real offer is the proof. Also owed: whether the

server itself closes the dialog after `QuestStartRequestMessage` (the page reports `closed` either

way), and whether an explicit `reply: 0` on a known tutorial dialog behaves exactly like the old

default (offline it is the same packet).



---



## Priority, if these were ordered by what blocks a real run



1. **G1** keep-alive — it is why healthy runs still die. Implemented; the live logs show

   `INACTIVITY` in 5,106 observation rows, which is the mechanism confirmed, not the fix.

2. **G13** the 287-refusal claim loop and **G15** the 27-step no-window grind — both

   measured dead-loops against criterion 3, both fixed behind the mutation-tested gate; one

   live run shows whether either still repeats.

3. **G12** map-transition resilience — **closed in the pipeline 2026-10-26** (rules in

   patch-engine + patch-rules, bundle rebuilt, selftest IDENTICAL, js-error-finder 23/0);

   watching a real stall self-heal is still owed.

4. **G3** dialog reasoning — root-caused offline 2026-10-26, and it was never the ranker: the

   page it re-answered 87 times says *"Go and equip your new items."* and the objective behind it

   is `QuestObjectiveFreeForm` (`objective 14070`, `knowledge: objective_not_in_store`), which no

   reply can satisfy. **G22** bounds that re-ask (two asks, then a close, then a refusal) and

   **G21** labels who ended the conversation; both are behind the gate, neither is live proof.

5. **G2** equip — promoted by that trace: it is the missing verb that makes the first quest chain

   completable, and the observation already carries `inventory` plus the objective's

   `parameter0 = 715354`. Mechanism researched; wiring still needs the live capture it was gated on.

6. **G24/G25 the learned policy has never decided a turn — and now the reason is measured.** The

   loop starts the brain and records it (wired, gated). Separately, 156 recorded live boards all

   failed the observation contract (`max_hp/ap/mp` null on every fighter including the character,

   `initiative` absent), so the page kept its heuristic even with the brain running. Cause found

   and fixed offline: `fight.state` read the characteristics sheet's names off the fighter stats

   bag, and the bundle contains the real ones — all six are now read, for monsters as well as for

   the character. What is left is one live fight: the recorded corpus predates the fix and cannot

   show it, and `stat_keys` per fighter is kept as the cross-check in case the running client

   disagrees with the bundle. This is what criterion (2)'s "fights using the learned policy" and

   criterion (4) both actually gate on. Replay measurement says the model is ready: 18 casts of 64

   own turns at p50 3.3 ms once the values exist.

7. **G14** repair-label taxonomy — the 455 `unknown` rows are now classified and the

   decision row records the game outcome; the data criterion (4) trains on exists. Needs the

   next run's histogram to show it helps.

8. **G18** the read-as-action step mix — 3,010 of 4,287 baseline steps were looking, 3,005

   of them scored `advanced`. A wait now costs time instead of a step, bounded at 40 passes.

   This is the number that makes criterion (2) readable at all: read `steps`, `waits` and the

   productive rate together, not `steps` alone.

9. **G8** test gate — **922 checks** (2026-10-27), delegating JS + Go + bundle `--check` and a

   whole-file `node --check` of the injected patch; green, and every new

   section is mutation-tested: `tools/mutate_cause_gate.py` holds **329 mutations**. The count

   recorded here said 431 checks / 194 mutations and was stale: the tree measured 461/0 with 217

   entries before the accept batch. On 2026-10-27 the ten G33 `accept:` (10/10 caught), fourteen

   `catalog:` (10/10 then 4/4), ten `ingest:` (10/10), seven `offer:` (7/7), eight `identity:`

   (8/8), seven `witness:` (7/7), four `build:` (4/4), four `head:` (4/4), three `obs:` (3/3),

   five `watchdog:` (5/5), six `fight:` (6/6), five `kaggle:` (5/5), two `chain:` (2/2), four new

   `ready:` preflight mutations (4/4; the label batch also re-ran seven older ready-scan ones),

   one `sim:` (1/1) and five `command:` (5/5) were each run as a batch, 0 missed and 0

   anchor-skipped. After the command-dataset expansion, the intent-slot legality mask, the read-only

   command shadow, the prompt-shadow report, the goal-mode comparison, the action-to-tool

   bridge, the replay extractor (including its page-verdict outcome) and the session witness

   the gate re-ran 670/0 and the command batch (now nineteen: 19/19 caught, including

   eval-text, near-copy, intent-illegal-slot, shadow-override, mode-change, action-bridge,

   adapter-guard, replay-delta, dropped-advancement and dropped-witness leaks; the separate

   goal-routing, live-evidence, canary and ONNX-decode mutations are 1/1 each) re-ran green. The release build was re-run twice after the

   page and GUI changes: `compile.bat` exit 0, 11 patches active, `DofusFarm.exe` 540,672 bytes,

   `setup.exe` 104,955,904 bytes (100.1 MB), and the post-build gate and `js-error-finder` (23/0)

   were green. A full 329 sweep is owed on the next quiet tree. The

   driver

   also takes `MUT_ONLY="a label,b another"` so a new batch can be proven without re-running

   history — which matters because it writes broken code into the working tree and this repo has

   more than one writer. Two driver bugs surfaced earlier and are fixed: its snippet did not run

   the repeat-guard section (so a G22 mutation could not have been caught at all), and one anchor

   was a single line that appears five times in `autopilot.py`, which mutated an unrelated helper

   and reported a MISS that was not a real gap — anchors in this driver must be unique by context.

10. **G20/G21** dialog outcomes — the ranker's primary signal fired on 0 of 1,547 recorded dialogs

   and 90 of 110 recorded replies left the page unchanged; the pairing plus the dispatch fields are

   now logged, so the first live talk attempt produces the labels a scorer needs. Training on them

   is the next step, not this one.

11. **G26/G27/G28/G29/G30/G31 the quest loop could not be measured, and now the reasons are separated.**

    The metric was dead (G28: `finishedCount` is written only on completion, so 10,175 rows held one

    value, 0), the claim loop read one entry of a client-ordered ready list (G26: a wrapper held

    index 0 in 4,285 of 4,286 rows), the free-form objectives it did act on were targeted by an

    invented monster id (G27), the knowledge base that should have replaced those guesses has

    never contained a single live quest id (G29: cache 18…2,546 vs live 3841…4644, 0/7 coverage) —

    and worse, its objective table resolves 23 of the 26 live objective ids to *another game's* rows,

    which the loop published as `resolved` (G30: 3,567 of 8,777 rows ranked NPC replies against

    foreign text). The same wrong-game table was also *sending live requests*: all 366

    quest-start rows named a quest the client never reported, 297 of them one Dofus 2 quest, with the

    3-attempt bound re-armed by every scene change (G31). G26, G27, G28, G30 and G31 are fixed and

    gated offline; G29 is data coverage that is not half-done in the tree, its download route is now

    closed by the source's licence; its client-side capture read, observed-knowledge path and

    persistent ingest shipped on 2026-10-27 (offline-verified; live population owed), and the

    store/training merge is what stands between this batch and a retrained label set. **G33** (the

    accept verb was unreachable while a dialog was open) is fixed offline behind the page's offer

    guard, and a quest/auto run now takes a newly offered quest — 17 new mutations, gate 511/0 — and

    the first live offer is its proof. None of these is live proof — see each section's *Not

    verified*.

12. **G5** forecaster, **G7** fight proof, then G4/G6/G10. G9 is closed offline (identity binding +

    drift), with only its live relogin proof owed.



---



## G34. An action the server ignores looked like an action worth retrying *(fixed 2026-10-27, measured live)*



**Symptom.** Two characters spent half a session repeating one call: 119 identical

`quest.advance {npc_id: 3888, quest_id: 3841}` and 120 identical

`nav.goto {direction: left}`, each `ok: true`, each verdict `noop`, the world never

moving.



**Cause.** Nothing distinguished "the packet went out and nothing answered it" from

"the packet has not been tried enough". The loop's only repetition test was three

identical no-ops, and on a tutorial character the scripted step made the guard reset

its own counters and wait forever. Two specific dead actions were measured:



* **the talk.** `NpcGenericActionRequestMessage{npcId:-1, npcActionId:3, npcMapId:…}`

  is field-for-field what the client's own context menu sends

  (`{npcId: l.contextualId, npcActionId: 3, npcMapId: playerData.position.mapId}`),

  the NPC stands one cell away (262 vs 263), the page reports `via: talked` — and

  `playerData.isInDialog` is still false a second later. The server does not answer

  this talk at all.

* **the crossing.** `last_nav.stage = change_requested_at_edge` from cell 322, which

  *is* one of the left exit's own cells `[322,294,350,308,336,280]`, with

  `tutorial.can_change_map.left: true`. The request leaves, the map never changes.



**Fix.** Each has a receipt, so each is now noticed on the second send instead of the

hundredth, and the step is then held like any other scripted wait:



* `payload_of(result)` unwraps the bridge's `response.result.data` in one place — the

  first version of this rule read `result.data["via"]`, got `None`, and silently never

  fired, which is the same class of bug as the missing `recordActivity` in G1;

* `_standing_on_exit(args, obs)` answers "the character is already on a cell that

  leads where it asked" from the client's own exit list, so a declined crossing needs

  no map database and no guessed coordinates;

* `_tutorial_hold` + `_holding` bound the patience: one window per action signature

  (`TUTORIAL_HOLD_S`), released the moment the world signature (map, cell, active

  objective, dialog) changes, and **expiry restarts the window rather than ending the

  run** — a loop that exits stops sending directives, the page's keep-alive gate closes

  with it, and the same client timer logs the character out ten minutes later. The

  retry rate is therefore one per window, and `hold_cycles` says how many were spent.



**Measured after the fix** (same four clients, five minutes): 45 steps in total across

the farm, max 4-9 repeats of any one signature, and the log now says why each is

stopped — `quest.advance opened no dialog 3x; the server is not answering this talk,

holding the step` and `nav.goto left declined from cell 322 5x; holding the step`.



**What is still open.** *Why* the server declines both. The talk needs the tutorial

script to accept an agent-initiated `NpcGenericActionRequestMessage` for NPC 3888, and

the crossing needs the step's own gate; neither is visible from the client. The

counters (`dead_talks`, `declined_crossings`) are in the run summary so the next live

session can start from the measurement instead of rediscovering it.



Guards: `test_tutorial_hold` (16 checks, behavioural on `_tutorial_hold`, `_holding`,

`_standing_on_exit` and `payload_of`) plus the `hold:` and `talk:` mutation batches.



## G36. The simulator reported an open quest dialog just for standing on a start map *(fixed 2026-10-28, measured)*



**Symptom.** The teacher fell from **119 quests / 148 objectives, max_repeat 1** to

**82 / 177, max_repeat 13, stalled** in a 400-step level-60 run. The first

hypothesis was the new `hdv.buy` branch or the sub-goal stack; both were tested and

neither was the cause (disabling `child_for_objective` and removing prices changed

nothing).



**Bisect.** Worktrees at `51c429c` (Oct 24), `bd6a04b`, `08c1a8c`, and `0990695`

all score **119/148**. `f690da0` (the next commit) scores **82/177**, and the

working tree matches it. The causal diff is `sim/world.py`: `_observe_now()`

started publishing `dialog: {open: true, start_quest: ...}` from `offer_here()`

whenever the character merely stood on a quest's start map. The planner took that

as a real offer and accepted quests instead of completing the one in front of it.

The 13 identical `nav.goto {direction: top}` calls were a corridor walk (each moved

1–3 hops), not a loop; they were a consequence of the diverted route.



**Fix.** The simulator publishes `dialog: {open: false}`; a dialog is a talk, not a

map. The harness still offers its ids through `_client_quests` (the G31 seed), so

`sim/tool_choice.py` continues to generate rows (108 rows / 65 advanced in the

smoke test). `test_sim_planner_runs` now runs the full 400-step level-60 gate and

requires **≥119 quests, max_repeat ≤3, stalled false**, plus a direct check that a

standing character is not in a dialog. Re-measured: **119 / 148, max_repeat 1,

stalled false**; noise 94 / 99; poor start (`--kamas 0 --random-start-map`) 119 / 148.



**What is still not modelled.** A real NPC talk does not yet open a dialog in the

sim; the planner starts quests through the harness's reported ids. That is

sufficient for the planning corpus and is deliberately not a claim about the live

dialog gate.



## G35. Cause-head validation leaked identical inputs *(gate-pass claim withdrawn 2026-10-28)*



**What was built.** `agent/constraint_corpus.py` now generates **2,600** rows

(25 tools × 8 causes × 13 template families) with expected cause and action.

The controller routes **100% cause / 100% action** on the generated set; the

built-in 33 hand cases still pass, and the gate checks the 95%/90% floors and

≥50 rows per tool.



**The measured result.** `ai/train_cause_head.py` (hashed char + word + generic

marker features, MLP 256, calibrated on a separate family split) was evaluated on

**two whole phrase families per cause that were never trained on**, across five

splits. The MLP alone scores mean **0.81 accuracy / 0.19 ECE** (range

0.7625–0.875) and fails the gate. A cosine-kNN retrieval half over the same

features reaches **0.853**; the MLP+kNN blend (weight chosen on calib) scores mean

**0.855 / 0.089**, originally reported as clearing ≥0.85 / <0.15, with one weak fold at 0.807 / 0.163.

The artifact is exported with both halves (`models/cause_head.onnx`,

`models/cause_head_prototypes.npz`) and the MLP-alone number is kept in the

sidecar rather than hidden. It is **not wired** into the controller and

`test_cause_head_artifact` pins that.



**Correction after input-level audit (2026-10-28).** The seed-7 validation set has

**50 of 400 feature vectors identical to fit and calibration inputs**, all labelled

`none`: changing the family name did not change the model input. Reloading both

FP32 and INT8 artifacts with their stored prototypes reproduces 0.855 accuracy /

0.0768 ECE, but excluding the duplicated validation inputs yields **292/350 =

0.8343 accuracy**, below the 0.85 gate. This exclusion is a diagnostic, not a new

held-out benchmark. The prior gate-pass claim is withdrawn; all five splits were

used during development, so an independent evaluation is still required.

`audit_splits` now rejects family overlap, identical float32 model inputs, and

missing cause coverage before training or export; eight behavioural tests exercise

that rejection. Existing artifacts are retained, not promoted. The 2,600 text rows

also cross irrelevant preconditions with tools (for example a snapshot needing a

purchase price); they are taxonomy probes, not 2,600 validated game scenarios.



**Why.** With 13 template families per cause, holding out 2 leaves ~11 examples of

each phrasing pattern. Some held-out templates share no surface marker with the

training ones (`"the walk ended on the same cell"` for `incomplete`), and a hashed

n-gram model cannot infer the cause from semantics. The corpus is rule-distilled

(`kind: constraint_synthetic`, `live: false`); it measures the taxonomy, not game

outcomes.



**What would promote it.** Live error texts from a real session (the

entity-pointer/live collection route), a pretrained text encoder instead of hashed

features, and a shadow run where the ensemble's cause agrees with the rule or the

outcome. Until then the artifact is an unverified development result; the

controller keeps its rules.



Guards: `test_constraint_corpus` (taxonomy agreement, not scenario applicability),

`test_cause_split_audit` (input/family leakage rejection),

`test_cause_head_artifact` (unpromoted status, not a model-quality pass).



## G37. Three fights were played with the weapon only because the damage spells were gated behind a state the planner never cast *(fixed 2026-10-28, measured live)*



**Symptom.** Brytnee Spyr killed the level-7 Pandawa twice and Ek Spyr once while every

turn looked legal: the page cast `spellId 0` (the Albueran Fighter Hammer, 6.9 damage at

range 1) and the combat policy answered `policy_pass` with four empty slots. The

policy's own decision log shows the binding had nothing else to offer:



```

slots [hammer 6.9, empty, empty, empty]  reason policy_pass

slots [hammer, empty, empty, empty]      reason empty_spell_slot

```



while `derived.self` in the same observations estimated 20.9–44.1 damage for

7017/7041/7043/7045/7051. The brain was not refusing good spells; they were never

offered.



**Cause.** `canCastThisSpell` is one boolean over ~10 gates, and the failing gate was

`statesRequired`. Bruuh is a Pandawa: 7031/7041/7045/7051 all carry

`statesRequired: [1]`, and state 1 is the Boozer state granted by spell **7001

"Boozer"** (AP 1, range 0, `minCastInterval 2`). With no state the client refuses every

damage spell, `state.spells[].castable` comes back false for all of them, `_bind_slots`

sees no usable damage row, and both planners fall back to the one castable attack: the

weapon. A new `debug.spell_probe` replays each gate per spell and reported the exact

one live:



```

7041 status 2 gate missing_state_1 can_cast False

7045 status 2 gate missing_state_1 can_cast False

7051 status 2 gate missing_state_1 can_cast False

7001 status 2 gate None            can_cast True

```



**Fix (page, `scripts/mobile-disguise.js`).** `_spellStates(spell)` reads

`statesRequired` / `statesForbidden` from the live spell and the state ids granted by

the spell's own effects (the 950–952 state family, id in `value`), so nothing is

hardcoded per class. `_planFightAction` now collects damage spells whose only blocker

is a missing state, and `_planStateEnabler` casts the castable spell that grants it —

**before** the reachable-weapon branch, because the hammer keeps the good spells locked

for the whole fight. `canCastThisSpell` refuses a second cast while Boozer's cooldown

runs, so the order cannot loop. The brain takes over by itself once the state exists:

the next `fight.state` payload reports the class spells castable and the policy picks

7045 directly (`policy_direct` in `live_policy_decisions.jsonl`).



**Measured after the fix.** Same fight, same level: first cast is Boozer, then

`GameActionFightCastOnTargetRequestMessage{spellId:7051}` and 7045 land; Brytnee goes

down (+2,000 XP) and the policy's next decisions show slots `7045/7051/7041`. The

earlier Ek loss (weapon-only, 120→0 HP) is the same fight class the fix addresses.



**What is still open.** The policy's slot binding still excludes support/self-buff rows

(`_bind_slots` keeps only damage/heal rows), so the *learned* path can only use the

enabler once the page has cast it. With the state already active the model plays well;

from a cold start the page owns the first move. Folding a grant-state row into the

binding is the next experiment, gated on live win-rate rather than on the slot list.



Guards: `tools/test_fight_planner.js` — "a state-gated spell casts its enabler before a

reachable weapon" and "with the state present the gated spell is cast normally"

(12 checks in the suite, inside `agent.regression`).





## G38. Level-ups were never spent: the bot fought at base stats with 35 points in the bank *(fixed 2026-10-28, measured live)*



**Symptom.** Bruuh reached level 10 with 25–35 unspent characteristic points and 8

unspent spell points. Nothing in the tool vocabulary owned the "+" button, so every

later fight ran at base stats and the spell rank was never bought. Fights were won

with the weapon and one low-rank spell, which reads as a balance problem and is a

housekeeping gap.



**Cause.** `debug.stat_upgrade` / `debug.spell_upgrade` exist as test-only handlers

(the tutorial used them once), but nothing calls them after a level-up: the autopilot

plan is goal-driven (`quest.advance` / `nav.goto` / `fight.join`), and a snapshot that

does not expose the counters cannot make "points are unspent" visible. The policy then

sees `canCastThisSpell` and damage for the spells it has, never a reason to spend.



**Fix.**

* `state.snapshot.points = {stats, spells}` — the two counters from the live

  characteristics object, so any driver can see the need.

* `char.build`: picks the best damage spell from live spell data (damage mid / AP,

  biased toward `canCastThisSpell`, then USABLE status), reads its element from the

  damage effect id (96 water, 97 earth, 98 air, 99 fire, 100 neutral — the client

  action table), and sends the UI's own packets: `StatsUpgradeRequestMessage

  {statId, pointsUsed}` and `SpellChangeRequestMessage {spells:[{spellId, spellLevel}]}`.

  No class table.

* `FarmBot.autoBuild()` on the existing 20 s keep-alive timer, gated on the bot being

  enabled and not mid-fight, throttled to 60 s; a rank the server refused at the

  current character level is remembered and not re-asked until the level changes.

* `char.build` stays **out** of the fixed 25-tool vocabulary (housekeeping, not a

  model-facing verb); it is reachable through the `CHAR_BUILD` directive alias.



**Measured live.** Manual first pass: 35 points → agility (7045 "Alcoholic Breath" is

air), 8 spell points → 7045 rank 2→4 (accepted; rank 5 refused at level 10). Then the

automated path: the raccoon fight took the character to level 11 and `agility base`

went **35 → 40** with no manual step. Fights after the spend: class spells at 20–40

damage instead of a 7-damage hammer.



**What is still open.** The element is "the strongest damage spell", not a planned

rotation; the 4 banked spell points need a level-up (rank cap), and the refusal memory

is the only guard.



Guards: `tools/test_char_build.js` (12 checks) inside `agent.regression`.







## G39. The training pipeline was scoring itself against a teacher it could not beat, and its live labels credited the wrong step *(measured 2026-10-29)*

**Symptom.** `models/tool_policy.onnx` reports val accuracy 0.8599 with teacher
agreement 0.857 and a lift over the teacher of +0.003. That is the whole model: a
49,365-parameter MLP that has learned to say what `QuestPlanner` would say. It was
never asked whether the planner's choice was any good, so the reported number cannot
tell a good policy from a faithful copy of a mediocre one.

**Cause, part 1 - the label recipe has no headroom.** `sim/tool_choice.py` labels
each step by forking the world for every candidate tool, running the teacher's
continuation, and keeping the argmax of `_fork_score` (ties go to the teacher). If the
teacher's own continuation is inside every fork, the teacher's first move is at least
as good as any alternative, by construction. A new held-out benchmark,
`tools/bench_quest_policies.py`, runs 400 steps on 10 seeds that the corpus does not
use (the corpus seeds are 7..126, 984..1103, ...; the tool warns when a range
overlaps). Result: **rule 79.90 objectives, learned 80.50 (5 wins / 5 ties / 0 losses,
sign test p=0.0625), oracle 79.80 (3 wins / 1 tie / 6 losses, delta -0.10)**. The
oracle is the label recipe itself; it loses to the plain teacher. More labels of this
kind cannot buy anything.

**Cause, part 2 - the old face-off was not held out.** `tools/policy_faceoff.py`
evaluates on the corpus's own seed range, so its "winner" was measured on episodes the
training data already covered. Any comparison built on it is invalid; the bench above
exists to replace it.

**Cause, part 3 - sim data does not transfer to live choices.** `models/foresight_v1`
was trained on simulator forks; on 1,467 fresh live rows it scores `live_move_auc`
0.4605 (chance) with ECE 0.732. The same architecture trained on live rows
(`models/foresight_live_v1`) scores 0.6389 with ECE 0.235 on the same rows. The
simulator is a useful planner playground and a useless source of live-serving labels.

**Cause, part 4 - the live dataset was two days stale.** `data/transitions` stopped
at 2026-10-27 while the trajectories had grown to 40 MB + 67 MB rotated, because only
`tools/auto_live.py` rebuilt transitions and it only reads its own round logs; every
daemon-launched session was invisible. `tools/refresh_live_dataset.py` now rebuilds
`data/transitions_v2` from every `trajectories/dofus-*.jsonl` (+ rotated +
`default*.jsonl`, excluding `play_*` and `*.stats.json`) through
`tools/build_live_transitions.py`. Two runs produce the same numbers.

**Cause, part 5 - the `moved` label credited the read, not the act.** The log pair for
a step is (observation before the directive, snapshot the directive returned). Some
effects land after the directive returns, so they appear in the *next* step's pair.
Measured on 9,687 rows: 195 reads carried `moved`, and 173 of them sat directly after
a `quest.advance` - the read was being credited with the action's effect. A value head
trained on that learns "reading moves you". `_attribute_lagged` in the builder now
credits a change first observed in a run of reads to the last acting step (a read
cannot change the world), keeps the raw diff in `moved_observed` /
`progress_observed`, and stamps `moved_lag` / `progress_lag` with how many rows later
the effect appeared. 170 move-credits and 6 progress credits moved back to the acting
step; `manifest.label_version` is 2. Guarded by `test_live_transitions` ("the acting
step owns the effect the next read revealed").

**Measured corpus (label v2).** 9,687 rows, 457 segments, 5,289 actions, 1,030 moved
(19.47%, was 16.26%), 77 progress events (was 71), of which **11 actually advance a
quest** (`objectives_completed` / `quest_steps` / `quests_finished`; the other 66 are
"an objective became claimable" or "a quest was accepted"), 4,289 reads, 98% x
coverage; train/val/test = 7,307 / 1,251 / 1,129 rows. Per tool, advance / progress /
moved: quest.advance 11 / 62 / 636 of 2,848, nav.goto 0 / 8 / 348 of 2,145, fight.join
1 / 3 / 11 of 21, tap.interactive 1 / 2 / 31 of 46, ui.tap 0 / 2 / 2 of 10, inv.equip
and farm.start 0. Split by segment, leak-free: 0 segment overlap across the three
splits, 0 episode overlap in `tool_sft_v3`, guarded by `test_dataset_split_integrity`.

**The global AUC was a phase detector, and the phase ordering does not transfer.**
`tools/live_outcome_probe.py` (StandardScaler + logistic regression, balanced, C=0.5)
on the 42-feature row plus tool one-hot reaches `progress` AUC 0.883 on test (26
positives) and 0.850 on val; the biggest coefficients are `level` +4.83,
`step_index` +3.26 and `kamas` -3.23. The 42 features describe the *objective and the
run*, never the candidate action - the action enters only as a tool one-hot - so a
model on them can order two steps of the same segment only by phase. Scoring just the
positive-negative pairs *inside* a segment (`--within-segment`, CI by resampling
segments) makes that explicit:

| target | split | phase-only | action-only | action-only, tool-discordant |
|---|---|---|---|---|
| moved | val (29 segs) | 0.607 | 0.698 | 0.806 (4,149 pairs, CI 0.604-0.953) |
| moved | test (27 segs) | 0.467 | 0.591 | 0.648 (4,136 pairs, CI 0.397-0.855) |
| progress | val (11 segs) | 0.902 | 0.806 | 0.901 (664 pairs, CI 0.647-0.991) |
| progress | test (11 segs) | **0.297** | 0.652 | 0.897 (78 pairs, CI 0.828-1.000) |

First reading: the global AUC is not action signal. The phase-only model reaches 0.833
globally on test while its within-segment ordering *inverts* (0.297, CI 0.23-0.525 -
below chance) after being 0.902 on val. Segments are split by a hash of the segment id,
so this is not a time split; it is instability estimated on 11 held-out segments.

**Second reading - and this is why no head gets promoted - the visible "action
signal" is the definition, not a discovery.** After attribution a read can never own
an outcome, so action-vs-read pairs are decided 1.000 by construction (200,201 moved
pairs, 7,350 progress pairs). The within-segment action-only column above is
concentrated in exactly those pairs. Restricted to pairs of two *different actions*
(reads excluded), using tool outcome rates fitted on train and scored on held-out
segments, the ordering is not stable: moved 0.708 on val / 0.355 on test, progress
0.560 on val / 0.860 on test (57 pairs). The rates doing that ranking are 1.0 from 46
tap.interactive rows and 1.0 from 21 fight.join rows; that is small-sample noise, and
it flips sign between splits. Nothing here identifies a better tool.

**Why it cannot, structurally.** The log is observational: only the tool the teacher
chose was executed, so for every state the corpus contains one action and no
counterfactual. A model trained on it can learn when the teacher's action *worked*,
never that another action would have worked better. The simulator was the only
counterfactual source and it does not transfer. More rows of the same shape will not
change that.

**The mathematics of what this corpus can answer.** Three numbers decide it, and
`tools/ope_probe.py` computes all three on the live rows.

*Clustering.* Actions inside one segment share a state, a map and a goal, so they are
not independent observations. A one-way random-effects ICC gives 0.241 for `moved`
(mean 61 rows per segment), 0.287 for `progress` and 0.082 for `advance`, i.e. an
effective sample size of **604 / 513 / 1,580 rows out of 9,393** - a design effect of
6-18x. The minimum relative change this corpus can see at 80% power (two-proportion,
5% two-sided, on n_eff) is **45.6% for moved, 192% for progress, 289% for advance**.
Below that, a "no change" reading is indistinguishable from a real improvement. The
test split holds **zero** advancement events, so every test-split progress number
quoted earlier was a claimable/accept number, not a quest advancing.

*Overlap.* The corpus is one action per state: at no state did two tools get tried.
Formally the design matrix is block-diagonal by state, so `Q(x, a)` for an untaken
`a` is not identified by any estimator, however large the model. The closest
identified quantity is a reweighted one: cross-fitted propensities (a multinomial
model of the taken tool on the 42 features, out-of-fold accuracy 0.423, mean max
probability 0.69, 12.2% of rows >= 0.9) give importance weights for a target policy.
For `no-read` (the teacher's own distribution renormalised over non-read tools) and
for `uniform` the effective sample size is **19-44 rows out of 9,393 (0.2-0.5%)**,
with single weights up to 1,737 and 125,000, and 0.9-1.4% of rows above weight 20.
An estimate from 38 effective rows is a rumour, not a measurement; the bootstrap over
segments still prints a CI (e.g. no-read progress 6.2%, CI 1.0-12.5% against 0.8%
observed) because it resamples rows under *fixed* propensities and cannot see the
model error that dominates the result. Clipping at 20 raises ESS to 2,482 but
removes exactly the rows the question is about, so the clipped estimate (1.8%) is
biased toward the behaviour it is supposed to critique.

*The price of asking.* ESS/n for a changed policy is ~0.004 because the teacher is
nearly deterministic. A randomized design - at states where the planner would read,
act instead with probability 1/2 - has ESS/n ~ 0.5 by construction, about **100x more
usable information per logged row** for the read/act question. That is the argument
for exploration in one line, and it is also why exploration cannot be bolted on
later: the current code returns one call, so a randomized arm needs the planner to
expose the candidate actions it already considered.

**The alternative supply exists, and the planner can enumerate it.** The candidate
set an exploration arm needs is already inside the planner: skip the quest it just
chose, ask again, and it plans the next objective. On the 420 recorded live turns in
`data/replay/planner_obs.jsonl`, 419 of which get a plan, **351 (84%) have a distinct
second plan**; the pairs are `quest.advance -> quest.advance` with different arguments
(242) and `tap.interactive -> quest.advance` (109). The ready set behind that: 236
turns with one ready objective, 151 with two, 32 with three. In the derived corpus 56%
of rows (5,390 of 9,578) hold at least two ready objectives, and 3,090 read-steps were
taken while two objectives were ready. So a bounded exploration arm does not need to
invent actions: both arms are calls the planner itself produces, which makes them
legal by construction, and the question they answer - which objective first - is one
the corpus cannot answer today.

That enumeration was blocked by a bug: `fork()` did not copy seven attributes added
after it was written (`_equip_tries`, `_exhausted_at`, `_interactive_tries`,
`_rule_policy`, `_spawn_waits`, `_tutorial_step_seen`, `_ui_attempts`). Any fork that
reached a free-form objective, a UI tap, an equip retry or a spawn wait raised
AttributeError - measured 2026-10-29 on the replay corpus, the tap branch crashed
outright. Every counterfactual label the simulator ever produced came from forks that
avoided those branches. Fixed in `fork()`; guarded by
`test_fork_carries_the_planner_state` (attribute diff plus a fork replanning a real
free-form turn).

**The exploration arm is built, priced, and off.** `agent/explore.py` wraps a policy:
at every state it is *offered* (not in a fight, not in a dialog, tutorial inactive, a
current quest to retire) it computes the planner's second plan before the coin, so the
row records the truth about the state - `has_alternative`, `p_explore` = epsilon when
one existed and 0 when none did - and only then draws the arm. Eligibility is a
whitelist, epsilon outside (0, 0.5] is refused, and the event rides
`ToolCall.meta -> executor -> recorder -> transitions` as `explored`, `arm`,
`p_explore`, `has_alternative`, `teacher_tool`. Manifest: `explored_actions`,
`randomized_actions` (both 0: no run has used it). Knobs `--explore-objectives` /
`--explore-seed`, or `explore_objectives` in a `/brain/start` config. `--explore`
already randomizes repair decisions; the two are separate on purpose.

Simulator pricing, 10 seeds x 400 steps, epsilon 0/0.05/0.1/0.2/0.5: objectives 197
and quests 70 at every level, stalls 0, and **explored 0** - the sim planner has one
ready objective per state, so its second plan is always the same call (`skipped`
18.6/39.1/78.2/203 tracks the coin exactly). The simulator cannot price this arm; the
live replay can (84% of recorded turns have a distinct second plan).

`tools/explore_report.py` is the pre-registered read. It uses only rows with a live
propensity (0 < p_explore < 1), compares the arms on the same randomized set, gives a
segment-clustered CI, audits the explore share against the mean propensity, checks arm
balance on objective type and map, and computes the inverse-propensity value of
"always take the alternative". With no randomized rows it prints the plan instead,
priced on this corpus's own progress rate (77/5,289 actions = 1.46%) and a 95% CI
half-width:

| epsilon | +/-1.0pp | explored rows | hours at 5.8 s/step | +/-0.5pp | hours |
|---|---|---|---|---|---|
| 0.05 | 11,605 steps | 581 | 18.7 | 46,417 | 74.8 |
| 0.10 | 6,125 | 613 | 9.9 | 24,498 | 39.5 |
| 0.20 | 3,445 | 689 | 5.6 | 13,780 | 22.2 |
| 0.50 | 2,205 | 1,103 | 3.6 | 8,820 | 14.2 |

Priced at the corpus's real cadence, not an assumed 2 s: epsilon 0.2 buys a one
percentage point answer (a 68% relative change on the 1.46% base) in about **5.6 h**
of play, epsilon 0.5 in 3.6 h, epsilon 0.05 in 18.7 h. A two-point answer at epsilon
0.2 costs 1.4 h. That is the trade the user is being asked to make; nothing randomizes
live behavior until then.

**The reward had the wrong denominator.** `latency_ms` is the directive round trip:
six minutes in total across 9,687 rows, so it cannot price a step. The recorder's
`ts` can: the builder now charges the time between two records to the step that caused
the wait (`dt_ms`), and the manifest reports it. Measured: 9.34 h of session time in
the corpus, 5.8 s per action on average (median 6.4 s; the 120 s segment split keeps
pauses out). Time share of the actions: **nav.goto 62.4%, quest.advance 34.2%,
fight.join 0.9%, tap.interactive 0.5%**; reads are **13.4%** of all recorded time
(1.25 h), not the 44% their step count suggests. Rates per hour: quest.advance 22.5
progress and 2.2 advancements, nav.goto 1.6 and 0.8, tap.interactive 48.7 and 24.3
(from 46 actions), ui.tap 1,310 (from 10). Two consequences. First, the "reads eat
the budget" reading was a step-count artefact: removing 10% of reads saves ~7 minutes,
removing 10% of travel saves ~30. Second, a per-step target under-weights travel,
which is both the most expensive action and the one that progresses least; every
policy comparison should be stated per hour as well as per step, and the manifest now
carries both. The per-tool hours also reprice the exploration plan (below).

**What this rules out.**
* More simulator episodes with the fork-argmax label recipe. Its oracle is at the
  teacher's level and slightly below it.
* Face-off numbers as evidence for or against a policy.
* Sim-trained value heads for live choices.
* Reading a global live AUC as action signal; it is mostly phase, and the phase
  ordering is unstable across held-out segments.
* Treating action-vs-read accuracy as policy evidence, or fitting tool rates on
  corpora with 10-50 rows per tool.

**What is next, in order.**
1. Auto-ingest daemon trajectories after a session. Done:
   `BrainDaemon._ingest_live` rebuilds the dataset when a run's thread finishes
   (subprocess, one job at a time, 60 s debounce), guarded by
   `test_ingest_after_run`. It takes effect at the next daemon start.
2. Reach >=500 **advancement** events (11 today; the 77 "progress" rows are 89%
   claims and accepts) before any live progress head, or change the target to
   something denser and measurable.
3. Evaluate only on within-segment tool-discordant pairs among *actions*
   (`live_outcome_probe.py --within-segment`, guarded by
   `test_live_within_segment_eval`). It is a measurement, not a model.
4. Give candidate-conditioned inputs (what each candidate tool would do from this
   state) rather than the objective-only 42-vector plus a one-hot.
5. Identification is the missing ingredient, not capacity. The ESS calculation
   prices it: an unrandomized corpus gives an off-policy question ~0.4% of its rows,
   a randomized arm gives ~50% of the eligible ones. Built, tagged and priced: see
   the arm paragraph above. The measurement is one command
   (`python tools/explore_report.py`) once a run has produced randomized rows; the
   table there says an evening at epsilon 0.2 buys a +/-1pp answer. The next step is
   the user's: authorize a short epsilon run or stay observational. Until then
   nothing randomizes live behavior.

**Open in the game, not the pipeline.** Quest 4262 step 4954 objective 13948
("Recover the first page of the aqueduct blueprints") on dofus-05: element 513208
(skill 136137092) answers ok but does not complete. Memory says skill 184 completed
the same objective on dofus-03 on 2026-10-28, so the skill id may differ per session;
this needs a live read of the element's own action list, not a guess.


## G40. The downloaded knowledge store is legacy for live quests; the client-captured index is the live authority *(measured 2026-10-29)*

**Verdict on the store.** `data/knowledge/dofus_knowledge.sqlite` (44.0 MB,
`knowledge.CanonicalStore`) holds **1,976 quests, ids 18..2546**, every row
sourced `dofusdb_cache`. Measured today: **none** of the five live quests
(3841, 4260, 4262, 4264, 4266) is present, `verified_live` is 0 on every
entity row, and 2 of 3 sampled live objective ids (13900, 14070; 13948 absent)
*do* exist - under other quests, which is exactly the G30 collision. It also
falls under the DofusDB No-AI licence: it cannot be re-synced and must not feed
training. So for live quests it is **legacy**, kept only as (a) a fallback for
the id space it does describe and (b) the evidence the collision guard compares
against. `agent/knowledge_context` already resolves in the order that follows
from this: the snapshot's `quests_catalog` (the running client's own graph) ->
the persisted client index -> the downloaded store, and the catalogue always
wins, so a stale index can only fill gaps, never overwrite a fresh graph.

**What replaced it.** `tools/knowledge_index.py` indexes only values the client
itself sent, from four sources - `quests_catalog`, `ready_objectives`,
`current_objective`, `step_objectives` - into `data/knowledge/client_index.json`
(+ `.manifest.json`). Over the 8 trajectory logs: **57 objectives / 9 quests**
(3841, 4260, 4262, 4264, 4266, 4274, 4278, 4604, 4644); type / params / step id /
quest name 51/57 (89.5%), text 45/57 (78.9%), map 26/57 (45.6%); sources catalog
51, ready 48, current 33, step 39; **0 conflicts**; 217 step objectives whose
step id no catalogue names. An independent recount of every (key, log) pair
agrees exactly: 57/57 rows, 0 capture mismatches, 0 keys in the logs missing
from the index.

**What "100% accuracy" means here, and what it does not.**
  * every field is a literal the client sent; nothing is inferred, nothing is
    copied out of the store;
  * if the client ever sends two values for one field, the field is recorded as
    a conflict, withheld from the row, and the tool exits non-zero - it is never
    picked (time-varying progress counters are exempt and keep the latest);
  * `field_sources` names the source behind each individual field; every row
    carries captures, first/last sighting and the logs it came from;
  * `--check` rebuilds from the logs and fails when rows *or* the manifest
    differ, so a stale or hand-edited index cannot pass as verified;
  * writes are temp-file + rename: the runtime reads this file on every
    snapshot, and a truncated read would fall back to the store - the G30
    failure mode - so a reader must always see a whole index.
  It is **not completeness**: 57 objectives is what this character has been
  shown, not what the game contains. Everything else is reported absent.

**Binding bug found while auditing.** A `step_objectives` row usually carries a
step id but no quest id, so it is bound through its step's unique owner. Binding
during the scan used only the owners read so far, so a file whose step lists
precede its catalogue lost those rows. Measured with the owner map reset per
file: **10,843 of 19,527** such observations bound in-pass, **19,310** bind with
the deferred map (217 have a step id no catalogue names). The scan now defers
only the binding; the parse stays single-pass. The corpus index was already
nearly complete because the old map accumulated across files - the fix removes
the file-order dependency instead of correcting old numbers.

**Freshness.** The daemon's post-run ingest rebuilt only `data/transitions_v2`,
so the index would have gone stale after every run that captured new objectives.
It now runs `refresh_live_dataset.py --json` and then `knowledge_index.py
--write` in the same debounced job - dataset first, because the index reads the
logs, not the dataset. Both are separate processes; a failure is logged and
never kills the daemon.

**End-to-end proof (offline, real files).** With the real index and a store that
still collides: objective 14070 under 3841 resolves `status: observed`,
`observed_from: file`, text `Complete the "A Chaotic Arrival" quest.`, quest
`Getting Started`, step `Go to Albuera with Ayin Spyr`; 13900 under 4260
resolves as `QuestObjectiveGoToNpc` / `A Chaotic Arrival`. Without the index the
same lookups returned the store's foreign rows or a withheld collision. Gate:
**1,352 checks, 0 failed**.

**Open.** Coverage is 9 quests. The screenshot/free-form path (G2) and any quest
the client has not opened yet stay unindexed until a run captures them; that is
a property of capture, not of the tool.


## G41. Every live-outcome number so far was validated on the same days it trained on; a temporal fold turns 0.83 AUC into 0.63, under a per-tool rate *(measured 2026-10-29)*

**Symptom.** The 42-feature outcome model reports moved AUC **0.830** (CI 0.807-0.855)
on its test split. That split is a hash of segment ids, and measured today it puts
**all eight days (10-22..10-29), six characters and every phase in train, val and
test at once**. It is interpolation, not prediction: the model is asked to recognise
states it has already seen, not to act on tomorrow.

**Measured.** `data/transitions_temporal` is the same 9,687 rows cut by segment start
time - train ends 10-27, val is late 10-27/10-28, test is 10-28/10-29 (1,299 rows,
1,060 actions, 348 moved positives, 3 characters, median level 4 against 3 in
train). Segment overlap between folds is zero by construction.

    moved, test fold      hash split   temporal split
    phase_only               0.755         0.375
    action_only              0.714         0.724
    both                     0.830         0.634
    tool_rate (floor)        0.545         0.723

The floor is each tool's historical moved rate, Laplace-smoothed - **no state at
all**. On the future fold the phase features are *below chance* by themselves and
they drag the combined model under the floor; on the hash fold they add +0.12 AUC.
The same reversal holds on the earlier single-day cut (phase 0.475, both 0.538,
tool-rate 0.640), so it is not one unlucky boundary.

**Cause.** The feature vector describes the objective and the phase of the run. Phases
repeat across days and the hash split lets the model memorise "this objective on this
map moved last time"; out of time the phase distribution has shifted (level 3 -> 4,
tutorial -> post-tutorial) and the same coordinates point the wrong way. The only
thing that survives is which tool was used, which is a one-variable rule the planner
already beats by construction.

**What changed.**
  * `tools/live_outcome_probe.py --within-segment` now prints `tool_rate` beside the
    three models, on every fold. A learned scorer that cannot beat it on the temporal
    fold has bought nothing.
  * `tools/build_live_transitions.py` emits `at` (epoch, from the log's own `ts`) and
    `context.character` {id, level, breed}; the manifest carries characters, levels,
    days, instance_days, span_days and `split_policy`. Before this the derived rows
    carried **no wall-clock time and no character**, so neither a temporal nor a
    cross-character holdout was even expressible.
  * `--split temporal` (also on `tools/refresh_live_dataset.py`) cuts by segment
    start order: the latest ~15% of segments by start time are test, the previous
    ~15% val, undated segments stay in train. An action-share cut was tried first
    and left the future fold empty whenever the last segment was large.
  * The daemon's post-run ingest rebuilds `data/transitions_v2` (hash, the record)
    and `data/transitions_temporal` (the gate) before the knowledge index.
  * Gate: `test_temporal_split_plan` pins the ordering, the undated rule and the
    single-segment case; `test_live_within_segment_eval` pins the tool-rate floor.

**Corpus census, measured the same day.** 6 characters, levels 1..22 (median 3),
8 days, 20 instance-days, **8.05 h**. Strict `advance` events: temporal train **0**,
val 8, test 3; hash test 0. The strict-advance count, not the row count, is still
what limits every claim about quest progress.

**Consequence.** The honest ranking baseline for tool choice is now the per-tool rate
on the temporal fold (**0.723 moved AUC**), not the 0.830 reproduced on the hash
split. No live wiring changes from this; the planner remains the acting policy. The
next learned scorer must be reported on `transitions_temporal` and beat 0.723 before
it is considered anywhere.

**Open.** Which features could transfer is a design question, not a training one:
per-hour progress, legality/success, and map/quest-agnostic descriptors are the
candidates. Progress has 10 positives in 6 segments on the temporal test - every
arm's CI includes 0.5, so nothing can be claimed about it yet.


## G42. The outcome label counted walking: 318 of 636 "quest.advance" successes changed only the character's cell *(measured 2026-10-29)*

**Symptom.** `moved` means "any watched state key changed". For an action whose whole
point is to advance a quest, that is not success: it is "something happened". Decomposed
over the live corpus (2,848 `quest.advance` actions): 636 are labelled moved, **318 of
them changed only `cell`**, 91 only `in_dialog`, 25 only `ready_objectives`, and **59
touched quest state at all**. `nav.goto` has the same shape in reverse - 348 moved, 336
of them physical, which is the one tool where "moved" is close to the promise.

**Fix - a per-tool claim.** `tools/build_live_transitions.py` now derives `claim` and
`claim_met` from the call's arguments and the snapshot it returned:

    nav.goto          at:<map> when a destination is given, else one step (map or cell)
    fight.join        in_fight turns true
    quest.advance     the quest's own state changes (step, objectives, active, ready)
    tap.interactive   a quest/effect change, a dialog, or an inventory change
    inv.equip         an inventory or equipment change
    farm.start        farm_state becomes active
    safe.stop         farm_state or auto_fight changes
    reads             no claim (None), as does any call whose after-state cannot judge it

**Measured claim rates on the corpus (actions, 345 of 5,279 derivable met = 6.5%).**

    tool              n      moved%   claim-met%
    nav.goto       2,145     16.2      13.1
    quest.advance  2,848     22.3       2.1
    fight.join        21     52.4       4.8   (1 of 21 entered a fight)
    tap.interactive   46     67.4       4.3
    inv.equip         41      0.0       4.9
    farm.start        78      2.6       0.0
    safe.stop        100      0.0       0.0

Two of these are live questions, not training ones: `fight.join` reports ok and moves on
52% of calls while `in_fight` becomes true once, and `inv.equip` reports ok on all 41
calls with the inventory never changing. Either the effect arrives later than the
snapshot the call returns, or the snapshot does not expose equipment. Both are worth a
live check before any success head is trained on them.

**How it scores.** `tools/live_outcome_probe.py --within-segment` now reports a third
target, `claim_met`, restricted to rows where the promise is judgeable (a read is not a
failure). Temporal fold (test 1,031 rows, 66 positives): phase_only 0.641, action_only
0.724, both 0.714, tool_rate 0.723. Hash fold: both 0.865, tool_rate 0.697 - the same
interpolation gap as G41. So the target is semantically right and the phase features no
longer invert out of time (0.641 against 0.375 on `moved`), but the 42-feature model
still does **not** beat the tool-rate floor: 0.714 vs 0.723, intervals overlapping.

**Consequence.** No model is wired. The floor stands at 0.723 for `claim_met` and 0.723
for `moved` on the temporal fold. What has changed is that the pipeline now has a target
that means something, and a per-tool breakdown of where success is rare (`quest.advance`
2.1%, `fight.join` 4.8%) instead of a single blended move rate.

Gate: `test_claim_labels` pins each rule (direction vs destination, walking is not a
quest advance, a dialog is not a step, a read makes no claim, a missing after-state is
unknown) and checks the built manifest's `claim_pct < moved_pct` for `quest.advance`.


## G43. The outcome label read the pre-action snapshot: 45% of movements and 40 of 51 advancements were invisible *(measured 2026-10-29)*

**Symptom.** The builder took each step's after-state from `result.snapshot` - the state
the bridge returns with the directive. For movement that snapshot is taken before the
client applies the move. Measured over 2,139 `nav.goto` calls in the live logs: the
snapshot shows the move **5 times**, the **next step's observation** shows it **449
times**, and 1,685 show it nowhere. So the label was reading the wrong state for almost
every movement.

**Fix.** `label_steps` now prefers the next step's observation as the after-state (it is
the first state the loop actually saw after acting), and falls back to the directive
snapshot only for the last step of a segment, where nothing follows.

**Impact on the corpus** (same 9,687 rows, same 457 segments, same splits):

    label                 before   after
    moved actions          1,030   1,863   (19.47% -> 35.22%)
    progress actions          77     130
    strict advance events     11      51   (4.6x)
    claims met               345     788   (6.5% -> 14.9%)
    nav.goto claim rate     13.1%   32.2%

Every number in G39, G41 and G42 computed on the old labels is superseded; the temporal
split and the probes were rebuilt and re-run.

**What the corrected corpus still says.** On the temporal fold:
  * `moved` (test 1,276 rows, 736 positives): both 0.789 (0.761-0.817) vs tool-rate
    0.727 (0.701-0.758) - but within-segment 0.646 against 0.637, so the gap is
    between-segment calibration, not within-segment ranking.
  * `progress` (33 positives): all 0.767, tool-rate **0.774** - the floor still wins.
  * `claim_met` (430 positives of 1,031): action_only **0.877 equals the tool-rate
    0.877**; both 0.757; mechanism features 0.434 - below chance.
  The lesson of G41 survives the repair: only the action's identity transfers.

**Consequence.** The strict advance count is 51 rather than 11, and the folds now hold
events instead of zeros: hash test 23 (was 0), temporal train 13 / val 24 / test 14 (was
0 / 8 / 3). That improves the power of every quest-progress question - G41's "the test
split holds zero advancement events" no longer holds - but it does not make tool choice
identifiable from an observational log. The exploration arm (G39) remains that route.

## G44. The context features do not predict success inside a tool - the apparent win was the call's own shape *(measured 2026-10-29)*

**Symptom.** The dataset gained a bounded `context` block (exits, groups, interactives,
ready list, dialog, character) in the G39 pass, but the 42-vector `objective_features`
never read it. Adding a 24-feature mechanism block from that context to the probe
(`tools/live_outcome_probe.py`, `MECHANISM_NAMES`) produced a tempting number: on the
temporal `claim_met` target it scored **0.856 AUC** against the tool-rate 0.723.

**Decomposition - it was not the state.** On the same rows: `args_only` (which arguments
the call carries) **0.891**, `state_only` 0.724, `both` 0.868; `args_has_direction` alone
0.864. Inside `nav.goto`, where the tool is constant, the state features scored **0.468** -
below chance. A pure lookup on (tool, argument kinds) - eight cells fitted on train -
scored **0.902** on the temporal test, better than any learned model.

With the repaired after-state (G43) the illusion disappears completely: on the temporal
`claim_met` target, action_only 0.877 (= the tool-rate floor exactly), both 0.757,
mechanism_only **0.434**. The earlier 0.856 was the broken label plus
`args_has_direction`.

**What it means.** The world state as recorded does not carry a success signal for the
actions the planner takes. What does is the *shape of the call* - a direction move, a
destination move, a quest advance, an interaction - and that is a rule table, not a
model. A success head trained on these features would learn a lookup and then fail on any
state it has not seen; it is not wired.

**Gate.** `test_mechanism_features` pins the block's contract: the vector has the same
length as its names, every value is finite, ready list / group-on-my-cell / exits are
visible in the values, and the vector is **identical when the tool changes** - no tool
identity may leak into a state block. A row without context is all zeros, not a crash.


## G45. The travel primitive fails nine times out of ten, and it is the form the planner uses most *(measured 2026-10-29)*

**Symptom.** `nav.goto` is 62.4% of the agent's action time (G39) and the rebuilt corpus
now reports its success per call form in the manifest (`nav_form`):

    form         calls    map changed   moved (any key, post-lag)
    direction    1,804      185 (10.4%)      451 (25.0%)
    map target     338      268 (74.3%)      277 (82.0%)
    cell             3        0               0

Both forms are real travel: every map-targeted call names a *different* map, and a
direction call never changes the cell - it is a cross-border hop, not a walk.

**Correction, same day - that aggregate comparison is a Simpson's paradox.** Split by the
temporal folds (`nav_form_by_split`):

    fold      direction            map target
    train     255 / 1,494 (17.1%)    0 / 12   (0%)
    val        29 / 106  (27.4%)    13 / 16   (81.2%)
    test      167 / 204  (81.9%)   264 / 310  (85.2%)

The form is **not** the driver: in the training period map-targeted calls never moved
(n=12) while direction hops moved 17%; in the test period both forms move at 82-85%. The
aggregate favours the map form only because map calls are concentrated in the later
period, when movement worked at all. What varies is the map/period - the tutorial and the
first days move by edge clicks, the later sessions by direction hops - so any claim of
the form "map beats direction" from the whole corpus is confounding, and the corpus now
records the breakdown per fold so it cannot be repeated silently.

**Why the direction form dominates the training period.** `agent/planner.py` prefers the
map-targeted hop (`{"map_id": hop_map}` at line 1313) and falls back to
`{"direction": hop_dir}` (line 1321) when the route's first hop map is in the route index
but not among the client's live exits. In the training period it almost always fell back
(1,494 direction calls against 12 map calls) - that fallback rate is the thing to explain,
not the form's success.

**Consequence for the training pipeline.** In the training period movement usually
fails (17%); in the test period it usually works (82%). A model trained on the whole
corpus without a temporal split would average the two and be wrong on both. Travel is
still 62.4% of action time, so its per-period success rate is the largest throughput
lever measured - and it is a property of the map/period, not of the label or the form.

**What is instrumented.** Every rebuild records `nav_form` and `nav_form_by_split`, and
`test_claim_labels` pins the per-form counting, the per-fold breakdown, and that a non-nav
row is not counted. An earlier draft of this gate asserted "map beats direction by 20
points"; that assertion encoded the confound and was replaced.

**Open (needs a live check, not a guess).** Either teach the route index to match the
client's exits (fill the gap between `route_first_hop` and `map.exits`), or stop sending
direction hops that the index cannot confirm and read state instead. Both change live
behaviour and must be verified on a running client before either is wired; until then the
planner keeps its fallback.


## G46. The planner's reasons were thrown away, and its decisions cannot be reconstructed offline *(measured 2026-10-29)*

**Symptom.** Every planner decision carries a `why` ("objective 4: map X is 4
crossing(s) away; the top exit to Y is the first"), and `QuestPlanner._log_plan` writes
it to **stderr** only - its own docstring says the step rows carry the call but not the
plan's reason. So G45's question ("why does the direction fallback fire 5.3x more than
the map-targeted form?") could not be answered from the corpus at all: no row said which
branch fired.

**Fix - provenance.** `ToolCall.why` now exists and rides `record_meta()` into the
recorder metadata, then into the transition row as `why`; the dashboard `args` are
untouched. The derived context block also keeps each exit's `direction` (it had been
dropped, so no offline analysis could match a hop to an exit). Coverage starts at the
next live run: the 9,687 existing rows carry `why: null`, because the field did not
exist when they were written.

**A second reason the corpus cannot produce counterfactuals.** Replaying a fresh
`QuestPlanner` over 300 recorded observations that produced `nav.goto` yields
**`quest.advance` in 298 of them**. The planner's per-session state - retired quests,
attempt counters, the pending claim - is what decided to travel, and a stateless replay
does not have it. `fork()` (G39) copies that state inside a live decision; nothing in the
corpus does. So offline alternative generation is not merely unidentifiable (G39), it is
not reproducible: the recorded action cannot even be re-derived from its own observation.

**Gate.** Tests pin the contract: `record_meta()` merges the policy's own tags with the
reason and leaves `args` clean; a call with no reason records no metadata; the context
block keeps the exit direction.

**Open.** With `why` recorded, the next live run can finally price the travel branches
(G45): which reason text precedes the failing direction hops, and how many of them the
route index could have replaced with a map-targeted hop.


## G47. The loop acts on a state carried from the previous step, and the corpus never said so *(measured 2026-10-29)*

**Symptom.** 4,153 of 4,344 live steps (95.6%) decided on a **carried** observation - not
the fresh one - median age **707 ms**, p90 1,769 ms, max 3.5 s. Every feature the model
sees therefore describes a world that had already moved on, and the derived rows dropped
both `observation_source` and `observation_age_ms`, so no analysis could say which inputs
were stale.

**Fix.** The transition row now carries `obs_age_ms` and `obs_source`.

**Measured - staleness is not the missing signal.** Acting on an older state did not make
failure likelier: on the temporal test fold the age alone scored AUC 0.428 for
`nav.goto` moved/claim (slightly inverted) and 0.485-0.492 for `quest.advance` (chance).
So the stale input is a fact about the loop worth recording, not a predictor worth
feeding. It also means the earlier "state features do not transfer" results (G41, G44)
are not explained away by input age.

**Open.** Only 187 steps (4.3%) acted on a fresh snapshot. Whether the loop can be made
to observe before deciding is a live-loop question (it costs a round trip), not a
training one.

## G48. Re-issuing an identical request is the loop's dominant failure mode, and the pooled success head cannot see it *(measured 2026-10-29)*

**Symptom.** On the temporal test fold, `nav.goto` promises a map/cell step and meets it
78% of the time overall - yet after **one** identical request missed, the next identical
one met 22% (6/27), after two 0/18, after three or more 1/41 (2%). In `train` the same
gradient appears at a different base rate (78% at zero streak, then 0% / 6% / 2%), so it
is a property of the calls, not of the test period.

**Measured - the retry economics.** Over all 2,044 consecutive `nav.goto` pairs in the
corpus, restricted to pairs after a claim miss:

| what happened between the two calls | next claim met |
|---|---|
| same args, no read | 25/802 = 3% |
| same args, one read | 5/528 = 1% |
| direction changed | 14/37 = 38% |
| map changed | 18/23 = 78% |

The identical retries are not late arrivals: in all 1,330 same-args retries the
character's map was unchanged when the retry was issued. The control group - after a
**met** call - has same-args 77% (175/226) and changed-args 93% (397/428), so changing the
request is mildly good in general and decisive after a miss. Reads between retries change
nothing (1% vs 3%), which matches the loop's own comment that read-only recoveries are
ignored by its no-op streak.

**Fix - a per-rule streak.** The corpus now carries `repeat_streak` next to `miss_streak`:
consecutive claim misses of the **same tool and same args** (canonical JSON), reset by that
rule's own success, untouched by reads or other rules. Measured on the temporal test fold:
AUC for failure 0.851 inside `nav.goto` (vs 0.805 for the per-tool streak) and 0.720 inside
`quest.advance` (vs 0.595); hash-split test 0.920 and 0.708. It is the first feature in
this corpus with a real out-of-time signal, and it is the loop's own memory, not the world.

**A correction to the earlier read of the probe.** The pooled `claim_met` model does **not**
transfer: on the temporal **val** fold mechanism-only scored 0.736 (floor 0.528), but on
the **test** fold it scores 0.556 and 0.577 (floor 0.877). The reason is the tool mix: the
test period is 78% `nav.goto`-met and 6% `quest.advance`-met, the reverse of train (17% /
2%), so tool identity is the only thing that transfers and the mechanism model - which
deliberately excludes tool identity - is at chance. The 0.736 was a 268-row val accident,
not a result about state features. Within a single tool the streak features above are the
signal; across tools the pooled target is not learnable from mechanism features. (These
pooled numbers predate G49's per-form quest claim; with the corrected label the test fold
is 540/1,031 positives and the floor is 0.750 against mechanism_only 0.740 - the same
conclusion, with a target that measures something real.)

**Gate.** `test_claim_labels` pins the semantics on a synthetic six-step log: the claim is
judged against the map the call asked for; `repeat_streak` keeps its count across other
rules and resets on its own success (`[0,1,0,1,2,0]`); `miss_streak` ignores the args
(`[0,1,2,3,4,0]`).

**Does a learned head beat the counter? No.** `tools/live_outcome_probe.py --within-tool`
fits one head per tool on train rows and scores that tool's future rows, with a **paired**
segment bootstrap of the difference against `streaks_only` (two marginal intervals that
overlap say nothing about their difference). Temporal fold, `claim_met`:

| tool | fold | streaks_only | context only | all mechanism | delta all vs streaks (95% CI) |
|---|---|---|---|---|---|
| nav.goto | val | 0.761 | 0.483 | 0.609 | -0.153 (-0.213, 0.108) |
| nav.goto | test | 0.888 | 0.456 | 0.753 | -0.135 (-0.503, 0.151) |
| quest.advance | val | 0.568 | 0.500 | 0.554 | -0.014 (-0.393, 0.028) |
| quest.advance | test | 0.703 | 0.778 | 0.819 | +0.116 (0.040, 0.180) |

The context model's advantage flips sign between adjacent folds (-0.014 on val, +0.116 on
test) with 30 and 23 positives; it is not evidence. Inside `nav.goto` it is negative on
both folds. The marginal intervals are wide and asymmetric because 18 segments of very
different length are all the evidence there is; the paired delta is the statistic to read.
So the training-pipeline answer for this corpus is the counter, not a head: `repeat_streak`
is a rule that can be shipped as a gate, and a 25k-parameter success head on top of state
context does not beat it out of time.

**Open.** This is a target and a feature, not a policy. The loop still allows the same
exact call three times (`autopilot.py` no-op streak, `streak >= 3`). Whether to refuse an
identical retry after one miss is a live-policy change: it needs a shadow run against the
running client before it can be wired, and the corpus alone cannot price the alternative
request it would issue instead.

## G49. `quest.advance`'s claim is too strong for a talk, so 58% of the corpus's quest calls are labelled failures by definition *(measured 2026-10-29)*

**Symptom.** `quest.advance` is 2,848 of the corpus's judgeable calls and meets its claim
93 times (3.3%). The retry lever from G48 does not apply to it: after a miss, a same-args
retry met 2% (22/1,349) and a **changed-args** retry also met 2% (27/1,223). Repetition and
rephrasing are equally useless, so the failure is in the state, not the request.

**Measured - what the calls look like.** Split by the call's own dialog form, on all
folds (temporal test in brackets):

| form | calls | claim met |
|---|---|---|
| `npc_id` (talk to an NPC) | 1,639 (152) | 13 = **0.8%** (2 = 1%) |
| no dialog args, not in dialog | 667 (42) | 49 = 7.3% (4 = 10%) |
| in dialog, no reply/npc | 512 (185) | 31 = 6.1% (17 = 9%) |
| in dialog + reply | 19 | **0** |
| in dialog + npc_id | 9 | **0** |

The zero on `reply` is a flag with only 19 rows; the load-bearing number is the first row.
1,639 calls - 58% of the quest corpus - carry `npc_id`, and the claim only counts a change
in quest-state keys (`PROGRESS_KEYS`). A talk's own promise is that a **dialog opens**;
opening it is what the reply call needs. So the dominant call is scored as a failure even
when it does its job, and `claim_met` on this tool currently measures "the next
observation showed a quest-state change", not "the call did what it said".

**Why it matters for the pipeline.** The earlier claim fix (G42) removed walking from the
target; this one removes dialog-opening from it. Any success head trained on the current
label learns "talking is failure", which is the opposite of the tutorial path the loop
actually needs. It also explains why the within-tool context model looks unstable on this
tool (G48): the label mixes three different promises (open a dialog, send a reply, accept
a quest) into one target, and their base rates differ by an order of magnitude.

**Fix - the promise now depends on the call form**, exactly as `nav.goto` already did for
`map_id` vs `direction`. A talk (`npc_id`, no `reply`, no `accept`) promises `in_dialog`
becoming true in the after-state, or a quest-state change; a reply promises a quest-state
change; accept keeps its own promise. Measured before wiring on the clean witness: of
1,546 talks issued outside a dialog, **401 (26%) opened one** by the next observation,
while 13 changed quest state. The clean-witness requirement matters: a talk issued while
already in a dialog does not claim to have opened one (9 such rows exist).

**Measured after the rebuild.** `quest.advance` claim met goes 93/2,848 (3.3%) to
**344/2,039 in hash train, 85/463 hash test (18.4%) and 133/408 temporal test (32.6%)**.
The temporal test period is dialog-heavy: talk 112/160 (70%), reply 17/138 (12%), other
4/110 (4%). Nothing else moved - `nav.goto`'s claim is untouched.

**What it buys the pipeline.** On the temporal fold, inside `quest.advance`, the counter
goes from AUC 0.703 to **0.780** and the full mechanism model from 0.819-on-23-positives
to **0.800 on 133 positives**. The context delta over the counter is **+0.020 (0.006,
0.067) on test** and +0.099 (-0.060, 0.166) on val - small and consistent, not the fold
flip seen with the broken label. Pooled, nothing changes: with the corrected target the
test fold is 540/1,031 positives and the tool-rate floor is 0.750 against mechanism_only
0.740 - the cross-tool target is still identity-dominated. The label fix made the target
measure something real; it did not make a pooled head competitive.

**Open.** Decide whether a dialog-opening call should count as progress in reward terms at
all: it is setup, not progress, and a reward that pays for it would pay for loops that
reopen the same dialog. The corpus now distinguishes the three promises, so the reward can
pay for the reply and not for the talk, if that is the choice. There is no dialog-node id
in the observation, so a reply that walks to the next node without changing quest state is
still scored as a miss; if the client exposes the node, that becomes judgeable.

## G50. A reply's claim counted the wrong event, and a more mechanical claim is *less* learnable from state *(measured 2026-10-29)*

**Symptom.** The page publishes the dialog node - `message` plus each reply's `reply_id` - whenever
a dialog is open (458 recorded rows carry it), but the corpus only ever recorded whether a
dialog was open. So a reply was scored on quest-state change: 41/612 (6.7%) met. Measured
against the node identity on the 160 replies where both states are judgeable, **119 (74%)
moved to another node or closed the dialog** and only 25 changed quest state. The dialog's
working path was scored as failure.

**Fix.** `_dialog_sig(obs)` is `closed`, or a hashable `(message[:160], reply ids)`; a reply
promises that the node changes, and returns `None` (unknown) when there is no before-node
to compare. A talk keeps its own claim (G49), and a quest-state change still counts as met
for either. After the rebuild (both datasets): replies are met 146/157 in hash train and
104/128 on the temporal test fold (81%); talks stay at 26%; `other` quest calls stay at 3%.

**The measured lesson - label choice decides whether there is anything to learn.** Inside
`quest.advance` on the temporal test fold, with the *mixed* label (G49: talk = dialog,
reply = quest state) the counter scored 0.780 and the mechanism model 0.800. With the more
mechanical reply label the counter is 0.769 but the mechanism model falls to **0.652**, and
the paired delta is **-0.117 (-0.139, -0.063)**. Nothing about the state changed; the target
did. A reply moving to the next node is decided by the dialog's own script, which the
observation does not contain, so the more honest the mechanics label, the less the world
state can predict it. The counter (repeat/miss streak) is the only feature that survives
every label in this corpus.

**Open.** Keep the two targets separate: `claim_met` answers "did the call do its mechanical
job" (talk 26%, reply 81-93%, other 3%); `advance` answers "did the quest progress" and is
the reward target. `advance` still holds only ~51 events, so a progress head is data-blocked
regardless of how good the mechanics label becomes.

## G51. The rule-keyed streak is a shippable veto; the tool-keyed streak is not *(measured 2026-10-29)*

**Question.** A feature that predicts failure is not yet a policy. The only decision the
corpus can price offline is the reject option: refuse a call whose own history says it will
fail, and re-plan. `tools/live_outcome_probe.py --gate` scores that on the temporal **test**
fold: calls vetoed, their success rate, the successes kept, and a paired segment bootstrap
of success(kept) - success(vetoed).

**Measured (`claim_met`, temporal test).**

| tool (base) | rule | vetoed | vetoed met | kept met | successes kept | lost /1,000 | diff CI |
|---|---|---|---|---|---|---|---|
| nav.goto (78%) | repeat_streak >=1 | 86 (17%) | 8% | 92.5% | 98.3% | 13.6 | 0.653..0.885 |
| nav.goto | repeat_streak >=2 | 59 (11%) | **2%** | 88.4% | **99.8%** | 1.9 | 0.0..0.574 |
| nav.goto | miss_streak >=1 | 95 (18%) | 25% | 90.5% | 94.0% | 46.7 | 0.06..0.876 |
| nav.goto | miss_streak >=2 | 60 (12%) | 2% | 88.5% | 99.8% | 1.9 | 0.0..0.366 |
| quest.advance (55%) | repeat_streak >=1 | 115 (29%) | 20% | 69.6% | 89.5% | 57.8 | 0.372..0.625 |
| quest.advance | repeat_streak >=2 | 71 (18%) | **3%** | 66.7% | **99.1%** | 5.0 | 0.658..0.719 |
| quest.advance | miss_streak >=1 | 155 (39%) | **65%** | 49.0% | 54.1% | 253.8 | **-0.288..-0.055** |
| quest.advance | miss_streak >=2 | 43 (11%) | **77%** | 52.7% | 85.0% | 82.9 | **-0.36..-0.141** |

**What it says.**
- **`repeat_streak >= 2` is the gate.** It vetoes 11-18% of calls that would have met ~2-3%
  of the time and keeps 99%+ of the successes; the paired CI excludes zero on both tools.
  On `quest.advance` the effect is larger (0.658..0.719) than on `nav.goto` (near the
  boundary, 0.0..0.574, because the folded segments are few and large).
- **`miss_streak` must not be used as a gate.** On `quest.advance` it is *anti*-predictive:
  the calls it would veto met 65-77% of the time, and the CI of the difference is negative.
  The reason is the two interleaved call forms: a talk miss is followed by a reply that
  usually works, so a per-tool streak counts unrelated successes as failures. It is a
  feature because the model can weight it against the call form; it is not a rule.
- The per-tool probe (G48) said the same thing from the other side: `repeat_streak` beat
  `miss_streak` inside both tools. The gate probe turns it into the decision.

**Open - what a veto costs is still unpriced.** The corpus says what the vetoed call would
have done; it cannot say what the loop would do instead. If the replacement is another
identical call, the gate buys nothing; if it is a re-plan to a different request, the gate
converts a ~2% call into whatever that request achieves, which is the 38-78% measured for
changed args in G48 - but that comparison is confounded by why the planner changed them.
This is the same counterfactual wall as G39/G46: a live shadow run is the only way to
price the replacement, and it needs the user's sign-off.

## G52. The retry decision is a step at the second miss, and the richer state-change policy does not survive out of time *(measured 2026-10-29)*

**The problem, stated properly.** Retrying a failed call is optimal stopping: attempt
again only while p(success | what happened since the failure) is at least the success rate
lambda of the alternative. Two candidate state variables: the count of consecutive
identical failures, and whether the world changed between the failure and the retry.

**Measured (`--stopping`, retries of a just-failed rule).**

| fold | tool | immediate | changed | other-action |
|---|---|---|---|---|
| train | nav.goto | 1/711 = 0.3% | **26/28 = 90%** | 0/475 = 0.2% |
| train | quest.advance | 10/895 = 1.2% | 4/119 = 4% | 0/432 = 0.2% |
| **test** | nav.goto | 5/35 = 16% | **2/21 = 13%** | 0/30 = 3% |
| **test** | quest.advance | 9/36 = 26% | 12/68 = 19% | 2/11 = 23% |

(`immediate` = the previous step was the same rule; `changed` = another action ran and the
cell or dialog differs from the failure; `other-action` = another action ran and the state
is identical. Intervals are cluster bootstraps over segments.)

**The state hypothesis is refuted out of time.** On train, a retry after a state change
succeeds 90% against 0.3% for an immediate retry - the number that would justify a
"retry only after the state changes" policy. On the temporal test fold the same comparison
is 13% against 16%, with overlapping intervals, and for quests 19% against 26%. The train
figure is a 28-row artifact. G48's "changed args succeed 38-78%" is the same selection
effect and must not be read as causal; G51's gate is the only rule that replicates.

**What does survive is a step at two consecutive misses.** Temporal test, `nav.goto` claim
met by `repeat_streak`: 0 -> 93% (396/428), 1 -> 22% (6/27), 2 -> 0/18, >=3 -> 1/41 (2%).
The drop is not at the first failure but at the second, and it is why G51's
`repeat_streak >= 2` gate keeps 99%+ of the successes while vetoing 11-18% of calls. The
hazard is a threshold, not a smooth decay, and the immediate/delayed distinction adds
nothing once the count is known.

**A modelling trap worth recording.** A logistic fit of p(success) on `repeat_streak`
looks fine and is wrong: in train, calls with `repeat_streak` exactly 3 met 21/37 (57%),
because the counter persists across state changes and a rule retried much later in a new
situation can succeed. Any smooth hazard fit either flattens the cliff at 2 or has to
average across these strata. Fit the step, or condition on the stratum - do not fit a line
through the counter.

**Open.** No state-dependent stopping policy is identified from this corpus: the natural
candidate fails out of time, and the counter is the only variable that replicates. A richer
policy needs the randomized arm (off, needs sign-off) or a live shadow run; the counter
threshold is the only rule that can ship today.

## G53. The gate preserves progress; progress itself is the binding constraint *(measured 2026-10-29)*

**Does the gate cost progress?** G51 prices it on the claim label. The reward target is
`advance` (an objective completes), so the gate was re-scored on it. On the temporal test
fold the `repeat_streak >= 2` gate vetoes 59 `nav.goto` and 71 `quest.advance` calls and
loses **0 of 11 advances and 0 of 27 progress events**; on the hash split it loses 3 of
39 advances (all in train) and on the temporal split 0 of 23. (The two datasets are the
same rows split differently, so the counts are not additive.) The calls it refuses are, on this evidence, calls
that never produced progress.

**What that also shows - there is almost no progress to learn from.** Over 4,943 judgeable
actions (9,687 rows include reads):

| label | events | per 1,000 actions |
|---|---|---|
| claim met | 1,385 | 280 |
| progress | 126 | 25.5 |
| advance | 51 | 10.3 |

Action time is **7.8 h** across a **157.8 h** wall span at 5.71 s per step. At this
density, 100 advance events - a minimum for any progress head - need **9,692 actions =
15.4 h of action time**, which at the observed arrival rate is roughly four days of
wall-clock. That is the training pipeline's real budget, and it is why every head in this
audit was evaluated on claims.

**Why that 5% is not a duty cycle.** Decomposing the 171.3 h span over 222 segments:
9.3 h inside segments, 161.9 h between them, and **0.0 h of within-segment waiting** - because
the builder splits a segment on a 120 s silence, so every long hold (a spawn wait, a
tutorial hold, an offline break) *becomes* a new segment. The corpus therefore cannot tell
"the bot was off" from "the bot was waiting 3 minutes", and 5% is a lower bound on idle,
not a measured utilisation. Deriving throughput from these segments is invalid; it needs
the raw log's decision rows and timestamps. The derived corpus is for learning, not for
time accounting.

**Where the events are.** The temporal train period (early tutorial content) advances 7 of
1,994 quest calls (3.5 per 1,000); the test period (later content) advances 10 of 398 (25
per 1,000) - **7x denser**. The corpus is 60% `quest.advance` calls but most of them are
tutorial steps that cannot advance anything. Collecting more tutorial hours cannot fix the
reward scarcity; collecting the same hours on later quests can, by roughly a factor of 7.

**Why this matters for the plan.** Model capacity, features, architecture and Kaggle time
are all irrelevant to the bottleneck the last four passes measured. The corpus has 280
well-powered claims per 1,000 actions (enough to learn mechanics) and 10 advances
(not enough to learn progress). Either the reward target changes to something denser
(claims, per-form progress), or data collection targets advance-dense content. A 30M model
on 51 events would memorise 51 events.

**Open.** The 5% duty cycle is itself a throughput finding: the loop's wall time is mostly
waiting, so even perfect labels would arrive slowly. It is measured here, not explained;
the wait/offline breakdown is a separate instrument (G47's obs age is not the same thing).

## G54. The gate replicates across four rolling-origin cuts, so it is a rule and not one period's luck *(measured 2026-10-29)*

**The objection G51 left open.** One temporal cut is one future, and the nav interval even
touched zero (0.0..0.574). A gate chosen because it looked good on that cut could be
fitting the period. `--rolling` walks the cut through the segment start order: train on
everything before the cut, score the next 15% of segments, repeat at 40%, 55%, 70% and
85% of the timeline (about 40 test segments each, 269 segments in total).

**Measured - `repeat_streak >= 2`, `claim_met`.**

| cut | nav base | nav vetoed met | nav kept met | nav AUC(fail) | quest base | quest vetoed met | quest kept met | quest AUC(fail) |
|---|---|---|---|---|---|---|---|---|
| 0.40 | 0.04 | 0.016 (808) | 0.275 | 0.939 | 0.01 | 0.000 (665) | 0.134 | 0.973 |
| 0.55 | 0.18 | 0.100 (60) | 0.283 | 0.753 | 0.06 | 0.000 (31) | 0.087 | 0.771 |
| 0.70 | 0.36 | 0.316 (19) | 0.380 | 0.655 | 0.44 | 0.222 (9) | 0.457 | 0.592 |
| 0.85 | 0.80 | 0.018 (56) | 0.895 | 0.853 | 0.56 | 0.029 (70) | 0.684 | 0.726 |

Every cut has a positive kept-minus-vetoed gap and AUC above chance, across base rates
from 1% to 80% - the rule survives the two regime changes the corpus contains (the early
failure-heavy period and the late dialog-heavy one). The weakest cut is 0.70, where only 19
and 9 calls were vetoed: there the evidence is thin, not negative.

**Why it matters.** G51's gate can now be quoted without the single-cut caveat: on four
independent future windows it keeps calls that succeed 27-90% of the time and refuses calls
that succeed 0-32% of the time. The remaining uncertainty is what the loop does *instead* of
the refused call, which is still unpriced (G39/G46/G52) and needs a live shadow or the
randomized arm.

## G55. The loop already contains the replacement, and takes it only 17% of the time *(measured 2026-10-29)*

**The question the gate left open.** G51/G54 price what a vetoed call *would have done*; they
cannot say what the loop would do instead. The corpus cannot answer that counterfactually,
but it does show what the loop actually did next after 2,707 high-streak failures
(`repeat_streak >= 2`, claim missed):

| next step (reads skipped to the first action after them) | share | its claim outcome |
|---|---|---|
| the same rule again | 1,482 (55%) | **6/1,482 = 0.4%** |
| a read, then the same rule | 540 | **1/540 = 0.2%** |
| a **different action**, no read between | 459 (17%) | **349/459 = 76%** |
| a read, then a different action | 217 | 65/217 = 30% |

(The 763 read-next rows split into the 540 + 217 above; the remainder were end-of-segment.)

The most common transitions are `quest.advance -> quest.advance` (827) and
`nav.goto -> nav.goto` (636) - the pathology - against `nav.goto -> state.snapshot` (503)
and `quest.advance -> other action` (298 and 49).

**What this means.** The successful replacement is not hypothetical: when the loop breaks
the repeat and does something else, it succeeds 76% of the time, against 0.4% when it
repeats. It takes that branch only 17% of the time. A veto alone would therefore not be
enough - it would remove the 55% repeat branch without telling the planner what to do - but
the loop demonstrably has a working alternative, so the gate should be paired with a
forced re-plan (retire the current rule for the step, ask again), not with an idle wait.
Reading is not a recovery: read -> same rule is 0.2%, *worse* than repeating directly,
because the read costs a step and the request is unchanged. The recovery has to change the
rule.

**The caveat that keeps this descriptive.** The 76% is selected: the loop switches when the
situation has changed (a quest became ready, a dialog opened), so part of that success is
the state, not the switch. It is the same selection effect that refuted the state-change
policy in G52. What survives is the asymmetry - repeating at 0.4% for 55% of the
opportunities - which no selection can explain away, because repetition is the branch taken
when nothing changed.

**Open.** Price the re-plan on a live shadow run: record the veto and the next planned call,
then compare the outcome of "repeat" against "re-plan" on the same states. The three-way
choice (repeat / re-plan / wait) is where the remaining value is, and the corpus has said
everything it can about it.

## G56. A forest ranks better than the counter; at the shipping point it is identical *(measured 2026-10-29)*

**The challenge.** Is a regression / more complex model actually more accurate than the
`repeat_streak >= 2` rule? Tested on the temporal fold, judgeable rows only (train 3,598 /
681 positives; test 1,021 / 627), features = the 26 mechanism values + tool one-hot + the
step flag. (A first run of this comparison counted reads - `claim_met` None - as failures
and produced nonsense; the probe filters them, ad-hoc scripts must too.)

| model | AUC (segment CI) | Brier | at ~15% veto: vetoed met | successes kept |
|---|---|---|---|---|
| rule (`streak < 2`) | 0.683 (0.618..0.752) | 0.246 | **2.6%** | **99.4%** |
| logistic (same features) | 0.674 (0.550..0.766) | 0.305 | 45.3% | 85.3% |
| gradient boosting | 0.838 (0.783..0.870) | 0.169 | 3.9% | 99.0% |
| random forest | **0.849** (0.772..0.872) | **0.157** | **2.6%** | **99.4%** |

**Reading it correctly.**
- The rule's AUC is **tie-capped**: it is a 0/1 score, so 85% of pairs are ties. AUC is the
  wrong yardstick for it; the operating point is the right one, and there it is not beaten.
- Ranking does improve a lot (0.68 -> 0.85). The forest can also work at 5% veto (kept
  100%, vetoed met 0%) and degrades gracefully at 20-40%, where the rule is a single step
  at 14.8%. The forest gives a *menu* of operating points; the rule gives one.
- **At the point you would actually use, they are identical** (99.4% kept, 2.6% vetoed
  met). The forest's extra ranking power lives *inside the kept set* - separating talks
  from replies - which is for choosing among calls, not for vetoing them.
- The logistic is worse than the rule because the test base rate is **61% against 19% in
  train**: a probability model fit on train is miscalibrated out of time (Brier 0.305,
  veto set 45% positive), while the count rule has no intercept to miscalibrate. In a
  drifting environment that is an argument for the count, not against it.

**Per tool - the same answer, and the caution is sharper.** Same features without the tool
one-hot, same fold, 15% veto chosen by rank (the rule keeps its own 11%/18% step):

| tool | model | AUC | Brier | vetoed met | successes kept |
|---|---|---|---|---|---|
| nav.goto | rule | 0.760 | 0.105 | 1.7% (11%) | **99.8%** |
| nav.goto | logistic | 0.739 | 0.326 | 61.0% | 88.3% |
| nav.goto | boosting | 0.888 | 0.096 | 33.8% | 93.5% |
| nav.goto | forest | **0.930** | 0.085 | 7.8% | 98.5% |
| quest.advance | rule | 0.689 | 0.279 | 2.8% (18%) | 99.1% |
| quest.advance | logistic | 0.657 | 0.271 | 3.3% | 99.1% |
| quest.advance | boosting | 0.729 | 0.247 | 3.3% | 99.1% |
| quest.advance | forest | 0.728 | **0.210** | 3.3% | 99.1% |

The forest ranks `nav.goto` far better (0.930 vs 0.760) yet still keeps fewer successes at
15% veto than the rule does at 11%, and boosting - the second-best AUC - is the *worst*
veto of the five (93.5% kept). AUC and the veto are not the same objective: a model can
order the middle of the distribution better and still put a rare success in its veto tail.
On quests every model lands on the same 99.1%. So the per-tool evidence does not overturn
G50 or G51: the counter is still the thing to ship, and a forest is a candidate *ranker*
for choosing among the kept calls, not a better veto.

**What this does not settle.** A rolling-origin check of the forest's operating curve (the
rule has one, G54) and its use for ranking alternatives - the decision the corpus cannot
observe. Until a model beats 99.8% kept at <=11% veto on `nav.goto` out of time, the rule
remains the right thing to ship.

