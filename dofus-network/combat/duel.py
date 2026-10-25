"""V4.0: a 1v1 duel on real map cells with real class spell kits.

This is deliberately one step beyond v3, not the final game:

* initiative-ordered turns (not "all allies, then all enemies");
* real cell geometry, line of sight and MP costs from ``battle_solver``;
* real class spell kits from the Touch class-spell cache, with the build's
  focused-element damage folded into the spells (the v3 dataset row);
* AP/MP/HP/resists from the build;
* the battle solver generates legal move-and-cast plans; a policy chooses one;
* direct damage, steal and heal are applied; statuses, traps, summons, push
  and AP/MP removal are not modelled yet.

Both fighters use the same plan interface, which is what makes the value
lookahead policy symmetric and usable for PvP self-play. The build source can
be the v3 encounter rows now and Dofusbook builds later.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from combat.battle_solver import (BattleSolver, Board, CellMap, Fighter,
                                  LiveEffect, LiveSpell, TurnPlan)
from combat.encounter.build_dataset import ELEMENTS

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAP = REPO_ROOT / "data" / "maps" / "100139008.json"
DEFAULT_DATA = REPO_ROOT / "kaggle" / "tpu_selfplay" / "dataset" / "encounters.npz"

EFFECT_BY_ELEMENT = (100, 97, 99, 96, 98)
CLASS_NAMES = ("Feca", "Osamodas", "Enutrof", "Sram", "Xelor", "Ecaflip",
               "Eniripsa", "Iop", "Cra", "Sadida", "Sacrier", "Pandawa",
               "Rogue", "Masqueraider", "Foggernaut")
CLASS_INDEX = {name: index for index, name in enumerate(CLASS_NAMES)}


# ---------------------------------------------------------------------------
# build rows -> duelists
# ---------------------------------------------------------------------------


def load_map(path: Path = DEFAULT_MAP) -> CellMap:
    import json

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return CellMap(tuple(int(cell.get("l", 0)) for cell in payload.get("cells", [])))


def _live_spell(slot: int, ap: float, rmin: float, rmax: float,
                damage: float, heal: float, element: int,
                ally: bool) -> LiveSpell:
    if ally and heal > 0:
        effects = (LiveEffect(108, int(round(heal)), int(round(heal))),)
    elif damage > 0:
        effects = (LiveEffect(EFFECT_BY_ELEMENT[int(element) % 5],
                              int(round(damage)), int(round(damage))),)
    else:
        effects = ()
    return LiveSpell(
        id=1000 + slot, name=f"spell{slot}",
        ap_cost=max(1, int(round(ap))),
        min_range=max(0, int(round(rmin))),
        max_range=max(0, int(round(rmax))),
        cast_in_line=False, cast_in_diagonal=False, requires_los=True,
        need_free_cell=False, need_taken_cell=False,
        max_cast_per_turn=None, max_cast_per_target=None,
        min_cast_interval=0, casts_this_turn=0, castable=True,
        critical_hit_probability=0.0, effects=effects, critical_effects=(),
        is_weapon=False, zone_size=None, zone_shape=None,
    )


@dataclass
class Duelist:
    id: int
    row: int
    class_name: str
    level: int
    hp: float
    max_hp: float
    ap: float
    max_ap: float
    mp: float
    max_mp: float
    initiative: float
    cell: int
    resist: Tuple[Tuple[str, int], ...]
    spells: Tuple[LiveSpell, ...]
    stats: Dict[str, float] = field(default_factory=dict)
    resist_fixed: Tuple[Tuple[str, int], ...] = ()
    alive: bool = True

    @property
    def class_index(self) -> int:
        return CLASS_INDEX.get(self.class_name, 0)

    def resist_value(self, element: str) -> int:
        return dict(self.resist).get(element.lower(), 0)


def _resists(data: Any, row: int) -> Tuple[Tuple[str, int], ...]:
    return tuple((name, int(round(float(data["res"][row, 0, i]))))
                 for i, name in enumerate(ELEMENTS))


def duelist_from_row(data: Any, row: int, ident: int,
                     cell: int) -> Optional[Duelist]:
    damage = data["spell_damage"][row, 0]
    heal = data["spell_heal"][row, 0]
    if not np.any(damage > 0) and not np.any(heal > 0):
        return None
    spells = []
    for slot in range(damage.shape[0]):
        if damage[slot] <= 0 and heal[slot] <= 0:
            continue
        spells.append(_live_spell(
            slot, float(data["spell_ap"][row, 0, slot]),
            float(data["spell_rmin"][row, 0, slot]),
            float(data["spell_rmax"][row, 0, slot]),
            float(damage[slot]), float(heal[slot]),
            int(data["spell_element"][row, 0, slot]),
            bool(data["spell_ally"][row, 0, slot])))
    if not spells:
        return None
    class_name = (str(np.asarray(data["_classes"])[row])
                  if "_classes" in data.files else "Iop")
    return Duelist(
        id=ident, row=row, class_name=class_name,
        level=int(data["level"][row, 0]),
        hp=float(data["max_hp"][row, 0]),
        max_hp=float(max(1.0, data["max_hp"][row, 0])),
        ap=float(data["ap_max"][row, 0]),
        max_ap=float(max(1.0, data["ap_max"][row, 0])),
        mp=float(data["mp_max"][row, 0]),
        max_mp=float(max(1.0, data["mp_max"][row, 0])),
        initiative=float(data["initiative"][row, 0]),
        cell=cell, resist=_resists(data, row), spells=tuple(spells),
    )


def engaged_cells(cells: CellMap, rng: random.Random,
                  min_distance: int = 3, max_distance: int = 8
                  ) -> Optional[Tuple[int, int]]:
    walkable = [cell for cell in range(len(cells.flags))
                if cells.walkable(cell) and cells.sees(cell)]
    if len(walkable) < 2:
        return None
    for _ in range(20000):
        a, b = rng.sample(walkable, 2)
        distance = cells.distance(a, b)
        if (min_distance <= distance <= max_distance
                and cells.has_los(a, b)):
            return a, b
    return None


# ---------------------------------------------------------------------------
# duel state
# ---------------------------------------------------------------------------


@dataclass
class DuelState:
    fighters: Tuple[Duelist, Duelist]
    cells: CellMap
    order: Tuple[int, int]
    round: int = 1
    turn: int = 0
    done: bool = False
    winner: Optional[int] = None
    max_rounds: int = 12
    turns: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def actor(self) -> Duelist:
        return self.fighters[0] if self.order[self.turn % 2] == self.fighters[0].id \
            else self.fighters[1]

    @property
    def opponent(self) -> Duelist:
        return self.fighters[1] if self.actor is self.fighters[0] else self.fighters[0]

    def by_id(self, ident: int) -> Duelist:
        return self.fighters[0] if self.fighters[0].id == ident else self.fighters[1]


def make_duel(data: Any, row_a: int, row_b: int, cells: CellMap,
              rng: random.Random, max_rounds: int = 12
              ) -> Optional[DuelState]:
    pair = engaged_cells(cells, rng)
    if pair is None:
        return None
    a = duelist_from_row(data, row_a, 1, pair[0])
    b = duelist_from_row(data, row_b, 2, pair[1])
    if a is None or b is None:
        return None
    order = ((a.id, b.id) if a.initiative >= b.initiative else (b.id, a.id))
    if a.initiative == b.initiative and rng.random() < 0.5:
        order = (b.id, a.id)
    return DuelState(fighters=(a, b), cells=cells, order=order,
                     max_rounds=max_rounds)


# ---------------------------------------------------------------------------
# solver board and plan application
# ---------------------------------------------------------------------------


def _fighter(d: Duelist, team: str) -> Fighter:
    return Fighter(
        id=d.id, name=d.class_name, cell=d.cell, team=team,
        hp=int(round(d.hp)), max_hp=int(round(d.max_hp)),
        ap=int(round(d.ap)), mp=int(round(d.mp)), shield=0,
        level=d.level, alive=d.alive, by_me=team == "player",
        resist_percent=d.resist, resist_fixed=d.resist_fixed,
        critical_resistance=0, initiative=int(round(d.initiative)),
        max_ap=int(round(d.max_ap)), max_mp=int(round(d.max_mp)),
        spells=tuple(d.spells),
    )


def board_for(state: DuelState, actor: Duelist) -> Board:
    opponent = state.fighters[1] if actor is state.fighters[0] else state.fighters[0]
    stats = {"strength": 0, "intelligence": 0, "chance": 0, "agility": 0,
             "power": 0, "damage": 0, "critical_hit": 0, "heals": 0}
    stats.update(actor.stats or {})
    return Board(
        map_id=100139008, in_fight=True, in_preparation=False, is_my_turn=True,
        my_id=actor.id, my_cell=actor.cell, my_ap=int(round(actor.ap)),
        my_mp=int(round(actor.mp)),
        fighters=[_fighter(actor, "player"), _fighter(opponent, "enemy")],
        spells=list(actor.spells), my_stats=stats, cells=state.cells,
    )


def plans_for(state: DuelState, solver: BattleSolver, k: int = 6
              ) -> List[TurnPlan]:
    if state.done:
        return []
    board = board_for(state, state.actor)
    return solver.plan_candidates(board, k)


def apply_plan(state: DuelState, plan: TurnPlan) -> DuelState:
    """Apply the plan's HP/cell/AP/MP results and mark a winner."""
    actor = state.actor
    opponent = state.opponent
    for ident, hp in plan.final_hp.items():
        target = state.by_id(int(ident))
        target.hp = max(0.0, min(target.max_hp, float(hp)))
        target.alive = target.hp > 0.0
    actor.cell = int(plan.final_cell)
    actor.ap = max(0.0, float(plan.remaining_ap))
    actor.mp = max(0.0, float(plan.remaining_mp))
    if not actor.alive and not opponent.alive:
        state.done, state.winner = True, None
    elif not opponent.alive:
        state.done, state.winner = True, actor.id
    elif not actor.alive:
        state.done, state.winner = True, opponent.id
    return state


