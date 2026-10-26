from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from combat.damage_calculator import CharacterStats, DofusDamageCalculator, TargetStats
    from data.game_data_extractor import DamageEffect, SpellInfo
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from combat.damage_calculator import CharacterStats, DofusDamageCalculator, TargetStats
    from data.game_data_extractor import DamageEffect, SpellInfo

from .models import ELEMENTS, CharacterSnapshot, SpellState
from .repository import OfficialSpell, Repository, effect_roll

HEAL_WEIGHT = .8
UTILITY_WEIGHT = 1.
OFF_ELEMENT_WEIGHT = .25
RESPEC_PREFERENCE = .08
UNLIMITED = 10 ** 6

_BEST = re.compile(r"(\d+)\s+to\s+(\d+)\s+\(best element\)", re.I)
_HEAL = re.compile(r"(\d+)\s+to\s+(\d+)\s+\(HP restored\)", re.I)
_STEAL = re.compile(r"\((?:Earth|Fire|Water|Air|Neutral) steal\)", re.I)
_MAXHP = re.compile(r"(\d+)%\s+of\s+max\s+hp", re.I)
_AP = re.compile(r"-(\d+)\s+ap\b")
_AP2 = re.compile(r"(\d+)\s+ap\b")
_STEALS = re.compile(r"steals\s+(\d+)\s+mp\b")
_MP = re.compile(r"-(\d+)\s+mp\b")
_MP2 = re.compile(r"(\d+)\s+mp\b")
_RANGE = re.compile(r"-?(\d+)\s+range")
_POWER = re.compile(r"(\d+)\s+power")
_VIT = re.compile(r"(\d+)%\s+vitality")
_SHIELD = re.compile(r"(\d+)%\s+of\s+level\s+to\s+shield")
_SUST = re.compile(r"x(\d+)%\s+damage sustained")
_SPDMG = re.compile(r"(\d+)%\s+spell damage inflicted")
_EROS = re.compile(r"(\d+)%\s+erosion")
_CELL = re.compile(r"(\d+)\s+cell")
_GRADE = re.compile(r"grade\s+(\d+)")
_DODGE = re.compile(r"(\d+)\s+(?:dodge|lock)")
_CRIT = re.compile(r"(\d+)%\s+critical hits")
_BY = re.compile(r"by\s+(\d+)")
_NUM = re.compile(r"(\d+)")


@dataclass(slots=True)
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
        return max(self.element_damage, key=self.element_damage.get) if self.element_damage else None


def _best_element_roll(raw: Dict[str, Any], focus: Optional[str]) -> Optional[Tuple[str, int, int]]:
    m = _BEST.search(str(raw.get("text") or ""))
    return (focus if focus in ELEMENTS else "earth", int(m[1]), int(m[2])) if m else None


