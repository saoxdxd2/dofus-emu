"""AND/OR plan search: "get this item" -> an explicit, auditable plan tree.

The model of the world is a small AND/OR graph, all of it derived from the cache:

    acquire(item, qty)
        OR  buy      -> kamas  (needs kamas, or farm them first)
        OR  farm     -> one of the monsters that drops it  (AND: reach it, kill N)
        OR  craft    -> AND over every ingredient  (recursive, cycle-guarded)
        OR  quest    -> item is quest-locked (no recipe, no drop, no price)

`min()` over those branches, all expressed in minutes, is the entire "reasoning".
It is a chain of thought that can be printed, diffed, audited and unit-tested —
which a token-level chain of thought cannot be, and which costs no RAM.

The search is best-first with a node budget so a pathological recipe tree cannot
stall the agent loop. ``alternatives`` keeps the rejected branches *with their
costs*, because "why did it choose to farm instead of buy" is the question a user
actually asks.
"""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from planning.economy import CostModel, Economy, Estimate, PlayerState
from planning.kb import KnowledgeBase

if TYPE_CHECKING:  # pragma: no cover
    from planning.bandit import StrategyRanker

# Tools the harness can actually execute today (agent/schema.py). Anything a plan
# needs outside this set is reported as a gap instead of silently planned.
EXECUTABLE_TOOLS = {
    "knowledge.search", "nav.goto", "nav.follow", "quest.list", "quest.advance",
    "farm.start", "farm.stop", "fight.join", "fight.pass", "inv.list", "inv.sell",
    "hdv.scan", "hdv.list", "chat.say", "state.snapshot", "safe.stop",
}

MAX_CRAFT_DEPTH = 3
MAX_NODES = 3000


@dataclass
class Goal:
    item_id: int
    qty: int = 1
    need_equip: bool = False   # True => the character's level gates the goal
    lang: str = "fr"


@dataclass
class PlanNode:
    kind: str                     # buy | farm | craft | quest | level_up | blocked
    label: str
    estimate: Estimate
    children: List["PlanNode"] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def minutes(self) -> float:
        return self.estimate.minutes

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def leaves(self) -> List["PlanNode"]:
        if not self.children:
            return [self]
        found: List[PlanNode] = []
        for child in self.children:
            found.extend(child.leaves())
        return found

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "minutes": None if math.isinf(self.minutes) else round(self.minutes, 1),
            "kamas": self.estimate.kamas,
            "confidence": round(self.estimate.confidence, 2),
            "calibrated": self.estimate.calibrated,
            "detail": self.estimate.detail,
            "meta": self.meta,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass
class Plan:
    goal: Goal
    root: PlanNode
    alternatives: List[PlanNode]
    player: PlayerState
    node_budget_used: int = 0

    @property
    def minutes(self) -> float:
        return self.root.minutes

    @property
    def feasible(self) -> bool:
        return not math.isinf(self.minutes)

    def leaves(self) -> List[PlanNode]:
        return self.root.leaves()

    def missing_tools(self) -> List[str]:
        return sorted({
            node.meta["tool"] for node in self.root.walk()
            if node.meta.get("tool") and node.meta["tool"] not in EXECUTABLE_TOOLS
        })

    def next_action(self) -> Optional[Dict[str, Any]]:
        """The first executable leaf, as a tool call the harness can run.

        Falls back to the preflight tool when the leaf itself is not
        implementable (e.g. ``buy`` -> ``hdv.scan`` to at least read the price).
        """
        for leaf in self.leaves():
            tool = leaf.meta.get("tool")
            if tool and tool in EXECUTABLE_TOOLS:
                return {"tool": tool, "args": leaf.meta.get("args") or {},
                        "why": leaf.label}
            preflight = leaf.meta.get("preflight_tool")
            if preflight and preflight in EXECUTABLE_TOOLS:
                return {"tool": preflight,
                        "args": leaf.meta.get("preflight_args") or leaf.meta.get("args") or {},
                        "why": f"preflight for: {leaf.label}",
                        "preflight": True}
        return None

    def render(self) -> str:
        lines: List[str] = []

        def walk(node: PlanNode, depth: int) -> None:
            prefix = "  " * depth
            marker = {"buy": "$", "farm": "*", "craft": "+", "quest": "?", "level_up": "^"}.get(node.kind, "-")
            minutes = "inf" if math.isinf(node.minutes) else f"{node.minutes:,.0f}m"
            flags = []
            if not node.estimate.calibrated:
                flags.append("uncalibrated")
            if node.estimate.confidence < 0.5:
                flags.append(f"conf {node.estimate.confidence:.0%}")
            correction = node.meta.get("correction")
            if correction and abs(correction - 1.0) > 0.05:
                flags.append(f"x{correction:.2f} from experience")
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            lines.append(f"{prefix}{marker} {node.label}  ({minutes}, {node.estimate.kamas:,}k){suffix}")
            if node.estimate.detail and depth > 0:
                lines.append(f"{prefix}    . {node.estimate.detail}")
            for child in node.children:
                walk(child, depth + 1)

        walk(self.root, 0)
        if self.alternatives:
            lines.append("  rejected:")
            for alt in self.alternatives:
                minutes = "inf" if math.isinf(alt.minutes) else f"{alt.minutes:,.0f}m"
                lines.append(f"    - {alt.label} ({minutes})")
        missing = self.missing_tools()
        if missing:
            lines.append(f"  missing tools: {', '.join(missing)}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": {"item_id": self.goal.item_id, "qty": self.goal.qty,
                     "need_equip": self.goal.need_equip},
            "player": self.player.as_dict(),
            "minutes": None if math.isinf(self.minutes) else round(self.minutes, 1),
            "feasible": self.feasible,
            "missing_tools": self.missing_tools(),
            "root": self.root.to_dict(),
            "alternatives": [alt.to_dict() for alt in self.alternatives],
            "node_budget_used": self.node_budget_used,
        }


