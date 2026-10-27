"""Fit and evaluate the candidate-level tool stack.

Base models
-----------
* tool policy: ``models/tool_policy.onnx`` -> logits over the 21 tools
* foresight:   ``models/foresight_v1/foresight.json`` -> gain / move / curve
  for every (state, candidate) pair

The meta-rankers are compared by whole-episode cross-validation, never fitted
on the base models' training split. A decision tree, a small gradient-boosted
forest, and a linear stack share the same inputs plus K-means state-cluster
distances. The best cross-validated model is activated only when gain capture
beats both bases and a majority of folds are ahead. Both failures and successes
are written into the artifact; an inactive artifact is useful evidence.

    python ai/train_stacker.py
    python ai/train_stacker.py --cv-folds 5 --clusters 4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from agent.ensemble import (BASE_FEATURES, DEFAULT_MODEL, KIND, VERSION,  # noqa: E402
                            candidate_features, fit_gbdt, fit_kmeans,
                            fit_listwise, fit_tree, gbdt_predict,
                            kmeans_distances, soft_targets, tree_predict)
from agent.foresight import load as load_foresight  # noqa: E402

DEFAULT_CORPUS = REPO_ROOT / "data" / "tool_sft_v3" / "val.jsonl"
DEFAULT_CLUSTER_CORPUS = REPO_ROOT / "data" / "tool_sft_v3" / "train.jsonl"
DEFAULT_POLICY = REPO_ROOT / "models" / "tool_policy.onnx"
DEFAULT_POLICY_META = REPO_ROOT / "models" / "tool_policy.json"
DEFAULT_FORESIGHT = REPO_ROOT / "models" / "foresight_v1" / "foresight.json"


def _digest(path: Path) -> Dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError:
        return {"path": str(path), "missing": True}
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()[:16],
            "bytes": len(data)}


def load_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row.get("x"), list) and isinstance(row.get("gains"), dict):
                rows.append(row)
    return rows


def _policy_logits(session: Any, input_name: str, x: np.ndarray,
                   batch: int = 4096) -> np.ndarray:
    chunks: List[np.ndarray] = []
    for start in range(0, len(x), batch):
        chunks.append(np.asarray(
            session.run(None, {input_name: x[start:start + batch]})[0],
            dtype=np.float64))
    return np.vstack(chunks) if chunks else np.zeros((0, 0), dtype=np.float64)


def _state_choice(states: Sequence[Dict[str, Any]], key: str) -> Dict[str, Any]:
    capture = 0.0
    oracle = 0.0
    top1 = 0
    chosen_tools: Dict[str, int] = defaultdict(int)
    ranked = 0
    for state in states:
        gains = state["gains"]
        best = max(gains)
        if best <= 0:
            continue
        ranked += 1
        values = state[key]
        choice = max(range(len(values)), key=lambda index: values[index])
        chosen_tools[state["names"][choice]] += 1
        capture += gains[choice]
        oracle += best
        top1 += int(abs(gains[choice] - best) < 1e-12)
    return {
        "states": ranked,
        "gain_capture": round(capture / oracle, 6) if oracle > 0 else None,
        "top1": round(top1 / ranked, 6) if ranked else None,
        "top_tools": dict(sorted(chosen_tools.items(),
                                 key=lambda pair: -pair[1])[:6]),
    }


def base_metrics(states: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "policy": _state_choice(states, "policy"),
        "foresight_gain": _state_choice(states, "gain"),
    }


def attach_scores(states: Sequence[Dict[str, Any]],
                  model_type: str, model: Dict[str, Any]) -> None:
    if model_type == "tree":
        for state in states:
            state["model"] = [tree_predict(model["tree"], row)
                              for row in state["features"]]
    elif model_type == "gbdt":
        forest = model["forest"]
        for state in states:
            state["model"] = [gbdt_predict(forest, row) for row in state["features"]]
    else:
        weights = model["weights"]
        for state in states:
            state["model"] = [sum(weight * value for weight, value in
                                  zip(weights, row)) for row in state["features"]]


def model_metrics(states: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return _state_choice(states, "model")


def _tree_leaves(node: Dict[str, Any]) -> int:
    if "value" in node:
        return 1
    return _tree_leaves(node["left"]) + _tree_leaves(node["right"])


def fit_models(states: Sequence[Dict[str, Any]],
               args: argparse.Namespace) -> Tuple[Dict[str, Any], List[float]]:
    weights = fit_listwise([state["features"] for state in states],
                           [state["target"] for state in states],
                           [state["weight"] for state in states],
                           l2=args.l2, iterations=args.iterations,
                           learning_rate=args.learning_rate)
    flat_features = [row for state in states for row in state["features"]]
    flat_targets = [value for state in states for value in state["target"]]
    flat_weights = [value for state in states
                    for value in [state["weight"]] * len(state["features"])]
    tree = fit_tree(flat_features, flat_targets, flat_weights,
                    depth=args.tree_depth, min_leaf=args.tree_min_leaf)
    forest = fit_gbdt(flat_features, flat_targets, flat_weights,
                      rounds=args.boosting_rounds, depth=args.boosting_depth,
                      min_leaf=args.boosting_min_leaf,
                      learning_rate=args.boosting_rate)
    return {
        "linear": {"weights": weights, "complexity": 4},
        "tree": {"tree": tree, "complexity": _tree_leaves(tree)},
        "gbdt": {"forest": forest,
                 "complexity": sum(_tree_leaves(one)
                                   for one in forest["trees"])},
    }, weights


def _fold_metrics(states: Sequence[Dict[str, Any]],
                  models: Dict[str, Any]) -> Dict[str, Any]:
    out = base_metrics(states)
    for name, model in models.items():
        attach_scores(states, name, model)
        out[name] = model_metrics(states)
        out[name]["leaves"] = model["complexity"]
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--cluster-corpus", type=Path,
                        default=DEFAULT_CLUSTER_CORPUS)
    parser.add_argument("--clusters", type=int, default=4)
    parser.add_argument("--shortlist", type=int, default=6)
    parser.add_argument("--policy-model", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--policy-meta", type=Path, default=DEFAULT_POLICY_META)
    parser.add_argument("--foresight-model", type=Path, default=DEFAULT_FORESIGHT)
    parser.add_argument("--out", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--cv-folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--iterations", type=int, default=1600)
    parser.add_argument("--learning-rate", type=float, default=0.06)
    parser.add_argument("--l2", type=float, default=0.02)
    parser.add_argument("--tree-depth", type=int, default=5)
    parser.add_argument("--tree-min-leaf", type=int, default=20)
    parser.add_argument("--boosting-rounds", type=int, default=40)
    parser.add_argument("--boosting-depth", type=int, default=3)
    parser.add_argument("--boosting-min-leaf", type=int, default=25)
    parser.add_argument("--boosting-rate", type=float, default=0.05)
    parser.add_argument("--force-active", action="store_true",
                        help="activate even when the held-out gate fails")
    args = parser.parse_args(list(argv) if argv is not None else None)

    import onnxruntime as ort

    if not Path(args.corpus).is_file():
        raise SystemExit("no corpus at %s" % args.corpus)
    if not Path(args.policy_model).is_file():
        raise SystemExit("no tool policy at %s" % args.policy_model)
    net = load_foresight(Path(args.foresight_model))
    if net is None:
        raise SystemExit("foresight artifact could not be loaded")
    meta = json.loads(Path(args.policy_meta).read_text(encoding="utf-8"))
    tools = [str(tool) for tool in meta.get("tools") or []]
    if not tools:
        raise SystemExit("tool policy metadata has no tools")

    rows = load_rows(Path(args.corpus))
    if not rows:
        raise SystemExit("no usable rows in %s" % args.corpus)

    session = ort.InferenceSession(str(args.policy_model),
                                   providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    expected = int(session.get_inputs()[0].shape[1])
    if len(rows[0]["x"]) != expected:
        raise SystemExit("policy wants %d features, corpus has %d"
                         % (expected, len(rows[0]["x"])))

    all_x = np.asarray([row["x"] for row in rows], dtype=np.float32)
    logits = _policy_logits(session, input_name, all_x)
    cluster_rows = load_rows(Path(args.cluster_corpus))
    if not cluster_rows:
        raise SystemExit("no clustering rows at %s" % args.cluster_corpus)
    cluster_x = np.asarray([row["x"] for row in cluster_rows], dtype=np.float64)
    if cluster_x.shape[1] != expected:
        raise SystemExit("cluster corpus has %d features, policy wants %d"
                         % (cluster_x.shape[1], expected))
    kmeans = fit_kmeans(cluster_x, clusters=max(1, int(args.clusters)),
                        seed=int(args.seed))
    feature_names = (list(BASE_FEATURES)
                     + ["cluster_%d" % index
                        for index in range(int(kmeans["clusters"]))])

    def make(indices: List[int]) -> List[Dict[str, Any]]:
        subset = [rows[i] for i in indices]
        sub_logits = logits[indices]
        states: List[Dict[str, Any]] = []
        for local, row in enumerate(subset):
            names = [tool for tool in tools if tool in row["gains"]]
            if len(names) < 2:
                continue
            vectors = [net.vector([float(value) for value in row["x"]], tool)
                       for tool in names]
            if any(vector is None for vector in vectors):
                continue
            scored = net.score_vector(np.asarray(vectors, dtype=np.float32))
            cells = {tool: cell for tool, cell in zip(names, scored)}
            context = kmeans_distances(kmeans, row["x"])
            kept, matrix = candidate_features(
                sub_logits[local], cells, tools, context=context)
            if len(kept) < 2:
                continue
            gains = [float(row["gains"].get(tool) or 0.0) for tool in kept]
            teacher = kept.index(row["teacher"]) if row.get("teacher") in kept else None
            margin = float(row.get("margin") or 0.0)
            weight = 0.20 if margin <= 0.0 else (0.60 if margin <= 0.10 else 1.0)
            weight *= 0.35 if row.get("trivial") else 1.0
            states.append({
                "ep": str(row.get("ep")),
                "names": kept,
                "gains": gains,
                "target": soft_targets(gains, teacher),
                "weight": weight,
                "features": matrix,
                "policy": [float(sub_logits[local][tools.index(tool)]) for tool in kept],
                "policy_logp": [float(value[0]) for value in matrix],
                "gain": [float(value[2]) for value in matrix],
                "move": [float(value[4]) for value in matrix],
                "curve": [float(value[5]) for value in matrix],
            })
        return states

    all_states = make(list(range(len(rows))))
    if len(all_states) < 50:
        raise SystemExit("too little stacking data: %d states" % len(all_states))

    episodes = sorted({state["ep"] for state in all_states})
    rng = random.Random(args.seed)
    rng.shuffle(episodes)
    folds = max(2, min(int(args.cv_folds), len(episodes)))
    buckets = [set(episodes[index::folds]) for index in range(folds)]
    fold_metrics: List[Dict[str, Any]] = []
    for held in buckets:
        train = [state for state in all_states if state["ep"] not in held]
        evaluate = [state for state in all_states if state["ep"] in held]
        if len(train) < 20 or len(evaluate) < 10:
            continue
        models_fold, _ = fit_models(train, args)
        fold_metrics.append(_fold_metrics(evaluate, models_fold))
    if len(fold_metrics) < 2:
        raise SystemExit("cross-validation produced fewer than two usable folds")

    names = ("linear", "tree", "gbdt")
    summary: Dict[str, Dict[str, float]] = {
        name: {
            "gain_capture": float(np.mean([
                row[name]["gain_capture"] for row in fold_metrics
                if row[name]["gain_capture"] is not None])),
            "top1": float(np.mean([
                row[name]["top1"] for row in fold_metrics
                if row[name]["top1"] is not None])),
        }
        for name in ("policy", "foresight_gain") + names
    }
    chosen = max(
        names,
        key=lambda name: (
            summary[name]["gain_capture"],
            summary[name]["top1"],
            -max(row[name].get("leaves", 0) for row in fold_metrics),
        ),
    )
    best_base_capture = max(summary["policy"]["gain_capture"],
                            summary["foresight_gain"]["gain_capture"])
    best_base_top1 = max(summary["policy"]["top1"],
                         summary["foresight_gain"]["top1"])
    fold_wins = sum(
        1 for row in fold_metrics
        if row[chosen]["gain_capture"] is not None
        and row[chosen]["gain_capture"] >= max(
            row["policy"]["gain_capture"] or 0.0,
            row["foresight_gain"]["gain_capture"] or 0.0) + 0.0005)
    gate_passed = (summary[chosen]["gain_capture"] >= best_base_capture + 0.0005
                   and summary[chosen]["top1"] >= best_base_top1 - 0.005
                   and fold_wins >= (len(fold_metrics) + 1) // 2)
    active = bool(gate_passed or args.force_active)

    models, weights = fit_models(all_states, args)
    chosen_model = models[chosen]
    fit_metrics = base_metrics(all_states)
    for name, model in models.items():
        attach_scores(all_states, name, model)
        fit_metrics[name] = model_metrics(all_states)
        fit_metrics[name]["leaves"] = model["complexity"]
    chosen_capture = summary[chosen]["gain_capture"]
    chosen_top1 = summary[chosen]["top1"]

    payload = {
        "kind": KIND,
        "version": VERSION,
        "features": list(feature_names),
        "model_type": chosen,
        "weights": (dict(zip(feature_names, weights))
                    if chosen == "linear" else {}),
        "tree": (models["tree"]["tree"] if chosen == "tree" else {}),
        "forest": (models["gbdt"]["forest"] if chosen == "gbdt" else {}),
        "kmeans": kmeans,
        "shortlist": max(2, int(args.shortlist)),
        "active": active,
        "chosen": {
            "model_type": chosen,
            "gain_capture": chosen_capture,
            "top1": chosen_top1,
            "leaves": chosen_model["complexity"],
        },
        "gate": {
            "passed": bool(gate_passed),
            "forced": bool(args.force_active and not gate_passed),
            "rule": ("cross-validated gain_capture >= max(base) + 0.0005, "
                     "top1 >= max(base) - 0.005, and a majority of folds ahead"),
            "best_base_gain_capture": best_base_capture,
            "stack_gain_capture": chosen_capture,
            "best_base_top1": best_base_top1,
            "stack_top1": chosen_top1,
            "fold_wins": fold_wins,
            "folds": len(fold_metrics),
        },
        "metrics": {
            "fit": fit_metrics,
            "cv": {
                "summary": summary,
                "folds": fold_metrics,
            },
            "selection": {
                "chosen": chosen,
                "reason": ("highest mean cross-validated gain capture, "
                           "then top1, then fewest leaves"),
            },
        },
        "provenance": {
            "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "corpus": _digest(Path(args.corpus)),
            "cluster_corpus": _digest(Path(args.cluster_corpus)),
            "clusters": int(kmeans["clusters"]),
            "shortlist": max(2, int(args.shortlist)),
            "policy": _digest(Path(args.policy_model)),
            "policy_meta": _digest(Path(args.policy_meta)),
            "foresight": _digest(Path(args.foresight_model)),
            "split": "whole episodes, cross-validation",
            "cv_folds": folds,
            "seed": int(args.seed),
            "states": len(all_states),
            "episodes": len(episodes),
            "iters": int(args.iterations),
            "l2": float(args.l2),
            "tree_depth": int(args.tree_depth),
            "tree_min_leaf": int(args.tree_min_leaf),
            "boosting_rounds": int(args.boosting_rounds),
            "boosting_depth": int(args.boosting_depth),
            "boosting_min_leaf": int(args.boosting_min_leaf),
            "boosting_rate": float(args.boosting_rate),
            "note": ("Meta-rankers are cross-validated on whole episodes that "
                     "were not used to fit either base model."),
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    print("weights: " + ", ".join("%s=%.4f" % pair
                                  for pair in zip(feature_names, weights)))
    for name in ("policy", "foresight_gain", "linear", "tree", "gbdt"):
        row = summary[name]
        print("%-14s cv_gain_capture %-9s cv_top1 %-8s leaves %s"
              % (name, round(row["gain_capture"], 6),
                 round(row["top1"], 6),
                 models.get(name, {}).get("complexity", "-")))
    print("selected: %s (%d/%d folds ahead)"
          % (chosen, fold_wins, len(fold_metrics)))
    print("gate: %s -> %s" % ("PASS" if gate_passed else "FAIL",
                              "active" if active else "inactive"))
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