def _heal_roll(raw: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    h = raw.get("heal")
    if isinstance(h, dict):
        return int(h.get("min_roll") or 0), int(h.get("max_roll") or 0)
    m = _HEAL.search(str(raw.get("text") or ""))
    return (int(m[1]), int(m[2])) if m else None


@lru_cache(maxsize=512)
def _utility_points(text: str, level: int, max_hp: float) -> float:
    s = text.lower().strip()
    if not s:
        return 0.
    m = _AP.match(s)
    if m:
        return min(60., 8. * int(m[1]))
    m = _AP2.match(s)
    if m:
        return min(60., 7. * int(m[1]))
    m = _STEALS.match(s)
    if m:
        return min(40., 4. * int(m[1]))
    m = _MP.match(s)
    if m:
        return min(50., 5. * int(m[1]))
    m = _MP2.match(s)
    if m:
        return min(50., 5. * int(m[1]))
    if "range" in s:
        m = _RANGE.search(s)
        return min(40., 6. * (int(m[1]) if m else 0))
    if "power" in s:
        m = _POWER.search(s)
        return min(40., 1. * (int(m[1]) if m else 0))
    if "vitality" in s:
        m = _VIT.search(s)
        return min(40., 2. * (int(m[1]) if m else 0))
    if "shield" in s:
        m = _SHIELD.search(s)
        return min(80., .8 * (int(m[1]) if m else 0) * max(1, level) / 100.)
    if "heal:" in s or "heal " in s:
        m = _MAXHP.search(s)
        if m and max_hp > 0:
            return min(200., .8 * int(m[1]) * max_hp / 100.)
        return 4.
    if "damage sustained" in s:
        m = _SUST.search(s)
        return max(-60., min(60., 100. - int(m[1]))) if m else 0.
    if "spell damage inflicted" in s:
        m = _SPDMG.search(s)
        return min(60., float(m[1]) if m else 0.)
    if "erosion" in s:
        m = _EROS.search(s)
        return min(40., float(m[1]) if m else 0.)
    if "pushes back" in s or "attracts" in s or "advances" in s:
        m = _CELL.search(s)
        return min(30., 1.5 * (int(m[1]) if m else 0))
    if "summons" in s:
        return 12.
    if "trap" in s:
        m = _GRADE.search(s)
        return min(30., 3. * (int(m[1]) if m else 1.))
    if "kills the target" in s:
        return 20.
    if "dodge" in s or "lock" in s:
        m = _DODGE.search(s)
        return min(20., .5 * (int(m[1]) if m else 0))
    if "critical hits" in s:
        m = _CRIT.search(s)
        return min(20., float(m[1]) if m else 0.)
    if "reduces damage" in s:
        m = _BY.search(s)
        return min(30., .05 * (int(m[1]) if m else 0))
    if "resistance" in s or "resist" in s:
        m = _NUM.search(s)
        return min(30., .5 * (int(m[1]) if m else 0))
    if "damage" in s and "inflicted" in s:
        m = _NUM.search(s)
        return min(30., float(m[1]) if m else 0.)
    if "state" in s or "removes" in s:
        return .5
    if "teleport" in s or "switches positions" in s:
        return 2.
    return .25


def _is_steal_roll(raw: Dict[str, Any]) -> bool:
    return bool(_STEAL.search(str(raw.get("text") or "")))


_TARGET = TargetStats(name="advisor dummy", max_hp=10_000, current_hp=10_000, level=100)
_CALC = DofusDamageCalculator.calculate_spell_damage


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
        return SpellPower(0., 0., 0., 0., None, {}, 0)
    normal = row.effects or []
    crit = row.critical_effects or []
    ncrit = len(crit)
    effects: List[DamageEffect] = []
    steals: List[bool] = []
    heals: List[DamageEffect] = []
    utility = 0.
    lines: List[str] = []
    fixed = 0.
    level = stats.level
    elements = ELEMENTS
    roll = effect_roll
    for i, raw in enumerate(normal):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "")
        run = roll(raw)
        if run is None:
            m = _BEST.search(text)
            if m:
                run = (focus if focus in elements else "earth", int(m[1]), int(m[2]))
        h = raw.get("heal")
        if isinstance(h, dict):
            heal = int(h.get("min_roll") or 0), int(h.get("max_roll") or 0)
        else:
            m = _HEAL.search(text)
            heal = (int(m[1]), int(m[2])) if m else None
        if run is not None:
            element, low, high = run
            cl = ch = 0
            if i < ncrit:
                cr = crit[i]
                if isinstance(cr, dict):
                    crun = roll(cr) or _best_element_roll(cr, focus)
                    if crun and crun[0] == element:
                        cl, ch = crun[1], crun[2]
            steal = bool(_STEAL.search(text))
            folded = False
            if steal:
                for j, effect in enumerate(effects):
                    if (not steals[j] and effect.element.lower() == element
                            and effect.min_roll == low and effect.max_roll == high):
                        steals[j] = True
                        folded = True
                        break
            if not folded:
                effects.append(DamageEffect(element.upper(), low, high, cl, ch))
                steals.append(steal)
        if heal is not None:
            crit_heal = _heal_roll(crit[i]) if i < ncrit and isinstance(crit[i], dict) else None
            heals.append(DamageEffect("HEAL", heal[0], heal[1],
                                      crit_heal[0] if crit_heal else 0,
                                      crit_heal[1] if crit_heal else 0))
        if run is None and heal is None:
            line = text.strip()
            if line:
                points = _utility_points(line, level, max_hp)
                if points:
                    utility += points
                    lines.append(line)
                    m = _MAXHP.search(line)
                    if m and "heal" in line.lower() and max_hp > 0:
                        fixed += max_hp * float(m[1]) / 100.
    effects.extend(heals)
    steals.extend([False] * len(heals))
    if not effects:
        return SpellPower(0., 0., fixed, utility, None, {}, 0, lines)
    info = spell.info(grade)
    info.name_fr = info.name_en = spell.name
    info.name_es = ""
    info.effects = effects
    result = _CALC(info, stats, _TARGET)
    crit_rate = result.crit_rate
    damage = 0.
    heal = fixed
    element_damage: Dict[str, float] = {}
    focus_ok = focus in elements
    off = max(0., min(1., off_element_weight))
    nsteal = len(steals)
    for i, line in enumerate(result.lines):
        expected = (1. - crit_rate) * line.final_avg + crit_rate * line.crit_final_avg
        element = line.element.lower()
        if element == "heal":
            heal += expected
            continue
        if i < nsteal and steals[i]:
            heal += expected
        if element == "neutral":
            element = "earth"
        weight = off if focus_ok and element != focus else 1.
        element_damage[element] = element_damage.get(element, 0.) + expected * weight
        damage += expected * weight
    raw_damage = sum((1.0 - crit_rate) * line.final_avg + crit_rate * line.crit_final_avg
                     for line in result.lines if line.element.upper() != "HEAL")
    return SpellPower(damage, raw_damage, heal, utility,
                      max(element_damage, key=element_damage.get) if element_damage else None,
                      element_damage, len(effects), lines)


