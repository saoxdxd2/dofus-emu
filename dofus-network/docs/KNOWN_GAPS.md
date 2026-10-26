# KNOWN GAPS — what is not true yet, with the evidence

An honest list of the things the system **cannot** do or **does not** verify
today. Each entry gives the symptom, the code or log evidence, why it matters, and
what closing it would take. This is the counterpart to `CURRENT.md`: that file
maps what runs; this file maps what is missing or unproven.

The rule that generated this file: a gap you can point at in the code is worth
more than a worry. Everything below is pointed at. Anything I could not verify is
labelled *unverified*.

---

## G1. Server-visible keep-alive → vanilla AFK disconnects *(implemented 2026-10-26, live-unverified)*

**Symptom.** A run ends with the game's own popup — *"Prolonged inactivity will
lead to automatic disconnection… Show some sign of life (move, send a chat
message…)"* — immediately followed by `AI_DISCONNECT reason=INACTIVITY`. This has
looked like a ban ("got kicked for inactivity") and is not one.

**Cause.** The server's AFK timer counts *wire traffic the server can see*. The
loop's observation step is `state.snapshot`, which is a **pure local read**
(`_agentResult(true, _agentSnapshot())` in `scripts/mobile-disguise.js`) — it sends
no packet. A `noop` or `blocked` action likewise changes nothing on the wire. So a
loop that is *busy reading and deciding* is *silent to the server*, and the timer
fires. The only anti-AFK traffic in the client is `GameFightTurnFinishMessage
{isAfk:false}` in `mobile-disguise.js`, and that is **fight-scoped** — it never
fires in the overworld.

**This is not bot inspection.** There is no detection mechanism being tripped; it
is the ordinary inactivity timeout any idle player hits. When the loop also crashed
(`[AI] loop finished (exit -1)`) just before such a disconnect, the crash left the
client genuinely idle and the AFK timer finished the job.

**Fix — implemented 2026-10-26, pending live proof.** The mechanism was found in
the bundle: the client tracks activity in `connectionManager.recordActivity()`
(sets `hasSeenActivity` + clears the inactive warning), and its own `_pingServer`
timer then sends `BasicPingMessage {quiet:true}` — the exact "sign of life" a human
produces; with no activity it escalates `_enterInactiveMode()` →
`disconnect("INACTIVITY")`. The adapter now calls that flag three ways
(`scripts/mobile-disguise.js`): on every directive send (`_cmSend`, `sendPacket`,
`chat.say`), and via a gated heartbeat in `bindNetworkHooks` that fires every 20 s
**only while the bot is driving** (`enabled || aiRunning`), cleared on disconnect.
It invents no packet and does not touch the egress/session pin. Guard:
`node tools/test_keep_alive.js` (15 checks). **Still *unverified*:** that the real
server treats `BasicPingMessage` as activity for a long enough stretch to clear the
countdown, and that a pure read-only wait (the "tutorial is holding this step open"
branch) is covered — both need one live session, which is the only honest proof.

---

## G2. Equip: no verb yet — mechanism identified, implementation gated on a live capture

