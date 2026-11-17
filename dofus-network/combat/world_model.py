"""Dofus Combat World Model & Latent Environment Simulator.

Implements model-based planning and lookahead rollouts:
1. Environment state representation s_t = (PlayerState, TargetState, ActiveBuffs)
2. Deterministic transition function s_{t+1} = WorldModel.step(s_t, action)
3. Multi-turn tree search / lookahead rollouts before taking action in the real client
4. Continuous reward evaluation:
       R = 15.0 * LethalKill + 1.2 * (Damage / AP) + 2.0 * BuffSynergyROI - 8.0 * LowHP - 0.2 * WastedAP

Stage II module 1 (G128 closed): mechanics bind on canonical effect ids through
`bind_spell_mechanics`, never on a spell's display name. The old French-name
regex is gone; a buff is a spell whose typed mechanics fields say so. State is
cloned with `dataclasses.replace` over immutable tuples -- no `copy.deepcopy`
anywhere in the transition path. The board-geometry half of the 2-ply evaluator
lives in `combat.battle_solver.BattleSolver.evaluate_two_ply`; this module is the
latent 1v1 transition it prices single casts with.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Dict, List, Tuple

from combat.damage_calculator import (
    CharacterStats,
    DofusDamageCalculator,
    TargetStats,
)
from data.game_data_extractor import DamageEffect, SpellInfo

_DAMAGE_ELEMENTS = {"NEUTRAL", "EARTH", "FIRE", "WATER", "AIR"}

# Stat deltas applied by each modelled buff: name -> (attribute, magnitude).
# Kept for the legacy named buffs; id-bound mechanics carry their own magnitude
# on the BuffEffect itself.
_BUFF_STAT_DELTAS: Dict[str, Tuple[str, int]] = {
    "power": ("power", 200),
    "divine": ("flat_damage", 20),
}

# Effect ids the solver's UTILITY_EFFECT_KINDS table does not carry, read
# verbatim from the client bundle's ACTION_* table (client/www/build/script.js):
#   1040 ACTION_CHARACTER_BOOST_SHIELD
#   1039 ACTION_CHARACTER_BOOST_SHIELD_BASED_ON_CASTER_LIFE
#    138 ACTION_CHARACTER_BOOST_DAMAGES_PERCENT   (power)
#    112 ACTION_CHARACTER_BOOST_DAMAGES           (flat damage)
#    776 ACTION_CHARACTER_BOOST_PERMANENT_DAMAGE_PERCENT (erosion)
#    414 ACTION_CHARACTER_BOOST_PUSH_DAMAGE
#    416 ACTION_CHARACTER_BOOST_PUSH_DAMAGE_REDUCTION
_MECHANIC_EFFECT_FIELDS: Dict[int, str] = {
    1040: "shield_flat",
    1039: "shield_level_scale",
    138: "power_bonus",
    112: "flat_damage_bonus",
    776: "erosion_pct",
    414: "push_damage_stat",
    416: "push_resist",
}

_NON_DAMAGE_IDS: frozenset = frozenset()


def _non_damage_ids() -> frozenset:
    """Effect ids that are mechanics, not damage, whatever element they carry."""
    global _NON_DAMAGE_IDS
    if not _NON_DAMAGE_IDS:
        from combat.battle_solver import UTILITY_EFFECT_KINDS
        _NON_DAMAGE_IDS = frozenset(UTILITY_EFFECT_KINDS) | frozenset(_MECHANIC_EFFECT_FIELDS)
    return _NON_DAMAGE_IDS


@lru_cache(maxsize=4096)
def _expected_damage(effects_sig: Tuple[Tuple[Any, ...], ...],
                     crit_base: int, crit_percent: Any,
                     level: int, strength: int, intelligence: int,
                     chance: int, agility: int, power: int, flat: int,
                     resists_sig: Tuple[Tuple[str, int], ...]) -> float:
    """Memoised pure damage price. The calculator is a function of exactly these
    inputs; a rollout that recasts the same spell from the same stats pays once."""
    spell = SpellInfo(
        id=0, name_fr="", name_en="", name_es="", breed_id=0,
        ap_cost=0, crit_rate_base=crit_base, crit_rate_percent=crit_percent,
        effects=[DamageEffect(*sig) for sig in effects_sig],
    )
    char = CharacterStats(level=level, action_points=0, strength=strength,
                          intelligence=intelligence, chance=chance,
                          agility=agility, power=power, damage=flat)
    target = TargetStats(name="", max_hp=1, current_hp=1,
                         res_percent_neutral=dict(resists_sig).get("neutral", 0),
                         res_percent_earth=dict(resists_sig).get("earth", 0),
                         res_percent_fire=dict(resists_sig).get("fire", 0),
                         res_percent_water=dict(resists_sig).get("water", 0),
                         res_percent_air=dict(resists_sig).get("air", 0))
    return float(DofusDamageCalculator.calculate_spell_damage(
        spell, char, target).expected_value)


def bind_spell_mechanics(spell: SpellInfo) -> SpellInfo:
    """Fill the typed mechanics fields from the spell's canonical effect ids.

    The id -> kind vocabulary is the client's own ACTION table, shared with
    `combat.battle_solver.UTILITY_EFFECT_KINDS` (imported lazily so this module
    stays cheap to import). A spell with no ids keeps whatever typed fields it
    was constructed with; nothing here reads the spell's name.
    """
    from combat.battle_solver import UTILITY_EFFECT_KINDS

    ap_delta = mp_delta = push = 0
    shield_flat = power_bonus = flat_bonus = 0
    shield_scale = erosion = 0.0
    push_stat = push_resist = 0
    is_summon = False
    for effect in spell.effects:
        eid = effect.effect_id
        if not eid:
            continue
        roll = int(round((max(0, effect.min_roll) + max(0, effect.max_roll)) / 2.0))
        kind = UTILITY_EFFECT_KINDS.get(eid)
        if kind == "ap_gain":
            ap_delta += roll
        elif kind == "ap_steal":
            ap_delta += roll
        elif kind == "ap_loss":
            ap_delta -= roll
        elif kind == "mp_gain":
            mp_delta += roll
        elif kind == "mp_loss":
            mp_delta -= roll
        elif kind == "push":
            push += roll
        elif kind == "pull":
            push -= roll
        elif kind == "summon":
            is_summon = True
        field_name = _MECHANIC_EFFECT_FIELDS.get(eid)
        if field_name == "shield_flat":
            shield_flat += roll
        elif field_name == "shield_level_scale":
            shield_scale += roll / 100.0
        elif field_name == "power_bonus":
            power_bonus += roll
        elif field_name == "flat_damage_bonus":
            flat_bonus += roll
        elif field_name == "erosion_pct":
            erosion += float(roll)
        elif field_name == "push_damage_stat":
            push_stat += roll
        elif field_name == "push_resist":
            push_resist += roll
    return replace(
        spell,
        ap_delta=spell.ap_delta + ap_delta,
        mp_delta=spell.mp_delta + mp_delta,
        push_cells=spell.push_cells + push,
        shield_flat=spell.shield_flat + shield_flat,
        shield_level_scale=spell.shield_level_scale + shield_scale,
        power_bonus=spell.power_bonus + power_bonus,
        flat_damage_bonus=spell.flat_damage_bonus + flat_bonus,
        erosion_pct=spell.erosion_pct + erosion,
        is_summon=spell.is_summon or is_summon,
        cooldown_turns=spell.cooldown_turns or spell.cooldown,
        casts_per_turn=spell.max_cast_per_turn,
        casts_per_target=spell.max_cast_per_target,
    )


@dataclass(frozen=True)
class BuffEffect:
    """One active buff: its name, turns left, and the stat it leans on."""

    name: str
    turns_left: int
    attr: str = ""
    magnitude: int = 0


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
    active_buffs: Tuple[BuffEffect, ...] = ()

    # Target
    target_name: str = "Enemy"
    target_hp: int = 200
    target_max_hp: int = 200
    target_resists: Dict[str, int] = field(default_factory=dict)  # element -> %
    distance_cells: int = 3

    # Stage II tactical state. Shields absorb before HP; erosion shrinks the
    # target's effective max HP; cooldowns is an immutable sorted tuple of
    # (spell_id, turns_left) so a clone is a tuple copy, not a deepcopy.
    char_shield: int = 0
    target_shield: int = 0
    target_ap: int = 6
    target_mp: int = 3
    target_erosion: float = 0.0
    cooldowns: Tuple[Tuple[int, int], ...] = ()
    # Pushback geometry: free cells behind the target along the push axis, and
    # the two stats the collision formula reads. 0 free cells = backed against
    # a wall/edge, so the full push distance converts to collision damage.
    push_free_cells: int = 0
    push_damage_stat: int = 0
    target_push_resist: int = 0

    @property
    def char_hp_percent(self) -> float:
        return max(0.0, min(1.0, self.char_hp / max(1, self.char_max_hp)))

    @property
    def target_hp_percent(self) -> float:
        return max(0.0, min(1.0, self.target_hp / max(1, self.target_max_hp)))

    @property
    def is_target_dead(self) -> bool:
        return self.target_hp <= 0

    @property
    def buff_names(self) -> Tuple[str, ...]:
        return tuple(b.name for b in self.active_buffs)

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


def pushback_damage(caster_level: int, push_damage_stat: int,
                    target_push_resist: int, remaining_cells: int) -> int:
    """The client's collision formula, for the cells a push could not travel.

    floor((level/2 + push_damage - push_resist + 32) * remaining / 4)
    """
    if remaining_cells <= 0:
        return 0
    base = caster_level / 2.0 + push_damage_stat - target_push_resist + 32.0
    return max(0, int(base * remaining_cells / 4.0))


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
    def _tick_buffs(buffs: Tuple[BuffEffect, ...]
                    ) -> Tuple[Tuple[BuffEffect, ...], Dict[str, int]]:
        """Age every buff one turn; collect the stat reverts of the expired ones."""
        kept: List[BuffEffect] = []
        reverts: Dict[str, int] = {}
        for buff in buffs:
            if buff.turns_left > 1:
                kept.append(replace(buff, turns_left=buff.turns_left - 1))
            elif buff.attr:
                reverts[buff.attr] = reverts.get(buff.attr, 0) + buff.magnitude
        return tuple(kept), reverts

    @staticmethod
    def _apply_buff(buffs: Tuple[BuffEffect, ...], name: str, duration: int,
                    attr: str = "", magnitude: int = 0
                    ) -> Tuple[Tuple[BuffEffect, ...], Dict[str, int]]:
        """Add a buff, reverting any still-active application first (no stacking)."""
        if not attr:
            legacy = _BUFF_STAT_DELTAS.get(name)
            if legacy is not None:
                attr, magnitude = legacy
        reverts: Dict[str, int] = {}
        kept: List[BuffEffect] = []
        for buff in buffs:
            if buff.name == name:
                if buff.attr:
                    reverts[buff.attr] = reverts.get(buff.attr, 0) + buff.magnitude
                continue
            kept.append(buff)
        kept.append(BuffEffect(name=name, turns_left=duration,
                               attr=attr, magnitude=magnitude))
        return tuple(kept), reverts

    @classmethod
    def step(cls, state: CombatState, spell: SpellInfo) -> StepResult:
        """Transitions state s_t -> s_{t+1} applying spell effects, buffs, or damage."""
        if state.action_points < spell.ap_cost:
            # Illegal action: AP penalty
            return StepResult(next_state=replace(state), damage_dealt=0.0,
                              reward=-5.0, is_terminal=False)
        if state.cooldowns and any(sid == spell.id for sid, _ in state.cooldowns):
            return StepResult(next_state=replace(state), damage_dealt=0.0,
                              reward=-5.0, is_terminal=False,
                              info={"reason": "cooldown"})

        changes: Dict[str, Any] = {"action_points": state.action_points - spell.ap_cost}
        buffs, reverts = cls._tick_buffs(state.active_buffs)
        if state.cooldowns:
            cooldowns = tuple(sorted(
                (sid, left - 1) for sid, left in state.cooldowns if left > 1))
        else:
            cooldowns = ()
        if spell.cooldown_turns > 0:
            # +1 because the next step ticks before it casts: a 2-turn cooldown
            # must still block two casts, not one.
            cooldowns = tuple(sorted(cooldowns + ((spell.id, spell.cooldown_turns + 1),)))
        changes["cooldowns"] = cooldowns

        non_damage = _non_damage_ids()
        damage_elements = [e for e in spell.effects
                           if e.element.upper() in _DAMAGE_ELEMENTS
                           and e.effect_id not in non_damage]
        is_heal = any(e.element == "HEAL" for e in spell.effects)
        has_self_buff = (spell.ap_delta > 0 or spell.mp_delta > 0
                         or spell.shield_flat > 0 or spell.shield_level_scale > 0.0
                         or spell.power_bonus > 0 or spell.flat_damage_bonus > 0)
        is_buff = not damage_elements and not is_heal and has_self_buff

        info: Dict[str, Any] = {}
        grants: Dict[str, int] = {}
        damage_dealt = 0.0
        target_hp = state.target_hp
        target_shield = state.target_shield
        target_erosion = state.target_erosion
        distance = state.distance_cells

        # 1. Buff handling -- typed fields only, no name matching.
        if is_buff:
            if spell.power_bonus > 0:
                buffs, rev = cls._apply_buff(buffs, "power", 2, "power", spell.power_bonus)
                for attr, mag in rev.items():
                    reverts[attr] = reverts.get(attr, 0) + mag
                grants["power"] = grants.get("power", 0) + spell.power_bonus
            if spell.flat_damage_bonus > 0:
                buffs, rev = cls._apply_buff(buffs, "divine", 3,
                                             "flat_damage", spell.flat_damage_bonus)
                for attr, mag in rev.items():
                    reverts[attr] = reverts.get(attr, 0) + mag
                grants["flat_damage"] = grants.get("flat_damage", 0) + spell.flat_damage_bonus
            if spell.shield_flat > 0 or spell.shield_level_scale > 0.0:
                gained = spell.shield_flat + int(state.char_max_hp * spell.shield_level_scale)
                changes["char_shield"] = state.char_shield + gained
                buffs, _ = cls._apply_buff(buffs, "shield", 2)
                info["shield_gained"] = gained
            if spell.ap_delta > 0:
                changes["action_points"] = changes["action_points"] + spell.ap_delta
            if spell.mp_delta > 0:
                changes["movement_points"] = state.movement_points + spell.mp_delta

        # 2. Healing handling
        elif is_heal:
            heal_amount = 120 + int(state.intelligence * 1.2)
            changes["char_hp"] = min(state.char_max_hp, state.char_hp + heal_amount)

        # 3. Direct damage handling
        elif damage_elements:
            effects_sig = tuple(
                (e.element, e.min_roll, e.max_roll, e.crit_min_roll,
                 e.crit_max_roll, e.effect_id) for e in spell.effects)
            damage_dealt = _expected_damage(
                effects_sig, spell.crit_rate_base, spell.crit_rate_percent,
                state.char_level, state.strength, state.intelligence,
                state.chance, state.agility,
                state.power - reverts.get("power", 0),
                state.flat_damage - reverts.get("flat_damage", 0),
                tuple(sorted(state.target_resists.items())))
            absorbed = min(target_shield, int(damage_dealt))
            target_shield -= absorbed
            target_hp = max(0, int(target_hp - (damage_dealt - absorbed)))
            if absorbed:
                info["shield_absorbed"] = absorbed

        # 4. Mechanics that ride along with any cast (drain, push, erosion, summon).
        if spell.ap_delta < 0:
            changes["target_ap"] = max(0, state.target_ap + spell.ap_delta)
        if spell.mp_delta < 0:
            changes["target_mp"] = max(0, state.target_mp + spell.mp_delta)
        if spell.erosion_pct > 0.0:
            target_erosion = min(100.0, target_erosion + spell.erosion_pct)
            eroded_max = int(state.target_max_hp * (1.0 - target_erosion / 100.0))
            target_hp = min(target_hp, max(0, eroded_max))
            info["erosion_pct"] = target_erosion
        if spell.push_cells != 0 and not state.is_target_dead:
            if spell.push_cells > 0:
                moved = min(spell.push_cells, max(0, state.push_free_cells))
                remaining = spell.push_cells - moved
                distance += moved
            else:
                want = -spell.push_cells
                moved = min(want, max(0, distance - 1))
                remaining = want - moved
                distance -= moved
            if remaining > 0:
                collision = pushback_damage(state.char_level, state.push_damage_stat,
                                            state.target_push_resist, remaining)
                absorbed = min(target_shield, collision)
                target_shield -= absorbed
                target_hp = max(0, target_hp - (collision - absorbed))
                damage_dealt += collision - absorbed
                info["pushback_damage"] = collision
            info["push_moved"] = moved
        if spell.is_summon:
            info["summon"] = True
            info["summon_hp_ratio"] = spell.summon_hp_ratio

        # Apply the accumulated buff stat reverts, then this step's grants.
        for attr, mag in reverts.items():
            changes[attr] = max(0, getattr(state, attr) - mag)
        for attr, mag in grants.items():
            changes[attr] = changes.get(attr, getattr(state, attr)) + mag
        changes["active_buffs"] = buffs
        changes["target_hp"] = target_hp
        changes["target_shield"] = target_shield
        changes["target_erosion"] = target_erosion
        changes["distance_cells"] = max(1, distance)

        next_s = replace(state, **changes)
        is_lethal = next_s.is_target_dead
        reward = cls.calculate_reward(state, next_s, damage_dealt, spell.ap_cost, is_lethal)

        return StepResult(
            next_state=next_s,
            damage_dealt=damage_dealt,
            reward=reward,
            is_terminal=is_lethal,
            info=info,
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
        curr = replace(initial_state)
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