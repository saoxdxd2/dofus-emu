"""Weight-space programming for the small learned heads (Phase 0 + 1).

An LLM (offline, between episodes -- never in the live loop) reads a small
head as data, proposes a sparse structured edit, and a deterministic gate
replays it before it may act. This module is that substrate:

* inventory: sha256 of the base ONNX + sidecar, tensor shapes, param count;
* export: ONNX initializers -> a readable JSON manifest (weights as numbers);
* unit manifest: which input features / output tools each hidden unit serves;
* edit applier: validates shape, range, budget and base hash, then applies;
* replay: forward pass + accuracy / F1 / ECE / expected-gain on val rows;
* pin / verify: freeze the val corpus identity (rows + sha256) and the base
  model's numbers on it, so the gate is not scored against a moving target;
* skill spec: a user prompt is first compiled to a fixed, validated spec --
  never directly to weights;
* publish / rollback: a candidate lives in models/weight_edits/<id>/ and the
  base artifact is never overwritten by an unproven edit.

Measured faithfulness of the pure replay forward pass against onnxruntime:
agreement 1.0, max logit difference 6.0e-06 on all 3,719 val rows
(models/weight_edits_onnx_agreement.json). The graph is MatMul->Add->GELU
twice, then MatMul->Add; the replay below is that graph, in float64.

Nothing here is imported by the runtime planner. The acting policy, tool
legality and safety stay in code; a weight edit may only re-rank legal tools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import logging
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODELS_DIR = REPO_ROOT / "models"
BASE_ONNX = MODELS_DIR / "tool_policy.onnx"
BASE_SIDECAR = MODELS_DIR / "tool_policy.json"
BASE_REPORT = MODELS_DIR / "tool_policy_report.json"
EDITS_DIR = MODELS_DIR / "weight_edits"
BACKUPS_DIR = MODELS_DIR / "backups"
DEFAULT_VAL = REPO_ROOT / "data" / "tool_sft_v3" / "val.jsonl"
PROVENANCE_PATH = MODELS_DIR / "tool_policy_provenance.json"
MANIFEST_KIND = "weight-space-manifest"
MANIFEST_VERSION = 1
COMMON_SUPPORT = 50

# Absolute floor on all-class macro F1. `macro_f1_common` is already guarded
# relative to the base (-0.01), but a candidate can buy common-class accuracy by
# abandoning every rare tool and still pass that check: at the pinned baseline
# (macro_f1 0.2058 over 21 tools, 15 of them rare) collapsing the tail drives the
# all-class figure to roughly 0.06. This floor rejects that trade outright. It is
# deliberately below the baseline so ordinary drift is not blocked -- it exists to
# catch collapse, not to freeze the metric.
MIN_MACRO_F1 = 0.15


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_by_sha(prefix: str, root: Optional[Path] = None) -> Optional[Path]:
    """The artifact whose sha256 starts with `prefix`, or None.

    A floor, a baseline and a promotion bar are all properties of *one* artifact.
    Every one of them records the sha it was measured on, and an artifact that is
    replaced stops matching -- which is the whole point. This turns that sha back
    into a file so a check can re-derive the number instead of quoting it, and so
    "the model this was measured on" is a claim that can be answered.

    The retired artifacts live in `models/backups/` under their own sha prefix, so
    the search covers both the installed path and the backups.
    """
    key = str(prefix or "").strip().lower()
    if len(key) < 8:
        return None
    base = Path(root or MODELS_DIR)
    for folder in (base, base / "backups"):
        if not folder.is_dir():
            continue
        for candidate in sorted(folder.glob("*.onnx")):
            try:
                if sha256_file(candidate).startswith(key):
                    return candidate
            except OSError:
                continue
    return None


def load_sidecar(path: Path = BASE_SIDECAR) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def feature_names() -> List[str]:
    try:
        from agent.features import FEATURE_NAMES
        return list(FEATURE_NAMES)
    except Exception:
        return []


def feature_names_for(width: Optional[int]) -> List[str]:
    """Names for an artifact of this width, not for the 42-wide head.

    `feature_names()` describes the base featuriser. Reporting those 42 names for a
    266-wide input would label two thirds of the vector as nothing, and an inventory
    that cannot say what the model reads is not an inventory.
    """
    names = feature_names()
    if not width:
        return names
    if width <= len(names):
        return names[:width]
    return names + ["history_%d" % i for i in range(width - len(names))]


def onnx_tensor_shapes(path: Path) -> List[Tuple[str, Tuple[int, ...]]]:
    import onnx
    model = onnx.load(str(path))
    return [(t.name, tuple(t.dims)) for t in model.graph.initializer]


def inventory(onnx_path: Path = BASE_ONNX,
              sidecar_path: Path = BASE_SIDECAR) -> Dict[str, Any]:
    """Phase 0: what exactly is the base head we would edit?"""
    onnx_path, sidecar_path = Path(onnx_path), Path(sidecar_path)
    sidecar = load_sidecar(sidecar_path) if sidecar_path.is_file() else {}
    tensors = onnx_tensor_shapes(onnx_path)
    params = sum(math.prod(shape) for _name, shape in tensors)
    return {
        "onnx": str(onnx_path),
        "sha256": sha256_file(onnx_path) if onnx_path.is_file() else None,
        "sidecar": str(sidecar_path),
        "sidecar_sha256": sha256_file(sidecar_path) if sidecar_path.is_file() else None,
        "report_sha256": sha256_file(BASE_REPORT) if BASE_REPORT.is_file() else None,
        "params": params,
        "sidecar_params": sidecar.get("params"),
        "features": sidecar.get("features"),
        "tools": sidecar.get("tools") or [],
        "hidden": sidecar.get("hidden"),
        "depth": sidecar.get("depth"),
        "arch": sidecar.get("arch") or "mlp",
        "feature_names": feature_names_for(sidecar.get("features")),
        "tensors": [{"name": name, "shape": list(shape)} for name, shape in tensors],
        "params_match_sidecar": params == sidecar.get("params"),
    }


def export_manifest(onnx_path: Path = BASE_ONNX,
                    sidecar_path: Path = BASE_SIDECAR) -> Dict[str, Any]:
    import onnx
    from onnx import numpy_helper
    onnx_path = Path(onnx_path)
    model = onnx.load(str(onnx_path))
    tensors = []
    for tensor in model.graph.initializer:
        array = numpy_helper.to_array(tensor)
        tensors.append({
            "name": tensor.name,
            "shape": [int(d) for d in array.shape],
            "dtype": str(array.dtype),
            "values": array.tolist(),
        })
    sidecar = load_sidecar(sidecar_path) if Path(sidecar_path).is_file() else {}
    return {
        "kind": MANIFEST_KIND,
        "version": MANIFEST_VERSION,
        "base": {
            "onnx": onnx_path.name,
            "sha256": sha256_file(onnx_path),
            "sidecar": Path(sidecar_path).name,
            "sidecar_sha256": sha256_file(Path(sidecar_path)),
            "params": sum(math.prod(t["shape"]) for t in tensors),
            "features": sidecar.get("features"),
            "tools": sidecar.get("tools") or [],
            "arch": sidecar.get("arch") or "mlp",
            "history": sidecar.get("history"),
            "feature_names": feature_names_for(sidecar.get("features")),
            "input": model.graph.input[0].name if model.graph.input else "features",
            "output": model.graph.output[0].name if model.graph.output else "logits",
            "node_types": [node.op_type for node in model.graph.node],
        },
        "tensors": tensors,
    }


def manifest_weights(manifest: Dict[str, Any]) -> Dict[str, Any]:
    return {t["name"]: t["values"] for t in manifest["tensors"]}


def _flatten(values: Any) -> List[float]:
    if isinstance(values, list):
        out: List[float] = []
        for v in values:
            out.extend(_flatten(v))
        return out
    return [float(values)]


def _get(values: Any, index: Sequence[int]) -> float:
    cursor = values
    for i in index:
        cursor = cursor[i]
    return float(cursor)


def _set(values: Any, index: Sequence[int], new_value: float) -> None:
    cursor = values
    for i in index[:-1]:
        cursor = cursor[i]
    cursor[index[-1]] = float(new_value)


def validate_manifest(manifest: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    if manifest.get("kind") != MANIFEST_KIND:
        problems.append("kind is not %r" % MANIFEST_KIND)
    base = manifest.get("base") or {}
    shapes = {t["name"]: tuple(t["shape"]) for t in manifest.get("tensors") or []}
    weights = [n for n in shapes if n.endswith(".weight")]
    biases = [n for n in shapes if n.endswith(".bias")]
    if len(weights) != len(biases):
        problems.append("%d weight tensors vs %d biases" % (len(weights), len(biases)))
    tools, features = base.get("tools") or [], base.get("features")
    # The dense-stack shape checks describe an MLP: first matrix consumes the
    # features, last matrix emits the tools. A recurrent policy has neither, so
    # applying them would report a shape error for a correct graph. Its width is
    # checked against the graph input instead.
    if (base.get("arch") or "mlp") == "mlp":
        for name in weights:
            if len(shapes[name]) != 2:
                problems.append("%s is not a matrix: %s" % (name, shapes[name]))
        if weights:
            ordered = sorted(weights)
            first, last = ordered[0], ordered[-1]
            if features and shapes[first][1] != features:
                problems.append("first weight in=%d, sidecar features=%d"
                                % (shapes[first][1], features))
            if tools and shapes[last][0] != len(tools):
                problems.append("last weight out=%d, tools=%d"
                                % (shapes[last][0], len(tools)))
    elif features:
        import onnx as _onnx
        graph = _onnx.load(str(BASE_ONNX)).graph
        dims = graph.input[0].type.tensor_type.shape.dim
        width = int(dims[-1].dim_value) if dims and dims[-1].dim_value else 0
        if width and width != int(features):
            problems.append("graph input is %d wide, sidecar features=%d"
                            % (width, int(features)))
    for tensor in manifest.get("tensors") or []:
        flat = _flatten(tensor["values"])
        if len(flat) != math.prod(tensor["shape"]):
            problems.append("%s holds %d values, shape says %d"
                            % (tensor["name"], len(flat), math.prod(tensor["shape"])))
        if not all(math.isfinite(v) for v in flat):
            problems.append("%s holds a non-finite value" % tensor["name"])
    return problems


def _top_indices(row: Sequence[float], names: Sequence[str],
                 k: int = 5) -> List[Dict[str, Any]]:
    order = sorted(range(len(row)), key=lambda i: -abs(row[i]))[:k]
    return [{"name": names[i], "weight": round(float(row[i]), 6)} for i in order
            if abs(row[i]) > 1e-9]


def _gelu(z) -> Any:
    import numpy as np
    return 0.5 * z * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))


def _activations(weights: Dict[str, Any], x) -> dict[str, Any]:
    import numpy as np
    names = sorted(n for n in weights if n.endswith(".weight"))
    h, out = x, {}
    for i, name in enumerate(names):
        w = np.asarray(weights[name], dtype="float64")
        b = np.asarray(weights[name.replace(".weight", ".bias")], dtype="float64")
        h = h @ w.T + b
        if i < len(names) - 1:
            h = _gelu(h)
            out[name] = np.mean(np.abs(h), axis=0)
    return out


def build_unit_manifest(weights: Dict[str, Any],
                        feats: Sequence[str], tools: Sequence[str],
                        rows: Optional[Sequence[Dict[str, Any]]] = None,
                        top_k: int = 5) -> Dict[str, Any]:
    """Plain-language map of the head, from measured statistics only."""
    names = sorted(n for n in weights if n.endswith(".weight"))
    layers = []
    acts: Optional[Dict[str, List[float]]] = None
    if rows:
        try:
            import numpy as np
            acts = _activations(weights, np.array([r["x"] for r in rows],
                                                   dtype="float64"))
        except Exception:
            acts = None
    for li, name in enumerate(names):
        matrix = weights[name]
        out_names = tools if li == len(names) - 1 else [
            "%s.unit_%d" % (name.split(".")[0], j) for j in range(len(matrix))]
        in_names = list(feats) if li == 0 else [
            "%s.unit_%d" % (names[li - 1].split(".")[0], j)
            for j in range(len(matrix[0]))]
        units = []
        for j, row in enumerate(matrix):
            unit: Dict[str, Any] = {
                "unit": j,
                "top_inputs": _top_indices(row, in_names, top_k),
            }
            if acts and name in acts:
                unit["mean_abs_activation"] = round(float(acts[name][j]), 6)
            units.append(unit)
        layers.append({"tensor": name, "units": units,
                       "outputs": list(out_names)})
    return {"kind": "unit-manifest", "feature_names": list(feats),
            "tools": list(tools), "layers": layers}


def forward(weights: Dict[str, Any], x) -> Any:
    import numpy as np
    arr = np.asarray(x, dtype="float64")
    names = sorted(n for n in weights if n.endswith(".weight"))
    h = arr
    for i, name in enumerate(names):
        w = np.asarray(weights[name], dtype="float64")
        b = np.asarray(weights[name.replace(".weight", ".bias")], dtype="float64")
        h = h @ w.T + b
        if i < len(names) - 1:
            h = _gelu(h)
    return h


def _softmax(logits) -> Any:
    import numpy as np
    z = logits - logits.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def read_val_rows(path: Path = DEFAULT_VAL) -> List[Dict[str, Any]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def gain_of_row(row: Dict[str, Any], tool_name: str) -> float:
    """The counterfactual gain the corpus recorded for choosing `tool_name`.

    Module-level so the gate's trade-off probe reads the same number the gate
    reads; a second copy of this lookup would be a second definition of "gain".
    """
    gains = row.get("gains") or {}
    return float(gains.get(tool_name, 0.0)) if isinstance(gains, dict) else 0.0


def metrics_from_logits(logits, rows: Sequence[Dict[str, Any]],
                        tools: Sequence[str],
                        corpus_sha256: Optional[str] = None,
                        return_correct: bool = False) -> Dict[str, Any]:
    """Accuracy / F1 / ECE / expected-gain -- the promotion gate's numbers.

    Deliberately independent of how the logits were produced. `evaluate` is the
    MLP path; a sequence policy with a recurrent encoder is scored by this same
    function, because a metric that depends on the architecture cannot judge a
    change of architecture.

    `corpus_sha256` stamps which bytes were scored. Two corpora with the same row
    count are otherwise indistinguishable to `gate_decision`, which used to compare
    only `rows`.
    """
    import numpy as np
    if not rows:
        return {"rows": 0}
    y = np.array([int(r["y"]) for r in rows])
    logits = np.asarray(logits, dtype="float64")
    probs = _softmax(logits)
    pred = logits.argmax(axis=1)
    correct = pred == y
    per_class = []
    for c in range(len(tools)):
        tp = int(((pred == c) & (y == c)).sum())
        fp = int(((pred == c) & (y != c)).sum())
        fn = int(((pred != c) & (y == c)).sum())
        support = int((y == c).sum())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class.append({"tool": tools[c], "support": support,
                          "predicted": int((pred == c).sum()),
                          "recall": round(recall, 4),
                          "precision": round(precision, 4),
                          "f1": round(f1, 4)})
    f1s = [p["f1"] for p in per_class]
    common = [p["f1"] for p in per_class if p["support"] >= COMMON_SUPPORT]
    conf: Dict[Tuple[int, int], int] = {}
    for true_c, pred_c in zip(y.tolist(), pred.tolist()):
        if true_c != pred_c:
            conf[(true_c, pred_c)] = conf.get((true_c, pred_c), 0) + 1
    confusion_top = [{"true": tools[t], "predicted": tools[p], "n": n}
                     for (t, p), n in sorted(conf.items(), key=lambda kv: -kv[1])[:12]]
    confidence = probs.max(axis=1)
    ece = 0.0
    for lo in range(10):
        upper = (lo + 1) / 10 + (1e-9 if lo == 9 else 0)
        mask = (confidence >= lo / 10) & (confidence < upper)
        if mask.any():
            ece += float(mask.mean()) * abs(float(correct[mask].mean())
                                             - float(confidence[mask].mean()))

    model_gain = sum(gain_of_row(r, tools[int(p)]) for r, p in zip(rows, pred.tolist())) / len(rows)
    teacher_gain = sum(gain_of_row(r, str(r.get("teacher") or "")) for r in rows) / len(rows)
    oracle_gain = sum(gain_of_row(r, str(r.get("tool") or "")) for r in rows) / len(rows)
    capture = ((model_gain - teacher_gain) / (oracle_gain - teacher_gain)
               if oracle_gain != teacher_gain else 0.0)

    # Task optimality, not label agreement. On 22% of rows several tools sit at the
    # same maximum gain, so the stored label is one of several equally good answers
    # and the tie-break was the teacher's. A model that picks a different tied-optimal
    # tool is right about the world and wrong about the label; this is the metric that
    # tells those two apart, and it is the one a policy trained on outcomes is
    # actually optimising.
    optimal = []
    for r, p in zip(rows, pred.tolist()):
        gains = r.get("gains") or {}
        if not gains:
            optimal.append(True)
            continue
        best_gain = max(gains.values())
        optimal.append(abs(gain_of_row(r, tools[int(p)]) - best_gain) < 1e-9)
    optimal_arr = np.asarray(optimal, dtype=bool)

    out = {
        "rows": len(rows),
        "val_accuracy": round(float(correct.mean()), 4),
        "optimal": round(float(optimal_arr.mean()), 4),
        "macro_f1": round(float(sum(f1s) / len(f1s)), 4) if f1s else 0.0,
        "macro_f1_common": round(float(sum(common) / len(common)), 4) if common else 0.0,
        "ece": round(ece, 4),
        "per_class": per_class,
        "confusion_top": confusion_top,
        "decision": {
            "expected_gain_model": round(model_gain, 4),
            "expected_gain_teacher": round(teacher_gain, 4),
            "expected_gain_oracle": round(oracle_gain, 4),
            "gain_capture": round(capture, 4),
        },
    }
    # A tool the head never ranks first is unreachable no matter what it scores:
    # no amount of accuracy on the other classes makes it selectable. Tracked
    # separately from accuracy because the two failures are not the same failure.
    out["reachable_tools"] = sorted({tools[int(p)] for p in pred.tolist()})
    out["unreachable_tools"] = sorted(set(tools) - set(out["reachable_tools"]))
    if corpus_sha256:
        out["corpus_sha256"] = corpus_sha256
    if return_correct:
        out["_correct"] = correct.tolist()
        out["_optimal_correct"] = optimal_arr.tolist()
    return out


def evaluate(weights: Dict[str, Any], rows: Sequence[Dict[str, Any]],
             tools: Sequence[str],
             corpus_sha256: Optional[str] = None,
             return_correct: bool = False) -> Dict[str, Any]:
    """Score the manifest-weights MLP. Definitions live in `metrics_from_logits`."""
    import numpy as np
    if not rows:
        return {"rows": 0}
    x = np.array([r["x"] for r in rows], dtype="float64")
    return metrics_from_logits(forward(weights, x), rows, tools,
                               corpus_sha256=corpus_sha256,
                               return_correct=return_correct)


def paired_bootstrap(base_correct: Sequence[bool],
                     cand_correct: Sequence[bool],
                     *, iters: int = 2000, seed: int = 0) -> Dict[str, Any]:
    """Paired bootstrap of the accuracy delta on the same rows.

    The gate otherwise compares point estimates, and on 3,719 rows a point estimate
    moves by several tenths of a point for no reason: a retrain that gained 76 rows
    and lost 72 (+0.0011) promoted on a CI of [-0.0054, +0.0075]. Pairing the same
    rows and resampling them removes the shared difficulty that makes the two
    numbers correlated, which is the only way this delta can be read.
    """
    import numpy as np
    b = np.asarray(base_correct, dtype=bool)
    c = np.asarray(cand_correct, dtype=bool)
    if b.shape != c.shape or b.size == 0:
        return {"comparable": False, "rows": int(min(b.size, c.size))}
    delta = c.astype(np.float64) - b.astype(np.float64)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, b.size, size=(iters, b.size))
    means = delta[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"comparable": True, "rows": int(b.size),
            "delta": round(float(delta.mean()), 5),
            "lo": round(float(lo), 5), "hi": round(float(hi), 5),
            "gained": int((c & ~b).sum()), "lost": int((b & ~c).sum()),
            "significant": bool(lo > 0.0)}


def gate_decision(base_metrics: Dict[str, Any],
                  cand_metrics: Dict[str, Any],
                  *, min_macro_f1: Optional[float] = None,
                  paired: Optional[Dict[str, Any]] = None,
                  paired_optimal: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Promote only on measured improvement, never on one metric alone.

    Two guards here exist because the comparison is only meaningful when both
    sides were scored on the same bytes:

    * corpus identity -- `rows` alone let a candidate be compared against a base
      measured on a different corpus of equal length. When both metrics carry a
      `corpus_sha256`, a mismatch rejects the candidate.
    * absolute macro-F1 floor -- `macro_f1_common` only guards the common classes,
      so trading the whole rare tail for common accuracy used to pass.
    * paired significance -- a point-estimate delta of +0.0011 on 3,719 rows is
      noise. When `paired` is supplied (see `paired_bootstrap`), a candidate whose
      paired 95% interval includes zero is rejected no matter how the point
      estimates compare.

    Significance is required on **either** label accuracy or task optimality. They
    are not the same question: 22% of rows have several tools at the same maximum
    gain, so the stored label is one of several equally good answers, and a policy
    trained on outcomes can be reliably better at the task while being no better at
    reproducing the teacher's tie-break. Demanding label agreement alone would
    reject exactly the model that learns the world instead of the label.
    """
    reasons: List[str] = []
    if cand_metrics.get("rows", 0) < base_metrics.get("rows", 1):
        reasons.append("candidate evaluated on fewer rows")
    base_corpus = base_metrics.get("corpus_sha256")
    cand_corpus = cand_metrics.get("corpus_sha256")
    if base_corpus and cand_corpus and base_corpus != cand_corpus:
        reasons.append("candidate scored on a different corpus (%s vs %s)"
                       % (cand_corpus[:12], base_corpus[:12]))
    base_reach = set(base_metrics.get("reachable_tools") or [])
    cand_reach = set(cand_metrics.get("reachable_tools") or [])
    lost = base_reach - cand_reach
    if lost:
        reasons.append("tools became unreachable: %s" % ", ".join(sorted(lost)))
    if cand_metrics.get("val_accuracy", 0) < base_metrics.get("val_accuracy", 0):
        reasons.append("accuracy regressed")
    if cand_metrics.get("decision", {}).get("expected_gain_model", 0) \
            <= base_metrics.get("decision", {}).get("expected_gain_model", 0):
        reasons.append("no decision-gain improvement")
    if cand_metrics.get("ece", 1) > base_metrics.get("ece", 0) + 0.02:
        reasons.append("calibration regressed beyond tolerance")
    if cand_metrics.get("macro_f1_common", 0) \
            < base_metrics.get("macro_f1_common", 0) - 0.01:
        reasons.append("common-class F1 regressed beyond tolerance")
    floor = MIN_MACRO_F1 if min_macro_f1 is None else min_macro_f1
    if floor and "macro_f1" in cand_metrics \
            and cand_metrics["macro_f1"] < floor:
        reasons.append("all-class macro F1 %.4f below floor %.4f"
                       % (cand_metrics["macro_f1"], floor))
    verdict: Dict[str, Any] = {"promote": not reasons, "reasons": reasons}
    if paired and paired.get("comparable") and paired.get("delta", 0.0) >= 0.0:
        # Only the noise case: a negative paired delta is already "accuracy
        # regressed", and calling a loss "a gain that is not significant" reads
        # like the candidate merely failed to improve.
        exact_sig = bool(paired.get("significant", False))
        optimal_sig = bool(paired_optimal and paired_optimal.get("comparable")
                           and paired_optimal.get("significant"))
        verdict["significant"] = {"accuracy": exact_sig, "optimal": optimal_sig}
        if not (exact_sig or optimal_sig):
            reasons.append(
                "no significant improvement in label accuracy (%+.5f, 95%% CI "
                "[%+.5f, %+.5f]) or task optimality"
                % (paired.get("delta", 0.0), paired.get("lo", 0.0),
                   paired.get("hi", 0.0)))
    verdict["promote"] = not reasons
    return verdict


