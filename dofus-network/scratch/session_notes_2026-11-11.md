
## Live testing pass: five defects found on the running server, four fixed

The operator's instruction was "live testing and fixing everything we found as not working
or holding the intelligence down". Nothing was running when this pass started, so the pass
began by bringing the stack up: `bin/warp/wireproxy.exe -c wireproxy.runtime.conf` (exit
`104.28.220.131` through the tunnel, verified by `curl --socks5-hostname`), then
`tools/host_control.py --start-host` and `--launch-all`, which brought 4 instances up and
3 bridge clients in-world. `tools/live_ready.py` had blocked on `egress` and on a stale
`page build`; both clear once the tunnel and a fresh page are up.

The instrument that made the pass possible is `scratch/live_census.py` (new): `tools/
session_report.py` reads `trajectories/default.jsonl` and `autopilot.jsonl`, but the brain
daemon writes `autopilot.<instance>.jsonl`, so the live run was invisible to the report.
The census reads one of those and prints verdicts by tool, refusals grouped with their
volatile numbers masked, the map sequence, and the longest identical repeats.

### 1. The inn loop: 281 of 294 steps were no-ops, and the cause was one branch

Measured on dofus-01, session `db682fc5`: 294 steps, 281 `noop`, 285 `nav.goto`, and the
map never changed. Every one was `nav.goto {"direction": "left"}` on map 146276352, cell
322, returning `ok: true`. The controller read it as `recover/incomplete` 185 times and
`replan/plan_wrong` 93 times and never escaped.

The map is the Belladonna inn: four neighbours, `exits: []` (it publishes no crossable
border cell), and two doors, cell 346 (`via: arrival`) and cell 451 (`via: walk`), both to
145490435. `nav.goto` by direction, finding no exit cell, called
`isoEngine._requestMapChange(...)` and returned ok -- the server refuses a map change from
an arbitrary cell and says nothing, so the call "succeeded" forever.

Fixed in `scripts/mobile-disguise.js`: when a direction names no published exit, walk onto
a door first (`_agentDoorFor(here, neighbour)`, then any door, preferring `via: walk` over
`via: arrival` -- an arrival cell is where a transition dropped the player and can be
unreachable: 346 stopped the walk at 359 while 451 crossed), and only ask the server
directly when the map has no door at all. The duplicated step-off-then-on door logic was
folded into one `_agentWalkOntoDoor` helper so the two call sites cannot drift.

Verified live, twice: `nav.goto {cell: 451}` from cell 330 crossed to 145490435; then from
inside the inn `nav.goto {"direction": "left"}` returned `cell: 451` and the character
crossed to 145490435 at cell 451. After the fix the same instance ran
`146276352 -> 145490435 -> 145489923` and 18 of its first 40 steps advanced.

### 2. `already_failed` named the tool and not the edge

`ActionMemory.describe` rendered arguments from a fixed list that did not include
`direction`, so four different dead edges came out as
`already failed here: nav.goto x5; nav.goto x3; nav.goto x2; nav.goto` -- the policy was
told the tool was dead but not which edge, and kept choosing one. `direction` and a
short-argument digest are now rendered. Three checks added to `test_durable_memory`.

### 3. `quest.list` was served an area argument that was its own prompt

`quest.list {"area": "urrent state: where: map 146538496 cell 176"}` -- the learned policy
echoing the `Current state:` prompt line into a name slot. 168 rows in the 2026-11-08
trajectory, 20 more in the live run, each one served as an empty list, which reads as a
successful read and so repeats. Now refused with an error naming the problem, so the memory
can record it: `agent/executor.py::_is_prompt_echo`, pinned by
`test_quest_area_rejects_the_prompt_echo` (9 checks).

### 4. `104 retired` was a number with no reason, and 88 of it was bookkeeping

`last_no_call` said "nothing plannable -- 3 ready objective(s), 3 active quest(s), 104
retired" and nothing anywhere said which. Retirement reasons are now recorded per quest and
rendered as a grouped line, which immediately answered it:

    104 retired (the client has never shown this quest x88,
                 not in the quest knowledge base x13, no progress x2, ...)

88 of them were the curriculum walking the downloaded catalogue and retiring every entry the
client has never offered. That is not a verdict about a quest, and it was consuming the
retirement budget that exists for real dead ends. They are now counted separately in
`_unseen` and never added to `_skipped`. Live result after the change, same instance:

    16 retired (not in the quest knowledge base x13, no progress x3),
    88 in the catalogue the client never offered

### 5. The planner's quest table cannot contain the quest it is standing in

The downloaded cache and the live game are different id spaces: the cache stops at 2,546
and the live ids start at 3,841. `QuestPlanner.quests` is the cache -- 1,976 quests keyed
18, 26, 29, 30, 32 -- and none of 4278, 4280, 4282, 4276, 3841 or 4262 is in it. So the
three quests the character actually held were unplannable, and `_plannable` retired them.

The page already captures the live graph: `state.snapshot.quests_catalog` carried all three
active quests with full steps and objectives. `_absorb_catalog` now merges it into the
table at the top of every decision, drops a retirement whose only reason was "not in the
quest knowledge base" (the evidence for it is now false), and leaves every other reason
alone. Verified live: the agent went from "nothing plannable" to
`quest.advance {"npc_id": 3912, "quest_id": 4278}` and kept working 4278.

### What is still not working, with the number that says so

- **`nav.goto {npc_id}` cannot reach an NPC whose neighbours are all blocked.**
  `_movePlayerOnMap noPath:242:269`, 5 times in 40 steps, on map 146277376 (an interior).
  Walking to 243/241/258/289 from the same cell all succeed, so the cell is reachable and
  the NPC pocket is not. The client needs a neighbour-by-neighbour fallback in the
  `stopAdjacent` branch, or the planner needs to mark that NPC unreachable for the visit
  and take another objective. Not fixed: the second is a planner change and the first needs
  the client own neighbour geometry, which the page does not currently expose.
- **`policy returned no call`, 8 of 40 steps**, always "nothing plannable" while 3 ready
  objectives exist. Now readable, because the retirement line rides the same row.
- **13 quests are "not in the quest knowledge base"** even after the absorption, because the
  page captures a graph only for the quests it has active (G29 coverage). The client has
  shown them at some point but their steps were never captured.
- **The fight bootstrap still refuses at 43% expected HP loss against a 30% limit.** That is
  the no-calibrator envelope doing its job; the fix is a calibrator, and 22 fight-outcome
  rows are not enough to fit one honestly.

### Notes for the next session

- `Reload client` in the AI panel (`window.location.reload()`) does re-inject an edited
  `scripts/mobile-disguise.js`; a Stop All / Launch All is not needed for a page-agent
  change, only for a C# change.
- The brain daemon reloads Python code on its own only when no instance is running, and
  `code_stale: []` plus a changed `brain_build` hash is how to confirm it did.
- A `nav.goto` by direction now returns `unverified: true` on the door and
  `_requestMapChange` paths. That is honest, not a defect: the client cannot see the map
  change synchronously, and the next snapshot is what decides.
