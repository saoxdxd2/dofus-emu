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

import pickle
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

import jax
import jax.numpy as jnp
import numpy as np

from combat.battle_solver import BattleSolver, Board, TurnPlan
from combat.encounter import model
from combat.encounter.env import Config, action_masks, initial_state, observe
from combat.observation import build_observation, validate_observation
from combat.plan_value import (board_encounter, _empty_spell_row,
                               _player_spell_rows)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHECKPOINT = REPO_ROOT / "models" / "encounter_selfplay.pkl"


class LivePolicy:
    """Stateless per-request slot binding around the trained encounter policy."""

    def __init__(self, checkpoint: Optional[Path] = None,
                 beam_width: int = 8, max_actions: int = 4) -> None:
        self.checkpoint = Path(checkpoint or DEFAULT_CHECKPOINT)
        payload = pickle.loads(self.checkpoint.read_bytes())
        self.params = jax.tree_util.tree_map(jnp.asarray, payload["params"])
        saved = payload.get("config") or {}
        self.cfg = Config(
            allies=int(saved.get("allies", 1)),
            enemies=int(saved.get("enemies", 4)),
            spells=int(saved.get("spells", 4)),
            max_rounds=int(saved.get("max_rounds", 24)),
            max_dist=int(saved.get("max_dist", 12)),
            casts_per_turn=int(saved.get("casts_per_turn", 4)),
        )
        self.solver = BattleSolver(beam_width=int(beam_width),
                                   max_actions=int(max_actions))
        self.plan_k = max(2, int(beam_width))
        # The state/masks/observation/value path is pure JAX. Compiling the
        # bundle once removes the per-turn eager dispatch overhead; the board
        # values still enter as traced arrays, so nothing about the instance is
        # baked into the graph.
        self._bundle = jax.jit(self._forward_bundle)

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
        names = {sid: spell.name for sid, spell in by_id.items()}
        slots: List[Dict[str, Any]] = []
        for slot, row in enumerate(chosen):
            slots.append({
                "slot": slot, "spell_id": int(row["id"]),
                "name": names.get(row["id"], ""),
                "ap": row["ap"], "rmin": row["rmin"], "rmax": row["rmax"],
                "damage": round(float(row["damage"]), 2),
                "heal": round(float(row["heal"]), 2),
                "element": int(row["element"]), "ally": bool(row["ally"]),
            })
        while len(chosen) < 4:
            chosen.append(_empty_spell_row())
        while len(slots) < 4:
            slots.append({
                "slot": len(slots), "spell_id": 0, "name": "", "ap": 6.0,
                "rmin": 0.0, "rmax": 0.0, "damage": 0.0, "heal": 0.0,
                "element": 0, "ally": False,
            })
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
        """Initial state + masks + observation + logits as one JAX program."""
        state = initial_state(spec, tables, self.cfg)
        state["ap"] = state["ap"].at[:, 0].set(current_ap)
        state["mp"] = state["mp"].at[:, 0].set(current_mp)
        state["round"] = jnp.asarray([round_], dtype=jnp.int32)
        masks = action_masks(state, tables, self.cfg)
        obs, glob, attention = observe(
            state, tables, self.cfg,
            acting_team=jnp.zeros((1,), dtype=jnp.int32))
        return model.forward(params, obs, glob, attention), masks

    def _choose(self, logits: Dict[str, jax.Array],
                masks: Dict[str, jax.Array]
                ) -> Tuple[int, int, int, float, float]:
        spell_logits = np.asarray(logits["spell"][0, 0], dtype=np.float64)
        spell_mask = np.asarray(masks["spell"][0, 0], dtype=bool)
        spell_scores = np.where(spell_mask, spell_logits, -1e9)
        spell_slot = int(np.argmax(spell_scores))

        move_logits = np.asarray(logits["move"][0, 0], dtype=np.float64)
        move_mask = np.asarray(masks["move"][0, 0], dtype=bool)
        move_scores = np.where(move_mask, move_logits, -1e9)
        move = int(np.argmax(move_scores)) if move_mask.any() else 1

        if spell_slot == self.cfg.spells:
            target_slot = 0
        else:
            target_logits = np.asarray(
                logits["target"][0, 0, spell_slot], dtype=np.float64)
            target_mask = np.asarray(
                masks["target"][0, 0, spell_slot], dtype=bool)
            target_scores = np.where(target_mask, target_logits, -1e9)
            target_slot = int(np.argmax(target_scores)) if target_mask.any() else -1

        finite = np.exp(spell_scores - np.max(spell_scores))
        probability = float(finite[spell_slot] / finite.sum())
        value = float(np.asarray(logits["value"])[0])
        return move, spell_slot, target_slot, probability, value

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
        if move == 2:
            return -float(delta)
        if move == 0:
            return float(delta)
        return -float(abs(delta))

    def _realize(self, board: Board, spell_slot: int, target_slot: int,
                 move: int, slots: Sequence[Dict[str, Any]],
                 targets: Sequence[Dict[str, Any]]
                 ) -> Tuple[Optional[Dict[str, Any]], bool, str]:
        if spell_slot == self.cfg.spells:
            return {"type": "pass"}, False, "policy_pass"
        if spell_slot < 0 or spell_slot >= len(slots):
            return None, True, "slot_out_of_range"
        slot = slots[spell_slot]
        if int(slot["spell_id"]) <= 0:
            return None, True, "empty_spell_slot"
        if target_slot < 0 or target_slot >= len(targets):
            return None, True, "no_target"
        target = targets[target_slot]
        target_id = target.get("fighter_id")
        if target_id is None:
            return None, True, "target_slot_empty"

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
            best = max(matching, key=lambda plan: self._move_score(
                board, plan, target, move))
            return self._action_for_plan(board, best), False, "policy"

        fallback = next((plan for plan in plans
                         if plan.actions and plan.actions[0].kind != "pass"), None)
        if fallback is None:
            return {"type": "pass"}, True, "no_legal_plan"
        return self._action_for_plan(board, fallback), True, "solver_fallback"

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
            observation = build_observation(board, source="live")
            contract = validate_observation(observation)
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
        try:
            plan = self._current_plan(board)
            tables, spec = board_encounter(
                board, plan, self.cfg, first_team=0, player_rows=player_rows)
            logits, masks = self._bundle(
                self.params, spec, tables,
                float(max(0, board.my_ap)), float(max(0, board.my_mp)),
                int(board.round or 0))
            move, spell_slot, target_slot, probability, value = self._choose(
                logits, masks)
            action, fallback, reason = self._realize(
                board, spell_slot, target_slot, move, slots, targets)
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