class EditRejected(ValueError):
    pass


def apply_edits(manifest: Dict[str, Any], edits: Sequence[Dict[str, Any]],
                *, base_sha256: Optional[str] = None,
                max_changes: int = 20, max_abs_delta: float = 1.0,
                max_l2: float = 2.0,
                allowed_tensors: Optional[Sequence[str]] = None) -> tuple[Dict[str, Any], dict[str, Any]]:
    if base_sha256 and manifest["base"].get("sha256") != base_sha256:
        raise EditRejected("base sha256 mismatch: edit was made against other weights")
    if len(edits) > max_changes:
        raise EditRejected("%d changes exceeds budget %d" % (len(edits), max_changes))
    tensors = {t["name"]: t for t in manifest["tensors"]}
    allowed = set(allowed_tensors) if allowed_tensors else set(tensors)
    seen = set()
    l2 = 0.0
    for edit in edits:
        name = edit.get("tensor")
        if name not in tensors:
            raise EditRejected("unknown tensor %r" % name)
        if name not in allowed:
            raise EditRejected("tensor %r is not editable" % name)
        index = tuple(int(i) for i in edit.get("index") or [])
        if not index:
            raise EditRejected("empty index for %r" % name)
        shape = tensors[name]["shape"]
        if len(index) != len(shape) or any(i < 0 or i >= d for i, d in zip(index, shape)):
            raise EditRejected("index %s out of bounds for %s %s" % (index, name, shape))
        if (name, index) in seen:
            raise EditRejected("duplicate change to %s%s" % (name, index))
        seen.add((name, index))
        old = _get(tensors[name]["values"], index)
        if "old_value" in edit and abs(old - float(edit["old_value"])) > 1e-6:
            raise EditRejected("%s%s: old_value %r does not match %r"
                               % (name, index, edit["old_value"], old))
        new = float(edit.get("new_value", old))
        if not math.isfinite(new):
            raise EditRejected("%s%s: new_value is not finite" % (name, index))
        if abs(new - old) > max_abs_delta:
            raise EditRejected("%s%s: delta %.4f exceeds %.4f"
                               % (name, index, new - old, max_abs_delta))
        l2 += (new - old) ** 2
    if math.sqrt(l2) > max_l2:
        raise EditRejected("edit L2 %.4f exceeds %.4f" % (math.sqrt(l2), max_l2))
    new_manifest = json.loads(json.dumps(manifest))
    new_tensors = {t["name"]: t for t in new_manifest["tensors"]}
    diff = []
    for edit in edits:
        name, index = edit["tensor"], tuple(int(i) for i in edit["index"])
        old = _get(new_tensors[name]["values"], index)
        new = float(edit.get("new_value", old))
        _set(new_tensors[name]["values"], index, new)
        diff.append({"tensor": name, "index": list(index), "old": old, "new": new,
                     "reason": edit.get("reason", "")})
    report = {"changes": len(diff), "l2": round(math.sqrt(l2), 6),
              "max_abs_delta": max((abs(d["new"] - d["old"]) for d in diff), default=0.0),
              "diff": diff}
    return new_manifest, report


