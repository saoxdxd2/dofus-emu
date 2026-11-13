"""Live PPO/PBT actor: bind the current board to the encounter slots and act.

The checkpoint was trained on an abstract 1v4 encounter with four spell slots
and four enemy slots. Live characters are not fixed instances: their class,
level, spells, AP/MP and enemies change every fight. This module rebuilds the
binding from the board on every request:

* the four slots are the live character's own spells, selected by damage/heal
  impact and never by a hardcoded spell id;
* the enemy slots are the live enemies in board order, padded/masked;
* the policy chooses a spell slot, a target slot and a move direction;
* the cell solver realizes that intent with real cells, LoS and MP.

The policy is the actor; the solver is the legality/geometry layer and also the
fallback when no candidate plan can express the chosen spell/target.
"""

from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, FrozenSet, List, Optional, Sequence, Tuple

from combat.encounter.backend import Array, XP, at, jit
import numpy as np

from combat.battle_solver import (BattleSolver, Board, Fighter, LiveSpell,
                                  TurnPlan)
from combat.encounter import model
from combat.encounter.checkpoint import load_params_config
from combat.encounter.env import action_masks, initial_state, observe
from combat.observation import live_view
from combat.grid_infer import rank_plans as grid_rank_plans
from combat.plan_value import (board_encounter, _empty_spell_row,
                               _player_spell_rows)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHECKPOINT = REPO_ROOT / "models" / "encounter_selfplay.pkl"

# The 30M grid ranker is OFF by default, and that is a measured decision rather
# than caution. On 121 distinct recorded boards, against the solver it was
# distilled from (scratch/measure_ranker_quality.py, 2026-11-11):
#
#   picks the solver's own top plan     77.7%
#   picks any plan the solver ties best 90.9%   (a random pick gets 49.4%)
#   strictly worse pick                  9.1% of boards
#   aggregate solver score              -3.31%
#   Spearman against the teacher         +0.77
#   cost                                292 ms/board vs 0.4 ms for the solver
#
# So it is a 78%-faithful copy of its teacher that is 3.3% worse and ~700x more
# expensive. A distilled ranker cannot beat the model that produced its labels,
# and this one does not: it can only match the solver or lose to it. It stays
# behind this switch until a benchmark shows it winning, and the checkpoint
# loading is not evidence of that -- loading only means the tensor widths agree.
GRID_RANKER_ENABLED = (os.environ.get("DOFUS_GRID_RANKER", "")
                       .strip().lower() in ("1", "on", "true", "yes"))

# Re-rank the solver's candidate plans by the HP differential they produce,
# instead of by its weighted score. No model involved: `plan.final_hp` is already
# the search's own projection, so this only changes which projection is maximised.
#
# Measured, 600 paired duels against the solver's own choice on level-200 builds
# (`python -m combat.duel_value --evaluate`, side-swapped and board-paired):
#
#   boards where the two policies differ   121 / 600
#   boards the HP ranking wins              104
#   boards it loses                          17
#   mean final HP differential            +0.0113   (95% CI +0.0072 .. +0.0154)
#
# On by default because unlike the grid ranker this one was measured *winning*.
# The caveat is scope: those are 1v1 duels on the duel harness. The sum over each
# side generalises to multi-enemy fights, but it has not been measured there, so
# `DOFUS_COMBAT_HP_RANK=off` restores the old choice exactly.
HP_RANK_ENABLED = (os.environ.get("DOFUS_COMBAT_HP_RANK", "on")
                   .strip().lower() not in ("0", "off", "false", "no"))

# What one point of expected damage is worth against one cell of closing distance
# when the actor's cast already decides the turn and only the cell is open.
#
# 0.01 keeps the term under a full cell even at `threat.MAX_PRICED_SWING` (60 points
# -> 0.6), so the direction the policy asked for is never overruled by cover: this is
# a tie-break between cells at the same distance, not a second engagement decision.
# The beam search prices the same quantity at `threat.POSITION_WEIGHT` (0.45) per
# point against `APPROACH_VALUE` (0.5) per cell, which is where "do not walk into the
# turn" is enforced; by the time a plan reaches here that choice has been made and
# the question is only which of several castable cells to end on.
EXPOSURE_TIEBREAK = 0.01


