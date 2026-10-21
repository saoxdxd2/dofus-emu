"""Demo: a level-1 character asks for a level-200 item.

Run from ``dofus-network``::

    python -m planning.demo                          # auto-pick a level 200 item
    python -m planning.demo --item "Dofus Pourpre" --equip
    python -m planning.demo --item Goultard --kamas 2000000
    python -m planning.demo --simulate               # exercise the schedule manager

The point of the demo is the *plan tree*: it is the chain of thought, printed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planning.economy import CostModel, Economy, PlayerState
from planning.kb import KnowledgeBase
from planning.planner import Goal, Planner
from planning.schedule import ScheduleManager


def _auto_item(kb: KnowledgeBase, level: int = 200) -> Optional[int]:
    """Pick a real, reachable item at the requested level.

    Preference order: has drops (so the farm branch is real), has a recipe of a
    sane size (so the recursion is real), and has a non-zero cache price.
    """
    arrays = kb._arrays  # noqa: SLF001 - demo only
    levels = arrays["item_level"]
    recipe_len = arrays["item_recipe_len"]
    drop_len = arrays["item_drop_len"]
    prices = arrays["item_price"]
    ids = arrays["item_id"]

    best: Optional[tuple[int, int]] = None   # (score, item_id)
    for index in range(len(ids)):
        item_level = int(levels[index])
        if not (int(level) - 15 <= item_level <= int(level)):
            continue
        drops = int(drop_len[index])
        recipe = int(recipe_len[index])
        if drops <= 0:
            continue
        score = 0
        score += 3 if 3 <= recipe <= 12 else 0
        score += 2 if int(prices[index]) > 1 else 0
        score += 1 if drops <= 3 else 0
        candidate = (score, int(ids[index]))
        if best is None or candidate > best:
            best = candidate
    return best[1] if best else None


def build(item: Optional[str], level: int) -> tuple[KnowledgeBase, int, str]:
    kb = KnowledgeBase()
    if item:
        hits = kb.find_items(item, "fr", 3)
        if not hits:
            raise SystemExit(f"item {item!r} not found in the knowledge base")
        best = max(hits, key=lambda h: h.level)
        return kb, best.id, best.name
    item_id = _auto_item(kb, level)
    if item_id is None:
        raise SystemExit("could not pick a target item")
    resolved = kb.item(item_id)
    return kb, item_id, resolved.name if resolved else str(item_id)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item", default=None)
    parser.add_argument("--level", type=int, default=200, help="target level when auto-picking")
    parser.add_argument("--player-level", type=int, default=1)
    parser.add_argument("--kamas", type=int, default=0)
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--equip", action="store_true", help="require the level gate")
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args(argv)

    kb, item_id, name = build(args.item, args.level)
    target = kb.item(item_id)
    print(f"knowledge base : {json.dumps(kb.stats())}")
    print(f"goal           : {args.qty}x {name} (item {item_id}, level {target.level if target else '?'})")
    print(f"player         : level {args.player_level}, {args.kamas:,} kamas")
    print()

    cost = CostModel(kb)
    economy = Economy(cost)
    planner = Planner(kb, cost, economy)
    player = PlayerState(level=args.player_level, kamas=args.kamas)

    print(f"price source   : {economy.price_of(item_id):,}k for the goal ({economy.price_source(item_id)})")
    kpm, best = cost.kamas_per_minute(player.level)
    print(f"best farm at level {player.level}: "
          f"{best.name if best else 'none'} at {kpm:,.0f} kamas/min")
    print("top farms:")
    for monster, rate in cost.farm_estimates(player.level, top_n=5):
        print(f"  {monster.name:28s} lvl {monster.level:3d}  {rate:6,.0f} k/min")
    print()

    goal = Goal(item_id=item_id, qty=args.qty, need_equip=args.equip)
    plan = planner.plan(goal, player)
    print(f"PLAN ({plan.node_budget_used} nodes searched)")
    print(plan.render())
    print()

    action = plan.next_action()
    print("next tool call:", json.dumps(action, ensure_ascii=False))
    missing = plan.missing_tools()
    if missing:
        print(f"blocked by missing tools: {', '.join(missing)}")
    print()

    if args.simulate:
        manager = ScheduleManager(planner, economy)
        manager.submit(goal, player)
        print("phase 1 — execute the plan, but the farm yields 0 drops")
        for tick in range(3):
            call = manager.tick()
            if call is None:
                print("  plan exhausted")
                break
            print(f"  tick {tick}: {call['tool']} {json.dumps(call['args'], ensure_ascii=False)}"
                  f"  <- {call['why']}")
            result = {"ok": True, "source": "sim",
                      "data": {"kills": 200, "drops": 0, "kamas": 0}}
            outcome = manager.report(result)
            print(f"          -> {outcome}")

        print()
        print("phase 2 — the player comes back with 50M kamas; the manager should notice")
        rich = PlayerState(level=player.level, kamas=50_000_000)
        call = manager.tick(rich)
        if manager.replans:
            print("  replanned:", json.dumps(manager.replans[-1], ensure_ascii=False))
        if call is None:
            print("  no executable action left")
        else:
            print(f"  next: {call['tool']} {json.dumps(call['args'], ensure_ascii=False)}"
                  f"  <- {call['why']}")
        print()
        print("plan now:")
        print(manager.plan.render() if manager.plan else "  (none)")
        print()
        print("manager status:", json.dumps(manager.status(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
