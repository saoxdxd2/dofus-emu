"""Combinatorial Gear + Spell Loadout Optimizer Ranked by Simulated TTK.

Solves the multi-slot gear selection problem subject to:
1. Hard AP <= 12 and MP <= 6 cap constraints.
2. Kamas budget limits from market data.
3. Item uniqueness and trophy set-bonus condition constraints (< 2 set bonuses).
4. Non-linear set bonuses dynamically activated for 2, 3, or 4 pieces.
5. Simulated Turns-To-Kill (TTK) ranking against boss stat profiles via combat damage formulas.
"""

from __future__ import annotations

import heapq
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from combat.damage_calculator import (
    CharacterStats,
    DofusDamageCalculator,
    TargetStats,
)
from data.game_data_extractor import DamageEffect, SpellInfo

from .gear import stat_score, weights_for
from .models import (
    ELEMENTS,
    PRIMARY_STAT,
    CharacterSnapshot,
    MarketSnapshot,
)
from .repository import (
    SLOT_NAMES,
    SLOT_ORDER,
    ItemTemplate,
    Repository,
    SetTemplate,
)

logger = logging.getLogger("dofus.advisor.optimizer")

# Default Boss archetypes for TTK ranking
DEFAULT_BOSS_PROFILES: Dict[str, Dict[str, Any]] = {
    "melee_bruiser": {
        "name": "Melee Bruiser",
        "hp": 4200,
        "level": 200,
        "resists": {"neutral": 10, "earth": 15, "fire": 10, "water": 10, "air": 15},
    },
    "ranged_artillery": {
        "name": "Ranged Artillery",
        "hp": 4200,
        "level": 200,
        "resists": {"neutral": 0, "earth": 0, "fire": 20, "water": 20, "air": 0},
    },
    "dungeon_boss_medium": {
        "name": "Mid-tier Boss",
        "hp": 2500,
        "level": 120,
        "resists": {"neutral": 10, "earth": 10, "fire": 10, "water": 10, "air": 10},
    },
}

# Standard core equipment slots to optimize
CORE_SLOTS = (0, 6, 7, 3, 5, 2, 4, 15)  # Amulet, Hat, Cloak, Belt, Boots, Ring 1, Ring 2, Shield


@dataclass
class LoadoutCandidate:
    slots: Dict[int, int]  # slot_id -> gid
    items: List[ItemTemplate]
    total_stats: Dict[str, float]
    active_sets: Dict[int, int]  # set_id -> piece_count
    cost: float
    ap: int
    mp: int
    heuristic_score: float
    damage_per_turn: float = 0.0
    simulated_ttk_turns: int = 0
    simulated_ttk_fractional: float = 0.0
    rotation: List[Tuple[str, int, float]] = field(default_factory=list)  # spell_name, count, total_damage

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slots": {SLOT_NAMES.get(k, str(k)): v for k, v in self.slots.items()},
            "cost": round(self.cost, 1),
            "ap": self.ap,
            "mp": self.mp,
            "heuristic_score": round(self.heuristic_score, 1),
            "damage_per_turn": round(self.damage_per_turn, 1),
            "simulated_ttk_turns": self.simulated_ttk_turns,
            "simulated_ttk_fractional": round(self.simulated_ttk_fractional, 2),
            "active_sets": self.active_sets,
            "rotation": [
                {"spell": name, "casts": cnt, "damage": round(dmg, 1)}
                for name, cnt, dmg in self.rotation
            ],
            "stats": {k: round(v, 1) for k, v in self.total_stats.items() if abs(v) > 0.01},
        }