class Planner:
    """Best-first AND/OR search over item acquisition strategies."""

    def __init__(self, kb: KnowledgeBase, cost: CostModel, economy: Economy,
                 ranker: Optional["StrategyRanker"] = None) -> None:
        self.kb = kb
        self.cost = cost
        self.economy = economy
        # Optional: reorders feasible branches using learned corrections. Without
        # it the planner is deterministic; with it, the estimates come from play.
        self.ranker = ranker
        self._nodes = 0
        self._memo: Dict[Tuple[int, int, int], Optional[PlanNode]] = {}

    # -- public --------------------------------------------------------------

    def plan(self, goal: Goal, player: PlayerState) -> Plan:
        self._nodes = 0
        self._memo = {}
        item = self.kb.item(goal.item_id, goal.lang)
        if item is None:
            root = PlanNode("blocked", f"unknown item {goal.item_id}",
                            Estimate(float("inf"), confidence=0.0, calibrated=False))
            return Plan(goal, root, [], player)

        acquisition = self._acquire(goal.item_id, goal.qty, goal.lang, player, depth=0, stack=())
        alternatives: List[PlanNode] = []
        if acquisition is None:
            root = PlanNode("blocked", f"{item.name}: no route found",
                            Estimate(float("inf"), confidence=0.0, calibrated=False))
        else:
            alternatives = acquisition.meta.pop("_alternatives", [])
            root = acquisition

        # Level gate: owning is not equipping.
        if goal.need_equip and player.level < item.level:
            level_up = self._level_up(player.level, item.level)
            root = PlanNode(
                "level_up",
                f"reach level {item.level} (now {player.level})",
                level_up.plus(root.estimate),
                children=[root],
                meta={"item_level": item.level, "player_level": player.level},
            )

        return Plan(goal, root, alternatives, player, node_budget_used=self._nodes)

    # -- strategies ----------------------------------------------------------

    def _acquire(self, item_id: int, qty: int, lang: str, player: PlayerState,
                 depth: int, stack: Tuple[int, ...]) -> Optional[PlanNode]:
        key = (item_id, qty, player.level)
        # Memo check first: revisiting an ingredient must not burn search budget.
        if key in self._memo:
            cached = self._memo[key]
            return None if cached is None else cached

        item = self.kb.item(item_id, lang)
        if item is None:
            return None
        if item_id in stack:
            return None  # cycle in the recipe graph

        if self._nodes > MAX_NODES:
            # Budget exhausted: keep the branch visible but never selectable.
            return PlanNode(
                "blocked", f"{item.name}: search budget exhausted",
                Estimate(float("inf"), confidence=0.0, calibrated=False,
                         detail="raise MAX_NODES or reduce recipe depth"))
        self._nodes += 1

        branches: List[PlanNode] = []

        buy = self._buy(item.id, qty, item.name, player)
        if buy is not None:
            branches.append(buy)

        farm = self._farm(item.id, qty, item.name, player)
        if farm is not None:
            branches.append(farm)

        if depth < MAX_CRAFT_DEPTH and item.recipe:
            craft = self._craft(item, qty, player, depth, stack + (item_id,), lang)
            if craft is not None:
                branches.append(craft)

        if not branches:
            branches.append(self._quest_only(item))

        branches.sort(key=lambda node: node.minutes)
        if self.ranker is not None:
            best = self.ranker.choose(branches, player.level)
        else:
            best = branches[0]
        best.meta["_alternatives"] = [node for node in branches if node is not best]
        self._memo[key] = best
        return best

    def _buy(self, item_id: int, qty: int, name: str, player: PlayerState) -> Optional[PlanNode]:
        unit = self.economy.price_of(item_id)
        if unit <= 0:
            return None
        total = unit * qty
        missing = max(0, total - player.kamas)
        kpm, best_monster = self.cost.kamas_per_minute(player.level, self.economy.drop_rate(item_id))
        travel = self.cost.travel_minutes(player, 4, -19)  # Astrub market as a stand-in
        farm_minutes = missing / kpm if kpm > 0 else float("inf")

        children: List[PlanNode] = []
        if missing > 0 and best_monster is not None:
            # The kamas have to come from somewhere: this is the "farm the best
            # kamas/minute spot, then buy" route, expanded as a real child.
            children.append(PlanNode(
                "farm",
                f"farm {best_monster.name} for {missing:,} kamas",
                Estimate(farm_minutes,
                         confidence=0.6,
                         calibrated=False,
                         detail=(f"{kpm:,.0f} k/min at level {player.level}, "
                                 f"short {missing:,}k")),
                meta={"tool": "farm.start",
                      "args": {"monster": best_monster.name,
                               "kill_count": max(10, math.ceil(farm_minutes * 2.5))},
                      "monster_id": best_monster.id, "for_kamas": missing},
            ))

        estimate = Estimate(
            farm_minutes + travel.minutes,
            kamas=total,
            confidence=0.7 if self.economy.price_source(item_id) == "market" else 0.45,
            calibrated=self.economy.price_source(item_id) == "market",
            detail=(f"{unit:,}k/unit ({self.economy.price_source(item_id)} price), "
                    f"have {player.kamas:,}k"),
        )
        return PlanNode("buy", f"buy {qty}x {name}", estimate, children=children,
                        meta={"tool": "hdv.buy", "args": {"item": name, "quantity": qty},
                              "preflight_tool": "hdv.scan",
                              "preflight_args": {"item": name},
                              "item_id": item_id, "price": total,
                              "missing_kamas": missing})

    def _farm(self, item_id: int, qty: int, name: str, player: PlayerState) -> Optional[PlanNode]:
        monsters = self.kb.monsters_for_item(item_id, "fr")
        if not monsters:
            return None
        rate = self.economy.drop_rate(item_id)
        kills = math.ceil(qty / max(rate, 1e-6))
        best: Optional[PlanNode] = None
        for monster in sorted(monsters, key=lambda m: m.level):
            # A level-1 character cannot kill a level-200 monster. This is a hard
            # feasibility gate, not a cost penalty: without it the search happily
            # "farms" content the character cannot survive.
            if monster.level > player.level + 20:
                continue
            per_kill = self.cost.kill_minutes(monster, player.level)
            minutes = kills * per_kill
            if monster.level > player.level + 10:
                minutes *= 1.6  # risk: deaths, retreats, corpse runs
            confidence = 0.4 + 0.5 * self.economy.drop_rate_confidence(item_id)
            estimate = Estimate(
                minutes,
                confidence=confidence,
                calibrated=self.economy.drop_rate_confidence(item_id) > 0.3,
                detail=(f"{kills:,} kills of {monster.name} (lvl {monster.level}) "
                        f"at {rate:.1%} drop, {per_kill * 60:.0f}s/kill"),
            )
            node = PlanNode(
                "farm", f"farm {monster.name} for {name}",
                estimate,
                meta={"tool": "farm.start",
                      "args": {"monster": monster.name, "kill_count": kills},
                      "monster_id": monster.id, "monster_level": monster.level,
                      "kills": kills, "drop_rate": round(rate, 4)},
            )
            if best is None or node.minutes < best.minutes:
                best = node
        return best

    def _craft(self, item, qty: int, player: PlayerState, depth: int,
               stack: Tuple[int, ...], lang: str) -> Optional[PlanNode]:
        if len(item.recipe) > 40:
            return None  # a 40-ingredient recipe is not a plan, it is a project
        children: List[PlanNode] = []
        total = Estimate(0.0)
        for ingredient_id in item.recipe:
            child = self._acquire(ingredient_id, qty, lang, player, depth + 1, stack)
            if child is None:
                return None
            child.meta.pop("_alternatives", None)
            children.append(child)
            total = total.plus(child.estimate)
        total = total.plus(Estimate(
            len(item.recipe) * self.cost.CRAFT_MINUTES_PER_INGREDIENT * qty,
            calibrated=True))
        return PlanNode(
            "craft", f"craft {qty}x {item.name} ({len(item.recipe)} ingredients)",
            total, children=children,
            meta={"tool": "craft.make", "args": {"item": item.name, "quantity": qty},
                  "item_id": item.id, "ingredients": len(item.recipe)},
        )

    def _quest_only(self, item) -> PlanNode:
        """No price, no drops, no recipe: the item is locked behind a chain."""
        quests = self.kb.quests_for_level(max(1, item.level), limit=3)
        return PlanNode(
            "quest", f"quest chain for {item.name}",
            Estimate(float("inf"), confidence=0.1, calibrated=False,
                     detail="quest-locked: no market price, no drop, no recipe in cache"),
            meta={"tool": "quest.list",
                  "args": {"level_min": item.level, "limit": 5},
                  "candidate_quests": [q.name for q in quests]},
        )

    def _level_up(self, from_level: int, to_level: int) -> Estimate:
        estimate = self.cost.level_up_minutes(from_level, to_level)
        if math.isinf(estimate.minutes):
            # Still give a usable ordering hint: level gap * a nominal farm hour.
            gap = to_level - from_level
            estimate = Estimate(
                minutes=float(gap) * 45.0,
                confidence=0.1,
                calibrated=False,
                detail=f"{gap} levels; xp/minute not calibrated (nominal 45 min/level)",
            )
        return estimate
