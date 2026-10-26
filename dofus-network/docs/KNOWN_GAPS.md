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

**Plan (do not skip the live step).** (1) read-shape change: expose
`presets:[{presetId,name}]` in `_agentSnapshot` — but *only after* a live client shows
where the preset list actually lives (guessing a field name is the bug class that made
this project undebuggable, per `CLEANUP.md`). (2) add an `equip.preset` capability + a
JS handler sending `InventoryPresetUseMessage{presetId}` and verifying via
`InventoryPresetUseResultMessage`, behind the same schema gate. (3) validate the packet
and verdict on a live session — never blind, because a wrong `presetId`/state is a
kick-adjacent action. Until (1)-(3) are done, any quest step gated on "equipped X" is a
hard stop, not a delay.

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
- delegated into `agent.regression` (82 checks, 0 failed), which prints the live
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

## Priority, if these were ordered by what blocks a real run

1. **G1** keep-alive — it is why healthy runs still die. Implemented; the live logs show
   `INACTIVITY` in 5,106 observation rows, which is the mechanism confirmed, not the fix.
2. **G13** the 287-refusal claim loop — the measured dead-loop against criterion 3. Fixed
   behind a mutation-tested gate; needs one live run to show the refusal stops at one.
3. **G12** map-transition resilience — **closed in the pipeline 2026-10-26** (rules in
   patch-engine + patch-rules, bundle rebuilt, selftest IDENTICAL, js-error-finder 23/0);
   watching a real stall self-heal is still owed.
4. **G3** dialog reasoning — it is why the talk objective never completes (`objectives +0`
   in every baseline session), and its dialog-close null-deref is now fixed.
5. **G8** test gate — 82 checks, delegating JS + Go + bundle `--check`; green.
6. **G2** equip, **G5** forecaster, **G7** fight proof, then G4/G6/G9/G10.
