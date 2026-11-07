"""Does the shipped fight net read the facts a lookup tool would hand it? Three arms.

The claim under test was made in conversation: *"a mob/boss lookup tool cannot raise in-fight
win rate, because the net is already fed the resistances."* Wiring is not usage. Arm `ablation`
measures usage: zero the five resistance columns in the observation, leave the physics untouched,
and count how many opening decisions and how many wins move.

Arms `structure` and `learn` test the other half of the argument -- that the deficit is in the
layers and not in the weights. `combat/encounter/model.py` scores targets as

    target[:, i, s, j] = att(x_i, x_j) + t_bias[s, j]

so the *difference between two spells' target rankings is a learned constant per actor slot*,
independent of what any actor is carrying. A boss whose kit says "dispel whoever holds the state"
is therefore not hard to learn; it is unrepresentable. `structure` measures that invariance on
real boards, and `learn` trains both architectures on a routing task whose labels need it, against
the ceiling a single ranking can reach.

    python tools/wsp_expressivity.py structure
    python tools/wsp_expressivity.py learn --steps 600
    python tools/wsp_expressivity.py ablation --boards 512
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import jax
import jax.numpy as jnp
import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from combat.encounter import mech_net, model
from combat.encounter.env import (Config, action_masks, initial_state, observe,
                                  step)
from combat.encounter.train import make_eval_rollout

CORPUS = Path(os.environ.get(
    "DOFUS_EVIDENCE", Path.home() / "Documents" / "dofus-evidence")) / "touch-data" \
    / "encounters_boss_eval_s4_2048.npz"
CHECKPOINT = REPO / "models" / "encounter_selfplay.pkl"
RES_COLUMNS = [8, 9, 10, 11, 12]      # observe()'s base block: the five resistance columns
N_ENEMIES = 4


def cfg_for(saved: Dict[str, Any]) -> Config:
    return Config(allies=int(saved.get("allies", 1)),
                  enemies=int(saved.get("enemies", N_ENEMIES)),
                  spells=int(saved.get("spells", 4)), max_rounds=24,
                  max_dist=int(saved.get("max_dist", 12)),
                  casts_per_turn=int(saved.get("casts_per_turn", 4)))


def load_corpus(path: Path, boards: int) -> Dict[str, Dict[str, jnp.ndarray]]:
    z = np.load(path)
    keys = [k for k in z.files if k not in
            ("level", "monster_id", "boss", "group", "_classes")]
    all_arrays = {k: jnp.asarray(z[k][:boards]) for k in keys}
    tables = {k: v for k, v in all_arrays.items() if k.startswith("spell_")}
    spec = {k: v for k, v in all_arrays.items() if not k.startswith("spell_")}
    return {"spec": spec, "tables": tables}


# --------------------------------------------------------------------- arm: structure
def spell_gap(target: np.ndarray, s1: int, s2: int) -> np.ndarray:
    """target diff between two spells per (board, actor, target-slot): what the board changes."""
    return target[:, 0, s1, :] - target[:, 0, s2, :]


def best_target_disagrees(target: np.ndarray, legal: np.ndarray) -> float:
    """How often two spells' best enemy target differs, over legal targets only."""
    t = np.asarray(target)[:, 0, :, 1:1 + N_ENEMIES]
    t = np.where(np.asarray(legal)[:, 0, :, 1:1 + N_ENEMIES], t, -1e9)
    best = t.argmax(axis=-1)
    return float(np.mean(best[:, 0] != best[:, 1]))


