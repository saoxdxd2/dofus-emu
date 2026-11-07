"""Compile every shipped .py file with the interpreter that actually ships.

The tree is edited and tested under a newer Python than `runtimes/python` (3.11.9) installs:
measured 2026-11-03, `ai/diag_call_budget.py` is a SyntaxError under 3.11 and parses fine under the
development 3.14 -- PEP 701 lets 3.12+ reuse a quote type inside an f-string expression, 3.11
cannot. A file like that is not merely unused: the installed copy raises the moment anything
imports it, and every gate run from the repo is blind to it.
"""
import json
import sys
from pathlib import Path

NETWORK = Path(__file__).resolve().parents[1] / "dofus-network"
SHIPPED = ["agent", "ai", "combat", "networkhealth", "planning", "advisor", "navigation",
           "tools", "scripts", "run_brain.py", "run_autopilot.py"]


def main() -> int:
    bad = []
    count = 0
    for entry in SHIPPED:
        root = NETWORK / entry
        files = [root] if root.is_file() else sorted(root.rglob("*.py")) if root.is_dir() else []
        for path in files:
            if "__pycache__" in path.parts or path.name.startswith("test_"):
                continue
            count += 1
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as exc:
                bad.append([str(path.relative_to(NETWORK)), exc.lineno, (exc.msg or "")[:90]])
            except OSError as exc:
                bad.append([str(path.relative_to(NETWORK)), None, "read: %s" % exc])
    print("compiled %d files with Python %s (%s)" % (count, sys.version.split()[0], sys.executable))
    for name, line, msg in bad:
        print("  SYNTAX FAIL %-46s line %s: %s" % (name, line, msg))
    if bad:
        print("ship-compat compile sweep FAILED: %d file(s) the installed interpreter cannot parse"
              % len(bad))
        return 1
    print("ship-compat compile sweep PASS")
    return 0


# The chain the trained fight policy needs. These were the modules an installed copy could not
# load at all: `combat/encounter/env.py` imported jax at module scope, and everything above it
# imports env, so `/v1/combat/decide` had no policy to ask (ARCH_REASSESSMENT.md §L.24).
COMBAT_CHAIN = ("combat.encounter.backend", "combat.encounter.env", "combat.encounter.model",
                "combat.plan_value", "combat.live_policy", "combat.brain_service")


def decide_one() -> int:
    """Ask the trained policy for one real decision, with the interpreter that ships.

    Importing is not answering: the numpy path through `initial_state`, `action_masks` and the
    indexed updates had never run here, so `--imports` alone would certify a module that throws on
    the first board. One duel board is enough -- it is built from the shipped map and dataset, and a
    refusal to name a spell is the failure this is looking for.
    """
    import pathlib
    import random
    import sys
    if str(NETWORK) not in sys.path:
        sys.path.insert(0, str(NETWORK))
    from combat import duel as du
    from combat.live_policy import LivePolicy
    import numpy as np

    state = du.make_duel(np.load(str(du.DEFAULT_DATA)), 0, 1, du.load_map(du.DEFAULT_MAP),
                         rng=random.Random(1))
    if state is None:
        print("  FAIL no duel could be built from the shipped map and dataset")
        return 1
    policy = LivePolicy(checkpoint=pathlib.Path(NETWORK) / "models"
                        / "encounter_selfplay.pkl", beam_width=8, max_actions=2)
    decision = policy.decide(du.board_for(state, state.actor).to_fight_state())
    action = decision.get("action") or {}
    reason = str((decision.get("policy") or {}).get("reason")
                 or decision.get("reason"))
    if action.get("type") not in ("cast", "move", "pass"):
        print("  FAIL decide() answered type=%r reason=%s" % (action.get("type"), reason))
        return 1
    print("  decide() on this interpreter: %s (%s)" % (action.get("type"), reason))
    return 0


def imports(argv) -> int:
    """Import the shipped modules with the interpreter that ships, and name what cannot load.

    Compiling proves the syntax; only importing proves the module loads. The parse sweep ran green
    while `import jax` sat in a file the installed copy had to import, so this is the arm that
    cannot be satisfied by a file that merely parses. Extra names may be passed to prove the census
    can fail: `--imports jax` is expected to exit 1 here, because the runtime that ships has no jax
    and that is the point of the numpy backend.
    """
    import importlib
    if str(NETWORK) not in sys.path:
        sys.path.insert(0, str(NETWORK))
    names = COMBAT_CHAIN + tuple(a for a in argv if not a.startswith("-"))
    bad = []
    for name in names:
        try:
            importlib.import_module(name)
            print("  OK   %s" % name)
        except Exception as exc:                          # noqa: BLE001 - a census reports, it cannot fix
            bad.append((name, type(exc).__name__, str(exc)[:90]))
            print("  FAIL %-34s %s: %s" % bad[-1])
    print("imported %d module(s) with Python %s (%s)"
          % (len(names), sys.version.split()[0], sys.executable))
    if bad:
        print("ship-compat import census FAILED: %d module(s) the installed interpreter cannot load"
              % len(bad))
        return 1
    print("ship-compat import census PASS")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--imports" in argv:
        rest = [a for a in argv if a not in ("--imports", "--decide")]
        code = imports(rest)
        if "--decide" in argv:
            code = decide_one() or code
        raise SystemExit(code)
    raise SystemExit(main())
