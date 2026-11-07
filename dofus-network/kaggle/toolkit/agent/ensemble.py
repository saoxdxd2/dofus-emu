"""Candidate-level stacking for the learned tool models.

The tool policy scores a state once and returns a distribution over tools. The
foresight net scores each (state, candidate) pair and returns a predicted gain,
movement probability and progress curve. They make different errors, so this
module learns one small listwise meta-ranker over both outputs instead of
choosing one model by hand.

The meta-ranker has four nonnegative weights and no hidden layer. Its input
features are computed identically offline and at serve time:

    policy_logp   log P(tool | state) from the tool policy
    gain_z        foresight gain, standardised across this state's candidates
    move_logit    logit of foresight P(the world changes)
    curve_z       mean foresight progress curve, standardised per state

Weights are fitted on whole held-out episodes and the artifact is inactive
unless the held-out gate passes. A missing or malformed artifact returns None,
so the deterministic planner remains available.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

KIND = "tool-stacking"
VERSION = 1
BASE_FEATURES: Tuple[str, ...] = ("policy_logp", "policy_rank", "gain_z",
                                  "gain_rank", "move_logit", "curve_z")
KMEANS_CLUSTERS = 4
FEATURES: Tuple[str, ...] = BASE_FEATURES + tuple(
    "cluster_%d" % index for index in range(KMEANS_CLUSTERS))
DEFAULT_MODEL = (Path(__file__).resolve().parent.parent
                 / "models" / "tool_stack.json")


def softmax(values: Sequence[float]) -> List[float]:
    values = [float(value) for value in values]
    if not values:
        return []
    peak = max(values)
    exps = [math.exp(max(-60.0, min(60.0, value - peak))) for value in values]
    total = sum(exps)
    return [value / total if total > 0 else 1.0 / len(exps) for value in exps]


def zscore(values: Sequence[float]) -> List[float]:
    values = [float(value) for value in values]
    if not values:
        return []
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    std = math.sqrt(variance)
    # A constant signal carries no ranking information. Returning zeros keeps it
    # from becoming a magnitude-1e6 feature after division.
    if std < 1e-9:
        return [0.0] * len(values)
    return [(value - mean) / std for value in values]


def fit_kmeans(points: Sequence[Sequence[float]], clusters: int = KMEANS_CLUSTERS,
               seed: int = 0, iterations: int = 30) -> Dict[str, Any]:
    """Deterministic K-means++ over state features, exported as JSON arrays.

    The clusters are context for the stacked ranker, not a policy. They let the
    tree/boosting meta-models learn different base-model blends for states the
    neural bases treat similarly.
    """
    import numpy as np

    x = np.asarray(points, dtype=np.float64)
    if x.ndim != 2 or len(x) < clusters:
        raise ValueError("not enough state rows for K-means")
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-9] = 1.0
    z = (x - mean) / scale
    rng = np.random.default_rng(seed)
    chosen = [int(rng.integers(len(z)))]
    nearest = ((z - z[chosen[0]][None, :]) ** 2).sum(axis=1)
    while len(chosen) < clusters:
        weights = np.maximum(nearest, 0.0)
        total = float(weights.sum())
        if total <= 1e-12:
            pick = int(rng.integers(len(z)))
        else:
            pick = int(rng.choice(len(z), p=weights / total))
        if pick in chosen:
            pick = int(np.argmax(nearest))
        chosen.append(pick)
        nearest = np.minimum(
            nearest, ((z - z[pick][None, :]) ** 2).sum(axis=1))
    centroids = z[chosen].copy()
    for _ in range(max(1, int(iterations))):
        distance = ((z[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
        assignment = distance.argmin(axis=1)
        moved = 0.0
        for index in range(clusters):
            members = z[assignment == index]
            if len(members):
                updated = members.mean(axis=0)
                moved = max(moved, float(((updated - centroids[index]) ** 2).sum()))
                centroids[index] = updated
        if moved < 1e-9:
            break
    distance = ((z[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
    inertia = float(distance.min(axis=1).mean())
    return {
        "clusters": int(clusters),
        "centroids": [[float(value) for value in row] for row in centroids],
        "mean": [float(value) for value in mean],
        "scale": [float(value) for value in scale],
        "inertia": round(inertia, 6),
        "rows": int(len(z)),
    }


def kmeans_distances(model: Mapping[str, Any],
                     state: Sequence[float]) -> List[float]:
    import numpy as np

    if not model or not model.get("centroids"):
        return []
    mean = np.asarray(model["mean"], dtype=np.float64)
    scale = np.asarray(model["scale"], dtype=np.float64)
    centroids = np.asarray(model["centroids"], dtype=np.float64)
    point = (np.asarray(state, dtype=np.float64) - mean) / np.maximum(1e-9, scale)
    distance = np.sqrt(np.maximum(
        0.0, ((centroids - point[None, :]) ** 2).sum(axis=1)))
    return [float(value) for value in distance]


def _mean_curve(cell: Mapping[str, Any]) -> float:
    curve = cell.get("curve")
    if isinstance(curve, (list, tuple)) and curve:
        return sum(float(value) for value in curve) / len(curve)
    gain = float(cell.get("gain") or 0.0)
    return gain


def candidate_features(policy_logits: Sequence[float],
                       foresight: Mapping[str, Mapping[str, Any]],
                       tools: Optional[Sequence[str]] = None,
                       context: Optional[Sequence[float]] = None,
                       width: Optional[int] = None,
                       allowed: Optional[Iterable[str]] = None
                       ) -> Tuple[List[str], List[List[float]]]:
    """Build the meta-ranker's rows for the candidates both bases scored."""
    names = list(tools or [])
    if not names:
        return [], []
    raw = [float(value) for value in policy_logits]
    if len(raw) != len(names):
        raise ValueError("policy logits and tool names differ in width")
    probs = softmax(raw)
    permit = set(allowed) if allowed is not None else None
    kept = [index for index, tool in enumerate(names)
            if tool in foresight and isinstance(foresight[tool], Mapping)
            and (permit is None or tool in permit)]
    if not kept:
        return [], []
    gains = [float(foresight[names[i]].get("gain") or 0.0) for i in kept]
    curves = [_mean_curve(foresight[names[i]]) for i in kept]
    gain_z = zscore(gains)
    curve_z = zscore(curves)
    kept_logits = [raw[index] for index in kept]
    policy_order = sorted(range(len(kept)),
                          key=lambda i: kept_logits[i], reverse=True)
    gain_order = sorted(range(len(kept)), key=lambda i: gains[i], reverse=True)
    policy_rank = [0.0] * len(kept)
    gain_rank = [0.0] * len(kept)
    span = max(1, len(kept) - 1)
    for position, candidate in enumerate(policy_order):
        policy_rank[candidate] = position / span
    for position, candidate in enumerate(gain_order):
        gain_rank[candidate] = position / span
    extras = [float(value) for value in (context or [])]
    if width is not None:
        if len(extras) < width:
            extras.extend([0.0] * (width - len(extras)))
        extras = extras[:width]
    rows: List[List[float]] = []
    for position, index in enumerate(kept):
        cell = foresight[names[index]]
        probability = max(1e-9, min(1.0 - 1e-9,
                                     float(cell.get("move_prob") or 0.5)))
        rows.append([
            math.log(max(1e-12, probs[index])),
            policy_rank[position],
            gain_z[position],
            gain_rank[position],
            math.log(probability / (1.0 - probability)),
            curve_z[position],
        ] + extras)
    return [names[index] for index in kept], rows