def arm_structure(args) -> Dict[str, Any]:
    data = load_corpus(CORPUS, args.boards)
    spec, tables = data["spec"], data["tables"]
    cfg = cfg_for({})
    state = initial_state(spec, tables, cfg)
    obs, glob, mask = observe(state, tables, cfg)
    n_spells = tables["spell_ap"].shape[2]
    spell_dim = 9
    actor_w = obs.shape[-1] - n_spells * spell_dim
    key = jax.random.PRNGKey(args.seed)
    old = model.init_params(key, obs.shape[-1], glob.shape[-1], cfg)
    new = mech_net.init_params(key, actor_w, spell_dim, glob.shape[-1], cfg)
    feats = {"actor": obs[..., :actor_w],
             "spell": obs[..., actor_w:].reshape(
                 obs.shape[0], obs.shape[1], n_spells, spell_dim),
             "glob": glob, "mask": mask}

    o = np.asarray(model.forward(old, obs, glob, mask)["target"])
    n = np.asarray(mech_net.forward(new, feats)["target"])
    g_old, g_new = spell_gap(o, 0, 1), spell_gap(n, 0, 1)
    bias = np.asarray(old["t_bias"])
    # The whole claim in one line: how much does the difference between "who does spell 0 hit"
    # and "who does spell 1 hit" depend on the board? A constant answers nothing.
    return {
        "boards": int(args.boards),
        "old_gap_std_across_boards": float(g_old.std(axis=0).mean()),
        "new_gap_std_across_boards": float(g_new.std(axis=0).mean()),
        "old_gap_equals_t_bias_difference_exactly": bool(
            np.allclose(g_old, bias[0] - bias[1], rtol=1e-4, atol=1e-4)),
        "old_spells_disagree_on_best_enemy_target": best_target_disagrees(
            o, action_masks(state, tables, cfg)["target"]),
        "new_spells_disagree_on_best_enemy_target": best_target_disagrees(
            n, action_masks(state, tables, cfg)["target"]),
    }


# --------------------------------------------------------------------- arm: learn
def make_boards(count: int, seed: int) -> Dict[str, np.ndarray]:
    """Boards where the right target depends on which spell is being cast."""
    rng = np.random.default_rng(seed)
    n_actor = 1 + N_ENEMIES
    hp = np.concatenate([rng.integers(150, 400, (count, 1)),
                         rng.integers(40, 300, (count, N_ENEMIES))], axis=1).astype(np.float64)
    shield = np.zeros((count, n_actor), bool)
    holder = rng.integers(1, n_actor, count)
    shield[np.arange(count), holder] = True
    low = 1 + np.argmin(hp[:, 1:], axis=1)
    label = np.zeros((count, n_actor, 2), np.int64)
    label[:, :, 0] = holder[:, None]
    label[:, :, 1] = low[:, None]
    alive = np.ones((count, n_actor), bool)
    dispel = np.zeros((count, n_actor, 2), bool)
    dispel[:, :, 0] = True
    return {"hp": hp, "shield": shield, "dispel": dispel, "label": label,
            "alive": alive, "spells": 2}


