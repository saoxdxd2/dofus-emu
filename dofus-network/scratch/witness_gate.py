"""Run the sweep's snippet under the witness instrument, then the coverage ratchet.

The ratchet asks what the 123 tests in the sweep's own list executed and opened, so it
cannot be checked by `one_test.py` -- a single test has no map behind it. This runs the
snippet the way the gate does (instrument installed, `_run_test` around each test) and
then the two meta suites, which is the whole of what the ratchet needs.

    python scratch/witness_gate.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.regression as R  # noqa: E402
from agent import witness  # noqa: E402


def main():
    _, snippet = R._mutation_sweep_lists()
    witness.install()
    started = time.perf_counter()
    for name in snippet:
        fn = getattr(R, name, None)
        if fn is None:
            print("missing test in the snippet: %s" % name)
            continue
        R._run_test(fn)
    print("snippet: %d tests, %.1fs" % (len(snippet), time.perf_counter() - started))
    R._run_test(R.test_the_witness_instrument_sees_what_a_test_touches)
    R._run_test(R.test_mutation_targets_have_witnesses)
    targets, _ = R._mutation_sweep_lists()
    import json
    rows = {t: witness.witnesses_of(t) for t in sorted(targets)}
    Path("scratch/witness_map.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print("checks %d, failed %d" % (R._CHECKS, len(R._FAILURES)))
    for failure in R._FAILURES:
        print("  -", failure)
    return 1 if R._FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