def fit_listwise(groups: Sequence[Sequence[Sequence[float]]],
                 targets: Sequence[Sequence[float]],
                 weights: Optional[Sequence[float]] = None,
                 l2: float = 0.02, iterations: int = 1200,
                 learning_rate: float = 0.06,
                 nonnegative: bool = True) -> List[float]:
    """Fit nonnegative weights by weighted listwise cross-entropy.

    Each group is one state's candidate matrix, so the softmax competes the
    candidates for that state against each other instead of treating every
    candidate row as its own classification problem.

    Adam is deliberately not used here: the model is four parameters and the
    objective is smooth, so plain gradient descent with normalised gradients is
    easier to audit and has no optimizer state to persist.
    """
    import numpy as np

    if not groups or len(groups) != len(targets):
        raise ValueError("groups and targets must be non-empty and aligned")
    width = len(groups[0][0])
    if width <= 0:
        raise ValueError("empty stacking feature rows")
    matrices: List[np.ndarray] = []
    target_rows: List[np.ndarray] = []
    for group, target in zip(groups, targets):
        matrix = np.asarray(group, dtype=np.float64)
        target_row = np.asarray(target, dtype=np.float64)
        if matrix.ndim != 2 or matrix.shape[1] != width:
            raise ValueError("ragged stacking matrix")
        if target_row.shape != (matrix.shape[0],):
            raise ValueError("target width does not match candidate count")
        matrices.append(matrix)
        target_rows.append(target_row)
    wrow = (np.asarray(weights, dtype=np.float64)
            if weights is not None else np.ones(len(matrices), dtype=np.float64))
    if wrow.shape != (len(matrices),):
        raise ValueError("state weights do not match the candidate groups")
    wrow = np.clip(wrow, 1e-6, None)
    wrow /= wrow.sum()
    params = np.full(width, 1.0 / max(1, width), dtype=np.float64)
    velocity = np.zeros_like(params)
    squared = np.zeros_like(params)
    beta1, beta2, epsilon = 0.9, 0.999, 1e-8
    for step in range(1, max(1, int(iterations)) + 1):
        grad = float(l2) * params
        for matrix, target, row_weight in zip(matrices, target_rows, wrow):
            scores = matrix @ params
            scores -= scores.max()
            exp_scores = np.exp(scores)
            probs = exp_scores / max(1e-12, exp_scores.sum())
            grad += row_weight * ((probs - target)[:, None] * matrix).sum(axis=0)
        velocity = beta1 * velocity + (1.0 - beta1) * grad
        squared = beta2 * squared + (1.0 - beta2) * grad * grad
        corrected = (velocity / (1.0 - beta1 ** step)
                     / (np.sqrt(squared / (1.0 - beta2 ** step)) + epsilon))
        params -= float(learning_rate) * corrected
        if nonnegative:
            params = np.maximum(0.0, params)
    return [float(value) for value in params]