# =========================================================================== #
# publish candidate + rollback (base artifact is never overwritten)
# =========================================================================== #

def save_candidate_onnx(base_onnx: Path, manifest: Dict[str, Any],
                        out_path: Path) -> Path:
    """Copy the graph, replace only the initializer weights."""
    import onnx
    from onnx import numpy_helper
    import numpy as np
    model = onnx.load(str(base_onnx))
    by_name = {t["name"]: t for t in manifest["tensors"]}
    for i, tensor in enumerate(model.graph.initializer):
        if tensor.name in by_name:
            array = np.asarray(by_name[tensor.name]["values"], dtype="float32")
            model.graph.initializer[i].CopyFrom(
                numpy_helper.from_array(array, name=tensor.name))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(out_path))
    return out_path


def publish_candidate(delta_id: str, manifest: Dict[str, Any],
                      diff_report: Dict[str, Any],
                      base_metrics: Dict[str, Any],
                      cand_metrics: Dict[str, Any],
                      edits: Sequence[Dict[str, Any]],
                      *, base_onnx: Path = BASE_ONNX,
                      edits_dir: Path = EDITS_DIR,
                      paired: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in delta_id)
    folder = Path(edits_dir) / safe_id
    folder.mkdir(parents=True, exist_ok=True)
    candidate = save_candidate_onnx(base_onnx, manifest, folder / "candidate.onnx")
    (folder / "edit.json").write_text(json.dumps(
        {"delta_id": safe_id, "edits": list(edits), "diff": diff_report},
        ensure_ascii=False, indent=2), encoding="utf-8")
    decision = gate_decision(base_metrics, cand_metrics, paired=paired)
    record = {
        "delta_id": safe_id,
        "base_sha256": manifest["base"]["sha256"],
        "candidate_sha256": sha256_file(candidate),
        "candidate": str(candidate),
        "base_metrics": base_metrics,
        "candidate_metrics": cand_metrics,
        "paired": paired,
        "gate": decision,
        "status": "accepted" if decision["promote"] else "rejected",
    }
    (folder / "report.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def rollback_candidate(delta_id: str, *, base_onnx: Path = BASE_ONNX,
                       edits_dir: Path = EDITS_DIR) -> Dict[str, Any]:
    """Remove a candidate; verify the base artifact is byte-identical."""
    folder = Path(edits_dir) / delta_id
    base_hash = sha256_file(base_onnx)
    if not folder.is_dir():
        return {"rolled_back": False, "reason": "no_such_candidate",
                "base_sha256": base_hash}
    shutil.rmtree(folder)
    return {"rolled_back": True, "removed": str(folder),
            "base_sha256": base_hash,
            "base_intact": base_hash == inventory()["sha256"]}


