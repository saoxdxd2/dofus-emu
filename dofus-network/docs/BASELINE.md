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
decision causes : none 3063, unknown 455, plan_wrong 381, incomplete 361, unavailable 26
disconnects seen: INACTIVITY 5106, (in_world:false with no reason) 142
failed by tool  : quest.advance 504, fight.join 48, nav.goto 25, state.snapshot 15, farm.start 3
```

## What the numbers say

1. **Zero quest objectives completed.** `objectives +0` in every single session, across
   both views and ~4,300 steps. This is the goal's headline failure and it is measured,
   not inferred — it is the live confirmation of G3: the run navigates, talks, fails and
   repairs, but never completes an objective.
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
- **Fights won** — `fight.join` failed 48× and the fight-outcome log is the calibration
  source; per G7 no live fight has been proven end to end.
- **Farm cycles per hour** — `farm.start` failed only 3×, but no session shows a
  completed cycle count, so there is no rate to improve on yet.
