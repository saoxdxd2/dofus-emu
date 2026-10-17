"""Dofus Combat World Model & Latent Environment Simulator.

Implements model-based planning and lookahead rollouts:
1. Environment state representation s_t = (PlayerState, TargetState, ActiveBuffs)
2. Deterministic transition function s_{t+1} = WorldModel.step(s_t, action)
3. Multi-turn tree search / lookahead rollouts before taking action in the real client
4. Continuous reward evaluation:
       R = 15.0 * LethalKill + 1.2 * (Damage / AP) + 2.0 * BuffSynergyROI - 8.0 * LowHP - 0.2 * WastedAP
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from combat.damage_calculator import (
    CharacterStats,
    DofusDamageCalculator,
    SpellDamageResult,
    TargetStats,
)
from data.game_data_extractor import SpellInfo

_DAMAGE_ELEMENTS = {"NEUTRAL", "EARTH", "FIRE", "WATER", "AIR"}
_BUFF_NAME_KEYWORDS = (
    "puissance",
    "tir puissant",
    "divine",
    "roue de la fortune",
    "bouclier",
    "châtiment",
)
_BUFF_NAME_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in _BUFF_NAME_KEYWORDS) + r")\b"
)

# Stat deltas applied by each modelled buff: name -> (attribute, magnitude).
_BUFF_STAT_DELTAS: Dict[str, Tuple[str, int]] = {
    "power": ("power", 200),
    "divine": ("flat_damage", 20),
}


@dataclass
class CombatState:
    # Character
    char_level: int
    char_hp: int
    char_max_hp: int
    action_points: int
    movement_points: int
    strength: int
    intelligence: int
    chance: int
    agility: int
    power: int
    flat_damage: int
    active_buffs: Dict[str, int] = field(default_factory=dict)  # buff_name -> remaining_turns

    # Target
    target_name: str = "Enemy"
    target_hp: int = 200
    target_max_hp: int = 200
    target_resists: Dict[str, int] = field(default_factory=dict)  # element -> %
    distance_cells: int = 3

    @property
    def char_hp_percent(self) -> float:
        return max(0.0, min(1.0, self.char_hp / max(1, self.char_max_hp)))

    @property
    def target_hp_percent(self) -> float:
        return max(0.0, min(1.0, self.target_hp / max(1, self.target_max_hp)))

    @property
    def is_target_dead(self) -> bool:
        return self.target_hp <= 0

    @classmethod
    def from_entities(
        cls,
        char: CharacterStats,
        target: TargetStats,
        char_hp: int = 1000,
        char_max_hp: int = 1000,
        distance: int = 3,
    ) -> CombatState:
        return cls(
            char_level=char.level,
            char_hp=char_hp,
            char_max_hp=char_max_hp,
            action_points=char.action_points,
            movement_points=char.movement_points,
            strength=char.strength,
            intelligence=char.intelligence,
            chance=char.chance,
            agility=char.agility,
            power=char.power,
            flat_damage=char.damage,
            target_name=target.name,
            target_hp=target.current_hp,
            target_max_hp=target.max_hp,
            target_resists={
                "neutral": target.res_percent_neutral,
                "earth": target.res_percent_earth,
                "fire": target.res_percent_fire,
                "water": target.res_percent_water,
                "air": target.res_percent_air,
            },
            distance_cells=distance,
        )


@dataclass
class StepResult:
    next_state: CombatState
    damage_dealt: float
    reward: float
    is_terminal: bool
    info: Dict[str, Any] = field(default_factory=dict)


class DofusWorldModel:
    """Predicts next state and rewards given an action before acting in the real environment."""

    @staticmethod
    def calculate_reward(
        prev_state: CombatState,
        next_state: CombatState,
        damage_dealt: float,
        ap_spent: int,
        is_lethal: bool,
    ) -> float:
        """Evaluates outcome quality using RL reward design."""
        reward = 0.0

        # 1. Huge bonus for lethal kill
        if is_lethal:
            reward += 15.0

        # 2. AP efficiency reward (damage per AP spent)
        if ap_spent > 0:
            reward += 1.2 * (damage_dealt / max(1, ap_spent))

        # 3. Penalize critical player HP risk
        if next_state.char_hp_percent < 0.30:
            reward -= 8.0

        # 4. Small penalty for wasting remaining AP when enemies are alive
        if not is_lethal and next_state.action_points > 2:
            reward -= 0.1 * next_state.action_points

        return reward

    @staticmethod
    def _tick_buffs(state: CombatState) -> None:
        """Decrement active buff durations and revert bonuses when they expire."""
        for name in list(state.active_buffs):
            remaining = state.active_buffs[name] - 1
            if remaining > 0:
                state.active_buffs[name] = remaining
                continue
            del state.active_buffs[name]
            delta = _BUFF_STAT_DELTAS.get(name)
            if delta is not None:
                attr, magnitude = delta
                setattr(state, attr, max(0, getattr(state, attr) - magnitude))

    @staticmethod
    def _apply_buff(
        state: CombatState,
        name: str,
        duration: int,
    ) -> None:
        """Apply a buff, reverting any still-active application first (no stacking)."""
        delta = _BUFF_STAT_DELTAS.get(name)
        if delta is not None and name in state.active_buffs:
            attr, magnitude = delta
            setattr(state, attr, max(0, getattr(state, attr) - magnitude))
        state.active_buffs[name] = duration
        if delta is not None:
            attr, magnitude = delta
            setattr(state, attr, getattr(state, attr) + magnitude)

    @classmethod
    def step(cls, state: CombatState, spell: SpellInfo) -> StepResult:
        """Transitions state s_t -> s_{t+1} applying spell effects, buffs, or damage."""
        if state.action_points < spell.ap_cost:
            # Illegal action: AP penalty
            return StepResult(next_state=copy.deepcopy(state), damage_dealt=0.0, reward=-5.0, is_terminal=False)

        next_s = copy.deepcopy(state)
        next_s.action_points -= spell.ap_cost
        cls._tick_buffs(next_s)

        damage_dealt = 0.0
        name_lower = spell.name_fr.lower()
        damage_elements = [e for e in spell.effects if e.element.upper() in _DAMAGE_ELEMENTS]
        is_heal = any(e.element == "HEAL" for e in spell.effects)
        # A spell that deals damage is never a buff, regardless of its name:
        # e.g. "Épée Divine" is an AIR damage spell, not a buff.
        is_buff = not damage_elements and bool(_BUFF_NAME_RE.search(name_lower)) and not is_heal

        # 1. Buff Handling
        if is_buff:
            if "puissance" in name_lower or "tir puissant" in name_lower:
                cls._apply_buff(next_s, "power", duration=2)
            elif re.search(r"\bdivine\b", name_lower):
                cls._apply_buff(next_s, "divine", duration=3)
            elif "bouclier" in name_lower:
                cls._apply_buff(next_s, "shield", duration=2)

        # 2. Healing Handling
        elif is_heal:
            heal_amount = 120 + int(next_s.intelligence * 1.2)
            next_s.char_hp = min(next_s.char_max_hp, next_s.char_hp + heal_amount)

        # 3. Direct Damage Handling
        else:
            char_stats = CharacterStats(
                level=next_s.char_level,
                action_points=next_s.action_points,
                strength=next_s.strength,
                intelligence=next_s.intelligence,
                chance=next_s.chance,
                agility=next_s.agility,
                power=next_s.power,
                damage=next_s.flat_damage,
            )
            target_stats = TargetStats(
                name=next_s.target_name,
                max_hp=next_s.target_max_hp,
                current_hp=next_s.target_hp,
                res_percent_neutral=next_s.target_resists.get("neutral", 0),
                res_percent_earth=next_s.target_resists.get("earth", 0),
                res_percent_fire=next_s.target_resists.get("fire", 0),
                res_percent_water=next_s.target_resists.get("water", 0),
                res_percent_air=next_s.target_resists.get("air", 0),
            )
            dmg_res = DofusDamageCalculator.calculate_spell_damage(spell, char_stats, target_stats)
            damage_dealt = dmg_res.expected_value
            next_s.target_hp = max(0, int(next_s.target_hp - damage_dealt))

        is_lethal = next_s.is_target_dead
        reward = cls.calculate_reward(state, next_s, damage_dealt, spell.ap_cost, is_lethal)

        return StepResult(
            next_state=next_s,
            damage_dealt=damage_dealt,
            reward=reward,
            is_terminal=is_lethal,
        )

    @classmethod
    def simulate_rollout(
        cls,
        initial_state: CombatState,
        action_sequence: List[SpellInfo],
    ) -> Tuple[CombatState, float, float]:
        """Simulates an entire turn trajectory in latent/model space.

        Returns: (final_state, total_damage, cumulative_reward)
        """
        curr = copy.deepcopy(initial_state)
        total_dmg = 0.0
        total_rew = 0.0

        for sp in action_sequence:
            if curr.action_points < sp.ap_cost or curr.is_target_dead:
                break
            res = cls.step(curr, sp)
            curr = res.next_state
            total_dmg += res.damage_dealt
            total_rew += res.reward
            if res.is_terminal:
                break

        return curr, total_dmg, total_rew
