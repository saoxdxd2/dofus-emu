"""Weight-space programming: learn to break the repeat-fail loop on the tool head.

Why this and not a hardcoded "if inside a dungeon" branch. The model loops in a
dungeon because every failed step it takes raises ``fail_streak`` and sets an
``err_*`` slot, but the shipped head was trained to *mimic the planner*, so it
reproduces the same choice that just failed. The fix we want is learned, not
told: teach the head that when its own recent attempts keep failing it must move
to a different tool. Those signals (``fail_streak``, ``last_tool``, ``err_*``)
are already inputs of the 42-feature vector, so this is a weight edit, not a new
feature and not a rule.

What it does (offline, never in the live loop):

1. loads the base ``models/tool_policy.onnx`` initializers as float64 weights
   (the same MatMul->Add->GELU x2->MatMul->Add graph ``agent/weight_space``
   replays);
2. evaluates the base on a deterministic fit / hold-out split of the val rows;
3. runs a small number of gradient steps that raise the weight on "I am stuck"
   rows (``fail_streak > 0``), so the head learns to switch tool instead of
   repeating the one that failed;
4. GATES the edit: it is only accepted when hold-out loop-row accuracy improves
   and overall hold-out accuracy does not regress beyond ``--tol``;
5. writes an accepted candidate to ``models/weight_edits/loopbreak_<ts>/`` --
   the base artifact is never overwritten.

    python tools/wsp_learn_loopbreak.py            # fit, gate, report
    python tools/wsp_learn_loopbreak.py --dry-run   # measure only, write nothing
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

REPO = Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
if str(BRAIN) not in sys.path:
    sys.path.insert(0, str(BRAIN))

from agent import weight_space as ws  # noqa: E402

BASE_ONNX = BRAIN / "models" / "tool_policy.onnx"
SIDECAR = BRAIN / "models" / "tool_policy.json"
VAL = BRAIN / "data" / "tool_sft_v3" / "val.jsonl"
EDITS_DIR = BRAIN / "models" / "weight_edits"

# fast erf: scipy if available, else math.erf vectorised (matches weight_space._gelu)
try:
    from scipy.special import erf as _erf  # type: ignore
    def _erf_np(z):
        return _erf(z)
except Exception:  # noqa: BLE001
    def _erf_np(z):
        return np.vectorize(math.erf)(z)


def gelu(z):
    return 0.5 * z * (1.0 + _erf_np(z / math.sqrt(2.0)))


def gelu_prime(z):
    cdf = 0.5 * (1.0 + _erf_np(z / math.sqrt(2.0)))
    pdf = np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    return cdf + z * pdf


def load_weights(path: Path) -> Dict[str, np.ndarray]:
    import onnx
    from onnx import numpy_helper
    model = onnx.load(str(path))
    out: Dict[str, np.ndarray] = {}
    for tensor in model.graph.initializer:
        out[tensor.name] = numpy_helper.to_array(tensor).astype("float64")
    return out


def ordered(weights: Dict[str, np.ndarray]):
    w_names = sorted(n for n in weights if n.endswith(".weight"))
    return [(n, weights[n], weights[n.replace(".weight", ".bias")]) for n in w_names]


def forward(weights, X):
    x = np.asarray(X, dtype="float64")
    names = sorted(n for n in weights if n.endswith(".weight"))
    cache = {"x": x}
    for i, name in enumerate(names):
        W = weights[name]
        b = weights[name.replace(".weight", ".bias")]
        z = x @ W.T + b
        if i < len(names) - 1:
            x = gelu(z)
            cache["z%d" % i] = z
            cache["h%d" % i] = x
        else:
            cache["logits"] = z
    return cache["logits"], cache


def ce_and_grads(weights, X, y):
    logits, c = forward(weights, X)
    B = X.shape[0]
    m = logits.max(axis=1, keepdims=True)
    e = np.exp(logits - m)
    p = e / e.sum(axis=1, keepdims=True)
    loss = -np.mean(np.log(p[np.arange(B), y] + 1e-12))
    dl = p.copy()
    dl[np.arange(B), y] -= 1.0
    dl /= B
    names = sorted(n for n in weights if n.endswith(".weight"))
    w0, w2, w4 = names[0], names[1], names[2]
    grads = {}
    h2 = c["h1"]
    grads[w4] = dl.T @ h2
    grads[w4.replace(".weight", ".bias")] = dl.sum(axis=0)
    dh2 = dl @ weights[w4]
    dz2 = dh2 * gelu_prime(c["z1"])
    h1 = c["h0"]
    grads[w2] = dz2.T @ h1
    grads[w2.replace(".weight", ".bias")] = dz2.sum(axis=0)
    dh1 = dz2 @ weights[w2]
    dz1 = dh1 * gelu_prime(c["z0"])
    grads[w0] = dz1.T @ c["x"]
    grads[w0.replace(".weight", ".bias")] = dz1.sum(axis=0)
    return loss, grads


def load_rows(tools):
    rows = ws.read_val_rows(VAL)
    X = np.array([r["x"] for r in rows], dtype="float64")
    Y = np.array([int(r["y"]) for r in rows])
    return rows, X, Y


def fail_streak_index():
    from agent.features import FEATURE_NAMES
    return FEATURE_NAMES.index("fail_streak")


def loop_accuracy(weights, X, Y, loop_mask, tools):
    rows = [{"x": X[i].tolist(), "y": int(Y[i])} for i in range(len(Y)) if loop_mask[i]]
    if not rows:
        return None, 0
    rep = ws.evaluate(weights, rows, tools)
    return rep.get("val_accuracy"), rep.get("rows")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--steps", type=int, default=180)
    ap.add_argument("--lr", type=float, default=0.05)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--tol", type=float, default=0.005,
                    help="max allowed drop in overall hold-out accuracy")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    rng = np.random.default_rng(args.seed)
    sidecar = json.loads(SIDECAR.read_text(encoding="utf-8"))
    tools = sidecar["tools"]
    base = load_weights(BASE_ONNX)
    rows, X, Y = load_rows(tools)
    fs_i = fail_streak_index()
    fs = X[:, fs_i]

    n = len(Y)
    idx = np.arange(n)
    fit_mask = (idx % 2 == 0)
    hold_mask = ~fit_mask
    loop = fs > 0.0

    print("rows %d  fit %d  holdout %d  loop_rows %d (fit %d / holdout %d)"
          % (n, fit_mask.sum(), hold_mask.sum(), loop.sum(),
             (loop & fit_mask).sum(), (loop & hold_mask).sum()))

    # Baseline on hold-out (whole + loop rows)
    base_hold = ws.evaluate(base, [{"x": X[i].tolist(), "y": int(Y[i])}
                                   for i in np.where(hold_mask)[0]], tools)
    base_hold_acc = base_hold.get("val_accuracy")
    base_loop_acc, base_loop_rows = loop_accuracy(base, X[hold_mask], Y[hold_mask],
                                                  loop[hold_mask], tools)
    print("BASE holdout acc %.4f  loop_acc %s over %d loop rows"
          % (base_hold_acc, base_loop_acc, base_loop_rows))

    # Training set: all fit rows, loop rows oversampled x6 so "I am stuck" is
    # not drowned out by the far more common easy steps.
    fit_idx = np.where(fit_mask)[0]
    loop_fit = fit_idx[loop[fit_idx]]
    pool = np.concatenate([fit_idx] + [loop_fit] * 6)

    weights = {k: v.copy() for k, v in base.items()}
    for step in range(args.steps):
        batch = rng.choice(pool, size=min(args.batch, len(pool)), replace=True)
        loss, grads = ce_and_grads(weights, X[batch], Y[batch])
        for k in weights:
            weights[k] = weights[k] - args.lr * grads[k]
        if step % 30 == 0 or step == args.steps - 1:
            print("  step %3d  loss %.4f" % (step, loss))

    edit_hold = ws.evaluate(weights, [{"x": X[i].tolist(), "y": int(Y[i])}
                                      for i in np.where(hold_mask)[0]], tools)
    edit_hold_acc = edit_hold.get("val_accuracy")
    edit_loop_acc, edit_loop_rows = loop_accuracy(weights, X[hold_mask], Y[hold_mask],
                                                  loop[hold_mask], tools)
    print("EDIT holdout acc %.4f  loop_acc %s over %d loop rows"
          % (edit_hold_acc, edit_loop_acc, edit_loop_rows))

    ok_overall = edit_hold_acc is not None and base_hold_acc is not None \
        and edit_hold_acc >= base_hold_acc - args.tol
    ok_loop = (edit_loop_acc is not None and base_loop_acc is not None
               and edit_loop_acc > base_loop_acc)
    accepted = bool(ok_overall and ok_loop)
    print("GATE: overall_ok=%s  loop_improved=%s  -> %s"
          % (ok_overall, ok_loop, "ACCEPT" if accepted else "REJECT"))

    if args.dry_run or not accepted:
        print("no artifact written" + ("" if not accepted else " (dry-run)"))
        return 0

    ts = time.strftime("%Y%m%dT%H%M%S")
    out = EDITS_DIR / ("loopbreak_" + ts)
    out.mkdir(parents=True, exist_ok=True)
    tensors = []
    for name in sorted(base):
        arr = np.asarray(weights[name], dtype="float32")
        tensors.append({"name": name, "shape": list(arr.shape),
                        "dtype": "float32", "values": arr.tolist()})
    manifest = {
        "kind": ws.MANIFEST_KIND, "version": ws.MANIFEST_VERSION,
        "created": ts, "purpose": "loop-break: switch tool when fail_streak>0",
        "base": {"onnx": str(BASE_ONNX), "sha256": ws.sha256_file(BASE_ONNX),
                 "features": sidecar["features"], "tools": tools},
        "metrics": {
            "base_holdout_acc": base_hold_acc, "edit_holdout_acc": edit_hold_acc,
            "base_loop_acc": base_loop_acc, "edit_loop_acc": edit_loop_acc,
            "loop_rows": int(base_loop_rows),
        },
        "tensors": tensors,
    }
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(manifest["metrics"], indent=1),
                                      encoding="utf-8")
    # Build a real candidate ONNX from the edited weights. save_candidate_onnx
    # copies the base graph and swaps only the initializers, so the base artifact
    # is never touched and the edit stays reversible.
    cand = ws.save_candidate_onnx(BASE_ONNX, manifest, out / "candidate.onnx")
    (out / "README.txt").write_text(
        "loop-break weight edit (learned, gated)\n"
        "purpose: when fail_streak>0 the head switches tool instead of repeating\n"
        "metrics: %s\n" % json.dumps(manifest["metrics"])
        + "activate (NNPlanner head): set DOFUS_TOOL_POLICY_MODEL=%s\n" % cand
        + "rollback: unset the env var; the base tool_policy.onnx is unchanged\n",
        encoding="utf-8")
    print("wrote candidate -> %s" % out)
    print("  candidate.onnx %d bytes" % cand.stat().st_size)
    print("  activate: set DOFUS_TOOL_POLICY_MODEL=%s" % cand)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())