@dataclass(slots=True)
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


@dataclass(slots=True)
class RotationCast:
    spell_id: int
    name: str
    grade: int
    element: Optional[str]
    casts: int = 0
    ap: int = 0
    damage: float = 0.
    heal: float = 0.
    utility: float = 0.
    value: float = 0.

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


@dataclass(slots=True)
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
    budget = max(0, int(ap_budget))
    remaining = budget
    casts_left = [max(0, int(c.max_casts)) for c in candidates]
    aps = [max(0, c.ap_cost) for c in candidates]
    keys = [(c.value / max(1, ap), c.value, -ap, -c.spell_id)
            for c, ap in zip(candidates, aps)]
    by_spell: Dict[int, RotationCast] = {}
    order: List[int] = []
    total_damage = total_heal = total_utility = total_value = 0.
    ap_used = 0
    for _ in range(200):
        best_index = -1
        best_key = None
        for index, candidate in enumerate(candidates):
            if casts_left[index] <= 0:
                continue
            ap = aps[index]
            if ap > remaining:
                continue
            key = keys[index]
            if best_key is None or key > best_key:
                best_key = key
                best_index = index
        if best_index < 0:
            break
        candidate = candidates[best_index]
        ap = aps[best_index]
        remaining -= ap
        ap_used += ap
        casts_left[best_index] -= 1
        spell_id = candidate.spell_id
        row = by_spell.get(spell_id)
        if row is None:
            row = RotationCast(spell_id, candidate.name, candidate.grade, candidate.element)
            by_spell[spell_id] = row
            order.append(spell_id)
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
    return RotationResult(budget, ap_used, [by_spell[spell_id] for spell_id in order],
                          total_damage, total_heal, total_utility, total_value)


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
    value = power.damage + power.heal * HEAL_WEIGHT + power.utility * UTILITY_WEIGHT
    if value <= 0:
        return None
    return RotationCandidate(
        spell.id, spell.name, grade, power.element, max(0, int(row.ap_cost)),
        max_casts, int(row.cooldown), power.damage, power.heal * HEAL_WEIGHT,
        power.utility * UTILITY_WEIGHT, value)


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
            if label not in existing.label.split(" | "):
                existing.label = existing.label + " | " + label
            if assumption not in existing.assumption.split("; "):
                existing.assumption = existing.assumption + "; " + assumption
            return
        option = BudgetOption(label, points_value, assumption)
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
        out.append(BudgetOption("unknown", 0, "the snapshot has no spell-point fields"))
    return out


def choose_budget(options: Sequence[BudgetOption]) -> BudgetOption:
    for label in ("character_cap_left", "cap_plus_account",
                  "raw_balance_total", "raw_spells_only"):
        for option in options:
            if label in option.label.split(" | ") and option.points > 0:
                return option
    for option in options:
        if option.points > 0:
            return option
    return options[0]


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
    if budget <= 0:
        return {}, 0., 0
    dp: Dict[int, tuple] = {0: (0., ())}
    for options in option_sets:
        next_dp: Dict[int, tuple] = {}
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
        return {}, 0., 0
    best_spent = max(dp, key=lambda cost: (round(dp[cost][0], 9), -cost))
    return dict(enumerate(dp[best_spent][1])), dp[best_spent][0], best_spent