def advance_turn(state: DuelState) -> DuelState:
    if state.done:
        return state
    state.turn += 1
    if state.turn % 2 == 0:
        state.round += 1
    if state.round > state.max_rounds:
        a, b = state.fighters
        ha = a.hp / max(a.max_hp, 1.0)
        hb = b.hp / max(b.max_hp, 1.0)
        state.done = True
        state.winner = a.id if ha > hb else (b.id if hb > ha else None)
        return state
    state.actor.ap = state.actor.max_ap
    state.actor.mp = state.actor.max_mp
    state.opponent.ap = 0.0
    state.opponent.mp = 0.0
    return state


# ---------------------------------------------------------------------------
# features and policies
# ---------------------------------------------------------------------------


def _spell_summary(d: Duelist) -> Tuple[float, float, float, int]:
    damage = 0.0
    efficiency = 0.0
    heal = 0.0
    for spell in d.spells:
        for effect in spell.effects:
            if effect.kind in ("damage", "steal"):
                damage = max(damage, float(effect.max_roll))
                efficiency = max(efficiency, float(effect.max_roll) / max(1, spell.ap_cost))
            elif effect.kind == "heal":
                heal = max(heal, float(effect.max_roll))
    return damage, efficiency, heal, len(d.spells)


def _main_element(d: Duelist) -> str:
    best = None
    best_damage = -1.0
    for spell in d.spells:
        for effect in spell.effects:
            if effect.kind in ("damage", "steal") and effect.max_roll > best_damage:
                best_damage = float(effect.max_roll)
                best = effect.element
    return (best or "NEUTRAL").lower()


