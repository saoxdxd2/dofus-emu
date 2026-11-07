"""Is the fight trunk's low effective rank costing decisions, or is rank-6 all the corpus needs?

Reads a raw outcome cache and answers with a truncation curve: project the cached trunk vector onto
its top-k principal directions, fit the closed-form linear readout on the same held-out split used
by the ceiling ladder, and score every arm with the per-decision terminal-win indicator
(mean `action_win[board, chosen]`), so the numbers are directly comparable to the incumbent and the
oracle from `wsp_layer_ceiling`.

    python tools/wsp_rank_curve.py --cache <cache.npz>
"""
from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np


def score(action_win, mask, pick):
    legal = mask.any(axis=1)
    p = np.where(legal, pick, 0)
    return float(action_win[np.arange(len(p)), p][legal].mean()), int(legal.sum())


def ridge_fit(H, Y, lam):
    Xc = H - H.mean(0)
    yc = Y - Y.mean(0)
    A = Xc.T @ Xc + lam * np.eye(H.shape[1])
    W = np.linalg.solve(A, Xc.T @ yc)
    return W, H.mean(0), Y.mean(0)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--lams", default="1e-3,1e-2,1e-1,1,10,100")
    args = ap.parse_args()

    z = np.load(args.cache, allow_pickle=False)
    H = z["hidden"].astype(np.float64)
    mask = z["head_mask"].astype(bool)
    pick = z["head_pick"].astype(int)
    aw = z["action_win"].astype(np.float64)
    obs = z["obs"].astype(np.float64)
    glob = z["glob"].astype(np.float64)
    tm = z["train_mask"].astype(bool)
    n = len(H)
    idx = np.arange(n)
    # `train_mask` is set per corpus row (one row = one sampled encounter, many decisions), so the
    # split is by fight rather than by board. The guard makes that assumption observable: if a row
    # ever appeared on both sides, the readout would be fitted on the fight it is scored against.
    if not tm.any() or not (~tm).any():
        raise SystemExit("the cache has an empty train or held-out side")
    if set(z["row"][tm]) & set(z["row"][~tm]):
        raise SystemExit("a corpus row crosses the train/held-out boundary; this curve would leak")
    tr, te = idx[tm], idx[~tm]

    inc, m = score(aw[te], mask[te], pick[te])
    # oracle: the best legal action's terminal win, per board -- the same quantity the ceiling
    # ladder prints, so the gap reported here is the ladder's gap
    orc = float(np.max(np.where(mask[te], aw[te], -1e9), axis=1)[mask[te].any(1)].mean())
    # random legal action, averaged over seeds: the floor a readout has to beat
    rnd = float(np.mean([score(aw[te], mask[te],
                               np.random.RandomState(s).randint(0, mask.shape[1], len(te)))[0]
                         for s in range(8)]))

    Hc = H - H[tr].mean(0)
    s = np.linalg.svd(Hc[tr], compute_uv=False)
    ev = s ** 2 / (s ** 2).sum()
    pr = float((s ** 2).sum() ** 2 / (s ** 4).sum())

    Y = aw[tr]
    curve = []
    for k in (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96):
        if k > H.shape[1]:
            continue
        Vt = np.linalg.svd(Hc[tr], full_matrices=False)[2][:k].T
        Ztr = Hc[tr] @ Vt
        Zte = Hc[te] @ Vt
        best = None
        for lam in [float(v) for v in args.lams.split(",")]:
            W, hm, ym = ridge_fit(Ztr, Y, lam)
            logits = Zte @ W + ym
            p = np.argmax(np.where(mask[te], logits, -1e9), axis=1)
            sc, _ = score(aw[te], mask[te], p)
            agree = float((p == pick[te]).mean())
            if best is None or sc > best[0]:
                best = (sc, lam, agree)
        curve.append({"k": k, "ridge_terminal_win": round(best[0], 4), "lam": best[1],
                      "agrees_with_incumbent": round(best[2], 4),
                      "var_frac": round(float(ev[:k].sum()), 4)})

    # how much of the *input* is rank-degenerate? obs/glob are the features before the trunk
    fin = np.concatenate([obs[:, 0].reshape(n, -1), glob], axis=1)
    sf = np.linalg.svd(fin[tr] - fin[tr].mean(0), compute_uv=False)
    evf = sf ** 2 / (sf ** 2).sum()

    out = {"cache": str(pathlib.Path(args.cache)), "held_out_boards": m,
           "incumbent": round(inc, 4), "oracle": round(orc, 4), "random": round(rnd, 4),
           "hidden_width": int(H.shape[1]),
           "participation_ratio": round(pr, 2),
           "input_participation_ratio": round(float((sf ** 2).sum() ** 2 / (sf ** 4).sum()), 2),
           "input_top10_var_frac": round(float(evf[:10].sum()), 4),
           "curve": curve,
           "oracle_minus_incumbent": round(orc - inc, 4)}
    print(json.dumps(out, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
