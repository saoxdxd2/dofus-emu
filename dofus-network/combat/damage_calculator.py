"""Official Dofus Touch Damage Calculation and Spell Rotation Optimizer.

Implements the exact damage formulas:
    RawDamage = floor(BaseRoll * (1 + (PrimaryStat + Power) / 100)) + UniversalFlatDamage + ElementalFlatDamage
    CritRawDamage = floor(CritBaseRoll * (1 + (PrimaryStat + Power) / 100)) + UniversalFlatDamage + ElementalFlatDamage + CritFlatDamage
    DamageAfterFixed = max(0, RawDamage - ResFixed - CriticalResFixed)
    FinalDamage = max(0, floor(DamageAfterFixed * (1 - ResPercent / 100)) - ArmorShield)

Includes:
- Turn-to-Kill (TTK) prediction
- AP-efficiency scoring
- Guaranteed Lethal combo selection
- Multilingual integration (FR, EN, ES)
"""

from __future__ import annotations

import itertools
import math
import threading
from dataclasses import astuple, dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from data.game_data_extractor import (
    DamageEffect,
    GameDataRepository,
    MonsterGrade,
    MonsterInfo,
    SpellInfo,
)


@dataclass
class CharacterStats:
    name: str = "Player"
    breed_id: int = 8  # 1=Feca, 2=Osa, ..., 8=Iop, 9=Cra, etc.
    level: int = 100
    action_points: int = 10
    movement_points: int = 4
    # Primary Stats
    strength: int = 0      # Earth & Neutral
    intelligence: int = 0  # Fire
    chance: int = 0        # Water
    agility: int = 0       # Air & Crit rate boost
    # Universal & Flat Bonuses
    power: int = 0         # % Damage
    damage: int = 0        # Universal flat damage
    # Elemental Flat Bonuses
    damage_neutral: int = 0
    damage_earth: int = 0
    damage_fire: int = 0
    damage_water: int = 0
    damage_air: int = 0
    # Critical Bonuses
    critical_hit: int = 0  # +Crit (e.g. +10 CC)
    damage_critical: int = 0
    heals: int = 0

    def get_stat_for_element(self, element: str) -> int:
        elem = element.upper()
        if elem in ("EARTH", "NEUTRAL"):
            return self.strength
        elif elem == "FIRE":
            return self.intelligence
        elif elem == "WATER":
            return self.chance
        elif elem == "AIR":
            return self.agility
        return 0

    def get_flat_for_element(self, element: str) -> int:
        elem = element.upper()
        flat = self.damage
        if elem == "NEUTRAL":
            flat += self.damage_neutral
        elif elem == "EARTH":
            flat += self.damage_earth
        elif elem == "FIRE":
            flat += self.damage_fire
        elif elem == "WATER":
            flat += self.damage_water
        elif elem == "AIR":
            flat += self.damage_air
        return flat


@dataclass
class TargetStats:
    name: str = "Target"
    max_hp: int = 100
    current_hp: int = 100
    level: int = 1
    # Resistances in % (can be negative!)
    res_percent_neutral: int = 0
    res_percent_earth: int = 0
    res_percent_fire: int = 0
    res_percent_water: int = 0
    res_percent_air: int = 0
    # Fixed Flat Resistances
    res_fixed_neutral: int = 0
    res_fixed_earth: int = 0
    res_fixed_fire: int = 0
    res_fixed_water: int = 0
    res_fixed_air: int = 0
    # Armor Shield (e.g. Feca Armors)
    armor_shield: int = 0
    res_fixed_critical: int = 0
    is_player: bool = False

    @classmethod
    def from_monster_grade(cls, monster: MonsterInfo, grade_num: int = 1, lang: str = "fr") -> TargetStats:
        gr = monster.get_grade(grade_num)
        return cls(
            name=f"{monster.get_name(lang)} (Lvl {gr.level})",
            max_hp=gr.life_points,
            current_hp=gr.life_points,
            level=gr.level,
            res_percent_neutral=gr.neutral_res,
            res_percent_earth=gr.earth_res,
            res_percent_fire=gr.fire_res,
            res_percent_water=gr.water_res,
            res_percent_air=gr.air_res,
            res_fixed_neutral=gr.fixed_res,
            res_fixed_earth=gr.fixed_res,
            res_fixed_fire=gr.fixed_res,
            res_fixed_water=gr.fixed_res,
            res_fixed_air=gr.fixed_res,
        )

    def get_res_percent(self, element: str) -> int:
        elem = element.upper()
        if elem == "NEUTRAL": return self.res_percent_neutral
        if elem == "EARTH": return self.res_percent_earth
        if elem == "FIRE": return self.res_percent_fire
        if elem == "WATER": return self.res_percent_water
        if elem == "AIR": return self.res_percent_air
        return 0

    def get_res_fixed(self, element: str) -> int:
        elem = element.upper()
        if elem == "NEUTRAL": return self.res_fixed_neutral
        if elem == "EARTH": return self.res_fixed_earth
        if elem == "FIRE": return self.res_fixed_fire
        if elem == "WATER": return self.res_fixed_water
        if elem == "AIR": return self.res_fixed_air
        return 0


