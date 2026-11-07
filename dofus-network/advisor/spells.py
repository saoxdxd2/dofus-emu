"""Spell grading, rotation and point allocation.

The client exposes enough to answer the two questions that matter:

* what does each spell do at each grade the character can reach?
* which upgrades buy the most real per-turn value for the spell points on hand?

The damage numbers come from `combat.damage_calculator`, the same engine the
fight solver uses. Utility lines (AP/MP removal, range, shields, pushes) are
priced with a small, explicit heuristic because the official data has no
numeric model for them; the report labels that clearly.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from combat.damage_calculator import CharacterStats, DofusDamageCalculator, TargetStats
    from data.game_data_extractor import DamageEffect, SpellInfo
except ModuleNotFoundError:  # running from another working directory
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from combat.damage_calculator import CharacterStats, DofusDamageCalculator, TargetStats  # type: ignore
    from data.game_data_extractor import DamageEffect, SpellInfo  # type: ignore

from .models import (
    ELEMENTS,
    CharacterSnapshot,
    SpellState,
)
from .repository import (
    OfficialSpell,
    Repository,
    effect_roll,
)

HEAL_WEIGHT = 0.8
UTILITY_WEIGHT = 1.0
OFF_ELEMENT_WEIGHT = 0.25
RESPEC_PREFERENCE = 0.08
UNLIMITED = 1_000_000


# ---------------------------------------------------------------------------
# effect parsing


@dataclass
class SpellPower:
    damage: float
    raw_damage: float
    heal: float
    utility: float
    element: Optional[str]
    element_damage: Dict[str, float]
    damage_lines: int
    utility_lines: List[str] = field(default_factory=list)

    @property
    def dominant_element(self) -> Optional[str]:
        if not self.element_damage:
            return None
        return max(self.element_damage, key=lambda key: self.element_damage[key])


def _best_element_roll(raw: Dict[str, Any], focus: Optional[str]) -> Optional[Tuple[str, int, int]]:
    text = str(raw.get("text") or "")
    match = re.search(r"(\d+)\s+to\s+(\d+)\s+\(best element\)", text, re.I)
    if not match:
        return None
    return (focus if focus in ELEMENTS else "earth", int(match.group(1)), int(match.group(2)))


def _heal_roll(raw: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    heal = raw.get("heal")
    if isinstance(heal, dict):
        return int(heal.get("min_roll") or 0), int(heal.get("max_roll") or 0)
    text = str(raw.get("text") or "")
    match = re.search(r"(\d+)\s+to\s+(\d+)\s+\(HP restored\)", text, re.I)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def _utility_points(text: str, level: int, max_hp: float) -> float:
    """Price one non-damage effect line.

    These weights are deliberately small and capped. A report that says
    "this utility line is worth ~8 points" is honest about being a heuristic;
    hiding utility spells at zero would be worse, because AP/MP removal and
    range buffs are often the reason a spell is levelled at all.
    """
    lower = text.lower().strip()
    if not lower:
        return 0.0

    def number(pattern: str) -> Optional[float]:
        match = re.search(pattern, lower)
        return float(match.group(1)) if match else None

    if re.match(r"-\d+\s+ap\b", lower):
        return min(60.0, 8.0 * (number(r"-(\d+)\s+ap\b") or 0))
    if re.match(r"\d+\s+ap\b", lower):
        return min(60.0, 7.0 * (number(r"(\d+)\s+ap\b") or 0))
    if re.match(r"steals\s+\d+\s+mp\b", lower):
        return min(40.0, 4.0 * (number(r"steals\s+(\d+)\s+mp") or 0))
    if re.match(r"-\d+\s+mp\b", lower):
        return min(50.0, 5.0 * (number(r"-(\d+)\s+mp\b") or 0))
    if re.match(r"\d+\s+mp\b", lower):
        return min(50.0, 5.0 * (number(r"(\d+)\s+mp\b") or 0))
    if "range" in lower:
        value = number(r"(?:-)?(\d+)\s+range")
        return min(40.0, 6.0 * (value or 0))
    if "power" in lower:
        value = number(r"(\d+)\s+power")
        return min(40.0, 1.0 * (value or 0))
    if "vitality" in lower:
        value = number(r"(\d+)%\s+vitality")
        return min(40.0, 2.0 * (value or 0))
    if "shield" in lower:
        value = number(r"(\d+)%\s+of\s+level\s+to\s+shield")
        return min(80.0, 0.8 * (value or 0) * max(1, level) / 100.0)
    if "heal:" in lower or "heal " in lower:
        value = number(r"(\d+)%\s+of\s+max\s+hp")
        if value is not None and max_hp > 0:
            return min(200.0, 0.8 * value * max_hp / 100.0)
        return 4.0
    if "damage sustained" in lower:
        value = number(r"x(\d+)%\s+damage sustained")
        if value is None:
            return 0.0
        return max(-60.0, min(60.0, (100.0 - value)))
    if "spell damage inflicted" in lower:
        return min(60.0, number(r"(\d+)%\s+spell damage inflicted") or 0.0)
    if "erosion" in lower:
        return min(40.0, number(r"(\d+)%\s+erosion") or 0.0)
    if "pushes back" in lower or "attracts" in lower or "advances" in lower:
        return min(30.0, 1.5 * (number(r"(\d+)\s+cell") or 0))
    if "summons" in lower:
        return 12.0
    if "trap" in lower:
        return min(30.0, 3.0 * (number(r"grade\s+(\d+)") or 1.0))
    if "kills the target" in lower:
        return 20.0
    if "dodge" in lower or "lock" in lower:
        return min(20.0, 0.5 * (number(r"(\d+)\s+(?:dodge|lock)") or 0))
    if "critical hits" in lower:
        return min(20.0, number(r"(\d+)%\s+critical hits") or 0.0)
    if "reduces damage" in lower:
        return min(30.0, 0.05 * (number(r"by\s+(\d+)") or 0))
    if "resistance" in lower or "resist" in lower:
        return min(30.0, 0.5 * (number(r"(\d+)") or 0))
    if "damage" in lower and "inflicted" in lower:
        return min(30.0, number(r"(\d+)") or 0.0)
    if "state" in lower or "removes" in lower:
        return 0.5
    if "teleport" in lower or "switches positions" in lower:
        return 2.0
    return 0.25


def _is_steal_roll(raw: Dict[str, Any]) -> bool:
    text = str(raw.get("text") or "")
    return bool(re.search(r"\((?:Earth|Fire|Water|Air|Neutral) steal\)", text, re.I))


def parse_spell_power(
    spell: OfficialSpell,
    grade: int,
    stats: CharacterStats,
    focus: Optional[str],
    max_hp: float,
    off_element_weight: float = OFF_ELEMENT_WEIGHT,
) -> SpellPower:
    row = spell.grade(grade)
    if row is None:
        return SpellPower(0.0, 0.0, 0.0, 0.0, None, {}, 0)

    damage_effects: List[DamageEffect] = []
    steal_flags: List[bool] = []
    crit_rows = list(row.critical_effects or [])
    normal_rows = list(row.effects or [])
    # Normal and critical effect rows are aligned by position in the official
    # data (`spell_level_to_info` relies on that too). Match by index, not by
    # damage-only order: a utility line before a damage line would otherwise
    # shift every critical roll by one.
    for index, raw in enumerate(normal_rows):
        if not isinstance(raw, dict):
            continue
        run = effect_roll(raw) or _best_element_roll(raw, focus)
        if run is None:
            continue
        element, low, high = run
        crit_low, crit_high = 0, 0
        if index < len(crit_rows) and isinstance(crit_rows[index], dict):
            crit_run = (effect_roll(crit_rows[index])
                        or _best_element_roll(crit_rows[index], focus))
            if crit_run and crit_run[0] == element:
                crit_low, crit_high = crit_run[1], crit_run[2]
        steal = _is_steal_roll(raw)
        if steal:
            # The encyclopedia lists a lifesteal hit twice: "N to M (Fire
            # damage)" and "N to M (Fire steal)". That is one hit that also
            # heals, not two hits. Fold the steal line into the matching
            # damage line; a steal with no twin stays as its own hit.
            twin = next((i for i, effect in enumerate(damage_effects)
                         if not steal_flags[i]
                         and effect.element.lower() == element
                         and effect.min_roll == low
                         and effect.max_roll == high), None)
            if twin is not None:
                steal_flags[twin] = True
                continue
        damage_effects.append(DamageEffect(
            element=element.upper(),
            min_roll=low,
            max_roll=high,
            crit_min_roll=crit_low,
            crit_max_roll=crit_high,
        ))
        steal_flags.append(steal)

    for index, raw in enumerate(normal_rows):
        if not isinstance(raw, dict):
            continue
        heal = _heal_roll(raw)
        if heal is None:
            continue
        crit_row = crit_rows[index] if index < len(crit_rows) else None
        crit_heal = _heal_roll(crit_row) if isinstance(crit_row, dict) else None
        damage_effects.append(DamageEffect(
            element="HEAL",
            min_roll=heal[0],
            max_roll=heal[1],
            crit_min_roll=crit_heal[0] if crit_heal else 0,
            crit_max_roll=crit_heal[1] if crit_heal else 0,
        ))
        steal_flags.append(False)

    utility = 0.0
    utility_lines: List[str] = []
    for raw in row.effects or []:
        if not isinstance(raw, dict):
            continue
        if effect_roll(raw) or _best_element_roll(raw, focus) or _heal_roll(raw):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        points = _utility_points(text, stats.level, max_hp)
        if points:
            utility += points
            utility_lines.append(text)

    fixed_heal = 0.0
    for line in utility_lines:
        match = re.search(r"(\d+)%\s+of\s+max\s+hp", line, re.I)
        if match and "heal" in line.lower() and max_hp > 0:
            fixed_heal += max_hp * float(match.group(1)) / 100.0

    if not damage_effects:
        return SpellPower(0.0, 0.0, fixed_heal, utility, None, {}, 0, utility_lines)

    base = spell.info(grade)
    info = SpellInfo(
        id=base.id,
        name_fr=spell.name,
        name_en=spell.name,
        name_es="",
        breed_id=base.breed_id,
        level=base.level,
        ap_cost=base.ap_cost,
        min_range=base.min_range,
        max_range=base.max_range,
        cast_in_line=base.cast_in_line,
        cast_in_diagonal=base.cast_in_diagonal,
        cast_test_los=base.cast_test_los,
        max_cast_per_turn=base.max_cast_per_turn,
        max_cast_per_target=base.max_cast_per_target,
        cooldown=base.cooldown,
        crit_rate_base=base.crit_rate_base,
        crit_bonus_damage=base.crit_bonus_damage,
        effects=damage_effects,
        crit_rate_percent=base.crit_rate_percent,
    )
    target = TargetStats(name="advisor dummy", max_hp=10_000, current_hp=10_000, level=100)
    result = DofusDamageCalculator.calculate_spell_damage(info, stats, target)
    crit_rate = result.crit_rate
    damage = 0.0
    heal = fixed_heal
    element_damage: Dict[str, float] = {}
    for index, line in enumerate(result.lines):
        expected = (1.0 - crit_rate) * line.final_avg + crit_rate * line.crit_final_avg
        if line.element.upper() == "HEAL":
            heal += expected
            continue
        if index < len(steal_flags) and steal_flags[index]:
            # A lifesteal hit heals for the damage it deals, scaled by the
            # element's stat, not by Intelligence. Add it from the damage line
            # the calculator just produced.
            heal += expected
        element = line.element.lower()
        if element == "neutral":
            element = "earth"
        weight = 1.0
        if focus in ELEMENTS and element != focus:
            weight = max(0.0, min(1.0, off_element_weight))
        element_damage[element] = element_damage.get(element, 0.0) + expected * weight
        damage += expected * weight

    dominant = max(element_damage, key=lambda key: element_damage[key]) if element_damage else None
    raw_damage = sum((1.0 - crit_rate) * line.final_avg + crit_rate * line.crit_final_avg
                     for line in result.lines if line.element.upper() != "HEAL")
    return SpellPower(
        damage=damage,
        raw_damage=raw_damage,
        heal=heal,
        utility=utility,
        element=dominant,
        element_damage=element_damage,
        damage_lines=len(damage_effects),
        utility_lines=utility_lines,
    )


# ---------------------------------------------------------------------------
# rotation


@dataclass
class RotationCandidate:
    spell_id: int
    name: str
    grade: int
    element: Optional[str]
    ap_cost: int
    max_casts: int
    cooldown: int
    damage: float
    heal: float
    utility: float
    value: float

    @property
    def efficiency(self) -> float:
        return self.value / max(1, self.ap_cost)


@dataclass
class RotationCast:
    spell_id: int
    name: str
    grade: int
    element: Optional[str]
    casts: int = 0
    ap: int = 0
    damage: float = 0.0
    heal: float = 0.0
    utility: float = 0.0
    value: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spell_id": self.spell_id,
            "name": self.name,
            "grade": self.grade,
            "element": self.element,
            "casts": self.casts,
            "ap": self.ap,
            "damage": round(self.damage, 2),
            "heal": round(self.heal, 2),
            "utility": round(self.utility, 2),
            "value": round(self.value, 2),
        }


@dataclass
class RotationResult:
    ap_budget: int
    ap_used: int
    casts: List[RotationCast]
    damage: float
    heal: float
    utility: float
    value: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ap_budget": self.ap_budget,
            "ap_used": self.ap_used,
            "damage": round(self.damage, 2),
            "heal": round(self.heal, 2),
            "utility": round(self.utility, 2),
            "value": round(self.value, 2),
            "casts": [cast.to_dict() for cast in self.casts],
        }


def greedy_rotation(candidates: Sequence[RotationCandidate], ap_budget: int) -> RotationResult:
    """Fill a turn with the best value per AP, respecting per-turn caps.

    One cast at a time, not one spell at a time: a 3 AP spell and a 4 AP spell
    can share a 10 AP turn, which is how the game is actually played. Cooldown
    spells are limited to one cast because the rotation covers one turn.
    """
    remaining = max(0, int(ap_budget))
    casts_left = {index: max(0, int(candidate.max_casts))
                  for index, candidate in enumerate(candidates)}
    by_spell: Dict[int, RotationCast] = {}
    order: List[int] = []
    total_damage = total_heal = total_utility = total_value = 0.0
    ap_used = 0

    for _ in range(200):
        best_index = -1
        best_key = None
        for index, candidate in enumerate(candidates):
            if casts_left[index] <= 0:
                continue
            ap = max(0, candidate.ap_cost)
            if ap > remaining:
                continue
            efficiency = candidate.value / max(1, ap)
            key = (efficiency, candidate.value, -ap, -candidate.spell_id)
            if best_key is None or key > best_key:
                best_key = key
                best_index = index
        if best_index < 0:
            break
        candidate = candidates[best_index]
        ap = max(0, candidate.ap_cost)
        remaining -= ap
        ap_used += ap
        casts_left[best_index] -= 1
        if candidate.spell_id not in by_spell:
            by_spell[candidate.spell_id] = RotationCast(
                spell_id=candidate.spell_id,
                name=candidate.name,
                grade=candidate.grade,
                element=candidate.element,
            )
            order.append(candidate.spell_id)
        row = by_spell[candidate.spell_id]
        row.casts += 1
        row.ap += ap
        row.damage += candidate.damage
        row.heal += candidate.heal
        row.utility += candidate.utility
        row.value += candidate.value
        total_damage += candidate.damage
        total_heal += candidate.heal
        total_utility += candidate.utility
        total_value += candidate.value

    return RotationResult(
        ap_budget=max(0, int(ap_budget)),
        ap_used=ap_used,
        casts=[by_spell[spell_id] for spell_id in order],
        damage=total_damage,
        heal=total_heal,
        utility=total_utility,
        value=total_value,
    )


def candidate_for(
    repo: Repository,
    spell: OfficialSpell,
    grade: int,
    stats: CharacterStats,
    focus: Optional[str],
    max_hp: float,
    off_element_weight: float = OFF_ELEMENT_WEIGHT,
) -> Optional[RotationCandidate]:
    row = spell.grade(grade)
    if row is None or row.ap_cost is None:
        return None
    power = parse_spell_power(spell, grade, stats, focus, max_hp, off_element_weight)
    max_casts = 1 if row.cooldown > 0 else min(99, max(1, int(row.max_cast_per_turn or 1)))
    value = (power.damage
             + power.heal * HEAL_WEIGHT
             + power.utility * UTILITY_WEIGHT)
    if value <= 0:
        return None
    return RotationCandidate(
        spell_id=spell.id,
        name=spell.name,
        grade=grade,
        element=power.dominant_element,
        ap_cost=max(0, int(row.ap_cost)),
        max_casts=max_casts,
        cooldown=int(row.cooldown),
        damage=power.damage,
        heal=power.heal * HEAL_WEIGHT,
        utility=power.utility * UTILITY_WEIGHT,
        value=value,
    )


# ---------------------------------------------------------------------------
# budgets


@dataclass
class BudgetOption:
    label: str
    points: int
    assumption: str


def budget_options(points: Dict[str, Any]) -> List[BudgetOption]:
    def number(key: str) -> Optional[int]:
        value = points.get(key)
        if value is None or isinstance(value, bool):
            return None
        try:
            return max(0, int(round(float(value))))
        except (TypeError, ValueError):
            return None

    cap_left = number("character_cap_left")
    raw_spells = number("raw_spells_points")
    raw_account = number("raw_account_spells_points")
    remaining = number("remaining_if_raw_is_balance")
    out: List[BudgetOption] = []
    seen: Dict[int, BudgetOption] = {}

    def add(label: str, points_value: Optional[int], assumption: str) -> None:
        if points_value is None:
            return
        existing = seen.get(points_value)
        if existing:
            labels = existing.label.split(" | ")
            if label not in labels:
                existing.label = existing.label + " | " + label
            assumptions = existing.assumption.split("; ")
            if assumption not in assumptions:
                existing.assumption = existing.assumption + "; " + assumption
            return
        option = BudgetOption(label=label, points=points_value, assumption=assumption)
        seen[points_value] = option
        out.append(option)

    if cap_left is not None:
        add("character_cap_left", cap_left,
            "(level - 1) - used spell points, the client's own cap")
        if raw_account:
            add("cap_plus_account", cap_left + raw_account,
                "character cap left plus account spell points as a remaining balance")
    if remaining is not None:
        add("raw_balance_total", remaining,
            "raw spellsPoints + accountSpellsPoints treated as remaining balances")
    if raw_spells is not None:
        add("raw_spells_only", raw_spells,
            "raw spellsPoints alone treated as a remaining balance")
    if not out:
        out.append(BudgetOption("unknown", 0,
                                "the snapshot has no spell-point fields"))
    return out


def choose_budget(options: Sequence[BudgetOption]) -> BudgetOption:
    """Pick the most conservative positive budget, preferring the client cap.

    `budget_options` merges labels that share the same point value, so a label
    lookup has to test the individual tokens rather than the joined string.
    """
    for label in ("character_cap_left", "cap_plus_account",
                  "raw_balance_total", "raw_spells_only"):
        for option in options:
            if label in option.label.split(" | ") and option.points > 0:
                return option
    for option in options:
        if option.points > 0:
            return option
    return options[0]


# ---------------------------------------------------------------------------
# allocation


@dataclass
class SpellOption:
    grade: int
    cost: int
    value_gain: float
    damage_gain: float
    heal_gain: float


@dataclass
class SpellUpgrade:
    spell_id: int
    name: str
    element: Optional[str]
    current_grade: int
    recommended_grade: int
    cost: int
    estimated_value_gain: float
    estimated_damage_gain: float
    grade_cap: int
    next_grade: Optional[int]
    next_cost: Optional[int]
    next_value_gain: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spell_id": self.spell_id,
            "name": self.name,
            "element": self.element,
            "current_grade": self.current_grade,
            "recommended_grade": self.recommended_grade,
            "cost": self.cost,
            "estimated_value_gain": round(self.estimated_value_gain, 2),
            "estimated_damage_gain": round(self.estimated_damage_gain, 2),
            "grade_cap": self.grade_cap,
            "next_grade": self.next_grade,
            "next_cost": self.next_cost,
            "next_value_gain": (round(self.next_value_gain, 2)
                                if self.next_value_gain is not None else None),
        }


@dataclass
class SpellPlan:
    focus: Optional[str]
    budget: int
    budget_source: str
    budget_assumption: str
    budget_options: List[Dict[str, Any]]
    spent: int
    unspent: int
    upgrades: List[SpellUpgrade]
    considered: List[Dict[str, Any]]
    rotation_before: RotationResult
    rotation_after: RotationResult
    actual_value_gain: float
    actual_damage_gain: float
    budget_scenarios: List[Dict[str, Any]]
    unscored: List[Dict[str, Any]]
    warnings: List[str]
    synthetic_spellbook: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "focus": self.focus,
            "budget": self.budget,
            "budget_source": self.budget_source,
            "budget_assumption": self.budget_assumption,
            "budget_options": self.budget_options,
            "spent": self.spent,
            "unspent": self.unspent,
            "upgrades": [upgrade.to_dict() for upgrade in self.upgrades],
            "considered": self.considered,
            "rotation_before": self.rotation_before.to_dict(),
            "rotation_after": self.rotation_after.to_dict(),
            "actual_value_gain": round(self.actual_value_gain, 2),
            "actual_damage_gain": round(self.actual_damage_gain, 2),
            "budget_scenarios": self.budget_scenarios,
            "unscored": self.unscored,
            "warnings": self.warnings,
            "synthetic_spellbook": self.synthetic_spellbook,
        }


def _solve_options(
    option_sets: Sequence[Sequence[SpellOption]],
    budget: int,
) -> Tuple[Dict[int, int], float, int]:
    """Multiple-choice knapsack over per-spell grade options."""
    if budget <= 0:
        return ({}, 0.0, 0)
    dp: Dict[int, Tuple[float, Tuple[int, ...]]] = {0: (0.0, ())}
    for options in option_sets:
        next_dp: Dict[int, Tuple[float, Tuple[int, ...]]] = {}
        for spent, (value, choices) in dp.items():
            for index, option in enumerate(options):
                new_spent = spent + option.cost
                if new_spent > budget:
                    continue
                new_value = value + option.value_gain
                current = next_dp.get(new_spent)
                if current is None or new_value > current[0] + 1e-9:
                    next_dp[new_spent] = (new_value, choices + (index,))
        dp = next_dp
    if not dp:
        return ({}, 0.0, 0)
    best_spent = max(dp, key=lambda cost: (round(dp[cost][0], 9), -cost))
    return (dict(enumerate(dp[best_spent][1])), dp[best_spent][0], best_spent)


def _rotation_for_grades(
    candidates: Dict[Tuple[int, int], RotationCandidate],
    grades: Dict[int, int],
    ap_budget: int,
) -> RotationResult:
    chosen = []
    for spell_id, grade in grades.items():
        candidate = candidates.get((spell_id, grade))
        if candidate is not None:
            chosen.append(candidate)
    chosen.sort(key=lambda row: row.spell_id)
    return greedy_rotation(chosen, ap_budget)


def effective_spell_states(
    repo: Repository,
    character: CharacterSnapshot,
) -> Tuple[Dict[int, SpellState], bool, List[str]]:
    """Live spell states, or a clearly-labelled grade-1 fallback."""
    live = {spell.spell_id: spell for spell in character.spells if not spell.is_item}
    warnings: List[str] = []
    synthetic = False
    if not live:
        synthetic = True
        warnings.append(
            "No live spellbook in the snapshot; assuming every class spell whose "
            "required level is met starts at grade 1."
        )
        for spell in repo.class_spells(character.breed_id):
            cap = repo.grade_cap(spell, None, character.level)
            if cap >= 1:
                live[spell.id] = SpellState(
                    spell_id=spell.id,
                    level=1,
                    max_grade=max(spell.grades) if spell.grades else 6,
                    max_grade_for_level=cap,
                    breed_id=spell.breed_id,
                )
    return live, synthetic, warnings


def plan_spells(
    repo: Repository,
    character: CharacterSnapshot,
    focus: Optional[str],
    budget: Optional[int] = None,
) -> SpellPlan:
    stats = character.to_character_stats()
    ap_budget = character.ap_budget
    max_hp = character.max_hp
    live, synthetic, warnings = effective_spell_states(repo, character)

    entries: List[Tuple[OfficialSpell, SpellState, int, int]] = []
    unscored: List[Dict[str, Any]] = []
    for spell_id, state in sorted(live.items()):
        spell = repo.spell_for(spell_id, state.breed_id or character.breed_id)
        if spell is None:
            unscored.append({
                "spell_id": spell_id,
                "name_id": state.name_id,
                "level": state.level,
                "reason": "not in the official class-spell database",
            })
            continue
        cap = repo.grade_cap(spell, state, character.level)
        current = max(1, min(state.level, cap)) if cap >= 1 else 0
        if current < 1:
            continue
        entries.append((spell, state, current, cap))

    if not entries:
        empty = greedy_rotation([], ap_budget)
        options = budget_options(character.points)
        return SpellPlan(
            focus=focus, budget=0, budget_source="none",
            budget_assumption="no scoreable spells",
            budget_options=[option.__dict__ for option in options],
            spent=0, unspent=0, upgrades=[], considered=[],
            rotation_before=empty, rotation_after=empty,
            actual_value_gain=0.0, actual_damage_gain=0.0,
            budget_scenarios=[], unscored=unscored,
            warnings=warnings + ["No spell in the live spellbook matched the official "
                                 "class database; nothing can be priced."],
            synthetic_spellbook=synthetic,
        )

    # Cache every grade the character can actually reach. Current grades are
    # also candidates even when the reported cap is lower (the server could
    # have given a grade the level row could not explain); _agentSpellEntry
    # already raises the cap for exactly that case, so cap >= current here.
    candidates: Dict[Tuple[int, int], RotationCandidate] = {}
    for spell, _state, current, cap in entries:
        for grade in range(current, cap + 1):
            candidate = candidate_for(repo, spell, grade, stats, focus, max_hp)
            if candidate is not None:
                candidates[(spell.id, grade)] = candidate

    baseline_grades = {spell.id: current for spell, _state, current, _cap in entries}
    baseline = _rotation_for_grades(candidates, baseline_grades, ap_budget)

    option_sets: List[List[SpellOption]] = []
    option_meta: List[Tuple[OfficialSpell, int, int]] = []
    for spell, _state, current, cap in entries:
        options: List[SpellOption] = [SpellOption(current, 0, 0.0, 0.0, 0.0)]
        for grade in range(current + 1, cap + 1):
            tentative = dict(baseline_grades)
            tentative[spell.id] = grade
            rotation = _rotation_for_grades(candidates, tentative, ap_budget)
            options.append(SpellOption(
                grade=grade,
                cost=repo.grade_costs(current, grade),
                value_gain=rotation.value - baseline.value,
                damage_gain=rotation.damage - baseline.damage,
                heal_gain=rotation.heal - baseline.heal,
            ))
        option_sets.append(options)
        option_meta.append((spell, current, cap))

    options = budget_options(character.points)
    if budget is None:
        chosen_budget_option = choose_budget(options)
        budget_value = chosen_budget_option.points
        budget_source = chosen_budget_option.label
        budget_assumption = chosen_budget_option.assumption
    else:
        budget_value = max(0, int(budget))
        budget_source = "explicit"
        budget_assumption = "budget supplied by the caller"

    # A plan is only meaningful up to the cost of taking everything.
    max_cost = sum(max(option.cost for option in option_set)
                   for option_set in option_sets)
    solve_budget = min(budget_value, max_cost)

    selection, estimated_gain, estimated_spend = _solve_options(option_sets, solve_budget)
    plan_grades = dict(baseline_grades)
    for index, option_index in selection.items():
        spell, _current, _cap = option_meta[index]
        plan_grades[spell.id] = option_sets[index][option_index].grade

    def spend_of(grades: Dict[int, int]) -> int:
        total = 0
        for spell, current, _cap in option_meta:
            total += repo.grade_costs(current, grades.get(spell.id, current))
        return total

    def value_of(grades: Dict[int, int]) -> RotationResult:
        return _rotation_for_grades(candidates, grades, ap_budget)

    current_rotation = value_of(plan_grades)
    spend = spend_of(plan_grades)

    # The knapsack values each upgrade against the current spellbook. Two
    # upgrades can therefore both look good and then compete for the same AP.
    # Re-score the exact rotation after every accepted single change, then
    # allow one-step swaps so a bad combination can be undone within budget.
    for _ in range(60):
        best = None
        base_value = current_rotation.value
        for spell, current, cap in option_meta:
            grade = plan_grades[spell.id]
            if grade >= cap:
                continue
            increment = repo.grade_costs(grade, grade + 1)
            if spend + increment > solve_budget:
                continue
            tentative = dict(plan_grades)
            tentative[spell.id] = grade + 1
            rotation = value_of(tentative)
            if rotation.value > base_value + 1e-6:
                key = (rotation.value, rotation.damage, -increment, -spell.id)
                if best is None or key > best[0]:
                    best = (key, spell.id, grade + 1, increment, rotation)
        if best is None:
            break
        _key, spell_id, grade, increment, rotation = best
        plan_grades[spell_id] = grade
        spend += increment
        current_rotation = rotation

    for _ in range(12):
        best = None
        base_value = current_rotation.value
        for spell, current, _cap in option_meta:
            grade = plan_grades[spell.id]
            if grade <= current:
                continue
            refund = repo.grade_costs(grade - 1, grade)
            for other, other_current, other_cap in option_meta:
                if other.id == spell.id:
                    continue
                other_grade = plan_grades[other.id]
                if other_grade >= other_cap:
                    continue
                increment = repo.grade_costs(other_grade, other_grade + 1)
                if spend - refund + increment > solve_budget:
                    continue
                tentative = dict(plan_grades)
                tentative[spell.id] = grade - 1
                tentative[other.id] = other_grade + 1
                rotation = value_of(tentative)
                if rotation.value > base_value + 1e-6:
                    key = (rotation.value, rotation.damage, -(increment - refund),
                           -spell.id, -other.id)
                    if best is None or key > best[0]:
                        best = (key, spell.id, other.id, rotation)
        if best is None:
            break
        _key, lower_id, raise_id, rotation = best
        plan_grades[lower_id] -= 1
        plan_grades[raise_id] += 1
        spend = spend_of(plan_grades)
        current_rotation = rotation

    after = current_rotation
    upgrades: List[SpellUpgrade] = []
    considered: List[Dict[str, Any]] = []
    for (spell, current, cap), option_set in zip(option_meta, option_sets):
        chosen_grade = plan_grades[spell.id]
        power = parse_spell_power(spell, chosen_grade, stats, focus, max_hp)
        picked = next((option for option in option_set if option.grade == chosen_grade),
                      option_set[0])
        next_option = next((option for option in option_set
                            if option.grade == chosen_grade + 1), None)
        considered.append({
            "spell_id": spell.id,
            "name": spell.name,
            "element": power.element,
            "current_grade": current,
            "grade_cap": cap,
            "recommended_grade": chosen_grade,
            "cost": repo.grade_costs(current, chosen_grade),
            "current_ap": (spell.grade(current).ap_cost if spell.grade(current) else None),
            "damage_at_recommended": round(power.damage, 2),
            "heal_at_recommended": round(power.heal, 2),
            "utility_at_recommended": round(power.utility, 2),
        })
        if chosen_grade > current:
            upgrades.append(SpellUpgrade(
                spell_id=spell.id,
                name=spell.name,
                element=power.element,
                current_grade=current,
                recommended_grade=chosen_grade,
                cost=repo.grade_costs(current, chosen_grade),
                estimated_value_gain=picked.value_gain,
                estimated_damage_gain=picked.damage_gain,
                grade_cap=cap,
                next_grade=next_option.grade if next_option else None,
                next_cost=next_option.cost - repo.grade_costs(current, chosen_grade)
                          if next_option else None,
                next_value_gain=next_option.value_gain - picked.value_gain
                                if next_option else None,
            ))
    upgrades.sort(key=lambda row: (-row.cost, -row.estimated_value_gain, row.spell_id))

    scenarios: List[Dict[str, Any]] = []
    seen_budgets = set()
    for option in options:
        if option.points in seen_budgets:
            continue
        seen_budgets.add(option.points)
        scenario_budget = min(option.points, max_cost)
        _selection, scenario_estimated, scenario_spend = _solve_options(
            option_sets, scenario_budget)
        scenarios.append({
            "label": option.label,
            "assumption": option.assumption,
            "budget": option.points,
            "spent": scenario_spend,
            "estimated_gain": round(scenario_estimated, 2),
        })
    scenarios.sort(key=lambda row: row["budget"])

    if estimated_gain <= 0 and upgrades:
        warnings.append(
            "The knapsack found no positive estimated upgrade; the listed plan "
            "comes from the post-plan rotation check."
        )
    if character.points.get("raw_spells_points") is not None:
        warnings.append(
            "Spell-point fields are reported both raw and client-derived. The plan "
            "uses the selected assumption above; check the budget_scenarios before "
            "committing points."
        )
    if not any(option.points > 0 for option in options):
        warnings.append("No positive spell-point budget was found in the snapshot.")

    return SpellPlan(
        focus=focus,
        budget=solve_budget,
        budget_source=budget_source,
        budget_assumption=budget_assumption,
        budget_options=[{
            "label": option.label,
            "points": option.points,
            "assumption": option.assumption,
        } for option in options],
        spent=spend,
        unspent=max(0, solve_budget - spend),
        upgrades=upgrades,
        considered=considered,
        rotation_before=baseline,
        rotation_after=after,
        actual_value_gain=after.value - baseline.value,
        actual_damage_gain=after.damage - baseline.damage,
        budget_scenarios=scenarios,
        unscored=unscored,
        warnings=warnings,
        synthetic_spellbook=synthetic,
    )
