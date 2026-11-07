"""Can the brain tell, mid-fight, that it is losing? Three arms, one label, no training.

The plan needs a refusal signal: pre-fight planning is only honest if something downstream of it
can say "this board does not end in a win", and the live loop needs it to stop walking into fights
it cannot finish. So measure the predictability of losing directly, on the raw outcome cache, where
the label is not a guess:

    y = 1  iff the terminal result of the fight, played from this decision with the head's own
           chosen action and then the fixed greedy continuation, is a player win.

That is `action_win[board, head_pick]`, which `wsp_program cache-pvm` already computed by rolling
the real environment to a terminal state. Every arm below is scored on the held-out side of the
cache's row split.

Arms, cheapest first:
  hp_material   mean ally hp_ratio minus mean enemy hp_ratio from the cached observation tokens.
                The floor: a plain read of who is healthier. If a learned readout cannot beat this,
                there is nothing in the network to use.
  value_head    the shipped `value` head -- the thing `combat/plan_value.rank_plans` already trusts.
                Recomputed here from the cached observation, so it costs nothing and, more
                importantly, the recomputation is *verified* against the cached action logits: if
                the forward pass does not reproduce the cache, this row would be meaningless.
  readout       closed-form ridge from the cached trunk vector onto the label, thresholded at 0.5.
                Not trained: solved. The best linear "am I losing" reader that representation
                can support.

Reported per arm: AUC (tie-corrected Mann-Whitney, the way `readiness_floor` had to be fixed to
count), base rate, the rate at which the arm flags a decision as losing, and the precision of that
flag. A predictor that cannot separate 0.5 from noise will show it here.

    python tools/wsp_lose_predictor.py --cache <cache.npz>
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


def auc(score, label):
    """Tie-corrected Mann-Whitney of P(score of a win > score of a loss), ties worth half.

    Ranks come from the sorted pool with a left/right searchsorted bracket, so a tie gets the
    average of the ranks it spans. A previous instrument in this repo used argsort-of-argsort,
    which silently assigns no mid-rank to ties and made every arm look worse than chance.
    """
    score = np.asarray(score, dtype=np.float64)
    label = np.asarray(label, dtype=np.int64)
    if not (label == 1).any() or not (label == 0).any():
        raise ValueError("the label has only one class on this side; AUC is undefined")
    pool = np.sort(score)
    lo = np.searchsorted(pool, score, side="left")
    hi = np.searchsorted(pool, score, side="right")
    ranks = (lo + hi + 1) / 2.0                     # 1-based, tied scores share the mean rank
    wins = label == 1
    u = ranks[wins].sum() - wins.sum() * (wins.sum() + 1) / 2.0
    return float(u / (int(wins.sum()) * int((~wins).sum())))


def _selftest_auc():
    checks = {"perfect": 1.0, "inverted": 0.0, "all_tied": 0.5, "half": 0.5}
    lab = np.array([1, 1, 0, 0])
    got = [auc(np.array([4.0, 3.0, 2.0, 1.0]), lab),
           auc(np.array([1.0, 2.0, 3.0, 4.0]), lab),
           auc(np.array([1.0, 1.0, 1.0, 1.0]), np.array([1, 0, 1, 0])),
           auc(np.array([1.0, 1.0, 2.0, 2.0]), np.array([1, 0, 1, 0]))]
    for name, want, have in zip(checks, checks.values(), got):
        if abs(want - have) > 1e-12:
            raise SystemExit("the AUC helper is wrong on the %r case: got %.6f want %.6f"
                             % (name, have, want))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--ridge", type=float, default=1.0)
    ap.add_argument("--checkpoint", default=None,
                    help="defaults to the checkpoint the cache was built against")
    args = ap.parse_args()

    _selftest_auc()
    os.environ.setdefault("DOFUS_FIGHT_BACKEND", "numpy")
    from combat.encounter import backend, model
    from combat.encounter.checkpoint import config_from_payload

    cache = pathlib.Path(args.cache)
    with np.load(cache, allow_pickle=False) as z:
        if str(z.get("cache_kind", "")) != "raw_pvm_outcomes_v1":
            raise SystemExit("need a raw outcome cache (hidden + terminal outcomes); found %r"
                             % str(z.get("cache_kind", "<absent>")))
        hidden = z["hidden"].astype(np.float64)
        mask = z["head_mask"].astype(bool)
        pick = z["head_pick"].astype(int)
        cached_logits = z["head_logits"].astype(np.float64)
        aw = z["action_win"].astype(np.float64)
        obs = z["obs"].astype(np.float64)
        glob = z["glob"].astype(np.float64)
        attention = z["attention"].astype(bool)
        train = z["train_mask"].astype(bool)
        rows = z["row"]
        ckpt_path = pathlib.Path(str(z["checkpoint_path"].item())).resolve()
        ckpt_sha = str(z["checkpoint_sha256"].item())
        cfg = json.loads(str(z["config"].item()))
        head = str(z["head"].item())
    if set(rows[train]) & set(rows[~train]):        raise SystemExit("a corpus row crosses the train/held-out boundary; every arm below would "
                         "be fitted on the fights it claims to predict")
    if not train.any() or not (~train).any():
        raise SystemExit("the cache has an empty train or held-out side")
    digest = hashlib.sha256(ckpt_path.read_bytes()).hexdigest()
    if digest != ckpt_sha:
        raise SystemExit("the cache's checkpoint changed under it (%s); rebuild the cache first"
                         % ckpt_path)

    # The label: did the fight end in a player win when played from here with the head's own pick?
    y = aw[np.arange(len(pick)), pick]
    if not np.isin(y, (0.0, 1.0)).all():
        raise SystemExit("the cached outcome of the chosen action is not 0/1 for every decision; "
                         "the label would be silently dropping draws or illegal picks")
    label = (y == 1.0).astype(np.int64)
    tr, te = np.where(train)[0], np.where(~train)[0]
    n_pos, n_neg = int(label[te].sum()), int((label[te] == 0).sum())

    payload = pickle.loads(ckpt_path.read_bytes())
    # tree_map, not a dict comprehension: the params are nested (a block is a dict of (W, b)
    # tuples), and np.asarray of a (weight, bias) pair is an inhomogeneous-shape error.
    params = backend.tree_map(np.asarray, payload["params"])
    heads = int(payload.get("heads", 4))
    logits = model.forward(params, obs, glob, attention, heads=heads)
    # actor 0 is the player: the cache stored the head's logits for that row only
    replay = np.asarray(logits[head], dtype=np.float64)[:, 0]
    worst = float(np.abs(replay - cached_logits).max())
    if worst > 1e-3:
        raise SystemExit("re-running the forward pass on the cached observation does not reproduce "
                         "the cached %s logits (max |delta| = %.4f); the value-head row would be "
                         "measuring a different network than the one that produced the labels"
                         % (head, worst))

    same = obs[:, :, 0] > 0.5
    alive = obs[:, :, 1] > 0.5
    hp_ratio = obs[:, :, 2]
    ally = np.where(same & alive, hp_ratio, 0.0).sum(1) / np.maximum(same.sum(1), 1)
    enemy = np.where(~same & alive, hp_ratio, 0.0).sum(1) / np.maximum((~same).sum(1), 1)
    arms = {"hp_material": ally - enemy,
            "value_head": np.asarray(logits["value"], dtype=np.float64).reshape(-1)}

    X = hidden - hidden[tr].mean(0)
    yc = label[tr] - label[tr].mean()
    W = np.linalg.solve(X[tr].T @ X[tr] + args.ridge * np.eye(hidden.shape[1]), X[tr].T @ yc)
    arms["readout"] = (X @ W + label[tr].mean())

    out = {"cache": str(cache), "head": head, "checkpoint": str(ckpt_path),
           "forward_replay_max_abs_delta": round(worst, 6),
           "decisions": int(len(label)), "held_out": int(len(te)),
           "held_out_win_rate": round(float(label[te].mean()), 4),
           "held_out_wins": n_pos, "held_out_losses": n_neg,
           "arms": {}}
    for name, score in arms.items():
        s = np.asarray(score, dtype=np.float64)[te]
        p = s >= 0.5 if name == "readout" else s >= float(np.median(s))
        flags = int(p.sum())
        out["arms"][name] = {
            "auc": round(auc(s, label[te]), 4),
            "flag_rate": round(flags / len(te), 4),
            "flag_precision": round(float(label[te][p].mean()), 4) if flags else None,
            "flag_recall_of_losses": round(float((~label[te].astype(bool))[p].mean()), 4) if flags
            else None,
            "brier": round(float(((s - label[te]) ** 2).mean()), 4)}
    out["note"] = ("hp_material and value_head are ranked by AUC only: neither has a natural 0.5 "
                   "threshold, so their flag_rate/precision use each arm's own held-out median as "
                   "the cut and are comparable between arms, not to the readout's 0.5. The number "
                   "that decides whether a refusal signal is buildable is the AUC column.")
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
