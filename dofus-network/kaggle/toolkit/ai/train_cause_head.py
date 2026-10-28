"""Train a failure-cause head on the constraint corpus (held-out templates).

The controller's cause labels are rules over error text. A model earns its place
only if it recognises a phrasing the rules have not seen, so the split here is by
**error template**, never by row: every copy of one phrasing across the 25 tools
lands in the same side. Metrics on that split measure generalisation to unseen
wording, not memory of the corpus.

This is still rule-distilled data (`kind: constraint_synthetic`, `live: false`).
It is a generalisation test for the taxonomy, not evidence about game outcomes,
and the sidecar says so.

    python -m ai.train_cause_head --corpus data/constraint_synthetic.jsonl \\
        --out models/cause_head.onnx
"""

from __future__ import annotations

import argparse
import json
import re
import zlib
from array import array
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

from agent.controller import CAUSES, error_class  # noqa: E402
from agent.features import TOOLS  # noqa: E402

HASH = 2048
FLAGS = 8
ERROR_CLASSES = ("structural", "transport", "refused", "action", "unknown")
# Generic English markers, never a cause label: "client"/"window"/"active" and
# "typeerror"/"cached" are the words the harness's own errors use. A held-out
# template with no shared marker is genuinely unknowable from text, which is why
# this list is broad rather than tuned to the templates.
MARKERS = (
    "not", "no", "unknown", "invalid", "needs", "schema", "rejected", "client",
    "window", "active", "fight", "board", "cached", "listing", "preset", "price",
    "inventory", "map", "npc", "dialog", "null", "typeerror", "referenceerror",
    "timeout", "resisted", "refused", "repeated", "same", "world", "moved",
    "unreadable", "partial",
)
DIM = HASH + len(TOOLS) + FLAGS + len(ERROR_CLASSES) + len(MARKERS)
TOOL_INDEX = {name: i for i, name in enumerate(TOOLS)}


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def _normalise(text: str) -> str:
    """Phrase shape, not the entity: ids would otherwise be memorised."""
    text = re.sub(r"\d+", "#", str(text or "none").lower())
    return re.sub(r"\s+", " ", text).strip()


def featurize(row: Dict[str, Any]) -> List[float]:
    vec = [0.0] * DIM
    text = _normalise(row.get("error"))
    total = 0.0
    for n in (3, 4, 5):
        for i in range(max(0, len(text) - n + 1)):
            gram = text[i:i + n]
            h = zlib.crc32(gram.encode("utf-8")) % HASH
            vec[h] += 1.0
            total += 1.0
    words = re.findall(r"[a-z]+", text)
    for word in words:
        h = zlib.crc32(("w:" + word).encode("utf-8")) % HASH
        vec[h] += 0.75
        total += 0.75
    if total:
        scale = total ** 0.5
        for i in range(HASH):
            if vec[i]:
                vec[i] = vec[i] / scale
    idx = TOOL_INDEX.get(str(row.get("tool") or ""))
    if idx is not None:
        vec[HASH + idx] = 1.0
    before = row.get("before") if isinstance(row.get("before"), dict) else {}
    after = row.get("after") if isinstance(row.get("after"), dict) else {}
    flags = [
        1.0 if row.get("ok") else 0.0,
        1.0 if not str(row.get("error") or "").strip() else 0.0,
        1.0 if not before else 0.0,
        1.0 if not after else 0.0,
        1.0 if before and after and before == after else 0.0,
        1.0 if int(row.get("repeat") or 1) > 1 else 0.0,
        1.0 if row.get("verdict") == "noop" else 0.0,
        1.0 if row.get("verdict") == "failed" else 0.0,
    ]
    start = HASH + len(TOOLS)
    for i, value in enumerate(flags):
        vec[start + i] = value
    cls = error_class(row.get("error"))
    if cls in ERROR_CLASSES:
        vec[start + FLAGS + ERROR_CLASSES.index(cls)] = 1.0
    marker_start = start + FLAGS + len(ERROR_CLASSES)
    for i, marker in enumerate(MARKERS):
        vec[marker_start + i] = 1.0 if marker in text else 0.0
    return vec


