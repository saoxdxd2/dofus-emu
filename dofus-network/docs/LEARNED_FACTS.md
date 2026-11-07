# Facts vs code: what belongs in a constant and what must be learned

Written 2026-11-07 after a session in which almost every fix was "add a rule". The rulebook grew to
eight facts in Python and four in the page, every one of them a place the agent could be wrong in a
new way, and none of them something the client had refused to tell us.

The distinction that matters is not "model vs rules". It is **could the client have answered this?**

## Three kinds of constant, and only one of them is debt

| kind | example | verdict |
|---|---|---|
| **protocol marker** | `position: 63` means not equipped; `-1` means remove; `ObjectSetPositionMessage` | keep. The page exposes numbers and message names; there is nothing to discover |
| **search budget** | `DIALOG_STALL_LIMIT = 4`, `QUEST_DATA_WAIT_LIMIT = 3`, `MAX_CONSECUTIVE_WAITS = 40` | keep. These are how long to try, not claims about the game |
| **game fact we asserted** | `_UI_WINDOWS = {"Bag": "equipment"}`, `"inventaire"` → Bag, `type_id == 0` never claimable | **debt.** The client publishes all three |

The test is simple: *if the page changed the name tomorrow, would the constant be wrong?* A protocol
marker is stable because it is the interface. A game fact is a guess about one build.

## The pattern: observe → store → seed

A fact that can be observed should be observed once, persisted, and adopted on the next start. The
project already has both halves: `agent/knowledge_store.py` (`remember` / `find`, append-only, keyed
by `(kind, key)`) and the objective ledger.

Worked example, 2026-11-07 — `menu_window`:

1. **Observe.** `ui.tap` already returns `open_before` and `open_windows`. The pair is one diff.
   `Autopilot._learn_menu_window` refuses to learn unless exactly one window appeared, because a
   press that opened two did not name the pair.
2. **Store.** `store.remember("menu_window", menu, window, source="observed")`.
3. **Seed.** `Autopilot._seed_menu_windows()` at the start of `run()` calls
   `policy.load_menu_windows(...)`, so the first press of the next run is already informed.

`QuestPlanner._UI_WINDOWS_SEED` keeps the one pair that was measured, so a fresh machine is not
blind. It is a starting point, not the answer: `menu_window_for()` reads the instance table.

Tests: `tools/test_menu_window_learning.py` (8 checks, driven through the real `Autopilot` and the
real `KnowledgeStore`), delegated into the gate as `test_menu_window_learning_suite`.

## Second conversion: a refusal becomes a fact (`server_counted_types`)

`_generate` used to skip the claim when `type_id == 0`, which is a claim about a type we may not
have met. The page already says which types it completes: *"objective 13531
(QuestObjectiveFreeForm) is completed by the server; perform the action instead of claiming it"*.
That sentence names the type, so it is evidence about **every** objective of that type.

- `ObjectiveLedger.server_counted_types` starts **empty**; the first refused claim adds one.
- The trigger is `SERVER_COUNTED_HINT = "completed by the server"` — the page's words, not ours.
- It is persisted in the ledger file, so a later session does not re-learn it.
- `record(..., reason=...)` carries the refusal; `Autopilot` passes `Step.error`, the field the
  refusal actually lands in.

Cost of being honest: the first claim of each type is now spent rather than skipped, once, ever.
Tests: `tools/test_objective_ledger.py` — offered until refused, generalises to an unseen objective
of the same type, an unrelated refusal teaches nothing, survives a reload, another type is still
claimed.

## Third conversion: the client's own surface names (`surface_label`)

The French→key table (`"inventaire"` → `{"menu": "Bag"}`) had the same defect one level
down. `state.snapshot.menu_icons` now publishes `[{key, label}]` from the page's own menu
bar, so the label is observed data rather than a translation someone typed.

1. **Observe.** Every `state.snapshot` carries `menu_icons`; `ui.icons` now returns the same
   list (one scanner, two readers).
2. **Store.** `Autopilot._learn_surface_labels` records `surface_label/<label>` →
   menu key for every icon that has both; a label shorter than three characters is refused
   because it would match prose by accident.
3. **Seed.** `Autopilot._seed_surface_labels()` calls `QuestPlanner.load_surface_labels`
   before the first plan. `_ui_surface` checks the learned labels first, then the English
   seed (`_UI_SURFACES_SEED`) that keeps a fresh machine working.

Tests: `tools/test_menu_window_learning.py::SurfaceLabelLearning` (7 checks) drives the
real `Autopilot` and `KnowledgeStore`; the gate asserts the French literal is gone and that
producer and reader both exist.

## What is still hardcoded, in leverage order

1. **`possible_positions == []` → one attempt.** Reclassified: the empty list is the
   client's own answer, so reading it is not an asserted game fact. The one-attempt bound
   is a **search budget** (how long to try), which the table at the top says to keep. The
   rank-never-exclude rule still allows a build that publishes an empty list for something
   wearable to succeed on a later pass.

## What is not the answer

Putting a model in the decision loop. Every failure that produced these constants was mechanical: a
disabled button, a stale `object_uid`, a marker position, a dialog page that never changed. No model
would have found them; looking at the client did, in one probe each. The lesson is **ask the client**,
not *use a bigger model*.

The corollary is the honest cost: a constant is verifiable in a 30-second test and a learned fact is
not. That asymmetry is exactly how a rulebook happens, so the check on any new constant is the table
above, not "does the test pass".
