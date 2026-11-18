"""Replay recorded boards through the live policy; imputation is explicit and diagnostic."""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent      # .../dofus-network
DEFAULT_LOG = Path(os.path.expandvars(r"%LOCALAPPDATA%\DofusFarm\fight_boards.jsonl"))
ASSUMPTION = ("missing max_hp := current hp (a lower bound, not a confirmed maximum); "
              "missing self max_ap/max_mp := my_stats.action_points/movement_points "
              "(unverified budget proxies)")


def fill(board: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of the board with the contract's missing max values supplied."""
    out = json.loads(json.dumps(board))
    stats = out.get("my_stats") or {}
    for fighter in out.get("fighters") or []:
        if not isinstance(fighter, dict):
            continue
        if fighter.get("is_me"):
            if fighter.get("max_hp") is None:
                fighter["max_hp"] = fighter.get("hp")
            if fighter.get("max_ap") is None:
                fighter["max_ap"] = stats.get("action_points")
            if fighter.get("max_mp") is None:
                fighter["max_mp"] = stats.get("movement_points")
            if fighter.get("ap") is None:
                fighter["ap"] = fighter.get("max_ap")
            if fighter.get("mp") is None:
                fighter["mp"] = fighter.get("max_mp")
        if fighter.get("max_hp") is None:
            fighter["max_hp"] = fighter.get("hp")
    return out


def replay(rows: List[Dict[str, Any]], decide: Callable[[Dict[str, Any]], Dict[str, Any]],
           only_my_turn: bool = True, fill_missing: bool = False) -> Dict[str, Any]:
    """Run `decide` over every selected board and count what came back."""
    reasons: collections.Counter = collections.Counter()
    action_types: collections.Counter = collections.Counter()
    flags: collections.Counter = collections.Counter()
    latencies: List[float] = []
    errors: collections.Counter = collections.Counter()
    used = 0
    for record in rows:
        board = record.get("board") or {}
        if only_my_turn and not board.get("is_my_turn"):
            continue
        used += 1
        try:
            decision = decide(fill(board) if fill_missing else board)
        except Exception as exc:                       # noqa: BLE001 - a harness reports, it cannot fix
            errors["%s: %s" % (type(exc).__name__, str(exc)[:70])] += 1
            continue
        reasons[str(decision.get("reason"))] += 1
        contract = decision.get("contract") or {}
        flags["actionable=%s value_ready=%s" % (contract.get("actionable"),
                                                contract.get("value_ready"))] += 1
        action = decision.get("action") or {}
        action_types[str(action.get("type"))] += 1
        if isinstance(decision.get("latency_ms"), (int, float)):
            latencies.append(float(decision["latency_ms"]))
    latencies.sort()
    return {
        "boards": used,
        "reasons": dict(reasons),
        "actions": dict(action_types),
        "contract": dict(flags),
        "errors": dict(errors),
        "p50_ms": round(latencies[len(latencies) // 2], 1) if latencies else None,
        "p95_ms": round(latencies[int(len(latencies) * 0.95)], 1) if latencies else None,
    }


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("logs", nargs="*", type=Path,
                        help="fight_boards.jsonl files (default: the live one)")
    parser.add_argument("--limit", type=int, default=0, help="only the first N boards")
    parser.add_argument("--all-turns", action="store_true",
                        help="also ask the policy on turns that belong to another fighter")
    parser.add_argument("--fill", action="store_true",
                        help="also replay a filled copy of each board (the imputation arm); "
                             "the default arm is the payload exactly as recorded")
    parser.add_argument("--checkpoint", default=str(REPO_ROOT / "models"
                                                    / "encounter_selfplay.pkl"))
    args = parser.parse_args(argv)

    paths = args.logs or [DEFAULT_LOG]
    rows: List[Dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            print("absent:", path)
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
                    rows.append(record)
    print("recorded boards: %d   source: %s" % (len(rows), ", ".join(map(str, paths))))
    if not rows:
        print("VERDICT: nothing recorded, so there is no board to replay. This is not a "
              "result about the policy.")
        return 1
    if args.limit > 0:
        rows = rows[:args.limit]

    raw_ready = collections.Counter(
        str(((r.get("board") or {}).get("contract") or {}).get("actionable")) for r in rows)
    print("as recorded, contract actionable:", dict(raw_ready))

    t0 = time.perf_counter()
    # Run as `python tools/x.py`, the repo root is not on sys.path -- and this tool exists to
    # import the brain, so it has to put it there itself rather than depend on the caller.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from combat.live_policy import LivePolicy
        policy = LivePolicy(checkpoint=Path(args.checkpoint))
    except Exception as exc:                           # noqa: BLE001 - environment, not behaviour
        print("the live policy could not be loaded here: %s: %s" % (type(exc).__name__, exc))
        print("Run this with the shipped runtime (runtimes/python/python.exe).")
        return 2
    print("policy loaded in %.1fs   checkpoint: %s"
          % (time.perf_counter() - t0, Path(args.checkpoint).name))

    def _show(label: str, report: Dict[str, Any]) -> None:
        print("--- %s ---" % label)
        print("asked: %d boards%s" % (report["boards"],
                                      "" if args.all_turns else " (own turn only)"))
        print("contract:", report["contract"])
        print("reasons:", report["reasons"])
        print("actions:", report["actions"])
        print("latency ms: p50=%s p95=%s" % (report["p50_ms"], report["p95_ms"]))
        if report["errors"]:
            print("exceptions:", report["errors"])

    # The default arm is the honest baseline: the board as the live page produced it. Any
    # refusal there is a fact about the payload, and supplying the values is a separate,
    # labelled experiment rather than a silent repair.
    report_raw = replay(rows, policy.decide, only_my_turn=not args.all_turns)
    _show("raw arm: the payload exactly as recorded, nothing supplied", report_raw)
    report_fill = None
    if args.fill:
        print("fill hypothesis:", ASSUMPTION)
        report_fill = replay(rows, policy.decide, only_my_turn=not args.all_turns,
                             fill_missing=True)
        _show("fill arm: the same boards with the missing max values supplied", report_fill)
    # Whether the trained critic ever picked the realization on real boards. `_realize` reaches it
    # only when the chosen cast cannot land from any cell the MP reaches, so a zero here says the
    # value head is not in the shipped path -- which no amount of value-head training changes.
    # The counter is cumulative on the policy, so it is printed once over every arm above.
    print("value head picks the realization:", policy.value_choice_stats)

    asked = report_raw["boards"]
    if asked and not report_raw["errors"]:
        print("\nVERDICT: as recorded, the actor answers %d of %d own turns with a cast "
              "(%.1f%%) at p50 %s ms."
              % (report_raw["actions"].get("cast", 0), asked,
                 100.0 * report_raw["actions"].get("cast", 0) / asked,
                 report_raw["p50_ms"]))
        if report_fill is not None and not report_fill["errors"]:
            fill_asked = report_fill["boards"]
            print("Under the fill hypothesis above it answers %d of %d (%.1f%%) at p50 %s ms."
                  % (report_fill["actions"].get("cast", 0), fill_asked,
                     100.0 * report_fill["actions"].get("cast", 0) / max(1, fill_asked),
                     report_fill["p50_ms"]))
            print("Both are measurements on recorded geometry -- not a live fight, not a "
                  "win rate, and no proof that the client will send those fields.")
        else:
            print("This is a measurement on recorded geometry -- not a live fight, not a "
                  "win rate, and no proof that the client will send those fields.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
