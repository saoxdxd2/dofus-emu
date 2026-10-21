"""Pipeline demo: one action in, two curves out.

Shows the whole chain end to end, with every stage doing exactly one job:

    tool result  ->  Outcome      (payload -> lesson; refuses to invent a signal)
    Outcome      ->  posteriors   (instant curve: before/after per parameter)
    Outcome      ->  decision graph (long-term curve: edges, constraints, discoveries)
    plan search  ->  thinking cache (structure reused, estimates re-priced)

Run from ``dofus-network``::

    python -m planning.pipeline_demo
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from planning.bandit import StrategyRanker
from planning.economy import CostModel, Economy, PlayerState
from planning.experience import ExperienceStore
from planning.graph_memory import DecisionGraph
from planning.kb import KnowledgeBase
from planning.planner import Goal, Planner
from planning.schedule import ScheduleManager
from planning.thinking_cache import ThinkingCache
from planning.trace import TraceLog

ITEM = 384          # Laine de Bouftou: dropped by Bouftou (lvl 22), also craftable
LEVEL = 5


def _farm_result(kills: int, drops: int, minutes: float) -> Dict[str, Any]:
    """What a client bridge *should* return: counters, not a wall of text."""
    return {"ok": True, "source": "live", "latency_ms": 1200.0,
            "data": {"kills": kills, "drops": drops, "minutes": minutes,
                     "kamas": drops * 180}}


def _format_update(update: Dict[str, Any]) -> str:
    """Rank corrections live in log space; show them as multipliers."""
    def show(value: Optional[float]) -> str:
        if value is None:
            return "     —"
        if update["param"].startswith("rank:"):
            return f"x{math.exp(value):.2f}"
        return f"{value:8.4f}"

    return (f"{update['param']:24s} {show(update['before']):>9s} -> "
            f"{show(update['after']):<9s} "
            f"(n {update['n_before']:.0f} -> {update['n_after']:.0f}, {update['why']})")


def _fail(reason: str) -> Dict[str, Any]:
    return {"ok": False, "source": "live", "error": reason, "data": None}


def main(argv: Optional[List[str]] = None) -> int:
    kb = KnowledgeBase()
    item = kb.item(ITEM)
    monsters = kb.monsters_for_item(ITEM)
    if not monsters:
        raise SystemExit("no drop table for the demo item")
    bouftou = min(monsters, key=lambda m: m.level)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = ExperienceStore(root / "experience.jsonl", root / "posteriors.json",
                                autosave_every=10_000)
        cost = CostModel(kb, store)
        economy = Economy(cost, store)
        ranker = StrategyRanker(store, seed=7)
        graph = DecisionGraph(root / "graph.json")
        cache = ThinkingCache(root / "cache.json", ttl_minutes=180)
        traces = TraceLog(root / "traces.jsonl")
        planner = Planner(kb, cost, economy, ranker, graph, cache)
        player = PlayerState(level=LEVEL, kamas=0)
        manager = ScheduleManager(planner, economy, state_file=root / "schedule.json",
                                  ranker=ranker, graph=graph, traces=traces)
        goal = Goal(item_id=ITEM)
        goal_text = f"get 1x {item.name}"

        print(f"item   : {item.name} (lvl {item.level}), dropped by {bouftou.name} "
              f"(lvl {bouftou.level}, id {bouftou.id})")
        print(f"player : level {LEVEL}, 0 kamas")
        print()

        # ---- A. plan + thinking cache ------------------------------------
        print("A. plan search and structure cache")
        timings: List[float] = []
        for attempt in range(4):
            started = time.perf_counter()
            plan = planner.plan(goal, player)
            elapsed_ms = (time.perf_counter() - started) * 1000
            timings.append(elapsed_ms)
            print(f"   attempt {attempt + 1}: {plan.root.kind:5s} "
                  f"{plan.minutes:8.1f}m  nodes={plan.node_budget_used:3d}  "
                  f"cache_hit={plan.cache_hit}  {elapsed_ms:6.1f} ms")
        print(f"   cache: {json.dumps(cache.stats())}")
        print()

        # ---- B. a day of actions -----------------------------------------
        print("B. actions -> instant updates -> long-term graph")
        print()

        actions = [
            ("farm", "farm.start", {"monster": bouftou.name, "kill_count": 16},
             {"monster_id": bouftou.id, "item_id": ITEM, "monster_level": bouftou.level},
             90.7, _farm_result(16, 0, 96.0), "16 kills, no drop, 96 min"),
            ("farm", "farm.start", {"monster": bouftou.name, "kill_count": 16},
             {"monster_id": bouftou.id, "item_id": ITEM, "monster_level": bouftou.level},
             90.7, _farm_result(16, 1, 88.0), "16 kills, 1 drop, 88 min"),
            ("farm", "farm.start", {"monster": bouftou.name, "kill_count": 16},
             {"monster_id": bouftou.id, "item_id": ITEM, "monster_level": bouftou.level},
             90.7, _farm_result(16, 0, 104.0), "16 kills, no drop, 104 min"),
            ("buy", "hdv.scan", {"item": item.name},
             {"item_id": ITEM}, 4.0,
             {"ok": True, "source": "live",
              "data": {"price": 1850, "quantity": 120}}, "scan: 1850k, 120 listings"),
        ]

        for action, tool, args, entity, predicted, result, label in actions:
            out = manager.record_action(action, tool, args, result, entity=entity,
                                        predicted_minutes=predicted,
                                        elapsed_minutes=predicted)
            print(f"   {label:34s} -> {out['signal']:10s} residual {out['residual'] or 0:.2f}")
            for update in out["updates"]:
                print("       " + _format_update(update))
            if out["graph"]:
                print(f"       graph {out['graph']['edge']}: "
                      f"{out['graph']['attempts']} attempts, "
                      f"success {out['graph']['success_rate']:.0%}")
            print()

        # ---- C. a route that keeps failing becomes a constraint -----------
        print("C. repeated failure changes the graph structure")
        hard_monster = 47   # Abraknyde, level 38 — unreachable for a level-5 char
        for attempt in range(5):
            out = manager.record_action(
                "farm", "farm.start", {"monster": "Abraknyde", "kill_count": 10},
                _fail("target unreachable: monster level 38"),
                entity={"monster_id": hard_monster, "monster_level": 38},
                predicted_minutes=120.0, elapsed_minutes=0.0)
            change = out["graph"] or {}
            note = (f"blocked_below={change['blocked_below']}"
                    if change.get("blocked_below") else f"attempt {attempt + 1}")
            print(f"   attempt {attempt + 1}: {out['signal']:8s}  {note}")
        print()
        print(f"   constraints: {json.dumps(graph.constraints())}")
        print(f"   planner now prunes it: "
              f"blocked={graph.blocked('farm', {'monster_id': hard_monster, 'monster_level': 38}, LEVEL)}")
        print()

        # ---- D. what the two curves accumulated ---------------------------
        print("D. accumulation")
        print(f"   graph   : {json.dumps(graph.stats())}")
        print(f"   store   : {len(store)} parameters, {store.events_seen} observations, "
              f"{store.rejected_events} rejected (non-live)")
        print(f"   traces  : {json.dumps(traces.summary())}")
        print(f"   cache   : {json.dumps(cache.stats())}")
        print()

        # ---- E. re-plan with what was learned ----------------------------
        print("E. the same goal, planned again")
        fresh = Planner(kb, cost, economy, ranker, graph, None)  # no cache: show real search
        plan = fresh.plan(goal, player)
        print(plan.render())
        print()
        print("   drop rate now:", round(economy.drop_rate(ITEM), 4),
              f"(prior {cost.DROP_RATE_PRIOR})")
        kpm, best = cost.kamas_per_minute(LEVEL)
        print(f"   best farm at level {LEVEL}: {best.name if best else '—'} at {kpm:,.0f} k/min")
        print()
        print("last trace:")
        for row in traces.tail(1):
            print("  ", json.dumps({k: row.get(k) for k in
                                    ("action", "tool", "predicted_minutes",
                                     "outcome", "updates")}, ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