**Symptom.** When an NPC instructs the player to *equip* something (the referenced
session's "equip equipment" case), the agent has no verb for it. The 21-tool
vocabulary (`CURRENT.md` §7) has no `equip`/`wear`/`preset` action, so the step
cannot be satisfied.

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

## G6. Verifier head is wired by the GUI, not orphaned — and unmeasured

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

**Decision + falsifier.** Keep the rule as authority for actions. The GUI
(`aipanel.cs:705-708`) should pass `--verifier-model` only for an artifact carrying a
validation contract measured on **live or human labels**, not because the file
exists, and not on self-referential rule labels. Falsifier that flips this: a head
trained and validated on held-out *real* step labels above the rule baseline — then
wiring it by default is correct, and this gap closes.

---

## G7. Live combat is unverified against the real server

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

---

## G8. The test gate is gone — partially restored 2026-10-26

The `agent/smoke_verifier.py` gate (last reported 110 checks) was deleted on
2026-10-25 along with the rest of the Python test suite. It has been **partly
rebuilt**: `python -m agent.regression` now runs 82 offline checks against the real
code paths — the verifier rule engine (all four verdicts, the nested `position.*`
aliases, the `ready_objectives` claim-proof, read-only-answered-is-advanced), the
verifier model contract (a baseline json loads; a wrong-kind artifact degrades to
rules; the read-only model-label guard), the tool-vocabulary equality
(`schema.TOOLS` == `capabilities.json` == `features.tool_registry()` == 21), the
`hdv.buy` ban, the objective-type buckets, the dialog question tie-break, and
prompt/named-mob fidelity (`_goal_mode` + `Autopilot._farm_allowed`: a quests prompt
never farms unless the objective is a delivery; `_farm_target` +
`QuestPlanner._visible_target`: a named mob stays that mob), plus the recovery
layer's no-repeat/terminate invariant. It **delegates the JS page suites**
(`tools/test_keep_alive.js`, `tools/test_npc_actor.js`) to `node`, **the read-only
bundle-resilience classification** (`tools/apply-map-resilience.js --check`, see G12) to
`node`, **and the Go relay tests** (`go test dofus-net-proxy.go *_test.go`) to `go`. **Still not covered by any
single gate:** the C# `DofusFarm.exe --bundle-selftest`, the combat/data suites,
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

## G9. Identity is keyed by slot, not by account

The bridge/instance layer addresses an instance by its **slot → port** mapping
(`bridge_port.txt`, relay port `8880+slot`). Nothing binds a run to the *account*
logged in on that client. On a multi-instance host, "which character did this step"
is answered by which slot, and a slot that reloaded into a different login is
indistinguishable to the harness. Relevant to the "different exit IP looks like a
hijacked session" class of disconnect.

---

## G10. `kaggle/toolkit/` drifts with no generator

`dofus-network/kaggle/toolkit/` is a hand-assembled copy of `agent/` + `sim/` that
backs the published Kaggle dataset (`tajdinenizar/thx-02-sim-toolkit`) a live kernel
runs. It has **already drifted** (its `autopilot.py` and `planner.py` differ from the
harness) and there is no export script in the repo to regenerate it. It is not a fork
to delete — deleting it breaks the dataset a kernel depends on — but it is a fourth
copy of the harness that will keep diverging. Fix: an export script, or accept the
drift explicitly.

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

## G12. The live client has no map-transition resilience at all — a stalled map load locks the screen forever

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

## G29. The quest knowledge cache cannot describe any live quest: its ids stop at 2,546, the live ids start at 3,841 *(measured 2026-10-26, open)*

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

**Next step, and it is a capture rather than a download.** The client carries the Touch quest graph
locally: its database manifest (the name list in `script.js` that `e.exports.load` walks) contains
`Quests`, `QuestSteps`, `QuestObjectives`, `QuestObjectiveTypes`, `QuestStepRewards` and
`QuestCategory`, and the page already reaches one of them — `_rawObjectiveDb` reads
`window.gui.databases.QuestObjectives[id]`, which is where the 99.4 % of objective entries that carry
a class name get it (`_type`). So the KB half does not need a third-party encyclopedia and must not be
filled by inventing a `type_id`→class enum: publish what the client already resolves (the objective
rows it holds, id → `_type` + declared parameters, and `QuestObjectiveTypes` as the client uses it),
ingest them as the store's `source: "observed"` rows, and let coverage be measured instead of
assumed. Until that capture runs, the four `type_id`↔class pairs the live corpus happens to name
(0/1/4/6) are all this project may claim.

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
`startquest,<id>` link in the dialogs this character reaches is the first thing the next live snapshot
has to answer, and until it does, the accept *verb* must not be wired: a page-level guard can refuse a
request when no offer is present, but it cannot invent the offer's shape.

---

## G33. `quest.advance` cannot accept a quest while a dialog is open — the accept branch is behind it *(measured 2026-10-26, open)*

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
9. **G8** test gate — **431 checks**, delegating JS + Go + bundle `--check` and a whole-file
   `node --check` of the injected patch; green, and every new
   section is mutation-tested: `tools/mutate_cause_gate.py` now holds **194 mutations**, all run
   and all caught on 2026-10-26 (the last full sweep on a quiet tree: 194/194, 0 missed,
   0 anchor-skipped, restored tree re-ran 431/0; the batch that added G30 and G31 was 39/39 first). The
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
    closed by the source's licence, and it is what stands between this batch and a retrained label
    set. None of the six is live proof — see each section's *Not verified*.
12. **G5** forecaster, **G7** fight proof, then G4/G6/G9/G10.