def soft_targets(gains: Sequence[float], teacher: Optional[int] = None,
                 temperature: float = 0.08) -> List[float]:
    values = [float(value) for value in gains]
    if not values:
        return []
    if max(values) <= 0.0:
        target = [0.0] * len(values)
        if teacher is not None and 0 <= teacher < len(values):
            target[teacher] = 1.0
        return target
    peak = max(values)
    exps = [math.exp(max(-60.0, min(60.0, (value - peak) / temperature)))
            for value in values]
    total = sum(exps)
    return [value / total for value in exps]


def fit_tree(features: Sequence[Sequence[float]],
             targets: Sequence[float],
             weights: Optional[Sequence[float]] = None,
             depth: int = 3, min_leaf: int = 40,
             min_gain: float = 1e-9) -> Dict[str, Any]:
    """Fit a weighted CART regressor and return a JSON-safe tree.

    A tree is the meta-learner here because the two base models fail in
    different regions: the policy is confident on common tools, foresight is
    sharper when the candidate set actually separates. A split on the base
    outputs can route those regions without assuming the blend is linear.
    """
    import numpy as np

    x = np.asarray(features, dtype=np.float64)
    y = np.asarray(targets, dtype=np.float64)
    if x.ndim != 2 or x.shape[0] != len(y) or len(y) == 0:
        raise ValueError("tree needs a non-empty candidate matrix")
    w = (np.asarray(weights, dtype=np.float64)
         if weights is not None else np.ones(len(y), dtype=np.float64))
    if w.shape != (len(y),):
        raise ValueError("tree weights do not match candidates")
    w = np.clip(w, 1e-9, None)

    def leaf(indices: Any) -> Dict[str, Any]:
        total = float(w[indices].sum())
        value = float((w[indices] * y[indices]).sum() / max(1e-12, total))
        return {"value": value, "n": int(len(indices))}

    def build(indices: Any, level: int) -> Dict[str, Any]:
        if level >= depth or len(indices) < 2 * min_leaf:
            return leaf(indices)
        parent_total = float(w[indices].sum())
        parent_sse = float((w[indices] * y[indices] ** 2).sum())
        best = None
        for feature in range(x.shape[1]):
            order = indices[np.argsort(x[indices, feature], kind="mergesort")]
            values = x[order, feature]
            if values[0] == values[-1]:
                continue
            ww = w[order]
            wy = ww * y[order]
            wy2 = wy * y[order]
            cw = np.cumsum(ww)
            cy = np.cumsum(wy)
            cy2 = np.cumsum(wy2)
            left_w = cw[:-1]
            right_w = parent_total - left_w
            valid = (left_w >= min_leaf) & (right_w >= min_leaf)
            valid &= values[:-1] < values[1:]
            if not valid.any():
                continue
            left_sse = cy2[:-1] - (cy[:-1] ** 2) / np.maximum(1e-12, left_w)
            right_sse = ((cy2[-1] - cy2[:-1])
                         - ((cy[-1] - cy[:-1]) ** 2)
                         / np.maximum(1e-12, right_w))
            gains = parent_sse - left_sse - right_sse
            gains = np.where(valid, gains, -np.inf)
            position = int(np.argmax(gains))
            gain = float(gains[position])
            if gain < min_gain or (best is not None and gain <= best[0]):
                continue
            threshold = float((values[position] + values[position + 1]) / 2.0)
            best = (gain, feature, threshold, order[:position + 1],
                    order[position + 1:])
        if best is None:
            return leaf(indices)
        _, feature, threshold, left, right = best
        return {
            "feature": int(feature),
            "threshold": threshold,
            "left": build(np.asarray(left, dtype=np.int64), level + 1),
            "right": build(np.asarray(right, dtype=np.int64), level + 1),
        }

    return build(np.arange(len(y), dtype=np.int64), 0)


