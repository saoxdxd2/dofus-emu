"""What the fight brain actually costs in memory, measured by the OS on the shipped runtime.

Why this exists. The cost ledger quoted `idle daemon 31 MB` and `8.7 MB per instance`, and the one
number it never had was the brain process itself: an earlier ctypes probe passed a real process
handle and read null, so the row stayed empty and the gap was remembered rather than filled.
`tools/knowledge_index_probe.residency()` already solves that correctly -- GetProcessMemoryInfo on
INVALID_HANDLE_VALUE, argtypes declared -- so this imports it instead of reimplementing it.

Peak cannot be reset inside a process, so each arm is its own run:

    runtimes\\python\\python.exe tools\\brain_ram_probe.py            # shipped config (slim view)
    runtimes\\python\\python.exe tools\\brain_ram_probe.py --full     # the pre-L.34 behaviour

Payloads are streamed one at a time and dropped, the way the server receives them. Holding the whole
recorded corpus in a list was the first version's mistake: it added ~30 MB of resident that belonged
to the script, and a claim about the brain's footprint is not the brain's footprint if the meter is
the biggest thing in the room. Writes `data/brain_ram_baseline.json` for `tools/doc_facts.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent      # .../dofus-network
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_LOG = Path(os.path.expandvars(r"%LOCALAPPDATA%\DofusFarm\fight_boards.jsonl"))
OUT_PATH = REPO_ROOT / "data" / "brain_ram_baseline.json"


def own_turn_payloads(path: Path) -> Iterator[Dict[str, Any]]:
    from tools.policy_over_record_boards import fill

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        board = row.get("board") if isinstance(row, dict) else None
        if isinstance(board, dict) and board.get("is_my_turn"):
            yield fill(board)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true",
                        help="restore the full live observation (the pre-§L.34 behaviour)")
    parser.add_argument("--mode", default="loop", choices=("loop", "parse", "floor"),
                        help="loop: fill and decide every board; parse: fill only, no decide; "
                             "floor: load the head and decide one board. Peak is cumulative per "
                             "process, so separating the candidates needs separate runs (#84).")
    parser.add_argument("--passes", type=int, default=5)
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    args = parser.parse_args(argv)

    from tools.knowledge_index_probe import residency

    marks: Dict[str, Any] = {}
    resident, peak, commit = residency()
    marks["interpreter"] = [resident, peak, commit]

    import numpy                                                       # noqa: F401
    marks["numpy"] = list(residency())

    if args.full:
        os.environ["DOFUS_FIGHT_OBSERVATION"] = "full"
    else:
        os.environ.pop("DOFUS_FIGHT_OBSERVATION", None)

    from combat import combat_ai_server as srv
    from combat.battle_solver import Board
    from combat.live_policy import LivePolicy

    def _board_from(payload: Dict[str, Any]) -> None:
        # The parse half of a turn, with nothing else kept: this is what `decide()` spends
        # before the forward starts, so mode=parse isolates it (#84).
        Board.from_fight_state(payload)

    marks["combat imported"] = list(residency())

    # The log writer is stubbed: this measures memory, and a probe that appends 675 rows to
    # trajectories/live_policy_decisions.jsonl would contaminate the evidence §L.34 prices.
    srv._log_decision = lambda payload, decision: None
    policy = LivePolicy(checkpoint=REPO_ROOT / "models" / "encounter_selfplay.pkl")
    srv.POLICY = policy
    policy.warmup()
    marks["policy loaded + warm"] = list(residency())

    path = Path(args.log)
    if not path.exists():
        print("absent:", path)
        print("VERDICT: no recorded boards, so the loop below measures an idle process. "
              "This is not the brain's footprint.")
        return 1

    decide_s = 0.0
    decided = 0
    started = time.perf_counter()
    if args.mode == "floor":
        for state in own_turn_payloads(path):
            inner = time.perf_counter()
            srv.decide(state)
            decide_s += time.perf_counter() - inner
            decided += 1
            del state
            break
    else:
        passes = args.passes if args.mode == "loop" else 1
        for _pass in range(passes):
            for state in own_turn_payloads(path):
                if args.mode == "loop":
                    inner = time.perf_counter()
                    srv.decide(state)
                    decide_s += time.perf_counter() - inner
                else:                                    # parse: the work, without the forward
                    _board_from(state)
                decided += 1
                del state
    elapsed = time.perf_counter() - started
    resident, peak, commit = residency()
    marks["final"] = [resident, peak, commit]

    payload = {
        "arm": "full" if args.full else "slim",
        "mode": args.mode,
        "resident_mb": round(resident, 1),
        "peak_mb": round(peak, 1),
        "commit_mb": round(commit, 1),
        "decides": decided,
        "passes": args.passes,
        # decide only. The loop's wall clock is higher because reading and filling the payload
        # is inside it; conflating the two is how a 7.9 ms figure would get quoted as a latency.
        "ms_per_decide": round(decide_s / max(1, decided) * 1000.0, 3),
        "ms_per_loop_iteration": round(elapsed / max(1, decided) * 1000.0, 3),
        "stages_mb": {k: [round(v, 1) for v in t] for k, t in marks.items()},
        "interpreter": "%d.%d.%d" % sys.version_info[:3],
        "backend": os.environ.get("DOFUS_FIGHT_BACKEND", "default"),
        "at": time.time(),
    }
    print(json.dumps(payload, indent=1))
    print("resident %.1f MB  peak %.1f MB  commit %.1f MB over %d streamed decides "
          "(%.3f ms each, loop wall %.1f s)"
          % (resident, peak, commit, decided,
             decide_s / max(1, decided) * 1000.0, elapsed))
    if args.mode != "loop":
        # Only the shipped loop is the ledger. Writing a parse-only or floor run over
        # data/brain_ram_baseline.json would leave CURRENT.md quoting a number for a loop the
        # brain does not run.
        print("baseline not written (mode=%s is a diagnostic arm, not the ledger)" % args.mode)
        return 0
    try:
        OUT_PATH.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        print("baseline written:", OUT_PATH)
    except OSError as exc:
        print("baseline NOT written (%s): the ledger must call these numbers unmeasured" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
