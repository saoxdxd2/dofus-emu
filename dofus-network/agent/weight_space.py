"""Weight-space programming for the small learned heads (Phase 0 + 1).

An LLM (offline, between episodes -- never in the live loop) reads a small
head as data, proposes a sparse structured edit, and a deterministic gate
replays it before it may act. This module is that substrate:

* inventory: sha256 of the base ONNX + sidecar, tensor shapes, param count;
* export: ONNX initializers -> a readable JSON manifest (weights as numbers);
* unit manifest: which input features / output tools each hidden unit serves;
* edit applier: validates shape, range, budget and base hash, then applies;
* replay: forward pass + accuracy / F1 / ECE / expected-gain on val rows;
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
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODELS_DIR = REPO_ROOT / "models"
BASE_ONNX = MODELS_DIR / "tool_policy.onnx"
BASE_SIDECAR = MODELS_DIR / "tool_policy.json"
BASE_REPORT = MODELS_DIR / "tool_policy_report.json"
EDITS_DIR = MODELS_DIR / "weight_edits"
DEFAULT_VAL = REPO_ROOT / "data" / "tool_sft_v3" / "val.jsonl"
MANIFEST_KIND = "weight-space-manifest"
MANIFEST_VERSION = 1
COMMON_SUPPORT = 50


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_sidecar(path: Path = BASE_SIDECAR) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def feature_names() -> List[str]:
    try:
        from agent.features import FEATURE_NAMES
        return list(FEATURE_NAMES)
    except Exception:
        return []


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
        "feature_names": feature_names(),
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
            "feature_names": feature_names(),
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


def _gelu(z):
    import numpy as np
    return 0.5 * z * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))


def _activations(weights: Dict[str, Any], x):
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


def forward(weights: Dict[str, Any], x):
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


def _softmax(logits):
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


def evaluate(weights: Dict[str, Any], rows: Sequence[Dict[str, Any]],
             tools: Sequence[str]) -> Dict[str, Any]:
    """Accuracy / F1 / ECE / expected-gain -- the promotion gate's numbers."""
    import numpy as np
    if not rows:
        return {"rows": 0}
    x = np.array([r["x"] for r in rows], dtype="float64")
    y = np.array([int(r["y"]) for r in rows])
    logits = forward(weights, x)
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

    def gain_of_row(r: Dict[str, Any], tool_name: str) -> float:
        gains = r.get("gains") or {}
        return float(gains.get(tool_name, 0.0)) if isinstance(gains, dict) else 0.0

    model_gain = sum(gain_of_row(r, tools[int(p)]) for r, p in zip(rows, pred.tolist())) / len(rows)
    teacher_gain = sum(gain_of_row(r, str(r.get("teacher") or "")) for r in rows) / len(rows)
    oracle_gain = sum(gain_of_row(r, str(r.get("tool") or "")) for r in rows) / len(rows)
    capture = ((model_gain - teacher_gain) / (oracle_gain - teacher_gain)
               if oracle_gain != teacher_gain else 0.0)
    return {
        "rows": len(rows),
        "val_accuracy": round(float(correct.mean()), 4),
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


def gate_decision(base_metrics: Dict[str, Any],
                  cand_metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Promote only on measured improvement, never on one metric alone."""
    reasons: List[str] = []
    if cand_metrics.get("rows", 0) < base_metrics.get("rows", 1):
        reasons.append("candidate evaluated on fewer rows")
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
    return {"promote": not reasons, "reasons": reasons}


class EditRejected(ValueError):
    pass


def apply_edits(manifest: Dict[str, Any], edits: Sequence[Dict[str, Any]],
                *, base_sha256: Optional[str] = None,
                max_changes: int = 20, max_abs_delta: float = 1.0,
                max_l2: float = 2.0,
                allowed_tensors: Optional[Sequence[str]] = None):
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
                      edits_dir: Path = EDITS_DIR) -> Dict[str, Any]:
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in delta_id)
    folder = Path(edits_dir) / safe_id
    folder.mkdir(parents=True, exist_ok=True)
    candidate = save_candidate_onnx(base_onnx, manifest, folder / "candidate.onnx")
    (folder / "edit.json").write_text(json.dumps(
        {"delta_id": safe_id, "edits": list(edits), "diff": diff_report},
        ensure_ascii=False, indent=2), encoding="utf-8")
    decision = gate_decision(base_metrics, cand_metrics)
    record = {
        "delta_id": safe_id,
        "base_sha256": manifest["base"]["sha256"],
        "candidate_sha256": sha256_file(candidate),
        "candidate": str(candidate),
        "base_metrics": base_metrics,
        "candidate_metrics": cand_metrics,
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
        out["val_rows"] = sum(1 for line in val.open(encoding="utf-8") if line.strip())
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
    p = sub.add_parser("apply")
    p.add_argument("edit_file"); p.add_argument("--delta-id", required=True)
    p.add_argument("--max-changes", type=int, default=20)
    p = sub.add_parser("rollback"); p.add_argument("delta_id")
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
        manifest = export_manifest()
        rows = read_val_rows(args.val)
        metrics = evaluate(manifest_weights(manifest), rows, manifest["base"]["tools"])
        replay = {k: metrics[k] for k in
                  ("rows", "val_accuracy", "macro_f1", "macro_f1_common",
                   "ece", "decision", "confusion_top")}
        replay["split"] = split_provenance(Path(args.val))
        print(json.dumps(replay, ensure_ascii=False, indent=2))
        return 0
    if args.command == "apply":
        payload = json.loads(Path(args.edit_file).read_text(encoding="utf-8"))
        edits = payload.get("edits") if isinstance(payload, dict) else payload
        manifest = export_manifest()
        rows = read_val_rows()
        base_metrics = evaluate(manifest_weights(manifest), rows, manifest["base"]["tools"])
        candidate, diff = apply_edits(manifest, edits, max_changes=args.max_changes,
                                      base_sha256=manifest["base"]["sha256"])
        cand_metrics = evaluate(manifest_weights(candidate), rows, manifest["base"]["tools"])
        record = publish_candidate(args.delta_id, candidate, diff, base_metrics,
                                   cand_metrics, edits)
        print(json.dumps({"delta_id": record["delta_id"], "status": record["status"],
                          "gate": record["gate"],
                          "base": base_metrics["val_accuracy"],
                          "candidate": cand_metrics["val_accuracy"]},
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