class LivePolicy:
    """Stateless per-request slot binding around the trained encounter policy."""

    def __init__(self, checkpoint: Optional[Path] = None,
                 beam_width: int = 8, max_actions: int = 4) -> None:
        self.checkpoint = Path(checkpoint or DEFAULT_CHECKPOINT)
        self.params, self.cfg = load_params_config(self.checkpoint)
        # The learned per-monster table, passed as a callable rather than a value so
        # the solver sees the rows this fight has just taught it. `battle_solver`
        # defaults to the level prior, which is what keeps every benchmark and test
        # independent of whatever `models/threat_model.json` happens to contain.
        from combat import threat as _threat
        from combat import live_learn as _live_learn

        self.solver = BattleSolver(beam_width=int(beam_width),
                                   max_actions=int(max_actions),
                                   memory=_threat.default_memory,
                                   enemy_turn_price=_live_learn.enemy_turn_price,
                                   damage_calibration=_live_learn.default_damage_calibration)
        self.plan_k = max(2, int(beam_width))
        # Set per request from the observation contract; False until then.
        self._value_ready = False
        # The state/masks/observation/value path is pure JAX. Compiling the
        # bundle once removes the per-turn eager dispatch overhead; the board
        # values still enter as traced arrays, so nothing about the instance is
        # baked into the graph.
        self._bundle = jit(self._forward_bundle)
        self._select = jit(self._select_impl)

    # -- binding -------------------------------------------------------------

    def _current_plan(self, board: Board) -> TurnPlan:
        final_hp = {fighter.id: int(fighter.hp) for fighter in board.fighters}
        return TurnPlan(
            actions=[], final_cell=board.my_cell, remaining_ap=int(board.my_ap),
            remaining_mp=float(board.my_mp), expected_damage=0.0,
            expected_healing=0.0, kills=[], score=0.0, final_hp=final_hp)

    def _bind_slots(self, board: Board
                    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        plan = self._current_plan(board)
        rows = _player_spell_rows(board, board.character_stats(), plan)
        by_id = {spell.id: spell for spell in board.spells}

        def usable(row: Dict[str, Any]) -> bool:
            spell = by_id.get(row["id"])
            if spell is None or not spell.castable:
                return False
            if (spell.max_cast_per_turn is not None
                    and spell.casts_this_turn >= spell.max_cast_per_turn):
                return False
            return True

        damage = [row for row in rows
                  if not row["ally"] and row["damage"] > 0 and usable(row)]
        heals = [row for row in rows
                 if row["ally"] and row["heal"] > 0 and usable(row)]
        damage.sort(key=lambda row: (row["damage"] / max(row["ap"], 1.0),
                                     row["damage"]), reverse=True)
        heals.sort(key=lambda row: (row["heal"] / max(row["ap"], 1.0),
                                    row["heal"]), reverse=True)
        chosen: List[Dict[str, Any]] = (damage[:3] + heals[:1]) if heals \
            else damage[:4]
        if len(chosen) < 4:
            for row in damage + heals:
                if row not in chosen:
                    chosen.append(row)
                if len(chosen) == 4:
                    break
        chosen = chosen[:4]
        chosen.extend(_empty_spell_row() for _ in range(4 - len(chosen)))
        names = {sid: spell.name for sid, spell in by_id.items()}
        slots = [{
            "slot": slot, "spell_id": int(row["id"]),
            "name": "" if row.get("empty") else names.get(row["id"], ""),
            "ap": row["ap"], "rmin": row["rmin"], "rmax": row["rmax"],
            "damage": round(float(row["damage"]), 2),
            "heal": round(float(row["heal"]), 2),
            "element": int(row["element"]), "ally": bool(row["ally"]),
            "empty": bool(row.get("empty", False)),
        } for slot, row in enumerate(chosen)]
        return slots, chosen

    def _bind_targets(self, board: Board) -> List[Dict[str, Any]]:
        targets: List[Dict[str, Any]] = []

        def entry(slot: int, fighter) -> Dict[str, Any]:
            if fighter is None:
                return {"slot": slot, "fighter_id": None, "name": "",
                        "cell": None, "hp": None, "max_hp": None,
                        "alive": False, "level": None}
            return {
                "slot": slot, "fighter_id": int(fighter.id),
                "name": fighter.name, "cell": int(fighter.cell),
                "hp": int(fighter.hp), "max_hp": int(fighter.max_hp),
                "alive": bool(fighter.alive), "level": int(fighter.level),
            }

        targets.append(entry(0, board.me))
        for slot, fighter in enumerate(board.enemies[:self.cfg.enemies], start=1):
            targets.append(entry(slot, fighter))
        while len(targets) < self.cfg.n_actors:
            targets.append(entry(len(targets), None))
        return targets

    # -- policy --------------------------------------------------------------

    def _forward_bundle(self, params, spec, tables, current_ap: float,
                        current_mp: float, round_: int):
        """Initial state + masks + observation + logits as one jitted program.

        The jit is the backend's: compiled on the training box, a plain call on an installed copy
        that has no jax at all (combat/encounter/backend.py). `at(...)` is the indexed update the
        numpy fallback has to provide itself, because jax arrays are immutable and numpy's are not.
        """
        state = initial_state(spec, tables, self.cfg)
        state["ap"] = at(state["ap"])[:, 0].set(current_ap)
        state["mp"] = at(state["mp"])[:, 0].set(current_mp)
        state["round"] = XP.asarray([round_], dtype=XP.int32)
        masks = action_masks(state, tables, self.cfg)
        obs, glob, attention = observe(
            state, tables, self.cfg,
            acting_team=XP.zeros((1,), dtype=XP.int32))
        return model.forward(params, obs, glob, attention), masks

    def _select_impl(self, logits: Dict[str, Array],
                     masks: Dict[str, Array]) -> Array:
        """All argmax/softmax work on device; one small transfer to the host.

        The old path moved six arrays to NumPy separately, which cost more than
        the model forward itself on a CPU-sized board.
        """
        spell_scores = XP.where(masks["spell"][0, 0], logits["spell"][0, 0],
                                 -1e9)
        spell_slot = XP.argmax(spell_scores)
        finite = XP.exp(spell_scores - XP.max(spell_scores))
        probability = finite[spell_slot] / XP.sum(finite)

        move_scores = XP.where(masks["move"][0, 0], logits["move"][0, 0], -1e9)
        move = XP.where(XP.any(masks["move"][0, 0]),
                         XP.argmax(move_scores), 1)

        target_scores = XP.where(masks["target"][0, 0],
                                  logits["target"][0, 0], -1e9)
        target_any = XP.any(masks["target"][0, 0], axis=-1)
        target_choice = XP.argmax(target_scores, axis=-1)[spell_slot]
        target_slot = XP.where(
            spell_slot < self.cfg.spells,
            XP.where(target_any[spell_slot], target_choice, -1),
            0,
        )
        value = logits["value"][0]
        return XP.stack([move, spell_slot, target_slot,
                          probability, value]).astype(XP.float32)

    def _choose(self, logits: Dict[str, Array],
                masks: Dict[str, Array]
                ) -> Tuple[int, int, int, float, float]:
        values = np.asarray(self._select(logits, masks), dtype=np.float64)
        return (int(values[0]), int(values[1]), int(values[2]),
                float(values[3]), float(values[4]))

    def _path_to(self, board: Board, start: int, goal: Optional[int]
                 ) -> List[int]:
        if goal is None or start == goal:
            return [start] if goal == start else []
        occupied = {fighter.cell for fighter in board.fighters
                    if fighter.alive and fighter.cell not in (start, goal)}
        parent: Dict[int, Optional[int]] = {start: None}
        queue: Deque[int] = deque([start])
        while queue:
            cell = queue.popleft()
            if cell == goal:
                break
            for neighbour in board.cells.neighbours(cell):
                if neighbour in parent or neighbour in occupied:
                    continue
                if not board.cells.walkable(neighbour):
                    continue
                parent[neighbour] = cell
                queue.append(neighbour)
        if goal not in parent:
            return []
        path: List[int] = []
        current: Optional[int] = goal
        while current is not None:
            path.append(current)
            current = parent[current]
        return list(reversed(path))

    def _action_for_plan(self, board: Board, plan: TurnPlan) -> Dict[str, Any]:
        if not plan.actions or plan.actions[0].kind == "pass":
            return {"type": "pass"}
        first = plan.actions[0]
        if first.kind == "cast":
            return {"type": "cast", "spellId": int(first.spell_id),
                    "targetId": int(first.target_id) if first.target_id is not None else None,
                    "cell": int(first.target_cell) if first.target_cell is not None else None}
        if first.kind == "move":
            cell = int(first.move_to) if first.move_to is not None else board.my_cell
            return {"type": "move", "cell": cell,
                    "path": self._path_to(board, board.my_cell, cell)}
        return {"type": "pass"}

    def _move_score(self, board: Board, plan: TurnPlan,
                    target: Dict[str, Any], move: int) -> float:
        if target.get("fighter_id") in (None, board.my_id):
            return 0.0
        old = board.cells.distance(board.my_cell, int(target["cell"]))
        new = board.cells.distance(int(plan.final_cell), int(target["cell"]))
        delta = new - old
        # The direction is the actor's intent and stays dominant; the exposure
        # of the cell the plan ends on only decides between plans that keep the
        # same distance. Capped below one cell of distance on purpose.
        exposure = EXPOSURE_TIEBREAK * float(getattr(plan, "exposure", 0.0) or 0.0)
        # 0 is approach and 2 is retreat -- the same codes `_walk_action` reads, where
        # approach keeps the hops with `distance < here` and retreat the ones with
        # `distance > here`. These two branches were written the other way round, so a
        # plan that was asked to back off was scored by how far it *closed*: the actor
        # chose retreat, `matching` offered one plan per reachable cell, and this picked
        # the cell nearest the enemy. Measured live 2026-11-10 on the board that lost the
        # fight: `move` 2 with a covered cell two hops back, and the character walked
        # into the two-monster turn instead.
        if move == 0:                       # approach: end nearer
            return -float(delta) - exposure
        if move == 2:                       # retreat: end farther
            return float(delta) - exposure
        return -float(abs(delta)) - exposure

    def _castable_pair(self, board: Board, slot: Dict[str, Any],
                       target: Dict[str, Any]
                       ) -> Optional[Tuple[LiveSpell, Fighter, int]]:
        """The chosen spell/target, or None when the pair itself is invalid."""
        spell_id = int(slot["spell_id"])
        spell = next((s for s in board.spells if s.id == spell_id), None)
        if spell is None or not spell.castable or spell.need_free_cell:
            return None
        if spell.ap_cost <= 0 or spell.ap_cost > board.my_ap:
            return None
        if (spell.max_cast_per_turn is not None
                and spell.casts_this_turn >= spell.max_cast_per_turn):
            return None
        if spell.min_cast_interval > 0 and spell.casts_this_turn > 0:
            return None
        target_id = int(target["fighter_id"])
        fighter = next((f for f in board.fighters if f.id == target_id), None)
        if fighter is None or not fighter.alive or target.get("cell") is None:
            return None
        is_enemy = fighter.team != "player" and fighter.id != board.my_id
        # Only damaging casts belong on enemies and only pure heals on our
        # side: a hybrid damage+heal spell on an ally would hurt them.
        if is_enemy:
            if not spell.has_damage:
                return None
        elif spell.has_damage:
            return None
        if spell.need_taken_cell and fighter.cell not in {
                f.cell for f in board.fighters
                if f.alive and f.id != board.my_id}:
            return None
        return spell, fighter, int(target["cell"])

    @staticmethod
    def _legal(board: Board, spell: LiveSpell, origin: int, target_cell: int,
               occupied: FrozenSet[int]) -> bool:
        """Range bracket, ring and line of sight for one cast position."""
        distance = board.cells.distance(origin, target_cell)
        if distance < spell.min_range or distance > spell.max_range:
            return False
        if target_cell not in board.cells.range_cells(
                origin, spell.min_range, spell.max_range,
                spell.cast_in_line, spell.cast_in_diagonal):
            return False
        return not spell.requires_los or board.cells.has_los(
            origin, target_cell, occupied)

    def _last_resort_cast(self, board: Board, slots: Sequence[Dict[str, Any]],
                          targets: Sequence[Dict[str, Any]]
                          ) -> Optional[Dict[str, Any]]:
        """The best cast that lands from where the character is already standing.

        Called only when a turn was about to end without spending any AP. Measured
        on the 1009 live decisions in `trajectories/live_policy_decisions.jsonl`:
        of the 850 turns that had a castable spell in hand, **355 (42%) ended in a
        pass**, and 146 of those were holding a 38-damage spell at the time. The
        path that produced them is ordinary -- the actor head chose to walk, the
        walk was impossible because the MP was already spent, and the solver had no
        plan left -- and every one of those turns threw away the whole AP pool.

        Two rules, in this order:

        1. **A kill beats a hit.** Among all legal (spell, target) pairs, take one
           that finishes a target, cheapest first so the AP left over still buys a
           second cast. Removing an enemy deletes a whole enemy turn from the rest
           of the fight, which is worth more than any single damage number.
        2. **Otherwise focus.** Hit the enemy with the least life left, with the
           strongest spell that reaches it. Spreading damage across three monsters
           is what the fallback did on the live fight of 2026-11-10: 233 damage
           dealt, three enemies still standing, and a loss.
        """
        enemies = [t for t in targets
                   if t.get("fighter_id") is not None and t.get("alive")
                   and t.get("cell") is not None
                   and int(t["fighter_id"]) != int(board.my_id)]
        if not enemies:
            return None
        ranked_slots = sorted(slots, key=lambda s: -float(s.get("damage") or 0.0))

        kills: List[Tuple[float, float, Dict[str, Any]]] = []
        for target in enemies:
            hp = target.get("hp")
            if hp is None:
                continue
            for slot in ranked_slots:
                if float(slot.get("damage") or 0.0) < float(hp):
                    continue
                action = self._direct_cast(board, slot, target)
                if action is not None:
                    kills.append((float(slot.get("ap") or 0.0), float(hp), action))
        if kills:
            kills.sort(key=lambda row: (row[0], row[1]))
            return kills[0][2]

        weakest = sorted(enemies, key=lambda t: (t.get("hp") is None,
                                                 t.get("hp") or 0))
        for target in weakest:
            for slot in ranked_slots:
                if float(slot.get("damage") or 0.0) <= 0.0:
                    continue
                action = self._direct_cast(board, slot, target)
                if action is not None:
                    return action
        return None

    def _direct_cast(self, board: Board, slot: Dict[str, Any],
                     target: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """A legal cast of the actor's own choice needs no beam search.

        The policy has already chosen the spell and target; the solver exists
        to find a legal realization. When the target is in range and visible
        from the current cell, that realization is exactly one cast.
        """
        pair = self._castable_pair(board, slot, target)
        if pair is None:
            return None
        spell, fighter, target_cell = pair
        occupied = frozenset(f.cell for f in board.fighters
                             if f.alive and f.id != board.my_id)
        if not self._legal(board, spell, board.my_cell, target_cell, occupied):
            return None
        return {"type": "cast", "spellId": spell.id,
                "targetId": fighter.id, "cell": target_cell}

    def _cast_cell(self, board: Board, slot: Dict[str, Any],
                   target: Dict[str, Any]) -> Optional[int]:
        """Cheapest reachable cell from which the chosen cast is legal.

        Used when the target is out of range from the current cell: walk to
        that cell now, then the next decision casts directly. This is the
        same legality check without running a full turn search.
        """
        pair = self._castable_pair(board, slot, target)
        if pair is None:
            return None
        spell, _fighter, target_cell = pair
        occupied = frozenset(f.cell for f in board.fighters
                             if f.alive and f.id != board.my_id)
        for cell in board.cells.reachable(board.my_cell, board.my_mp,
                                          occupied):
            if cell != board.my_cell and self._legal(
                    board, spell, cell, target_cell, occupied):
                return cell
        return None

    def _move_dest(self, board: Board, target: Optional[Dict[str, Any]],
                   move: int) -> Optional[int]:
        """The reachable cell the policy's move direction asks for.

        The trained actor moves every turn after casting: `0` toward the target,
        `2` away from it, `1` holds. The encounter env applies that as a straight
        change of distance; here it is a walk on the real cell graph, spending the
        turn's MP. Only a strict improvement counts, so a wall that blocks the
        direction leaves the character where it is instead of shuffling sideways.
        Without this the move head was never executed live: every turn ended in a
        pass once AP ran out, and the page's local heuristic walked forward.
        """
        if move not in (0, 2) or board.my_mp <= 0:
            return None
        cell = target.get("cell") if isinstance(target, dict) else None
        try:
            cell = int(cell)
        except (TypeError, ValueError):
            return None
        occupied = frozenset(f.cell for f in board.fighters
                             if f.alive and f.id != board.my_id)
        here = board.cells.distance(board.my_cell, cell)
        try:
            from combat import threat as _threat

            danger = _threat.build(board, memory=_threat.default_memory(),
                                   occupied=occupied)
        except Exception:  # noqa: BLE001 - a missing price must not cost the move
            danger = None
        best: Optional[int] = None
        best_key: Optional[tuple] = None
        for hop in board.cells.reachable(board.my_cell, board.my_mp, occupied):
            if hop == board.my_cell:
                continue
            distance = board.cells.distance(hop, cell)
            if move == 0:
                if distance >= here:
                    continue
                direction = distance
            else:
                if distance <= here:
                    continue
                direction = -distance
            # Same direction, quieter cell. Several cells usually tie on the
            # distance the actor asked for, and taking the first one is what
            # walked the character into the open: on the recorded boards two of
            # four enemies had no line of sight to it when it decided to close.
            quiet = danger.at(hop) if danger is not None else 0.0
            key = (direction, quiet)
            if best_key is None or key < best_key:
                best, best_key = hop, key
        return best

    @staticmethod
    def _reference_target(board: Board,
                          target: Optional[Dict[str, Any]]
                          ) -> Optional[Dict[str, Any]]:
        """The enemy the move direction is measured against.

        A pass carries no enemy target (the actor slot points at itself), and the
        env's distance is to the nearest enemy, so that is the reference then.
        """
        if isinstance(target, dict):
            fighter_id = target.get("fighter_id")
            try:
                fighter_id = int(fighter_id)
            except (TypeError, ValueError):
                fighter_id = None
            if fighter_id is not None and target.get("cell") is not None \
                    and fighter_id != board.my_id and any(
                        f.id == fighter_id and f.alive and f.team != "player"
                        for f in board.fighters):
                return target
        nearest = None
        nearest_distance = None
        for fighter in board.fighters:
            if not fighter.alive or fighter.id == board.my_id \
                    or fighter.team == "player":
                continue
            distance = board.cells.distance(board.my_cell, fighter.cell)
            if nearest_distance is None or distance < nearest_distance:
                nearest, nearest_distance = fighter, distance
        if nearest is None:
            return None
        return {"fighter_id": nearest.id, "cell": nearest.cell}

    def _walk_action(self, board: Board, reference: Optional[Dict[str, Any]],
                     move: int) -> Optional[Dict[str, Any]]:
        dest = self._move_dest(board, reference, move)
        if dest is None:
            return None
        path = self._path_to(board, board.my_cell, dest)
        if len(path) <= 1:
            return None
        return {"type": "move", "cell": int(dest), "path": path}

    @property
    def value_choice_stats(self) -> Dict[str, int]:
        """How often the critic was asked to pick a realization, and how often it did.

        `_realize` reaches `_value_choice` only when the actor's cast cannot land from any cell the
        MP reaches, so the branch is easy to believe is live and easy to never execute. The counters
        are what tell the two apart: `fired == 0` over a run means the trained value head is not in
        the realization path at all, which is a fact about the shipped rule rather than about one
        measurement.
        """
        return {"asked": int(getattr(self, "_vc_asked", 0)),
                "fired": int(getattr(self, "_vc_fired", 0))}

    def _value_choice(self, board: Board,
                      plans: Sequence[TurnPlan]) -> Optional[TurnPlan]:
        """The realization the trained critic expects to win.

        Several plans can execute the actor's chosen spell and target from
        different cells; `_move_score` only knows distance. The value head was
        trained on fight outcomes, so when the observation is value-ready (the
        enemy's own spells are loaded) it is the better judge. Not ready, or any
        failure, returns None and the caller keeps the old choice -- the value
        ranker is an upgrade, never a dependency.
        """
        if not getattr(self, "_value_ready", False) or len(plans) < 2:
            return None
        # Counted before the ranking, not after: a ranker that raises still reached this branch, and
        # "never reached" versus "reached and gave up" are different findings.
        self._vc_asked = getattr(self, "_vc_asked", 0) + 1
        try:
            from combat.plan_value import rank_plans

            ranked = rank_plans(self.params, board, plans, self.cfg)
        except Exception:  # noqa: BLE001 - a ranker failure must not cost the turn
            return None
        picked = ranked[0][1] if ranked else None
        self._vc_fired = getattr(self, "_vc_fired", 0) + int(picked is not None)
        return picked


    def _grid_choice(self, board: Board,
                     plans: Sequence[TurnPlan]) -> Optional[TurnPlan]:
        """The realization the 560-cell grid ranker expects to win.

        The grid model (``models/combat_grid/model.pkl``) scores each legal plan
        from the full board layout -- cell occupancy, LoS, distance, visibility --
        which is complementary to the encounter value head (fighter tokens only).
        It loads through the numpy backend on the embedded runtime, so no jax
        wheel is required. Returns None when the checkpoint is absent or the
        backend is unavailable; the caller falls through to the distance heuristic.
        The ranker is an upgrade, never a dependency.
        """
        if len(plans) < 2:
            return None
        if not GRID_RANKER_ENABLED:
            # Measured worse than the solver and ~700x slower; see the constant.
            # Returning None is the caller's fall-through to the solver's own
            # order, which is never worse than the teacher by definition.
            return None
        self._gc_asked = getattr(self, "_gc_asked", 0) + 1
        try:
            ranked = grid_rank_plans(board, plans)
        except Exception:  # noqa: BLE001 - a ranker failure must not cost the turn
            return None
        if ranked is None:
            return None
        # `rank_plans` answers `(list of (score, plan), metadata)`. This read
        # `ranked[0][1]`, which is the *second* entry of that list -- a `(score, plan)`
        # tuple, not a plan -- so even with the ranker working it would have handed the
        # caller a tuple where a `TurnPlan` belongs.
        order, _meta = ranked
        picked = order[0][1] if order else None
        self._gc_fired = getattr(self, "_gc_fired", 0) + int(picked is not None)
        return picked

    def _realize(self, board: Board, spell_slot: int, target_slot: int,
                 move: int, slots: Sequence[Dict[str, Any]],
                 targets: Sequence[Dict[str, Any]]
                 ) -> Tuple[Optional[Dict[str, Any]], bool, str]:
        reference = self._reference_target(
            board, targets[target_slot] if 0 <= target_slot < len(targets) else None)
        if spell_slot == self.cfg.spells:
            action = self._walk_action(board, reference, move)
            if action is not None:
                return (action, False,
                        "policy_approach" if move == 0 else "policy_retreat")
            # The actor chose a direction, not a pass; the walk is impossible
            # from here (no MP, or every cell that way is blocked). Ending the
            # turn standing still while a legal cast exists is the waste the
            # recorded boards show (31 pass turns of 116 asked). The solver
            # outranks the actor when the actor's own choice cannot execute,
            # which is the same rule the spell path below already follows.
            # A stub without a solver (binding tests) simply passes.
            solver = getattr(self, "solver", None)
            plans = solver.plan_candidates(board, self.plan_k) if solver else []
            fallback = next((plan for plan in plans
                             if plan.actions and plan.actions[0].kind != "pass"), None)
            if fallback is not None:
                return self._emit(board, fallback, True, "solver_fallback", "solver_move")
            return {"type": "pass"}, False, "policy_pass"
        if spell_slot < 0 or spell_slot >= len(slots):
            return None, True, "slot_out_of_range"
        slot = slots[spell_slot]
        # Padding and a real weapon share id 0; only the flag may answer this.
        if slot.get("empty") or (
                int(slot.get("spell_id") or 0) == 0
                and float(slot.get("damage") or 0.0) <= 0
                and float(slot.get("heal") or 0.0) <= 0):
            return None, True, "empty_spell_slot"
        if target_slot < 0 or target_slot >= len(targets):
            return None, True, "no_target"
        target = targets[target_slot]
        target_id = target.get("fighter_id")
        if target_id is None:
            return None, True, "target_slot_empty"

        direct = self._direct_cast(board, slot, target)
        if direct is not None:
            return direct, False, "policy_direct"

        # The policy asked to back off and the chosen cast cannot land from here:
        # retreating is what it chose, and walking toward a cast cell would invert
        # that intent. The cast happens next turn from the safer cell.
        if move == 2:
            action = self._walk_action(board, reference, move)
            if action is not None:
                return action, False, "policy_retreat"

        move_cell = self._cast_cell(board, slot, target)
        if move_cell is not None:
            path = self._path_to(board, board.my_cell, move_cell)
            if len(path) > 1:
                return ({"type": "move", "cell": int(move_cell),
                         "path": path}, True, "policy_move")

        plans = self.solver.plan_candidates(board, self.plan_k)
        matching: List[TurnPlan] = []
        for plan in plans:
            if not plan.actions or plan.actions[0].kind == "pass":
                continue
            for action in plan.actions:
                if action.kind == "cast" \
                        and int(action.spell_id) == int(slot["spell_id"]) \
                        and action.target_id is not None \
                        and int(action.target_id) == int(target_id):
                    matching.append(plan)
                    break
        if matching:
            picked = self._value_choice(board, matching)
            if picked is None:
                picked = self._grid_choice(board, matching)
                if picked is None:
                    picked = max(matching, key=lambda plan: self._move_score(
                        board, plan, target, move))
            return self._emit(board, picked, False, "policy", "policy_move")

        fallback = next((plan for plan in plans
                         if plan.actions and plan.actions[0].kind != "pass"), None)
        if fallback is None:
            # No cast is legal this turn, but the actor still chose a direction:
            # spend the MP instead of ending the turn standing still.
            action = self._walk_action(board, reference, move)
            if action is not None:
                return (action, False,
                        "policy_approach" if move == 0 else "policy_retreat")
            return {"type": "pass"}, True, "no_legal_plan"
        return self._emit(board, fallback, True, "solver_fallback", "solver_move")

    @staticmethod
    def _solver_first() -> bool:
        """Whether the deterministic solver drives the turn (default on).

        The solver encodes the simple winning strategy -- maximum damage, line
        of sight, AP/MP budget, finishing kills -- and is measured 0.0875 ABOVE
        the learned actor it would otherwise displace (brain_service.py, section L.31).
        So the actor choosing the spell/target is the losing half; the solver
        choosing the whole turn is what a competent player does. Set
        DOFUS_COMBAT_SOLVER_FIRST=off to fall back to actor-first.
        """
        return str(os.environ.get("DOFUS_COMBAT_SOLVER_FIRST", "on")).strip().lower() \
            not in ("0", "off", "false", "no")

    def _solver_turn(self, board: Board
                     ) -> Optional[Tuple[Dict[str, Any], bool, str]]:
        """The best legal solver plan for this turn, value/grid re-ranked."""
        try:
            plans = self.solver.plan_candidates(board, max(2, int(self.plan_k)))
        except Exception:  # noqa: BLE001 - a solver failure must not cost the turn
            return None
        plans = [plan for plan in plans
                 if plan.actions and plan.actions[0].kind != "pass"]
        if not plans:
            return None
        picked = self._value_choice(board, plans)
        if picked is None:
            picked = self._grid_choice(board, plans)
        if picked is None:
            picked = self._hp_choice(board, plans)
        if picked is None:
            picked = plans[0]
        # Kept for the decision record. "He walked into the enemy" has to be a
        # number in the log, not an impression from watching the character.
        self._last_plan = picked
        return self._emit(board, picked, False, "solver", "solver_move")

    @property
    def hp_choice_stats(self) -> Dict[str, int]:
        """How often the HP re-ranking was asked, and how often it changed the plan.

        Same reason as `value_choice_stats`: "the branch never ran" and "the branch
        ran and agreed with the solver" are different findings, and only the
        counters tell them apart. `changed` is the number that matters for the
        0.0113 measurement -- in the duels it fired on 121 of 600 boards, so a
        live run where it never changes a plan means the live boards are not the
        boards it was measured on.
        """
        return {"asked": int(getattr(self, "_hp_asked", 0)),
                "changed": int(getattr(self, "_hp_changed", 0))}

    def _hp_choice(self, board: Board,
                   plans: Sequence[TurnPlan]) -> Optional[TurnPlan]:
        """Re-rank the solver's plans by the HP differential they produce.

        No model, no simulation: `plan.final_hp` is already the solver's own
        projection of every fighter's HP after the plan, so this is a read of a
        number the search computed and then discarded in favour of a weighted
        score.

        Measured, 600 paired duels on level-200 builds against the solver's own
        choice (`combat/duel_value.py --evaluate`, 104 boards better to 17 worse,
        mean +0.0113 of an HP bar, t = 5.4). The weighted score is a *worse*
        objective than the differential it is built from, which is why a learned
        ranker trained on top of the same candidates could not beat it either.

        Two honest limits. The evidence is 1v1 duels on the duel harness, not
        live multi-enemy fights -- the sum over each side generalises, but it has
        not been measured there. And the effect is small: 0.011 of a bar, so it
        is a better tie-break among near-equivalent plans rather than a change of
        strategy.
        """
        if not HP_RANK_ENABLED or len(plans) < 2:
            return None
        me = board.me
        if me is None:
            return None
        living = [f for f in board.fighters if f.alive]
        if len(living) < 2:
            return None
        self._hp_asked = getattr(self, "_hp_asked", 0) + 1

        def margin(plan: TurnPlan) -> float:
            mine = theirs = 0.0
            for fighter in living:
                hp = float(plan.final_hp.get(fighter.id, fighter.hp))
                share = max(0.0, min(1.0, hp / max(1.0, fighter.max_hp)))
                if fighter.team == me.team:
                    mine += share
                else:
                    theirs += share
            return mine - theirs

        picked = max(plans, key=margin)
        if picked is not plans[0]:
            self._hp_changed = getattr(self, "_hp_changed", 0) + 1
        return picked

    def _position_report(self, board: Board) -> Optional[Dict[str, Any]]:
        """What the chosen plan costs in exposure, and what is doing the pricing.

        Recorded per decision so the two halves are separable: a plan that walks
        into the open, and a threat table that failed to notice the enemy doing
        it. Without the second half, a wrong price looks exactly like a wrong
        choice.
        """
        plan = getattr(self, "_last_plan", None)
        if plan is None:
            return None
        try:
            model = getattr(getattr(self, "solver", None), "last_threat", None)
            if model is None:
                from combat import threat
                model = threat.build(board, memory=threat.default_memory())
            return {
                "cell": int(plan.final_cell),
                "exposure": round(float(plan.exposure), 2),
                "here": round(model.at(board.my_cell), 2),
                "enemies": [{"id": t.fighter_id, "damage": round(t.damage, 1),
                             "reach": int(t.reach), "source": t.source}
                            for t in model.enemies],
            }
        except Exception:  # noqa: BLE001 - a report must never cost the turn
            return None

    def _emit(self, board: Board, plan: TurnPlan, fallback: bool, reason: str,
              walk_reason: str) -> Tuple[Dict[str, Any], bool, str]:
        """The plan's first action; walk first when its cast starts elsewhere.

        The page casts from the character's current cell. A plan that folds a
        walk into its cast must emit that walk now, or the client sees an
        out-of-range cast and the turn stalls.
        """
        first = plan.actions[0]
        if first.kind == "cast" and int(first.from_cell) != int(board.my_cell):
            path = self._path_to(board, board.my_cell, int(first.from_cell))
            if len(path) > 1:
                return ({"type": "move", "cell": int(first.from_cell),
                         "path": path}, True, walk_reason)
        return self._action_for_plan(board, plan), fallback, reason

    # -- request -------------------------------------------------------------

    def warmup(self) -> None:
        """Compile the policy/solver once so the first live turn is not slow."""
        self.decide({
            "in_fight": True, "in_preparation": False, "is_my_turn": True,
            "my_id": 1, "my_cell": 100, "map_id": 1, "round": 1, "fight_id": 0,
            "cells": [3] * 560,
            "my_stats": {"strength": 100, "intelligence": 0, "chance": 0,
                         "agility": 0, "power": 0, "damage": 0,
                         "critical_hit": 0, "heals": 0},
            "spells": [{"id": 1, "name": "warmup", "ap_cost": 2,
                        "min_range": 1, "range": 4, "requires_los": True,
                        "castable": True, "casts_this_turn": 0,
                        "max_cast_per_turn": 2, "max_cast_per_target": 2,
                        "effects": [{"effect_id": 100, "dice_num": 5,
                                     "dice_side": 5}],
                        "critical_effects": []}],
            "fighters": [
                {"id": 1, "name": "me", "cell": 100, "team": "player",
                 "is_me": True, "alive": True, "hp": 100, "max_hp": 100,
                 "ap": 6, "max_ap": 6, "mp": 3, "max_mp": 3, "level": 10,
                 "resist_percent": {}, "resist_fixed": {}, "spells": []},
                {"id": 2, "name": "foe", "cell": 101, "team": "enemy",
                 "is_me": False, "alive": True, "hp": 30, "max_hp": 30,
                 "ap": 4, "max_ap": 4, "mp": 2, "max_mp": 2, "level": 5,
                 "resist_percent": {}, "resist_fixed": {},
                 "spells": [{"id": 2, "name": "bite", "ap_cost": 2,
                             "min_range": 1, "range": 2, "requires_los": True,
                             "effects": [{"effect_id": 97, "dice_num": 3,
                                          "dice_side": 3}],
                             "critical_effects": []}]},
            ],
        })

    def decide(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            board = Board.from_fight_state(payload)
            observation, contract = live_view(board)
            slots, player_rows = self._bind_slots(board)
            targets = self._bind_targets(board)
        except Exception as exc:  # noqa: BLE001 - a malformed live board must fall back
            return {
                "checkpoint": str(self.checkpoint),
                "observation": None,
                "contract": {"version": 1, "ok": False, "actionable": False,
                             "value_ready": False,
                             "errors": [f"binding_error:{type(exc).__name__}: {exc}"],
                             "warnings": []},
                "slots": [], "targets": [],
                "ok": False, "action": None,
                "reason": f"binding_error:{type(exc).__name__}: {exc}",
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
            }
        base: Dict[str, Any] = {
            "checkpoint": str(self.checkpoint),
            "observation": observation,
            "contract": contract,
            "slots": slots,
            "targets": targets,
        }
        if board.me is None or not contract["actionable"]:
            base.update({"ok": False, "reason": "observation_not_actionable",
                         "action": None, "latency_ms": round(
                             (time.perf_counter() - started) * 1000.0, 1)})
            return base
        # The critic may only rank when its own inputs are complete (the enemy's
        # spells are loaded); otherwise the solver's choice stands. This is read
        # before the solver-first branch, not after it: that branch returns, so an
        # assignment below it never ran on the default path and `_value_choice`
        # saw the `__init__` False on every live turn.
        self._value_ready = bool(contract.get("value_ready"))
        # Solver-first: play the deterministic plan a good player would. The
        # actor path below is the fallback for a turn the solver cannot express.
        if self._solver_first():
            st = self._solver_turn(board)
            if st is not None:
                action, fallback, reason = st
                base["policy"] = {"move": None, "spell_slot": None,
                                  "target_slot": None, "spell_id": None,
                                  "target_id": None, "probability": None,
                                  "value": None, "fallback": bool(fallback),
                                  "reason": reason}
                position = self._position_report(board)
                if position is not None:
                    base["position"] = position
                base.update({"ok": action is not None, "action": action,
                             "reason": reason,
                             "latency_ms": round(
                                 (time.perf_counter() - started) * 1000.0, 1)})
                return base
        try:
            plan = self._current_plan(board)
            tables, spec = board_encounter(
                board, plan, self.cfg, first_team=0, player_rows=player_rows,
                as_jax=False)
            logits, masks = self._bundle(
                self.params, spec, tables,
                float(max(0, board.my_ap)), float(max(0, board.my_mp)),
                int(board.round or 0))
            move, spell_slot, target_slot, probability, value = self._choose(
                logits, masks)
            action, fallback, reason = self._realize(
                board, spell_slot, target_slot, move, slots, targets)
            # A turn that ends with AP in hand and a legal cast on the board is a
            # lost turn, whatever the reason it was declined for. Measured: 42% of
            # the live turns that had a spell available ended in a pass. The rescue
            # runs only when the answer is a pass, so it can add a cast but can
            # never override one.
            if action is None or action.get("type") == "pass":
                rescue = self._last_resort_cast(board, slots, targets)
                if rescue is not None:
                    action, fallback, reason = rescue, True, "unspent_ap_cast"
            base["policy"] = {
                "move": move, "spell_slot": spell_slot,
                "target_slot": target_slot,
                "spell_id": slots[spell_slot]["spell_id"]
                if 0 <= spell_slot < len(slots) else None,
                "target_id": targets[target_slot].get("fighter_id")
                if 0 <= target_slot < len(targets) else None,
                "probability": round(probability, 4),
                "value": round(value, 4),
                "fallback": bool(fallback),
                "reason": reason,
            }
            base.update({
                "ok": action is not None,
                "action": action,
                "reason": reason,
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
            })
            return base
        except Exception as exc:  # noqa: BLE001 - the game must get a fallback, not a crash
            base.update({"ok": False, "reason": f"policy_error:{type(exc).__name__}: {exc}",
                         "action": None,
                         "latency_ms": round((time.perf_counter() - started) * 1000.0, 1)})
            return base
