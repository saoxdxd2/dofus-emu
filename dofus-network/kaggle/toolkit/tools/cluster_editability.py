"""Is a failure cluster editable in weight space? Measure it before spending anything on Phase 3.

The question this answers, and the only question worth asking before an LLM edits weights: for the
examples the head gets wrong, is the *reason* they are wrong a single direction the head can be
pushed along, or is it spread over the input like noise? The published result this copies reported
coherent directions for one capability (story writing) and near-orthogonal per-example residuals
for another (translation), and the difference between 0.3-0.45 and 0.03-0.14 is the difference
between "an edit can fix this" and "this belongs in the prompt, the harness or the data".

Method, on the shipped head and the frozen val rows -- no training, no candidate, no live loop:

  1. Run the head and collect (true, predicted) for every row.
  2. For each confusion cluster with enough examples, take the hidden state at the divergence
     point (the layer feeding the final logits, where the class is actually decided).
  3. Subtract the mean hidden state of the SAME true class when it was classified correctly.
     That difference is what is unusual about the failing examples, in the representation the
     head itself uses -- not in feature space, where the answer would be about the data.
  4. Measure the cross-example cosine consistency of those residuals, and their alignment with
     the one edit direction that could flip the class: W_out[true] - W_out[predicted].
  5. Repeat with a shuffled-label control: clusters of the same size assembled from rows whose
     true class is the same but whose predicted label was permuted. If the real clusters are not
     more coherent than their own control, the number means nothing.

    python tools/cluster_editability.py [--min 20] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.weight_space import (  # noqa: E402
    DEFAULT_VAL, BASE_ONNX, export_manifest, manifest_weights, read_val_rows,
)

# The bands the published result landed in. Quoted as bands, not thresholds to hit: this script
# is a stop/go instrument, and a cluster that reads 0.2 is "unclear", not "editable".
#
# The verdict is taken from the LIFT over the shuffled control, not the raw consistency. Measured
# on this head: the two biggest clusters read 0.86 raw -- and their own controls read 0.58-0.64.
# A high floor is a property of a 784-unit hidden state after a mean subtraction, not evidence of
# an editable direction, so judging the raw number would call almost anything editable. The
# published bands (0.3-0.45 story writing, 0.03-0.14 translation) were reported for instruments
# without this floor, so they are kept here as the secondary condition, not the decision.
EDITABLE_AT = 0.30
NOISE_AT = 0.15
EDITABLE_LIFT = 0.15
NOISE_LIFT = 0.05


def _hidden_at_divergence(weights: Dict[str, Any], x):
    """The state feeding the final logits, for every row at once."""
    import numpy as np
    from agent.weight_space import _gelu

    names = sorted(n for n in weights if n.endswith(".weight"))
    h = np.asarray(x, dtype="float64")
    for i, name in enumerate(names[:-1]):
        w = np.asarray(weights[name], dtype="float64")
        b = np.asarray(weights[name.replace(".weight", ".bias")], dtype="float64")
        h = h @ w.T + b
        h = _gelu(h)
    return h, names[-1]


def _logits(weights: Dict[str, Any], x):
    import numpy as np
    from agent.weight_space import forward

    return np.asarray(forward(weights, x), dtype="float64")


def _cosines(vs, ref=None):
    import numpy as np
    vs = np.asarray(vs, dtype="float64")
    if vs.ndim == 1:
        vs = vs[None, :]
    n = np.linalg.norm(vs, axis=1, keepdims=True)
    u = vs / np.maximum(n, 1e-12)
    if ref is None:
        if len(u) < 2:
            return None
        g = u @ u.T
        iu = np.triu_indices(len(u), 1)
        return float(g[iu].mean())
    r = np.asarray(ref, dtype="float64")
    r = r / max(float(np.linalg.norm(r)), 1e-12)
    return float((u @ r).mean())


def clusters_for(weights, rows, tools, min_n: int) -> List[Dict[str, Any]]:
    import numpy as np

    x = np.array([r["x"] for r in rows], dtype="float64")
    y = np.array([int(r["y"]) for r in rows])
    lg = _logits(weights, x)
    pred = lg.argmax(axis=1)
    hid, last_name = _hidden_at_divergence(weights, x)
    w_out = np.asarray(weights[last_name], dtype="float64")

    by_true_clean: Dict[int, Any] = {}
    groups: Dict[tuple, List[int]] = defaultdict(list)
    for i in range(len(y)):
        if int(pred[i]) == int(y[i]):
            by_true_clean.setdefault(int(y[i]), []).append(i)
        else:
            groups[(int(y[i]), int(pred[i]))].append(i)

    out: List[Dict[str, Any]] = []
    rng = np.random.default_rng(20261104)
    for (t, p), idx in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(idx) < min_n or t not in by_true_clean:
            continue
        clean = np.array(by_true_clean[t], dtype=int)
        mean_clean = hid[clean].mean(axis=0)
        flagged = np.array(idx, dtype=int)
        resid = hid[flagged] - mean_clean
        consistency = _cosines(resid)
        mean_dir = resid.mean(axis=0)
        edit_dir = w_out[t] - w_out[p]
        align = _cosines([mean_dir], ref=edit_dir)
        # Shuffled-label control: same size, same true class, predicted label taken from the
        # other classes this true class is confused with. Any coherence left here is the
        # instrument's, not the cluster's.
        others = [v for (tt, v), ii in groups.items() if tt == t and v != p]
        ctrl = None
        if others:
            pool = np.array([i for (tt, v), ii in groups.items() if tt == t
                             for i in ii], dtype=int)
            if len(pool) >= len(flagged):
                pick = rng.choice(pool, size=len(flagged), replace=False)
                ctrl = _cosines(hid[pick] - mean_clean)
        lift = None if (consistency is None or ctrl is None) else consistency - ctrl
        verdict = ("editable" if lift is not None and lift >= EDITABLE_LIFT
                   and consistency is not None and consistency >= EDITABLE_AT
                   else "noise" if lift is not None and lift <= NOISE_LIFT
                   else "unclear")
        out.append({
            "true": tools[t] if t < len(tools) else str(t),
            "predicted": tools[p] if p < len(tools) else str(p),
            "n_flagged": int(len(flagged)),
            "n_clean_reference": int(len(clean)),
            "residual_cosine_consistency": None if consistency is None else round(consistency, 4),
            "mean_direction_vs_edit_direction": None if align is None else round(align, 4),
            "shuffled_control_consistency": None if ctrl is None else round(ctrl, 4),
            "lift_over_control": None if lift is None else round(lift, 4),
            "verdict": verdict,
        })
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ns = argparse.ArgumentParser(description=__doc__)
    ns.add_argument("--min", type=int, default=20, help="smallest cluster to score")
    ns.add_argument("--val", type=Path, default=DEFAULT_VAL)
    ns.add_argument("--onnx", type=Path, default=BASE_ONNX)
    ns.add_argument("--json", type=Path, default=None)
    args = ns.parse_args(argv)

    manifest = export_manifest(args.onnx)
    weights = manifest_weights(manifest)
    rows = read_val_rows(args.val)
    tools = manifest["base"]["tools"]
    if not rows:
        print(json.dumps({"error": "no val rows at %s" % args.val}))
        return 2
    scored = clusters_for(weights, rows, tools, args.min)
    acc = sum(1 for r in rows)
    report = {
        "rows": len(rows),
        "tools": len(tools),
        "clusters_scored": len(scored),
        "clusters_skipped_below_min": None,
        "verdicts": dict(Counter(s["verdict"] for s in scored)),
        "clusters": scored,
        "bands": {"editable_at": EDITABLE_AT, "noise_at": NOISE_AT},
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("rows", "tools", "clusters_scored", "verdicts")},
                     indent=1))
    for s in scored[:8]:
        print("  %-16s -> %-14s n=%-4d consistency=%-8s control=%-8s lift=%-8s %s" % (
            s["true"], s["predicted"], s["n_flagged"],
            s["residual_cosine_consistency"], s["shuffled_control_consistency"],
            s["lift_over_control"], s["verdict"]))
    # No clusters scored is a broken instrument (wrong min, wrong val file), not a clean bill.
    return 0 if scored else 2


if __name__ == "__main__":
    raise SystemExit(main())
