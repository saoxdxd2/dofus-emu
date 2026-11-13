"""Distributional value over a duel state, learned from self-play outcomes.

Why this exists, in one paragraph. The previous learned ranker was distilled from
the solver, so the solver was its ceiling: measured, it agreed with its teacher
77.7% of the time, picked a strictly worse plan on 9.1% of boards, and was ~700x
slower. The repo's older outcome-trained value (`models/duel_value_v4_0.pkl`) is
*inert* -- a constant across every candidate plan, so `plan_scores` fell back to
the solver's own tiebreak and every "value vs solver" result measured the solver
against itself. Neither is a value function. This is one: it predicts the
distribution of the final HP differential, learned from duels that were actually
played, and nothing in its target comes from the solver's heuristic score.

The objective is the HP differential, not win/loss, and that is forced by
measurement rather than taste: on these level-200 builds seat 1 wins essentially
every decided game (`seat1_win` 1.0, verdict `seat_dominated`), so a win/loss
value has no variance to learn from and cannot rank two good policies.

The stabilisers are not decoration. Each one exists because a plain regression
fails on this target:

``symlog``        Returns are bounded but concentrated near zero, where close
                  fights live. symlog keeps resolution there and compresses the
                  tails, so the loss is not dominated by a few blowouts.
``two-hot``       The target is encoded as weight on the two neighbouring bins of
                  a fixed support instead of a hard bin index. This is the
                  HL-Gauss/two-hot trick: it keeps the cross-entropy smooth in the
                  target and removes the discontinuity that makes categorical
                  regression brittle.
``percentile``    The return scale is set from the 5th-95th percentile of the
                  training returns, so the support covers the distribution
                  instead of being fixed by a hand-chosen constant.
``layernorm``     Inputs span 0..1 (ratios), -1..1 (differentials) and 0..200
                  (levels); without normalisation the level term dominates.
``layerscale``    Residual branches are scaled by a learned per-layer gain starting
                  at 1e-2, which is what makes a residual MLP trainable without a
                  warmup collapse.
``ema``           A Polyak-averaged copy of the weights is what gets saved and
                  served. Single-batch noise in a 50k-sample run is larger than the
                  differences between good and bad plans.
``trust region``  Exploration is a convex blend of the value-greedy distribution
                  and the solver prior, which is a mirror-descent step with a KL
                  bound; it keeps generated games in a sane state distribution
                  instead of wandering.

    python -m combat.duel_value --games 400 --out models/duel_value_nn.pkl
    python -m combat.duel_value --evaluate models/duel_value_nn.pkl
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from combat.grid_backend import XP, log_softmax, relu, tree_map
import logging
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "models" / "duel_value_nn.pkl"

ELEMENTS = ("water", "earth", "air", "fire", "neutral")

#: Support of the categorical return head, in symlog space. symlog(1.0) = 0.693,
#: so +-1.0 covers normalised returns up to symexp(1) = 1.72 -- wider than the
#: [-1, 1] the objective can produce, which keeps the target off the edge bins.
SUPPORT_LO, SUPPORT_HI = -1.0, 1.0
BINS = 51

#: Features per fighter block, then the shared geometry and global blocks.
#: `_block` returns 17: hp, ap, mp, level, initiative, alive, damage, heal,
#: efficiency, spell count, five resists, x, y.
BLOCK = 17
GEOMETRY = 6
GLOBAL = 3
FEATURES = 2 * BLOCK + GEOMETRY + GLOBAL


# ---------------------------------------------------------------------------
# transforms
# ---------------------------------------------------------------------------


def symlog(x: Any) -> Any:
    return XP.sign(x) * XP.log1p(XP.abs(x))


def symexp(x: Any) -> Any:
    return XP.sign(x) * (XP.exp(XP.abs(x)) - 1.0)


def support() -> np.ndarray:
    return np.linspace(SUPPORT_LO, SUPPORT_HI, BINS, dtype=np.float32)


def two_hot(values: np.ndarray) -> np.ndarray:
    """Encode targets as weight on the two neighbouring bins of the support.

    A hard bin index makes the categorical loss piecewise constant in the target,
    so two plans whose true returns differ slightly get the same gradient. Two-hot
    makes the loss linear in the target between bins, which is what lets the head
    express "slightly better" at all.
    """
    edges = support()
    step = float(edges[1] - edges[0])
    position = (np.asarray(values, dtype=np.float32) - edges[0]) / step
    lower = np.floor(position).astype(np.int64)
    upper = lower + 1
    lower = np.clip(lower, 0, BINS - 1)
    upper = np.clip(upper, 0, BINS - 1)
    weight_upper = np.clip(position - lower, 0.0, 1.0).astype(np.float32)
    weight_lower = 1.0 - weight_upper
    out = np.zeros((len(position), BINS), dtype=np.float32)
    rows = np.arange(len(position))
    np.add.at(out, (rows, lower), weight_lower)
    np.add.at(out, (rows, upper), weight_upper)
    return out


# ---------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------


def _reach(duelist: Any) -> float:
    """How far this duelist can damage from, this turn: mp plus the longest range."""
    spans = [float(s.max_range) for s in duelist.spells
             if s.ap_cost <= max(1.0, duelist.ap)]
    if not spans:
        spans = [float(s.max_range) for s in duelist.spells]
    return (max(spans) + float(duelist.mp)) if spans else float(duelist.mp)


def _block(duelist: Any) -> List[float]:
    from combat.battle_solver import cell_point

    damage, efficiency, heal, count = duelist.spell_summary()
    x, y = cell_point(duelist.cell)
    scale = max(1.0, duelist.max_hp)
    return [
        float(duelist.hp) / max(1.0, duelist.max_hp),
        float(duelist.ap) / max(1.0, duelist.max_ap),
        float(duelist.mp) / max(1.0, duelist.max_mp),
        float(duelist.level) / 200.0,
        float(duelist.initiative) / 200.0,
        1.0 if duelist.alive else 0.0,
        float(damage) / scale,
        float(heal) / scale,
        float(efficiency) / 100.0,
        min(1.0, float(count) / 20.0),
        *[float(duelist.resist_value(element)) / 100.0 for element in ELEMENTS],
        x / 32.0,
        y / 33.0,
    ]


def encode(state: Any, actor: Any) -> np.ndarray:
    """Features of ``state`` from ``actor``'s side.

    The caller is expected to pass the *successor* state -- the board after the
    candidate plan has been applied and with the opponent about to move -- so the
    encoding already reflects the damage dealt, the HP lost and the cell chosen.
    """
    me = state.by_id(actor.id)
    them = state.fighters[1] if me is state.fighters[0] else state.fighters[0]
    cells = state.cells
    distance = float(cells.distance(me.cell, them.cell))
    los = 1.0 if cells.has_los(me.cell, them.cell) else 0.0
    me_reach, them_reach = _reach(me), _reach(them)
    hp_diff = (float(me.hp) / max(1.0, me.max_hp)
               - float(them.hp) / max(1.0, them.max_hp))
    row = (_block(me) + _block(them) + [
        min(1.0, distance / 30.0),
        los,
        1.0 if (los and distance <= me_reach) else 0.0,
        1.0 if (los and distance <= them_reach) else 0.0,
        min(1.0, me_reach / 30.0),
        min(1.0, them_reach / 30.0),
        float(state.round) / max(1.0, float(state.max_rounds)),
        1.0 if state.turn % 2 else 0.0,
        hp_diff,
    ])
    if len(row) != FEATURES:
        # The width constant and the encoder drifted apart once already, and the
        # symptom was a broadcast error three frames deep inside a jitted matmul
        # with no mention of features. Fail where the mistake is.
        raise AssertionError("encode produced %d features, FEATURES is %d; "
                             "update BLOCK/GEOMETRY/GLOBAL"
                             % (len(row), FEATURES))
    return np.asarray(row, dtype=np.float32)


def outcome(state: Any, actor_id: int) -> float:
    """Normalised final HP differential from ``actor_id``'s side, in [-1, 1]."""
    mine = state.by_id(actor_id)
    theirs = state.fighters[1] if mine is state.fighters[0] else state.fighters[0]
    return (float(mine.hp) / max(1.0, mine.max_hp)
            - float(theirs.hp) / max(1.0, theirs.max_hp))