# =========================================================================== #
# skill spec: a prompt becomes a validated spec, never raw weights
# =========================================================================== #

SKILL_FIELDS = {"objective", "allowed_tools", "forbidden_tools", "level_band",
                "risk", "horizon_minutes", "success_metrics"}


def validate_skill_spec(spec: Dict[str, Any],
                        tools: Sequence[str]) -> List[str]:
    problems: List[str] = []
    unknown = set(spec) - SKILL_FIELDS
    if unknown:
        problems.append("unknown fields: %s" % sorted(unknown))
    if not spec.get("objective"):
        problems.append("objective is required")
    allowed = set(spec.get("allowed_tools") or [])
    forbidden = set(spec.get("forbidden_tools") or [])
    if not allowed:
        problems.append("allowed_tools must be non-empty")
    bad = (allowed | forbidden) - set(tools)
    if bad:
        problems.append("tools not in registry: %s" % sorted(bad))
    if allowed & forbidden:
        problems.append("tool both allowed and forbidden: %s"
                        % sorted(allowed & forbidden))
    if spec.get("risk") not in (None, "low", "medium", "high"):
        problems.append("risk must be low|medium|high")
    return problems


def editor_prompt(manifest: Dict[str, Any], unit_manifest: Dict[str, Any],
                  spec: Dict[str, Any], metrics: Dict[str, Any],
                  *, max_changes: int = 20) -> Dict[str, Any]:
    """The exact payload an offline LLM editor receives. It returns JSON edits;
    it never sees the evaluation split or the live loop."""
    return {
        "task": ("Propose a sparse weight edit to this tool-policy head so it "
                 "re-ranks legal tools better for the skill spec. Return JSON "
                 "edits only; do not retrain, do not invent tools."),
        "skill_spec": spec,
        "base_sha256": manifest["base"]["sha256"],
        "params": manifest["base"]["params"],
        "feature_names": manifest["base"]["feature_names"],
        "tools": manifest["base"]["tools"],
        "current_metrics": {k: metrics.get(k) for k in
                            ("val_accuracy", "macro_f1_common", "ece")},
        "confusion_top": metrics.get("confusion_top", [])[:6],
        "unit_manifest": unit_manifest,
        "edit_budget": {"max_changes": max_changes, "max_abs_delta": 1.0,
                        "max_l2": 2.0,
                        "editable_tensors": [t["name"] for t in manifest["tensors"]]},
        "rules": [
            "change only listed tensors, index in bounds",
            "old_value must equal the current weight",
            "never change the tool order, feature order, or graph",
            "safety, legality and stop conditions are not weights -- do not touch",
        ],
        "output_schema": {"edits": [{"tensor": "4.bias", "index": [3],
                                      "old_value": 0.0, "new_value": 0.1,
                                      "reason": "why"}]},
    }


