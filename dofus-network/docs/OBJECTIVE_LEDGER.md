# The objective ledger: confirmation at the level of the objective

The loop has three layers that answer "did something happen", and none that answers "was I right".

| layer | question it answers | where |
|---|---|---|
| verifier | did any `STATE_KEYS` value change? (`ready_objectives`, `objectives_completed`, `quest_steps` are in that list) | `agent/verifier.py` |
| controller | was that change progress, and which repair should follow? | `agent/controller.py` |
| planner | has this plan returned `None` too many times? | `agent/planner.py` (`_plan_none_reaches_limit`) |

So "did the world change" is covered, and "did this objective move" is *observable* -- but nothing
recorded **which hypothesis was tried against which objective, and what the attempt proved**. The
per-call counters (`_ui_attempts`, `_equip_tries`) forget across objectives, and the give-up trigger
was a wait count. `KNOWN_GAPS.md` G98 is what that costs: 306 waits on one tutorial objective while
the bag held two items and nothing was equipped.

## What the ledger adds

    agent/objective_ledger.py

One `ObjectiveRecord` per `(quest, objective)`, persisted per instance
(`%LOCALAPPDATA%/DofusFarm/objectives.<instance>.json`):

    status        open | ready | closed | blocked
    text          the client's own objective text (French, as published)
    hypotheses[]  {tool, args, why, tries, verdicts}  -- ordered, generated from the world
    block_reason  why every hypothesis was refuted
    reopen_on     map / level / bag size at the block, so it can re-open

Four operations, in the order the loop uses them:

1. **sync(obs)** -- which objective is actionable. The *ready* objective wins over
   `current_objective`, because the tracker's pointer can sit on a different objective (measured:
   pointer on 13541 "find the map" while the blocker was 13507 "open the bag").
2. **confirm(before, after)** -- the confirmation layer proper. Three ways an objective can move:
   it leaves `ready_objectives`, `objectives_completed` rises, or the current objective changes. A
   world change that leaves all three alone is **not** progress on this objective, however much
   else moved.
3. **record(rec, hyp, before, after, verdict)** -- credit or refute. `noop` gets the full retry
   budget (`MAX_TRIES = 3`); `failed` refutes immediately, because a refusal is the page answering
   "no" and two more tries only spend the step budget.
4. **next_hypothesis(rec, obs)** -- the next untried thing, generated evidence-first: claim the
   objective the game already accepts, then the bag (an item to wear), then the menu the text
   names, then the NPCs standing here, then travel. When everything is refuted the objective is
   `blocked` **with its evidence** -- and re-opened when the map, level or bag changes, instead of
   being retired for the run.

## Measured on the session that stalled

Replaying the 40 observations of session `fd24a682` through the ledger, per objective:

| objective | what the ledger would try |
|---|---|
| 13507 "Ouvrez votre inventaire depuis le menu" | claim x3 -> `ui.tap Bag` x3 -> **`inv.equip {object_uid: 144784272}`** at attempt 7 |
| 13549 "Defeat 1 Aggressive Merkaptan" | claim x3 -> (no fight hypothesis yet) -> blocked with evidence |
| 13495/13541 "Find the map" | claim x2-3 -> blocked with evidence |

The loop spent **306 waits** on 13507 and never equipped. Seven attempts is not a great policy, but it
is a policy that reaches the item.

## What is wired, and what is not

Wired: `agent/autopilot.py` constructs the ledger per instance and consults it in
`_next_real_action` -- the function the wait escalation already calls when the policy has nothing to
say. It can only *add* a candidate; a ledger fault is caught and printed. The previous hypothesis is
settled against the next observation there, so a call that changed the world without moving the
objective is refuted rather than repeated.

Not wired, and the reason the ledger still guesses: **the quest model**. `kb.quest(3841)` is `None`
-- the tutorial quests are absent from the knowledge base, so nothing can say "this step needs an
equip, that one needs a map". The ledger derives hypotheses from the objective's *text* and the
observation, which is why "Find the map" ends blocked: it has no map id to travel to. The fix is a
step model (objective -> precondition -> tool), which is a knowledge-build job, not a planner one.

Tests: `tools/test_objective_ledger.py` (6 checks: order, refutation by noop and by refusal,
confirmation about the objective rather than the world, blocked-reopens, survives a restart).
## Live, 2026-11-06 23:41-00:10 (dofus-04, planner policy)

The ledger is wired and running. What a real session produced:

    [autopilot] ledger: quest.advance {'quest_id': 3841, 'objective_id': 13507} instead of waiting
    [autopilot]   3 quest.advance  FAIL failed  -> objective 13507 is completed by the server
    [autopilot] ledger: ui.tap {'menu': 'Bag', 'cycle': True} instead of waiting
    [autopilot]   7 inv.equip      FAIL failed  -> equipItem sent nothing; pass slot

and `%LOCALAPPDATA%/DofusFarm/objectives.dofus-04.json` now exists with the record and its refuted
hypotheses. Two changes were needed to get there, both found by running it:

1. **The first wait consults the ledger.** It was only reachable from the 40-pass escalation, so a
   stalled step still sat still first. Now `_consecutive_waits == 1` asks the ledger for the next
   untried hypothesis and forces that call. That is why the log reads `wait x0` followed by a real
   action.
2. **A hypothesis is credited only by a step that ran it.** First attempt compared tool names, and
   the planner's `quest.advance` (asking an NPC) was credited to the ledger's `quest.advance`
   (claiming an objective), scoring a false `advanced`. Now the tool and the hypothesis's own
   arguments must match. The guard is conservative on purpose: a refusal whose step args were
   normalised by the executor reads as `noop`, which costs up to three tries instead of one -- a
   small price for never learning a success that did not happen.

The equip itself then failed for a reason the client stated exactly, recorded as `KNOWN_GAPS.md` G99:
the tutorial items have no declared position and nothing of their type is worn, so `inv.equip` needs
a `slot` the caller cannot know yet.