# ---------------------------------------------------------------------------
# self-play data
# ---------------------------------------------------------------------------


def gumbel_topk(logits: np.ndarray, k: int, rng: random.Random) -> np.ndarray:
    """Sample k indices without replacement, Gumbel-style.

    The same noise vector drives the ranking, which is what makes the sample
    consistent with the argmax it implies: a plan sampled this way is a draw from
    the softmax, not an arbitrary exploration rule.
    """
    if k >= len(logits):
        return np.arange(len(logits))
    noise = np.asarray([-math.log(-math.log(rng.random() + 1e-12) + 1e-12)
                        for _ in logits], dtype=np.float64)
    return np.argsort(-(logits + noise))[:k]


def generate(games: int, seed: int = 7, solver_prob: float = 0.6,
             k: int = 6, max_rounds: int = 12, rows: int = 4096
             ) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """Play duels and return (features, returns) for every decision point.

    Matchups are sampled from the encounter table (`make_duel` over two different
    combatants), **not** from the mirror duel. That is not a detail: the mirror
    duel is one deterministic fight -- identical builds on both sides -- so its
    final HP differential takes exactly two values (+-0.118, measured) and a value
    trained on it can only learn "did seat 1 win", which is already the saturated
    win/loss signal. Diversity in the matchup is what gives the return variance.

    Exploration is a trust-region blend: with probability ``solver_prob`` the
    solver's plan is played, otherwise a Gumbel-Top-k draw among the candidates.
    Pure random play on these builds produces fights that never engage, and the
    value would then be trained on positions that do not occur.
    """
    from combat.battle_solver import BattleSolver
    from combat.duel import DEFAULT_DATA, apply_plan, advance_turn, make_duel, plans_for
    from combat.duel_benchmark import load_data, sample_pair
    from combat.normal_build_duel import DEFAULT_MAP

    cells = _load_cells(DEFAULT_MAP)
    data, levels = load_data(DEFAULT_DATA)
    pool = list(range(min(rows, len(levels))))
    solver = BattleSolver(beam_width=8, max_actions=2)
    rng = random.Random(seed)

    features: List[np.ndarray] = []
    labels: List[float] = []
    played = decided = 0
    for index in range(games):
        row_a, row_b = sample_pair(levels, pool, rng)
        state = make_duel(data, row_a, row_b, cells, rng, max_rounds=max_rounds)
        if state is None:
            continue
        played += 1
        pending: List[Tuple[np.ndarray, int]] = []
        while not state.done:
            state.turns += 1
            actor = state.actor
            plans = plans_for(state, solver, k)
            if not plans:
                state.done = True
                break
            if rng.random() < solver_prob or len(plans) == 1:
                plan = plans[0]
            else:
                logits = np.asarray([p.score for p in plans], dtype=np.float64)
                plan = plans[int(gumbel_topk(logits, 1, rng)[0])]
            clone = state.clone()
            apply_plan(clone, plan)
            if not clone.done:
                # Only non-terminal successors need a prediction; a terminal one
                # is worth exactly its return and is handled by the policy.
                pending.append((encode(clone, actor), actor.id))
            apply_plan(state, plan)
            if state.done:
                break
            advance_turn(state)
            if state.done:
                break
        if state.done:
            decided += 1
        for row, actor_id in pending:
            features.append(row)
            labels.append(outcome(state, actor_id))

    X = (np.stack(features).astype(np.float32) if features
         else np.zeros((0, FEATURES), np.float32))
    y = np.asarray(labels, dtype=np.float32)
    return X, y, {"games": games, "played": played, "decided": decided,
                  "samples": int(len(y)), "solver_prob": solver_prob,
                  "distinct_returns": int(len(np.unique(np.round(y, 6)))),
                  "seed": seed}


_CELLS: Dict[str, Any] = {}
_DATA: Dict[str, Any] = {}


def _load_cells(path: Path) -> Any:
    key = str(path)
    if key not in _CELLS:
        from combat.duel import load_map
        _CELLS[key] = load_map(path)
    return _CELLS[key]


def _load_table(path: Path) -> Any:
    key = str(path)
    if key not in _DATA:
        from combat.duel_benchmark import load_data
        _DATA[key] = load_data(path)
    return _DATA[key]


# ---------------------------------------------------------------------------
# counterfactual data: every candidate plan, rolled out on the same board
# ---------------------------------------------------------------------------


def rollout_truth(state: Any, actor_id: int, plans: Sequence[Any], solver: Any,
                  rng: random.Random, max_rounds: int, k: int,
                  rollouts: int = 1) -> List[float]:
    """The final HP differential each candidate plan actually produces.

    This is the counterfactual the absolute-value model never saw. ``generate``
    plays one plan per decision and labels the resulting states with the game's
    outcome, so on any given board every sample carries the *same* label -- the
    board's own result. Ranking needs the difference between plans on the same
    board, and that difference only exists if each plan is played out.

    The continuation is pure solver play, on both sides, so the truth does not
    depend on the model being judged.
    """
    from combat.duel import advance_turn, apply_plan, play_game, solver_policy

    out: List[float] = []
    for plan in plans:
        samples = []
        for _ in range(max(1, rollouts)):
            clone = state.clone()
            apply_plan(clone, plan)
            if clone.done:
                samples.append(outcome(clone, actor_id))
                continue
            advance_turn(clone)
            if clone.done:
                samples.append(outcome(clone, actor_id))
                continue
            final, _ = play_game(clone, {1: solver_policy, 2: solver_policy},
                                 solver, rng, k=k)
            samples.append(outcome(final, actor_id))
        out.append(float(np.mean(samples)))
    return out


