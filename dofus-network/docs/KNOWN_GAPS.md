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
session to prove the accessor chain resolves on the running build. (2) add an `equip.preset`
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

**Verified:** 34 gate checks (0 failed inside the current 206-check gate) driving the real loop end to end with a stub
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
belong to `work the 0 goal` and `work the 1 goal` respectively; 0 failed in the current 206-check gate.
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

**Verified:** 19 gate checks (206 total, 0 failed) — the four outcome cases, features stored with
the pair, no row for a non-dialog step, no row with no dialog open, the call site on the step path
where both states exist, and the report reading every row kind the loop writes (including a
pre-instrument log saying *predates*, not *zero*). 5 more mutations cover it in
`tools/mutate_cause_gate.py` (**42 total; 33 run and caught, 9 pending** a quiet-tree re-run).
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

**Verified:** 12 gate checks (206 total, 0 failed) — id/handler recorded, no-dispatch-data row
still written, a refused send kept as `dispatch_ok: false` with its reason, `objective_type`
travelling with the row, `page_after` empty-but-open vs closed, `ended_by` for both closers, and
the report printing the "dialog ended by" census, the type ids, the refused-send count and a
`NOTE re-asked N x` line naming the page.

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

**Verified:** 10 gate checks (206 total, 0 failed) driving the real `_dialog_call`: first ask,
one retry, the third ask turned into a close, the refusal after that, the `exhausted` status,
a different objective re-asking, a page that moved re-asking, and the fork inheriting the memory.

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
6. **G14** repair-label taxonomy — the 455 `unknown` rows are now classified and the
   decision row records the game outcome; the data criterion (4) trains on exists. Needs the
   next run's histogram to show it helps.
7. **G18** the read-as-action step mix — 3,010 of 4,287 baseline steps were looking, 3,005
   of them scored `advanced`. A wait now costs time instead of a step, bounded at 40 passes.
   This is the number that makes criterion (2) readable at all: read `steps`, `waits` and the
   productive rate together, not `steps` alone.
8. **G8** test gate — 206 checks, delegating JS + Go + bundle `--check`; green, and the
   new sections are mutation-tested (`tools/mutate_cause_gate.py`: 33 of 42 mutations run
   and caught, 9 more written and awaiting a quiet-window re-run while the tree is not being
   edited or built). G21 and G22 have no mutation coverage yet.
9. **G20/G21** dialog outcomes — the ranker's primary signal fired on 0 of 1,547 recorded dialogs
   and 90 of 110 recorded replies left the page unchanged; the pairing plus the dispatch fields are
   now logged, so the first live talk attempt produces the labels a scorer needs. Training on them
   is the next step, not this one.
10. **G5** forecaster, **G7** fight proof, then G4/G6/G9/G10.