# =========================================================================== #
# CLI
# =========================================================================== #

def split_provenance(path: Optional[Path] = None) -> Dict[str, Any]:
    """What these numbers were scored on, and what the shipped report was scored on.

    One head published two expected gains 1.6x apart: 1.2553 in
    `models/tool_policy_report.json` (quoted by the weight-space plan) and 0.7977
    in the 2026-11-04 replay. Same formula, different corpus -- the report scored
    8,453 val rows of the v2+v2b shards, and `data/tool_sft_v3/val.jsonl`, the path
    it names, now holds 3,719. A path is not a dataset, so the replay says which
    bytes it read and whether the old number is comparable at all.
    """
    val = DEFAULT_VAL if path is None else Path(path)
    out: Dict[str, Any] = {"val": str(val), "val_rows": 0, "val_sha256": None}
    try:
        with val.open(encoding="utf-8") as handle:
            out["val_rows"] = sum(1 for line in handle if line.strip())
        out["val_sha256"] = sha256_file(val)
    except OSError as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
        return out
    try:
        report = json.loads(Path(BASE_REPORT).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["base_report"] = "unreadable (%s)" % type(exc).__name__
        return out
    decision = report.get("decision") or {}
    base = {"val_rows": report.get("val_rows"),
            "val_split": report.get("val_split"),
            "data_path": report.get("data"),
            "val_accuracy": report.get("val_accuracy"),
            "expected_gain_model": decision.get("expected_gain_model"),
            "gain_capture": decision.get("gain_capture")}
    out["base_report"] = base
    out["comparable"] = base.get("val_rows") == out["val_rows"]
    if not out["comparable"]:
        out["note"] = (
            "the shipped report scored %s val rows and this split has %s (sha256 %s), "
            "so the report's expected gain and gain capture describe a corpus that no "
            "longer exists at that path -- use the numbers in this file"
            % (base.get("val_rows"), out["val_rows"], out["val_sha256"][:12]))
    return out


def corpus_identity(path: Optional[Path] = None) -> Dict[str, Any]:
    """Which bytes are being scored: path, row count, sha256. A path is not a dataset."""
    val = DEFAULT_VAL if path is None else Path(path)
    out: Dict[str, Any] = {"path": str(val), "rows": 0, "sha256": None}
    try:
        with val.open(encoding="utf-8") as handle:
            out["rows"] = sum(1 for line in handle if line.strip())
        out["sha256"] = sha256_file(val)
    except OSError as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def pin_provenance(path: Optional[Path] = None,
                   out: Optional[Path] = None) -> Dict[str, Any]:
    """Freeze the corpus identity and the base model's numbers on it.

    Before this existed, `apply` recomputed `base_metrics` live on every run, so
    the baseline silently followed whatever `val.jsonl` happened to contain that
    day. The shipped report scored 8,453 rows; the path now holds 3,719. Pinning
    makes the comparison explicit: the gate is scored against the bytes recorded
    here, and `apply` refuses to run when the corpus no longer matches.
    """
    val = DEFAULT_VAL if path is None else Path(path)
    out = PROVENANCE_PATH if out is None else Path(out)
    corpus = corpus_identity(val)
    if corpus.get("sha256") is None:
        raise ValueError("cannot pin an unreadable corpus: %s"
                         % corpus.get("error", corpus["path"]))
    manifest = export_manifest()
    rows = read_val_rows(val)
    metrics = score_artifact(manifest, rows, BASE_ONNX,
                             corpus_sha256=corpus["sha256"])
    metrics.pop("confusion_top", None)
    shipped = split_provenance(val)
    # `split_provenance` reports a shipped report it cannot read as the string
    # "unreadable (FileNotFoundError)". A clean clone has no
    # models/tool_policy_report.json, and calling `.get` on that string raised
    # AttributeError -- which took the whole suite down instead of recording one
    # missing input. Keep the string as the note; it is the honest answer.
    raw_report = shipped.get("base_report")
    base_report = raw_report if isinstance(raw_report, dict) else {}
    note = shipped.get("note") or (raw_report if isinstance(raw_report, str) else None)
    record = {
        "kind": "weight-space-provenance",
        "version": 1,
        "pinned_at": datetime.now().isoformat(timespec="seconds"),
        "corpus": corpus,
        "base": {
            "onnx": str(BASE_ONNX),
            "onnx_sha256": manifest["base"]["sha256"],
            "params": manifest["base"]["params"],
        },
        "metrics": metrics,
        "shipped_report": {
            "val_rows": base_report.get("val_rows"),
            "val_accuracy": base_report.get("val_accuracy"),
            "expected_gain_model": base_report.get("expected_gain_model"),
            "comparable": shipped.get("comparable"),
            "note": note,
        },
    }
    Path(out).write_text(json.dumps(record, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return record


def check_pin(path: Optional[Path] = None,
              pin_path: Optional[Path] = None) -> Dict[str, Any]:
    """Does the corpus on disk still match the pinned bytes?"""
    live = corpus_identity(path)
    pin_path = PROVENANCE_PATH if pin_path is None else Path(pin_path)
    try:
        pin = json.loads(Path(pin_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pinned": False, "match": False, "corpus": live,
                "reasons": ["no provenance pin recorded at %s" % pin_path]}
    expected = pin.get("corpus") or {}
    reasons: List[str] = []
    if live.get("sha256") is None:
        reasons.append("corpus unreadable: %s" % live.get("error"))
    elif live.get("sha256") != expected.get("sha256"):
        reasons.append("corpus sha256 drifted: pinned %s, found %s"
                       % (str(expected.get("sha256"))[:12],
                          str(live.get("sha256"))[:12]))
    if live.get("rows") != expected.get("rows"):
        reasons.append("corpus rows drifted: pinned %s, found %s"
                       % (expected.get("rows"), live.get("rows")))
    return {"pinned": True, "match": not reasons, "corpus": live,
            "expected": expected, "pinned_at": pin.get("pinned_at"),
            "reasons": reasons}


def score_artifact(manifest: Dict[str, Any], rows: Sequence[Dict[str, Any]],
                   onnx_path: Path = BASE_ONNX,
                   corpus_sha256: Optional[str] = None,
                   return_correct: bool = False) -> Dict[str, Any]:
    """Score whatever artifact the manifest describes.

    `evaluate` reimplements a dense stack in numpy, which is exact for the MLP and
    wrong for anything else -- a GRU would be silently scored by the wrong function.
    The arch field in the sidecar decides, so installing a different architecture
    cannot quietly produce a number about a model that is not running.
    """
    base = manifest["base"]
    tools = list(base["tools"])
    if (base.get("arch") or "mlp") != "mlp":
        logits = _onnx_logits(Path(onnx_path), rows, base)
        return metrics_from_logits(logits, rows, tools,
                                   corpus_sha256=corpus_sha256,
                                   return_correct=return_correct)
    return evaluate(manifest_weights(manifest), rows, tools,
                    corpus_sha256=corpus_sha256, return_correct=return_correct)


def _history_rows(rows: Sequence[Dict[str, Any]], obs: bool = False,
                  derived: bool = False) -> List[List[float]]:
    """The history block for each row, aligned to `rows` order.

    Rebuilt with the same function the trainer and the runtime tracker use, so a
    candidate is scored on exactly the input it was trained on. Rows are grouped by
    episode and read in step order, because the file order is not a contract.

    `obs` selects the widened token: each record then carries the observation summary
    of the row it followed, read out of that row's own state vector. The runtime gets
    the same numbers from `ObsTracker.note_observation`, which reads the identical
    indices, so the two sides cannot disagree about what a past step looked like.
    """
    from agent.features import (HISTORY_DIM, HISTORY_DIM_OBS_DERIVED, HISTORY_STEPS,
                                _err_slot, history_tokens, obs_summary)
    from agent.features import HISTORY_DIM_OBS
    width = (HISTORY_DIM_OBS_DERIVED if derived
             else (HISTORY_DIM_OBS if obs else HISTORY_DIM))
    grouped: Dict[Any, List[Tuple[int, int]]] = {}
    for i, row in enumerate(rows):
        grouped.setdefault(row.get("ep", i), []).append((int(row.get("t", 0)), i))
    out: List[Optional[List[float]]] = [None] * len(rows)
    for items in grouped.values():
        items.sort()
        window: List[Dict[str, Any]] = []
        for _, i in items:
            out[i] = history_tokens(window, obs=obs, derived=derived)
            row = rows[i]
            rec = {"tool": row.get("tool"), "ok": bool(row.get("ok")),
                   "err": _err_slot(row.get("note"))}
            if obs:
                rec["summary"] = obs_summary(row.get("x") or [])
            window.append(rec)
            if len(window) > HISTORY_STEPS:
                window = window[-HISTORY_STEPS:]
    return [v if v is not None else [0.0] * width for v in out]


def _onnx_logits(onnx_path: Path, rows: Sequence[Dict[str, Any]],
                 base: Dict[str, Any]) -> Any:
    """Run a candidate graph over the corpus, whatever its architecture.

    `forward` is a numpy reimplementation of a dense stack, so it can only score the
    MLP it was written for. A sequence policy has a GRU in it; the honest way to
    score that is to run the graph that will actually be deployed.
    """
    import numpy as np
    import onnxruntime as ort
    hist = base.get("history") or {}
    steps = int(hist.get("steps") or 0)
    # The token width is read from the artifact, not assumed: 28 is the shipped
    # action-only token and 33 adds the per-step observation summary. A graph scored
    # with the wrong width would either fail loudly or, worse, line up by accident.
    from agent.features import HISTORY_TOKEN, HISTORY_TOKEN_OBS
    obs = int(hist.get("token") or HISTORY_TOKEN) == HISTORY_TOKEN_OBS
    derived = bool(hist.get("derived"))
    state = np.array([r["x"] for r in rows], dtype="float32")
    if steps:
        X = np.hstack([state, np.array(_history_rows(rows, obs=obs, derived=derived),
                                       dtype="float32")])
    else:
        X = state
    session = ort.InferenceSession(str(onnx_path),
                                   providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name
    return session.run(["logits"], {name: X})[0]


def candidate_verdict(cand_onnx: Path, val: Path = DEFAULT_VAL) -> Dict[str, Any]:
    """Score a separately trained artifact and decide, without printing.

    `_replay_candidate` is the CLI around this; `promote` is the install step. Both
    must reach the same verdict from the same numbers, so the comparison lives here
    once.
    """
    try:
        pin = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"error": "no provenance pin; run `pin` first",
                "pin": str(PROVENANCE_PATH)}
    base_metrics = pin.get("metrics") or {}
    if not base_metrics:
        return {"error": "pin carries no metrics"}

    sidecar = cand_onnx.with_suffix(".json")
    if not sidecar.is_file():
        sidecar = BASE_SIDECAR
    manifest = export_manifest(cand_onnx, sidecar)
    tools = list(manifest["base"]["tools"])
    base_tools = list(export_manifest()["base"]["tools"])
    if set(tools) != set(base_tools):
        return {"error": "candidate tool set differs from the baseline",
                "only_candidate": sorted(set(tools) - set(base_tools)),
                "only_base": sorted(set(base_tools) - set(tools))}

    corpus = corpus_identity(val)
    rows = read_val_rows(val)
    arch = (manifest["base"].get("arch") or "mlp")
    cand_eval = score_artifact(manifest, rows, Path(cand_onnx),
                               corpus_sha256=corpus.get("sha256"),
                               return_correct=True)
    base_eval = score_artifact(export_manifest(), rows, BASE_ONNX,
                               return_correct=True)
    paired = paired_bootstrap(base_eval["_correct"], cand_eval["_correct"])
    paired_optimal = paired_bootstrap(base_eval.get("_optimal_correct") or [],
                                      cand_eval.get("_optimal_correct") or [])
    gate = gate_decision(base_metrics, cand_eval, paired=paired,
                         paired_optimal=paired_optimal)
    base_reach = set(base_metrics.get("reachable_tools") or [])
    cand_reach = set(cand_eval["reachable_tools"])
    return {
        "candidate": str(cand_onnx),
        "candidate_sha256": manifest["base"]["sha256"],
        "arch": arch,
        "params": manifest["base"].get("params"),
        "corpus": {"rows": corpus.get("rows"), "sha256": corpus.get("sha256")},
        "base": {k: base_metrics.get(k) for k in
                 ("rows", "val_accuracy", "optimal", "macro_f1",
                  "macro_f1_common", "ece")},
        "candidate_metrics": {k: cand_eval[k] for k in
                              ("rows", "val_accuracy", "optimal", "macro_f1",
                               "macro_f1_common", "ece")},
        "paired": paired,
        "paired_optimal": paired_optimal,
        "decision": cand_eval["decision"],
        "reachable": {"count": len(cand_reach),
                      "gained": sorted(cand_reach - base_reach),
                      "lost": sorted(base_reach - cand_reach)},
        "gate": gate,
    }


def _replay_candidate(cand_onnx: Path, val: Path) -> int:
    """Score a separately trained head against the pinned baseline.

    A retrain is not an edit: it has a new base hash and no edit budget. It is
    still gated on the same corpus, the same thresholds and the same reachability
    rule, so a retrain cannot be promoted on a metric an edit would be denied.
    """
    verdict = candidate_verdict(cand_onnx, val)
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    if "error" in verdict:
        return 3 if "pin" in verdict["error"] else 4
    return 0 if verdict["gate"]["promote"] else 2


def install_candidate(cand_onnx: Path, *, base_onnx: Path = BASE_ONNX,
                      base_sidecar: Path = BASE_SIDECAR,
                      backup_dir: Path = BACKUPS_DIR) -> Dict[str, Any]:
    """Replace the shipped artifact, keeping the old one byte-identical.

    Nothing is overwritten in place: the current model is copied into
    `models/backups/tool_policy.<sha12>.onnx` first, so `restore` can always put the
    previous one back without a rebuild.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = sha256_file(base_onnx)[:12]
    saved_onnx = backup_dir / ("tool_policy.%s.onnx" % stamp)
    saved_side = backup_dir / ("tool_policy.%s.json" % stamp)
    shutil.copy2(base_onnx, saved_onnx)
    if Path(base_sidecar).is_file():
        shutil.copy2(base_sidecar, saved_side)
    shutil.copy2(cand_onnx, base_onnx)
    cand_side = Path(cand_onnx).with_suffix(".json")
    if cand_side.is_file():
        shutil.copy2(cand_side, base_sidecar)
    return {"installed": str(base_onnx), "backup": str(saved_onnx),
            "backup_sidecar": str(saved_side) if saved_side.is_file() else None,
            "installed_sha256": sha256_file(base_onnx)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    from agent.jsonio import utf8_console

    # Every subcommand prints game text with ensure_ascii=False: an accented quest
    # line on a cp1252 console is a UnicodeEncodeError in the middle of the report.
    utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory")
    p = sub.add_parser("export"); p.add_argument("--out", default="models/tool_policy.manifest.json")
    p = sub.add_parser("replay"); p.add_argument("--val", default=str(DEFAULT_VAL))
    p.add_argument("--candidate", default=None,
                   help="score this ONNX against the pinned baseline and print the gate verdict")
    p = sub.add_parser("pin"); p.add_argument("--val", default=str(DEFAULT_VAL))
    p.add_argument("--out", default=str(PROVENANCE_PATH))
    p = sub.add_parser("verify"); p.add_argument("--val", default=str(DEFAULT_VAL))
    p = sub.add_parser("apply")
    p.add_argument("edit_file"); p.add_argument("--delta-id", required=True)
    p.add_argument("--max-changes", type=int, default=20)
    p.add_argument("--allow-unpinned", action="store_true",
                   help="score against the live corpus even if it drifted from the pin")
    p = sub.add_parser("rollback"); p.add_argument("delta_id")
    p = sub.add_parser("promote")
    p.add_argument("onnx")
    p.add_argument("--val", default=str(DEFAULT_VAL))
    p.add_argument("--force", action="store_true",
                   help="install even if the gate rejects it (recorded in the pin)")
    p = sub.add_parser("restore")
    p.add_argument("--backup", default=None,
                   help="backup file to restore; default is the newest")
    p = sub.add_parser("editor-prompt")
    p.add_argument("spec_file")
    p.add_argument("--out", default="models/weight_edits/editor_prompt.json")
    args = parser.parse_args(argv)

    if args.command == "inventory":
        print(json.dumps(inventory(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "export":
        manifest = export_manifest()
        problems = validate_manifest(manifest)
        if problems:
            print("manifest problems: %s" % problems); return 1
        Path(args.out).write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        print("exported %d tensors (%d params) -> %s"
              % (len(manifest["tensors"]), manifest["base"]["params"], args.out))
        return 0
    if args.command == "replay":
        if args.candidate:
            return _replay_candidate(Path(args.candidate), Path(args.val))
        manifest = export_manifest()
        rows = read_val_rows(args.val)
        corpus = corpus_identity(Path(args.val))
        metrics = score_artifact(manifest, rows, BASE_ONNX,
                                 corpus_sha256=corpus.get("sha256"))
        replay = {k: metrics[k] for k in
                  ("rows", "val_accuracy", "optimal", "macro_f1", "macro_f1_common",
                   "ece", "decision", "confusion_top",
                   "reachable_tools", "unreachable_tools")}
        replay["split"] = split_provenance(Path(args.val))
        replay["pin"] = check_pin(Path(args.val))
        print(json.dumps(replay, ensure_ascii=False, indent=2))
        return 0
    if args.command == "promote":
        verdict = candidate_verdict(Path(args.onnx), Path(args.val))
        if "error" in verdict:
            print(json.dumps(verdict, ensure_ascii=False, indent=2))
            return 3
        passed = bool(verdict["gate"]["promote"])
        if not passed and not args.force:
            print(json.dumps({
                "promoted": False, "gate": verdict["gate"],
                "hint": "the gate rejected this artifact; --force installs it "
                        "anyway and records that it was forced",
            }, ensure_ascii=False, indent=2))
            return 2
        installed = install_candidate(Path(args.onnx))
        record = pin_provenance(Path(args.val), Path(PROVENANCE_PATH))
        print(json.dumps({
            "promoted": True, "forced": not passed,
            "arch": verdict.get("arch"), "params": verdict.get("params"),
            "gate": verdict["gate"], **installed,
            "new_baseline": {k: record["metrics"][k] for k in
                             ("val_accuracy", "optimal", "macro_f1", "ece")},
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "restore":
        backups = sorted(BACKUPS_DIR.glob("tool_policy.*.onnx"),
                         key=lambda p: p.stat().st_mtime)
        if not backups:
            print(json.dumps({"error": "no backups in %s" % BACKUPS_DIR}, indent=2))
            return 3
        target = Path(args.backup) if args.backup else backups[-1]
        if not target.is_file():
            print(json.dumps({"error": "no such backup: %s" % target}, indent=2))
            return 3
        side = target.with_suffix(".json")
        shutil.copy2(target, BASE_ONNX)
        if side.is_file():
            shutil.copy2(side, BASE_SIDECAR)
        record = pin_provenance(Path(args.val), Path(PROVENANCE_PATH))
        print(json.dumps({"restored": str(target), "to": str(BASE_ONNX),
                          "baseline": {k: record["metrics"][k] for k in
                                       ("val_accuracy", "optimal", "macro_f1")}},
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "pin":
        record = pin_provenance(Path(args.val), Path(args.out))
        print(json.dumps({
            "pinned_at": record["pinned_at"],
            "corpus": record["corpus"],
            "metrics": {k: record["metrics"][k] for k in
                        ("rows", "val_accuracy", "macro_f1",
                         "macro_f1_common", "ece")},
            "out": str(args.out),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "verify":
        status = check_pin(Path(args.val))
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status["match"] else 1
    if args.command == "apply":
        payload = json.loads(Path(args.edit_file).read_text(encoding="utf-8"))
        edits = payload.get("edits") if isinstance(payload, dict) else payload
        manifest = export_manifest()
        pin = check_pin()
        if not pin["match"] and not args.allow_unpinned:
            print(json.dumps({
                "delta_id": args.delta_id, "status": "blocked",
                "reason": "validation corpus does not match the pinned baseline",
                "pin": pin,
                "fix": "run `python -m agent.weight_space pin` to re-baseline, "
                       "or pass --allow-unpinned to score against the live corpus",
            }, ensure_ascii=False, indent=2))
            return 3
        rows = read_val_rows()
        # Weight-space editing writes coordinates of a dense stack. A recurrent
        # policy has no such coordinate system, so an edit applied to it would be a
        # silent no-op dressed up as a promotion. Say so instead.
        if (manifest["base"].get("arch") or "mlp") != "mlp":
            print(json.dumps({
                "error": "base artifact is %s, not a dense MLP"
                         % manifest["base"].get("arch"),
                "hint": "weight-space edits do not apply to a sequence policy; "
                        "retrain and score it with `replay --candidate`",
            }, indent=2))
            return 5
        # Stamp the bytes actually scored: the pin's hash only describes these
        # rows when the pin matched, otherwise --allow-unpinned is scoring the
        # live corpus and the record must say so.
        corpus_sha = (pin["corpus"].get("sha256") if pin["match"]
                      else corpus_identity().get("sha256"))
        base_metrics = evaluate(manifest_weights(manifest), rows,
                                manifest["base"]["tools"], corpus_sha256=corpus_sha,
                                return_correct=True)
        candidate, diff = apply_edits(manifest, edits, max_changes=args.max_changes,
                                      base_sha256=manifest["base"]["sha256"])
        cand_metrics = evaluate(manifest_weights(candidate), rows,
                                manifest["base"]["tools"], corpus_sha256=corpus_sha,
                                return_correct=True)
        paired = paired_bootstrap(base_metrics.pop("_correct"),
                                  cand_metrics.pop("_correct"))
        record = publish_candidate(args.delta_id, candidate, diff, base_metrics,
                                   cand_metrics, edits, paired=paired)
        print(json.dumps({"delta_id": record["delta_id"], "status": record["status"],
                          "gate": record["gate"], "paired": paired,
                          "base": base_metrics["val_accuracy"],
                          "candidate": cand_metrics["val_accuracy"],
                          "corpus_sha256": corpus_sha},
                         ensure_ascii=False, indent=2))
        return 0 if record["status"] == "accepted" else 2
    if args.command == "rollback":
        print(json.dumps(rollback_candidate(args.delta_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "editor-prompt":
        spec = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
        manifest = export_manifest()
        tools = manifest["base"]["tools"]
        problems = validate_skill_spec(spec, tools)
        if problems:
            print("invalid skill spec: %s" % problems); return 1
        rows = read_val_rows()
        metrics = evaluate(manifest_weights(manifest), rows, tools)
        units = build_unit_manifest(manifest_weights(manifest),
                                    manifest["base"]["feature_names"], tools, rows)
        prompt = editor_prompt(manifest, units, spec, metrics)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(prompt, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print("editor prompt -> %s" % args.out)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

