"""Cost model: everything is converted to minutes, and every prior is learnable.

Why minutes
-----------
A player's real currency is time. Kamas are just a convertible form of it. If the
planner knows the best kamas/minute available at the player's level, then:

    "buy the item"      costs  price / kamas_per_minute      minutes
    "farm the drop"     costs  kills / drop_rate * kill_time minutes
    "craft it"          costs  sum(ingredient costs) + craft  minutes
    "quest for it"      costs  prerequisite chain            minutes

and the choice between them is a plain `min()`. That is how "farm the best
kamas/hour item, then buy it" appears without anyone writing that rule.

Priors vs experience
--------------------
Every number below is a **fallback**, not a truth. When an ``ExperienceStore`` is
attached, each function asks it first:

    kamas/minute     Gamma posterior over (kills, minutes)   per monster x band
    drop rate        Beta posterior                          per item
    market price     log-normal, short half-life             per item
    travel time      log-normal                              per (from, to)

With no experience the model still works — it just says so, through
``calibrated=False`` on every estimate it produces.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from planning.bandit import band_of
from planning.experience import Event, ExperienceStore
from planning.kb import KnowledgeBase, Monster, REPO_ROOT

STATE_FILE = REPO_ROOT / "data" / "kb" / "economy_state.json"


@dataclass
class PlayerState:
    """The minimum the cost model needs to know about the character."""

    level: int = 1
    kamas: int = 0
    x: int = 4
    y: int = -19
    profession_levels: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level, "kamas": self.kamas, "x": self.x, "y": self.y,
            "professions": dict(self.profession_levels),
        }


@dataclass
class Estimate:
    """A cost estimate in minutes, with its own honesty flags."""

    minutes: float
    kamas: int = 0
    confidence: float = 1.0
    calibrated: bool = True
    detail: str = ""

    def scaled(self, factor: float) -> "Estimate":
        return Estimate(self.minutes * factor, self.kamas, self.confidence,
                        self.calibrated, self.detail)

    def plus(self, other: "Estimate") -> "Estimate":
        return Estimate(
            self.minutes + other.minutes,
            self.kamas + other.kamas,
            min(self.confidence, other.confidence),
            self.calibrated and other.calibrated,
            "",
        )


class CostModel:
    """Converts game facts into minutes. Priors first, experience when available."""

    # -- priors (all overridable by the experience store) --------------------
    KILLS_PER_MINUTE_AT_PARITY = 2.5   # one fight every ~24 s incl. walking
    BOSS_FIGHT_PENALTY = 0.25
    DROP_RATE_PRIOR = 0.06             # per kill, per dropped item
    SELL_FRICTION = 0.6
    TRAVEL_MINUTES_PER_MAP = 0.12
    CRAFT_MINUTES_PER_INGREDIENT = 0.05
    XP_CURVE_CALIBRATED = False

    def __init__(self, kb: KnowledgeBase, experience: Optional[ExperienceStore] = None) -> None:
        self.kb = kb
        self.experience = experience
        self._monster_drops: Optional[Dict[int, List[int]]] = None
        self._kpm_cache: Dict[tuple, tuple] = {}
        self._farm_cache: Dict[tuple, List[Tuple[Monster, float]]] = {}
        self.price_fn = self._npc_price

    # -- monster <-> item index ---------------------------------------------

    def _npc_price(self, item_id: int) -> int:
        pair = self.kb.item_level_price(item_id)
        return pair[1] if pair else 0

    def monster_drops(self) -> Dict[int, List[int]]:
        """monster_id -> [item_id], built once from the drop edges."""
        if self._monster_drops is not None:
            return self._monster_drops
        arrays = self.kb._arrays  # noqa: SLF001 - same package, avoids a copy
        drop_edges = arrays["drop_edges"]
        drop_off = arrays["item_drop_off"]
        drop_len = arrays["item_drop_len"]
        item_ids = arrays["item_id"]
        index: Dict[int, List[int]] = {}
        for item_index in range(len(item_ids)):
            offset = int(drop_off[item_index])
            length = int(drop_len[item_index])
            if length <= 0:
                continue
            item_id = int(item_ids[item_index])
            for edge in drop_edges[offset:offset + length]:
                index.setdefault(int(edge), []).append(item_id)
        self._monster_drops = index
        return index

    # -- combat --------------------------------------------------------------

    def prior_kills_per_minute(self, monster: Monster, player_level: int) -> float:
        """The hand-written curve. Used until experience replaces it."""
        gap = player_level - monster.level
        if gap >= 0:
            kpm = self.KILLS_PER_MINUTE_AT_PARITY * (1.0 + 0.03 * min(gap, 60))
            kpm = min(kpm, 6.0)
        else:
            kpm = self.KILLS_PER_MINUTE_AT_PARITY * max(0.12, 1.0 - 0.07 * (-gap))
        if monster.is_boss:
            kpm *= self.BOSS_FIGHT_PENALTY
        return max(kpm, 0.05)

    def kills_per_minute(self, monster: Monster, player_level: int) -> float:
        """Measured throughput when it exists, blended with the prior by evidence."""
        prior = self.prior_kills_per_minute(monster, player_level)
        if self.experience is None:
            return prior
        posterior = self.experience.get(f"kpm:{monster.id}:{band_of(player_level)}")
        if posterior is None or posterior.confidence() < 0.25:
            return prior
        weight = min(1.0, posterior.confidence())
        measured = posterior.mean()
        return max(0.02, prior * (1.0 - weight) + measured * weight)

    def kill_minutes(self, monster: Monster, player_level: int) -> float:
        return 1.0 / self.kills_per_minute(monster, player_level)

    def record_farm(self, monster_id: int, player_level: int, kills: int,
                    minutes: float, ts: Optional[float] = None,
                    source: str = "live") -> None:
        """One real farm session: the only way kills/minute ever improves."""
        if self.experience is None or kills <= 0 or minutes <= 0:
            return
        self.experience.record(
            Event(
                kind="gamma",
                key=f"kpm:{monster_id}:{band_of(player_level)}",
                value=kills / minutes,
                weight=minutes,
                ts=ts if ts is not None else __import__("time").time(),
                source=source,
                context={"monster": monster_id, "level": player_level},
                meta={"kills": kills, "minutes": round(minutes, 2)},
            ),
            shape=1.0, rate=1.0, half_life_days=45.0,
        )
        self._kpm_cache.clear()
        self._farm_cache.clear()

    # -- economy -------------------------------------------------------------

    def kamas_per_kill(self, monster_id: int, drop_rate: float) -> float:
        drops = self.monster_drops().get(int(monster_id), [])
        value = sum(self.price_fn(item_id) * drop_rate for item_id in drops)
        return value * self.SELL_FRICTION

    def kamas_per_minute(self, player_level: int, drop_rate: float = DROP_RATE_PRIOR,
                         top_n: int = 40) -> Tuple[float, Optional[Monster]]:
        """Best kamas/minute the player can actually reach at this level."""
        key = (int(player_level), round(float(drop_rate), 4))
        cached = self._kpm_cache.get(key)
        if cached is not None:
            return cached
        best_rate = 0.0
        best_monster: Optional[Monster] = None
        for monster_id in self.monster_drops().keys():
            monster = self.kb.monster(monster_id)
            if monster is None or monster.level > player_level + 3:
                continue
            if monster.level < max(1, player_level - 40):
                continue
            rate = (self.kamas_per_kill(monster_id, drop_rate)
                    * self.kills_per_minute(monster, player_level))
            if rate > best_rate:
                best_rate, best_monster = rate, monster
        result = (max(best_rate, 1.0), best_monster)
        self._kpm_cache[key] = result
        return result

    def farm_estimates(self, player_level: int, drop_rate: float = DROP_RATE_PRIOR,
                       top_n: int = 5) -> List[Tuple[Monster, float]]:
        """Ranked (monster, kamas/minute) — this is the "best farm" answer."""
        key = (int(player_level), round(float(drop_rate), 4), int(top_n))
        cached = self._farm_cache.get(key)
        if cached is not None:
            return cached
        scored: List[Tuple[float, Monster]] = []
        for monster_id in self.monster_drops().keys():
            monster = self.kb.monster(monster_id)
            if monster is None or monster.is_boss:
                continue
            if not (max(1, player_level - 40) <= monster.level <= player_level + 3):
                continue
            rate = (self.kamas_per_kill(monster_id, drop_rate)
                    * self.kills_per_minute(monster, player_level))
            if rate > 0:
                scored.append((rate, monster))
        scored.sort(key=lambda pair: -pair[0])
        result = [(monster, rate) for rate, monster in scored[:top_n]]
        self._farm_cache[key] = result
        return result

    # -- travel --------------------------------------------------------------

    def travel_minutes(self, player: PlayerState, x: int, y: int) -> Estimate:
        if self.experience is not None:
            posterior = self.experience.get(f"travel:{player.x},{player.y}->{x},{y}")
            if posterior is not None and posterior.confidence() > 0.3:
                return Estimate(posterior.mean(), confidence=posterior.confidence(),
                                calibrated=True, detail="from measured trips")
        hops = math.ceil((abs(player.x - x) + abs(player.y - y)) / 40)
        return Estimate(hops * self.TRAVEL_MINUTES_PER_MAP * 8, calibrated=False)

    def record_travel(self, player: PlayerState, x: int, y: int, minutes: float) -> None:
        if self.experience is None or minutes <= 0:
            return
        self.experience.record(
            Event(kind="lognormal", key=f"travel:{player.x},{player.y}->{x},{y}",
                  value=minutes, context={"from": [player.x, player.y], "to": [x, y]}),
            prior_median=2.0, prior_strength=2.0, half_life_days=120.0,
        )

    # -- level curve ---------------------------------------------------------

    def xp_to_next(self, level: int) -> int:
        return int(110 * (level ** 2.4) + 50 * level)

    def level_up_minutes(self, from_level: int, to_level: int,
                         xp_per_minute: Optional[float] = None) -> Estimate:
        if to_level <= from_level:
            return Estimate(0.0, calibrated=True)
        total_xp = sum(self.xp_to_next(level) for level in range(from_level, to_level))
        if not xp_per_minute:
            return Estimate(
                minutes=float("inf"), confidence=0.15, calibrated=False,
                detail=f"{total_xp:,} xp required; xp/minute not calibrated yet",
            )
        return Estimate(total_xp / xp_per_minute, calibrated=True,
                        detail=f"{total_xp:,} xp at {xp_per_minute:,.0f} xp/min")


class Economy:
    """Economic state: market prices, drop rates, throughput, run history.

    All of it lives in the ``ExperienceStore`` so it survives restarts, is
    auditable per observation, and can be rebuilt from the event log.
    """

    LEVEL_VALUE_CURVE = (25.0, 1.4)   # kamas = 25 * level^1.4
    PRICE_HALF_LIFE_DAYS = 3.0        # market prices drift fast

    def __init__(self, cost: CostModel, experience: Optional[ExperienceStore] = None,
                 state_file: Path = STATE_FILE) -> None:
        self.cost = cost
        self.experience = experience or ExperienceStore()
        self.state_file = state_file
        cost.experience = self.experience
        cost.price_fn = self.price_of
        self.runs: List[Dict[str, Any]] = []
        self._price_cache: Dict[int, int] = {}

    # -- persistence ---------------------------------------------------------

    def save(self) -> None:
        self.experience.save()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({
            "note": "derived mirror of the experience store; see experience.jsonl",
            "parameters": len(self.experience),
            "events_seen": self.experience.events_seen,
            "runs": self.runs[-200:],
        }, indent=2), encoding="utf-8")

    # -- prices --------------------------------------------------------------

    def estimated_value(self, level: int) -> int:
        scale, exponent = self.LEVEL_VALUE_CURVE
        return int(max(50.0, scale * (max(1, level) ** exponent)))

    def price_source(self, item_id: int) -> str:
        if self.experience.get(f"price:{int(item_id)}") is not None:
            return "market"
        pair = self.cost.kb.item_level_price(item_id)
        if pair is None:
            return "unknown"
        level, npc = pair
        return "npc" if npc > self.estimated_value(level) else "estimated"

    def price_of(self, item_id: int, fallback: int = 0) -> int:
        cached = self._price_cache.get(int(item_id))
        if cached is not None:
            return cached
        posterior = self.experience.get(f"price:{int(item_id)}")
        if posterior is not None and posterior.confidence() > 0.15:
            value = int(posterior.mean())
            self._price_cache[int(item_id)] = value
            return value
        pair = self.cost.kb.item_level_price(item_id)
        if pair is None:
            return fallback
        level, npc = pair
        prior = self.estimated_value(level)
        value = int(npc * 1.5) if npc > prior else prior
        self._price_cache[int(item_id)] = value
        return value

    def record_price(self, item_id: int, price: int, source: str = "live") -> None:
        if price <= 0:
            return
        self.experience.record(
            Event(kind="lognormal", key=f"price:{int(item_id)}", value=float(price),
                  source=source, context={"item": int(item_id)}),
            prior_median=float(self.price_of(item_id)),
            prior_strength=2.0, prior_sigma=0.5,
            half_life_days=self.PRICE_HALF_LIFE_DAYS,
        )
        self._price_cache.pop(int(item_id), None)
        self.cost._kpm_cache.clear()   # noqa: SLF001 - price feeds the scan
        self.cost._farm_cache.clear()  # noqa: SLF001

    # -- drop rate -----------------------------------------------------------

    def drop_rate(self, item_id: int) -> float:
        posterior = self.experience.get(f"drop:{int(item_id)}")
        if posterior is not None:
            return posterior.mean()
        return self.cost.DROP_RATE_PRIOR

    def drop_rate_confidence(self, item_id: int) -> float:
        posterior = self.experience.get(f"drop:{int(item_id)}")
        return 0.0 if posterior is None else posterior.confidence()

    def record_drop_run(self, item_id: int, drops: int, kills: int,
                        source: str = "live") -> float:
        """Update the Beta posterior after a real farm session."""
        if kills <= 0:
            return self.drop_rate(item_id)
        self.experience.record(
            Event(
                kind="beta",
                key=f"drop:{int(item_id)}",
                value=max(0.0, min(1.0, drops / kills)),
                weight=float(kills),
                source=source,
                context={"item": int(item_id)},
                meta={"drops": drops, "kills": kills},
            ),
            alpha=2.0, beta=30.0, half_life_days=180.0,
        )
        self.cost._kpm_cache.clear()   # noqa: SLF001 - drop rate feeds the scan
        self.cost._farm_cache.clear()  # noqa: SLF001
        return self.drop_rate(item_id)

    # -- run log -------------------------------------------------------------

    def record_run(self, record: Dict[str, Any]) -> None:
        self.runs.append(record)

    def realised_minutes_per_kama(self) -> Optional[float]:
        samples = [r for r in self.runs if r.get("kamas") and r.get("minutes")]
        if len(samples) < 3:
            return None
        total_minutes = sum(float(r["minutes"]) for r in samples)
        total_kamas = sum(float(r["kamas"]) for r in samples)
        if total_kamas <= 0:
            return None
        return total_minutes / (total_kamas / 1000.0)
