"""Run one regression test by name, without the 10-minute gate around it.

`python -m agent.regression` has no selective mode on purpose -- a gate that can be
asked to run less is a gate that gets asked to run less. This is the other thing: a
scratch instrument for iterating on a single test, so editing a check does not cost a
full sweep. It reports the same counters, so the check count it prints can be compared
against the per-test count the gate records in `data/last_gate_run.json`.

    python scratch/one_test.py test_durable_memory
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.regression as R  # noqa: E402


def main(argv):
    if not argv:
        print(__doc__)
        names = sorted(n for n in dir(R) if n.startswith("test_"))
        print("%d tests available, e.g. %s" % (len(names), ", ".join(names[:6])))
        return 2
    rc = 0
    for name in argv:
        fn = getattr(R, name, None)
        if not callable(fn):
            print("no such test: %s" % name)
            return 2
        R._CHECKS = 0
        del R._FAILURES[:]
        fn()
        print("\n%s: %d checks, %d failed" % (name, R._CHECKS, len(R._FAILURES)))
        if R._FAILURES:
            rc = 1
            print("FAILURES:\n  " + "\n  ".join(R._FAILURES))
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
