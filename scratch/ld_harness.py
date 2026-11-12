"""The harness's own rules plus the new live-data witness, before the sweep."""
import os
import sys

sys.path.insert(0, os.path.abspath("."))
os.environ["GATE_IN_MUTATION_SNIPPET"] = "1"
import agent.regression as r

for t in (r.test_live_fight_corpus_is_read_in_process,
          r.test_every_mutation_anchor_still_points_at_code,
          r.test_the_snippet_can_see_its_own_mutation_targets):
    r._run_test(t)
print("CHECKS=%d FAILURES=%d" % (r._CHECKS, len(r._FAILURES)))
for f in r._FAILURES:
    print("  -", f[:300])