"""Read the fight policy the way weight-space programming asks a model to read it.

Two things have to be in front of a reader at the same time or the technique is just statistics:

1. BEHAVIOUR -- for each board, what the shipped policy chose, what the solver's best candidate
   would have earned, and the gap classified (wrong spell / wrong target / wrong move / fallback).
   Without this there is no failure mode to localise.
2. WEIGHTS WITH MEANING -- `enc1` is [49, 96] and its 49 rows *are* the observation channels
   (same_team, alive, hp_ratio, ap_ratio, mp_ratio, dist, initiative, power, 5 resists, then 4 spell
   slots x [ap, rmin, rmax, log dmg, log heal, element, ally, log dmg/ap, log heal/ap]); `glob` is
   [9, 96] (allies, enemies, round, our hp, their hp, our dist, their dist, our ap, their ap); and
   `t_bias` is a [4 spells, 5 actors] target prior -- 20 numbers that are directly interpretable as
   "which body this spell aims at". Row influence and the bias table are what a reader edits.

Run:  python tools/wsp_read.py [--rows 384] [--checkpoint path] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import pickle
import random
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
os.environ.setdefault("DOFUS_FIGHT_BACKEND", "numpy")
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

import numpy as np  # noqa: E402

BASE_CHANNELS = ["same_team", "alive", "hp_ratio", "ap_ratio", "mp_ratio", "dist",
                 "initiative", "power", "res0", "res1", "res2", "res3", "res4"]
SPELL_CHANNELS = ["ap", "range_min", "range_max", "log_damage", "log_heal", "element",
                  "is_ally", "log_dmg_per_ap", "log_heal_per_ap"]
GLOBAL_CHANNELS = ["allies_alive", "enemies_alive", "round", "our_hp", "their_hp",
                   "our_dist", "their_dist", "our_ap", "their_ap"]


def channel_labels(n_spells):
    labels = list(BASE_CHANNELS)
    for slot in range(n_spells):
        labels += ["spell%d.%s" % (slot, name) for name in SPELL_CHANNELS]
    return labels


def behaviour(report_rows, params, cfg, policy, pb, solver, data, cells, seed, rows):
    """Ask the shipped decision path what it would play, and what it cost."""
    from combat.plan_value import board_encounter  # noqa: F401

    rng = random.Random(seed)
    sample = rng.sample(range(int(data["hp"].shape[0])), min(rows, int(data["hp"].shape[0])))
    from combat.live_policy import LivePolicy  # noqa: F401
    for row in sample:
        board = pb.make_board(data, row, cells, rng)
        if board is None:
            continue
        candidates = solver.plan_candidates(board, 8)
        if len(candidates) < 2:
            continue
        best = max(candidates, key=lambda plan: plan.score)
        # The policy's own choice, through the same bundle the live server runs.
        try:
            slots, player_rows = policy._bind_slots(board)
            targets = policy._bind_targets(board)
            plan = policy._current_plan(board)
            tables, spec = board_encounter(
                board, plan, cfg, first_team=0, player_rows=player_rows, as_jax=False)
            logits, masks = policy._bundle(policy.params, spec, tables,
                                           float(max(0, board.my_ap)), float(max(0, board.my_mp)),
                                           int(board.round or 0))
            move, spell_slot, target_slot, probability, value = policy._choose(logits, masks)
        except Exception as exc:  # noqa: BLE001 - localise, do not hide
            report_rows.append({"row": int(row), "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        report_rows.append({
            "row": int(row),
            "move": int(move), "spell_slot": int(spell_slot), "target_slot": int(target_slot),
            "probability": round(float(probability), 4), "value": round(float(value), 4),
            "best_damage": round(float(best.expected_damage), 1),
            "best_kills": int(len(best.kills)),
            "n_candidates": len(candidates),
            # Same indexing the shipped selector uses: batch 0, actor 0 (the player).
            "target_logits": [round(float(x), 3)
                              for x in np.asarray(logits["target"])[0, 0, spell_slot]],
            "spell_logits": [round(float(x), 3) for x in np.asarray(logits["spell"])[0, 0]],
            "move_logits": [round(float(x), 3) for x in np.asarray(logits["move"])[0, 0]],
        })
    return report_rows


def weight_reading(params, n_spells, n_actors):
    """Per-channel influence and the semantic tables a reader would edit."""
    out = {}
    enc1 = np.asarray(params["enc1"][0], dtype=np.float64)
    labels = channel_labels(n_spells)
    influence = [round(float(np.sqrt((enc1[index] ** 2).mean())), 4) for index in range(len(labels))]
    pairs = sorted(zip(labels, influence), key=lambda item: -item[1])
    out["enc1_row_rms"] = dict(zip(labels, influence))
    out["enc1_top10_channels"] = pairs[:10]
    out["enc1_bottom10_channels"] = pairs[-10:]
    glob = np.asarray(params["glob"][0], dtype=np.float64)
    out["glob_row_rms"] = {GLOBAL_CHANNELS[i]: round(float(np.sqrt((glob[i] ** 2).mean())), 4)
                           for i in range(min(glob.shape[0], len(GLOBAL_CHANNELS)))}
    t_bias = np.asarray(params["t_bias"], dtype=np.float64)
    out["t_bias_shape"] = list(t_bias.shape)
    out["t_bias"] = [[round(float(v), 3) for v in row] for row in t_bias]
    out["t_bias_row_spread"] = [round(float(row.max() - row.min()), 3) for row in t_bias]
    out["t_bias_flat"] = bool(np.allclose(t_bias, 0.0))
    for name in ("move", "spell", "t_q", "t_k", "value1", "value2"):
        leaf = params[name]
        weight = np.asarray(leaf[0], dtype=np.float64)
        bias = np.asarray(leaf[1], dtype=np.float64)
        out["head_%s_shape" % name] = [list(weight.shape), list(bias.shape)]
        out["head_%s_bias" % name] = [round(float(v), 4) for v in bias.reshape(-1)][:12]
    return out


def main(argv=None):
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--rows", type=int, default=384)
    args.add_argument("--seed", type=int, default=7)
    args.add_argument("--checkpoint", default=str(BRAIN / "models" / "encounter_selfplay.pkl"))
    args.add_argument("--json", default=None)
    args = args.parse_args(argv)

    from combat import plan_benchmark as pb
    from combat.battle_solver import BattleSolver
    from combat.encounter.backend import BACKEND
    from combat.encounter.checkpoint import config_from_payload
    from combat.live_policy import LivePolicy

    payload = pickle.loads(pathlib.Path(args.checkpoint).read_bytes())
    cfg = config_from_payload(payload)
    data = np.load(str(pb.DEFAULT_DATA))
    cells = pb.load_map(pathlib.Path(pb.DEFAULT_MAP))
    solver = BattleSolver(beam_width=16, max_actions=3)
    policy = LivePolicy(checkpoint=pathlib.Path(args.checkpoint), beam_width=16, max_actions=3)

    rows = []
    behaviour(rows, policy.params, cfg, policy, pb, solver, data, cells, args.seed, args.rows)
    errors = [r for r in rows if "error" in r]
    chosen = [r for r in rows if "error" not in r]
    summary = {
        "backend": BACKEND,
        "boards_attempted": len(rows),
        "policy_decisions": len(chosen),
        "binding_errors": len(errors),
        "first_error": (errors[0]["error"] if errors else None),
        "probability_mean": round(float(np.mean([r["probability"] for r in chosen])), 4) if chosen else None,
        "probability_at_max": sum(1 for r in chosen if r["probability"] > 0.999) if chosen else None,
        "value_at_extreme": sum(1 for r in chosen if abs(r["value"]) > 0.999) if chosen else None,
        "spell_slot_counts": {str(slot): sum(1 for r in chosen if r["spell_slot"] == slot)
                              for slot in range(cfg.spells)},
        "target_slot_counts": {str(slot): sum(1 for r in chosen if r["target_slot"] == slot)
                              for slot in range(cfg.enemies + 1)},
        "move_counts": {str(move): sum(1 for r in chosen if r["move"] == move) for move in (0, 1, 2)},
    }
    reading = weight_reading(policy.params, cfg.spells, cfg.enemies + 1)
    document = {"summary": summary, "weights": reading, "episodes": chosen[:6]}
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(document, indent=1), encoding="utf-8")
    print(json.dumps({"summary": summary, "weights": reading}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
