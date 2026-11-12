
### Update 2026-11-12, second measurement: the ratchet matched text, not execution

G114's fix added the missing suite to `SNIPPET`. The same hole was then found one file over, and
the guard that was supposed to prevent it had been passing the whole time.

Two anchors were written for `LiveSpell.castable_error` -- *the reason a spell could not be checked
is dropped* (the parser) and *the unchecked spell is not named in the plan* (`plan_turn`'s notes).
Both reported `MISSED ... 0 failing check(s)` against a suite that passes. Cause: `SNIPPET` ran
`test_live_fight_corpus_is_read_in_process`, whose body names `combat/live_data.py`, so `_castable`
was exercised there and its two older anchors failed for the right reason -- but nothing in the
snippet imported `combat.test_battle_solver`, so the parser and the notes were invisible.

`test_mutation_targets_have_witnesses` did not catch it, and could not: it matches the mutated
**file name** against the *text* of each snippet test's body, and a comment that says
`battle_solver.py` satisfies it. The ratchet checks that a name is mentioned, not that the file is
executed -- so a suite can be absent and the ratchet stays green.

**Fixed** by the same shape as the live-data runner: `test_the_battle_solver_reads_what_the_page_writes`
runs `test_battle_solver.PayloadParsingTests` in-process, one check per class, plus a check that the
class still carries the unchecked-spell rule so a rename is red instead of silently empty. It is in
`SNIPPET`. Both anchors are `CAUGHT` now (`scratch/mut_castable1.log`, `scratch/mut_castable2.log`).

**Not fixed, named:** the witness ratchet still matches text. Making it match execution would mean
mapping each anchor's target file to the test that imports it, which the sweep could derive from a
`--witnesses` mode of its own runner -- worth doing before the next batch of anchors, not instead of
the runner that actually executes them.
