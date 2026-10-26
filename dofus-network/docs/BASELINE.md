# Live baseline — measured, not remembered

Criterion (5) of the Full Auto goal asks for a recorded live baseline before any
improvement can be claimed. This is that record. Every number here is read out of the
**live step logs of real sessions on the real server**, not from an offline test.

## Provenance

| source | what it is | size |
|---|---|---|
| `dofus-network/trajectories/default.jsonl` | recorder view (observation + step rows) | 50 MB, 4,785 rows |
| `%LOCALAPPDATA%/DofusFarm/autopilot.jsonl` | loop view (step + decision rows) | 28 MB, ~4,300 steps |
| `.../autopilot.20261025-232150.jsonl`, `default.20261026-074102-070180.jsonl` | rotated predecessors of the same runs | 81 MB / 67 MB |

Sessions span **2026-10-25 22:21 → 2026-10-26 ~02:46 UTC** (09:10 local +7h shown by the
report). Measured with the two commands at the bottom; both are read-only.

**These runs predate every fix made on 2026-10-26** (G1 keep-alive, G3 dialog ranking,
G12 map-transition resilience, the restored gate). They are a valid *before* picture and
must not be compared against anything except a re-run of the same two commands.

## Headline numbers

```
trajectory view : 4 sessions,  57.0 min wall, 4012 steps, 3982 ok,  30 failed, objectives +0, maps 9
autopilot view  : 7 sessions,  40.6 min wall, 4287 steps, 3770 ok, 517 failed, objectives +0, maps 15
                    recoveries: recover 523, replan 407, retry 21
combined span   : 8 sessions, 172.5 min total, longest single session 125.1 min
verdicts        : advanced 3063, noop 707, failed 493, blocked 24
productive view : of 1,277 steps that could change the world, 58 moved it (4.5%)
                    quest.advance 32/972 (3.3%)   nav.goto 26/222 (11.7%)
                    farm.start 0/59              fight.join 0/24
                    (3,005 of the 3,063 `advanced` verdicts are state.snapshot reads)
decision causes : none 3063, unknown 455, plan_wrong 381, incomplete 361, unavailable 26
disconnects seen: INACTIVITY 5106, (in_world:false with no reason) 142
failed by tool  : quest.advance 504, fight.join 48, nav.goto 25, state.snapshot 15, farm.start 3
```

## What the numbers say

1. **Zero quest objectives completed.** `objectives +0` in every single session, across
   both views and ~4,300 steps. This is the goal's headline failure and it is measured,
   not inferred — it is the live confirmation of G3: the run navigates, talks, fails and
   repairs, but never completes an objective.
   **The sharper form of the same number, added after G16:** the run's own verdict
   histogram (`advanced 3063`) is not progress. 3,005 of those are `state.snapshot` reads,
   which the verifier correctly scores as success because a lookup that returns data did
   what was asked. Excluding reads and listings, **58 of 1,277 world-changing steps moved
   anything at all — 4.5%** (`quest.advance` 3.3%, `nav.goto` 11.7%, `farm.start` and
   `fight.join` both 0%). **The mechanism behind that number is G17: 480 of those steps were
   accepted claims scored `noop` because the settle loop gave up at ~0.8 s while the walk-and-talk
   the client had queued landed 1.2 s later.** Criterion (2) is measured by that line, not by the 71%
   `advanced` share, and `recovery_report.py` now prints it under
   *productive-action success* so no future read repeats the mistake.
   **And the reason those 3,005 reads were there at all is G18:** every wait in this loop — the
   planner's dialog grace and spawn wait, the thinking layer's "wait for the NPC / a map change",
   the controller's recovery hint — returns `state.snapshot` as its action, so the run was not
   *taking* those steps, it was *waiting*, 82% of its recorded time between two looks.
2. **The AFK kick is the dominant disconnect, empirically.** `INACTIVITY` appears in
   5,106 observation rows — by far the most common reason recorded for the client leaving
   the world. That is G1's mechanism observed in live data, which is the strongest
   evidence available without a fresh session; it says the fix is aimed at the right
   timer, not that the fix works yet.
3. **The top single failure is a quest-claim kick, not a bug in the guard.** 281 rows
   read "a previous claim of objective 14070 of quest 3841 ended the connection; perform
   ...". The claim-proof is *reporting* a connection the server already ended; the
   failure to explain is why the first claim of that objective cost the session. Same
   class as the exit-IP/looks-like-a-hijacked-session case in G9. **Root-caused after this
   snapshot** — the loop re-sent the refused claim 287 times at a 3.7 s median because the
   retry bound was wiped by the scene reset and could never fire; see KNOWN_GAPS G13.
   Expect this row to collapse to a handful in the next baseline, and treat it as unproven
   until then.