def tree_predict(tree: Mapping[str, Any], features: Sequence[float]) -> float:
    node: Mapping[str, Any] = tree
    while "value" not in node:
        feature = int(node["feature"])
        node = node["left"] if float(features[feature]) < float(node["threshold"]) \
            else node["right"]
    return float(node["value"])


def fit_gbdt(features: Sequence[Sequence[float]],
             targets: Sequence[float],
             weights: Optional[Sequence[float]] = None,
             rounds: int = 16, depth: int = 2, min_leaf: int = 60,
             learning_rate: float = 0.12,
             min_gain: float = 1e-9) -> Dict[str, Any]:
    """Small gradient-boosted tree ensemble, exported for pure-Python scoring."""
    import numpy as np

    y = np.asarray(targets, dtype=np.float64)
    w = (np.asarray(weights, dtype=np.float64)
         if weights is not None else np.ones(len(y), dtype=np.float64))
    base = float((w * y).sum() / max(1e-12, w.sum()))
    prediction = np.full(len(y), base, dtype=np.float64)
    trees: List[Dict[str, Any]] = []
    for _ in range(max(1, int(rounds))):
        residual = y - prediction
        tree = fit_tree(features, residual, w, depth=depth,
                        min_leaf=min_leaf, min_gain=min_gain)
        rows = [tree_predict(tree, row) for row in features]
        prediction += float(learning_rate) * np.asarray(rows, dtype=np.float64)
        trees.append(tree)
    return {"base": base, "learning_rate": float(learning_rate), "trees": trees}


def gbdt_predict(model: Mapping[str, Any], features: Sequence[float]) -> float:
    value = float(model.get("base") or 0.0)
    rate = float(model.get("learning_rate") or 1.0)
    for tree in model.get("trees") or []:
        value += rate * tree_predict(tree, features)
    return value


