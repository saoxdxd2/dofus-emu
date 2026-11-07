"""Is the fight net's deficit in the head's weights, or in what the layers hand to the head?

The claim this tests, in the user's words: "the problem is lack of training while the weights are
nearly perfect but there are problems in the layers". That is a falsifiable statement, and the
artifacts already make it measurable. `wsp_program cache-pvm` stores, for every player decision,

    hidden      the actor token the trunk feeds the action heads (the trunk output, unmodified)
    action_win  the terminal win indicator (0/1) of playing *each* legal action and finishing the
                fight with a fixed greedy continuation
    head_pick   the action the shipped head actually chose

so four numbers bound the question:

  incumbent  mean action_win[head_pick]        what the shipped weights achieve
  fitted     mean action_win[argmax of the best *linear* readout of hidden]
             -- solved in closed form on the train rows only, evaluated on rows never fitted
  forest     the same with a random forest: still one readout of the same vector, but nonlinear,
             so it asks "is the right answer present in this representation at all?"
  oracle     mean max(action_win)              perfect one-decision play, given this representation

Reading the ladder: `fitted` near `incumbent` while `oracle` is far above means more coordinated
weight writes on this head buy nothing -- the trunk does not encode the better action in a linearly
reachable way. `forest` near `oracle` while `fitted` stays low means the information *is* in the
vector but the head cannot express it: a representation/layer problem, not a weight problem. `forest`
also low means the fight was already decided by something the state does not carry -- which is what
training on more data cannot fix either, and needs the simulation to change.

Unit warning, stated because it misled an earlier round of this work: every number here is
per-decision with a fixed continuation, so it is NOT a fight win rate. The end-to-end instrument for
that is `wsp_fight --harness pvm`, and a gain here that never shows up there is a proxy gain.

    python tools/wsp_layer_ceiling.py --cache <cache.npz> [--splits 3]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

import numpy as np  # noqa: E402


def _readme(cache_path, z):
    if str(z.get("cache_kind", "")) != "raw_pvm_outcomes_v1":
        raise SystemExit("%s is not a raw outcome cache (cache_kind=%r); rebuild it with "
                         "`wsp_program cache-pvm` -- the beam-imitation cache cannot answer this "
                         "question because it has no terminal outcomes"
                         % (cache_path, str(z.get("cache_kind", "<absent>"))))
    if str(z.get("head", "")) != "spell":
        raise SystemExit("this probe reads the spell head; cache head is %r" % str(z["head"]))


def linear_fit(hidden, wins, mask, train, lam):
    """Closed-form ridge from the trunk vector to the value of each action, fitted on `train`.

    One least-squares problem per action column, solved together: W = (X^T X + lam*I)^-1 X^T Y, on
    the rows this is allowed to see. Masked-away columns are left unfitted: the head can only be
    scored on what it is permitted to pick.
    """
    x = hidden[train]
    y = wins[train]
    gram = x.T @ x + lam * np.eye(x.shape[1])
    weight = np.linalg.solve(gram, x.T @ y)
    return (hidden @ weight).astype(np.float64)


def forest_fit(hidden, wins, mask, train, seed):
    from sklearn.ensemble import RandomForestRegressor

    model = RandomForestRegressor(n_estimators=200, min_samples_leaf=8, random_state=seed,
                                 n_jobs=1)
    model.fit(hidden[train], wins[train])
    return model.predict(hidden)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--lambdas", default="1e-2,1,100,1e4")
    ap.add_argument("--forest", action="store_true",
                    help="also fit a nonlinear readout of the same vector (needs sklearn)")
    args = ap.parse_args(argv)

    path = pathlib.Path(args.cache)
    with np.load(path, allow_pickle=False) as z:
        data = {k: z[k] for k in z.files}
    _readme(path, data)
    hidden = data["hidden"].astype(np.float64)
    wins = data["action_win"].astype(np.float64)
    mask = data["head_mask"].astype(bool)
    pick = data["head_pick"].astype(int)
    train = data["train_mask"].astype(bool)
    rows = data["row"].astype(int)
    if set(rows[train]) & set(rows[~train]):
        raise SystemExit("a corpus row appears on both sides; the split would leak")
    n = len(pick)
    legal = np.where(mask, wins, -1.0)
    out = {"cache": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
           "decisions": int(n), "train": int(train.sum()), "heldout": int((~train).sum()),
           "unit": "per-decision terminal-win indicator with a fixed greedy continuation "
                   "(NOT a fight win rate)",
           "incumbent_train": float(wins[train, pick[train]].mean()),
           "incumbent_heldout": float(wins[~train, pick[~train]].mean()),
           "oracle_train": float(legal[train].max(axis=1).mean()),
           "oracle_heldout": float(legal[~train].max(axis=1).mean()),
           "linear": []}
    for lam in [float(x) for x in args.lambdas.split(",")]:
        logits = linear_fit(hidden, wins, mask, train, lam)
        cand = np.argmax(np.where(mask, logits, -1e9), axis=1)
        score = wins[np.arange(n), cand]
        out["linear"].append({
            "lambda": lam,
            "train": float(score[train].mean()), "heldout": float(score[~train].mean()),
            "changed_heldout": int((cand[~train] != pick[~train]).sum()),
            "share_of_oracle_gap_closed": round(float(
                (score[~train].mean() - out["incumbent_heldout"])
                / max(out["oracle_heldout"] - out["incumbent_heldout"], 1e-9)), 4)})
    if args.forest:
        logits = forest_fit(hidden, wins, mask, train, seed=7)
        cand = np.argmax(np.where(mask, logits, -1e9), axis=1)
        score = wins[np.arange(n), cand]
        out["forest"] = {"train": float(score[train].mean()),
                         "heldout": float(score[~train].mean()),
                         "share_of_oracle_gap_closed": round(float(
                             (score[~train].mean() - out["incumbent_heldout"])
                             / max(out["oracle_heldout"] - out["incumbent_heldout"], 1e-9)), 4)}
    verdict = []
    best_linear = max(out["linear"], key=lambda r: r["heldout"])
    if best_linear["heldout"] <= out["incumbent_heldout"] + 1e-9:
        verdict.append("no linear readout of this trunk beats the shipped head on held-out "
                       "decisions: the deficit is not reachable by writing this head, however "
                       "carefully")
    elif best_linear["share_of_oracle_gap_closed"] < 0.5:
        verdict.append("the best linear readout closes under half the held-out gap to perfect "
                       "one-decision play: the better action is not linearly expressible from "
                       "what the layers hand over")
    else:
        verdict.append("a linear readout closes at least half the held-out gap: this head is "
                       "under-trained for its own representation, and weight-space writes have "
                       "room here")
    if "forest" in out:
        if out["forest"]["share_of_oracle_gap_closed"] > best_linear["share_of_oracle_gap_closed"] + 0.1:
            verdict.append("but a nonlinear readout of the SAME vector does better: the information "
                           "is in the representation and the linear head cannot use it -- a layers "
                           "problem, not a weights problem")
        else:
            verdict.append("and a nonlinear readout of the same vector does not do better: the "
                           "decision is not recoverable from this representation at all")
    out["verdict"] = verdict
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