def state_features(state: DuelState, actor: Duelist) -> List[float]:
    opponent = state.fighters[1] if actor is state.fighters[0] else state.fighters[0]
    damage, efficiency, heal, count = _spell_summary(actor)
    element = _main_element(actor)
    main_resist = opponent.resist_value(element)
    distance = float(state.cells.distance(actor.cell, opponent.cell))
    los = 1.0 if state.cells.has_los(actor.cell, opponent.cell) else 0.0
    features = [
        actor.hp / max(actor.max_hp, 1.0),
        opponent.hp / max(opponent.max_hp, 1.0),
        (actor.hp / max(actor.max_hp, 1.0))
        - (opponent.hp / max(opponent.max_hp, 1.0)),
        actor.ap / max(actor.max_ap, 1.0),
        actor.mp / max(actor.max_mp, 1.0),
        opponent.ap / max(opponent.max_ap, 1.0),
        opponent.mp / max(opponent.max_mp, 1.0),
        actor.level / 200.0,
        opponent.level / 200.0,
        (actor.level - opponent.level) / 200.0,
        actor.initiative / 200.0,
        state.round / max(1, state.max_rounds),
        distance / 12.0,
        los,
        damage / 300.0,
        efficiency / 30.0,
        heal / 300.0,
        count / 4.0,
        float(main_resist) / 100.0,
        float(np.mean([value for _name, value in actor.resist])) / 100.0,
        float(np.mean([value for _name, value in opponent.resist])) / 100.0,
    ]
    actor_class = [0.0] * len(CLASS_NAMES)
    opponent_class = [0.0] * len(CLASS_NAMES)
    actor_class[actor.class_index] = 1.0
    opponent_class[opponent.class_index] = 1.0
    return features + actor_class + opponent_class


