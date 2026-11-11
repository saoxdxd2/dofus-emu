"""Which rankers in `LivePolicy._solver_turn` are actually reached, and which are dead.

Written 2026-11-11 because three documents disagreed about *why* `_value_choice`
never fires: `resume.md` said the `_cast_cell` scan is a superset of the beam's
origins, a later pass over the same file blamed the `_value_ready` gate, and
`ARCH_REASSESSMENT.md` §L.22 had already run four arms and ruled the gate out.
The counters exist to settle this kind of argument, so this drives a real board
through the real `decide()` and prints every counter.

`test_live_policy.board_payload()` is the fixture -- the same one the suite uses,
so a change here and a change there cannot silently disagree.

    python scratch/chain_reachability.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from combat.live_policy import LivePolicy  # noqa: E402
from combat.test_live_policy import board_payload  # noqa: E402

# A board with one enemy 1 cell away: the solver has several plans, so every
# ranker in the chain has something to choose between. A board with a single
# legal plan would make `_hp_choice` return None for the uninteresting reason
# (`len(plans) < 2`) and read as "unreachable".
BOARDS = ("adjacent", "distant")


def payload(kind: str) -> dict:
    data = board_payload()
    if kind == "distant":
        data["my_cell"] = board_payload()["my_cell"]
        data["fighters"][1]["cell"] = data["my_cell"] + 40  # several cells away
    return data


def main() -> int:
    for kind in BOARDS:
        policy = LivePolicy(beam_width=8, max_actions=4)
        data = payload(kind)
        plans = None
        try:
            from combat.battle_solver import Board

            board = Board.from_fight_state(data)
            plans = policy.solver.plan_candidates(board, max(2, int(policy.plan_k)))
        except Exception as exc:  # noqa: BLE001 - the count is the point, not the plan
            print(f"{kind}: plan_candidates raised {type(exc).__name__}: {exc}")

        out = policy.decide(data)
        # `_solver_turn` drops pass-only plans before handing the list to the
        # rankers, so `plan_candidates` is not the number the gates see. Without
        # this line a board with one non-pass plan reads as "hp ranker
        # unreachable" when the honest reading is "nothing to rank".
        usable = [p for p in (plans or [])
                  if p.actions and p.actions[0].kind != "pass"]
        print(f"\n=== board: {kind}")
        print(f"  plan_candidates            : {len(plans or [])}")
        print(f"  after the pass filter      : {len(usable)}")
        print(f"  solver_first               : {policy._solver_first()}")
        print(f"  decided reason             : {out.get('reason')!r} ok={out.get('ok')}")
        print(f"  value_ready after decide() : {policy._value_ready}")
        print(f"  value_choice_stats         : {policy.value_choice_stats}")
        print(f"  hp_choice_stats            : {policy.hp_choice_stats}")
        gc = {"asked": getattr(policy, "_gc_asked", 0),
              "fired": getattr(policy, "_gc_fired", 0)}
        print(f"  grid_choice (internal)     : {gc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
