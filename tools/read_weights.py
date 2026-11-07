"""Read the weights. Not a summary of what was trained: the actual arrays on disk.

A checkpoint that is only ever reloaded inside a forward pass is a claim about a file. This opens it
and prints the parameter tree -- every leaf's path, shape, dtype and norm -- and then the part that
matters for task #142: which *input features* the encoder puts weight on. `enc1` embeds the
81-number actor token, whose layout is fixed and presence-gated:

    [0:13)                 the actor's own 13 numbers (side, alive, hp/ap/mp, distance, initiative,
                           power, five resistances)
    [13 + 17*j : 13+17j+9) the shipped nine numbers of spell slot j (AP, range, damage, heal,
                           element, ally flag, damage/AP, heal/AP)
    [13+17j+9 : 13+17j+14) the five mechanic numbers (kill, push, AP delta, MP delta, duration)
    [13+17j+14: 13+17j+17) the three summon numbers (bodies, HP each, damage each)

and a 9-number spell token is possible too (older corpora, 49 wide, then 69 wide before summons), so
the widths are read from the artifact rather than assumed: `feat_dim` tells you the width and
`--slots` says how many spell heads it was trained against.

`--compare` is the arm-level reading: same init, same data, one arm blind to the mechanic numbers.
`--init-seed` rebuilds the actual `model.init_params` reference and measures drift from it, which is
what proves that a blind arm's zeroed input rows stayed at initialization instead of merely having a
similar norm to the visible arm's trained rows.

    python tools/read_weights.py <checkpoint.pkl> [--slots 4]
    python tools/read_weights.py <visible.pkl> --compare <blind.pkl>
    python tools/read_weights.py <blind.pkl> --init-seed 1
"""
from __future__ import annotations

import argparse
import json
import pathlib
import pickle
import sys

import numpy as np

BRAIN = pathlib.Path(__file__).resolve().parent.parent / "dofus-network"
sys.path.insert(0, str(BRAIN))


def leaves(tree, prefix=()):
    if isinstance(tree, dict):
        for k, v in tree.items():
            yield from leaves(v, prefix + (str(k),))
    elif isinstance(tree, (list, tuple)):
        for i, v in enumerate(tree):
            yield from leaves(v, prefix + (str(i),))
    else:
        yield "/".join(prefix), tree


def input_blocks(feat_dim: int, slots: int):
    """(label, start, stop) inside the actor token, for every layout this project has shipped."""
    base = 13
    if feat_dim < base:
        raise SystemExit("feat_dim %d is shorter than the actor's own 13 numbers" % feat_dim)
    span = (feat_dim - base) // max(slots, 1)
    yield ("actor_base", 0, base)
    for j in range(slots):
        start = base + j * span
        yield ("slot%d_shipped" % j, start, start + 9)
        if span >= 14:
            yield ("slot%d_mechanics" % j, start + 9, start + 14)
        if span >= 17:
            yield ("slot%d_summons" % j, start + 14, start + 17)


def describe(path: str, slots: int, detailed: bool):
    art = pickle.loads(pathlib.Path(path).read_bytes())
    params = art["params"]
    flat = list(leaves(params))
    total = int(sum(np.asarray(v).size for _, v in flat))
    info = {
        "checkpoint": pathlib.Path(path).name,
        "bytes": pathlib.Path(path).stat().st_size,
        "leaves": len(flat),
        "parameters": total,
        "hidden": art.get("hidden"), "layers": art.get("layers"),
        "feat_dim": art.get("feat_dim"), "global_dim": art.get("global_dim"),
        "blind": art.get("blind"), "config": art.get("config"),
        "iters": len(art.get("metrics") or []),
        "data": art.get("data"),
        "leaf_shapes": [{"name": name, "shape": list(np.asarray(v).shape),
                         "dtype": str(np.asarray(v).dtype),
                         "l2": round(float(np.linalg.norm(np.asarray(v))), 6),
                         "abs_max": round(float(np.abs(np.asarray(v)).max()), 6)}
                        for name, v in flat],
    }
    enc1 = params.get("enc1")
    weight = None
    if enc1 is not None:
        for item in enc1:
            arr = np.asarray(item)
            if arr.ndim == 2:
                weight = arr
                break
    if weight is not None and art.get("feat_dim"):
        in_dim = weight.shape[1] if weight.shape[1] == art["feat_dim"] else weight.shape[0]
        if in_dim != art["feat_dim"]:
            raise SystemExit("enc1 has no axis of length feat_dim (%d); shape %s"
                             % (art["feat_dim"], weight.shape))
        w = weight if weight.shape[1] == in_dim else weight.T       # [hidden, feat_dim]
        norms = {}
        for label, lo, hi in input_blocks(art["feat_dim"], slots):
            block = w[:, lo:hi]
            norms[label] = {"l2": round(float(np.linalg.norm(block)), 6),
                            "abs_max": round(float(np.abs(block).max()), 6)}
        if detailed:
            info["encoder_input_norms"] = norms
        info["mechanic_block_l2"] = round(sum(v["l2"] ** 2 for k, v in norms.items()
                                              if k.endswith("_mechanics")) ** 0.5, 6)
        info["summon_block_l2"] = round(sum(v["l2"] ** 2 for k, v in norms.items()
                                            if k.endswith("_summons")) ** 0.5, 6)
        info["shipped_block_l2"] = round(sum(v["l2"] ** 2 for k, v in norms.items()
                                             if k.endswith("_shipped")) ** 0.5, 6)
    return art, info, weight


