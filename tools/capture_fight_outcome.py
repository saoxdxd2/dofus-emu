"""Copy the page's own `last_fight_end` into the fight-outcome evidence log.

The autopilot writes `fight_outcome` rows only while its loop is driving the
instance. A fight played by the in-page policy (or by hand) still exists in the
client, verdict and battle log included, at `state.snapshot.last_fight_end`; this
tool appends that row in the same shape so `tools/damage_calibration.py` can pair
it with `trajectories/live_policy_decisions.jsonl`.

Nothing is invented: the battle log is the client's own message ring, the verdict
is the server's `GameFightEndMessage`, and a fight id already in the file is
skipped instead of duplicated.

    python tools/capture_fight_outcome.py --instance dofus-03
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "live_evidence" / "fight_outcomes.jsonl"


def bridge_port() -> int:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(local) / "DofusFarm" / "bridge_port.txt"
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 8894


def call(tool: str, args: Optional[Dict[str, Any]] = None,
         instance: str = "dofus-03", timeout: float = 40.0) -> Dict[str, Any]:
    body = json.dumps({"id": "capture", "tool": tool, "args": args or {},
                       "target_instance": instance}).encode()
    req = urllib.request.Request("http://127.0.0.1:%d/directive" % bridge_port(),
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def known_fights(path: Path) -> set:
    out = set()
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        outcome = row.get("outcome") if isinstance(row, dict) else None
        if isinstance(outcome, dict) and outcome.get("fight_id") is not None:
            out.add(str(outcome["fight_id"]))
    return out


def build_row(end: Dict[str, Any], instance: str) -> Dict[str, Any]:
    return {
        "kind": "fight_outcome",
        "run": "capture:%s" % instance,
        "outcome": {
            "fight_id": end.get("fight_id"),
            "my_id": end.get("my_id"),
            "won": end.get("won"),
            "at": end.get("at"),
            "end": {
                "fight_id": end.get("fight_id"),
                "my_id": end.get("my_id"),
                "won": end.get("won"),
                "battle_log": end.get("battle_log") or [],
            },
            "challenges": end.get("challenges") or [],
        },
        "observation": {"source": "state.snapshot.last_fight_end"},
    }


def run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    env = call("state.snapshot", None, args.instance)
    result = env.get("result") or {}
    snap = result.get("data") or {}
    if not result.get("ok"):
        print("capture: snapshot failed: %s" % (result.get("error") or "")[:200],
              file=sys.stderr)
        return 2
    end = snap.get("last_fight_end") or {}
    if end.get("fight_id") is None:
        print("capture: no fight has ended in this client session yet")
        return 0
    battle_log = end.get("battle_log") or []
    actions = [r for r in battle_log
               if isinstance(r, dict) and r.get("at_action") is not None]
    if not battle_log:
        print("capture: fight %s has an empty battle log; nothing to pair" % end["fight_id"])
        return 0
    row = build_row(end, args.instance)
    fight_key = str(end["fight_id"])
    out.parent.mkdir(parents=True, exist_ok=True)
    if fight_key in known_fights(out):
        print("capture: fight %s is already recorded" % fight_key)
        return 0
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    print("capture: fight %s (%s), %d battle-log rows, %d with an action id -> %s"
          % (fight_key, "won" if end.get("won") else "lost",
             len(battle_log), len(actions), out))
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", default="dofus-03")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