FEATURE_NAMES = [
    "actor_hp", "opp_hp", "hp_diff", "actor_ap", "actor_mp",
    "opp_ap", "opp_mp", "actor_level", "opp_level", "level_diff",
    "initiative", "round", "distance", "los", "best_damage",
    "damage_per_ap", "heal", "spell_count", "main_element_resist",
    "actor_resist_mean", "opp_resist_mean",
] + [f"actor_class_{name}" for name in CLASS_NAMES] \
  + [f"opp_class_{name}" for name in CLASS_NAMES]


def lookahead_features(state: DuelState, actor: Duelist) -> List[float]:
    """Features of the state after a plan, with the opponent about to act.

    The value model must see the opponent's full AP/MP, not the zeroed values
    left over from the actor's turn. The acting actor is still the reference
    for the win label, so we only swap the resource fields.
    """
    opponent = state.fighters[1] if actor is state.fighters[0] else state.fighters[0]
    saved = (actor.ap, actor.mp, opponent.ap, opponent.mp)
    actor.ap, actor.mp = 0.0, 0.0
    opponent.ap, opponent.mp = opponent.max_ap, opponent.max_mp
    features = state_features(state, actor)
    actor.ap, actor.mp, opponent.ap, opponent.mp = saved
    return features


def plan_value(features: List[float], model) -> float:
    if model is None:
        return 0.0
    try:
        probability = model.predict_proba([features])[0][1]
    except Exception:
        return 0.0
    return float(probability)


def solver_policy(state: DuelState, actor: Duelist,
                  plans: Sequence[TurnPlan], rng: random.Random,
                  model=None) -> TurnPlan:
    return plans[0]


def random_policy(state: DuelState, actor: Duelist,
                  plans: Sequence[TurnPlan], rng: random.Random,
                  model=None) -> TurnPlan:
    return rng.choice(list(plans))


def plan_scores(state: DuelState, actor: Duelist,
                plans: Sequence[TurnPlan], model, tie_break: float = 1e-4
                ) -> List[float]:
    scores: List[float] = []
    for plan in plans:
        clone = copy.deepcopy(state)
        apply_plan(clone, plan)
        if clone.done:
            score = 1.0 if clone.winner == actor.id else 0.0
        else:
            score = plan_value(lookahead_features(clone, actor), model)
        scores.append(score + tie_break * min(plan.score, 1000.0))
    return scores


def value_policy(state: DuelState, actor: Duelist,
                 plans: Sequence[TurnPlan], rng: random.Random,
                 model=None, tie_break: float = 1e-4) -> TurnPlan:
    scores = plan_scores(state, actor, plans, model, tie_break)
    return plans[max(range(len(plans)), key=lambda index: scores[index])]


