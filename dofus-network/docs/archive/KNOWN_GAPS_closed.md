# KNOWN GAPS — closed entries (archive)

Every entry here was removed from `../KNOWN_GAPS.md` because its own heading said
`(fixed ...)`. The register keeps a one-line row for each, so the pointer survives;
the evidence, the measurements and the reasoning are below, unchanged.

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