def board_features(boards: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    """One fair encoding for both architectures: the target's state, the spell's mechanic."""
    alive = boards["alive"].astype(np.float64)
    ratio = boards["hp"] / 400.0
    shield = boards["shield"].astype(np.float64)
    base = np.stack([alive, ratio, shield], axis=-1)                  # [B,N,3]
    spell = np.zeros((base.shape[0], base.shape[1], 2, 3))
    spell[:, :, :, 0] = boards["dispel"]                              # is this the dispel
    spell[:, :, :, 1] = 1.0 - boards["dispel"].astype(np.float64)     # is this the damage
    spell[:, :, :, 2] = 1.0                                           # same AP cost
    return {"base": base, "spell": spell}


def _adam_updates(lr: float):
    @jax.jit
    def update(p, m, v, t, grads):
        m_new = jax.tree_util.tree_map(lambda a, b: 0.9 * a + 0.1 * b, m, grads)
        v_new = jax.tree_util.tree_map(lambda a, b: 0.999 * a + 0.001 * b, v, grads)
        mh = jax.tree_util.tree_map(lambda a: a / (1 - 0.9 ** (t + 1)), m_new)
        vh = jax.tree_util.tree_map(lambda a: a / (1 - 0.999 ** (t + 1)), v_new)
        upd = jax.tree_util.tree_map(lambda pm, pv: lr * pm / (jnp.sqrt(pv) + 1e-8), mh, vh)
        return jax.tree_util.tree_map(lambda p, d: p - d, p, upd), m_new, v_new
    return update


def fit(arch: str, boards: Dict[str, np.ndarray], train: np.ndarray, steps: int,
        batch: int, seed: int, lr: float = 3e-3) -> Dict[str, float]:
    feats = board_features(boards)
    B, N = feats["base"].shape[:2]
    S = boards["spells"]
    label = jnp.asarray(boards["label"])
    base = jnp.asarray(feats["base"])
    spell = jnp.asarray(feats["spell"])
    mask = base[..., 0] > 0
    flat = jnp.concatenate([base, spell.reshape(B, N, S, 3).reshape(B, N, S * 3)], axis=-1)
    glob = jnp.zeros((B, 3))
    key = jax.random.PRNGKey(seed)
    if arch == "old":
        params = model.init_params(key, flat.shape[-1], glob.shape[-1],
                                   Config(allies=1, enemies=N_ENEMIES, spells=S))
        logits_of = lambda p: model.forward(
            p, flat, glob, mask)["target"][:, :, :S, :]
    else:
        params = mech_net.init_params(
            key, base.shape[-1], spell.shape[-1], glob.shape[-1],
            Config(allies=1, enemies=N_ENEMIES, spells=S))
        logits_of = lambda p: mech_net.forward(
            p, {"actor": base, "spell": spell, "glob": glob, "mask": mask})["target"][:, :, :S, :]

    def logits_at(p, idx):
        if arch == "old":
            full = model.forward(p, flat[idx], glob[idx], mask[idx])["target"]
        else:
            full = mech_net.forward(
                p, {"actor": base[idx], "spell": spell[idx], "glob": glob[idx],
                    "mask": mask[idx]})["target"]
        return full[:, :, :S, :]

    def loss(p, idx):
        full = logits_at(p, idx)
        chosen = jnp.take_along_axis(full, label[idx][:, :, :, None], axis=3)[:, :, :, 0]
        neg = jnp.where(mask[idx][:, :, None], chosen, -1e9)
        return -jnp.mean(jax.nn.log_softmax(neg, axis=-1))

    update = _adam_updates(lr)
    m = jax.tree_util.tree_map(jnp.zeros_like, params)
    v = jax.tree_util.tree_map(jnp.zeros_like, params)
    rng = np.random.default_rng(seed)
    order = rng.permutation(train)
    for t in range(steps):
        idx = jnp.asarray(order[(t * batch) % len(order):
                                (t * batch) % len(order) + batch])
        grads = jax.grad(loss)(params, idx)
        params, m, v = update(params, m, v, t, grads)

    best = np.asarray(logits_at(params, jnp.arange(B)))[:, 0].argmax(axis=-1)
    hold = np.asarray(boards["holdout"])
    lab = boards["label"]
    acc = [float((best[:, s][hold] == lab[:, 0, s][hold]).mean()) for s in range(S)]
    final = float(loss(params, jnp.asarray(train[:256])))
    if not np.isfinite(final):
        raise RuntimeError("the %s fit diverged to %s -- accuracy would be meaningless, so the "
                           "arm refuses instead of reporting 0%%" % (arch, final))
    return {"accuracy_dispel": acc[0], "accuracy_damage": acc[1],
            "accuracy_mean": float(np.mean(acc)), "final_loss": final}


def arm_learn(args) -> Dict[str, Any]:
    boards = make_boards(args.boards, args.seed)
    rng = np.random.default_rng(args.seed + 1)
    hold = np.zeros(args.boards, bool)
    hold[rng.permutation(args.boards)[:args.boards // 4]] = True
    boards["holdout"] = hold
    train = np.where(~hold)[0]
    lab = boards["label"]
    ceiling = float((lab[:, :, 0] == lab[:, :, 1])[hold].mean())
    out = {"boards": args.boards, "held_out": int(hold.sum()),
           "spells_on_board": boards["spells"],
           "single_ranking_ceiling": ceiling,
           "chance_on_four_enemies": 0.25}
    for arch in ("old", "new"):
        out[arch] = fit(arch, boards, train, args.steps, args.batch, args.seed, args.lr)
    return out


# --------------------------------------------------------------------- arm: ablation
def opening_decisions(params, state0, tables, cfg, observe_fn) -> Dict[str, np.ndarray]:
    masks = action_masks(state0, tables, cfg)
    obs, glob, mask = observe_fn(state0, tables, cfg)
    logits = model.forward(params, obs, glob, mask)
    acts = model.greedy_actions(logits, masks, cfg)
    return {k: np.asarray(v) for k, v in acts.items()}


def arm_ablation(args) -> Dict[str, Any]:
    payload = pickle.loads(CHECKPOINT.read_bytes())
    params = jax.tree_util.tree_map(jnp.asarray, payload["params"])
    cfg = cfg_for(dict(payload.get("config") or {}))
    data = load_corpus(CORPUS, args.boards)
    spec, tables = data["spec"], data["tables"]
    state0 = initial_state(spec, tables, cfg)

    import combat.encounter.train as train_module
    real_observe = train_module.observe

    def ablated(state, tbls, c):
        obs, glob, mask = real_observe(state, tbls, c)
        return obs.at[:, :, RES_COLUMNS].set(0.0), glob, mask

    keep = opening_decisions(params, state0, tables, cfg, real_observe)
    zero = opening_decisions(params, state0, tables, cfg, ablated)
    moved = {k: float(np.mean(keep[k][:, 0] != zero[k][:, 0]))
             for k in ("move", "spell", "target")}

    def run(observe_fn):
        train_module.observe = observe_fn
        try:
            rollout = jax.jit(make_eval_rollout(cfg, 2 * cfg.max_rounds,
                                                opponent="heuristic", player="greedy"))
            return np.stack([np.asarray(rollout(params, state0, tables,
                                                jax.random.PRNGKey(int(s)))["winner"])
                             for s in args.seeds])
        finally:
            train_module.observe = real_observe

    w_keep = run(real_observe)
    w_zero = run(ablated)
    # Paired by board: the same board either is won or is not, averaged over seeds, so the
    # spread the interval must carry is the board-to-board one. Taking the standard deviation
    # across the three seeds instead reports a number that shrinks with more seeds, not one
    # that shrinks with more boards -- measured on 512 boards here, and it printed +-0.0.
    paired = (w_keep == 0).astype(float) - (w_zero == 0).astype(float)
    per_board = paired.mean(axis=0)
    se = per_board.std(ddof=1) / np.sqrt(per_board.size)
    return {
        "checkpoint": CHECKPOINT.name,
        "boards": int(spec["hp"].shape[0]), "seeds": list(args.seeds),
        "win_rate_resistances_present": float(np.mean(w_keep == 0)),
        "win_rate_resistances_zeroed": float(np.mean(w_zero == 0)),
        "paired_delta_mean": float(per_board.mean()),
        "paired_delta_ci95": [float(-1.96 * se), float(1.96 * se)],
        "opening_decision_changed_by_zeroing": moved,
        "resistance_channel_is_inert": bool(
            abs(per_board.mean()) + 1.96 * se < 0.01
            and max(moved.values()) < 0.05),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("arm", choices=["structure", "learn", "ablation"])
    ap.add_argument("--boards", type=int, default=2048)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--steps", type=int, default=600)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    out = {"structure": arm_structure, "learn": arm_learn,
           "ablation": arm_ablation}[args.arm](args)
    print(json.dumps(out, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