def initial_comparison(art, weight, seed: int, slots: int):
    import jax
    from combat.encounter import model
    from combat.encounter.env import Config

    cfg = Config(**art["config"])
    initial = model.init_params(jax.random.PRNGKey(seed), art["feat_dim"],
                                art["global_dim"], cfg, hidden=art["hidden"],
                                layers=art["layers"])
    reference = np.asarray(initial["enc1"][0])
    current = np.asarray(weight)
    if current.shape != reference.shape:
        raise SystemExit("checkpoint enc1 shape %s does not match its initializer %s"
                         % (current.shape, reference.shape))
    diff = current - reference
    blocks = {}
    for label, lo, hi in input_blocks(art["feat_dim"], slots):
        delta = diff[lo:hi]
        blocks[label] = {"l2": round(float(np.linalg.norm(delta)), 9),
                         "abs_max": round(float(np.abs(delta).max()), 9)}
    if art["global_dim"] >= 13:
        glob_diff = (np.asarray(art["params"]["glob"][0])
                     - np.asarray(initial["glob"][0]))
        delta = glob_diff[9:13]
        blocks["pool_globals"] = {"l2": round(float(np.linalg.norm(delta)), 9),
                                  "abs_max": round(float(np.abs(delta).max()), 9)}
    blind = set(art.get("blind") or ())
    labels = [label for label in blocks
              if ("mechanics" in blind and label.endswith("_mechanics"))
              or ("summons" in blind and label.endswith("_summons"))
              or ("pool" in blind and label == "pool_globals")]
    return {"seed": seed,
            "encoder_l2_from_init": round(float(np.linalg.norm(diff)), 9),
            "blocks": blocks,
            "blinded_blocks": labels,
            "blinded_input_l2_from_init": round(float(np.sqrt(sum(
                blocks[label]["l2"] ** 2 for label in labels))), 9)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("checkpoint")
    ap.add_argument("--slots", type=int, default=4)
    ap.add_argument("--blocks", action="store_true", help="per-input-block encoder norms")
    ap.add_argument("--compare", default=None,
                    help="second checkpoint to diff against, same init (blind arm)")
    ap.add_argument("--init-seed", type=int, default=None,
                    help="rebuild model.init_params at this seed and measure drift from it")
    ap.add_argument("--out", default=None, help="optional JSON dump of the report")
    args = ap.parse_args(argv)
    art, info, weight = describe(args.checkpoint, args.slots, args.blocks)
    out = {"checkpoint": info}
    if args.init_seed is not None:
        out["initial_comparison"] = initial_comparison(art, weight, args.init_seed, args.slots)
    if args.compare:
        other, other_info, other_weight = describe(args.compare, args.slots, args.blocks)
        if weight is None or other_weight is None or weight.shape != other_weight.shape:
            raise SystemExit("the two encoders are not directly comparable: %s vs %s"
                             % (None if weight is None else weight.shape,
                                None if other_weight is None else other_weight.shape))
        diff = weight - other_weight
        out["comparison"] = {
            "other": other_info["checkpoint"],
            "encoder_l2_distance": round(float(np.linalg.norm(diff)), 6),
            "encoder_max_abs_change": round(float(np.abs(diff).max()), 6),
            "moved_leaves": [
                {"name": a, "l2": round(float(np.linalg.norm(np.asarray(va) - np.asarray(vb))), 6)}
                for (a, va), (b, vb) in zip(leaves(art["params"]), leaves(other["params"]))
                if a == b and np.asarray(va).shape == np.asarray(vb).shape
                and np.linalg.norm(np.asarray(va) - np.asarray(vb)) > 1e-6],
            "mechanic_block_l2": {"visible": info.get("mechanic_block_l2"),
                                  "blind": other_info.get("mechanic_block_l2")},
            "summon_block_l2": {"visible": info.get("summon_block_l2"),
                                "blind": other_info.get("summon_block_l2")},
            "blind": {"visible": info.get("blind"), "blind": other_info.get("blind")},
        }
    print(json.dumps(out, indent=1, sort_keys=True))
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