class ToolStack:
    """Torch-free inference for a fitted candidate stack."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        if payload.get("kind") != KIND:
            raise ValueError("not a tool stacking artifact")
        self.payload = dict(payload)
        self.features = [str(name) for name in payload.get("features") or FEATURES]
        if tuple(self.features[:len(BASE_FEATURES)]) != BASE_FEATURES:
            raise ValueError("unexpected stacking feature order")
        self.cluster_width = len(self.features) - len(BASE_FEATURES)
        self.kmeans: Dict[str, Any] = dict(payload.get("kmeans") or {})
        if self.cluster_width and not self.kmeans.get("centroids"):
            raise ValueError("stack expects cluster features but carries no centroids")
        self.shortlist = max(2, int(payload.get("shortlist") or 6))
        self.model_type = str(payload.get("model_type") or "linear")
        self.tree: Dict[str, Any] = dict(payload.get("tree") or {})
        self.forest: Dict[str, Any] = dict(payload.get("forest") or {})
        raw_weights = payload.get("weights") or {}
        if self.model_type == "linear":
            if isinstance(raw_weights, Mapping):
                self.weights = [float(raw_weights[name]) for name in self.features]
            else:
                self.weights = [float(value) for value in raw_weights]
            if len(self.weights) != len(self.features) or not all(
                    math.isfinite(value) for value in self.weights):
                raise ValueError("invalid stacking weights")
        elif self.model_type == "tree":
            self.weights = []
            if not self.tree:
                raise ValueError("tree stack has no tree")
        elif self.model_type == "gbdt":
            self.weights = []
            if not self.forest.get("trees"):
                raise ValueError("gbdt stack has no trees")
        else:
            raise ValueError("unknown stacking model %r" % self.model_type)
        gate = payload.get("gate") if isinstance(payload.get("gate"), Mapping) else {}
        self.gate_passed = bool(gate.get("passed"))
        self.active = bool(payload.get("active")) and self.gate_passed
        self.metrics = dict(payload.get("metrics") or {})
        self.chosen = dict(payload.get("chosen") or {})
        self.note = ("stack %s active (gain capture %s)"
                     % (self.model_type, self.chosen.get("gain_capture"))
                     if self.active else
                     "stack %s fitted but inactive (gate not passed)"
                     % self.model_type)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> Optional["ToolStack"]:
        try:
            payload = json.loads(Path(path or DEFAULT_MODEL).read_text(encoding="utf-8"))
            return cls(payload)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def score_rows(self, rows: Sequence[Sequence[float]]) -> List[float]:
        if self.model_type == "linear":
            return [sum(weight * float(value)
                        for weight, value in zip(self.weights, row))
                    for row in rows]
        if self.model_type == "tree":
            return [tree_predict(self.tree, row) for row in rows]
        return [gbdt_predict(self.forest, row) for row in rows]

    def context(self, state: Sequence[float]) -> List[float]:
        """K-means distances for one state, or [] when the stack has no clusters."""
        if not self.cluster_width:
            return []
        return kmeans_distances(self.kmeans, state)[:self.cluster_width]

    def _shortlist(self, policy_logits: Sequence[float],
                   foresight: Mapping[str, Mapping[str, Any]],
                   tools: Sequence[str]) -> Optional[List[str]]:
        supported = [tool for tool in tools if tool in foresight]
        if len(supported) <= self.shortlist:
            return None
        by_policy = sorted(supported, key=lambda tool: float(
            policy_logits[tools.index(tool)]), reverse=True)
        by_gain = sorted(supported, key=lambda tool: float(
            (foresight[tool] or {}).get("gain") or 0.0), reverse=True)
        chosen: List[str] = []
        for index in range(self.shortlist):
            prefer = by_policy if index % 2 == 0 else by_gain
            rank = index // 2
            if rank < len(prefer) and prefer[rank] not in chosen:
                chosen.append(prefer[rank])
        for tool in by_policy + by_gain:
            if len(chosen) >= self.shortlist:
                break
            if tool not in chosen:
                chosen.append(tool)
        return chosen[:self.shortlist]

    def rank(self, policy_logits: Sequence[float],
             foresight: Mapping[str, Mapping[str, Any]],
             tools: Optional[Sequence[str]] = None,
             context: Optional[Sequence[float]] = None
             ) -> List[Dict[str, Any]]:
        tool_names = list(tools or [])
        allowed = self._shortlist(policy_logits, foresight, tool_names) \
            if tool_names else None
        names, rows = candidate_features(
            policy_logits, foresight, tool_names, context=context,
            width=self.cluster_width, allowed=allowed)
        if rows and len(rows[0]) != len(self.features):
            raise ValueError("stack expects %d features, got %d"
                             % (len(self.features), len(rows[0])))
        scores = self.score_rows(rows)
        out = [{"tool": name, "score": score,
                "features": dict(zip(self.features, row))}
               for name, score, row in zip(names, scores, rows)]
        out.sort(key=lambda row: row["score"], reverse=True)
        return out

    def choose(self, policy_logits: Sequence[float],
               foresight: Mapping[str, Mapping[str, Any]],
               tools: Optional[Sequence[str]] = None,
               context: Optional[Sequence[float]] = None
               ) -> Optional[Dict[str, Any]]:
        ranked = self.rank(policy_logits, foresight, tools, context)
        return ranked[0] if ranked else None


def policy_choice(policy_logits: Sequence[float],
                  tools: Sequence[str]) -> Optional[str]:
    if not policy_logits or len(policy_logits) != len(tools):
        return None
    index = max(range(len(policy_logits)), key=lambda i: float(policy_logits[i]))
    return tools[index]
