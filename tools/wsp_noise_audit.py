"""Which fight weights are load-bearing, which are noise, and where a dead coordinate is a bug.

This is the use of weight-space programming the user named: not "train a smaller model to write
weights", but read a trained network, find the coordinates that do nothing, tell whether that is
meaningless noise or a defect, and fix it by writing directly.

Method, all of it exact and none of it training. The action heads are linear reads of the cached
trunk vector, so for any proposed change to a weight the resulting decision is computed from the
cached activations -- no rollout, no duel, no approximation:

  zero-column   set one whole output column (one action's row of the head) to zero and count how many
                cached decisions change. A column that changes nothing on thousands of boards is not
                "subtle", it is unused weight: it cannot be carrying the policy.
  zero-coord    same for individual input x output coordinates, in blocks, to find how much of the
                matrix is inert.
  runner-up     how often each action sits immediately below the chosen one. This is the honest
                measure of "the policy nearly did something else", which is what a bias-scale write
                can actually reach -- a column that is never chosen and never runner-up is not being
                suppressed, it is out of the running.
  coord blocks  zero blocks of input x output coordinates to see whether any region of the matrix is
                inert mass.

`--boards` decisions come from a raw outcome cache (`wsp_program cache-pvm`), which also stores the
legal masks; a column is only ever judged on boards where it could have been picked, otherwise the
mask is credited for the silence.

    python tools/wsp_noise_audit.py --cache <cache.npz> --head spell
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import pickle
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

import numpy as np  # noqa: E402


def _load(cache_path, head):
    with np.load(cache_path, allow_pickle=False) as z:
        if str(z.get("cache_kind", "")) != "raw_pvm_outcomes_v1":
            raise SystemExit("the noise audit needs a raw outcome cache (hidden + masks); rebuild "
                             "with `wsp_program cache-pvm`. Found cache_kind=%r"
                             % str(z.get("cache_kind", "<absent>")))
        if str(z.get("head", "")) != head:
            raise SystemExit("cache was built for head %r, asked to audit %r"
                             % (str(z["head"]), head))
        hidden = z["hidden"].astype(np.float64)
        mask = z["head_mask"].astype(bool)
        pick = z["head_pick"].astype(int)
        source = pathlib.Path(str(z["checkpoint_path"].item())).resolve()
        digest = str(z["checkpoint_sha256"].item())
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise SystemExit("the cache's checkpoint has changed under it; rebuild before auditing")
    return hidden, mask, pick, source


def decisions(weight, bias, hidden, mask):
    logits = hidden @ weight + bias
    return np.argmax(np.where(mask, logits, -1e9), axis=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--head", default="spell", choices=("spell", "move"))
    ap.add_argument("--coord-block", type=int, default=64,
                    help="how many input x output coordinates to zero per block")
    ap.add_argument("--margin", type=float, default=4.0,
                    help="logit gap within which an action counts as being in contention; the "
                         "measured median spell margin on boss boards was 0.0 and the median move "
                         "margin 9.26, so 4 sits between the two regimes rather than at either")
    args = ap.parse_args(argv)

    cache = pathlib.Path(args.cache)
    hidden, mask, pick, source = _load(cache, args.head)
    payload = pickle.loads(source.read_bytes())
    weight, bias = (np.asarray(payload["params"][args.head][0], dtype=np.float64),
                   np.asarray(payload["params"][args.head][1], dtype=np.float64))
    base = decisions(weight, bias, hidden, mask)
    n = len(base)
    if not np.array_equal(base, pick):
        raise SystemExit("the cached choices are not the argmax of these weights (%d of %d differ): "
                         "the cache and the checkpoint do not belong to each other, and a column "
                         "judged against the wrong baseline is not a measurement"
                         % (int((base != pick).sum()), n))
    ranked = np.argsort(np.where(mask, hidden @ weight + bias, -1e9), axis=1)
    margins = np.where(mask, hidden @ weight + bias, -1e9)
    margin = margins.max(axis=1)[:, None] - margins          # [boards, columns]
    columns = []
    for col in range(weight.shape[1]):
        # Judged only on boards where this action is legal: on the others the mask, not the weight,
        # is what keeps the column silent, and counting those as "inert" would credit the weights
        # with a decision they were never allowed to make.
        legal = mask[:, col]
        trial = weight.copy()
        trial[:, col] = 0.0
        flipped = int((decisions(trial, bias, hidden, mask)[legal] != base[legal]).sum())
        sign = weight.copy()
        sign[:, col] = -sign[:, col]
        inverted = int((decisions(sign, bias, hidden, mask)[legal] != base[legal]).sum())
        columns.append({"column": int(col), "legal_boards": int(legal.sum()),
                        "chosen_count": int((base[legal] == col).sum()),
                        "runner_up_count": int((ranked[:, -2][legal] == col).sum()),
                        "boards_within_margin": int((margin[:, col][legal] <= args.margin).sum()),
                        "median_margin_to_chosen": round(float(np.median(margin[:, col][legal])), 3)
                        if legal.any() else None,
                        "zero_changes_decisions": flipped,
                        "sign_flip_changes_decisions": inverted,
                        # Two different silences. `weights_inert` says the matrix cannot move this
                        # column's ranking; `in_contention` says whether the column is anywhere near
                        # the winning logit. A column that is inert and out of contention could be
                        # meaningless noise OR a deliberate clamp -- the weights alone cannot tell
                        # those apart, so the report says which question is still open instead of
                        # guessing (see `reading`).
                        "weights_inert": flipped == 0 and inverted == 0,
                        "in_contention": bool((margin[:, col][legal] <= args.margin).any())})
    blocks = []
    flat = weight.reshape(-1)
    for start in range(0, flat.size, args.coord_block):
        trial = flat.copy()
        trial[start:start + args.coord_block] = 0.0
        blocks.append(int((decisions(trial.reshape(weight.shape), bias, hidden, mask) != base).sum()))
    out = {"cache": str(cache), "head": args.head, "decisions": n, "margin": args.margin,
           "checkpoint": str(source),
           "columns": columns,
           "weights_inert_columns": [c["column"] for c in columns if c["weights_inert"]],
           "out_of_contention_columns": [c["column"] for c in columns
                                         if c["weights_inert"] and not c["in_contention"]],
           "columns_never_chosen": [c["column"] for c in columns if c["chosen_count"] == 0],
           "coord_block_flips": {"size": args.coord_block, "flips": blocks,
                                 "blocks_with_no_effect": sum(1 for f in blocks if f == 0),
                                 "blocks": len(blocks)},
           "reading": "weights_inert AND out of contention is ambiguous by construction: it is what "
                      "meaningless noise looks like and also what a hard clamp looks like, and this "
                      "audit cannot separate them from one checkpoint's weights alone -- the "
                      "separator is whether the column ever wins on any board in the corpus or "
                      "changes under a bias write (see wsp_program's held-out outcome gate). A "
                      "column that is never chosen but whose sign flip reorders many boards is a "
                      "strongly signed suppressor, not dead weight: writing to it is a scale change "
                      "with large blast radius, not a repair"}
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