def _rotation_for_grades(
    candidates: Dict[Tuple[int, int], RotationCandidate],
    grades: Dict[int, int],
    ap_budget: int,
) -> RotationResult:
    chosen = [candidates[key] for key in grades.items() if key in candidates]
    chosen.sort(key=lambda row: row.spell_id)
    return greedy_rotation(chosen, ap_budget)


def effective_spell_states(
    repo: Repository,
    character: CharacterSnapshot,
) -> Tuple[Dict[int, SpellState], bool, List[str]]:
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
                    spell.id, 1, max(spell.grades) if spell.grades else 6, cap,
                    None, spell.breed_id,
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

    entries: List[tuple] = []
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
        if current >= 1:
            entries.append((spell, state, current, cap))

    if not entries:
        empty = greedy_rotation([], ap_budget)
        options = budget_options(character.points)
        return SpellPlan(
            focus, 0, "none", "no scoreable spells",
            [option.__dict__ for option in options],
            0, 0, [], [], empty, empty, 0., 0., [], unscored,
            warnings + ["No spell in the live spellbook matched the official "
                        "class database; nothing can be priced."],
            synthetic,
        )

    candidates: Dict[tuple, RotationCandidate] = {}
    for spell, _state, current, cap in entries:
        for grade in range(current, cap + 1):
            candidate = candidate_for(repo, spell, grade, stats, focus, max_hp)
            if candidate is not None:
                candidates[(spell.id, grade)] = candidate

    baseline_grades = {spell.id: current for spell, _state, current, _cap in entries}
    baseline = _rotation_for_grades(candidates, baseline_grades, ap_budget)

    option_sets: List[List[SpellOption]] = []
    option_meta: List[tuple] = []
    for spell, _state, current, cap in entries:
        options = [SpellOption(current, 0, 0., 0., 0.)]
        for grade in range(current + 1, cap + 1):
            tentative = dict(baseline_grades)
            tentative[spell.id] = grade
            rotation = _rotation_for_grades(candidates, tentative, ap_budget)
            options.append(SpellOption(
                grade, repo.grade_costs(current, grade),
                rotation.value - baseline.value,
                rotation.damage - baseline.damage,
                rotation.heal - baseline.heal,
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

    max_cost = sum(max(option.cost for option in option_set)
                   for option_set in option_sets)
    solve_budget = min(budget_value, max_cost)

    selection, estimated_gain, estimated_spend = _solve_options(option_sets, solve_budget)
    plan_grades = dict(baseline_grades)
    for index, option_index in selection.items():
        spell, _current, _cap = option_meta[index]
        plan_grades[spell.id] = option_sets[index][option_index].grade

    def spend_of(grades: Dict[int, int]) -> int:
        return sum(repo.grade_costs(current, grades.get(spell.id, current))
                   for spell, current, _cap in option_meta)

    def value_of(grades: Dict[int, int]) -> RotationResult:
        return _rotation_for_grades(candidates, grades, ap_budget)

    current_rotation = value_of(plan_grades)
    spend = spend_of(plan_grades)

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
            for other, _other_current, other_cap in option_meta:
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
        index = chosen_grade - current
        picked = option_set[index]
        next_option = option_set[index + 1] if chosen_grade < cap else None
        cost = repo.grade_costs(current, chosen_grade)
        current_row = spell.grade(current)
        considered.append({
            "spell_id": spell.id,
            "name": spell.name,
            "element": power.element,
            "current_grade": current,
            "grade_cap": cap,
            "recommended_grade": chosen_grade,
            "cost": cost,
            "current_ap": (current_row.ap_cost if current_row else None),
            "damage_at_recommended": round(power.damage, 2),
            "heal_at_recommended": round(power.heal, 2),
            "utility_at_recommended": round(power.utility, 2),
        })
        if chosen_grade > current:
            upgrades.append(SpellUpgrade(
                spell.id, spell.name, power.element, current, chosen_grade, cost,
                picked.value_gain, picked.damage_gain, cap,
                next_option.grade if next_option else None,
                next_option.cost - cost if next_option else None,
                next_option.value_gain - picked.value_gain if next_option else None,
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
        focus, solve_budget, budget_source, budget_assumption,
        [{"label": option.label, "points": option.points,
          "assumption": option.assumption} for option in options],
        spend, max(0, solve_budget - spend), upgrades, considered,
        baseline, after,
        after.value - baseline.value, after.damage - baseline.damage,
        scenarios, unscored, warnings, synthetic,
    )
