"""Does a shared bid beat the same actors deciding alone? The wire, priced on terminal wins.

Section XI measured 5.03% of party states where *one* ally's different decision flips the fight --
the ceiling on every per-actor method. This tool prices the thing a per-actor ruler cannot see: all
four allies changing their minds at once because they read each other's intention.

The channel is one hop and no negotiation. Each ally first computes what it would do alone (the
shipped scripted value function, `combat.encounter.heuristic`), the intended targets are summed into
a per-actor attention count, and everyone re-decides with that count added to their score at
`weight` per bid. `weight=0` is therefore the independent arm *by construction*, and that is the
control: if the wired arm at weight 0 differs from the independent arm at all, the instrument is
broken and nothing else in this file means anything.

Three arms are worth pricing, and the sign is not obvious:

  * **concentrate** (weight > 0): "two of us are already on him" pulls the third onto the same
    target, which kills an actor before it acts -- the mechanism the per-actor ceiling cannot express.
  * **spread** (weight < 0): overkill. Four allies finishing one monster while three others take
    full turns is the failure mode a party actually has.
  * the reference line's *accidental* concentration is what both must beat, so the report includes
    how many decisions the channel actually moves: a wire that changes nothing is a wire nobody reads.

    python tools/bid_channel.py --boards 256 --weights 0,1,4,-4
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Dict

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
for entry in (str(BRAIN), str(REPO / "tools")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import jax                                                       # noqa: E402
import jax.numpy as jnp                                          # noqa: E402
import numpy as np                                               # noqa: E402

from combat.encounter.env import Config, initial_state            # noqa: E402
from combat.encounter.heuristic import action_values, heuristic_actions
# the party rollout loop is imported, not re-written: `make_player_rollout` is the same instrument
# the published move-write numbers were scored with, so a second copy here would be a second ruler
from wsp_program_target import make_player_rollout                # noqa: E402
from party_leverage import load                                  # noqa: E402

EVIDENCE = pathlib.Path(os.environ.get(
    "DOFUS_EVIDENCE", pathlib.Path.home() / "Documents" / "dofus-evidence")) / "touch-data"


def paired(arm: np.ndarray, ref: np.ndarray) -> Dict[str, object]:
    d = arm.astype(float) - ref.astype(float)
    n = max(d.size, 1)
    se = float(np.std(d, ddof=1) / np.sqrt(n)) if d.size > 1 else 0.0
    return {"delta": round(float(d.mean()), 6),
            "ci95": [round(float(d.mean() - 1.96 * se), 6),
                     round(float(d.mean() + 1.96 * se), 6)]}


def wired_actions(state, tables, cfg, weight: float):
    """One hop: aim alone, publish where the team is aiming, aim again.

    `weight` is in **gap units**, not score units: the bid count is multiplied by the median gap
    between an actor's best and second-best legal (spell, target) pair. Measured first because it is
    the whole experiment -- a bid weighted at 1.0 against damage scores in the hundreds changes no
    decision, and reporting that as "the channel does nothing" would be reporting my own scale
    error, not the mechanism.
    """
    first = heuristic_actions(state, tables, cfg)
    if weight == 0.0:
        return first
    unit = jnp.asarray(score_gap(state, tables, cfg), dtype=jnp.float32)
    spell = jnp.asarray(first["spell"])
    target = jnp.asarray(first["target"])
    acting = jnp.asarray(state["acting_team"])[:, None]
    mine = jnp.asarray(state["alive"]) & (jnp.asarray(state["team"]) == acting)
    # an actor that passes has no intention to publish -- counting its slot-0 default as a bid on
    # actor 0 is how a wire ends up advertising a target nobody chose
    intent = jnp.where(mine & (spell < cfg.spells), target, cfg.n_actors)
    onehot = jax.nn.one_hot(intent, cfg.n_actors + 1)[:, :, :cfg.n_actors]
    onehot = onehot * mine[:, :, None].astype(onehot.dtype)
    attention = onehot.sum(axis=1)                                # [B,T]
    return heuristic_actions(state, tables, cfg, attention=attention,
                             weight=weight * unit)


def score_gap(state, tables, cfg):
    """Mean gap between each acting actor's best and next-best legal pair, in score units.

    Trace-safe by construction (no Python branch, no boolean of a tracer) because it is called
    inside the scan. Actors whose best pair is an instant kill are excluded: that gap is the 1e6
    bonus itself, and letting it set the unit would price a bid at the size of an execution.
    """
    value, legal, acting_actor = action_values(state, tables, cfg)
    legal_value = jnp.where(legal, value, -jnp.inf)
    flat = jnp.sort(legal_value.reshape(legal_value.shape[0], legal_value.shape[1], -1), axis=-1)
    top, second = flat[..., -1], flat[..., -2]
    # second must be a real legal pair, not the pass column's -1e9 fill or an -inf pad: a
    # party actor with one legal spell otherwise reports a gap of a billion, which is my own
    # bookkeeping, not the decision the bid has to overturn
    usable = (acting_actor & jnp.isfinite(top) & jnp.isfinite(second)
              & (top < 1.0e5) & (second > -1.0e8))
    gap = jnp.where(usable, top - second, 0.0)
    return gap.sum() / jnp.maximum(usable.sum(), 1.0)


def concentration(actions, state, cfg) -> float:
    """Mean number of allies aimed at the same target this turn -- the mechanism, not the win."""
    spell = np.asarray(actions["spell"])
    target = np.asarray(actions["target"])
    team = np.asarray(state["team"])
    acting = np.asarray(state["acting_team"])
    mine = (team == acting[:, None]) & np.asarray(state["alive"]) & (spell < cfg.spells)
    out = []
    for row in range(spell.shape[0]):
        picks = target[row][mine[row]]
        if picks.size < 2:
            continue
        counts = np.bincount(picks, minlength=cfg.n_actors)
        out.append(float(counts.max()))
    return float(np.mean(out)) if out else 0.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=str(EVIDENCE / "encounters_party_s4_2048.npz"))
    ap.add_argument("--allies", type=int, default=None)
    ap.add_argument("--boards", type=int, default=256)
    ap.add_argument("--weights", default="0,1,4,-4")
    ap.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    ap.add_argument("--max-rounds", type=int, default=24)
    args = ap.parse_args(argv)

    path = pathlib.Path(args.corpus)
    allies = args.allies
    if allies is None:
        meta = pathlib.Path(str(path).rsplit(".", 1)[0] + ".json")
        allies = int(json.loads(meta.read_text(encoding="utf-8")).get("allies", 1))
    cfg = Config(allies=allies, enemies=4, spells=4, max_rounds=args.max_rounds)
    length = 2 * cfg.max_rounds
    spec, tables = load(path, args.boards)
    if spec["hp"].shape[1] != cfg.n_actors:
        raise SystemExit("%s has %d actor slots, --allies %d asks for %d"
                         % (path.name, spec["hp"].shape[1], allies, cfg.n_actors))
    state0 = initial_state(spec, tables, cfg)
    # the unit the weights are expressed in, read off the opening turn
    gap0 = float(score_gap(state0, tables, cfg))

    weights = [float(w) for w in args.weights.split(",")]
    # `make_player_rollout` calls the policy as player(state, tables, cfg) -- three arguments, so a
    # two-argument lambda here silently binds cfg into the weight slot and the "bid" becomes a
    # Config object being multiplied into a score tensor.
    wins: Dict[str, np.ndarray] = {}
    for w in weights:
        rollout = make_player_rollout(cfg, length,
                                      lambda s, t, c, w=w: wired_actions(s, t, c, w))
        wins[str(w)] = np.stack([np.asarray(rollout(state0, tables)["winner"]) == 0
                                 for _ in args.seeds]).mean(axis=0)

    # mechanism and vacuity, read off the opening turn rather than inferred from the win rate
    base = heuristic_actions(state0, tables, cfg)
    wired0 = wired_actions(state0, tables, cfg, 0.0)
    control_mismatch = int(np.count_nonzero(np.asarray(wired0["spell"]) !=
                                           np.asarray(base["spell"]))) + \
        int(np.count_nonzero(np.asarray(wired0["target"]) != np.asarray(base["target"])))
    moved = {}
    for w in weights:
        act = wired_actions(state0, tables, cfg, w)
        moved[str(w)] = {"targets_changed": int(np.count_nonzero(
            np.asarray(act["target"]) != np.asarray(base["target"]))),
            "mean_allies_on_busiest_target": {
                "independent": round(concentration(base, state0, cfg), 3),
                "wired": round(concentration(act, state0, cfg), 3)}}

    ref = wins["0.0"] if "0.0" in wins else wins[str(float(weights[0]))]
    out = {
        "corpus": path.name, "allies": allies, "boards": int(args.boards),
        "score_gap_opening": round(gap0, 2),
        "weight_unit": "multiples of the best-vs-next-best score gap",
        "seeds": list(args.seeds), "n_actors": cfg.n_actors,
        "control_mismatches_at_weight_zero": control_mismatch,
        "terminal_win_rate": {k: round(float(v.mean()), 6) for k, v in wins.items()},
        "paired_vs_independent": {k: paired(v, ref) for k, v in wins.items()},
        "opening_turn_channel_effect": moved,
        "base_win_rate_reference": round(float(ref.mean()), 6),
        "saturation_warning": bool(ref.mean() > 0.75 or ref.mean() < 0.25),
    }
    print(json.dumps(out, indent=1, sort_keys=True))
    if control_mismatch:
        print(json.dumps({"verdict": "weight 0 is not the independent arm -- %d opening decisions "
                                     "moved, so the comparison has no baseline"
                          % control_mismatch}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