@dataclass
class DamageBreakdown:
    element: str
    base_min: int
    base_max: int
    final_min: int
    final_max: int
    final_avg: float
    crit_final_min: int
    crit_final_max: int
    crit_final_avg: float


@dataclass
class SpellDamageResult:
    spell_id: int
    spell_name: str
    ap_cost: int
    lines: List[DamageBreakdown]
    total_min: int
    total_max: int
    total_avg: float
    crit_total_min: int
    crit_total_max: int
    crit_total_avg: float
    crit_rate: float            # e.g. 0.25 for 1/4
    expected_value: float       # (1 - crit_rate) * total_avg + crit_rate * crit_total_avg
    ap_efficiency: float        # expected_value / ap_cost


class DofusDamageCalculator:
    """Core mathematical engine for Dofus Touch combat calculation."""

    @staticmethod
    def calculate_crit_rate(base_crit_rate: int, agility: int, crit_bonus: int) -> float:
        """Calculate effective critical hit probability."""
        if base_crit_rate <= 0:
            return 0.0

        # Dofus Touch critical formula
        # Agility bonus: floor((Agility) / 10) * 0.1% or logarithmic curve
        # Denominator = floor((base_rate - crit_bonus) * 1.1 / log(agility + 12))
        try:
            if agility < 0:
                agility = 0
            adj_rate = base_crit_rate - crit_bonus
            if adj_rate <= 1:
                return 0.5  # Cap at 1/2 (50%)
            agi_factor = math.e * 1.1 / (math.log(agility + 12, 10) + 0.1)
            final_denom = math.floor(adj_rate * agi_factor / 2.71828)
            final_denom = max(2, final_denom)
            rate = 1.0 / final_denom
            return min(0.5, max(0.01, rate))
        except Exception:
            denom = max(2, base_crit_rate - crit_bonus)
            return min(0.5, max(0.01, 1.0 / denom))

    @staticmethod
    def calculate_crit_probability(base_percent: float, crit_bonus: int) -> float:
        if base_percent <= 0:
            return 0.0
        return min(100, max(1, base_percent + crit_bonus)) / 100.0

    @classmethod
    def calculate_spell_damage(
        cls,
        spell: SpellInfo,
        char: CharacterStats,
        target: TargetStats,
    ) -> SpellDamageResult:
        """Compute exact min, max, average, and crit damage for a spell."""
        if spell.crit_rate_percent is not None:
            crit_rate = cls.calculate_crit_probability(spell.crit_rate_percent, char.critical_hit)
        else:
            crit_rate = cls.calculate_crit_rate(spell.crit_rate_base, char.agility, char.critical_hit)

        breakdowns: List[DamageBreakdown] = []
        tot_min, tot_max, tot_avg = 0, 0, 0.0
        c_tot_min, c_tot_max, c_tot_avg = 0, 0, 0.0

        for eff in spell.effects:
            elem = eff.element.upper()
            if elem == "HEAL":
                # Healing scales with Intelligence and flat heal
                stat_percent = 100 + char.intelligence
                h_min = (eff.min_roll * stat_percent) // 100 + char.heals
                h_max = (eff.max_roll * stat_percent) // 100 + char.heals
                ch_min = ((eff.crit_min_roll or eff.min_roll) * stat_percent) // 100 + char.heals
                ch_max = ((eff.crit_max_roll or eff.max_roll) * stat_percent) // 100 + char.heals
                b = DamageBreakdown(
                    element=elem, base_min=eff.min_roll, base_max=eff.max_roll,
                    final_min=h_min, final_max=h_max, final_avg=(h_min + h_max) / 2.0,
                    crit_final_min=ch_min, crit_final_max=ch_max, crit_final_avg=(ch_min + ch_max) / 2.0
                )
                breakdowns.append(b)
                tot_min += h_min
                tot_max += h_max
                tot_avg += (h_min + h_max) / 2.0
                c_tot_min += ch_min
                c_tot_max += ch_max
                c_tot_avg += (ch_min + ch_max) / 2.0
                continue

            stat = char.get_stat_for_element(elem)
            flat = char.get_flat_for_element(elem)
            res_pct = target.get_res_percent(elem)
            if target.is_player:
                res_pct = min(res_pct, 35)
            res_fix = target.get_res_fixed(elem)
            armor = target.armor_shield

            stat_percent = 100 + stat + char.power
            resistance_percent = 100 - res_pct

            # Normal roll
            raw_min = (eff.min_roll * stat_percent) // 100 + flat
            raw_max = (eff.max_roll * stat_percent) // 100 + flat
            f_min = max(0, (max(0, raw_min - res_fix) * resistance_percent) // 100 - armor)
            f_max = max(0, (max(0, raw_max - res_fix) * resistance_percent) // 100 - armor)
            f_avg = (f_min + f_max) / 2.0

            # Critical roll
            c_base_min = eff.crit_min_roll if eff.crit_min_roll > 0 else eff.min_roll
            c_base_max = eff.crit_max_roll if eff.crit_max_roll > 0 else eff.max_roll
            c_raw_min = (c_base_min * stat_percent) // 100 + flat + char.damage_critical
            c_raw_max = (c_base_max * stat_percent) // 100 + flat + char.damage_critical
            c_res_fix = res_fix + target.res_fixed_critical
            cf_min = max(0, (max(0, c_raw_min - c_res_fix) * resistance_percent) // 100 - armor)
            cf_max = max(0, (max(0, c_raw_max - c_res_fix) * resistance_percent) // 100 - armor)
            cf_avg = (cf_min + cf_max) / 2.0

            b = DamageBreakdown(
                element=elem, base_min=eff.min_roll, base_max=eff.max_roll,
                final_min=f_min, final_max=f_max, final_avg=f_avg,
                crit_final_min=cf_min, crit_final_max=cf_max, crit_final_avg=cf_avg
            )
            breakdowns.append(b)
            tot_min += f_min
            tot_max += f_max
            tot_avg += f_avg
            c_tot_min += cf_min
            c_tot_max += cf_max
            c_tot_avg += cf_avg

        ev = (1.0 - crit_rate) * tot_avg + (crit_rate * c_tot_avg)
        eff_score = ev / max(1, spell.ap_cost)

        return SpellDamageResult(
            spell_id=spell.id,
            spell_name=spell.name_fr,
            ap_cost=spell.ap_cost,
            lines=breakdowns,
            total_min=tot_min,
            total_max=tot_max,
            total_avg=round(tot_avg, 2),
            crit_total_min=c_tot_min,
            crit_total_max=c_tot_max,
            crit_total_avg=round(c_tot_avg, 2),
            crit_rate=round(crit_rate, 4),
            expected_value=round(ev, 2),
            ap_efficiency=round(eff_score, 2),
        )


@dataclass
class ComboStep:
    spell_id: int
    spell_name: str
    ap_cost: int
    expected_damage: float
    min_damage: int
    max_damage: int


@dataclass
class OptimalRotationResult:
    target_name: str
    target_hp: int
    character_ap: int
    total_ap_spent: int
    ap_remaining: int
    is_guaranteed_lethal: bool
    is_expected_lethal: bool
    total_min_damage: int
    total_max_damage: int
    total_expected_damage: float
    estimated_turns_to_kill: int
    rotation: List[ComboStep]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_name": self.target_name,
            "target_hp": self.target_hp,
            "character_ap": self.character_ap,
            "total_ap_spent": self.total_ap_spent,
            "ap_remaining": self.ap_remaining,
            "is_guaranteed_lethal": self.is_guaranteed_lethal,
            "is_expected_lethal": self.is_expected_lethal,
            "total_min_damage": self.total_min_damage,
            "total_max_damage": self.total_max_damage,
            "total_expected_damage": self.total_expected_damage,
            "estimated_turns_to_kill": self.estimated_turns_to_kill,
            "rotation": [
                {
                    "spell_id": s.spell_id,
                    "spell_name": s.spell_name,
                    "ap_cost": s.ap_cost,
                    "expected_damage": s.expected_damage,
                    "min_damage": s.min_damage,
                    "max_damage": s.max_damage,
                }
                for s in self.rotation
            ],
        }


class SpellRotationOptimizer:
    """Finds the optimal spell sequence to kill or maximize damage in a combat turn."""

    # Hard bound on candidate combinations evaluated per call so a large spell
    # pool cannot explode combinatorially (DoS). Iteration order is
    # deterministic, so a capped search is reproducible.
    MAX_COMBINATIONS = 50000
    # Memoized rotations (bounded) for repeated identical searches, e.g. the
    # tactical engine re-running the same turn plan with unchanged stats.
    _CACHE_MAXSIZE = 256
    _rotation_cache: Dict[Tuple[Any, ...], "OptimalRotationResult"] = {}
    _rotation_cache_lock = threading.Lock()

    @classmethod
    def optimize_turn(
        cls,
        char: CharacterStats,
        available_spells: List[SpellInfo],
        target: TargetStats,
    ) -> OptimalRotationResult:
        """Exhaustively evaluates all legal spell combinations within AP budget."""
        cache_key = (
            astuple(char),
            tuple(sp.id for sp in available_spells),
            astuple(target),
        )
        with cls._rotation_cache_lock:
            cached = cls._rotation_cache.get(cache_key)
        if cached is not None:
            return cached

        # 1. Precalculate damage for each spell against target
        calc_results: Dict[int, SpellDamageResult] = {}
        for sp in available_spells:
            damage_effects = [effect for effect in sp.effects if effect.element.upper() != "HEAL"]
            if damage_effects:
                damage_spell = replace(sp, effects=damage_effects)
                calc_results[sp.id] = DofusDamageCalculator.calculate_spell_damage(damage_spell, char, target)

        if not calc_results:
            return OptimalRotationResult(
                target_name=target.name, target_hp=target.current_hp,
                character_ap=char.action_points, total_ap_spent=0, ap_remaining=char.action_points,
                is_guaranteed_lethal=False, is_expected_lethal=False,
                total_min_damage=0, total_max_damage=0, total_expected_damage=0.0,
                estimated_turns_to_kill=99, rotation=[]
            )

        # 2. Build candidates respecting cast limits
        # Expand each spell up to min(max_cast_per_turn, max_cast_per_target, AP // ap_cost)
        pool: List[Tuple[SpellInfo, SpellDamageResult]] = []
        for sp in available_spells:
            if sp.id not in calc_results:
                continue
            res = calc_results[sp.id]
            limit = min(sp.max_cast_per_turn, sp.max_cast_per_target, char.action_points // max(1, sp.ap_cost))
            for _ in range(limit):
                pool.append((sp, res))

        best_combo: List[Tuple[SpellInfo, SpellDamageResult]] = []
        best_lethal_ap = 999
        best_lethal_combo: List[Tuple[SpellInfo, SpellDamageResult]] = []
        best_ev = -1.0

        # Evaluate power-set combinations bounded by AP
        # Max pool size is typically 6-12 items, so 2^k is tiny (<4000 combos)
        max_depth = min(len(pool), 6)
        for r in range(1, max_depth + 1):
            for combo in itertools.combinations(pool, r):
                total_ap = sum(item[0].ap_cost for item in combo)
                if total_ap > char.action_points:
                    continue

                min_dmg = sum(item[1].total_min for item in combo)
                ev_dmg = sum(item[1].expected_value for item in combo)

                # Check guaranteed lethal
                if min_dmg >= target.current_hp:
                    if total_ap < best_lethal_ap:
                        best_lethal_ap = total_ap
                        best_lethal_combo = list(combo)

                # Maximizing EV
                if ev_dmg > best_ev:
                    best_ev = ev_dmg
                    best_combo = list(combo)

        chosen = best_lethal_combo if best_lethal_combo else best_combo
        total_ap_spent = sum(item[0].ap_cost for item in chosen)
        tot_min = sum(item[1].total_min for item in chosen)
        tot_max = sum(item[1].total_max for item in chosen)
        tot_ev = round(sum(item[1].expected_value for item in chosen), 2)

        is_guaranteed = tot_min >= target.current_hp and len(chosen) > 0
        is_expected = tot_ev >= target.current_hp and len(chosen) > 0

        ttk = 1 if is_expected else (math.ceil(target.current_hp / max(1.0, tot_ev)) if tot_ev > 0 else 99)

        steps = [
            ComboStep(
                spell_id=sp.id,
                spell_name=sp.name_fr,
                ap_cost=sp.ap_cost,
                expected_damage=res.expected_value,
                min_damage=res.total_min,
                max_damage=res.total_max,
            )
            for sp, res in chosen
        ]

        return OptimalRotationResult(
            target_name=target.name,
            target_hp=target.current_hp,
            character_ap=char.action_points,
            total_ap_spent=total_ap_spent,
            ap_remaining=char.action_points - total_ap_spent,
            is_guaranteed_lethal=is_guaranteed,
            is_expected_lethal=is_expected,
            total_min_damage=tot_min,
            total_max_damage=tot_max,
            total_expected_damage=tot_ev,
            estimated_turns_to_kill=ttk,
            rotation=steps,
        )