4. **148 failures are a real null-deref in the dialog close path**: `dialog close
   failed: TypeError: Cannot read properties of null (reading 'id')`. That is client-side
   code reading `.id` on a null dialog object — a defect, not a game rule, and it sits
   directly on the dialog path that #1 above says never completes.
   **Fixed after this baseline was taken** (2026-10-26): each client close path now has
   its own `try`, the unproven `sendMessage('LeaveDialogRequestMessage', null)` guess was
   dropped, and success is decided by re-reading the dialog state instead of by which
   call survived. Behaviourally covered by `tools/test_dialog_close.js` (13 checks, in
   the gate). Do not re-fix it; re-measure it instead — this row should go to zero in the
   next baseline, and if it does not, the diagnosis above was wrong.
5. **21.7% of steps were repair** (951 recover/replan/retry out of 4,287). The recovery
   layer keeps sessions alive but is spending a fifth of all activity on it, and 455
   repairs were classified `unknown` — the cause taxonomy is not yet covering what it is
   actually reacting to.
   **Root-caused after this snapshot, and it was two bugs, not one.** (a) The decision row
   logged the *transport* result (`result.ok`, `result.error`) instead of the game-level
   outcome the controller was handed (`step_ok`, `step_error`), so 455 rows read "succeeded
   with no error" while the bridge had in fact reported a refusal — an offline analysis of
   *why* a repair failed was reading a field that is always null. (b) `cause_of()` matched
   none of the seven phrasings these refusals actually use, so `wrong_target`,
   `stale_state` and `invalid_assumption` had **zero** live rows while `unknown` held 455 —
   the strategy table cannot rank a recovery for a bucket it never fills. Replaying all
   4,286 live decisions through the fixed classifier moves `unknown` 517 → 0 (313
   `invalid_assumption`, 149 `stale_state`, 28 `wrong_target`, 27 `unavailable`) with the
   routing of rule refusals deliberately left as `continue`, the measured action. See
   KNOWN_GAPS G14; the cause line above is what the *old* code produced and will not
   reproduce after this fix.
   Caveat for re-readers of *this* log file: `kind:"decision"` rows written before the fix
   carry transport `ok`/`error`. The paired `kind:"step"` rows carry the real game error.
6. **Longest session 125 min.** Multi-hour survival is *not* established: that span is a
   session id's first-to-last row, and a multi-hour *productive* unattended run is still
   the thing to be measured after the fixes.

## Re-measure

```bash
cd dofus-network
python -m tools.session_report trajectories/default.jsonl "$LOCALAPPDATA/DofusFarm/autopilot.jsonl"
python tools/recovery_report.py trajectories/default.jsonl "$LOCALAPPDATA/DofusFarm/autopilot.jsonl"
python tools/dialog_labels.py            # census + writes data/dialog_labels.jsonl
python tools/brain_readiness.py          # does a live board satisfy the combat contract?
python tools/group_scan.py               # why was `map.groups` empty on every recorded map?
python tools/brain_readiness.py          # does the page's own fight.state satisfy the contract?
python tools/policy_over_record_boards.py  # what the trained actor would have done with them
```

`tools/recovery_report.py` is new with this baseline: `session_report` gives throughput
and objective deltas, and this gives the verdict/decision/cause/disconnect breakdown that
"recovery success" is actually measured by. Both are read-only over the logs.

`tools/dialog_labels.py` (added with G23) reconstructs `(page, reply sent) -> outcome` from the
trajectory logs and joins each reply id to the `PLAY_LOG` wire stream, so a dispatch can be
labelled by what the server sent back (`QuestObjectiveValidatedMessage`,
`QuestStepValidatedMessage`, `QuestStepInfoMessage`, `MapNpcsQuestStatusUpdateMessage` = credited;
`LeaveDialogMessage` = ended) rather than by an observation that may predate the answer. Run it
again after the next live session: the census is the measurement of whether dialog data now
exists, and it prints its own verdict when it does not.

**Which policy played the fights is now a field, not an assumption (G24).** Read the run's
`kind:"brain"` rows (`state`, `model`, `cmd`) or the summary's `brain` object: `started`/`reused`
means 127.0.0.1:8891 answered `/health` for that session, `no_interpreter` means the machine has
no python that can import the brain, `port_held` means something else owns 8891. Then check
`trajectories/live_policy_decisions.jsonl` for the turns themselves: each row logs
`decision.contract.value_ready` and the contract's warning list, which is what says whether the
actor had the features it was trained on or answered `policy_pass` from an incomplete board. The
baseline has 8 rows and not one of them came from the page.