def _group(row: Dict[str, Any]) -> str:
    # The template family, not the rendered text: otherwise `npc 3888` in val is
    # "unseen" beside `npc 447` in train, and the score measures entity memory.
    return str(row.get("family") or row.get("error") or "none").lower()


def split_rows(rows: Sequence[Dict[str, Any]], seed: int = 7,
               val_per_cause: int = 2,
               calib_per_cause: int = 2) -> Tuple[List, List, List]:
    """Stratified by cause and family: every cause gets the same unseen count.

    A 20% cut over all families drew ten invalid families and one `none`, and the
    resulting accuracy was a statement about that skew. Two unseen templates per
    cause is the measurement the gate claims to make.
    """
    by_cause: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for row in rows:
        cause = str(row.get("expected_cause") or "unknown")
        by_cause.setdefault(cause, {}).setdefault(_group(row), []).append(row)
    fit: List[Dict[str, Any]] = []
    calib: List[Dict[str, Any]] = []
    val: List[Dict[str, Any]] = []
    for cause in CAUSES:
        families = by_cause.get(cause) or {}
        keys = sorted(families, key=lambda k: zlib.crc32(
            (k + ":%d" % seed).encode("utf-8")))
        if len(keys) < val_per_cause + calib_per_cause + 1:
            val_per = max(1, len(keys) // 3)
            calib_per = max(1, len(keys) // 3)
        else:
            val_per, calib_per = val_per_cause, calib_per_cause
        for key in keys[:val_per]:
            val.extend(families[key])
        for key in keys[val_per:val_per + calib_per]:
            calib.extend(families[key])
        for key in keys[val_per + calib_per:]:
            fit.extend(families[key])
    return fit, calib, val


def audit_splits(fit, calib, val) -> Dict[str, Any]:
    splits = {"fit": fit, "calib": calib, "val": val}
    families = {name: {_group(row) for row in rows} for name, rows in splits.items()}
    fingerprints = {
        name: [sha256(array("f", featurize(row)).tobytes()).hexdigest() for row in rows]
        for name, rows in splits.items()
    }
    overlaps = {}
    for left, right in (("fit", "calib"), ("fit", "val"), ("calib", "val")):
        known = set(fingerprints[left])
        leaked = [row for row, key in zip(splits[right], fingerprints[right]) if key in known]
        overlaps[left + "_" + right] = {
            "families": sorted(families[left] & families[right]),
            "input_rows": len(leaked),
            "causes": dict(Counter(row.get("expected_cause") for row in leaked)),
        }
    missing = {
        name: sorted(set(CAUSES) - {row.get("expected_cause") for row in rows})
        for name, rows in splits.items()
    }
    return {
        "valid": not any(missing.values()) and not any(
            entry["families"] or entry["input_rows"] for entry in overlaps.values()),
        "rows": {name: len(rows) for name, rows in splits.items()},
        "overlap": overlaps,
        "missing_causes": missing,
    }


def require_disjoint_splits(fit, calib, val) -> Dict[str, Any]:
    audit = audit_splits(fit, calib, val)
    if not audit["valid"]:
        raise ValueError("split audit failed: " + json.dumps(audit, sort_keys=True))
    return audit


def to_arrays(rows: Sequence[Dict[str, Any]]):
    import numpy as np

    xs, ys = [], []
    for row in rows:
        cause = row.get("expected_cause")
        if cause not in CAUSES:
            continue
        xs.append(featurize(row))
        ys.append(CAUSES.index(cause))
    return np.asarray(xs, dtype="float32"), np.asarray(ys, dtype="int64")


def build_model(hidden: int, dropout: float):
    import torch.nn as nn

    return nn.Sequential(
        nn.Linear(DIM, hidden),
        nn.GELU(),
        nn.Dropout(dropout),
        nn.Linear(hidden, hidden // 2),
        nn.GELU(),
        nn.Dropout(dropout),
        nn.Linear(hidden // 2, len(CAUSES)),
    )


def metrics_from_probs(probs, y) -> Dict[str, Any]:
    import numpy as np

    pred = probs.argmax(axis=1)
    n = len(y)
    confusion = [[0] * len(CAUSES) for _ in CAUSES]
    for t, p in zip(y, pred):
        confusion[int(t)][int(p)] += 1
    per_class = {}
    for c, name in enumerate(CAUSES):
        support = sum(confusion[c])
        per_class[name] = {
            "support": support,
            "recall": round(confusion[c][c] / support, 3) if support else None,
        }
    conf = probs.max(axis=1)
    correct = (pred == y).astype("float32")
    bins = [[0, 0.0, 0.0] for _ in range(10)]
    for c, ok in zip(conf, correct):
        b = min(9, int(float(c) * 10))
        bins[b][0] += 1
        bins[b][1] += float(c)
        bins[b][2] += float(ok)
    ece = 0.0
    for count, conf_sum, ok_sum in bins:
        if count:
            ece += (count / max(1, n)) * abs(ok_sum / count - conf_sum / count)
    f1s = []
    for c in range(len(CAUSES)):
        tp = confusion[c][c]
        fp = sum(confusion[r][c] for r in range(len(CAUSES)) if r != c)
        fn = sum(confusion[c][r] for r in range(len(CAUSES)) if r != c)
        if tp + fp + fn:
            f1s.append(2 * tp / (2 * tp + fp + fn))
    return {
        "accuracy": round(float(correct.mean()) if n else 0.0, 4),
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else 0.0,
        "ece": round(float(ece), 4),
        "n": int(n),
        "per_class": per_class,
        "confusion": confusion,
    }


def evaluate(model, x, y, temperature: float = 1.0) -> Dict[str, Any]:
    import torch

    with torch.no_grad():
        logits = model(torch.from_numpy(x)) / max(1e-6, temperature)
        probs = torch.softmax(logits, dim=1).numpy()
    return metrics_from_probs(probs, y)


def knn_probs(query, prototypes, labels, k: int = 7):
    """Cosine kNN probabilities from the stored fit prototypes."""
    import numpy as np

    q = query / (np.linalg.norm(query, axis=1, keepdims=True) + 1e-6)
    p = prototypes / (np.linalg.norm(prototypes, axis=1, keepdims=True) + 1e-6)
    sim = q @ p.T
    out = np.zeros((len(q), len(CAUSES)))
    for i in range(len(q)):
        top = np.argsort(sim[i])[-k:]
        for j in top:
            out[i, int(labels[j])] += max(0.0, float(sim[i, j]))
    out /= np.maximum(out.sum(axis=1, keepdims=True), 1e-9)
    return out


def combine_probs(mlp, knn, weight: float):
    return weight * knn + (1.0 - weight) * mlp


def choose_ensemble(mlp_c, knn_c, cy, mlp_v, knn_v, vy):
    """Pick the blend and temperature on calib, then report val once."""
    import numpy as np

    best = (-1.0, 0.0, 1.0)
    for step in range(11):
        weight = step / 10.0
        p = combine_probs(mlp_c, knn_c, weight)
        score = float((p.argmax(1) == cy).mean()) - _ece(p, cy)
        if score > best[0]:
            best = (score, weight, 1.0)
    _, weight, _ = best
    p = np.clip(combine_probs(mlp_c, knn_c, weight), 1e-9, 1.0)
    logits = np.log(p)
    best_t, best_ece = 1.0, 9.0
    for step in range(10, 61):
        t = step / 20.0
        q = np.exp(logits / t)
        q /= q.sum(axis=1, keepdims=True)
        ece = _ece(q, cy)
        if ece < best_ece:
            best_t, best_ece = t, ece
    p_val = np.clip(combine_probs(mlp_v, knn_v, weight), 1e-9, 1.0)
    p_val = np.exp(np.log(p_val) / best_t)
    p_val /= p_val.sum(axis=1, keepdims=True)
    return weight, best_t, metrics_from_probs(p_val, vy)


def _ece(probs, y) -> float:
    import numpy as np

    pred = probs.argmax(axis=1)
    conf = probs.max(axis=1)
    correct = (pred == y).astype("float32")
    bins = [[0, 0.0, 0.0] for _ in range(10)]
    for c, ok in zip(conf, correct):
        b = min(9, int(float(c) * 10))
        bins[b][0] += 1
        bins[b][1] += float(c)
        bins[b][2] += float(ok)
    total = max(1, len(y))
    return sum((count / total) * abs(ok_sum / count - conf_sum / count)
               for count, conf_sum, ok_sum in bins if count)


def fit_temperature(model, x, y) -> float:
    import torch

    with torch.no_grad():
        logits = model(torch.from_numpy(x))
    best_t, best_ece = 1.0, float("inf")
    for step in range(4, 61):
        t = step / 20.0
        probs = torch.softmax(logits / t, dim=1).numpy()
        ece = _ece(probs, y)
        if ece < best_ece:
            best_t, best_ece = t, ece
    return best_t


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", type=Path,
                        default=REPO_ROOT / "data" / "constraint_synthetic.jsonl")
    parser.add_argument("--out", type=Path,
                        default=REPO_ROOT / "models" / "cause_head.onnx")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--hidden", type=int, default=256)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--dropout", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--no-int8", action="store_true")
    args = parser.parse_args(argv)

    rows = read_jsonl(args.corpus)
    if not rows:
        print("missing corpus: %s (run agent.constraint_corpus --generate)" % args.corpus)
        return 2
    fit, calib, val = split_rows(rows, args.seed)
    try:
        split_audit = require_disjoint_splits(fit, calib, val)
    except ValueError as exc:
        print("GATE FAILED: %s; existing artifacts unchanged" % exc)
        return 1

    try:
        import numpy as np
        import torch
    except Exception as exc:  # noqa: BLE001
        print("torch/numpy required: %s" % exc)
        return 2

    x, y = to_arrays(fit)
    cx, cy = to_arrays(calib)
    vx, vy = to_arrays(val)
    print("fit %d / calib %d / val %d rows"
          % (len(x), len(cx), len(vx)))
    print("val covers %s"
          % json.dumps(dict(Counter(r.get("expected_cause") for r in val)),
                       sort_keys=True))
    print("unseen families in val: %d of %d"
          % (len({_group(r) for r in val}),
             len({_group(r) for r in rows})))

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    counts = np.bincount(y, minlength=len(CAUSES)).astype("float32")
    class_w = np.where(counts > 0, len(y) / (len(CAUSES) * np.maximum(counts, 1)), 1.0)
    class_w = np.clip(class_w, 0.5, 8.0)
    sample_w = torch.from_numpy(class_w[y].astype("float32"))

    model = build_model(args.hidden, args.dropout)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    loss_fn = torch.nn.CrossEntropyLoss(reduction="none")
    tx, ty = torch.from_numpy(x), torch.from_numpy(y)

    best = {"accuracy": -1.0, "epoch": -1, "state": None}
    for epoch in range(1, args.epochs + 1):
        model.train()
        opt.zero_grad()
        loss = (loss_fn(model(tx), ty) * sample_w).mean()
        loss.backward()
        opt.step()
        if epoch % 10 == 0 or epoch == args.epochs:
            model.eval()
            train_m = evaluate(model, x, y)
            calib_m = evaluate(model, cx, cy)
            print("epoch %3d loss %.4f fit %.4f calib %.4f ece %.4f"
                  % (epoch, float(loss), train_m["accuracy"],
                     calib_m["accuracy"], calib_m["ece"]))
            # Model selection is on calib, never on val: val is reported once.
            if calib_m["accuracy"] > best["accuracy"]:
                best = {"accuracy": calib_m["accuracy"], "epoch": epoch,
                        "state": {k: v.clone() for k, v in model.state_dict().items()}}
            model.train()

    if best["state"] is not None:
        model.load_state_dict(best["state"])
        print("using epoch %d (calib %.4f)" % (best["epoch"], best["accuracy"]))
    model.eval()
    temperature = fit_temperature(model, cx, cy)
    print("temperature %.2f (fit on calib)" % temperature)
    majority = Counter(int(v) for v in y).most_common(1)[0][0]
    majority_acc = float((vy == majority).mean()) if len(vy) else 0.0

    metrics = {
        "train": evaluate(model, x, y, temperature),
        "calib": evaluate(model, cx, cy, temperature),
        "val": evaluate(model, vx, vy, temperature),
        "temperature": temperature,
        "majority_cause_baseline": {"cause": CAUSES[majority],
                                    "accuracy": round(majority_acc, 4)},
        "best_calib_epoch": best["epoch"],
        "split": "grouped_by_error_template",
        "split_audit": split_audit,
        "seed": args.seed,
        "corpus_sha256": sha256(args.corpus.read_bytes()).hexdigest(),
        "promotion_ready": False,
        "kind": "cause_head",
        "labels": "constraint_synthetic (rule-distilled, live=false)",
        "classes": list(CAUSES),
        "features": DIM,
        "train_rows": len(x),
        "val_rows": len(vx),
        "val_unseen_families": len({_group(r) for r in val}),
        "train_families": len({_group(r) for r in fit}),
        "calib_rows": len(cx),
        "gate_notes": ("cause accuracy and ECE are measured on unseen error phrasings; "
                       "the 'beats always continue on simulated regret' gate needs a "
                       "repair-outcome simulator this corpus does not contain"),
    }
    # The MLP alone memorises the fit families (fit 1.0, val ~0.81). A cosine-kNN
    # over the same features is a second opinion that shares no weights, and the
    # blend is chosen on calib. Both halves are stored; neither is a rule label.
    with torch.no_grad():
        mlp_c = torch.softmax(model(torch.from_numpy(cx)), dim=1).numpy()
        mlp_v = torch.softmax(model(torch.from_numpy(vx)), dim=1).numpy()
    knn_c = knn_probs(cx, x, y)
    knn_v = knn_probs(vx, x, y)
    weight, blend_t, ens_val = choose_ensemble(mlp_c, knn_c, cy, mlp_v, knn_v, vy)
    metrics["mlp_val"] = metrics["val"]
    metrics["ensemble"] = {"weight": weight, "temperature": blend_t,
                           "prototypes": args.out.stem + "_prototypes.npz",
                           "k": 7, "val": ens_val}
    metrics["val"] = ens_val
    val_m = ens_val
    print("mlp val %.4f / ece %.4f; ensemble (weight %.1f, T %.2f) val %.4f / ece %.4f"
          % (metrics["mlp_val"]["accuracy"], metrics["mlp_val"]["ece"],
             weight, blend_t, val_m["accuracy"], val_m["ece"]))
    if val_m["accuracy"] < 0.85 or val_m["ece"] >= 0.15:
        print("GATE FAILED: need accuracy >= 0.85 and ECE < 0.15")

    model.eval()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    proto = args.out.with_name(args.out.stem + "_prototypes.npz")
    np.savez_compressed(proto, x=x.astype(np.float16), y=y.astype(np.int8))
    metrics["prototypes_path"] = str(proto)
    print("wrote %s (%.1f KB)" % (proto, proto.stat().st_size / 1024.0))
    dummy = torch.zeros(1, DIM, dtype=torch.float32)
    torch.onnx.export(model, dummy, str(args.out), input_names=["features"],
                      output_names=["logits"], opset_version=17, dynamo=False,
                      dynamic_axes={"features": {0: "batch"}, "logits": {0: "batch"}})
    print("wrote %s (%.1f KB)" % (args.out, args.out.stat().st_size / 1024.0))
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(args.out), providers=["CPUExecutionProvider"])
        with torch.no_grad():
            ref = model(torch.from_numpy(vx[:16])).numpy()
        got = sess.run(None, {"features": vx[:16]})[0]
        diff = float(np.abs(ref - got).max())
        metrics["onnx_parity_max_abs_diff"] = round(diff, 8)
        print("onnx parity max|diff| = %.6f" % diff)
    except Exception as exc:  # noqa: BLE001
        print("parity check skipped: %s" % exc)
    if not args.no_int8:
        try:
            from onnxruntime.quantization import quantize_dynamic, QuantType
            int8 = args.out.with_name(args.out.stem + "_int8.onnx")
            quantize_dynamic(str(args.out), str(int8), weight_type=QuantType.QInt8)
            metrics["int8_path"] = str(int8)
            print("wrote %s (%.1f KB)" % (int8, int8.stat().st_size / 1024.0))
        except Exception as exc:  # noqa: BLE001
            print("int8 export skipped: %s" % exc)
    sidecar = args.out.with_suffix(".json")
    sidecar.write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    print("wrote %s" % sidecar)
    return 0 if (val_m["accuracy"] >= 0.85 and val_m["ece"] < 0.15) else 1


if __name__ == "__main__":
    raise SystemExit(main())
