"""Write the value head by solving it, the way WSP writes the action heads -- and gate the write.

Why this is the one place a weight edit should pay. On the 13,002-decision boss outcome cache:

* the action head is at its ceiling. `wsp_noise_audit` finds no inert column and no coordinate
  block without effect; no closed-form readout of the cached trunk vector beats the head that is
  already there (0.2259 against the incumbent's 0.2654 per-decision terminal-win); the
  per-decision oracle is +0.0119 above the incumbent, and the best measured weight edit in this
  line has been +/-0.003.
* the value head is not at its ceiling. `wsp_lose_predictor` scores "will this fight end in a
  player win, played from here" at AUC 0.7816 held out -- *below* the 0.7986 you get by
  subtracting the enemies' mean HP ratio from the allies' -- while a closed-form linear readout of
  the actor vector in the same forward pass reaches 0.9009. The trunk carries the outcome better
  than the head reports it.

`value2` is a linear read of one activation (`model.value_readout` returns it).
This tool fits that layer by supervised ridge regression, not direct numerical WSP diagnosis.
It writes the fitted arrays and gates on held-out discrimination, not policy win-rate improvement.
Two witnesses check the cached activation and the written artifact:

  before  `model.forward` on the cached observation must reproduce the cache's own head logits, so
          the activation being solved is the one the labelled network produced.
  after   the written pickle is re-read and replayed: its value must match the solved scores, and
          the parameter leaves that differ from the source must be exactly `value2`.

    python tools/wsp_program_value.py --cache <cache.npz> --report-only
    python tools/wsp_program_value.py --cache <cache.npz> --out value_wsp.pkl

The label is a terminal outcome from `wsp_program cache-pvm`: the fight was rolled to an end in
the real environment, so "verified victory" is the currency here, not a mirror score.
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


def flat(tree):
    """Every leaf of a params tree, concatenated -- so 'what changed' is one comparison."""
    if isinstance(tree, dict):
        return np.concatenate([flat(v) for v in sorted(tree.values(), key=repr)])
    if isinstance(tree, (list, tuple)):
        return np.concatenate([flat(v) for v in tree])
    return np.asarray(tree, np.float64).ravel()


def auc(score, label):
    """Tie-corrected Mann-Whitney over the pooled sample (see `wsp_lose_predictor.auc`)."""
    score = np.asarray(score, dtype=np.float64)
    label = np.asarray(label, dtype=np.int64)
    wins = label == 1
    n_pos, n_neg = int(wins.sum()), int((~wins).sum())
    if n_pos == 0 or n_neg == 0:
        raise ValueError("the label has only one class on this side; AUC is undefined")
    pool = np.sort(score)
    lo = np.searchsorted(pool, score, side="left")
    hi = np.searchsorted(pool, score, side="right")
    ranks = (lo + hi + 1) / 2.0
    u = ranks[wins].sum() - n_pos * (n_pos + 1) / 2.0
    return float(u / (n_pos * n_neg))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--ridge", type=float, default=1.0)
    args = ap.parse_args()

    os.environ.setdefault("DOFUS_FIGHT_BACKEND", "numpy")
    from combat.encounter import backend, model

    cache = pathlib.Path(args.cache)
    with np.load(cache, allow_pickle=False) as z:
        if str(z.get("cache_kind", "")) != "raw_pvm_outcomes_v1":
            raise SystemExit("need a raw outcome cache with terminal outcomes; found %r"
                             % str(z.get("cache_kind", "<absent>")))
        pick = z["head_pick"].astype(int)
        aw = z["action_win"].astype(np.float64)
        obs = z["obs"].astype(np.float32)
        glob = z["glob"].astype(np.float32)
        attention = z["attention"].astype(bool)
        train = z["train_mask"].astype(bool)
        rows = z["row"]
        cached_head = z["head_logits"].astype(np.float64)
        head = str(z["head"].item())
        ckpt = pathlib.Path(str(z["checkpoint_path"].item())).resolve()
        want_sha = str(z["checkpoint_sha256"].item())
    if set(rows[train]) & set(rows[~train]):
        raise SystemExit("a corpus row crosses the train/held-out boundary; the solved readout "
                         "would be fitted on the fights it claims to predict")
    if not train.any() or not (~train).any():
        raise SystemExit("the cache has an empty train or held-out side")
    if hashlib.sha256(ckpt.read_bytes()).hexdigest() != want_sha:
        raise SystemExit("the cache's checkpoint changed under it: %s" % ckpt)

    label = (aw[np.arange(len(pick)), pick] == 1.0).astype(np.int64)
    tr, te = train, ~train

    payload = pickle.loads(ckpt.read_bytes())
    source_params = backend.tree_map(np.asarray, payload["params"])
    heads = int(payload.get("heads", 4))

    def value_and_activation(p):
        """The value head's estimate, the activation `value2` reads, and the head-replay delta."""
        actors, global_token = model.trunk(p, obs, glob, attention, heads=heads)
        count = np.maximum(np.sum(attention, axis=1, keepdims=True), 1.0)
        value, act = model.value_readout(p, actors, global_token, attention, count)
        replay = np.asarray(model.forward(p, obs, glob, attention, heads=heads)[head],
                            np.float64)[:, 0]
        delta = float(np.abs(replay - cached_head).max())
        if delta > 1e-3:
            raise SystemExit("`model.forward` on the cached observation does not reproduce the "
                             "cached %s logits (max |delta| = %.6f): the checkpoint and the cache "
                             "do not belong to each other" % (head, delta))
        return np.asarray(value, np.float64), np.asarray(act, np.float64), delta

    base_value, z_act, replay_delta = value_and_activation(source_params)

    # Solve the last layer against +/-1 so the tanh output lands on the label's own scale.
    target = (2.0 * label - 1.0)[:, None]
    mu = z_act[tr].mean(0)
    W = np.linalg.solve((z_act[tr] - mu).T @ (z_act[tr] - mu) + args.ridge * np.eye(z_act.shape[1]),
                        (z_act[tr] - mu).T @ (target[tr] - target[tr].mean(0)))
    b = (target[tr].mean(0) - mu @ W).ravel()
    edited = dict(source_params)
    edited["value2"] = (W, b)
    new_value, _, _ = value_and_activation(edited)

    before_auc = auc(base_value[te], label[te])
    after_auc = auc(new_value[te], label[te])
    # Brier only on a probability scale. The head is `tanh`, so the solved arm is trained against
    # +/-1 and its probability is (v+1)/2; the trained arm was fitted to bootstrapped advantages on
    # a comparable but not identical scale, so its raw squared error against a 0/1 label is not a
    # number with a meaning (printing it once produced 1.036, larger than the worst useful
    # constant). Both are therefore reported after the same (v+1)/2 map, against the constant
    # base-rate predictor as the floor.
    rate = float(label[te].mean())
    base_floor = rate * (1.0 - rate)

    def prob(v):
        return np.clip((np.asarray(v, np.float64) + 1.0) / 2.0, 0.0, 1.0)

    report = {"cache": str(cache), "source_checkpoint": str(ckpt),
              "head_replay_max_abs_delta": round(replay_delta, 8),
              "decisions": int(len(label)), "train": int(tr.sum()), "held_out": int(te.sum()),
              "held_out_win_rate": round(rate, 4),
              "ridge": args.ridge,
              "value_head_auc_before": round(before_auc, 4),
              "value_head_auc_after": round(after_auc, 4),
              "auc_gain": round(after_auc - before_auc, 4),
              "brier_after_on_probability_scale": round(float(((prob(new_value[te]) - label[te])
                                                               ** 2).mean()), 4),
              "brier_before_on_probability_scale": round(float(((prob(base_value[te]) - label[te])
                                                                ** 2).mean()), 4),
              "brier_constant_base_rate": round(base_floor, 4),
              "reference_arms_from_wsp_lose_predictor": {
                  "hp_material_floor_auc": 0.7986, "actor_vector_readout_auc": 0.9009},
              "label": "terminal player win of the fight played from this decision with the head's "
                       "own chosen spell, rolled to an end by combat.encounter.env"}

    if after_auc <= before_auc:
        print(json.dumps(dict(report, verdict="refused: the solved readout does not discriminate "
                                             "better held out than the trained value head"),
                         indent=1, sort_keys=True))
        return 1
    if args.report_only:
        print(json.dumps(dict(report, verdict="report only, nothing written"), indent=1,
                         sort_keys=True))
        return 0
    if not args.out:
        raise SystemExit("--out is required unless --report-only")

    out_path = pathlib.Path(args.out)
    written = pickle.loads(ckpt.read_bytes())          # fresh copy of the source artifact
    written["params"]["value2"] = (np.asarray(W, np.float32), np.asarray(b, np.float32))
    written["weight_space_program"] = {"head": "value2", "method": "closed_form_last_layer",
                                       "ridge": args.ridge, "cache": str(cache),
                                       "cache_sha256": hashlib.sha256(cache.read_bytes()).hexdigest(),
                                       "held_out_auc_before": round(before_auc, 4),
                                       "held_out_auc_after": round(after_auc, 4)}
    out_path.write_bytes(pickle.dumps(written))

    # Verify the artifact, not the intention.
    back = pickle.loads(out_path.read_bytes())
    back_params = backend.tree_map(np.asarray, back["params"])
    verify, _, _ = value_and_activation(back_params)
    drift = float(np.abs(verify - new_value).max())
    changed = sorted(k for k in back["params"]
                     if not np.array_equal(flat(back["params"][k]), flat(source_params[k])))
    print(json.dumps(dict(report, wrote=str(out_path),
                          artifact_value_matches_solved_max_abs_delta=round(drift, 8),
                          changed_params=changed,
                          artifact_sha256=hashlib.sha256(out_path.read_bytes()).hexdigest(),
                          verdict="written"), indent=1, sort_keys=True))
    if drift > 1e-5:
        raise SystemExit("the written artifact does not reproduce the solved value (max |delta| = "
                         "%g); refusing to call it an edit" % drift)
    if changed != ["value2"]:
        raise SystemExit("the artifact differs from the source in %r, not only value2" % changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