That readiness question turned out to be answerable without waiting: `tools/brain_readiness.py`
runs the contract over the 156 `fight.state` snapshots the loop already recorded, and **all 156 are
rejected** (`max_hp`, `ap`, `max_ap`, `mp`, `max_mp` null on the self fighter; `initiative` key
absent; enemy max hp missing on 445 slots). So "the brain is up" does not yet mean "the learned
policy decides the turn" -- the page has to fill those fields first (KNOWN_GAPS G25). Re-run the
reader after the next live session: `actionable=True value_ready=True` is the line that says the
blocker moved.

The model itself was then measured against those same real boards (`tools/policy_over_record_boards.py`,
which states its fill hypothesis out loud): with the missing max values supplied, all 64 of its own
turns become actionable and the trained actor answers **18 of 64 with a cast** at p50 3.3 ms --
inside the page's 4 s fetch budget. That is an offline measurement of the policy on real geometry,
not a live fight and not a win rate.

**Quest progress is now measured by instruments that can move (G26/G27/G28).** This baseline prints
`objectives +0, quests completed +0` followed by the starvation line naming the ready-objective head
that held every step. That is the *pre-fix* corpus, and both halves of it were structural: the page
derived progress from a property the client only writes when a quest *finishes* (G28), the claim loop
read one entry of a client-ordered ready list (G26), and the wrappers it did act on were hunted as
monsters they never were (G27). The four lines to read in the next run's
`tools/session_report.py` output and its rows:

- `quests completed +N` with **N > 0** — the monotone count from the client's own
  `getListOfFinishedQuestsId()`, the only quest number in this goal that can only go up.
- `quest_steps` changing inside a session, and `quests_active` holding a **nonzero** value: progress
  now comes from the live `stepId` position in `dbQuest.stepIds`, which the step handlers do update.
- The starvation line **absent** — it prints only when steps carried a ready objective and none was
  completed, which is exactly the G26 defect.
- No `fight.join`/`farm.start` refusal naming a free-form objective's `parameter0` (715354-shaped, six
  digits), and no "no group on this map; ask NPC to start the fight" spent on a
  `QuestObjectiveFreeForm` entry — such an objective now plans nothing, and the scan moves past it.

All four require a run whose page build contains `_questProgress`; a zero from the older corpus is not
a verdict about the character, it is the instrument. None of it has been observed live yet.

**Quest requests are now only sent for quests this game has (G31), and `knowledge.status` can say
"collision" (G30).** Two more lines to read in the next run, and both are *reductions* — the baseline
corpus contains them, the fixed loop must not:

- **No *start-shaped* `quest.advance` row whose `quest_id` never appears in that session's
  client-reported quests.** Start-shaped means the arguments are a quest id and nothing else — no
  `npc_id`, no `objective_id`, no `reply`, no `close` — because a `quest_id` on its own does not
  identify a start (G31's first pass mistook 779 talk/reply/close rows for starts and reported 1,145;
  `tools/quest_advance_shapes.py` is the classifier). The baseline corpora hold **366** such requests
  and **all 366 name a quest this game has never shown** (16 distinct ids; one Dofus 2 quest, 584,
  accounts for 297, and 310 of the 366 were immediate repeats) — **3.21 % of the 11,395 recorded rows**,
  31 of 66 sessions, 28.1 % of its own rows in the average session that sent one and 119 starts in the
  single worst. Since G31 the planner consults a session-scoped set of client-reported quest ids before
  sending, so the count of never-reported starts must be **0** and those steps must appear as
  `goal_unresolved` rows with no directive on the wire. This is the one quest number in the goal that
  is pure waste: it cannot help progress, and each of the 366 was a request the server had to reject.
- **`knowledge.status` reporting `objective_id_collision` instead of a foreign `resolved`.** In the
  baseline corpus 3,567 of 8,777 enriched rows were `resolved` on another game's objective text. The
  next run's rows should show that population labelled `objective_id_collision`, carrying the
  client's quest id plus the store's for diagnosis and no text — and the dialog rows in it should
  name the quest the character is actually doing, which is also why G34's labels must be re-derived
  rather than reused.

Neither line needs a new tool: both are read out of the `quest.advance` rows and the `knowledge` block
the loop already writes.

## What is NOT baselined yet

- **Manual interventions** — this baseline could not measure them, because nothing in
  the logs marked a human action. Instrumented on 2026-10-26 and now counted: the page
  tallies *trusted* input events (`isTrusted: true`) that arrive while the loop is
  running — the agent's own clicks are `dispatchEvent`-synthesised and never count — the
  loop writes an `intervention` row per delta, and both reports print the total. The run
  above correctly shows `0 events in 0 rows`: the instrument did not exist during these
  sessions, so **zero is "not measured" here, not "no human touched it"**. The next
  baseline is the first that can answer criterion (5)'s zero-interventions claim.
