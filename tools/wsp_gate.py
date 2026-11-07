"""Gate-zero harness for weight-space programming on the fight policy.

Why a harness and not an edit: the published weight-space-programming result is a *failure-mode
repair* on a degenerate baseline, and the sharpest independent objection is that any perturbation
of the right size might look like an improvement. So nothing is edited until three things are true,
and this file measures the first two:

1. POWER      -- the eval must be able to see the size of effect claimed. Boards are the unit, the
                  per-board Spearman correlation between the model's value and the solver's own plan
                  score is the statistic, and the harness reports its standard error.
2. PAIRING    -- every later arm (no-op, random-sign, proposed edit) is scored on the *same* boards
                  in the same order, so comparisons are paired per board rather than marginal.
3. SPLIT      -- half the boards are `fit` and half are `holdout`, assigned by board index. An edit
                  that only helps the half it was tuned on is not an edit that works.

Run:
    python tools/wsp_gate.py score <out.json> [--rows 512] [--seed 7] [--checkpoint path]
    python tools/wsp_gate.py compare <baseline.json> <candidate.json>     # paired, both arms
    python tools/wsp_gate.py control <baseline.json> [--edits 32] [--seed 1]
                                             # random-sign perturbation, same coordinate count

The default array backend is the one an installed copy uses (`DOFUS_FIGHT_BACKEND=numpy`), so the
number being defended is the shipped path's, not the training box's.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import pickle
import random
import statistics
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
os.environ.setdefault("DOFUS_FIGHT_BACKEND", "numpy")
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

import numpy as np  # noqa: E402


def _ranks(values):
    order = np.argsort(values, kind="stable")
    out = np.empty(len(values), dtype=float)
    out[order] = np.arange(len(values), dtype=float)
    return out


def spearman(a, b):
    if len(a) < 3 or float(np.std(a)) == 0.0 or float(np.std(b)) == 0.0:
        return None
    ra, rb = _ranks(a) - np.mean(_ranks(a)), _ranks(b) - np.mean(_ranks(b))
    denom = (float(np.sqrt((ra ** 2).sum())) * float(np.sqrt((rb ** 2).sum())))
    return None if denom == 0.0 else float((ra * rb).sum() / denom)


def collect(rows, seed, checkpoint):
    """Score every usable board once, in a fixed order, and keep the params for later edits."""
    from combat import plan_benchmark as pb
    from combat.battle_solver import BattleSolver
    from combat.encounter.backend import BACKEND
    from combat.encounter.checkpoint import config_from_payload
    from combat.plan_value import score_plan

    payload = pickle.loads(pathlib.Path(checkpoint).read_bytes())
    params = payload["params"]
    cfg = config_from_payload(payload)
    data = np.load(str(pb.DEFAULT_DATA)) if not hasattr(pb, "DEFAULT_DATA") else np.load(pb.DEFAULT_DATA)
    cells = pb.load_map(pathlib.Path(pb.DEFAULT_MAP))
    rng = random.Random(seed)
    solver = BattleSolver(beam_width=16, max_actions=3)

    sample = rng.sample(range(int(data["hp"].shape[0])), min(rows, int(data["hp"].shape[0])))
    boards = []
    for row in sample:
        board = pb.make_board(data, row, cells, rng)
        if board is None:
            continue
        candidates = solver.plan_candidates(board, 8)
        if len(candidates) < 2:
            continue
        values = np.array([float(score_plan(params, board, plan, cfg)) for plan in candidates])
        scores = np.array([float(plan.score) for plan in candidates])
        rho = spearman(values, scores)
        boards.append({
            "row": int(row),
            "rho": rho,
            "agree": bool(int(np.argmax(values)) == int(np.argmax(scores))),
            "regret": float(scores.max() - scores[int(np.argmax(values))]),
            "tie": bool(float(np.std(scores)) == 0.0),
        })
    return {"backend": BACKEND, "seed": seed, "requested_rows": rows,
            "checkpoint": str(checkpoint), "boards": boards}


def summarise(report):
    boards = report["boards"]
    measurable = [b["rho"] for b in boards if b["rho"] is not None]
    n = len(measurable)
    mean = statistics.fmean(measurable) if measurable else float("nan")
    se = statistics.stdev(measurable) / (n ** 0.5) if n > 1 else float("nan")
    halves = {}
    for name, part in (("fit", boards[0::2]), ("holdout", boards[1::2])):
        rho = [b["rho"] for b in part if b["rho"] is not None]
        halves[name] = {"n": len(rho),
                        "mean_rho": round(statistics.fmean(rho), 4) if rho else None,
                        "agree": round(sum(1 for b in part if b["agree"]) / max(1, len(part)), 4),
                        "mean_regret": round(statistics.fmean([b["regret"] for b in part]), 3) if part else None}
    return {
        "backend": report["backend"],
        "boards_total": len(boards),
        "boards_measurable": n,
        "degenerate_untied": sum(1 for b in boards if b["tie"]),
        "mean_rho": round(mean, 4),
        "standard_error": round(se, 4) if n > 1 else None,
        "negative_boards": sum(1 for b in boards if b["rho"] is not None and b["rho"] < 0),
        "agree_rate": round(sum(1 for b in boards if b["agree"]) / max(1, len(boards)), 4),
        "random_baseline_agree": round(1.0 / 8.0, 4),
        "halves": halves,
    }


def paired(baseline, candidate):
    """Per-board difference with a bootstrap CI, and the power statement the edit must clear."""
    a = {b["row"]: b for b in baseline["boards"]}
    b = {b["row"]: b for b in candidate["boards"]}
    shared = sorted(set(a) & set(b))
    diffs = [b[r]["rho"] - a[r]["rho"] for r in shared
             if a[r]["rho"] is not None and b[r]["rho"] is not None]
    if not diffs:
        return {"error": "no board has a measurable correlation in both arms"}
    mean = statistics.fmean(diffs)
    draws = []
    rng = random.Random(0)
    for _ in range(4000):
        draws.append(statistics.fmean(rng.choices(diffs, k=len(diffs))))
    draws.sort()
    agree_delta = (sum(1 for r in shared if b[r]["agree"]) - sum(1 for r in shared if a[r]["agree"]))
    regret_delta = statistics.fmean([b[r]["regret"] - a[r]["regret"] for r in shared])
    low = draws[int(0.025 * len(draws))]
    high = draws[int(0.975 * len(draws))]
    return {
        "boards_paired": len(diffs),
        "delta_rho_candidate_minus_baseline": round(mean, 4),
        "bootstrap_ci95": [round(low, 4), round(high, 4)],
        "sign_test_p_two_sided": None,
        "agree_wins_minus_losses": agree_delta,
        "mean_regret_delta": round(regret_delta, 3),
        # A candidate arm is only interesting if its CI excludes zero AND it beats the control band.
        "ci_excludes_zero": bool(low > 0.0 or high < 0.0),
        "verdict": ("no detectable change (CI covers 0)" if low <= 0.0 <= high else
                    "improvement, CI excludes 0" if low > 0.0 else
                    "REGRESSION, CI excludes 0"),
    }


def control(baseline_path, edits, seed, checkpoint):
    """Gate zero: perturb the same NUMBER of coordinates, signs at random, and score again.

    If this arm looks like a real edit, the eval cannot distinguish them and the whole route is
    noise-following -- which is the specific criticism levelled at the published result.
    """
    path = pathlib.Path(baseline_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    original = pathlib.Path(report["checkpoint"] if path.exists() else checkpoint)
    rng = random.Random(seed)
    flat, holders = _load_for_perturbation(str(original))
    touched = rng.sample(range(len(flat)), min(edits, len(flat)))
    for index in touched:
        flat[index] = -flat[index]
    edited = _write_perturbed(original, holders, touched, flat, path.with_suffix(".control%d.pkl" % seed))
    # The control has to be able to fail: prove the perturbed file differs from the original in
    # exactly the coordinates asked for, or the arm measures nothing.
    before = np.concatenate([np.asarray(a).reshape(-1) for _, a in _leaf_arrays(str(original))])
    after = np.concatenate([np.asarray(a).reshape(-1) for _, a in _leaf_arrays(edited)])
    differing = int((before != after).sum())
    candidate = collect(report["requested_rows"], report["seed"], edited)
    arm_path = path.with_name("%s.control%d.json" % (path.stem, seed))
    arm_path.write_text(json.dumps(candidate), encoding="utf-8")
    return (summarise(candidate),
            {"edits": len(touched), "coordinates_differing": differing,
             "edit_machinery_verified": differing == len(touched),
             "arm_report": str(arm_path), "target": str(edited)},
            paired(report, candidate))


def _leaf_arrays(checkpoint):
    """(path, ndarray) for every parameter leaf, in a deterministic order."""
    found = []

    def walk(node, path=()):
        if isinstance(node, dict):
            for key in sorted(node):
                walk(node[key], path + (key,))
        elif isinstance(node, (list, tuple)):
            for position, value in enumerate(node):
                walk(value, path + (position,))
        else:
            found.append((path, np.asarray(node)))

    walk(pickle.loads(pathlib.Path(checkpoint).read_bytes())["params"])
    return found


def _load_for_perturbation(checkpoint):
    """Flatten the leaf arrays into one list with per-leaf spans, so an edit count is comparable."""
    leaves = []
    holders = []
    for path, array in _leaf_arrays(checkpoint):
        holders.append({"path": path, "shape": array.shape, "dtype": str(array.dtype),
                        "start": len(leaves)})
        leaves.extend(array.reshape(-1).tolist())
    return leaves, holders


def _write_perturbed(source, holders, touched, flat, destination):
    params = pickle.loads(pathlib.Path(source).read_bytes())["params"]
    span_of = {id(holder): int(np.prod(holder["shape"])) for holder in holders}
    lookup = {}
    for holder in holders:
        for index in range(holder["start"], holder["start"] + span_of[id(holder)]):
            lookup[index] = holder
    changed = {}
    for index in touched:
        holder = lookup[index]
        key = id(holder)
        changed.setdefault(key, (holder, set()))[1].add(index - holder["start"])
    for key, (holder, offsets) in changed.items():
        block = np.array(flat[holder["start"]:holder["start"] + span_of[key]], dtype=np.float32)
        params = _replace(params, holder["path"], block.reshape(holder["shape"]))
    payload = pickle.loads(pathlib.Path(source).read_bytes())
    payload["params"] = params
    pathlib.Path(destination).write_bytes(pickle.dumps(payload))
    return str(destination)


def _replace(node, path, value):
    """Return a copy of the params tree with one leaf replaced.

    The tree mixes dicts, lists *and* tuples (jax pytrees do), so assignment in place is not
    possible for every leaf; rebuilding the spine is.
    """
    if not path:
        return value
    step, rest = path[0], path[1:]
    if isinstance(node, dict):
        out = dict(node)
        out[step] = _replace(node[step], rest, value)
        return out
    if isinstance(node, (list, tuple)):
        items = list(node)
        items[step] = _replace(items[step], rest, value)
        return type(node)(items)
    raise TypeError("cannot descend into %r at %r" % (type(node).__name__, step))


def report_path(value, mode):
    """`score`'s positional argument is an OUTPUT; `compare`'s is an INPUT.

    Measured the hard way on 2026-11-06: `wsp_gate.py score <corpus>.npz` wrote its JSON report over
    two published encounter corpora. Nothing in the CLI said the argument was a destination -- the
    docstring's `<out.json>` was the only hint -- and the files it chose to overwrite were the ones
    several ceilings in these docs were derived from. One was regenerable from its own recorded
    knobs; the other predates a generator change and is not.

    The rule: a `score` target must end in `.json`, and an existing file whose first byte is not a
    JSON opener is refused rather than replaced. Suffix alone would be satisfied by `x.npz.json`,
    so the bytes are what is inspected.
    """
    path = pathlib.Path(value)
    if mode != "score":
        return path
    if path.suffix != ".json":
        raise SystemExit(
            "score writes its report to the positional argument; asked for %r. Pass <out.json>. "
            "The boards are generated inside collect(), so a corpus is never an input to this mode "
            "-- and one was just destroyed by being passed here." % value)
    if path.exists():
        head = path.read_bytes()[:1]
        if head not in (b"", b"{", b"["):
            raise SystemExit("refusing to overwrite %r: it is not a JSON report (starts with %r)"
                             % (path, head))
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("score", "compare", "control"))
    parser.add_argument("first")
    parser.add_argument("second", nargs="?", default=None)
    parser.add_argument("--rows", type=int, default=512)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--edits", type=int, default=32, help="0.016%% of 202,882 params = 32")
    parser.add_argument("--checkpoint", default=str(BRAIN / "models" / "encounter_selfplay.pkl"))
    args = parser.parse_args(argv)

    if args.mode == "score":
        # Validated before `collect`, so a wrong path costs nothing instead of costing a corpus.
        out = report_path(args.first, args.mode)
        report = collect(args.rows, args.seed, args.checkpoint)
        out.write_text(json.dumps(report), encoding="utf-8")
        print(json.dumps(summarise(report), indent=1))
        return 0
    if args.mode == "compare":
        baseline = json.loads(pathlib.Path(args.first).read_text(encoding="utf-8"))
        candidate = json.loads(pathlib.Path(args.second).read_text(encoding="utf-8"))
        print(json.dumps({"baseline": summarise(baseline), "candidate": summarise(candidate),
                          "paired": paired(baseline, candidate)}, indent=1))
        return 0
    summary, meta, comparison = control(args.first, args.edits, args.seed, args.checkpoint)
    print(json.dumps({"arm": "random-sign control", "meta": meta,
                      "summary": summary, "paired_vs_baseline": comparison}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
