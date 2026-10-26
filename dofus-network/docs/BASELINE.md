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
```

`tools/recovery_report.py` is new with this baseline: `session_report` gives throughput
and objective deltas, and this gives the verdict/decision/cause/disconnect breakdown that
"recovery success" is actually measured by. Both are read-only over the logs.

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
- **Fights won** — `fight.join` failed 48× and the fight-outcome log is the calibration
  source; per G7 no live fight has been proven end to end.
- **Farm cycles per hour** — `farm.start` failed only 3×, but no session shows a
  completed cycle count, so there is no rate to improve on yet.