- **Recovery success** — criterion (5) asks whether the run gets itself back, and this
  baseline had no number for it: recoveries were only printed lines. Each `_recover_world`
  now writes `{"kind":"recovery", trigger, reason, recovered, waited_s}` and the summary
  carries `recoveries / recoveries_ok / recovery_wait_s`; `recovery_report.py` prints them
  per trigger. This run shows `no rows`, which means *unmeasured*, not *never needed*.
- **Repair labels** — see finding 5: `unknown` covered a third of all failures and the
  decision row hid the game's error. Fixed 2026-10-26, so the next baseline is the first one
  whose cause histogram means anything, and the one above is not comparable to it.
- **Idle waits** — the baseline has no wait rows because a wait used to be a directive:
  **3,010 of its 4,287 steps are `state.snapshot`**, all `source: rule`, 3,005 of them
  `advanced`, and 3,005 of 4,286 decisions are `continue` pointing at another read. They come
  in 671 runs with a p99 length of 6 passes — except two runs of 149 and 1,581 passes, which
  hold 57% of them; the long one is 1,576 *successful* reads in 586 s on a goal that had no
  action left. Summing the gap between consecutive reads, **82% of the loop's recorded
  step-time is time between two looks** (39.0 of 47.3 minutes). Fixed 2026-10-26 (G18): a wait
  now writes `{"kind":"wait", goal, reason, consecutive, in_dialog}` and costs no step row, no
  verdict, no reward and no budget; `summary()` carries `waits`, and `recovery_report.py`
  prints passes, runs, the longest run, the reason histogram and a NOTE when a wait reached the
  40-pass bound. This run shows `no rows`, which means *predates the instrument*. Expect the
  next baseline's `steps` to fall to roughly a third of this for the same wall time — the
  comparable numbers there are `waits`, the productive rate and the recovery rows, not `steps`.
- **Dialog reply labels** — the baseline proves the ranker *ran* (dialog opens were read and
  replies were scored) but nothing recorded whether the reply it chose was right, so there is
  no accuracy number to improve on, and criterion (4) cannot be closed for dialogs. Fixed
  2026-10-26 (G20): each dialog interaction now writes `{"kind":"dialog", name_id, reason,
  page, replies, scores, outcome}` where `outcome` is the objective comparison after the click
  (`advanced` / `same page` / `closed` / `unknown`); `summary()` carries `dialogs` /
  `dialog_stalls` and `recovery_report.py` prints the outcome census, the per-NPC count and how
  often the primary score beat zero. This run shows `no rows -- this log predates the dialog
  instrument`, which means *unmeasured*. G21 adds the dispatch half (`reply_id`, `via`,
  `dispatch_ok`, `objective_type`, `ended_by`) and G22 bounds the re-ask, so the census is now
  readable. **The old corpus's stall is root-caused** (KNOWN_GAPS G3): the page's only reply says
  *"Go and equip your new items."* and the objective is `QuestObjectiveFreeForm`, so no reply could
  have advanced it — expect `ended_by: after_reply` plus `objective_type: 0` rather than a ranking
  story, and expect `advanced` to stay rare until G2 gives the loop an equip verb.
- **Fights won** — `fight.join` failed 48× and the fight-outcome log is the calibration
  source; per G7 no live fight has been proven end to end.
- **Farm cycles per hour** — measured now instead of asserted. Of 9,164 rows that carry a
  `farm_session`, **8,797 show `fights: 0`** (the session object exists and is idle), 164 rows have
  a non-zero `fights`, and the maximum every counter ever reached in the entire corpus is:

  ```
  {"fights": 2, "wins": 0, "losses": 2, "kills": 0, "xp": 0,
   "drop_count": 0, "drops": {}, "kamas": 2, "minutes": 0}
  ```

  So the farm never completed a cycle and never recorded a kill; the only fights it entered, it
  **lost** (max `losses` 2, `wins` 0 in all 9,164 rows). Across the corpus the character's kamas go
  418 → 0 (peak 1,773), i.e. **net -418** — what a run that pays repair bills and never farms looks
  like. The number to beat next session is not a rate; it is a non-zero `kills`, then a `wins: 1`.
  `farm.start` succeeded 78 of 78 times with identical args
  (`{monster: 698229, kill_count: 100, auto_sell: false}`), so the verb is accepted and repeats of
  it are the same accepted-but-inert shape as G17. This sits behind **G7 (no live fight has been
  proven end to end)**, not behind the farm tool — and G7 is now *negatively* observed rather than
  merely unmeasured.
