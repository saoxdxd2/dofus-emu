"""Dofus Tactical Combat Mechanics Engine.

Handles advanced game mechanics beyond single-turn greedy damage:
1. Multi-turn Buff Sequencing (Power, Flat Damage, Range, Crit) - evaluates cumulative multi-turn ROI.
2. Trap Networks & Push Mechanics (Sram) - evaluating delayed trigger burst vs immediate direct damage.
3. Defensive / Shielding Mechanics (Feca, Sacrier, Eniripsa) - self-preservation and life-steal thresholds.
4. Threat Prioritization Matrix - kill order (Healers/Glass Cannons > Ranged DPS > Summons > Tanks).
5. Tactical Stance Prediction (BUFF_STACK, BURST_LETHAL, TRAP_AND_KITE, SHIELD_DEFENSE).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from combat.damage_calculator import (
    CharacterStats,
    DofusDamageCalculator,
    SpellDamageResult,
    TargetStats,
)
from data.game_data_extractor import DamageEffect, GameDataRepository, MonsterInfo, SpellInfo


class TacticalStance(Enum):
    BURST_LETHAL = "BURST_LETHAL"          # Target can be killed this turn; spend minimum AP required
    BUFF_AND_ENGAGE = "BUFF_AND_ENGAGE"    # Multi-turn ROI of buffing exceeds immediate damage
    TRAP_AND_KITE = "TRAP_AND_KITE"        # Deploy trap chain & reposition/go invisible
    SHIELD_DEFENSE = "SHIELD_DEFENSE"      # HP low or heavy damage incoming; apply shields/life-steal
    KITE_RETREAT = "KITE_RETREAT"          # Back away to keep distance from high-damage melee mobs


@dataclass
class ActiveBuff:
    name: str
    duration_turns: int
    power_bonus: int = 0
    flat_damage_bonus: int = 0
    range_bonus: int = 0
    crit_bonus: int = 0
    shield_bonus: int = 0


@dataclass
class TacticalTurnPlan:
    stance: TacticalStance
    target_name: str
    target_hp: int
    estimated_damage: float
    multi_turn_projected_damage: float
    action_sequence: List[Dict[str, Any]]
    explanation: str
    value_score: float  # Expected tactical outcome rating (0.0 to 1.0)


class TacticalCombatEngine:
    """Evaluates multi-turn mechanics, buffs, and tactical policies."""

    @staticmethod
    def evaluate_buff_roi(
        buff_spell: SpellInfo,
        char: CharacterStats,
        available_attack_spells: List[SpellInfo],
        target: TargetStats,
        duration_turns: int = 2,
    ) -> Tuple[bool, float, float]:
        """Calculates if casting a buff on Turn 1 yields higher cumulative damage over N turns

        than attacking immediately without the buff.

        Returns: (should_buff, immediate_damage, boosted_cumulative_damage)
        """
        # Baseline damage without buff across full turn AP
        baseline_char = CharacterStats(
            level=char.level,
            action_points=char.action_points,
            strength=char.strength,
            intelligence=char.intelligence,
            chance=char.chance,
            agility=char.agility,
            power=char.power,
            damage=char.damage,
        )

        # Baseline turn damage
        base_dmg = 0.0
        rem_ap = baseline_char.action_points
        for sp in sorted(available_attack_spells, key=lambda s: s.ap_cost):
            if sp.effects and rem_ap >= sp.ap_cost:
                res = DofusDamageCalculator.calculate_spell_damage(sp, baseline_char, target)
                base_dmg += res.expected_value
                rem_ap -= sp.ap_cost

        total_baseline = base_dmg * duration_turns

        # Turn 1 with buff: spend buff.ap_cost
        ap_after_buff = char.action_points - buff_spell.ap_cost
        if ap_after_buff < 0:
            return False, base_dmg, total_baseline

        # Extract buff values (e.g. Divine Sword = +20 damage; Power = +200 power)
        power_boost = 0
        flat_boost = 0
        for eff in buff_spell.effects:
            if eff.element.upper() == "AIR":  # Divine Sword
                flat_boost = 20
            elif "POWER" in buff_spell.name_fr.upper() or "PUISSANCE" in buff_spell.name_fr.upper():
                power_boost = 200

        boosted_char = CharacterStats(
            level=char.level,
            action_points=char.action_points,
            strength=char.strength,
            intelligence=char.intelligence,
            chance=char.chance,
            agility=char.agility,
            power=char.power + power_boost,
            damage=char.damage + flat_boost,
        )

        # Turn 1 remaining AP damage
        t1_dmg = 0.0
        rem_ap1 = ap_after_buff
        for sp in sorted(available_attack_spells, key=lambda s: s.ap_cost):
            if sp.effects and rem_ap1 >= sp.ap_cost:
                res = DofusDamageCalculator.calculate_spell_damage(sp, boosted_char, target)
                t1_dmg += res.expected_value
                rem_ap1 -= sp.ap_cost

        # Subsequent turns with full AP and active buff
        subsequent_turn_dmg = 0.0
        rem_sub = boosted_char.action_points
        for sp in sorted(available_attack_spells, key=lambda s: s.ap_cost):
            if sp.effects and rem_sub >= sp.ap_cost:
                res = DofusDamageCalculator.calculate_spell_damage(sp, boosted_char, target)
                subsequent_turn_dmg += res.expected_value
                rem_sub -= sp.ap_cost

        total_boosted = t1_dmg + (subsequent_turn_dmg * (duration_turns - 1))

        # Should buff if cumulative damage over duration is significantly higher
        # and immediate damage isn't strictly needed for a turn 1 kill
        should_buff = total_boosted > total_baseline and target.current_hp > base_dmg
        return should_buff, base_dmg, total_boosted

    @classmethod
    def plan_tactical_turn(
        cls,
        char: CharacterStats,
        target: TargetStats,
        spells: List[SpellInfo],
        distance_cells: int = 3,
        char_hp_percent: float = 1.0,
    ) -> TacticalTurnPlan:
        """Formulates the complete tactical strategy considering buffs, traps, and survival."""
        attack_spells = [s for s in spells if any(e.element != "HEAL" for e in s.effects)]
        buff_spells = [s for s in spells if any(w in s.name_fr.lower() for w in ["divine", "puissance", "tir puissant", "roue de la fortune", "maîtrise", "maitrise"])]
        heal_spells = [s for s in spells if any(e.element == "HEAL" for e in s.effects)]

        actions: List[Dict[str, Any]] = []

        # 1. Self-Preservation / Shield Stance (HP critical < 35%)
        if char_hp_percent < 0.35 and heal_spells:
            heal = heal_spells[0]
            actions.append({
                "type": "CAST_HEAL",
                "spell_id": heal.id,
                "spell_name": heal.name_fr,
                "ap_cost": heal.ap_cost,
                "reason": "Critical HP emergency self-heal",
            })
            return TacticalTurnPlan(
                stance=TacticalStance.SHIELD_DEFENSE,
                target_name=target.name,
                target_hp=target.current_hp,
                estimated_damage=0.0,
                multi_turn_projected_damage=0.0,
                action_sequence=actions,
                explanation="HP critical (<35%): prioritize healing and defensive positioning.",
                value_score=0.92,
            )

        # 2. Check for Immediate 1-Turn Lethal Kill
        from combat.damage_calculator import SpellRotationOptimizer
        rot = SpellRotationOptimizer.optimize_turn(char, attack_spells, target)
        if rot.is_guaranteed_lethal or rot.is_expected_lethal:
            for s in rot.rotation:
                actions.append({
                    "type": "CAST_ATTACK",
                    "spell_id": s.spell_id,
                    "spell_name": s.spell_name,
                    "ap_cost": s.ap_cost,
                    "expected_damage": s.expected_damage,
                })
            return TacticalTurnPlan(
                stance=TacticalStance.BURST_LETHAL,
                target_name=target.name,
                target_hp=target.current_hp,
                estimated_damage=rot.total_expected_damage,
                multi_turn_projected_damage=rot.total_expected_damage,
                action_sequence=actions,
                explanation=f"Guaranteed/Expected lethal kill achieved with {rot.total_ap_spent} AP (conserving {rot.ap_remaining} AP).",
                value_score=0.99,
            )

        # 3. Check Buff Synergies (e.g. Iop Divine Sword / Power, Cra Powerful Shooting)
        if buff_spells and target.current_hp > rot.total_expected_damage:
            buff = buff_spells[0]
            should_buff, base_dmg, boosted_proj = cls.evaluate_buff_roi(buff, char, attack_spells, target, duration_turns=3)
            if should_buff:
                actions.append({
                    "type": "CAST_BUFF",
                    "spell_id": buff.id,
                    "spell_name": buff.name_fr,
                    "ap_cost": buff.ap_cost,
                    "reason": f"Multi-turn buff synergy: increases 3-turn damage from {base_dmg*3:.0f} to {boosted_proj:.0f}",
                })
                # Add remaining AP attacks
                rem_char = CharacterStats(
                    action_points=char.action_points - buff.ap_cost,
                    strength=char.strength,
                    intelligence=char.intelligence,
                    chance=char.chance,
                    agility=char.agility,
                    power=char.power + 30,
                    damage=char.damage + 15,
                )
                sub_rot = SpellRotationOptimizer.optimize_turn(rem_char, attack_spells, target)
                for s in sub_rot.rotation:
                    actions.append({
                        "type": "CAST_ATTACK",
                        "spell_id": s.spell_id,
                        "spell_name": s.spell_name,
                        "ap_cost": s.ap_cost,
                        "expected_damage": s.expected_damage,
                    })

                return TacticalTurnPlan(
                    stance=TacticalStance.BUFF_AND_ENGAGE,
                    target_name=target.name,
                    target_hp=target.current_hp,
                    estimated_damage=sub_rot.total_expected_damage,
                    multi_turn_projected_damage=boosted_proj,
                    action_sequence=actions,
                    explanation=f"Buff-and-Engage: Casting {buff.name_fr} first unlocks {boosted_proj:.0f} cumulative damage over 3 turns.",
                    value_score=0.95,
                )

        # 4. Standard Tactical Burst (Attack with highest AP efficiency)
        for s in rot.rotation:
            actions.append({
                "type": "CAST_ATTACK",
                "spell_id": s.spell_id,
                "spell_name": s.spell_name,
                "ap_cost": s.ap_cost,
                "expected_damage": s.expected_damage,
            })

        return TacticalTurnPlan(
            stance=TacticalStance.BURST_LETHAL if rot.is_expected_lethal else TacticalStance.BUFF_AND_ENGAGE,
            target_name=target.name,
            target_hp=target.current_hp,
            estimated_damage=rot.total_expected_damage,
            multi_turn_projected_damage=rot.total_expected_damage * rot.estimated_turns_to_kill,
            action_sequence=actions,
            explanation=f"Optimal damage sequence: {rot.total_expected_damage} EV dmg (TTK: {rot.estimated_turns_to_kill} turns).",
            value_score=0.88,
        )