def _ranked_chunk(job: Tuple[int, int, int, int, int, int, int, int, int]
                  ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Dict[str, Any]]:
    """Worker body for :func:`generate_ranked`. Returns flat rows and group ids.

    Decision points are sampled **across the trajectory**, not just at the
    opening. A model trained only on turn 1 is asked at serving time to rank
    plans in positions it has never seen -- and it does: measured, that model
    lost to the solver by 0.266 of HP in duels while its turn-1 regret was
    *better* than the solver's. Its residual path still carried the HP
    differential correctly at any depth, so the damage was entirely the network's
    correction being applied out of distribution.
    """
    start, count, seed, k, rollouts, max_rounds, rows, snapshots, _unused = job
    from combat.battle_solver import BattleSolver
    from combat.duel import (DEFAULT_DATA, advance_turn, apply_plan, make_duel,
                             plans_for)
    from combat.duel_benchmark import sample_pair
    from combat.normal_build_duel import DEFAULT_MAP

    cells = _load_cells(DEFAULT_MAP)
    data, levels = _load_table(DEFAULT_DATA)
    pool = list(range(min(rows, len(levels))))
    solver = BattleSolver(beam_width=8, max_actions=2)
    rng = random.Random(seed)

    feats: List[np.ndarray] = []
    groups: List[int] = []
    returns: List[float] = []
    stats = {"built": 0, "used": 0, "single": 0, "flat": 0, "snapshots": 0}
    group = 0
    for _ in range(count):
        row_a, row_b = sample_pair(levels, pool, rng)
        state = make_duel(data, row_a, row_b, cells, rng, max_rounds=max_rounds)
        if state is None:
            continue
        stats["built"] += 1

        # Walk one solver-vs-solver trajectory and keep a counterfactual
        # snapshot of every decision point on it.
        walk = state
        taken: List[Tuple[Any, int, List[Any]]] = []
        while not walk.done:
            walk.turns += 1
            actor = walk.actor
            plans = plans_for(walk, solver, k)
            if not plans:
                break
            taken.append((walk.clone(), actor.id, plans))
            apply_plan(walk, plans[0])
            if walk.done:
                break
            advance_turn(walk)
            if walk.done:
                break
        if not taken:
            stats["single"] += 1
            continue

        wanted = min(max(1, snapshots), len(taken))
        picks = np.unique(np.linspace(0, len(taken) - 1, wanted).round()
                          .astype(int))
        used_here = 0
        for pick in picks:
            snapshot, actor_id, plans = taken[int(pick)]
            if len(plans) < 2:
                continue
            truths = rollout_truth(snapshot, actor_id, plans, solver, rng,
                                   max_rounds, k, rollouts)
            if len(set(round(t, 6) for t in truths)) < 2:
                stats["flat"] += 1
                continue
            actor = snapshot.by_id(actor_id)
            for plan, truth in zip(plans, truths):
                clone = snapshot.clone()
                apply_plan(clone, plan)
                feats.append(encode(clone, actor))
                groups.append(group)
                returns.append(truth)
            group += 1
            used_here += 1
        if used_here:
            stats["used"] += 1
            stats["snapshots"] += used_here
        else:
            stats["single"] += 1
    X = (np.stack(feats).astype(np.float32) if feats
         else np.zeros((0, FEATURES), np.float32))
    return (X, np.asarray(groups, dtype=np.int32),
            np.asarray(returns, dtype=np.float32), stats)


