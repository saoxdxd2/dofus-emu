"""Weight-space programming on the fight head: localise, edit downstream, verify exactly.

Why this tool and not another training loop. The user asked for weight-space programming: read the
network's weights, interpret them, write new ones. Two facts from `wsp_read.py` make the naive version
of that a waste of a duel:

* the deciding biases are ~0.01 while the logits they compete with are 8-15 (spell) and +/-40
  (target), so an edit written at "bias scale" cannot move an argmax;
* on a board where the mask forbids every real spell, no weight edit can change the decision at all.

So the first command is a *localiser*, not an editor: for each board it records the head's pick, the
beam's pick, whether the beam's pick was even legal under the head's own mask, and the logit margin
that would have to be overcome. That splits the deficit into "the weights chose this" and "the mask
forced this", which are different problems with different fixes.

The second command exploits a structural property: the move/spell/target heads are linear reads of the
trunk's actor representation, so editing a head leaves that representation unchanged. Cache the
representation once and every candidate edit is evaluated *exactly* -- same argmax, same masks, no
re-running the trunk and no duel -- on train and held-out boards. Only an edit that wins held-out
agreement is written to a checkpoint for `wsp_fight.py` to price in duels.

    python tools/wsp_program.py cache --data %TEMP%/encounters_boss_s4_2048.npz --boards 192 \
        --out %TEMP%/wsp_cache.npz
    python tools/wsp_program.py edit --cache %TEMP%/wsp_cache.npz --head spell --out arm.pkl
    python tools/wsp_program.py edit --cache %TEMP%/wsp_cache.npz --head move --out arm_move.pkl
    python tools/wsp_fight.py base --matchups 200 --policy production --checkpoint arm.pkl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import pickle
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

import numpy as np  # noqa: E402

CHECKPOINT = BRAIN / "models" / "encounter_selfplay.pkl"


def trunk_actors(params, obs, glob, attention, heads=4):
    """The actor tokens `model.forward` feeds its heads.

    This used to be a hand copy of `forward`'s trunk with the comment "replicated rather than
    refactored so the shipped model file stays untouched", which is the wrong trade: a copy has no
    witness, and the day `forward` changed the caches would have been quietly describing a network
    that no longer existed. `model.trunk` is now the one implementation and both call it.
    """
    from combat.encounter import model as m

    return m.trunk(params, obs, glob, attention, heads)[0]


def reachable_after_walk(distance, mp, rmin, rmax):
    """Whether the one-dimensional encounter move can make a spell range legal."""
    try:
        distance, mp, rmin, rmax = map(float, (distance, mp, rmin, rmax))
    except (TypeError, ValueError):
        return False
    if not all(map(math.isfinite, (distance, mp, rmin, rmax))) or mp < 0:
        return False
    return max(1.0, distance - mp) <= rmax and distance + mp >= rmin


MOVE_INTENT_NAMES = (
    "close_to_optimal_range",  # 0
    "step_to_break_los",       # 1
    "lateral_flank_los",       # 2
    "hold",                    # 3
    "kite_max_mp",             # 4
)


def teacher_move_intent(board, target_cell, final_cell, map_field=None) -> Optional[int]:
    """Tactical cell-intent classification for 2D Spectral-LOS move head.

    0: close_to_optimal_range (closing distance to target)
    1: step_to_break_los (stepping behind obstacle breaking enemy directed line of sight)
    2: lateral_flank_los (flanking to gain line of sight to target, or lateral step along resistance gradient)
    3: hold (stationary on cell)
    4: kite_max_mp (retreating / kiting away from target/threat)
    """
    if target_cell is None or final_cell is None:
        return None
    my_cell = int(board.my_cell)
    final_c = int(final_cell)
    target_c = int(target_cell)

    if final_c == my_cell:
        return 3  # hold

    # If map_field has tactical_intent method, delegate directly
    if map_field is not None and hasattr(map_field, "tactical_intent"):
        enemies = getattr(board, "enemies", ()) or ()
        enemy_cells = [int(e.cell) for e in enemies if getattr(e, "alive", True) and getattr(e, "cell", None) is not None]
        return map_field.tactical_intent(my_cell, final_c, target_c, enemy_cells=enemy_cells)

    # If board.cells has flags and we can load MapField:
    if hasattr(board, "cells") and hasattr(board.cells, "flags") and not hasattr(board.cells, "_distances"):
        from combat.map_field import MapField
        try:
            mf = MapField.from_flags(board.cells.flags, map_id=getattr(board.cells, "map_id", None))
            enemies = getattr(board, "enemies", ()) or ()
            enemy_cells = [int(e.cell) for e in enemies if getattr(e, "alive", True) and getattr(e, "cell", None) is not None]
            return mf.tactical_intent(my_cell, final_c, target_c, enemy_cells=enemy_cells)
        except Exception:
            pass

    # Fallback when only basic LOS or distance is available:
    def _check_los(origin, target):
        if origin == target:
            return True
        if hasattr(board, "cells") and hasattr(board.cells, "has_los"):
            try:
                return bool(board.cells.has_los(origin, target))
            except Exception:
                pass
        return None

    enemies = getattr(board, "enemies", ()) or ()
    threat_cells = [int(e.cell) for e in enemies if getattr(e, "alive", True) and getattr(e, "cell", None) is not None]
    if not threat_cells and target_c != my_cell:
        threat_cells = [target_c]

    # Check step_to_break_los
    if threat_cells:
        had_los = [tc for tc in threat_cells if _check_los(tc, my_cell) is True]
        have_los = [tc for tc in threat_cells if _check_los(tc, final_c) is True]
        if target_c in threat_cells and _check_los(target_c, my_cell) is True and _check_los(target_c, final_c) is False:
            return 1
        if had_los and not have_los:
            return 1

    # Check lateral_flank_los (gained line of sight)
    my_had_los = _check_los(my_cell, target_c)
    my_have_los = _check_los(final_c, target_c)
    if my_had_los is False and my_have_los is True:
        return 2

    # Distance check
    d_before = board.cells.distance(my_cell, target_c)
    d_after = board.cells.distance(final_c, target_c)
    if d_after == d_before:
        return 2  # lateral_flank_los
    if d_after < d_before:
        return 0  # close_to_optimal_range
    return 4  # kite_max_mp


def teacher_move_class(board, target_cell, final_cell, map_field=None, n_classes=3):
    """Which of the move choices the teacher's plan implies.

    n_classes=3: radial direction (0 closes gap, 1 holds, 2 opens gap).
    n_classes=5: 2D tactical cell-intent basis (close_to_optimal_range, step_to_break_los,
                 lateral_flank_los, hold, kite_max_mp).
    """
    if target_cell is None or final_cell is None:
        return None
    if n_classes == 5:
        return teacher_move_intent(board, target_cell, final_cell, map_field=map_field)
    before = board.cells.distance(int(board.my_cell), int(target_cell))
    after = board.cells.distance(int(final_cell), int(target_cell))
    if after < before:
        return 0
    if after > before:
        return 2
    return 1


def cache_boards(boards_count, seed, checkpoint, out_path, data_path=None):
    from combat import plan_benchmark as pb
    from combat.battle_solver import BattleSolver
    from combat.encounter.checkpoint import config_from_payload
    from combat.encounter.env import action_masks, initial_state, observe
    from combat.live_policy import LivePolicy
    from combat.plan_value import board_encounter
    import random

    checkpoint = pathlib.Path(checkpoint).resolve()
    checkpoint_bytes = checkpoint.read_bytes()
    checkpoint_sha256 = hashlib.sha256(checkpoint_bytes).hexdigest()
    payload = pickle.loads(checkpoint_bytes)
    cfg = config_from_payload(payload)
    policy = LivePolicy(checkpoint=pathlib.Path(checkpoint), beam_width=8, max_actions=2)
    solver = BattleSolver(beam_width=8, max_actions=2)
    data_path = pathlib.Path(data_path or pb.DEFAULT_DATA).resolve()
    data_sha256 = hashlib.sha256(data_path.read_bytes()).hexdigest()
    data = np.load(data_path)
    cells = pb.load_map(pathlib.Path(pb.DEFAULT_MAP))
    from combat.map_field import MapField
    try:
        map_field = MapField.from_flags(cells.flags, map_id=getattr(cells, "map_id", 100139008))
    except Exception:
        map_field = None
    rng = random.Random(seed)
    n_moves = payload["params"]["move"][0].shape[1] if "move" in payload.get("params", {}) else 3

    store = {key: [] for key in ("hidden", "spell_mask", "move_logits", "move_mask",
                                 "target_logits", "target_mask", "teacher_spell",
                                 "teacher_move", "teacher_target", "head_spell",
                                 "head_target", "head_move", "margin", "row", "current_distance",
                                 "movement_points", "teacher_ap_legal", "teacher_team_legal",
                                 "teacher_range_legal", "teacher_range_blocked",
                                 "teacher_range_reachable_after_walk")}
    classes = {}
    sample = rng.sample(range(int(data["hp"].shape[0])), min(boards_count, int(data["hp"].shape[0])))
    for row in sample:
        board = pb.make_board(data, row, cells, rng)
        if board is None or board.me is None or not board.enemies:
            continue
        candidates = solver.plan_candidates(board, 8)
        if not candidates:
            continue
        best = max(candidates, key=lambda plan: plan.score)
        cast = next((a for a in best.actions if getattr(a, "kind", "") == "cast"), None)
        slots, player_rows = policy._bind_slots(board)
        targets = policy._bind_targets(board)
        tables, spec = board_encounter(board, policy._current_plan(board), cfg, first_team=0,
                                       player_rows=player_rows, as_jax=False)
        state = initial_state(spec, tables, cfg)
        from combat.encounter.backend import at, XP
        state["ap"] = at(state["ap"])[:, 0].set(float(max(0, board.my_ap)))
        state["mp"] = at(state["mp"])[:, 0].set(float(max(0, board.my_mp)))
        state["round"] = XP.asarray([int(board.round or 0)], dtype=XP.int32)
        masks = action_masks(state, tables, cfg)
        obs, glob, attention = observe(state, tables, cfg,
                                       acting_team=XP.zeros((1,), dtype=XP.int32))
        actors = trunk_actors(policy.params, obs, glob, attention)
        hidden = np.asarray(actors)[0, 0].astype(np.float32)
        logits, _ = policy._bundle(policy.params, spec, tables, float(max(0, board.my_ap)),
                                   float(max(0, board.my_mp)), int(board.round or 0))
        move, spell_slot, target_slot, _p, _v = policy._choose(logits, masks)
        spell_logits = np.asarray(logits["spell"])[0, 0].astype(np.float32)
        spell_mask = np.asarray(masks["spell"])[0, 0].astype(bool)
        target_logits = np.asarray(logits["target"])[0, 0].astype(np.float32)

        teacher_spell = cfg.spells
        if cast is not None:
            teacher_spell = next((i for i, s in enumerate(slots)
                                  if s.get("spell_id") == cast.spell_id), -1)
        teacher_target = 0
        if cast is not None and getattr(cast, "target_id", None) is not None:
            teacher_target = next((i for i, t in enumerate(targets)
                                   if t.get("fighter_id") == cast.target_id), -1)
        # What the beam's walk says about distance, in the three directions the env can express.
        # A plan that holds and casts names no body, so the reference falls back to the nearest
        # living enemy: the walk is still an opinion about closing or opening the gap.
        aim_cell = (targets[teacher_target].get("cell")
                    if 0 <= teacher_target < len(targets) else None)
        if aim_cell is None:
            living = [t["cell"] for t in targets
                      if t.get("alive") and t.get("cell") is not None
                      and t["slot"] >= cfg.allies]
            aim_cell = (min(living, key=lambda c: board.cells.distance(
                int(board.my_cell), int(c))) if living else None)
        teacher_move = teacher_move_class(board, aim_cell,
                                          getattr(best, "final_cell", None),
                                          map_field=map_field, n_classes=n_moves)
        # The margin is measured over *legal* spells only: with one legal slot the masked-out
        # entries sit at -1e9 and a naive margin reports 1e9, which is the mask talking.
        legal = np.sort(spell_logits[spell_mask])
        margin = float(legal[-1] - legal[-2]) if legal.size > 1 else 0.0

        # Separate "the mask says no" into the reason it says no. Only a range gate can be
        # lifted by walking first; AP, team and a dead body are facts the actor cannot change.
        real_teacher = 0 <= teacher_spell < cfg.spells and teacher_target >= 0
        distance_now = (float(np.asarray(state["dist"])[0, 0])
                        if real_teacher else 0.0)
        mp_now = float(np.asarray(state["mp"])[0, 0])
        rmin_now = float(np.asarray(tables["spell_rmin"])[0, 0, teacher_spell]) if real_teacher else 0.0
        rmax_now = float(np.asarray(tables["spell_rmax"])[0, 0, teacher_spell]) if real_teacher else 0.0
        ally = bool(np.asarray(tables["spell_ally"])[0, 0, teacher_spell]) if real_teacher else False
        ap_ok = (real_teacher and float(np.asarray(state["ap"])[0, 0])
                 >= float(np.asarray(tables["spell_ap"])[0, 0, teacher_spell]))
        target_alive = bool(np.asarray(state["alive"])[0, teacher_target]) if real_teacher else False
        same_team = (int(np.asarray(state["team"])[0, teacher_target])
                     == int(np.asarray(state["team"])[0, 0])) if real_teacher else False
        team_ok = same_team if ally else (not same_team)
        range_ok = ally or (rmin_now <= distance_now <= rmax_now)
        range_blocked = bool(real_teacher and ap_ok and target_alive and team_ok and not range_ok)
        reachable_now = bool(range_blocked and reachable_after_walk(
            distance_now, mp_now, rmin_now, rmax_now))

        if teacher_spell < 0:
            kind = "teacher_spell_not_bound"
        elif teacher_target < 0:
            kind = "teacher_target_not_bound"
        elif spell_slot == teacher_spell and (cast is None or target_slot == teacher_target):
            kind = "agree"
        elif spell_slot == cfg.spells and cast is not None:
            kind = ("head_pass_teacher_cast_masked" if not spell_mask[teacher_spell]
                    else "head_pass_teacher_cast_legal")
        elif spell_slot != teacher_spell and cast is not None:
            kind = ("wrong_spell_teacher_masked" if not spell_mask[teacher_spell]
                    else "wrong_spell_teacher_legal")
        elif cast is None:
            # The beam's best plan holds or walks and the head casts anyway. Not a target error:
            # the teacher never named a body, so counting it as one inflated the target class.
            kind = "teacher_no_cast_head_cast"
        else:
            kind = "wrong_target"
        classes[kind] = classes.get(kind, 0) + 1
        store["hidden"].append(hidden)
        store["spell_mask"].append(spell_mask)
        store["move_logits"].append(np.asarray(logits["move"])[0, 0].astype(np.float32))
        store["move_mask"].append(np.asarray(masks["move"])[0, 0].astype(bool))
        store["teacher_move"].append(-1 if teacher_move is None else int(teacher_move))
        store["target_logits"].append(target_logits)
        store["target_mask"].append(np.asarray(masks["target"])[0, 0].astype(bool))
        store["teacher_spell"].append(teacher_spell)
        store["teacher_target"].append(teacher_target)
        store["head_spell"].append(spell_slot)
        store["head_target"].append(target_slot)
        store["head_move"].append(move)
        store["margin"].append(margin)
        store["row"].append(int(row))
        store["current_distance"].append(distance_now)
        store["movement_points"].append(mp_now)
        store["teacher_ap_legal"].append(ap_ok)
        store["teacher_team_legal"].append(team_ok)
        store["teacher_range_legal"].append(bool(real_teacher and range_ok))
        store["teacher_range_blocked"].append(range_blocked)
        store["teacher_range_reachable_after_walk"].append(reachable_now)

    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != checkpoint_sha256:
        raise ValueError("checkpoint changed while building cache")
    arrays = {key: np.asarray(value) for key, value in store.items()}
    if hashlib.sha256(data_path.read_bytes()).hexdigest() != data_sha256:
        raise ValueError("encounter table changed while building cache")
    arrays["data_path"] = np.asarray(str(data_path))
    arrays["data_sha256"] = np.asarray(data_sha256)
    arrays["checkpoint_path"] = np.asarray(str(checkpoint))
    arrays["checkpoint_sha256"] = np.asarray(checkpoint_sha256)
    np.savez_compressed(out_path, **arrays)
    n = len(arrays["row"])
    blocked = arrays["teacher_range_blocked"].astype(bool)
    reachable = arrays["teacher_range_reachable_after_walk"].astype(bool)
    # The move axis is a separate addressable set, not part of `fixable_in_weight_space`: the
    # spell/target classes are already counted there, and a board can disagree on both.
    head_move = arrays["head_move"].astype(int)
    teacher_move = arrays["teacher_move"].astype(int)
    move_legal = arrays["move_mask"].astype(bool)
    n_moves = move_legal.shape[1] if move_legal.ndim > 1 else 3
    named = teacher_move >= 0
    move_rows = np.arange(n)
    move_fixable = int((named & (head_move != teacher_move)
                        & move_legal[move_rows, np.clip(teacher_move, 0, n_moves - 1)]).sum())
    ranked_moves = np.sort(arrays["move_logits"].astype(np.float64), axis=1)
    median_move_margin = (float(np.median(ranked_moves[:, -1] - ranked_moves[:, -2]))
                          if n else None)
    summary = {"boards": n, "classes": classes,
               "move_disagreements_teacher_legal": move_fixable,
               "head_move_classes": np.bincount(head_move[named], minlength=n_moves).tolist(),
               "teacher_move_classes": np.bincount(teacher_move[named], minlength=n_moves).tolist(),
               "fixable_in_weight_space": sum(v for k, v in classes.items()
                                              if k.endswith("legal") or k == "wrong_target"),
               "mask_forced": sum(v for k, v in classes.items() if k.endswith("masked")),
               "teacher_range_blocked": int(blocked.sum()),
               "teacher_range_blocked_reachable_after_walk": int((blocked & reachable).sum()),
               "teacher_range_blocked_unreachable_after_walk": int((blocked & ~reachable).sum()),
               "median_move_margin": median_move_margin,
               "median_margin": float(np.median(arrays["margin"])) if n else None,
               "cache": str(out_path)}
    print(json.dumps(summary, indent=1))
    return summary


def edit_target_bias(z, out_path, payload, etas, split_share=0.75):
    """Program the 20-number `t_bias[spell, actor]` target prior at logit scale.

    The localiser put nearly all the weight-fixable disagreement here: the head picks the beam's
    spell and then aims it at a different body. `t_bias` is added straight to the target logits, so
    an edit of 0.05 against logits that span +/-40 is not a program, it is a rounding error -- the
    eta grid therefore runs to tens, and each candidate is scored exactly on cached logits.

    The spell choice is untouched by this leaf (it feeds no other head), so the counterfactual is
    exact: masked argmax over `target_logits + delta` on the board's own chosen spell row.
    """
    logits = z["target_logits"].astype(np.float64)
    mask = z["target_mask"].astype(bool)
    teacher_spell = z["teacher_spell"].astype(int)
    head_spell = z["head_spell"].astype(int)
    teacher_target = z["teacher_target"].astype(int)
    head_target = z["head_target"].astype(int)
    n = len(teacher_spell)
    split = max(1, int(n * split_share))
    rows = np.arange(n)
    real = (teacher_spell >= 0) & (teacher_spell == head_spell) & (teacher_target >= 0) \
        & (teacher_spell < logits.shape[1] - 1)
    chosen = rows[real]
    spell_of = head_spell[real]
    legal = mask[real, spell_of, :]
    base = logits[real, spell_of, :]
    picked = np.argmax(np.where(legal, base, -1e9), axis=1)
    want = teacher_target[real]
    usable = real & (teacher_target != head_target) & mask[rows, np.clip(head_spell, 0, 4),
                                                       np.clip(teacher_target, 0, 4)]

    def agreement(values, sl):
        if not len(values[sl]):
            return 0.0
        return float((np.argmax(np.where(legal[sl], values[sl], -1e9), axis=1)
                      == want[sl]).mean())

    unit = np.zeros(logits.shape[1:], dtype=np.float64)
    touched = 0
    for board in np.where(usable[:split])[0]:
        unit[head_spell[board], teacher_target[board]] += 1.0
        if 0 <= head_target[board] < unit.shape[1]:
            unit[head_spell[board], head_target[board]] -= 1.0
        touched += 1
    if touched == 0:
        print(json.dumps({"verdict": "no same-spell target disagreement with a legal teacher "
                                     "target in the training slice; nothing to program"}, indent=1))
        return 1
    report = {"boards": n, "same_spell_boards": int(real.sum()),
              "train_target_disagreements": touched,
              "baseline_train_agreement": round(agreement(base, slice(0, split)), 4),
              "baseline_heldout_agreement": round(agreement(base, slice(split, len(base))), 4),
              "head_target_slot_counts": np.bincount(head_target[real], minlength=5).tolist(),
              "teacher_target_slot_counts": np.bincount(want, minlength=5).tolist(),
              "candidates": []}
    best = None
    for eta in etas:
        delta = eta * unit
        candidate = base + delta[spell_of]
        row = {"eta": eta,
               "train_agreement": round(agreement(candidate, slice(0, split)), 4),
               "heldout_agreement": round(agreement(candidate, slice(split, len(base))), 4)}
        report["candidates"].append(row)
        if best is None or row["train_agreement"] > best[0]["train_agreement"]:
            best = (row, delta)
    report["chosen"] = best[0]
    if best[0]["train_agreement"] <= report["baseline_train_agreement"] + 1e-9:
        report["verdict"] = "no eta improved training agreement; edit not written"
        print(json.dumps(report, indent=1))
        return 1
    if best[0]["heldout_agreement"] < report["baseline_heldout_agreement"] - 1e-9:
        report["verdict"] = ("train agreement rose but held-out agreement fell -- the prior "
                             "memorised the slice; edit not written")
        print(json.dumps(report, indent=1))
        return 1
    payload["params"]["t_bias"] = (np.asarray(payload["params"]["t_bias"], dtype=np.float64)
                                   + best[1]).astype(np.float32)
    payload["weight_space_program"] = {"head": "t_bias", "eta": best[0]["eta"],
                                       "train_disagreements": touched,
                                       "delta": np.round(best[1], 4).tolist(),
                                       "baseline_heldout_agreement":
                                           report["baseline_heldout_agreement"],
                                       "heldout_agreement": best[0]["heldout_agreement"]}
    pathlib.Path(out_path).write_bytes(pickle.dumps(payload))
    report["delta"] = np.round(best[1], 4).tolist()
    report["wrote"] = str(out_path)
    print(json.dumps(report, indent=1))
    return 0


def cache_pvm(boards_count, seed, checkpoint, out_path, data_path, head="spell", turns=3):
    """Label raw-state counterfactuals by terminal wins with a fixed greedy continuation."""
    from combat.encounter import backend, model
    if not backend.HAS_JAX:
        raise ValueError("cache-pvm requires JAX: " + str(backend.JAX_IMPORT_ERROR))
    import jax
    import jax.numpy as jnp
    from combat.encounter.checkpoint import config_from_payload
    from combat.encounter.env import action_masks, initial_state, observe, step
    from combat.encounter.heuristic import heuristic_actions
    from combat.encounter.train import TABLE_KEYS, SPEC_KEYS, make_eval_rollout

    if head not in ("move", "spell") or boards_count < 2 or turns < 1:
        raise ValueError("cache-pvm needs move/spell, at least two boards and positive turns")
    checkpoint, data_path = pathlib.Path(checkpoint).resolve(), pathlib.Path(data_path).resolve()
    identity = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (checkpoint, data_path)}
    payload = pickle.loads(checkpoint.read_bytes())
    cfg = config_from_payload(payload)
    if cfg.allies != 1:
        raise ValueError("raw counterfactuals currently require one player actor")
    params = jax.tree_util.tree_map(jnp.asarray, payload["params"])
    with np.load(data_path, allow_pickle=False) as data:
        count = min(boards_count, len(data["hp"]))
        if count < 2:
            raise ValueError("cache-pvm requires at least two corpus rows")
        chosen = np.random.default_rng(seed).choice(len(data["hp"]), count, replace=False)
        tables = {k: jnp.asarray(data[k][chosen]) for k in TABLE_KEYS}
        spec = {k: jnp.asarray(data[k][chosen]) for k in SPEC_KEYS if k in data}
    if any(t.shape != (count, cfg.n_actors, cfg.spells) for t in tables.values()):
        raise ValueError("corpus actor/spell dimensions do not match checkpoint")
    state = initial_state(spec, tables, cfg)
    continuation = make_eval_rollout(cfg, 2 * cfg.max_rounds, opponent="heuristic", player="greedy")

    @jax.jit
    def enemy_turn(s, t):
        moved = step(s, heuristic_actions(s, t, cfg), t, cfg)
        take = (s["acting_team"] == 1) & ~s["done"]
        return jax.tree_util.tree_map(
            lambda old, new: jnp.where(take.reshape((count,) + (1,) * (old.ndim - 1)), new, old),
            s, moved)

    @jax.jit
    def inspect(p, s, t):
        obs, glob, attention = observe(s, t, cfg)
        masks = action_masks(s, t, cfg)
        logits = model.forward(p, obs, glob, attention)
        actions = model.greedy_actions(logits, masks, cfg)
        hidden = trunk_actors(p, obs, glob, attention, heads=int(payload.get("heads", 4)))
        return obs, glob, attention, masks, logits, actions, hidden

    @jax.jit
    def outcome(p, s, t, actions):
        after = step(s, actions, t, cfg)
        final = continuation(p, after, t, jax.random.PRNGKey(seed))
        return final["winner"], final["done"]

    state = enemy_turn(state, tables)
    advance = jax.jit(lambda s, a, t: step(s, a, t, cfg))
    chunks = []
    for turn in range(turns):
        active = np.asarray(~state["done"] & (state["acting_team"] == 0))
        if not active.any():
            break
        obs, glob, attention, masks, logits, actions, hidden = inspect(params, state, tables)
        width = int(logits[head].shape[-1])
        scores = np.full((count, width), -1.0, np.float32)
        for candidate in range(width):
            changed = dict(actions)
            changed[head] = actions[head].at[:, 0].set(candidate)
            if head == "spell":
                target_logits = logits["target"][:, 0, candidate]
                target_mask = masks["target"][:, 0, candidate]
                target = jnp.argmax(jnp.where(target_mask, target_logits, -1e9), axis=-1)
                changed["target"] = actions["target"].at[:, 0].set(target)
            winner, done = outcome(params, state, tables, changed)
            if not np.asarray(done).all() or not np.isin(np.asarray(winner), (0, 1, 2)).all():
                raise ValueError("counterfactual did not reach a terminal state")
            legal = np.asarray(masks[head][:, 0, candidate]) & active
            scores[legal, candidate] = (np.asarray(winner)[legal] == 0).astype(np.float32)
        base = np.asarray(actions[head][:, 0])
        base_score = scores[np.arange(count), base]
        best = scores.argmax(axis=1)
        teacher = np.where(scores.max(axis=1) > base_score, best, base)
        chunks.append({"hidden": np.asarray(hidden[:, 0])[active],
                       "head_logits": np.asarray(logits[head][:, 0])[active],
                       "head_mask": np.asarray(masks[head][:, 0])[active],
                       "head_pick": base[active], "teacher_pick": teacher[active],
                       "action_win": scores[active], "row": chosen[active],
                       "turn": np.full(int(active.sum()), turn, int),
                       "train_mask": (np.arange(count) < int(0.75 * count))[active],
                       "obs": np.asarray(obs)[active], "glob": np.asarray(glob)[active],
                       "attention": np.asarray(attention)[active]})
        state = enemy_turn(advance(state, actions, tables), tables)
    if not chunks:
        raise ValueError("no surviving player decisions in sampled encounters")
    arrays = {k: np.concatenate([c[k] for c in chunks]) for k in chunks[0]}
    if any(hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() != digest
           for p, digest in identity.items()):
        raise ValueError("checkpoint or corpus changed while building counterfactual cache")
    arrays.update(cache_kind=np.asarray("raw_pvm_outcomes_v1"), head=np.asarray(head),
                  checkpoint_path=np.asarray(str(checkpoint)),
                  checkpoint_sha256=np.asarray(identity[str(checkpoint)]),
                  data_path=np.asarray(str(data_path)), data_sha256=np.asarray(identity[str(data_path)]),
                  config=np.asarray(json.dumps(cfg.__dict__, sort_keys=True)),
                  source_sha256=np.asarray(hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()))
    np.savez_compressed(out_path, **arrays)
    summary = {"decisions": len(arrays["row"]), "distinct_rows": len(np.unique(arrays["row"])),
               "head": head, "strict_improvements": int((arrays["teacher_pick"] != arrays["head_pick"]).sum()),
               "train_decisions": int(arrays["train_mask"].sum()),
               "heldout_decisions": int((~arrays["train_mask"]).sum()),
               "cache": str(out_path), "scope": "raw abstract PvM, greedy continuation versus heuristic"}
    print(json.dumps(summary, indent=1))
    return summary


def covariance_write(hidden, train, eligible, logits, wanted, mask, ridge):
    context = hidden[train]
    covariance = context.T @ context / len(context)
    scale = max(float(np.trace(covariance) / covariance.shape[0]), 1e-12)
    covariance += ridge * scale * np.eye(covariance.shape[0])
    keys = hidden[eligible]
    directions = np.linalg.solve(covariance, keys.T)
    residual = np.zeros((len(keys), logits.shape[1]))
    margins = np.max(np.where(mask[eligible], logits[eligible], -1e9), axis=1)
    targets = wanted[eligible]
    residual[np.arange(len(keys)), targets] = (
        margins - logits[eligible][np.arange(len(keys)), targets] + 1.0)
    # Minimum context-weighted disturbance subject to the edit-key logit constraints.
    return directions @ np.linalg.pinv(keys @ directions, rcond=1e-8) @ residual


def edit_outcome_head(z, out_path, payload, etas, cache_path, head, method="mean", ridge=0.01):
    """Program one linear readout and gate its exact cached counterfactual win score."""
    if head != str(z["head"].item()):
        raise ValueError("requested head differs from the counterfactual cache")
    hidden = z["hidden"].astype(np.float64)
    mask = z["head_mask"].astype(bool)
    train = z["train_mask"].astype(bool)
    if not train.any() or not (~train).any():
        raise ValueError("raw outcome edits require nonempty train and held-out decisions")
    if set(z["row"][train]) & set(z["row"][~train]):
        raise ValueError("a corpus row crosses the train/held-out boundary")
    weight, bias = (np.asarray(p, np.float64) for p in payload["params"][head])
    base_logits = hidden @ weight + bias
    picks = np.argmax(np.where(mask, base_logits, -1e9), axis=1)
    if not np.array_equal(picks, z["head_pick"]):
        raise ValueError("cached head choices do not reproduce from the source weights")
    wins = z["action_win"]
    rows = np.arange(len(picks))
    base_scores = wins[rows, picks]
    wanted = z["teacher_pick"].astype(int)
    eligible = train & (wins[rows, wanted] > base_scores)
    if not eligible.any():
        print(json.dumps({"verdict": "no strict terminal-win improvements to program", "head": head}))
        return 1
    if method == "covariance":
        if not np.isfinite(ridge) or ridge <= 0:
            raise ValueError("covariance ridge must be positive and finite")
        unit = covariance_write(hidden, train, eligible, base_logits, wanted, mask, ridge)
    elif method == "mean":
        unit = np.zeros_like(weight)
        for i in np.where(eligible)[0]:
            unit[:, wanted[i]] += hidden[i] / eligible.sum()
            unit[:, picks[i]] -= hidden[i] / eligible.sum()
    else:
        raise ValueError("unknown outcome write method")
    report = {"head": head, "method": method, "ridge": ridge,
              "edit_keys": int(eligible.sum()), "baseline_train_win": float(base_scores[train].mean()),
              "baseline_heldout_win": float(base_scores[~train].mean()), "candidates": []}
    best = None
    for eta in etas:
        candidate_weight = (weight + eta * unit).astype(np.float32)
        logits = hidden @ candidate_weight.astype(np.float64) + bias
        candidate = np.argmax(np.where(mask, logits, -1e9), axis=1)
        scores = wins[rows, candidate]
        row = {"eta": eta, "train_win": float(scores[train].mean()),
               "heldout_win": float(scores[~train].mean()),
               "changed": int((candidate != picks).sum()),
               "delta_norm": float(np.linalg.norm(candidate_weight.astype(np.float64) - weight))}
        report["candidates"].append(row)
        if best is None or row["train_win"] > best[0]["train_win"]:
            best = row, candidate_weight
    if best is None or best[0]["train_win"] <= report["baseline_train_win"]:
        report["verdict"] = "no training outcome gain; edit not written"
    elif best[0]["heldout_win"] < report["baseline_heldout_win"]:
        report["verdict"] = "held-out outcome regression; edit not written"
    else:
        payload["params"][head] = (best[1], bias.astype(np.float32))
        payload["weight_space_program"] = {"head": head, "eta": best[0]["eta"],
            "method": method, "ridge": ridge, "edit_keys": int(eligible.sum()),
            "cache": str(cache_path), "cache_kind": "raw_pvm_outcomes_v1",
            "baseline_train_win": report["baseline_train_win"],
            "baseline_heldout_win": report["baseline_heldout_win"], **best[0]}
        pathlib.Path(out_path).write_bytes(pickle.dumps(payload))
        report["wrote"] = str(out_path)
    print(json.dumps(report, indent=1))
    return 0 if "wrote" in report else 1


def solve_move_linear_probe(
    hidden: np.ndarray,
    teacher_move: np.ndarray,
    move_mask: Optional[np.ndarray] = None,
    split_share: float = 0.75,
    alphas: Sequence[float] = (1e-4, 1e-3, 1e-2, 0.1, 1.0, 10.0, 100.0),
    n_classes: int = 5,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """Solve the exact ridge linear-probe warm start in weight space for the move head.

    Minimizes:
        L(W, b) = 1/N_tr || X_tr W + b - Y_tr ||_F^2 + alpha * scale/N_tr || W ||_F^2
    over centered representations from trunk_actors.

    Returns:
        (W_best, b_best, report) where W is [D, n_classes], b is [n_classes].
    """
    hidden = np.asarray(hidden, dtype=np.float64)
    teacher = np.asarray(teacher_move, dtype=int)
    n = len(teacher)
    split = max(1, int(n * split_share))
    train_slice = slice(0, split)
    heldout_slice = slice(split, n)

    if move_mask is None:
        mask = np.ones((n, n_classes), dtype=bool)
    else:
        mask = np.asarray(move_mask, dtype=bool)
        if mask.shape[1] < n_classes:
            mask = np.pad(mask, ((0, 0), (0, n_classes - mask.shape[1])), constant_values=True)

    valid = (teacher >= 0) & (teacher < n_classes)
    valid &= mask[np.arange(n), np.clip(teacher, 0, n_classes - 1)]
    train_valid = valid[train_slice]
    train_indices = np.where(train_valid)[0]
    if len(train_indices) == 0:
        raise ValueError("no valid training boards with legal teacher move")

    X_tr = hidden[train_slice][train_indices]
    y_tr = teacher[train_slice][train_indices]
    D = X_tr.shape[1]

    Y_tr = np.zeros((len(y_tr), n_classes), dtype=np.float64)
    Y_tr[np.arange(len(y_tr)), y_tr] = 1.0

    X_mean = np.mean(X_tr, axis=0)
    Y_mean = np.mean(Y_tr, axis=0)
    X_tilde = X_tr - X_mean
    Y_tilde = Y_tr - Y_mean

    XTX = X_tilde.T @ X_tilde
    XTY = X_tilde.T @ Y_tilde
    scale = max(float(np.trace(XTX) / max(1, D)), 1e-12)

    def evaluate(W, b, sl):
        keep = valid[sl]
        if not keep.any():
            return 0.0
        logits = hidden[sl] @ W + b
        m = mask[sl]
        if m.shape[1] < W.shape[1]:
            m = np.pad(m, ((0, 0), (0, W.shape[1] - m.shape[1])), constant_values=True)
        preds = np.argmax(np.where(m, logits, -1e9), axis=1)
        return float((preds[keep] == teacher[sl][keep]).mean())

    candidates = []
    best_cand = None
    best_wb = None
    for alpha in alphas:
        reg = float(alpha) * scale * np.eye(D)
        W_cand = np.linalg.solve(XTX + reg, XTY)
        b_cand = Y_mean - X_mean @ W_cand
        train_acc = evaluate(W_cand, b_cand, train_slice)
        heldout_acc = evaluate(W_cand, b_cand, heldout_slice)
        cand = {
            "alpha": float(alpha),
            "train_agreement": round(train_acc, 4),
            "heldout_agreement": round(heldout_acc, 4),
            "weight_norm": float(np.linalg.norm(W_cand)),
        }
        candidates.append(cand)
        if (
            best_cand is None
            or cand["train_agreement"] > best_cand["train_agreement"]
            or (
                cand["train_agreement"] == best_cand["train_agreement"]
                and cand["heldout_agreement"] > best_cand["heldout_agreement"]
            )
        ):
            best_cand = cand
            best_wb = (W_cand, b_cand)

    report = {
        "n_boards": n,
        "train_boards": len(train_indices),
        "heldout_boards": int(valid[heldout_slice].sum()),
        "n_classes": n_classes,
        "candidates": candidates,
        "chosen": best_cand,
    }
    return best_wb[0], best_wb[1], report


def edit_move_head(z, out_path, payload, etas, cache_path, split_share=0.75, method="mean", ridge=0.01):
    """Rank-1 or exact ridge linear-probe move-head edit toward the direction the beam's walk implies.

    This is the head that answers the class the spell head cannot: on a board where the mask says
    "that spell is out of range", no logit on the spell head can cast it -- but walking in is a
    legal move, and it is the beam's own plan. The move head is a linear read of the same cached
    actor representation and feeds no other head, so every candidate eta is scored exactly.
    """
    weight = np.asarray(payload["params"]["move"][0], dtype=np.float64)
    bias = np.asarray(payload["params"]["move"][1], dtype=np.float64)
    hidden = z["hidden"].astype(np.float64)
    mask = z["move_mask"].astype(bool)
    teacher = z["teacher_move"].astype(int)
    head_pick = z["head_move"].astype(int)
    n = len(teacher)
    split = max(1, int(n * split_share))
    rows = np.arange(n)
    n_classes = mask.shape[1] if mask.ndim > 1 else 3
    if weight.shape[1] > n_classes:
        n_classes = weight.shape[1]
    if (teacher >= 0).any():
        n_classes = max(n_classes, int(np.max(teacher[teacher >= 0])) + 1)

    if method == "ridge":
        alphas = etas if etas else (1e-4, 1e-3, 1e-2, 0.1, 1.0, 10.0, 100.0)
        valid = (teacher >= 0) & (teacher < n_classes)
        m = mask
        if m.shape[1] < n_classes:
            m = np.pad(m, ((0, 0), (0, n_classes - m.shape[1])), constant_values=True)
        valid &= m[rows, np.clip(teacher, 0, n_classes - 1)]

        def agreement(W, b, sl):
            keep = valid[sl]
            if not keep.any():
                return 0.0
            cur_m = m[sl]
            logits = hidden[sl] @ W + b
            best = np.argmax(np.where(cur_m, logits, -1e9), axis=1)
            return float((best[keep] == teacher[sl][keep]).mean())

        if weight.shape[1] == n_classes:
            base_train = agreement(weight, bias, slice(0, split))
            base_test = agreement(weight, bias, slice(split, n))
        else:
            base_train = 0.0
            base_test = 0.0

        usable_count = int(valid[:split].sum())
        if usable_count == 0:
            print(json.dumps({"verdict": "no board where teacher move is legal; nothing to program",
                              "cache_boards": n}, indent=1))
            return 1

        W_opt, b_opt, probe_report = solve_move_linear_probe(
            hidden=hidden,
            teacher_move=teacher,
            move_mask=mask,
            split_share=split_share,
            alphas=alphas,
            n_classes=n_classes,
        )
        best_cand = probe_report["chosen"]
        report = {
            "head": "move",
            "method": "ridge",
            "boards": n,
            "train": split,
            "teacher_legal_boards": int(valid.sum()),
            "baseline_train_agreement": round(base_train, 4),
            "baseline_heldout_agreement": round(base_test, 4),
            "candidates": probe_report["candidates"],
            "chosen": best_cand,
        }
        if best_cand["train_agreement"] <= base_train + 1e-9 and base_train > 0:
            report["verdict"] = "no eta improved training agreement; edit not written"
            print(json.dumps(report, indent=1))
            return 1
        if best_cand["heldout_agreement"] < base_test - 1e-9:
            report["verdict"] = ("train agreement rose but held-out agreement fell; the direction "
                                 "memorised the slice, edit not written")
            print(json.dumps(report, indent=1))
            return 1

        payload["params"]["move"] = (W_opt.astype(np.float32), b_opt.astype(np.float32))
        payload["weight_space_program"] = {
            "head": "move",
            "method": "ridge",
            "alpha": best_cand["alpha"],
            "train_boards": probe_report["train_boards"],
            "cache": str(cache_path),
            "baseline_heldout_agreement": round(base_test, 4),
            "heldout_agreement": best_cand["heldout_agreement"],
        }
        pathlib.Path(out_path).write_bytes(pickle.dumps(payload))
        report["wrote"] = str(out_path)
        print(json.dumps(report, indent=1))
        return 0

    valid = (teacher >= 0) & mask[rows, np.clip(teacher, 0, mask.shape[1] - 1)]
    base_logits = hidden @ weight + bias
    picked = np.argmax(np.where(mask, base_logits, -1e9), axis=1)
    known = teacher >= 0
    if n and not bool((picked[known] == head_pick[known]).all()):
        print(json.dumps({"verdict": "the cached move choice is not the argmax of these weights; "
                                     "the counterfactual would not be exact, edit not written",
                          "reproduced": int((picked[known] == head_pick[known]).sum()),
                          "of": int(known.sum())}, indent=1))
        return 1
    usable = valid & (teacher != head_pick)
    count = int(usable[:split].sum())
    if count == 0:
        print(json.dumps({"verdict": "no board where the beam walks a direction the head did not "
                                     "choose and that direction is legal; nothing to program",
                          "cache_boards": n}, indent=1))
        return 1

    def agreement(logits, sl):
        keep = valid[sl]
        if not keep.any():
            return 0.0
        best = np.argmax(np.where(mask[sl], logits[sl], -1e9), axis=1)
        return float((best[keep] == teacher[sl][keep]).mean())

    base_train, base_test = agreement(base_logits, slice(0, split)), \
        agreement(base_logits, slice(split, n))
    report = {"head": "move", "boards": n, "train": split,
              "teacher_named_boards": int(known.sum()),
              "teacher_legal_boards": int(valid.sum()),
              "disagreements_with_legal_teacher": int(usable.sum()), "candidates": []}
    best = None
    for eta in etas:
        delta = np.zeros_like(weight)
        for index in np.where(usable[:split])[0]:
            delta[:, teacher[index]] += eta * hidden[index] / count
            delta[:, head_pick[index]] -= eta * hidden[index] / count
        row = {"eta": eta,
               "train_agreement": round(agreement(base_logits + hidden @ delta,
                                                  slice(0, split)), 4),
               "heldout_agreement": round(agreement(base_logits + hidden @ delta,
                                                    slice(split, n)), 4)}
        report["candidates"].append(row)
        if best is None or row["train_agreement"] > best[0]["train_agreement"]:
            best = (row, delta)
    report["baseline_train_agreement"] = round(base_train, 4)
    report["baseline_heldout_agreement"] = round(base_test, 4)
    report["chosen"] = best[0]
    if best[0]["train_agreement"] <= base_train + 1e-9:
        report["verdict"] = "no eta improved training agreement; edit not written"
        print(json.dumps(report, indent=1))
        return 1
    if best[0]["heldout_agreement"] < base_test - 1e-9:
        report["verdict"] = ("train agreement rose but held-out agreement fell; the direction "
                             "memorised the slice, edit not written")
        print(json.dumps(report, indent=1))
        return 1
    payload["params"]["move"] = ((weight + best[1]).astype(np.float32),
                                 bias.astype(np.float32))
    payload["weight_space_program"] = {"head": "move", "eta": best[0]["eta"],
                                       "train_boards": count, "cache": str(cache_path),
                                       "baseline_heldout_agreement": round(base_test, 4),
                                       "heldout_agreement": best[0]["heldout_agreement"]}
    pathlib.Path(out_path).write_bytes(pickle.dumps(payload))
    report["wrote"] = str(out_path)
    print(json.dumps(report, indent=1))
    return 0


def edit(cache_path, head, out_path, checkpoint, etas, teacher_class="all", method="mean", ridge=0.01):
    """Rank-1 head edit toward the teacher, sized from the measured margins.

    `W[:, teacher] += eta * h_mean` and `W[:, head] -= eta * h_mean` over the boards where the head
    and the teacher disagree *and the teacher's choice is legal*. The effect on a board with actor
    representation h is exactly `2 * eta * (h . h_mean)` on the logit gap, so the candidate etas are
    read against the cached margins instead of guessed, and the whole grid is scored exactly.
    """
    with np.load(cache_path, allow_pickle=False) as cached:
        z = dict(cached)
    if "checkpoint_path" not in z or "checkpoint_sha256" not in z:
        raise ValueError("cache has no checkpoint identity; rebuild it")
    source = pathlib.Path(checkpoint or str(z["checkpoint_path"].item())).resolve()
    source_bytes = source.read_bytes()
    digest = hashlib.sha256(source_bytes).hexdigest()
    if digest != str(z["checkpoint_sha256"].item()):
        raise ValueError("checkpoint does not match cache SHA-256")
    if pathlib.Path(out_path).resolve() == source:
        raise ValueError("edit output must not overwrite its source checkpoint")
    payload = pickle.loads(source_bytes)
    payload["wsp_source"] = {"path": str(source), "sha256": digest}
    if str(z.get("cache_kind", "")) == "raw_pvm_outcomes_v1":
        return edit_outcome_head(z, out_path, payload, etas, cache_path, head, method, ridge)
    if method not in ("mean", "ridge"):
        raise ValueError("covariance writes require a raw PvM outcome cache")
    if method == "ridge" and head != "move":
        raise ValueError("ridge linear probe is supported for move head")
    if head == "t_bias":
        return edit_target_bias(z, out_path, payload, etas)
    if head == "move":
        return edit_move_head(z, out_path, payload, etas, cache_path, split_share=0.75, method=method, ridge=ridge)
    if head != "spell":
        raise ValueError("this cache supports spell, t_bias and move edits")
    hidden = z["hidden"].astype(np.float64)
    mask = z["spell_mask"].astype(bool)
    teacher = z["teacher_spell"].astype(int)
    head_pick = z["head_spell"].astype(int)
    n = len(teacher)
    split = max(1, int(n * 0.75))
    usable = (teacher >= 0) & (teacher != head_pick) & mask[np.arange(n), teacher]
    # The two directions are not interchangeable in the shipped client. Pushing toward a cast the
    # board cannot realize hands the turn to `_realize`'s solver fallback, which raises the measured
    # win rate by substituting the teacher; pushing toward pass cannot create an unrealizable cast.
    # `--teacher-class` separates them so a duel delta can be attributed to the weights.
    pass_index = mask.shape[1] - 1
    if teacher_class == "pass":
        usable &= teacher == pass_index
    elif teacher_class == "cast":
        usable &= teacher < pass_index
    count = int(usable[:split].sum())
    if count == 0:
        print(json.dumps({"verdict": "no board where the teacher's spell is legal and unchosen; "
                                     "the disagreement is not in this head's weights",
                          "cache": str(cache_path)}, indent=1))
        return 1

    weight, bias = (np.asarray(payload["params"][head][0], dtype=np.float64).copy(),
                    np.asarray(payload["params"][head][1], dtype=np.float64).copy())
    base_logits = hidden @ weight + bias
    # Agreement is only defined where the teacher's own choice is legal. Boards the mask settles
    # against the teacher are a code problem, and leaving them in the denominator would cap every
    # arm at the same number and hide the edit's real effect.
    valid = (teacher >= 0) & mask[np.arange(n), teacher]
    report = {"boards": n, "train": split, "teacher_legal_boards": int(valid.sum()),
              "teacher_class": teacher_class,
              "disagreements_with_legal_teacher": int(usable.sum()), "candidates": []}

    def agreement(logits, sl):
        picked = np.argmax(np.where(mask, logits, -1e9), axis=1)
        keep = valid[sl]
        return float((picked[sl][keep] == teacher[sl][keep]).mean()) if keep.any() else 0.0

    base_train, base_test = agreement(base_logits, slice(0, split)), agreement(base_logits, slice(split, n))
    best = None
    for eta in etas:
        # Mean, not sum, over the disagreeing boards: eta then means "this many times the average
        # actor representation", so the grid is comparable as the sample grows.
        delta = np.zeros_like(weight)
        for index in np.where(usable[:split])[0]:
            delta[:, teacher[index]] += eta * hidden[index] / count
            delta[:, head_pick[index]] -= eta * hidden[index] / count
        candidate = base_logits + hidden @ delta
        row = {"eta": eta,
               "train_agreement": round(agreement(candidate, slice(0, split)), 4),
               "heldout_agreement": round(agreement(candidate, slice(split, n)), 4)}
        report["candidates"].append(row)
        if best is None or row["train_agreement"] > best[0]["train_agreement"]:
            best = (row, delta)
    report["baseline_train_agreement"] = round(base_train, 4)
    report["baseline_heldout_agreement"] = round(base_test, 4)
    report["chosen"] = best[0]
    if best[0]["train_agreement"] <= base_train + 1e-9:
        report["verdict"] = "no eta improved training agreement; edit not written"
        print(json.dumps(report, indent=1))
        return 1
    if best[0]["heldout_agreement"] < base_test - 1e-9:
        report["verdict"] = ("train agreement rose but held-out agreement fell; the direction "
                             "memorised the slice, edit not written")
        print(json.dumps(report, indent=1))
        return 1
    payload["params"][head] = ((weight + best[1]).astype(np.float32), bias.astype(np.float32))
    payload["weight_space_program"] = {"head": head, "eta": best[0]["eta"],
                                       "teacher_class": teacher_class,
                                       "train_boards": count, "cache": str(cache_path),
                                       "baseline_heldout_agreement": round(base_test, 4),
                                       "heldout_agreement": best[0]["heldout_agreement"]}
    pathlib.Path(out_path).write_bytes(pickle.dumps(payload))
    report["wrote"] = str(out_path)
    print(json.dumps(report, indent=1))
    return 0


def matched_control(reference_path, out_path, seed):
    reference_path = pathlib.Path(reference_path).resolve()
    reference_bytes = reference_path.read_bytes()
    reference = pickle.loads(reference_bytes)
    source_path = pathlib.Path(reference["wsp_source"]["path"]).resolve()
    source_bytes = source_path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != reference["wsp_source"]["sha256"]:
        raise ValueError("control source no longer matches the edit's source identity")
    if pathlib.Path(out_path).resolve() in (reference_path, source_path):
        raise ValueError("control must not overwrite reference or source")
    source = pickle.loads(source_bytes)
    head = reference["weight_space_program"]["head"]
    np.testing.assert_equal({k: v for k, v in source["params"].items() if k != head},
                            {k: v for k, v in reference["params"].items() if k != head})
    np.testing.assert_array_equal(source["params"][head][1], reference["params"][head][1])
    np.testing.assert_equal(source["config"], reference["config"])
    weight = np.asarray(source["params"][head][0], np.float64)
    delta = np.asarray(reference["params"][head][0], np.float64) - weight
    support = delta != 0
    norm = float(np.linalg.norm(delta))
    if not np.isfinite(norm) or norm == 0:
        raise ValueError("control requires a nonzero finite reference edit")
    noise = np.random.default_rng(seed).normal(size=weight.shape) * support
    noise *= norm / np.linalg.norm(noise)
    changed = (weight + noise).astype(np.float32)
    source["params"][head] = (changed, source["params"][head][1])
    source["wsp_source"] = reference["wsp_source"]
    source["weight_space_program"] = {"head": head, "method": "support_and_norm_matched_random",
        "seed": seed, "reference": str(reference_path),
        "reference_sha256": hashlib.sha256(reference_bytes).hexdigest(),
        "reference_norm": norm, "delta_norm": float(np.linalg.norm(changed - weight)),
        "reference_coordinates": int(support.sum()), "changed_coordinates": int((changed != weight).sum())}
    pathlib.Path(out_path).write_bytes(pickle.dumps(source))
    print(json.dumps(source["weight_space_program"], indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("cache", "cache-pvm", "edit", "control"))
    ap.add_argument("--reference", help="edited checkpoint to match in control mode")
    ap.add_argument("--turns", type=int, default=3, help="player decisions per row for cache-pvm")
    ap.add_argument("--boards", type=int, default=192)
    ap.add_argument("--data", default=None, help="encounter table reserved for weight editing")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--checkpoint", default=None,
                    help="cache source; edits default to the cache's recorded source")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--head", default="spell")
    ap.add_argument("--teacher-class", default="all", choices=("all", "pass", "cast"),
                    help="spell head only: learn only the direction toward the beam's pass, or "
                         "only the direction toward its casts. 'pass' cannot manufacture an "
                         "unrealizable cast, so its duel delta is not solver substitution")
    ap.add_argument("--method", choices=("mean", "covariance", "ridge"), default="mean",
                    help="raw outcome write: mean key direction or covariance-preserving constraints")
    ap.add_argument("--ridge", type=float, default=0.01, help="relative covariance regularization")
    ap.add_argument("--etas", default="0.05,0.1,0.25,0.5,1,2,4")
    args = ap.parse_args(argv)
    if __name__ == "__main__":
        os.environ.setdefault("DOFUS_FIGHT_BACKEND", "jax" if args.mode == "cache-pvm" else "numpy")
    if args.mode == "control":
        if not args.reference or not args.out:
            ap.error("control requires --reference and --out")
        return matched_control(args.reference, args.out, args.seed)
    if args.mode == "cache-pvm":
        if not args.data or not args.out:
            ap.error("cache-pvm requires --data and --out")
        cache_pvm(args.boards, args.seed, args.checkpoint or CHECKPOINT,
                  args.out, args.data, args.head, args.turns)
        return 0
    if args.mode == "cache":
        out = args.out or str(pathlib.Path(os.environ.get("TEMP", ".")) / "wsp_cache.npz")
        cache_boards(args.boards, args.seed, args.checkpoint or CHECKPOINT, out, args.data)
        return 0
    out = args.out or str(pathlib.Path(os.environ.get("TEMP", ".")) / ("wsp_%s_arm.pkl" % args.head))
    return edit(args.cache, args.head, out, args.checkpoint,
                [float(x) for x in args.etas.split(",")], args.teacher_class, args.method, args.ridge)


if __name__ == "__main__":
    raise SystemExit(main())
