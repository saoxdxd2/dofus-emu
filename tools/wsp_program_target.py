"""Write the relational target head in closed form, from what each candidate pair actually won.

`dofus-network/tools/wsp_expressivity.py structure` measured the defect this tool repairs: in the
shipped net the difference between "who spell 0 hits" and "who spell 1 hits" is exactly
`t_bias[0] - t_bias[1]` on 2,048 real boss boards -- a learned constant, standard deviation 0.000
across boards. No weight edit and no amount of training can make that head route a spell by what
its target carries, because the quantity it would need does not exist inside it.

`combat/encounter/mech_net.py` has one: `joint(spell, target) = q(spell) . k(target)`, which is
bilinear in the two tokens and therefore *linear in the relational matrix* `M = t_q t_k^T`. Linear
means a closed-form fit is possible. This is supervised ridge regression, not direct numerical WSP:

  1. walk the reference fight; at every state where the player has a decision, enumerate every
     legal (spell, target) pair;
  2. apply that pair and play the rest of the fight out in the real environment under the fixed
     scripted continuation, recording whether the player won. The label is a terminal outcome, so
     the currency is verified victory and no score is a proxy for it;
  3. regress the outcomes on `spell_token (x) target_token` by ridge -- 256 unknowns at the default
     width, one `np.linalg.solve`;
  4. install it exactly (`t_q = M`, `t_k = I`, slot bias and spell prior zeroed) and check the
     written network's joint equals `s^T M a` to float32;
  5. score on boards never used for labelling, paired per board: against the same network with the
     write removed (isolates the write), against uniform-legal pair choice, against a lowest-index
     target control, and against the trained shipped checkpoint.

The trunk is frozen and randomly initialized; only the relational head is fitted to outcome labels.
This tests a supervised readout, not a hand-diagnosed coordinate edit. If the fitted head does
not beat the unwritten network on held-out wins, the tool refuses to save.

    python tools/wsp_program_target.py --boards 128 --holdout 512
    python tools/wsp_program_target.py --boards 128 --score-only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import pickle
import sys
import time
from typing import Any, Callable, Dict, Optional, Tuple

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

import jax                                                       # noqa: E402
import jax.numpy as jnp                                          # noqa: E402
import numpy as np                                               # noqa: E402

from combat.encounter import mech_net, model                      # noqa: E402
from combat.encounter.env import (Config, MECH_TABLES, action_masks,       # noqa: E402
                                  initial_state, observe, step)
from combat.encounter.heuristic import heuristic_actions           # noqa: E402
from combat.encounter.train import (make_baseline_rollout,         # noqa: E402
                                    make_eval_rollout)

EVIDENCE = pathlib.Path(os.environ.get(
    "DOFUS_EVIDENCE", pathlib.Path.home() / "Documents" / "dofus-evidence")) / "touch-data"
DEV = EVIDENCE / "encounters_boss_s4_2048.npz"
HOLD = EVIDENCE / "encounters_boss_hold_s4_2048.npz"
CHECKPOINT = BRAIN / "models" / "encounter_selfplay.pkl"
OUT = EVIDENCE.parent / "encounter_target_wsp.pkl"
SKIP_KEYS = ("level", "monster_id", "boss", "group", "_classes")


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def ruler_digest() -> str:
    """Hash of the code that *makes* the labels, not just the corpus that feeds them.

    A cached label set matched the old meta on seed, width, move set and corpus digests, and none of
    those change when the ruler changes. Fixing which states count as decisions has to invalidate
    caches that were labelled before the fix, or the corrected tool would score the old diluted
    labels and report the corrected number from uncorrected data.
    """
    parts = [pathlib.Path(__file__).read_bytes()]
    for name in ("env.py", "heuristic.py", "mech_net.py"):
        parts.append((BRAIN / "combat" / "encounter" / name).read_bytes())
    return hashlib.sha256(b"".join(parts)).hexdigest()[:16]


def load(path: pathlib.Path, boards: int) -> Tuple[Dict[str, jnp.ndarray],
                                                   Dict[str, jnp.ndarray]]:
    z = np.load(path)
    arrays = {k: jnp.asarray(z[k][:boards]) for k in z.files if k not in SKIP_KEYS}
    tables = {k: v for k, v in arrays.items() if k.startswith("spell_")}
    spec = {k: v for k, v in arrays.items() if not k.startswith("spell_")}
    return spec, tables


def spell_features(tables: Dict[str, jnp.ndarray]) -> int:
    """How many numbers `observe` writes per spell slot: 9, or 14 with the mechanic columns.

    Derived from the tables rather than passed in, because the width is what decides whether the
    token the matrix is solved in can even see a pushback. A corpus that carries the columns but is
    read at 9 would silently price the write without the mechanics it claims to test.
    """
    return 9 + (5 if all(key in tables for key in MECH_TABLES) else 0)


def typed(obs: jnp.ndarray, glob: jnp.ndarray, attn: jnp.ndarray,
          tables: Dict[str, jnp.ndarray], cfg: Config,
          spell_feats: Optional[int] = None) -> Dict[str, jnp.ndarray]:
    """Split `observe`'s flat actor vector into the typed tokens `mech_net` reads.

    The table can carry more spell columns than the config allows spells; the head is defined
    over the ones the actor may actually cast, so the slice is what keeps the joint's spell axis
    and `t_bias` the same length.
    """
    columns = tables["spell_ap"].shape[2]
    spell_feats = spell_features(tables) if spell_feats is None else spell_feats
    base = obs.shape[-1] - columns * spell_feats
    return {"actor": obs[..., :base],
            "spell": obs[..., base:].reshape(obs.shape[0], obs.shape[1], columns,
                                             spell_feats)[:, :, :cfg.spells],
            "glob": glob, "mask": attn}


def incumbent_player(params: Dict[str, Any]) -> Callable:
    """The trained shipped checkpoint, greedy -- used as the continuation when the question is
    whether a decision matters to a *competent* player.

    The reference line and the counterfactual continuations both default to the scripted opponent.
    A weak continuation can make two candidates finish the same way for the wrong reason: it loses
    the fight anyway, so the pair looks irrelevant and the measured ceiling understates the true
    one. Re-labelling under the trained policy is what turns "3.1% of states are decided" from an
    artifact of the continuation into a claim about the fights.
    """
    def player(state, tables, cfg):
        obs, glob, attn = observe(state, tables, cfg)
        logits = model.forward(params, obs, glob, attn)
        return model.greedy_actions(logits, action_masks(state, tables, cfg), cfg)
    return player


def make_player_rollout(cfg: Config, length: int,
                        player: Callable[[Dict[str, jnp.ndarray], Dict[str, jnp.ndarray],
                                          Config], Dict[str, jnp.ndarray]]) -> Callable:
    """Monsters scripted, player whatever `player` says -- the same shape as eval_rollout."""
    def rollout(state0, tables):
        def body(state, _):
            mine = player(state, tables, cfg)
            theirs = heuristic_actions(state, tables, cfg)
            use = state["acting_team"] == 0
            acts = {k: jnp.where(use[:, None], mine[k], theirs[k])
                    for k in ("move", "spell", "target")}
            return step(state, acts, tables, cfg), None
        state, _ = jax.lax.scan(body, state0, None, length=length)
        return state
    return jax.jit(rollout)


# ----------------------------------------------------------------------- labelling
def feature_token_params(base_dim: int, spell_dim: int, global_dim: int, hidden: int,
                         cfg: Config, seed: int) -> Dict[str, Any]:
    """Tokens are the features themselves, not a random projection of them.

    The closed-form write was measured against a frozen random trunk and lost badly, which is not
    a verdict on the write: it says the matrix was solved in a token space that carries no trained
    structure. This construction removes the trunk as a contributor -- the encoders are padded
    identity and every block's output and MLP are zeroed, so a forward pass leaves each token as
    `relu(feature)`. Anything the written head does is then attributable to the solved relational
    matrix and nothing else, and the matched control is the same network with a random matrix of
    the same norm.
    """
    if hidden < max(base_dim, spell_dim):
        raise SystemExit("identity tokens need hidden >= max(%d, %d), got %d"
                         % (base_dim, spell_dim, hidden))
    key = jax.random.PRNGKey(seed)
    params = mech_net.init_params(key, base_dim, spell_dim, global_dim, cfg,
                                  hidden=hidden, heads=4)
    # encoder weights are (fan_in, fan_out), so the identity lives in the top-left block of a
    # (feature_dim, hidden) rectangle -- not in a square, which is what the first attempt built
    # and the matmul rejected.
    eye_actor = jnp.zeros((base_dim, hidden), jnp.float32)
    eye_spell = jnp.zeros((spell_dim, hidden), jnp.float32)
    params["actor_enc"] = (eye_actor.at[:base_dim, :base_dim].set(jnp.eye(base_dim)),
                           jnp.zeros((hidden,), jnp.float32))
    params["spell_enc"] = (eye_spell.at[:spell_dim, :spell_dim].set(jnp.eye(spell_dim)),
                           jnp.zeros((hidden,), jnp.float32))
    blocks = []
    for block in params["blocks"]:
        blocks.append({**block,
                       "out": (jnp.zeros_like(block["out"][0]),
                               jnp.zeros_like(block["out"][1])),
                       "mlp2": (jnp.zeros_like(block["mlp2"][0]),
                                jnp.zeros_like(block["mlp2"][1]))})
    params["blocks"] = blocks
    return params


def pick_heads(hidden: int) -> int:
    """Attention splits the width across heads, so the width has to divide. A small closed-form
    solve wants a small width, so the head count comes down rather than the width going up."""
    for heads in (4, 2, 1):
        if hidden % heads == 0:
            return heads
    raise SystemExit("hidden=%d divides by no head count" % hidden)


def collect_labels(params: Dict[str, Any], spec: Dict[str, jax.Array],
                   tables: Dict[str, jax.Array], cfg: Config,
                   continuation, hidden: int, heads: int = 4,
                   moves: Tuple[int, ...] = (1,),
                   policy: Optional[Callable] = None) -> Dict[str, np.ndarray]:
    """Every legal (move, spell, target) on the reference line, with its terminal outcome.

    `moves` defaults to holding still. Passing (0, 1, 2) adds the axis the pair ceiling does not
    bound: the walk changes which spells are in range at all, so it changes the legal set instead
    of reordering it -- which is why the turn-order fix was worth eight times the best action-head
    edit. The heuristic's own pick is recorded per state so the move can be measured at a fixed
    spell and target, and the two ceilings stay separable.
    """
    act = policy or (lambda s, t: heuristic_actions(s, t, cfg))
    state = initial_state(spec, tables, cfg)
    n_spells = cfg.spells
    n_actor = cfg.n_actors
    grid = [(m, s, t) for m in moves for s in range(n_spells) for t in range(n_actor)]
    cand = np.asarray(grid, dtype=np.int64)                       # [C, 3] = move, spell, target
    C = cand.shape[0]
    keys = ("spell_token", "target_token", "actor_token", "label", "board", "step",
            "cand_move", "cand_spell", "cand_target", "base_spell", "base_target")
    parts = {k: [] for k in keys}
    for t in range(2 * cfg.max_rounds):
        acting = np.asarray(state["acting_team"])
        # `env.step` freezes `acting_team` once a board is decided, so without the `~done` half every
        # step after the fight is over is still counted as "the player had a decision". Those states
        # can never be mixed -- the winner is already fixed -- so they only dilute the share, and they
        # diluted it by 5x: 192 mechanic boards give 1.79% decided states with them and 9.31% without,
        # measured both ways by tools/party_leverage.py. The published 3.1%/2.76%/3.82% ceiling figures
        # were computed over the diluted denominator and are being restated.
        #
        # Why `~done` is not redundant with `acting == 0`, which is what an earlier test of this line
        # failed to prove: a board that ends on the monsters' turn is excluded twice over, so the
        # filter only bites when the *player's* own cast finished the fight. Then no monster is alive
        # and every hostile pair is illegal -- but a friendly-target spell (a heal, or a summon) is
        # still legal, so the state still enters the denominator. Measured over 256 boards per corpus
        # by dofus-evidence/check_frozen_candidate_filter.py: of 2,479 player rows on decided boards,
        # 527 still carry a legal (spell, target) pair; on the 4v4 corpus it is 1,151 of 10,173.
        # The fixture in tools/test_wsp_program_target.py::LiveStateTests carries an ally spell for
        # exactly that reason, and its non-vacuity arm says so if someone removes it.
        rows = np.where((acting == 0) & ~np.asarray(state["done"]))[0]
        if rows.size:
            sub = {k: v[rows] for k, v in state.items()}
            sub_tables = {k: v[rows] for k, v in tables.items()}
            masks = action_masks(sub, sub_tables, cfg)
            legal_target = np.asarray(masks["target"])[:, 0, :, :]          # [R, S+1, N]
            legal_spell = np.asarray(masks["spell"])[:, 0, :]               # [R, S+1]
            keep = legal_target[:, cand[:, 1], cand[:, 2]] & \
                legal_spell[:, cand[:, 1]]
            obs, glob, attn = observe(sub, sub_tables, cfg)
            feats = typed(obs, glob, attn, sub_tables, cfg)
            actors, spell_tokens, _ = mech_net.trunk(params, feats["actor"],
                                                     feats["spell"], feats["glob"],
                                                     feats["mask"], heads=heads)
            base = act(sub, sub_tables)
            reps = np.repeat(np.arange(rows.size), C)
            tiled = np.tile(cand, (rows.size, 1))                           # [R*C, 3]
            big = {k: jnp.asarray(np.repeat(np.asarray(v), C, axis=0))
                   for k, v in sub.items()}
            big_tables = {k: jnp.asarray(np.repeat(np.asarray(v), C, axis=0))
                          for k, v in sub_tables.items()}
            acts = dict(act(big, big_tables))
            for axis, column in (("move", 0), ("spell", 1), ("target", 2)):
                acts[axis] = acts[axis].at[:, 0].set(jnp.asarray(tiled[:, column]))
            final = continuation(step(big, acts, big_tables, cfg), big_tables)
            won = (np.asarray(final["winner"]) == 0).astype(np.float64)
            valid = keep.ravel()
            parts["spell_token"].append(np.asarray(spell_tokens)[reps, 0,
                                                                  tiled[:, 1]][valid])
            parts["target_token"].append(np.asarray(actors)[reps, tiled[:, 2]][valid])
            parts["actor_token"].append(np.asarray(actors)[reps, 0][valid])
            parts["label"].append((2.0 * won - 1.0)[valid])
            parts["board"].append(rows[reps][valid])
            parts["step"].append(np.full(int(valid.sum()), t))
            for name, source in (("cand_move", tiled[:, 0]), ("cand_spell", tiled[:, 1]),
                                 ("cand_target", tiled[:, 2]),
                                 ("base_spell", np.repeat(
                                     np.asarray(base["spell"])[:, 0], C)),
                                 ("base_target", np.repeat(
                                     np.asarray(base["target"])[:, 0], C))):
                parts[name].append(source[valid])
        state = step(state, act(state, tables), tables, cfg)
        if bool(np.asarray(state["done"]).all()):
            break
    return {k: np.concatenate(v, axis=0) for k, v in parts.items() if v}


def decision_leverage(cache: Dict[str, np.ndarray], subset: np.ndarray,
                      name: str) -> Dict[str, float]:
    """How often the axis under test flips the terminal outcome, over the states it can act on.

    This is the ceiling, not a quality score: a weight edit that routes this axis perfectly can
    win at most the share of states where the choice decides the fight.
    """
    groups: Dict[Tuple[int, int], List[int]] = {}
    for b, s, lab in zip(cache["board"][subset].tolist(), cache["step"][subset].tolist(),
                         cache["label"][subset].tolist()):
        wins, losses = groups.setdefault((b, s), [0, 0])
        if lab > 0:
            groups[(b, s)][0] = wins + 1
        else:
            groups[(b, s)][1] = losses + 1
    states = len(groups)
    winnable = sum(1 for w, l in groups.values() if w > 0)
    mixed = sum(1 for w, l in groups.values() if w > 0 and l > 0)
    return {"%s_states" % name: states,
            "%s_winnable_share" % name: winnable / max(states, 1),
            "%s_decides_share" % name: mixed / max(states, 1),
            "%s_decides_count" % name: mixed}


def solve_relational(cache: Dict[str, np.ndarray], train: np.ndarray, hidden: int,
                     lam: float) -> np.ndarray:
    s = cache["spell_token"][train][:, :hidden]
    a = cache["target_token"][train][:, :hidden]
    y = cache["label"][train]
    X = (s[:, :, None] * a[:, None, :]).reshape(len(train), hidden * hidden)
    return np.linalg.solve(X.T @ X + lam * np.eye(hidden * hidden), X.T @ y
                           ).reshape(hidden, hidden)


def solve_move(cache: Dict[str, np.ndarray], train: np.ndarray, hidden: int,
               lam: float) -> np.ndarray:
    """Fit move-specific terminal outcomes by ridge regression on actor tokens."""
    x = cache["actor_token"][train][:, :hidden]
    y = cache["label"][train]
    m = cache["cand_move"][train].astype(int)
    # Each row says "taking move m at this state produced this outcome", so the design row is the
    # token gated by that move's column: kron(x, onehot(m)). 45 unknowns at hidden=15, not 256.
    onehot = np.eye(3)[m]
    X = (x[:, :, None] * onehot[:, None, :]).reshape(len(train), hidden * 3)
    return np.linalg.solve(X.T @ X + lam * np.eye(hidden * 3), X.T @ y
                           ).reshape(hidden, 3)


def install_move(params: Dict[str, Any], W: np.ndarray, hidden: int) -> Dict[str, Any]:
    written = {k: (dict(v) if isinstance(v, dict) else v) for k, v in params.items()}
    written["move"] = (jnp.asarray(W, jnp.float32), jnp.zeros((3,), jnp.float32))
    return written


def install(params: Dict[str, Any], M: np.ndarray, hidden: int) -> Dict[str, Any]:
    """Delegate to the module that owns the head: `t_q = sqrt(hidden) M`, `t_k = I`, no slot bias."""
    return mech_net.install_relational(params, M, hidden)


def mech_player(params: Dict[str, Any], heads: int = 4) -> Callable:
    def player(state, tables, cfg):
        obs, glob, attn = observe(state, tables, cfg)
        logits = mech_net.forward(params, typed(obs, glob, attn, tables, cfg), heads)
        return mech_net.greedy_actions(logits, action_masks(state, tables, cfg), cfg)
    return player


def scripted_move_player(params: Dict[str, Any], heads: int = 4) -> Callable:
    """The written spell and target, but the walk taken from the scripted opponent.

    Every arm of a freshly initialised `mech_net` inherits a random move head, whose argmax is
    effectively a constant. Comparing those arms with a trained checkpoint would then be measuring
    the move head and not the write, so this arm removes that handicap from the comparison.
    """
    inner = mech_player(params, heads)

    def player(state, tables, cfg):
        acts = inner(state, tables, cfg)
        scripted = heuristic_actions(state, tables, cfg)
        return {**acts, "move": scripted["move"]}
    return player


def random_player(key0: int) -> Callable:
    def player(state, tables, cfg):
        masks = action_masks(state, tables, cfg)
        neg = jnp.float32(-1e9)
        key = jax.random.PRNGKey(key0)
        k_move, k_spell, k_target = jax.random.split(key, 3)
        move = jax.random.categorical(k_move, jnp.where(masks["move"], 0.0, neg))
        spell = jax.random.categorical(k_spell, jnp.where(masks["spell"], 0.0, neg))
        chosen = jnp.take_along_axis(masks["target"], spell[..., None, None], axis=2)[:, :, 0, :]
        target = jax.random.categorical(k_target, jnp.where(chosen, 0.0, neg))
        return {"move": move, "spell": spell,
                "target": jnp.where(spell < cfg.spells, target, 0)}
    return player


def fixed_slot_player(params: Dict[str, Any], heads: int = 4) -> Callable:
    """The written spell choice, but always the lowest-numbered legal target: the control that
    separates 'the ranking is spell-conditioned' from 'the spell head got better'."""
    inner = mech_player(params, heads)

    def player(state, tables, cfg):
        acts = inner(state, tables, cfg)
        masks = action_masks(state, tables, cfg)
        legal = masks["target"]
        idx = jnp.arange(cfg.n_actors, dtype=jnp.int32)
        first = jnp.min(jnp.where(legal, idx[None, None, None, :], cfg.n_actors), axis=-1)
        target = jnp.take_along_axis(first, acts["spell"][..., None], axis=2)[:, :, 0]
        return {**acts, "target": jnp.where(acts["spell"] < cfg.spells, target, 0)}
    return player


def group_metrics(board: np.ndarray, step: np.ndarray, score: np.ndarray,
                  label: np.ndarray) -> Dict[str, float]:
    """Per decision state: when the pairs disagree about the outcome, does the solved ranking
    pick one that wins?

    States where every legal pair wins are not a ranking problem -- the heuristic line was already
    winning, so any deviation still won -- and pooling them inflates any scorer. The honest sample
    is the mixed states, where the choice decides the fight, so that is what this reports, with
    the share of states that are winnable, losing-everywhere and mixed counted separately.
    """
    best: Dict[Tuple[int, int], float] = {}
    worst: Dict[Tuple[int, int], float] = {}
    argmax: Dict[Tuple[int, int], int] = {}
    members: Dict[Tuple[int, int], list] = {}
    for i, (b, s, sc, lb) in enumerate(zip(board.tolist(), step.tolist(),
                                           score.tolist(), label.tolist())):
        key = (b, s)
        if lb > best.get(key, -2.0):
            best[key] = lb
        if lb < worst.get(key, 2.0):
            worst[key] = lb
        if key not in argmax or sc > score[argmax[key]]:
            argmax[key] = i
        members.setdefault(key, []).append(lb)
    winnable = [k for k, v in best.items() if v > 0]
    mixed = [k for k in winnable if worst[k] < 0]
    if not mixed:
        # Not a failure to report as an error: "no state here is decided by the pair" *is* the
        # finding, and the win rates below are still worth reading. It does mean no ranking claim
        # can be made, which the gate turns into a refusal to write.
        return {"states": float(len(best)),
                "states_where_a_pair_wins": len(winnable) / len(best),
                "states_where_the_choice_decides": 0.0,
                "states_where_the_choice_decides_count": 0,
                "solved_picks_a_winning_pair": None,
                "chance_picks_a_winning_pair": None}
    solved = float(np.mean([label[argmax[k]] > 0 for k in mixed]))
    chance = float(np.mean([np.mean(np.asarray(members[k]) > 0) for k in mixed]))
    return {"states": float(len(best)), "states_where_a_pair_wins": len(winnable) / len(best),
            "states_where_the_choice_decides": len(mixed) / len(best),
            "states_where_the_choice_decides_count": len(mixed),
            "solved_picks_a_winning_pair": solved,
            "chance_picks_a_winning_pair": chance}


def paired(a: np.ndarray, b: np.ndarray) -> Dict[str, float]:
    """Paired-by-board win difference: the same board under both arms, nothing averaged twice."""
    d = a - b
    se = d.std(ddof=1) / np.sqrt(d.size)
    return {"delta": float(d.mean()), "ci95": [float(-1.96 * se), float(1.96 * se)]}


def verify_artifact(path: pathlib.Path, pre_write: Dict[str, Any],
                    cache: Dict[str, np.ndarray], evals: np.ndarray, M: np.ndarray,
                    W: np.ndarray, hidden: int, pair_authored: bool,
                    move_authored: bool,
                    expected_changed: List[str]) -> Dict[str, Any]:
    """Read the written pickle back and prove it computes the matrices that were solved.

    A written artifact that is not re-loaded is a claim about a file, not about a network. The check
    is arm-aware, though: scoring a move-only artifact's pair head against `M` reports a huge error
    for an artifact that never claimed to install `M`. What must hold is that every matrix this arm
    did author is present on disk, and that nothing outside that arm's own heads moved.
    """
    art = pickle.loads(pathlib.Path(path).read_bytes())
    p = art["params"]

    def flat(tree):
        if isinstance(tree, dict):
            return np.concatenate([flat(v) for v in tree.values()])
        if isinstance(tree, (list, tuple)):
            return np.concatenate([flat(v) for v in tree])
        return np.asarray(tree, np.float64).ravel()

    s = cache["spell_token"][evals][:, :hidden]
    a = cache["target_token"][evals][:, :hidden]
    x = cache["actor_token"][evals][:, :hidden]
    q = s @ np.asarray(p["t_q"][0]) + np.asarray(p["t_q"][1])
    k = a @ np.asarray(p["t_k"][0]) + np.asarray(p["t_k"][1])
    move_got = x @ np.asarray(p["move"][0]) + np.asarray(p["move"][1])
    changed = sorted(key for key in p
                     if np.asarray(flat(p[key])).tobytes()
                     != np.asarray(flat(pre_write[key])).tobytes())
    out = {"changed_leaf_groups": changed,
           "expected_changed_leaf_groups": sorted(expected_changed),
           "only_authored_heads_changed": sorted(changed) == sorted(expected_changed),
           "artifact_sha256": hashlib.sha256(
               pathlib.Path(path).read_bytes()).hexdigest()[:16]}
    if move_authored:
        out["move_max_abs_error"] = float(np.abs(move_got - x @ np.asarray(W)).max())
    if pair_authored:
        got = np.einsum("ij,ij->i", q, k) / np.sqrt(hidden)
        want = np.einsum("ij,jk,ik->i", s, M, a)
        out["joint_max_abs_error"] = float(np.abs(got - want).max())
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boards", type=int, default=128)
    ap.add_argument("--holdout", type=int, default=512)
    ap.add_argument("--hidden", type=int, default=16)
    ap.add_argument("--tokens", choices=("random", "identity"), default="random",
                    help="random frozen trunk (the original arm) or identity features, which "
                         "leaves the solved matrix as the only thing the head can be doing")
    ap.add_argument("--lam", type=float, default=1.0)
    ap.add_argument("--min-mixed", type=int, default=60,
                    help="states where the pair choice changes the terminal outcome needed before "
                         "a ranking difference is called real; below it the write is refused")
    ap.add_argument("--moves", choices=("hold", "all"), default="hold",
                    help="hold still while enumerating (the pair axis) or enumerate the walk too "
                         "-- the axis that changes which spells are legal rather than their order")
    ap.add_argument("--labels", default=None,
                    help="npz of previously labelled candidates; refused unless its recorded "
                         "seed, width, token mode, move set, board count, continuation and "
                         "corpus digests all match this run")
    ap.add_argument("--labels-out", default=None,
                    help="write the labelled candidates here so the gate can be re-run without "
                         "re-rolling every counterfactual fight")
    ap.add_argument("--continuation", choices=("heuristic", "checkpoint"), default="heuristic",
                    help="who plays the rest of the fight after the candidate: the scripted "
                         "opponent, or the trained checkpoint -- a weak continuation can make two "
                         "candidates finish alike for the wrong reason and understate the ceiling")
    ap.add_argument("--min-ratio", type=float, default=4.0,
                    help="labelled pairs required per identifiable unknown before solving")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--dev", default=None, help="labelling corpus (default: the boss dev set)")
    ap.add_argument("--hold", default=None,
                    help="held-out corpus; give the mechanic rebuilds of both to price a write "
                         "on boards where a cast really pushes, drains or kills")
    ap.add_argument("--score-only", action="store_true")
    args = ap.parse_args(argv)
    global DEV, HOLD
    if args.dev:
        DEV = pathlib.Path(args.dev)
    if args.hold:
        HOLD = pathlib.Path(args.hold)

    cfg = Config(allies=1, enemies=4, spells=4, max_rounds=24)
    length = 2 * cfg.max_rounds
    heads = pick_heads(args.hidden)
    spec, tables = load(DEV, args.boards)
    obs0, glob0, _ = observe(initial_state(spec, tables, cfg), tables, cfg)
    columns = tables["spell_ap"].shape[2]
    feats = spell_features(tables)
    base_dim = obs0.shape[-1] - columns * feats
    if args.tokens == "identity":
        params = feature_token_params(base_dim, feats, glob0.shape[-1], args.hidden, cfg,
                                      args.seed)
    else:
        params = mech_net.init_params(jax.random.PRNGKey(args.seed), base_dim, feats,
                                      glob0.shape[-1], cfg, hidden=args.hidden, heads=heads)
    payload = pickle.loads(CHECKPOINT.read_bytes())
    inc_params = jax.tree_util.tree_map(jnp.asarray, payload["params"])
    # The shipped checkpoint was trained on the 9-number spell token, so it physically cannot read
    # a board that describes a pushback. Measuring against it there would be measuring against
    # nothing, so the live comparator becomes the scripted walk and the report says which one it
    # used -- the gate is refused, not quietly re-anchored.
    inc_width = int(payload.get("feat_dim") or inc_params["enc1"][0].shape[0])
    comparator = "incumbent" if inc_width == obs0.shape[-1] else "scripted"
    if args.continuation == "checkpoint" and comparator != "incumbent":
        raise SystemExit("the checkpoint reads a %d-wide observation and this corpus emits %d, so "
                         "it cannot play the continuation; drop --continuation checkpoint"
                         % (inc_width, obs0.shape[-1]))
    if args.continuation == "checkpoint":
        reference = incumbent_player(inc_params)
        policy = lambda s, t: reference(s, t, cfg)      # noqa: E731 -- collect_labels is 2-arg
        continuation = make_player_rollout(cfg, length, reference)
    else:
        policy = None
        continuation = make_baseline_rollout(cfg, length)
    started = time.time()
    moves = (0, 1, 2) if args.moves == "all" else (1,)
    meta = json.dumps({"seed": args.seed, "hidden": args.hidden, "tokens": args.tokens,
                       "moves": list(moves), "boards": args.boards, "heads": heads,
                       "continuation": args.continuation, "dev": digest(DEV),
                       "ruler": ruler_digest()}, sort_keys=True)
    if args.labels:
        with np.load(args.labels, allow_pickle=False) as z:
            recorded = str(z["meta"][()])
            if recorded != meta:
                raise SystemExit("the cached labels came from a different run, so the solve "
                                 "would not be the one this command claims:\n  want %s\n  got  %s"
                                 % (meta, recorded))
            cache = {k: z[k] for k in z.files if k != "meta"}
    else:
        cache = collect_labels(params, spec, tables, cfg, continuation, args.hidden, heads,
                               moves, policy)
        if args.labels_out:
            np.savez_compressed(args.labels_out, meta=np.asarray(meta), **cache)
    lever = {"pair_hold": decision_leverage(cache, cache["cand_move"] == 1, "pair"),
             "move_at_heuristic_pair": decision_leverage(
                 cache, (cache["cand_spell"] == cache["base_spell"])
                        & (cache["cand_target"] == cache["base_target"]), "move")}
    seconds = time.time() - started
    # Only the block the tokens can reach is identifiable: with identity features the spell token
    # is nonzero in `feats` positions and the actor token in `base_dim`, so counting hidden^2 would
    # refuse a solve that is actually well determined -- and accept nothing at all is the wrong
    # error.
    identifiable = min(args.hidden, feats) * min(args.hidden, base_dim)
    if cache["label"].size < args.min_ratio * identifiable:
        raise SystemExit("only %d labelled pairs for %d identifiable unknowns (%d parameters, "
                         "%d of them reachable) -- the solve would be a fit to noise, so the "
                         "tool refuses; raise --boards"
                         % (cache["label"].size, identifiable, args.hidden ** 2, identifiable))
    boards = np.unique(cache["board"])
    rng = np.random.default_rng(args.seed)
    eval_boards = set(rng.choice(boards, max(2, len(boards) // 4), replace=False).tolist())
    train = np.where(~np.isin(cache["board"], list(eval_boards)))[0]
    evals = np.where(np.isin(cache["board"], list(eval_boards)))[0]
    M = solve_relational(cache, train, args.hidden, args.lam)
    written = install(params, M, args.hidden)

    score = np.einsum("ij,jk,ik->i", cache["spell_token"][evals][:, :args.hidden], M,
                      cache["target_token"][evals][:, :args.hidden])
    ranking = group_metrics(cache["board"][evals], cache["step"][evals], score,
                            cache["label"][evals])

    hold_spec, hold_tables = load(HOLD, args.holdout)
    hold0 = initial_state(hold_spec, hold_tables, cfg)
    if args.tokens == "identity":
        draw = np.random.default_rng(args.seed + 5).normal(size=(args.hidden, args.hidden))
        control = mech_net.install_relational(
            params, draw * (np.linalg.norm(M) / np.linalg.norm(draw)), args.hidden)
    else:
        control = params
    W = solve_move(cache, train, args.hidden, args.lam)
    move_only = install_move(control, W, args.hidden)
    both = install_move(written, W, args.hidden)
    arms = {"written": make_player_rollout(cfg, length, mech_player(written, heads)),
            "written_hmove": make_player_rollout(cfg, length,
                                                 scripted_move_player(written, heads)),
            "unwritten": make_player_rollout(cfg, length, mech_player(control, heads)),
            # ... and the same cell with the scripted walk, which is what turns "the solved walk is
            # worth +0.20" into a claim separable from "any competent walk is worth +0.20": the two
            # walk arms and the two pair arms then form a 2x2, and each factor is read holding the
            # other fixed.
            "unwritten_hmove": make_player_rollout(cfg, length,
                                                   scripted_move_player(control, heads)),
            "move_written": make_player_rollout(cfg, length, mech_player(move_only, heads)),
            "both_written": make_player_rollout(cfg, length, mech_player(both, heads)),
            "random_pair": make_player_rollout(cfg, length, random_player(args.seed)),
            "scripted": continuation,
            "fixed_slot": make_player_rollout(cfg, length,
                                              fixed_slot_player(written, heads))}
    wins: Dict[str, np.ndarray] = {}
    for name, rollout in arms.items():
        wins[name] = np.stack([np.asarray(rollout(hold0, hold_tables)["winner"]) == 0
                               for _ in args.seeds]).mean(axis=0)
    if comparator == "incumbent":
        incumbent = jax.jit(make_eval_rollout(cfg, length, opponent="heuristic",
                                              player="greedy"))
        wins["incumbent"] = np.stack([np.asarray(incumbent(
            inc_params, hold0, hold_tables, jax.random.PRNGKey(int(s)))["winner"]) == 0
            for s in args.seeds]).mean(axis=0)

    report = {
        "dev_corpus_digest": digest(DEV), "holdout_corpus_digest": digest(HOLD),
        "spell_features": feats, "observation_width": int(obs0.shape[-1]),
        "live_comparator": comparator,
        "checkpoint_observation_width": inc_width,
        "labelled_pairs": int(cache["label"].size),
        "label_states": int(len(set(zip(cache["board"].tolist(),
                                        cache["step"].tolist())))),
        "labelling_seconds": round(seconds, 1),
        "hidden": args.hidden, "unknowns": args.hidden ** 2, "tokens": args.tokens,
        "continuation": args.continuation,
        "moves_enumerated": list(moves),
        "decision_leverage": {k: v for arm in lever.values() for k, v in arm.items()},
        "relational_matrix_frobenius": float(np.linalg.norm(M)),
        "pair_ranking_on_held_out_states": ranking,
        "terminal_win_rate_holdout": {k: float(np.mean(v)) for k, v in wins.items()},
        "paired_vs_unwritten": paired(wins["written"], wins["unwritten"]),
        "paired_vs_random_pair": paired(wins["written"], wins["random_pair"]),
        "paired_vs_live": paired(wins["written"], wins[comparator]),
    }
    report["paired_move_vs_random_pair"] = paired(wins["move_written"], wins["random_pair"])
    report["paired_both_vs_random_pair"] = paired(wins["both_written"], wins["random_pair"])
    report["paired_hmove_vs_random_pair"] = paired(wins["written_hmove"], wins["random_pair"])
    report["paired_hmove_vs_scripted"] = paired(wins["written_hmove"], wins["scripted"])
    # The comparison that decides whether anything is worth shipping: the written arm against the
    # trained checkpoint and against the scripted policy, paired per board, not two rates read off
    # the same table side by side.
    report["paired_move_vs_live"] = paired(wins["move_written"], wins[comparator])
    report["paired_move_vs_scripted"] = paired(wins["move_written"], wins["scripted"])
    report["paired_both_vs_live"] = paired(wins["both_written"], wins[comparator])
    # The two interactions, each holding one factor fixed: the solved walk against the scripted
    # walk with the pair head left random, and the solved pair head against the random one with the
    # walk held at the scripted rule.
    report["paired_move_vs_hmove_unwritten"] = paired(wins["move_written"],
                                                      wins["unwritten_hmove"])
    report["paired_written_hmove_vs_unwritten_hmove"] = paired(
        wins["written_hmove"], wins["unwritten_hmove"])
    report["paired_both_vs_written_hmove"] = paired(wins["both_written"],
                                                    wins["written_hmove"])
    by_arm = {"written": report["paired_vs_random_pair"],
              "written_hmove": report["paired_hmove_vs_random_pair"],
              "move_written": report["paired_move_vs_random_pair"],
              "both_written": report["paired_both_vs_random_pair"]}
    best_arm = max(by_arm, key=lambda k: by_arm[k]["delta"])
    report["best_arm"] = best_arm
    against = {"written": report["paired_vs_live"],
               "written_hmove": report["paired_hmove_vs_scripted"],
               "move_written": report["paired_move_vs_live"],
               "both_written": report["paired_both_vs_live"]}[best_arm]
    improves = by_arm[best_arm]["delta"] + by_arm[best_arm]["ci95"][0] > 0.0
    beats_incumbent = against["delta"] + against["ci95"][0] > 0.0
    report["best_arm_vs_live_comparator"] = against
    # The same quality test for the axis the winning arm actually writes: rank the three moves at
    # the reference pair and ask whether the solved ordering picks a winning one more often than
    # chance would, over held-out states where the moves disagree about the outcome.
    move_rows = np.where((cache["cand_spell"] == cache["base_spell"])
                         & (cache["cand_target"] == cache["base_target"]))[0]
    eval_m = np.intersect1d(move_rows, evals)
    move_score = np.einsum(
        "ij,ij->i", cache["actor_token"][eval_m][:, :args.hidden],
        np.asarray(W).T[cache["cand_move"][eval_m].astype(int)])
    ranking_move = group_metrics(cache["board"][eval_m], cache["step"][eval_m],
                                 move_score, cache["label"][eval_m])
    report["move_ranking_on_held_out_states"] = ranking_move
    metric = ranking_move if best_arm == "move_written" else ranking
    axis = "move" if best_arm == "move_written" else "pair"
    axis_mixed = metric["states_where_the_choice_decides_count"]
    enough_states = axis_mixed >= args.min_mixed
    has_ranking = metric["solved_picks_a_winning_pair"] is not None
    beats_chance = (has_ranking
                    and metric["solved_picks_a_winning_pair"]
                    > metric["chance_picks_a_winning_pair"])
    report["gate_axis"] = axis
    report["gate_axis_mixed_states"] = axis_mixed
    report["gate_write_accepted"] = bool(improves and beats_incumbent and enough_states
                                         and beats_chance)
    report["gate_reasons"] = [
        "%s beats uniform-legal choice on held-out wins (%+.4f, ci %s)"
        % (best_arm, by_arm[best_arm]["delta"], by_arm[best_arm]["ci95"]) if improves else
        "%s does not beat uniform-legal choice on held-out wins (%+.4f, ci %s)"
        % (best_arm, by_arm[best_arm]["delta"], by_arm[best_arm]["ci95"]),
        "%s beats the live comparator (%+.4f, ci %s)"
        % (best_arm, against["delta"], against["ci95"]) if beats_incumbent else
        "%s does not beat the live comparator (%+.4f, ci %s)"
        % (best_arm, against["delta"], against["ci95"]),
        "%d %s-decided states held out, below the %d needed before a ranking difference is not "
        "noise" % (axis_mixed, axis, args.min_mixed) if not enough_states else
        "%d %s-decided states scored" % (axis_mixed, axis),
        "%s ranking: %s" % (axis, "no decided state to score" if not has_ranking else
                            "solved %.3f against chance %.3f"
                            % (metric["solved_picks_a_winning_pair"],
                               metric["chance_picks_a_winning_pair"])),
    ]
    print(json.dumps(report, indent=1, sort_keys=True))
    if not report["gate_write_accepted"]:
        return 1
    if args.score_only:
        return 0
    arm_plan = {"written": (params, written, ["t_q", "t_k", "t_bias", "spell_prior"]),
                "written_hmove": (params, written, ["t_q", "t_k", "t_bias", "spell_prior"]),
                "move_written": (control, move_only, ["move"]),
                "both_written": (written, both, ["move"])}[best_arm]
    base, accepted_params, expected_changed = arm_plan
    artifact = {"params": accepted_params, "M": M, "W": W, "arm": best_arm,
                "hidden": args.hidden, "lam": args.lam,
                "seed": args.seed, "cfg": vars(cfg), "report": report,
                "source_checkpoint_digest": digest(CHECKPOINT),
                "dev_corpus_digest": digest(DEV), "holdout_corpus_digest": digest(HOLD)}
    final = pathlib.Path(args.out)
    staging = final.with_suffix(final.suffix + ".unverified")
    staging.write_bytes(pickle.dumps(artifact))
    check = verify_artifact(staging, base, cache, evals, M, W, args.hidden,
                            pair_authored=best_arm == "both_written" or "t_q" in expected_changed,
                            move_authored="move" in expected_changed,
                            expected_changed=[k for k in expected_changed if k != "move"]
                            + (["move"] if "move" in expected_changed else []))
    report["artifact_verification"] = check
    ok = check["only_authored_heads_changed"]
    for key in ("joint_max_abs_error", "move_max_abs_error"):
        ok = ok and check.get(key, 0.0) < 1e-3
    print(json.dumps({"verified": ok, **check}, indent=1, sort_keys=True))
    if not ok:
        staging.unlink()
        print("the written artifact did not reproduce the solved matrices, or it changed heads "
              "this tool never authored, so nothing was published", file=sys.stderr)
        return 1
    # Published by rename only after the file on disk has been read back and shown to compute the
    # solved matrices: a crash between write and verify must not leave an artifact at the real path.
    staging.replace(final)
    # The report embedded in the file is written after the check, so the artifact carries the proof
    # that it was read back and found to compute the solved matrices -- a reader should not have to
    # trust the console of whoever produced it.
    artifact["report"] = report
    final.write_bytes(pickle.dumps(artifact))
    print("wrote %s (%d bytes), arm=%s" % (final, final.stat().st_size, best_arm))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