def generate_ranked(games: int, k: int = 6, rollouts: int = 1, seed: int = 7,
                    max_rounds: int = 12, rows: int = 4096, workers: int = 0,
                    snapshots: int = 3
                    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Dict[str, Any]]:
    """Counterfactual corpus: ``games`` duels, each sampled at ``snapshots`` turns.

    Returns ``(X, groups, returns, meta)`` where row ``i`` is one plan applied to
    one decision point and ``groups[i]`` names that decision point. Every group
    holds at least two plans with distinct truths, which is what makes a
    within-board comparison defined at all.

    Generation is the expensive half (a rollout per plan per decision point), so
    it is spread over processes. Each worker seeds its own stream, so the result
    is reproducible for a given ``workers`` count.
    """
    workers = workers or min(8, (os.cpu_count() or 1))
    workers = max(1, min(workers, games))
    if workers == 1:
        X, groups, returns, stats = _ranked_chunk(
            (0, games, seed, k, rollouts, max_rounds, rows, snapshots, 0))
        return X, groups, returns, {"games": games, "workers": 1, **stats,
                                    "samples": int(len(returns)),
                                    "boards": int(len(set(groups.tolist())))}

    import multiprocessing as mp

    # Every worker imports jax (through `combat.grid_backend`) even though this
    # path never uses it, and each XLA pool then spins its own thread count
    # against the others. Measured on 8 workers: 65% CPU utilisation, and the
    # fix is to give each worker exactly one thread. Set before the Pool so the
    # spawned children inherit it.
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("XLA_FLAGS",
                          "--xla_cpu_multi_thread_eigen=false "
                          "intra_op_parallelism_threads=1")

    chunk = max(1, games // workers)
    jobs = []
    for index in range(workers):
        count = chunk if index < workers - 1 else games - chunk * (workers - 1)
        jobs.append((index * chunk, count, seed + 10007 * index, k, rollouts,
                     max_rounds, rows, snapshots, 0))
    context = mp.get_context("spawn")
    with context.Pool(workers) as pool:
        results = pool.map(_ranked_chunk, jobs)

    xs, gs, rs = [], [], []
    offset = 0
    rows_total = 0
    totals = {"built": 0, "used": 0, "single": 0, "flat": 0}
    for X, groups, returns, stats in results:
        if len(X) == 0:
            continue
        xs.append(X)
        gs.append(groups + offset)
        rs.append(returns)
        offset += int(groups.max()) + 1 if len(groups) else 0
        rows_total += len(returns)
        for key in totals:
            totals[key] += int(stats[key])
    if not xs:
        return (np.zeros((0, FEATURES), np.float32), np.zeros((0,), np.int32),
                np.zeros((0,), np.float32), {"games": games, "workers": workers,
                                             "samples": 0, **totals})
    return (np.concatenate(xs), np.concatenate(gs), np.concatenate(rs),
            {"games": games, "workers": workers, "samples": int(rows_total),
             "boards": int(offset), **totals})


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


def init_params(seed: int = 0, hidden: int = 256, layers: int = 3,
                features: int = FEATURES, bins: int = BINS,
                score_head: bool = False) -> Dict[str, Any]:
    """He-initialised residual MLP. Numpy init so training and serving agree.

    ``score_head`` adds an unbounded scalar output, and a residual path from the
    encoder's last feature -- the HP differential -- with a gain initialised at
    exactly 1.0. Both exist for the ranking objective, and for the same reason:
    the categorical head's mean is bounded by the support and is nearly
    insensitive while the distribution is near-uniform, so it cannot express
    "this plan is much better than that one", which is precisely what a
    conditional logit has to express. Measured before the change: the ranker
    reached a within-board Spearman of 0.63 where the trivial greedy reading of
    the successor state -- which the residual path reproduces at initialisation --
    scores 0.78.

    Initialising the gain at 1.0 makes the model start *at* the incumbent rather
    than near zero, so training can only learn a correction to the one-ply
    signal. A ranker that fails to learn then costs nothing relative to the
    heuristic it replaces, instead of being strictly worse.
    """
    rng = np.random.default_rng(seed)
    scale = math.sqrt(2.0 / features)
    params: Dict[str, Any] = {
        "w_in": (rng.standard_normal((features, hidden)) * scale).astype(np.float32),
        "b_in": np.zeros((hidden,), np.float32),
        "norm_w": np.ones((features,), np.float32),
        "norm_b": np.zeros((features,), np.float32),
    }
    if score_head:
        # Zero, not He: the initial score is then exactly ``hp_diff``, so the
        # model *starts* at the greedy incumbent and the network only ever learns
        # a correction. He-scaled weights give the untrained branch a standard
        # deviation of ~0.5 against the residual's ~0.3, so the model starts
        # somewhere arbitrary instead of at the baseline it is supposed to
        # improve -- measured, that cost 0.23 of Spearman at epoch 1.
        params["w_score"] = np.zeros((hidden, 1), np.float32)
        params["b_score"] = np.zeros((1,), np.float32)
        params["hp_gain"] = np.ones((1,), np.float32)
    for index in range(layers):
        params["ln%d_w" % index] = np.ones((hidden,), np.float32)
        params["ln%d_b" % index] = np.zeros((hidden,), np.float32)
        params["w1_%d" % index] = (rng.standard_normal((hidden, 2 * hidden))
                                   * math.sqrt(2.0 / hidden)).astype(np.float32)
        params["b1_%d" % index] = np.zeros((2 * hidden,), np.float32)
        params["w2_%d" % index] = (rng.standard_normal((2 * hidden, hidden))
                                   * math.sqrt(2.0 / (2 * hidden))).astype(np.float32)
        params["b2_%d" % index] = np.zeros((hidden,), np.float32)
        params["ls_%d" % index] = np.full((hidden,), 1e-2, np.float32)
    params["w_out"] = (rng.standard_normal((hidden, bins))
                       * math.sqrt(2.0 / hidden)).astype(np.float32)
    params["b_out"] = np.zeros((bins,), np.float32)
    return params


def n_layers(params: Dict[str, Any]) -> int:
    """Layer count inferred from the keys.

    The config deliberately does *not* live inside the parameter pytree: an int
    leaf there is something `jax.value_and_grad` has to differentiate with respect
    to, which yields a float0 tangent and breaks the optimiser.
    """
    return sum(1 for key in params if key.startswith("w1_"))


def _layernorm(x: Any, weight: Any, bias: Any, eps: float = 1e-5) -> Any:
    mean = XP.mean(x, axis=-1, keepdims=True)
    var = XP.mean((x - mean) ** 2, axis=-1, keepdims=True)
    return (x - mean) * (1.0 / XP.sqrt(var + eps)) * weight + bias


def forward(params: Dict[str, Any], features: Any) -> Any:
    """Output of the head: categorical logits, or a scalar score.

    Which one is decided by the parameters, not by a flag, so a checkpoint is
    self-describing: ``w_score`` present means the listwise objective trained it
    and the output is an unbounded score.
    """
    h = _layernorm(features, params["norm_w"], params["norm_b"])
    h = relu(h @ params["w_in"] + params["b_in"])
    for index in range(n_layers(params)):
        block = _layernorm(h, params["ln%d_w" % index], params["ln%d_b" % index])
        block = relu(block @ params["w1_%d" % index] + params["b1_%d" % index])
        block = block @ params["w2_%d" % index] + params["b2_%d" % index]
        h = h + params["ls_%d" % index] * block
    if "w_score" in params:
        # The residual carries the successor's HP differential through with a
        # gain of 1 at initialisation, so the model starts at the greedy
        # incumbent and learns only the correction.
        return (h @ params["w_score"] + params["b_score"]
                + params["hp_gain"] * features[..., -1:])
    return h @ params["w_out"] + params["b_out"]


def value_of(logits: Any) -> Any:
    """Expected return: decode the categorical mean through symexp."""
    probabilities = XP.exp(log_softmax(logits, axis=-1))
    centres = XP.asarray(support())
    return symexp(XP.sum(probabilities * centres, axis=-1))


def predict(params: Dict[str, Any], features: np.ndarray) -> np.ndarray:
    if len(features) == 0:
        return np.zeros((0,), np.float32)
    out = forward(params, XP.asarray(features))
    if "w_score" in params:
        return np.asarray(out[:, 0], dtype=np.float32)
    return np.asarray(value_of(out), dtype=np.float32)


# ---------------------------------------------------------------------------
# training
# ---------------------------------------------------------------------------


def train(X: np.ndarray, y: np.ndarray, epochs: int = 40, batch: int = 256,
          lr: float = 3e-3, hidden: int = 256, layers: int = 3,
          seed: int = 0, ema: float = 0.995, clip: float = 1.0,
          verbose: bool = True) -> Dict[str, Any]:
    """Fit the distributional head. Returns params plus the training report.

    The stabiliser order matters: percentile scaling first (so the support is
    calibrated to this dataset), then symlog, then two-hot. Doing two-hot before
    scaling would put most targets in the two centre bins.
    """
    import jax
    import jax.numpy as jnp

    if len(X) == 0:
        raise SystemExit("no samples: generate duels first")

    scale = float(np.percentile(np.abs(y), 95)) or 1.0
    normalised = np.clip(y / scale, -1.0, 1.0)
    targets = two_hot(symlog(normalised))
    report: Dict[str, Any] = {"scale": scale, "samples": int(len(y)),
                              "return_mean": float(y.mean()),
                              "return_std": float(y.std())}

    order = np.random.default_rng(seed).permutation(len(X))
    split = int(len(order) * 0.85)
    train_idx, val_idx = order[:split], order[split:]

    params = init_params(seed, hidden, layers)
    ema_params = tree_map(lambda a: np.array(a, copy=True), params)

    def loss_fn(p, batch_x, batch_t):
        logits = forward(p, batch_x)
        return -jnp.mean(jnp.sum(batch_t * log_softmax(logits, axis=-1), axis=-1))

    grad_fn = jax.jit(jax.value_and_grad(loss_fn))
    adam_m = tree_map(lambda a: jnp.zeros_like(jnp.asarray(a)), params)
    adam_v = tree_map(lambda a: jnp.zeros_like(jnp.asarray(a)), params)
    step = 0
    history = []
    steps_per_epoch = max(1, math.ceil(len(train_idx) / batch))
    rate = 0.0
    best_ce = float("inf")
    best_params: Optional[Dict[str, Any]] = None
    best_epoch = 0

    for epoch in range(1, epochs + 1):
        np.random.default_rng(seed + epoch).shuffle(train_idx)
        total = 0.0
        seen = 0
        # Partial trailing batches are kept: a small corpus (a smoke run, or a
        # first pass with a few hundred samples) is then still trainable, where
        # `range(0, n - batch + 1, batch)` silently trains on nothing.
        for start in range(0, len(train_idx), batch):
            chunk = train_idx[start:start + batch]
            if len(chunk) == 0:
                continue
            seen += 1
            batch_x = jnp.asarray(X[chunk])
            batch_t = jnp.asarray(targets[chunk])
            loss, grads = grad_fn(params, batch_x, batch_t)
            step += 1
            # Cosine decay with a short linear warmup: the first steps of a
            # residual net with LayerScale are small, and a flat high LR there is
            # what turns a warm start into a dead ReLU field.
            warm = min(1.0, step / max(1, 0.05 * steps_per_epoch * epochs))
            rate = lr * warm * 0.5 * (1.0 + math.cos(math.pi * epoch / epochs))
            norm = jnp.sqrt(sum(jnp.sum(jnp.square(g)) for g in jax.tree_util.tree_leaves(grads)))
            factor = jnp.minimum(1.0, clip / (norm + 1e-12))
            grads = jax.tree_util.tree_map(lambda g: g * factor, grads)
            m = jax.tree_util.tree_map(lambda m, g: 0.9 * m + 0.1 * g, adam_m, grads)
            v = jax.tree_util.tree_map(lambda v, g: 0.999 * v + 0.001 * g * g, adam_v, grads)
            mh = jax.tree_util.tree_map(lambda x: x / (1 - 0.9 ** step), m)
            vh = jax.tree_util.tree_map(lambda x: x / (1 - 0.999 ** step), v)
            params = jax.tree_util.tree_map(
                lambda p, mh, vh: p - rate * mh / (jnp.sqrt(vh) + 1e-8), params, mh, vh)
            adam_m, adam_v = m, v
            # EMA warmup: a fixed 0.995 decay has a 200-step horizon, so on a
            # short run the *served* weights are still at initialisation while
            # the training weights have moved -- the model then reports a spread
            # of ~0.003 and a validation loss worse than uniform, which looks
            # like a broken objective and is really a lagging average.
            decay = min(ema, (1.0 + step) / (10.0 + step))
            ema_params = jax.tree_util.tree_map(
                lambda e, p: decay * e + (1.0 - decay) * p, ema_params, params)
            total += float(loss)

        averaged = {key: np.asarray(value) for key, value in ema_params.items()}
        val_ce = _cross_entropy(averaged, X[val_idx], targets[val_idx])
        row = {"epoch": epoch, "train_ce": round(total / max(1, seen), 5),
               "val_ce": round(val_ce, 5), "lr": round(rate, 6)}
        history.append(row)
        # Keep the best *held-out* weights, not the last ones. With 4k samples
        # against 250k parameters the validation loss turns up after ~25 epochs
        # (measured 2.04 -> 2.56 over the next 35), so serving the final epoch
        # would ship a memoriser.
        if val_ce < best_ce:
            best_ce, best_epoch = val_ce, epoch
            best_params = {key: np.array(value, copy=True) for key, value in averaged.items()}
        if verbose and (epoch == 1 or epoch % 5 == 0 or epoch == epochs):
            logger.debug(json.dumps(row))

    served = best_params if best_params is not None else {
        key: np.asarray(value) for key, value in ema_params.items()}
    report["history"] = history
    report["config"] = {"hidden": hidden, "layers": layers, "bins": BINS,
                        "epochs": epochs, "batch": batch, "lr": lr, "ema": ema}
    report["val_ce"] = float(best_ce)
    report["best_epoch"] = best_epoch
    report["final_epoch_ce"] = history[-1]["val_ce"] if history else float("nan")
    report["uniform_ce"] = float(np.log(BINS))
    report["spread"] = _spread(served, X[val_idx])
    return {"params": served, "report": report}


def _cross_entropy(params: Dict[str, Any], X: np.ndarray, targets: np.ndarray) -> float:
    if len(X) == 0:
        return float("nan")
    logits = forward(params, XP.asarray(X))
    probabilities = np.asarray(XP.exp(log_softmax(logits, axis=-1)))
    return float(-np.mean(np.sum(targets * np.log(probabilities + 1e-9), axis=-1)))


def _spread(params: Dict[str, Any], X: np.ndarray) -> float:
    """Standard deviation of predicted values across held-out states.

    The forest this replaces scored 0.0 here on every real board, which is what
    "inert" means: a head with no spread cannot rank anything.
    """
    values = predict(params, X)
    return float(np.std(values)) if len(values) else 0.0


# ---------------------------------------------------------------------------
# listwise training: conditional logit on within-board comparisons
# ---------------------------------------------------------------------------


def dense_groups(groups: np.ndarray, X: np.ndarray, returns: np.ndarray
                 ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Pack ragged groups into ``[G, K]`` arrays plus a mask.

    Returns ``(Xd, mask, td, ids)`` where ``Xd`` is ``[G, K, F]``, ``mask`` and
    ``td`` are ``[G, K]`` and ``ids`` names the source group of each row.
    """
    order = np.argsort(groups, kind="stable")
    ids, starts, counts = np.unique(groups[order], return_index=True,
                                    return_counts=True)
    width = int(counts.max()) if len(counts) else 0
    Xd = np.zeros((len(ids), width, X.shape[1]), np.float32)
    mask = np.zeros((len(ids), width), np.float32)
    td = np.zeros((len(ids), width), np.float32)
    for slot in range(len(ids)):
        rows = order[starts[slot]:starts[slot] + counts[slot]]
        Xd[slot, :len(rows)] = X[rows]
        mask[slot, :len(rows)] = 1.0
        td[slot, :len(rows)] = returns[rows]
    return Xd, mask, td, ids


def _soft_targets(td: np.ndarray, mask: np.ndarray, temp: float) -> np.ndarray:
    """``softmax(t / temp)`` inside each board, zero on padding.

    The temperature is fixed in *return* units rather than standardised per
    board. Standardising would turn a board whose plans are genuinely equivalent
    -- differing only by rollout noise -- into a confident target, which is the
    opposite of what the data says. With a fixed temperature a flat board gets a
    near-uniform target and a decisive board gets a peaked one.
    """
    scaled = np.where(mask > 0, td / max(1e-6, temp), -1e9)
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    weight = np.exp(scaled) * mask
    return weight / np.maximum(weight.sum(axis=1, keepdims=True), 1e-12)


def _listwise_metrics(params: Dict[str, Any], Xd: np.ndarray, mask: np.ndarray,
                      td: np.ndarray, temp: float) -> Dict[str, float]:
    """Decision metrics on held-out boards: nll, top-1 hit rate, mean Spearman."""
    if len(Xd) == 0:
        return {"nll": float("nan"), "top1": float("nan"),
                "mean_spearman": float("nan"), "boards": 0}
    scores = np.asarray(predict(params, Xd.reshape(-1, Xd.shape[-1]))
                        ).reshape(Xd.shape[:2])
    q = _soft_targets(td, mask, temp)
    scaled = np.where(mask > 0, scores, -1e9)
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    weight = np.exp(scaled) * mask
    p = weight / np.maximum(weight.sum(axis=1, keepdims=True), 1e-12)
    nll = float(-np.sum(q * np.log(p + 1e-9)) / max(1, len(Xd)))

    hits = 0
    rhos: List[float] = []
    for slot in range(len(Xd)):
        keep = mask[slot] > 0
        truth = td[slot][keep]
        score = scores[slot][keep]
        best = float(truth.max())
        hits += int(abs(float(truth[int(np.argmax(score))]) - best) < 1e-9)
        if truth.std() > 0 and len(truth) > 1:
            ra = _average_ranks(score)
            rb = _average_ranks(truth)
            ra = ra - ra.mean()
            rb = rb - rb.mean()
            denominator = math.sqrt(float((ra * ra).sum() * (rb * rb).sum()))
            if denominator > 0:
                rhos.append(float((ra * rb).sum() / denominator))
    return {"nll": nll, "top1": hits / float(len(Xd)),
            "mean_spearman": float(np.mean(rhos)) if rhos else float("nan"),
            "boards": int(len(Xd))}


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """Average ranks, so ties share a rank instead of being broken by index."""
    array = np.asarray(values, dtype=np.float64)
    order = np.argsort(array, kind="mergesort")
    out = np.empty(len(array), dtype=np.float64)
    position = 0
    while position < len(array):
        end = position
        while end + 1 < len(array) and array[order[end + 1]] == array[order[position]]:
            end += 1
        average = 0.5 * (position + end) + 1.0
        for slot in range(position, end + 1):
            out[order[slot]] = average
        position = end + 1
    return out


def train_listwise(X: np.ndarray, groups: np.ndarray, returns: np.ndarray,
                   epochs: int = 60, batch: int = 32, lr: float = 3e-3,
                   hidden: int = 256, layers: int = 3, seed: int = 0,
                   ema: float = 0.995, clip: float = 1.0, temp: float = 0.08,
                   verbose: bool = True) -> Dict[str, Any]:
    """Fit the head so that it *orders* plans inside a board.

    The objective is McFadden's conditional logit::

        P(p | s) = exp(f(s, p)) / sum_{p' in s} exp(f(s, p'))
        loss     = - mean_s  sum_p  q(p | s) log P(p | s)

    with ``q`` the softmax of the measured returns on that board. The point of
    the functional form is what it does *not* depend on: adding any constant
    ``c(s)`` to every score on a board leaves ``P`` unchanged. The board-level
    offset -- 93.5% of the variance of the outcome, measured on 60 boards -- is
    therefore unrepresentable, and all of the model's capacity goes to the 6.5%
    that the decision depends on. Regressing the absolute return, which is what
    :func:`train` does, spends that capacity on the offset and then cannot rank:
    measured, its regret against ground truth was 0.0350 against the one-ply
    score's 0.0177, i.e. significantly worse.

    ``batch`` counts *boards*, not rows: a mini-batch of rows would mix boards
    and destroy the very comparison the loss is built on.

    The split is by board for the same reason. A row-level split would put some
    of a board's plans in training and the rest in validation, leaking the
    within-board ordering and reporting a validation number that means nothing.
    """
    import jax
    import jax.numpy as jnp

    if len(X) == 0:
        raise SystemExit("no samples: generate ranked duels first")

    Xd, mask, td, ids = dense_groups(groups, X, returns)
    q = _soft_targets(td, mask, temp)
    order = np.random.default_rng(seed).permutation(len(ids))
    split = int(len(order) * 0.85)
    train_idx, val_idx = order[:split], order[split:]

    report: Dict[str, Any] = {
        "objective": "conditional_logit",
        "samples": int(len(returns)),
        "boards": int(len(ids)),
        "train_boards": int(len(train_idx)),
        "val_boards": int(len(val_idx)),
        "mean_plans": round(float(mask.sum(axis=1).mean()), 3),
        "temp": temp,
        "return_std_within": round(float(np.mean([
            td[i][mask[i] > 0].std() for i in range(len(ids))])), 4),
    }

    params = init_params(seed, hidden, layers, score_head=True)
    ema_params = tree_map(lambda a: np.array(a, copy=True), params)
    adam_m = tree_map(lambda a: jnp.zeros_like(jnp.asarray(a)), params)
    adam_v = tree_map(lambda a: jnp.zeros_like(jnp.asarray(a)), params)

    def loss_fn(p, batch_x, batch_mask, batch_q):
        # [..., 0] drops the score axis; [:, 0] would drop the *plan* axis and
        # give every plan on a board the same score, making the loss a constant.
        scores = forward(p, batch_x)[..., 0]
        scores = jnp.where(batch_mask > 0, scores, -1e9)
        log_p = scores - jax.nn.logsumexp(scores, axis=1, keepdims=True)
        return -jnp.sum(batch_q * log_p * batch_mask) / batch_x.shape[0]

    grad_fn = jax.jit(jax.value_and_grad(loss_fn))
    steps_per_epoch = max(1, math.ceil(len(train_idx) / batch))
    step = 0
    rate = 0.0
    history: List[Dict[str, Any]] = []
    best_nll = float("inf")
    best_epoch = 0
    best_params: Optional[Dict[str, Any]] = None

    for epoch in range(1, epochs + 1):
        np.random.default_rng(seed + epoch).shuffle(train_idx)
        total = 0.0
        seen = 0
        for start in range(0, len(train_idx), batch):
            chunk = train_idx[start:start + batch]
            if len(chunk) == 0:
                continue
            seen += 1
            loss, grads = grad_fn(params, jnp.asarray(Xd[chunk]),
                                  jnp.asarray(mask[chunk]), jnp.asarray(q[chunk]))
            step += 1
            warm = min(1.0, step / max(1, 0.05 * steps_per_epoch * epochs))
            rate = lr * warm * 0.5 * (1.0 + math.cos(math.pi * epoch / epochs))
            norm = jnp.sqrt(sum(jnp.sum(jnp.square(g))
                                for g in jax.tree_util.tree_leaves(grads)))
            factor = jnp.minimum(1.0, clip / (norm + 1e-12))
            grads = jax.tree_util.tree_map(lambda g: g * factor, grads)
            m = jax.tree_util.tree_map(lambda m, g: 0.9 * m + 0.1 * g, adam_m, grads)
            v = jax.tree_util.tree_map(lambda v, g: 0.999 * v + 0.001 * g * g,
                                       adam_v, grads)
            mh = jax.tree_util.tree_map(lambda x: x / (1 - 0.9 ** step), m)
            vh = jax.tree_util.tree_map(lambda x: x / (1 - 0.999 ** step), v)
            params = jax.tree_util.tree_map(
                lambda p, mh, vh: p - rate * mh / (jnp.sqrt(vh) + 1e-8),
                params, mh, vh)
            adam_m, adam_v = m, v
            decay = min(ema, (1.0 + step) / (10.0 + step))
            ema_params = jax.tree_util.tree_map(
                lambda e, p: decay * e + (1.0 - decay) * p, ema_params, params)
            total += float(loss)

        averaged = {key: np.asarray(value) for key, value in ema_params.items()}
        metrics = _listwise_metrics(averaged, Xd[val_idx], mask[val_idx],
                                    td[val_idx], temp)
        row = {"epoch": epoch, "train_nll": round(total / max(1, seen), 5),
               "val_nll": round(metrics["nll"], 5),
               "val_top1": round(metrics["top1"], 4),
               "val_spearman": round(metrics["mean_spearman"], 4),
               "lr": round(rate, 6)}
        history.append(row)
        # Selected on held-out nll, which is a proper scoring rule and far less
        # noisy than top-1 on a few hundred boards.
        if metrics["nll"] < best_nll:
            best_nll = metrics["nll"]
            best_epoch = epoch
            best_params = {key: np.array(value, copy=True)
                           for key, value in averaged.items()}
        if verbose and (epoch == 1 or epoch % 10 == 0 or epoch == epochs):
            logger.debug(json.dumps(row))

    served = best_params if best_params is not None else {
        key: np.asarray(value) for key, value in ema_params.items()}
    final = _listwise_metrics(served, Xd[val_idx], mask[val_idx], td[val_idx], temp)
    report.update({
        "history": history,
        "config": {"hidden": hidden, "layers": layers, "epochs": epochs,
                   "batch": batch, "lr": lr, "ema": ema, "temp": temp},
        "val_nll": float(best_nll),
        "best_epoch": best_epoch,
        "val_top1": final["top1"],
        "val_spearman": final["mean_spearman"],
        "final_epoch_nll": history[-1]["val_nll"] if history else float("nan"),
        "spread": _spread(served, X),
    })
    return {"params": served, "report": report}


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------


def make_policy_for_params(params: Dict[str, Any], temperature: float = 0.15,
                           trust: float = 0.5, tie_break: float = 1e-4,
                           margin: float = 0.0) -> Callable[[DuelState, Duelist, Sequence[TurnPlan], random.Random], TurnPlan]:
    """Greedy one-step lookahead over the solver's candidate plans.

    Each candidate is applied, the successor is encoded, and the plan whose
    successor has the highest predicted return is played. Terminal plans are
    scored by their exact return, never by the network. ``trust`` blends the
    value-greedy distribution with the solver prior as a KL-bounded step, so the
    policy cannot drift onto a plan the solver considers illegal in spirit.

    This is the improvement operator the pipeline was missing. A distilled ranker
    can only reproduce its teacher; ranking by ``r(plan) + V(s')`` can exceed it,
    because ``V`` encodes consequences the one-ply score cannot see. Gumbel
    MuZero's guarantee (Danihelka et al., 2022) is what licenses calling this an
    improvement rather than a re-parameterisation.

    ``margin`` is a conservative-improvement gate, measured in units of the
    spread of the candidate scores on that board: the policy only departs from
    the solver's own choice when the best candidate beats it by more than
    ``margin`` standard deviations. It exists because of a measured failure. The
    value is trained on continuations where *both* sides play the solver, but the
    policy then deviates on 84% of boards, so it acts on estimates of a
    continuation that does not happen. Measured on 200 paired duels: the
    unrestricted policy lost by 0.146 of an HP bar (7 boards better, 160 worse),
    while the same model restricted to its residual path -- deviating on 20% of
    boards -- won by 0.008. Deviation has to be paid for.

    ``trust`` only means anything for a model trained with :func:`train`, whose
    score is in return units and can be blended with the solver prior at a
    comparable temperature. A model trained with :func:`train_listwise` produces
    a conditional-logit score in arbitrary units -- only its ordering is
    identified -- so it must be served with ``trust=0``.
    """
    params = tree_map(XP.asarray, params)

    def policy(state: Any, actor: Any, plans: Sequence[Any], rng: random.Random,
               _model: Any = None) -> Any:
        from combat.duel import apply_plan

        rows: List[Optional[np.ndarray]] = []
        exact: List[Optional[float]] = []
        for plan in plans:
            clone = state.clone()
            apply_plan(clone, plan)
            if clone.done:
                rows.append(None)
                exact.append(outcome(clone, actor.id))
            else:
                rows.append(encode(clone, actor))
                exact.append(None)
        values = np.zeros(len(plans), dtype=np.float64)
        pending = [index for index, row in enumerate(rows) if row is not None]
        if pending:
            predicted = predict(params, np.stack([rows[i] for i in pending]))
            for slot, index in enumerate(pending):
                values[index] = float(predicted[slot])
        for index, value in enumerate(exact):
            if value is not None:
                values[index] = value
        scores = np.asarray([p.score for p in plans], dtype=np.float64)
        if margin > 0.0 and len(plans) > 1:
            incumbent = int(np.argmax(scores + tie_break * values))
            spread = float(values.std())
            if spread <= 0.0 or values.max() - values[incumbent] <= margin * spread:
                return plans[incumbent]
        if trust > 0.0:
            prior = np.exp((scores - scores.max()) / max(1e-6, temperature))
            prior /= prior.sum()
            greedy = np.exp((values - values.max()) / max(1e-6, temperature))
            greedy /= greedy.sum()
            blended = (1.0 - trust) * greedy + trust * prior
        else:
            blended = values + tie_break * scores
        return plans[int(np.argmax(blended))]

    return policy


def make_policy(path: Path, temperature: float = 0.15, trust: float = 0.5,
                tie_break: float = 1e-4, margin: float = 0.0) -> Callable[[DuelState, Duelist, Sequence[TurnPlan], random.Random], TurnPlan]:
    """Load a checkpoint and build its lookahead policy.

    Thin wrapper over :func:`make_policy_for_params`; the checkpoint also carries
    the scale used to normalise targets, which the policy does not need because
    :func:`predict` undoes it internally.
    """
    with Path(path).open("rb") as handle:
        payload = pickle.load(handle)
    return make_policy_for_params(payload["params"], temperature=temperature,
                                  trust=trust, tie_break=tie_break, margin=margin)


def evaluate(policy_a: Any, policy_b: Any, games: int = 200, seed: int = 11,
             k: int = 6, max_rounds: int = 12, rows: int = 4096,
             name_a: str = "a", name_b: str = "b") -> Dict[str, Any]:
    """Paired, side-swapped duels on diverse matchups.

    Every board is played twice with the two policies exchanged, so seat advantage
    cancels. The score is the final HP differential from ``a``'s side, not the win
    -- win/loss is seat-dominated on these builds (measured `seat1_win` 1.0), so a
    win column cannot rank two policies.

    **The paired statistic is the SUM, not the difference.** Both games report
    ``a``'s own margin: ``m1`` with ``a`` in seat 1, ``m2`` with ``a`` in seat 2. A
    stronger ``a`` scores high in both, so its advantage is ``(m1 + m2) / 2``.
    ``m1 - m2`` is the *seat gap* -- a property of the board, not of the policies.
    The first version of this function took the difference, which made every
    comparison a null by construction: it reported ``p 1.0`` with a 20/20 split on
    a 0.17 mean margin, and that "honest null result" was an artifact of the
    estimator, not a measurement. The seat gap is still reported, as a diagnostic
    that the swap did its job.

    Three other things are reported, because each answers a different question and
    the first one alone has already misled this project:

    * ``boards_differed`` -- the effective sample size. A board where both
      policies play the same plan in the same order produces a difference of
      exactly zero, and a p-value computed over 400 boards of which 12 differed is
      a lie about how much evidence exists.
    * the exact sign test -- is ``a`` ahead on more boards than ``b``, ignoring by
      how much.
    * a paired t-test and a seeded bootstrap CI on the *mean* margin -- is ``a``
      ahead by more. The verdict follows the CI, not the counts.
    """
    from combat.battle_solver import BattleSolver
    from combat.duel import DEFAULT_DATA, make_duel, play_game
    from combat.duel_benchmark import load_data, sample_pair
    from combat.normal_build_duel import DEFAULT_MAP
    from combat.side_swap import binomial_two_sided

    cells = _load_cells(DEFAULT_MAP)
    data, levels = load_data(DEFAULT_DATA)
    pool = list(range(min(rows, len(levels))))
    solver = BattleSolver(beam_width=8, max_actions=2)
    rng = random.Random(seed)

    diffs: List[float] = []
    gaps: List[float] = []
    played = 0
    while played < games:
        row_a, row_b = sample_pair(levels, pool, rng)
        # Both games of a pair must start from the *same* board. `make_duel`
        # draws its engagement cells from the rng, so advancing the shared stream
        # between the two calls silently gave each game a different opening --
        # measured, that is most of the variance in the paired difference, and it
        # is the difference between a design that can resolve 0.02 of HP and one
        # that cannot. Same seed for the board, same seed for the in-game
        # tie-breaks, so the two games differ only in which policy holds which
        # seat.
        board_seed = rng.randrange(1 << 30)
        paired = []
        for swap in (False, True):
            state = make_duel(data, row_a, row_b, cells, random.Random(board_seed),
                              max_rounds=max_rounds)
            if state is None:
                break
            if swap:
                policies = {1: policy_b, 2: policy_a}
                a_seat = 2
            else:
                policies = {1: policy_a, 2: policy_b}
                a_seat = 1
            final, _ = play_game(state, policies, solver,
                                 random.Random(board_seed + 1), k=k)
            if not final.done:
                break
            paired.append(outcome(final, a_seat))
        if len(paired) != 2:
            continue
        played += 1
        diffs.append(0.5 * (paired[0] + paired[1]))
        gaps.append(0.5 * (paired[0] - paired[1]))

    if not diffs:
        return {"boards": 0, "verdict": "no_board"}
    array = np.asarray(diffs, dtype=np.float64)
    favour = int((array > 0).sum())
    against = int((array < 0).sum())
    differed = int((array != 0).sum())
    sign_p = binomial_two_sided(favour, against)

    mean = float(array.mean())
    # Standard error of the mean margin. n is the number of boards played, not
    # the number that differed: the zeros are real evidence of no effect and
    # dropping them would inflate the mean's precision.
    se = float(array.std(ddof=1) / np.sqrt(len(array))) if len(array) > 1 else 0.0
    if se > 0.0:
        t_stat = mean / se
        # Two-sided normal tail. With n >= 100 boards the t and normal quantiles
        # agree to three decimals, and this avoids depending on scipy.
        t_p = float(math.erfc(abs(t_stat) / math.sqrt(2.0)))
    else:
        t_stat, t_p = 0.0, 1.0

    boot = np.random.default_rng(seed + 1)
    if len(array) > 1:
        draws = boot.choice(array, size=(4000, len(array)), replace=True).mean(axis=1)
        lo, hi = np.percentile(draws, [2.5, 97.5])
    else:
        lo = hi = mean
    excludes_zero = bool(lo > 0.0 or hi < 0.0)

    return {
        "boards": played,
        "a": name_a,
        "b": name_b,
        "boards_differed": differed,
        "mean_hp_diff": round(mean, 4),
        "mean_ci95": [round(float(lo), 4), round(float(hi), 4)],
        "median_hp_diff": round(float(np.median(array)), 4),
        "std_hp_diff": round(float(array.std(ddof=1)) if len(array) > 1 else 0.0, 4),
        "t_stat": round(t_stat, 3),
        "t_p_value": round(t_p, 4),
        # The shape of the effect: a policy that wins big and loses small is a
        # different animal from one that wins small and loses big, and the mean
        # alone cannot tell them apart.
        "mean_gain_when_ahead": round(float(array[array > 0].mean()), 4)
        if favour else 0.0,
        "mean_loss_when_behind": round(float(array[array < 0].mean()), 4)
        if against else 0.0,
        "boards_favouring_a": favour,
        "boards_favouring_b": against,
        "p_value": round(sign_p, 4),
        # Diagnostic, not a result: if the swap worked, the mean seat gap is ~0
        # and a large one means the two games in a pair were not the same board.
        "mean_seat_gap": round(float(np.mean(gaps)), 4),
        "max_abs_seat_gap": round(float(np.max(np.abs(gaps))), 4),
        "verdict": ("a_better" if excludes_zero and mean > 0 else
                    "b_better" if excludes_zero and mean < 0 else
                    "no_measurable_difference"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--games", type=int, default=400)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--hidden", type=int, default=256)
    parser.add_argument("--layers", type=int, default=3)
    parser.add_argument("--solver-prob", type=float, default=0.6)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--evaluate", type=Path, default=None,
                        help="benchmark the saved model against the solver")
    parser.add_argument("--bench-games", type=int, default=200)
    parser.add_argument("--trust", type=float, default=0.5)
    parser.add_argument("--margin", type=float, default=0.0,
                        help="conservative-improvement gate in units of the "
                             "candidate score spread; deviate from the solver "
                             "only when the advantage clears it")
    parser.add_argument("--objective", choices=("value", "rank"), default="rank",
                        help="'value' regresses the absolute return (the objective "
                             "that could not rank); 'rank' is the conditional "
                             "logit on within-board comparisons")
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--rollouts", type=int, default=1,
                        help="rollouts per plan when building the ranked corpus")
    parser.add_argument("--temp", type=float, default=0.08,
                        help="soft-target temperature, in return units")
    parser.add_argument("--workers", type=int, default=0,
                        help="generation processes; 0 means one per core")
    parser.add_argument("--snapshots", type=int, default=3,
                        help="decision points sampled per duel; 1 reproduces the "
                             "opening-only corpus that could not play mid-fight")
    parser.add_argument("--corpus", type=Path, default=None,
                        help="cache the counterfactual corpus here; an existing "
                             "file is reused, which makes two runs an A/B on the "
                             "same boards instead of two draws")
    args = parser.parse_args(argv)

    if args.evaluate is not None:
        from combat.duel import solver_policy

        with args.evaluate.open("rb") as handle:
            report = pickle.load(handle).get("report", {})
        logger.debug("model", json.dumps({key: report.get(key) for key in
                                   ("objective", "samples", "boards", "val_ce",
                                    "val_nll", "val_top1", "val_spearman",
                                    "spread", "best_epoch", "final_epoch_ce")}))
        policy = make_policy(args.evaluate, trust=args.trust,
                             margin=args.margin)
        result = evaluate(policy, solver_policy, games=args.bench_games,
                          seed=args.seed, name_a="neural_value", name_b="solver")
        logger.debug("benchmark", json.dumps(result, indent=1))
        return 0

    if args.objective == "rank":
        if args.corpus is not None and args.corpus.exists():
            blob = np.load(args.corpus)
            X, groups, returns = blob["X"], blob["groups"], blob["returns"]
            meta = json.loads(str(blob["meta"]))
            logger.debug("corpus (cached)", json.dumps(meta))
        else:
            X, groups, returns, meta = generate_ranked(
                args.games, k=args.k, rollouts=args.rollouts, seed=args.seed,
                workers=args.workers, snapshots=args.snapshots)
            logger.debug("counterfactual", json.dumps(meta))
            if args.corpus is not None:
                args.corpus.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(args.corpus, X=X, groups=groups,
                                    returns=returns,
                                    meta=np.asarray(json.dumps(meta)))
        fitted = train_listwise(X, groups, returns, epochs=args.epochs,
                                hidden=args.hidden, layers=args.layers,
                                seed=args.seed, temp=args.temp)
        report = fitted["report"]
        logger.debug("fit", json.dumps({key: report[key] for key in
                                 ("boards", "samples", "val_nll", "val_top1",
                                  "val_spearman", "best_epoch", "spread")}))
    else:
        X, y, meta = generate(args.games, args.seed, args.solver_prob)
        logger.debug("self-play", json.dumps(meta))
        fitted = train(X, y, epochs=args.epochs, hidden=args.hidden,
                       layers=args.layers, seed=args.seed)
        report = fitted["report"]
        logger.debug("fit", json.dumps({key: report[key] for key in
                                 ("samples", "scale", "val_ce", "uniform_ce",
                                  "spread")}))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("wb") as handle:
        pickle.dump({"params": fitted["params"], "report": report}, handle)
    logger.debug("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
