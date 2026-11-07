# Wiring audit (2026-11-06): what the running loop actually uses

The suspicion was that the pieces are not connected. This is the check, done by construction sites
and by what the live logs recorded, not by what the docs claim.

## The path that runs

`desktop/aipanel.cs` spawns, per instance:

    python agent/autopilot.py --goals-file ... --steps N --interval S --policy <P>
        [--minutes] [--dry-run] [--no-fight-boards] [--verifier-model ...]
        --stop-file ... --log ... [--instance <name>]

Everything else is a default. That list is the whole of what a real run can enable.

## What is constructed and consumed

| layer | constructed | consumed by | in a real run | evidence |
|---|---|---|---|---|
| planner (rules) | `Autopilot.__init__` | every step | **yes** | 24 `quest.advance`, 24 `nav.goto` in `autopilot.dofus-04.jsonl` |
| verifier | `Autopilot._verdict` | verdicts + labels | **yes, rules** | `label_sources: {"rule": 3}`; the model head is gated off by design (G6) |
| controller | `Autopilot.__init__` | repair decisions | **yes** | `controller.strategies` in every summary |
| plan_stack | `QuestPlanner` | macro objectives | **yes** | child goals in the plan path |
| knowledge store | `ToolExecutor.__init__` | `knowledge.*` | **yes** | all 6 local tools called directly: `quest.list` ok, `knowledge.search` ok, `knowledge.forget` ok, `remember` schema-checked, `fight.*` refuse without a client |
| combat brain (:8891) | the host | `fight.estimate`/`join` | **partial** | `brain: {"state": "reused", "model": "encounter_ppo_pbt_actor"}` |
| objective ledger | `Autopilot` (2026-11-06) | wait escalation | **not yet** | wired + 6 tests; no live client (slot banned) |
| experience store | `Autopilot` (2026-11-06) | repair credit | **not yet** | wired + 4 tests; `ExperienceStore` was referenced **0** times in `autopilot.py` before today |
| economy / schedule | `serve.py --schedule` **only** | — | **no** | the loop has no `--schedule` flag; the GUI never launches `serve.py` |
| exploration | `--explore`, default **0.0** | — | **no** | `aipanel.cs` never passes it |
| command shadow | `--command-shadow`, default **off** | — | **no** | log `command_shadow: {"mode": "off", "available": false}` |
| ensemble | `--ensemble`, default `act` | planner only | **n/a** | needs `--policy planner`; the GUI's default policy is `rule` |

## What that means

1. **The loop that runs is rules-only.** Planner, verifier-by-rule, controller. Everything learned is
   optional, gated, or off -- which is a deliberate design (`PLANNER_DESIGN.md`: rules first), but it
   means "the AI" in a normal session is a symbolic planner with a repair table, not a model.
2. **Two layers had no caller at all**, and both are now wired: the conjugate store (zero references
   in the loop; reachable only from `serve.py --schedule`, which nothing launches) and the objective
   ledger (new).
3. **Exploration is unreachable from the GUI.** `--explore` defaults to 0.0 and `aipanel.cs` never
   passes it, so the "learnable failure" corpus the P1 plan needs cannot accumulate from a real run.
   That is a one-line change in `aipanel.cs` plus a rebuild, which is why it is reported rather than
   done at the end of a session.
4. **The economy path is a different entry point.** `serve.py` can wire `ScheduleManager` +
   `CostModel` + `Economy`, but the GUI launches `autopilot.py`, which has no such flag. The
   conjugate store is now fed from the loop instead, which is the part that mattered.

## Fixed in this pass

* `_note_experience` credits one Beta per `(cause, strategy)` at the same point the controller is
  credited, so the store and the in-memory table cannot disagree. `save()` on exit, because the log
  buffers 25 events and a short session would otherwise end with nothing written.
* `ObjectiveLedger` consulted from `_next_real_action`, the function the wait escalation already
  calls. It can only add a candidate; faults are caught.
* (earlier today) the bridge port is re-resolved when the host moves, the tutorial dialog reply
  carries `npc_id`, and the bag step equips.

## The next wiring, in order

1. **`--explore` from the app** (C# + rebuild): without it no live session produces the labels the
   repair model needs.
2. **Run one session and check the posteriors**: `%LOCALAPPDATA%/DofusFarm/posteriors.json` should
   now exist with `repair:*` keys. If it does not, the credit path is not being reached and the
   label source is the thing to look at.
3. **The quest model** (`kb.quest(3841)` is `None`): the ledger can only guess until the KB can say
   what a step needs.
