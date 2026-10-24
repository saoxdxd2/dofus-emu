"""Element choice from the spells the character can actually use.

The comparison is deliberately mechanical: for every element, take the best
grade the character's level allows, price those spells with the shared damage
calculator, and fill one turn of AP. Two columns are reported:

* `current` uses the stats on the character sheet;
* `potential` assumes every current primary point could be moved into that
  element. Gear-locked stats cannot be separated from spent points in the live
  report, so `potential` is an upper bound, not a promise.

The recommendation only follows the bigger number when the existing element is
not within a few percent of it. Respeccing a whole character to gain 2% damage
is usually a bad trade.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import (
    ELEMENTS,
    ELEMENT_LABEL,
    PRIMARY_STAT,
    CharacterSnapshot,
    SpellState,
)
from .repository import OfficialSpell, Repository
from .spells import (
    RESPEC_PREFERENCE,
    RotationResult,
    candidate_for,
    effective_spell_states,
    greedy_rotation,
)


@dataclass
class ElementScore:
    element: str
    primary_stat: str
    current_stat: float
    pooled_stat: float
    current_damage: float
    current_value: float
    potential_damage: float
    potential_value: float
    damage_per_ap: float
    spell_count: int
    best_spell: Optional[str]
    best_grade: Optional[int]
    current_rotation: RotationResult
    potential_rotation: RotationResult

    def to_dict(self) -> Dict[str, Any]:
        return {
            "element": self.element,
            "label": ELEMENT_LABEL[self.element],
            "primary_stat": self.primary_stat,
            "current_stat": round(self.current_stat, 1),
            "pooled_stat": round(self.pooled_stat, 1),
            "current_damage": round(self.current_damage, 2),
            "current_value": round(self.current_value, 2),
            "potential_damage": round(self.potential_damage, 2),
            "potential_value": round(self.potential_value, 2),
            "damage_per_ap": round(self.damage_per_ap, 2),
            "spell_count": self.spell_count,
            "best_spell": self.best_spell,
            "best_grade": self.best_grade,
            "current_rotation": self.current_rotation.to_dict(),
            "potential_rotation": self.potential_rotation.to_dict(),
        }


@dataclass
class ElementDecision:
    requested: str
    chosen: str
    reason: str
    follow_existing: Optional[str]
    respec_needed: bool
    scores: List[ElementScore]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested": self.requested,
            "chosen": self.chosen,
            "label": ELEMENT_LABEL.get(self.chosen, self.chosen),
            "reason": self.reason,
            "follow_existing": self.follow_existing,
            "respec_needed": self.respec_needed,
            "scores": [score.to_dict() for score in self.scores],
            "warnings": self.warnings,
        }


def _spells_and_caps(
    repo: Repository,
    character: CharacterSnapshot,
    live: Dict[int, SpellState],
) -> List[Tuple[OfficialSpell, int]]:
    out: List[Tuple[OfficialSpell, int]] = []
    for spell in repo.class_spells(character.breed_id):
        cap = repo.grade_cap(spell, live.get(spell.id), character.level)
        if cap >= 1:
            out.append((spell, cap))
    return out


def _rotation_for_element(
    repo: Repository,
    spells: Sequence[Tuple[OfficialSpell, int]],
    stats,
    element: str,
    max_hp: float,
    ap_budget: int,
    off_element_weight: float,
) -> Tuple[RotationResult, int, Optional[str], Optional[int]]:
    candidates = []
    for spell, cap in spells:
        candidate = candidate_for(
            repo, spell, cap, stats, element, max_hp, off_element_weight)
        if candidate is not None:
            candidates.append(candidate)
    # Element choice is a damage question. A pure buff can outscore a damage
    # spell under the utility heuristic and then fill the whole turn, leaving
    # the comparison with zero damage and no information. Rank elements on the
    # damaging spells only; the spell planner still values utility.
    damaging = [candidate for candidate in candidates if candidate.damage > 0]
    rotation = greedy_rotation(damaging, ap_budget)
    best = max(damaging, key=lambda row: (row.damage, row.value, -row.spell_id),
               default=None)
    return rotation, len(damaging), (best.name if best else None), (best.grade if best else None)


def choose_element(
    repo: Repository,
    character: CharacterSnapshot,
    requested: str = "auto",
) -> ElementDecision:
    request = (requested or "auto").strip().lower()
    warnings: List[str] = []
    if character.breed_id <= 0:
        warnings.append(
            "The snapshot has no breed id; class spells cannot be selected. "
            "Capture a character report while the character is in world."
        )
    live, _synthetic, state_warnings = effective_spell_states(repo, character)
    warnings.extend(state_warnings)
    spells = _spells_and_caps(repo, character, live)

    stats = character.to_character_stats()
    totals = character.primary_totals
    pool = sum(totals.values())
    scores: List[ElementScore] = []

    for element in ELEMENTS:
        stat_names = PRIMARY_STAT
        current_stats = stats
        pooled = replace(
            stats,
            strength=pool if element == "earth" else 0,
            intelligence=pool if element == "fire" else 0,
            chance=pool if element == "water" else 0,
            agility=pool if element == "air" else 0,
        )
        current_rotation, count, best_spell, best_grade = _rotation_for_element(
            repo, spells, current_stats, element, character.max_hp,
            character.ap_budget, off_element_weight=0.0)
        potential_rotation, potential_count, potential_best, potential_best_grade = (
            _rotation_for_element(
                repo, spells, pooled, element, character.max_hp,
                character.ap_budget, off_element_weight=0.0))
        use_count = max(count, potential_count)
        scores.append(ElementScore(
            element=element,
            primary_stat=stat_names[element],
            current_stat=totals.get(element, 0.0),
            pooled_stat=pool,
            current_damage=current_rotation.damage,
            current_value=current_rotation.value,
            potential_damage=potential_rotation.damage,
            potential_value=potential_rotation.value,
            damage_per_ap=potential_rotation.damage / max(1, potential_rotation.ap_used),
            spell_count=use_count,
            best_spell=potential_best or best_spell,
            best_grade=potential_best_grade or best_grade,
            current_rotation=current_rotation,
            potential_rotation=potential_rotation,
        ))

    scores.sort(key=lambda row: (-row.potential_damage, -row.current_damage, row.element))
    damaging = [score for score in scores if score.potential_damage > 0]
    dominant = character.dominant_element
    follow_existing = None
    if dominant:
        follow_existing = dominant
    elif damaging:
        follow_existing = max(damaging, key=lambda row: row.current_damage).element

    if not damaging:
        chosen = request if request in ELEMENTS else "earth"
        warnings.append(
            "No damaging class spell is available at this level; the element "
            "choice has no damage basis."
        )
        return ElementDecision(
            requested=request,
            chosen=chosen,
            reason="no damaging spell available at this level",
            follow_existing=follow_existing,
            respec_needed=False,
            scores=scores,
            warnings=warnings,
        )

    mode = request
    if mode in ELEMENTS:
        requested_score = next(score for score in scores if score.element == mode)
        if requested_score.potential_damage <= 0:
            warnings.append(
                "Requested element %s has no damaging spell at this level; "
                "using the best available element instead." % mode)
            mode = "auto"
        else:
            chosen = mode
            reason = "user requested %s" % mode

    if mode not in ("auto", "existing") and mode not in ELEMENTS:
        warnings.append("Unknown element request %r; using auto." % requested)
        mode = "auto"

    if mode == "existing":
        if follow_existing:
            chosen = follow_existing
            reason = "keeps the element with the most current damage"
        else:
            chosen = damaging[0].element
            reason = "no current element; picked the best damaging element"
    elif mode == "auto":
        best = damaging[0]
        current_best = max(damaging, key=lambda row: (row.current_damage, row.potential_damage))
        if follow_existing:
            dominant_score = next(score for score in scores
                                  if score.element == follow_existing)
            if dominant_score.element == best.element:
                chosen = best.element
                reason = "the existing element is also the best potential damage"
            elif dominant_score.potential_damage >= best.potential_damage * (1.0 - RESPEC_PREFERENCE):
                chosen = follow_existing
                reason = (
                    "%s is within %.0f%% of the best potential damage; keeping "
                    "the current element avoids a respec"
                    % (follow_existing, RESPEC_PREFERENCE * 100.0))
            elif current_best.element == best.element:
                chosen = best.element
                reason = "largest potential damage and already the best current element"
            else:
                chosen = best.element
                reason = "largest potential damage; %s would need a respec" % best.element
        else:
            chosen = best.element
            reason = "largest potential damage"

    chosen_score = next(score for score in scores if score.element == chosen)
    respec_needed = bool(dominant and chosen != dominant)
    if respec_needed:
        warnings.append(
            "The recommendation is not the current main stat (%s). Moving points "
            "needs a respec and is a human decision; the potential column assumes "
            "all primary points can be moved." % dominant)
    if chosen_score.spell_count == 0:
        warnings.append("The chosen element has no damaging spell in the database.")

    return ElementDecision(
        requested=request,
        chosen=chosen,
        reason=reason,
        follow_existing=follow_existing,
        respec_needed=respec_needed,
        scores=scores,
        warnings=warnings,
    )


def marginal_primary_damage(
    repo: Repository,
    character: CharacterSnapshot,
    element: str,
    points: int = 10,
) -> Dict[str, Any]:
    """Damage gained by moving N more points into each primary stat.

    The fight calculator is linear in the primary stat, but rounding, crits and
    range upgrades make a one-point probe noisy; ten points gives a readable
    slope. This is about the next characteristic point, not a full respec.
    """
    live, _synthetic, _warnings = effective_spell_states(repo, character)
    spells = _spells_and_caps(repo, character, live)
    stats = character.to_character_stats()
    out: Dict[str, Any] = {
        "stat_points_raw": character.points.get("statsPoints"),
        "primary": {},
        "vitality": {"hp_per_point": 1.0,
                     "note": "Touch gives 1 HP per vitality point; survival "
                             "weight is a human choice."},
        "wisdom": {"damage_per_point": 0.0,
                   "note": "wisdom buys experience and AP/MP resistance, not damage."},
    }
    for candidate_element in ELEMENTS:
        stat_name = PRIMARY_STAT[candidate_element]
        base = _rotation_for_element(
            repo, spells, stats, candidate_element, character.max_hp,
            character.ap_budget, off_element_weight=0.0)[0]
        bumped = replace(stats, **{stat_name: getattr(stats, stat_name) + points})
        after = _rotation_for_element(
            repo, spells, bumped, candidate_element, character.max_hp,
            character.ap_budget, off_element_weight=0.0)[0]
        out["primary"][candidate_element] = {
            "primary_stat": stat_name,
            "damage_per_point": round((after.damage - base.damage) / max(1, points), 3),
            "current_damage": round(base.damage, 2),
            "damage_after_%d" % points: round(after.damage, 2),
            "preferred": candidate_element == element,
        }
    return out
