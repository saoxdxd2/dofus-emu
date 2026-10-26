"""Is the live board the brain receives actually usable? Measured, not assumed.

Why this exists. The page sends `fight.state` to ``127.0.0.1:8891`` for every turn, and the
server answers only if the payload satisfies its observation contract. Whether the *live*
payload does is not a question about the model -- it is a question about the fields the page
emits -- so it can be answered from the snapshots the loop already recorded.

Reads only. The board rows come from ``Autopilot._record_fight_board``
(``%LOCALAPPDATA%\\DofusFarm\\fight_boards.jsonl``); each carries the contract the loop
computed when it took the snapshot. A row without one is rebuilt through
``combat.observation`` when that import is available.

    python tools/brain_readiness.py                       # default log
    python tools/brain_readiness.py path/to/fight_boards.jsonl
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

DEFAULT_LOG = Path(os.path.expandvars(r"%LOCALAPPDATA%\DofusFarm\fight_boards.jsonl"))
REPO_ROOT = Path(__file__).resolve().parent.parent      # .../dofus-network
# The fields the contract needs per fighter, in the order a reader should fix them.
REQUIRED = ("max_hp", "ap", "max_ap", "mp", "max_mp", "initiative")


def _rows(paths: List[Path]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            print(f"absent: {path}")
            continue
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and isinstance(record.get("board"), dict):
                    out.append(record)
    return out


def _contract(board: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """The contract for one board: the recorded one if present, else recomputed."""
    recorded = board.get("contract")
    if isinstance(recorded, dict) and "value_ready" in recorded:
        return recorded, "recorded"
    try:
        # `python tools/x.py` does not put the repo root on sys.path, and this is the only
        # branch that needs the package.
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from combat.observation import observation_from_fight_state
        _obs, built = observation_from_fight_state(board)
    except Exception as exc:                       # noqa: BLE001 - a reader reports, it cannot fix
        return {}, f"unavailable ({type(exc).__name__}: {str(exc)[:60]})"
    return built, "recomputed"


def _null_census(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """How often each required field is missing on the *self* fighter."""
    census = collections.Counter()
    for record in rows:
        fighters = (record.get("board") or {}).get("fighters") or []
        me = next((f for f in fighters
                   if isinstance(f, dict) and f.get("is_me")), None)
        for field in REQUIRED:
            if me is None:
                census[f"{field}: no self fighter"] += 1
            elif field not in me:
                census[f"{field}: key absent"] += 1
            elif me.get(field) is None:
                census[f"{field}: null"] += 1
            else:
                census[f"{field}: set"] += 1
    return dict(census)


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("logs", nargs="*", type=Path,
                        help="fight_boards.jsonl files (default: the live one)")
    args = parser.parse_args(argv)
    paths = args.logs or [DEFAULT_LOG]
    rows = _rows(paths)
    print(f"board snapshots: {len(rows)}   source: "
          + ", ".join(str(p) for p in paths))
    if not rows:
        print("VERDICT: no recorded boards -- the loop never captured a fight, so "
              "nothing here is evidence about a live one.")
        return 1

    errors: collections.Counter = collections.Counter()
    warnings: collections.Counter = collections.Counter()
    ready = collections.Counter()
    sourced = collections.Counter()
    fights = set()
    for record in rows:
        board = record["board"]
        contract, source = _contract(board)
        sourced[source] += 1
        if not contract:
            continue
        ready[(str(contract.get("actionable")), str(contract.get("value_ready")))] += 1
        fights.add(board.get("fight_id"))
        for item in contract.get("errors") or []:
            errors[str(item).split(":")[0]] += 1
        for item in contract.get("warnings") or []:
            warnings[str(item).split(":")[0]] += 1

    print(f"distinct fight ids: {len(fights)}   contracts: "
          + ", ".join(f"{k}={v}" for k, v in sourced.most_common()))
    print("actionable / value_ready:")
    for (actionable, value_ready), count in ready.most_common():
        print(f"  actionable={actionable} value_ready={value_ready}: {count}")
    print("errors:", dict(errors.most_common(8)) or "none")
    print("warnings:", dict(warnings.most_common(10)) or "none")
    print("self-fighter fields:")
    for field, count in sorted(_null_census(rows).items()):
        print(f"  {field}: {count}")
    # The page also captures which names the live objects really carry. Printed here so
    # one live fight is enough to answer the fields this census still reports as missing
    # (creature_generic_id, enemy spells) without a second instrument.
    for bag in ("stat_keys", "data_keys", "fighter_keys"):
        sample = next(((row.get("board") or {}).get(bag) for row in rows
                       if (row.get("board") or {}).get(bag)), None)
        if sample:
            for side in ("me", "enemy"):
                names = sample.get(side) or []
                print(f"{bag}[{side}] ({len(names)}): {', '.join(names) or 'none'}")

    unusable = ready.get(("False", "False"), 0)
    total = sum(ready.values())
    # `initiative` is only in the payload since the producer read the right bag, so its
    # absence dates the row, independent of what any file's mtime says.
    vintage = sum(1 for record in rows
                  if any("initiative" in fighter
                         for fighter in (record.get("board") or {}).get("fighters") or []
                         if isinstance(fighter, dict)))
    print(f"boards carrying the post-fix fields: {vintage} of {len(rows)}")
    if total and unusable == total and not vintage:
        print("\nVERDICT: not one recorded live board satisfied the contract, so the brain "
              "would have answered `observation_not_actionable` on every turn of every fight "
              "in this corpus -- and the page's own fallback (`_localTick`) would have played "
              "them. Starting the brain (G24) is necessary and provably not sufficient: the "
              "missing fields above are the blocker (G25).\n"
              "This corpus predates the producer fix, so it measures the old reads and says "
              "nothing about the current ones. It is evidence that the bug was real, not "
              "evidence that it is gone -- only a newly captured fight is.")
    elif total and unusable == total:
        print(f"\nVERDICT: all {total} boards were rejected even though {vintage} of them "
              "carry the post-fix fields, so the blocker is still open -- the census above "
              "names the fields.")
    elif unusable:
        print(f"\nVERDICT: {unusable} of {total} recorded boards were rejected by the "
              "contract. The field census above says which fields to fill.")
    else:
        print("\nVERDICT: every recorded board was actionable; the learned policy could "
              "decide these turns. Readiness is still not a win rate -- see the fight log.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
