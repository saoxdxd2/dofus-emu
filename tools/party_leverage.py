"""How often does one ally's decision change the outcome? That is the ceiling on any per-actor edit.

Every leverage number measured so far in this project was drawn with a single-actor ruler: the
published 3.8% comes from enumerating *the player's* (spell, target) on a 1-vs-4 board, applying one
candidate and playing the rest of the fight under a fixed continuation. On a party the question
changes, and so must the ruler. A weight-space edit, an authored head, a lookup table or a wire can
only ever change **one actor's decision at a time** -- everyone else is still playing the reference
plan. So the quantity that bounds all of those methods together is:

    the share of party decision states where changing some living ally's action flips the fight.

This measures it per ally and as a union, with the same instruments the published numbers used: the
reference line and the continuation are `combat.encounter.heuristic.heuristic_actions` and
`train.make_baseline_rollout`, both sides scripted, and the label is a terminal win -- not a score,
not a value estimate.

Three things it is built to fail on, deliberately:

  * **control**: a candidate equal to the reference action must reproduce the reference outcome on
    every state. If it does not, the continuation is not deterministic and nothing below means
    anything.
  * **saturation**: four allies deal roughly four times the damage, so the win rate can climb
    towards 1 while the decided-state share collapses for want of close fights. The base win rate
    is reported beside the share for exactly that reason -- a share measured on decided-by-nothing
    boards is not a ceiling, it is an artifact.
  * **config**: the corpus' actor width must equal `allies + 4`, read from the corpus meta. A 4v4
    file scored as 1v4 is four dead allies, and the wrong team split makes the numbers meaningless
    rather than merely small.

    python tools/party_leverage.py --corpus <party npz>            # allies from the corpus meta
    python tools/party_leverage.py --corpus <1v4 npz> --allies 1   # the published ruler, for contrast
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
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

import jax.numpy as jnp                                          # noqa: E402
import numpy as np                                               # noqa: E402

from combat.encounter.env import (Config, action_masks,           # noqa: E402
                                  initial_state, step)
from combat.encounter.heuristic import heuristic_actions
from combat.encounter.train import make_baseline_rollout

EVIDENCE = pathlib.Path(os.environ.get(
    "DOFUS_EVIDENCE", pathlib.Path.home() / "Documents" / "dofus-evidence")) / "touch-data"


def load(path: pathlib.Path, boards: int):
    z = np.load(path)
    arrays = {k: jnp.asarray(z[k][:boards]) for k in z.files
              if np.issubdtype(z[k].dtype, np.number) or z[k].dtype == np.bool_}
    tables = {k: v for k, v in arrays.items() if k.startswith("spell_")}
    spec = {k: v for k, v in arrays.items() if not k.startswith("spell_")}
    return spec, tables


def as_np(x: Dict[str, object]) -> Dict[str, np.ndarray]:
    return {k: np.asarray(v) for k, v in x.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=str(EVIDENCE / "encounters_party_s4_2048.npz"))
    ap.add_argument("--meta", default=None, help="corpus meta json (default: <corpus>.json)")
    ap.add_argument("--allies", type=int, default=None,
                    help="override the party size the corpus was generated with")
    ap.add_argument("--enemies", type=int, default=4, help="monster slots, as generated")
    ap.add_argument("--boards", type=int, default=128)
    ap.add_argument("--max-rounds", type=int, default=24)
    ap.add_argument("--include-done", action="store_true",
                    help="count states on boards the fight has already decided, which is what "
                         "`wsp_program_target.collect_labels` does -- the published 3.8%% ceiling "
                         "was measured over that denominator, and a decided board can never be "
                         "mixed, so it only dilutes the share")
    args = ap.parse_args(argv)

    path = pathlib.Path(args.corpus)
    allies = args.allies
    if allies is None:
        meta = pathlib.Path(args.meta or (str(path).rsplit(".", 1)[0] + ".json"))
        allies = int(json.loads(meta.read_text(encoding="utf-8")).get("allies", 1))
    cfg = Config(allies=allies, enemies=args.enemies, spells=4, max_rounds=args.max_rounds)
    length = 2 * cfg.max_rounds

    spec, tables = load(path, args.boards)
    if spec["hp"].shape[1] != cfg.n_actors:
        raise SystemExit("%s was generated with %d actor slots; --allies %d --enemies %d asks for "
                         "%d. The party size is not a free parameter at scoring time."
                         % (path.name, spec["hp"].shape[1], allies, args.enemies, cfg.n_actors))
    state0 = initial_state(spec, tables, cfg)
    rollout = make_baseline_rollout(cfg, length)

    # candidates: which ally deviates, and to which (spell, target). The walk is held at the
    # reference plan so this prices the pair axis -- the axis a target head or a wire edits.
    cands = np.asarray([(a, s, t) for a in range(allies)
                        for s in range(cfg.spells) for t in range(cfg.n_actors)], dtype=np.int64)
    K = cands.shape[0]
    # which board each row belongs to, so decided states can be attributed back to boards: states
    # inside one fight are strongly correlated, and an interval that treats 363 states as 363
    # independent draws is far too narrow -- the 64- and 128-board runs disagreeing by more than
    # their intervals is exactly what that mistake looks like
    board_of_row = np.arange(args.boards)

    state = state0
    base_win_flags, decided_union, per_ally_mixed, states_counted = [], 0, np.zeros(allies), 0
    per_board_decisions = np.zeros(args.boards, dtype=np.int64)
    per_board_states = np.zeros(args.boards, dtype=np.int64)
    control_bad = 0
    for _t in range(length):
        acting = np.asarray(state["acting_team"])
        done = np.asarray(state["done"])
        rows = np.where((acting == 0) & (~done if not args.include_done else True))[0]
        if rows.size:
            boards_here = board_of_row[rows]
            sub = {k: v[rows] for k, v in state.items()}
            sub_t = {k: v[rows] for k, v in tables.items()}
            base = heuristic_actions(sub, sub_t, cfg)
            step_base = step(sub, base, sub_t, cfg)
            ref = np.asarray(rollout(step_base, sub_t)["winner"]) == 0
            base_win_flags.append(ref)
            masks = action_masks(sub, sub_t, cfg)
            ms = np.asarray(masks["spell"])[:, :, :cfg.spells]        # [R,N,S]
            mt = np.asarray(masks["target"])[:, :, :cfg.spells, :]    # [R,N,S,T]
            big = {k: jnp.asarray(np.repeat(np.asarray(v), K, axis=0)) for k, v in sub.items()}
            big_t = {k: jnp.asarray(np.repeat(np.asarray(v), K, axis=0)) for k, v in sub_t.items()}
            # copy, not asarray: `np.asarray` of a jax array is a read-only view, and the deviation
            # is written into these arrays a line later
            acts = {k: np.array(v, copy=True)
                    for k, v in heuristic_actions(big, big_t, cfg).items()}
            tiled = np.tile(cands, (rows.size, 1))
            # actions are per-actor *chosen indices* -- [B,N] each, not [B,N,S] -- so the deviation
            # writes one column per row, at that row's own ally index
            ridx = np.arange(rows.size * K)
            acts["spell"][ridx, tiled[:, 0]] = tiled[:, 1]
            acts["target"][ridx, tiled[:, 0]] = tiled[:, 2]
            # the masks belong to R states and the batch is R*K rows, so they repeat with it --
            # indexing the un-repeated mask with an R*K range is an out-of-bounds read, not a bug
            # that shows up as a wrong number
            ms = np.repeat(ms, K, axis=0)
            mt = np.repeat(mt, K, axis=0)
            idx = np.arange(rows.size * K)          # 1-D: paired with `tiled`, not broadcast
            legal = ms[idx, tiled[:, 0], tiled[:, 1]] & \
                mt[idx, tiled[:, 0], tiled[:, 1], tiled[:, 2]]
            won = np.asarray(rollout(step(big, acts, big_t, cfg), big_t)["winner"]) == 0
            lab = won.reshape(rows.size, K)
            ok = legal.reshape(rows.size, K)
            # control: the candidate equal to the reference action must reproduce the reference
            base_idx = np.arange(rows.size)
            bs, bt = np.asarray(base["spell"]), np.asarray(base["target"])
            for a in range(allies):
                # the candidate grid is real spells only, so an ally the reference line let *pass*
                # has no matching column -- its control is skipped rather than wrapped around into
                # another ally's candidate, which is the bug that reads as index 128 of 128
                castable = bs[:, a] < cfg.spells
                if not castable.any():
                    continue
                sel_idx = base_idx[castable]
                col = (a * cfg.spells * cfg.n_actors + bs[sel_idx, a] * cfg.n_actors
                       + bt[sel_idx, a])
                control_bad += int(np.count_nonzero(lab[sel_idx, col] != ref[castable]))
            any_mixed = np.zeros(rows.size, dtype=bool)
            for a in range(allies):
                sel = slice(a * cfg.spells * cfg.n_actors, (a + 1) * cfg.spells * cfg.n_actors)
                valid = ok[:, sel].any(axis=1)
                l = lab[:, sel].astype(np.int8)
                # filled outside {0,1} instead of NaN: `nanmin` of an all-illegal row emits an
                # All-NaN RuntimeWarning that a green run would hide, and a warning in a
                # measurement tool is a defect report nobody reads
                mn = np.min(np.where(ok[:, sel], l, 2), axis=1)
                mx = np.max(np.where(ok[:, sel], l, -1), axis=1)
                mixed = valid & (mn != mx)
                per_ally_mixed[a] += float(np.count_nonzero(mixed))
                any_mixed |= mixed
            per_board_decisions[boards_here] += any_mixed.astype(np.int64)
            per_board_states[boards_here] += 1
            decided_union += int(any_mixed.sum())
            states_counted += int(rows.size)
        acts = heuristic_actions(state, tables, cfg)
        state = step(state, acts, tables, cfg)

    win = np.concatenate(base_win_flags) if base_win_flags else np.zeros(0, bool)
    out = {
        "corpus": path.name, "allies": allies, "enemies": args.enemies,
        "boards": int(args.boards), "n_actors": cfg.n_actors,
        "decision_states": states_counted,
        "base_win_rate": round(float(win.mean()), 6) if win.size else None,
        "states_decided_by_one_ally_deviation": decided_union,
        "leverage_share": round(decided_union / max(states_counted, 1), 6),
        "leverage_share_per_ally": {str(a): round(float(per_ally_mixed[a] /
                                                       max(states_counted, 1)), 6)
                                    for a in range(allies)},
        "control_mismatches": control_bad,
        "candidates_per_state": K,
        "done_states_included": bool(args.include_done),
        "boards_with_decision_states": int(np.count_nonzero(per_board_states)),
        "boards_with_any_decided_state": int(np.count_nonzero(per_board_decisions)),
        "decided_states_per_active_board_mean": round(
            float(per_board_decisions[per_board_states > 0].mean()), 3) if states_counted else None,
        "decided_states_most_in_one_board": int(per_board_decisions.max()),
    }
    # cluster bootstrap over BOARDS, not states: resample fights and divide the resampled decided
    # states by the resampled states, which is the interval a reader can actually use
    active = np.where(per_board_states > 0)[0]
    boot = []
    draw = np.random.default_rng(args.boards)
    for _ in range(2000):
        sel = draw.choice(active, size=active.size, replace=True)
        tot = per_board_states[sel].sum()
        if tot:
            boot.append(per_board_decisions[sel].sum() / tot)
    boot = np.asarray(boot)
    out["leverage_share_board_bootstrap_ci95"] = [
        round(float(np.quantile(boot, 0.025)), 6), round(float(np.quantile(boot, 0.975)), 6)]
    print(json.dumps(out, indent=1, sort_keys=True))
    if control_bad:
        print(json.dumps({"verdict": "the continuation is not deterministic: %d reference "
                                     "candidates did not reproduce the reference outcome"
                          % control_bad}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