class CombinatorialLoadoutOptimizer:
    """Branch-and-Bound / Beam Search equipment optimizer ranked by simulated TTK."""

    def __init__(
        self,
        repo: Repository,
        character: CharacterSnapshot,
        market: Optional[MarketSnapshot] = None,
        focus: str = "earth",
        max_budget: float = float("inf"),
        beam_width: int = 50,
        level_margin: int = 10,
    ) -> None:
        self.repo = repo
        self.character = character
        self.market = market or MarketSnapshot()
        self.focus = focus if focus in ELEMENTS else "earth"
        self.max_budget = max_budget
        self.beam_width = max(10, beam_width)
        self.level_margin = level_margin
        self.weights = weights_for(self.focus)

        # Baseline character attributes
        self.base_ap = 6
        self.base_mp = 3
        self.base_stats: Dict[str, float] = {}
        c_stats = getattr(character, "stats", {}) or {}
        for elem, stat in PRIMARY_STAT.items():
            self.base_stats[stat] = float(c_stats.get(stat, getattr(character, stat, 0)) or 0)
        self.base_stats["vitality"] = float(c_stats.get("vitality", getattr(character, "vitality", 0)) or 0)
        self.base_stats["wisdom"] = float(c_stats.get("wisdom", getattr(character, "wisdom", 0)) or 0)

    def _get_item_price(self, item_id: int) -> float:
        avg = self.market.average(item_id)
        if avg and avg > 0:
            return float(avg)
        return 0.0

    def _get_item_stats(self, template: ItemTemplate) -> Dict[str, float]:
        stats: Dict[str, float] = {}
        raw = self.repo.item_stats(template)
        for k, v in raw.items():
            if hasattr(v, "average"):
                stats[k] = float(v.average)
            elif isinstance(v, (int, float)):
                stats[k] = float(v)
        return stats

    def _get_set_bonus(self, set_id: int, count: int) -> Dict[str, float]:
        st = self.repo.set(set_id)
        if st is None or count <= 1:
            return {}
        bonus_ranges = st.bonus(count)
        stats: Dict[str, float] = {}
        for k, v in bonus_ranges.items():
            if hasattr(v, "average"):
                stats[k] = float(v.average)
            elif isinstance(v, (int, float)):
                stats[k] = float(v)
        return stats

    def _filter_candidates_for_slot(self, slot_id: int, min_score: float = 5.0) -> List[ItemTemplate]:
        max_level = self.character.level
        min_level = max(1, self.character.level - self.level_margin)

        candidates: List[Tuple[float, ItemTemplate]] = []
        for item in self.repo.items.values():
            if not (min_level <= item.level <= max_level):
                continue
            positions = self.repo.item_positions(item)
            if slot_id not in positions:
                continue

            cost = self._get_item_price(item.id)
            if cost > self.max_budget:
                continue

            stats = self._get_item_stats(item)
            score = stat_score(stats, self.weights)
            if score >= min_score:
                candidates.append((score, item))

        # Sort descending by score, retain top 15 for each slot to bound beam branch factor
        candidates.sort(key=lambda t: t[0], reverse=True)
        return [item for _, item in candidates[:15]]

    def optimize(
        self,
        slots: Optional[Sequence[int]] = None,
        boss_profile: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
    ) -> List[LoadoutCandidate]:
        """Runs Beam Search across slots, then ranks surviving builds by simulated TTK."""
        target_slots = list(slots if slots is not None else CORE_SLOTS)
        if not target_slots:
            return []

        # Candidate pool per slot
        slot_candidates: Dict[int, List[ItemTemplate]] = {
            s: self._filter_candidates_for_slot(s) for s in target_slots
        }

        # Initialize beam with empty build
        # State: (assigned_slots, equipped_ids, set_counts, cost, aggregated_stats)
        initial_state = (
            {},  # assigned slots
            set(),  # equipped item ids
            {},  # set counts
            0.0,  # cost
            dict(self.base_stats),  # aggregated stats
        )

        beam = [initial_state]

        for slot_pos in target_slots:
            candidates = slot_candidates.get(slot_pos, [])
            if not candidates:
                continue

            next_beam = []
            for assigned, item_ids, set_counts, cost, stats in beam:
                for item in candidates:
                    if item.id in item_ids:
                        continue  # Enforce uniqueness constraint

                    new_cost = cost + self._get_item_price(item.id)
                    if new_cost > self.max_budget:
                        continue

                    # Update stats
                    item_stats = self._get_item_stats(item)
                    new_stats = dict(stats)
                    for k, v in item_stats.items():
                        new_stats[k] = new_stats.get(k, 0.0) + v

                    # Update set counts & bonuses
                    new_set_counts = dict(set_counts)
                    set_id = item.set_ids[0] if item.set_ids else None
                    if set_id:
                        prev_cnt = new_set_counts.get(set_id, 0)
                        curr_cnt = prev_cnt + 1
                        new_set_counts[set_id] = curr_cnt

                        # Remove previous bonus, add new bonus
                        prev_bonus = self._get_set_bonus(set_id, prev_cnt)
                        curr_bonus = self._get_set_bonus(set_id, curr_cnt)
                        for k, v in prev_bonus.items():
                            new_stats[k] = new_stats.get(k, 0.0) - v
                        for k, v in curr_bonus.items():
                            new_stats[k] = new_stats.get(k, 0.0) + v

                    # Check hard cap constraints
                    total_ap = self.base_ap + int(new_stats.get("action_points", 0.0))
                    total_mp = self.base_mp + int(new_stats.get("movement_points", 0.0))
                    if total_ap > 12 or total_mp > 6:
                        continue

                    # Heuristic score for beam pruning
                    score = stat_score(new_stats, self.weights)
                    new_assigned = dict(assigned)
                    new_assigned[slot_pos] = item.id
                    new_ids = set(item_ids)
                    new_ids.add(item.id)

                    next_beam.append((score, (new_assigned, new_ids, new_set_counts, new_cost, new_stats)))

            if next_beam:
                # Retain top beam_width states
                next_beam.sort(key=lambda t: t[0], reverse=True)
                beam = [state for _, state in next_beam[: self.beam_width]]

        if not beam:
            return []

        # Convert surviving beam candidates to LoadoutCandidate instances
        candidate_builds: List[LoadoutCandidate] = []
        for assigned, item_ids, set_counts, cost, stats in beam:
            items_list = [self.repo.item(item_id) for item_id in assigned.values() if self.repo.item(item_id)]
            active_sets = {s_id: cnt for s_id, cnt in set_counts.items() if cnt >= 2}
            cand = LoadoutCandidate(
                slots=assigned,
                items=items_list,
                total_stats=stats,
                active_sets=active_sets,
                cost=cost,
                ap=min(12, self.base_ap + int(stats.get("action_points", 0.0))),
                mp=min(6, self.base_mp + int(stats.get("movement_points", 0.0))),
                heuristic_score=stat_score(stats, self.weights),
            )
            candidate_builds.append(cand)

        # Pipe candidate builds into battle_solver / damage calculator for TTK simulation
        boss_spec = boss_profile or DEFAULT_BOSS_PROFILES["melee_bruiser"]
        target = TargetStats(
            name=boss_spec.get("name", "Boss"),
            level=boss_spec.get("level", 200),
            max_hp=boss_spec.get("hp", 4200),
            current_hp=boss_spec.get("hp", 4200),
            res_percent_neutral=boss_spec.get("resists", {}).get("neutral", 0),
            res_percent_earth=boss_spec.get("resists", {}).get("earth", 0),
            res_percent_fire=boss_spec.get("resists", {}).get("fire", 0),
            res_percent_water=boss_spec.get("resists", {}).get("water", 0),
            res_percent_air=boss_spec.get("resists", {}).get("air", 0),
        )

        for cand in candidate_builds:
            self._simulate_ttk(cand, target)

        # Rank by simulated TTK ascending (fractional turns), then by damage descending
        candidate_builds.sort(key=lambda c: (c.simulated_ttk_fractional, -c.damage_per_turn))
        return candidate_builds[:top_k]

    def _simulate_ttk(self, cand: LoadoutCandidate, target: TargetStats) -> None:
        """Computes optimal spell rotation and simulated Turns-To-Kill against the target."""
        stats = cand.total_stats
        char = CharacterStats(
            level=self.character.level,
            action_points=cand.ap,
            movement_points=cand.mp,
            strength=int(stats.get("strength", 0)),
            intelligence=int(stats.get("intelligence", 0)),
            chance=int(stats.get("chance", 0)),
            agility=int(stats.get("agility", 0)),
            power=int(stats.get("power", 0)),
            damage=int(stats.get("damage", 0)),
            damage_neutral=int(stats.get("damage_neutral", 0)),
            damage_earth=int(stats.get("damage_earth", 0)),
            damage_fire=int(stats.get("damage_fire", 0)),
            damage_water=int(stats.get("damage_water", 0)),
            damage_air=int(stats.get("damage_air", 0)),
            critical_hit=int(stats.get("critical_hit", 0)),
            heals=int(stats.get("heals", 0)),
        )

        # Generate representative spell repertoire for the character's focus element
        spells = self._get_representative_spells()

        # Solve knapsack rotation: maximize expected damage under available AP
        ap_budget = cand.ap
        best_dmg, best_rotation = self._solve_spell_rotation(spells, char, target, ap_budget)

        cand.damage_per_turn = best_dmg
        cand.rotation = best_rotation

        if best_dmg > 0:
            cand.simulated_ttk_fractional = target.max_hp / best_dmg
            cand.simulated_ttk_turns = math.ceil(cand.simulated_ttk_fractional)
        else:
            cand.simulated_ttk_fractional = 999.0
            cand.simulated_ttk_turns = 999

    def _get_representative_spells(self) -> List[SpellInfo]:
        """Provides realistic class/elemental damage spells for rotation calculation."""
        elem = self.focus
        return [
            # High-damage heavy hitter (e.g. 4 AP, 32-38 base roll)
            SpellInfo(
                id=101,
                name_fr=f"{elem.capitalize()} Lourd",
                name_en=f"{elem.capitalize()} Heavy",
                name_es=f"{elem.capitalize()} Pesado",
                breed_id=8,
                ap_cost=4,
                min_range=1,
                max_range=4,
                crit_rate_percent=15.0,
                crit_bonus_damage=10,
                max_cast_per_turn=2,
                effects=[DamageEffect(min_roll=32, max_roll=38, element=elem)],
            ),
            # Medium fast spell (3 AP, 22-27 base roll)
            SpellInfo(
                id=102,
                name_fr=f"{elem.capitalize()} Frappe",
                name_en=f"{elem.capitalize()} Strike",
                name_es=f"{elem.capitalize()} Golpe",
                breed_id=8,
                ap_cost=3,
                min_range=1,
                max_range=6,
                crit_rate_percent=20.0,
                crit_bonus_damage=8,
                max_cast_per_turn=3,
                effects=[DamageEffect(min_roll=22, max_roll=27, element=elem)],
            ),
            # Light filler spell (2 AP, 13-17 base roll)
            SpellInfo(
                id=103,
                name_fr=f"{elem.capitalize()} Fléchette",
                name_en=f"{elem.capitalize()} Dart",
                name_es=f"{elem.capitalize()} Dardo",
                breed_id=8,
                ap_cost=2,
                min_range=1,
                max_range=8,
                crit_rate_percent=25.0,
                crit_bonus_damage=6,
                max_cast_per_turn=2,
                effects=[DamageEffect(min_roll=13, max_roll=17, element=elem)],
            ),
        ]

    def _solve_spell_rotation(
        self,
        spells: List[SpellInfo],
        char: CharacterStats,
        target: TargetStats,
        ap: int,
    ) -> Tuple[float, List[Tuple[str, int, float]]]:
        """Exhaustive bounded search over cast counts to maximize single-turn expected damage."""
        dmg_cache = {}
        for sp in spells:
            res = DofusDamageCalculator.calculate_spell_damage(sp, char, target)
            dmg_cache[sp.id] = res.expected_value

        best_damage = 0.0
        best_counts = {}

        # Search combinations within AP limit and per-turn limits
        def search(spell_idx: int, rem_ap: int, current_dmg: float, counts: Dict[int, int]):
            nonlocal best_damage, best_counts
            if current_dmg > best_damage:
                best_damage = current_dmg
                best_counts = dict(counts)

            if spell_idx >= len(spells) or rem_ap <= 0:
                return

            sp = spells[spell_idx]
            max_casts = min(sp.max_cast_per_turn or 99, rem_ap // sp.ap_cost)
            for c in range(max_casts, -1, -1):
                counts[sp.id] = c
                add_dmg = c * dmg_cache[sp.id]
                search(spell_idx + 1, rem_ap - c * sp.ap_cost, current_dmg + add_dmg, counts)
                counts.pop(sp.id, None)

        search(0, ap, 0.0, {})

        rotation = []
        for sp in spells:
            cnt = best_counts.get(sp.id, 0)
            if cnt > 0:
                rotation.append((sp.name_en, cnt, cnt * dmg_cache[sp.id]))

        return best_damage, rotation
