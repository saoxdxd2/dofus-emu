
### Update 2026-11-12, third measurement: the gate never called three of its own suites

`run()` is an explicit list of `_run_test(...)` calls, and nothing checked that every `test_*`
function in the module was on it. Measured after the second update: **three suites were defined,
passed when named on the command line, and were never executed by the gate** --
`test_the_page_s_word_for_a_wall_is_read` (24 checks, G112's own suite),
`test_the_unread_field_audit_can_find_an_unread_field` (4) and
`test_the_battle_solver_reads_what_the_page_writes` (2). The gate reported 2,316 checks green while
30 checks it was supposed to run never ran.

`test_no_suite_stops_answering` could not see them, and this is the interesting part: it compares
this run's per-suite counts against the previous run's, and a suite that has never run has no count
in either run. The silence audit is about suites that *stopped* answering; this was suites that were
never asked. The mutation sweep had run the wall suite (it was added to `SNIPPET` for G114), so the
same suite was executed 715 times by the sweep and zero times by the gate.

**Fixed** by adding all three to `run()`, and by a check inside `test_no_suite_stops_answering` that
every `test_*` function defined in `agent/regression.py` appears in `run()`'s source. It reads the
runner's own source because `run()` is defined after the tests and cannot call itself. The gate now
runs **232** suites, not 229, and the check fails on a suite that is written and forgotten -- the
class of omission that produced all three of these.
