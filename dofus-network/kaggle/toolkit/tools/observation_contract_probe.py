"""Prove the slim live observation is equivalent to the full one, and price it.

`combat.observation.live_view` answers a live request with the four-key board
block plus the contract instead of the whole observation, because the decision
reads only the contract's two booleans and the only offline reader
(`combat/grid_probe.py:51`) reads only `observation.board`. That is only safe if
`contract_from_board` returns *exactly* `validate_observation(build_observation(b))`
-- same `ok`/`actionable`/`value_ready`, same `errors`, same `warnings` strings.
This checks that on every recorded own-turn board and then times both, so the
claim is a measurement and not an intention.

    python tools/observation_contract_probe.py                 # parity + price
    python tools/observation_contract_probe.py --parity-only
    python tools/observation_contract_probe.py --rounds 5 --passes 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent      # .../dofus-network
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from combat.battle_solver import Board                                # noqa: E402
from combat.observation import (board_block, build_observation,        # noqa: E402
                                contract_from_board, live_view,
                                validate_observation)

DEFAULT_LOG = Path(os.path.expandvars(r"%LOCALAPPDATA%\DofusFarm\fight_boards.jsonl"))


def load_boards(paths: List[Path]) -> List[Board]:
    """Every own-turn board in the recorded corpus, filled the way the policy fills it."""
    from tools.policy_over_record_boards import fill

    states: List[Dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            print("absent:", path)
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            board = row.get("board") if isinstance(row, dict) else None
            if isinstance(board, dict) and board.get("is_my_turn"):
                states.append(fill(board))
    return [Board.from_fight_state(state) for state in states]


def parity(boards: List[Board]) -> Dict[str, int]:
    counts = {"contract": 0, "board_block": 0, "slim_view": 0, "full_view": 0,
              "actionable": 0}
    for board in boards:
        observation = build_observation(board, source="live")
        want = validate_observation(observation)
        got = contract_from_board(board)
        if want != got:
            counts["contract"] += 1
            if counts["contract"] <= 3:
                for key in sorted(set(want) | set(got)):
                    if want.get(key) != got.get(key):
                        print("  CONTRACT DIFF %s: full=%r slim=%r"
                              % (key, want.get(key), got.get(key)))
        if observation["board"] != board_block(board):
            counts["board_block"] += 1
            if counts["board_block"] <= 3:
                print("  BOARD DIFF full=%r slim=%r"
                      % (observation["board"], board_block(board)))
        counts["actionable"] += bool(got["actionable"])

        slim_obs, slim_contract = live_view(board)
        if slim_contract != want or slim_obs["board"] != observation["board"]:
            counts["slim_view"] += 1
        os.environ["DOFUS_FIGHT_OBSERVATION"] = "full"
        try:
            full_obs, full_contract = live_view(board)
        finally:
            del os.environ["DOFUS_FIGHT_OBSERVATION"]
        if full_obs != observation or full_contract != want:
            counts["full_view"] += 1

    print("boards: %d   actionable: %d" % (len(boards), counts["actionable"]))
    print("contract mismatches      : %d" % counts["contract"])
    print("board-block mismatches   : %d" % counts["board_block"])
    print("live_view slim mismatches: %d" % counts["slim_view"])
    print("live_view full mismatches: %d" % counts["full_view"])
    counts["boards"] = len(boards)
    return counts


def price(boards: List[Board], rounds: int, passes: int) -> None:
    def full(board: Board) -> None:
        validate_observation(build_observation(board, source="live"))

    def slim(board: Board) -> None:
        live_view(board)

    for board in boards[:5]:
        full(board)
        slim(board)

    arms = {"full": full, "slim": slim}
    best: Dict[str, List[float]] = {"full": [], "slim": []}
    for rnd in range(rounds):
        order = ("full", "slim") if rnd % 2 == 0 else ("slim", "full")
        for label in order:
            call = arms[label]
            total = 0.0
            for _ in range(passes):
                started = time.perf_counter()
                for board in boards:
                    call(board)
                total += time.perf_counter() - started
            best[label].append(total / passes / len(boards) * 1000.0)

    full_ms = min(best["full"])
    slim_ms = min(best["slim"])
    print("full  build+validate: %.5f ms/board  (rounds %s)"
          % (full_ms, ", ".join("%.5f" % v for v in best["full"])))
    print("slim  live_view     : %.5f ms/board  (rounds %s)"
          % (slim_ms, ", ".join("%.5f" % v for v in best["slim"])))
    print("removed: %.5f ms/board = %.1f%% of the observation path"
          % (full_ms - slim_ms, (full_ms - slim_ms) / full_ms * 100.0))
    if min(best["slim"]) >= max(best["full"]):
        print("the two round ranges do not overlap")
    return {"full_ms": full_ms, "slim_ms": slim_ms,
            "removed_ms": full_ms - slim_ms,
            "removed_pct": (full_ms - slim_ms) / full_ms * 100.0,
            "rounds": rounds, "passes": passes}


def _write_baseline(boards: List[Board], counts: Dict[str, int],
                    timings: Optional[Dict[str, Any]]) -> None:
    """Publish what was measured, so a document can reference it instead of retyping it."""
    payload = {
        "boards": counts["boards"],
        "actionable": counts["actionable"],
        "contract_mismatches": counts["contract"],
        "board_block_mismatches": counts["board_block"],
        "slim_view_mismatches": counts["slim_view"],
        "full_view_mismatches": counts["full_view"],
        "full_ms_per_board": None if timings is None else round(timings["full_ms"], 5),
        "slim_ms_per_board": None if timings is None else round(timings["slim_ms"], 5),
        "removed_pct_of_observation_path":
            None if timings is None else round(timings["removed_pct"], 1),
        "rounds": None if timings is None else timings["rounds"],
        "passes": None if timings is None else timings["passes"],
        "interpreter": "%d.%d.%d" % sys.version_info[:3],
        "at": time.time(),
    }
    out = REPO_ROOT / "data" / "fight_observation_baseline.json"
    try:
        out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        print("baseline written: %s" % out)
    except OSError as exc:
        print("baseline NOT written (%s): docs must call these numbers unmeasured" % exc)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", nargs="*", default=None,
                        help="fight_boards.jsonl files (default: LOCALAPPDATA/DofusFarm)")
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--passes", type=int, default=6)
    parser.add_argument("--parity-only", action="store_true")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.logs] if args.logs else [DEFAULT_LOG]
    boards = load_boards(paths)
    if not boards:
        print("VERDICT: no recorded own-turn boards, so there is nothing to prove "
              "equivalence on. This is not a pass.")
        return 1

    counts = parity(boards)
    mismatches = (counts["contract"] + counts["board_block"]
                  + counts["slim_view"] + counts["full_view"])
    if mismatches:
        print("VERDICT: the slim observation is NOT equivalent -- %d differing check(s). "
              "live_view must not ship." % mismatches)
        return 1
    timings = None
    if not args.parity_only:
        timings = price(boards, args.rounds, args.passes)
    print("VERDICT: the slim live observation is field-identical to the full one on "
          "%d recorded boards" % len(boards))
    # The gate does the same thing: publish what was measured, so docs and
    # tools/doc_facts.py read a number off disk instead of one someone typed. An
    # unmeasurable fact is listed as absent, never guessed.
    _write_baseline(boards, counts, timings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