def make_residual_policy(model, margin: float = 0.05):
    """Keep the solver unless the value head is confidently better."""
    def policy(state: DuelState, actor: Duelist, plans: Sequence[TurnPlan],
               rng: random.Random, _model=None) -> TurnPlan:
        scores = plan_scores(state, actor, plans, model)
        best = max(range(len(plans)), key=lambda index: scores[index])
        if best == 0 or scores[best] <= scores[0] + margin:
            return plans[0]
        return plans[best]
    return policy


def make_two_ply_policy(solver: BattleSolver, opponent_k: int = 4,
                        tie_break: float = 1e-4):
    """Choose a plan after the opponent's greedy reply.

    The solver's own score is one-ply. This policy applies each candidate,
    lets the opponent use the solver's best plan for its next turn, and ranks
    the resulting HP position. It is a tactical baseline, not a learned one.
    """
    def policy(state: DuelState, actor: Duelist, plans: Sequence[TurnPlan],
               rng: random.Random, model=None) -> TurnPlan:
        best = plans[0]
        best_score = -1.0e9
        for plan in plans:
            clone = copy.deepcopy(state)
            apply_plan(clone, plan)
            if clone.done:
                score = 1.0 if clone.winner == actor.id else 0.0
            else:
                clone = advance_turn(clone)
                opponent_plans = plans_for(clone, solver, opponent_k)
                if opponent_plans:
                    apply_plan(clone, opponent_plans[0])
                if clone.done:
                    score = 1.0 if clone.winner == actor.id else 0.0
                else:
                    mine = clone.by_id(actor.id)
                    theirs = (clone.fighters[1]
                              if clone.fighters[0].id == actor.id
                              else clone.fighters[0])
                    score = (mine.hp / max(mine.max_hp, 1.0)
                             - theirs.hp / max(theirs.max_hp, 1.0))
            score += tie_break * min(plan.score, 1000.0)
            if score > best_score:
                best_score = score
                best = plan
        return best
    return policy


Policy = Callable[[DuelState, Duelist, Sequence[TurnPlan], random.Random, Any],
                  TurnPlan]


# ---------------------------------------------------------------------------
# game loop and data capture
# ---------------------------------------------------------------------------


def play_game(state: DuelState, policies: Dict[int, Policy],
              solver: BattleSolver, rng: random.Random,
              model=None, k: int = 6, capture: bool = False
              ) -> Tuple[DuelState, List[Dict[str, Any]]]:
    records: List[Dict[str, Any]] = []
    winner: Optional[int] = None
    while not state.done:
        state.turns += 1
        actor = state.actor
        plans = plans_for(state, solver, k)
        if not plans:
            state.done = True
            break
        policy = policies.get(actor.id, solver_policy)
        plan = policy(state, actor, plans, rng, model)
        pre = state_features(state, actor) if capture else None
        before_hp = (state.fighters[0].hp, state.fighters[1].hp)
        apply_plan(state, plan)
        if capture:
            record = {
                "round": state.round,
                "actor": actor.id,
                "actor_class": actor.class_name,
                "opponent_class": state.fighters[1].class_name
                if actor is state.fighters[0] else state.fighters[0].class_name,
                "actor_level": actor.level,
                "opponent_level": (state.fighters[1].level
                                   if actor is state.fighters[0]
                                   else state.fighters[0].level),
                "plan_kind": plan.actions[0].kind if plan.actions else "pass",
                "plan_score": float(plan.score),
                "damage": float(plan.expected_damage),
                "kills": len(plan.kills),
                "pre_features": pre,
                "post_features": lookahead_features(state, actor),
                "hp_before": before_hp,
                "hp_after": (state.fighters[0].hp, state.fighters[1].hp),
            }
            records.append(record)
        if state.done:
            winner = state.winner
            break
        advance_turn(state)
        if state.done:
            winner = state.winner
            break
    return state, records
