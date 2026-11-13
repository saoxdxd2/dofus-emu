"""PCA signature of a combat board, streaming subspace, and OOD residual.

A board the grid core consumes is 560 cells x 14 features plus 16 fighters x 20
features, 8,160 numbers before any padding. Most of that is redundant: three of
the cell columns are constant flags, occupancy is zero on all but a handful of
cells, and line of sight is a function of the same geometry for every board on
one map. A PCA fitted once over recorded boards turns each board into a short
vector that is cheap to log, cheap to compare and cheap to feed a learned head.

This is a compression and similarity surface, not a replacement for the grid
model: the transformer still sees the full tokens. What the signature adds is
(a) a fixed-width state id for the decision log, so boards can be clustered and
de-duplicated afterwards, and (b) a projection that is a few hundred thousand
multiply-adds, cheap enough for the live path at 40 instances.

Streaming and OOD
-----------------
``fit`` is the batch reference, but the live path uses ``StreamingPCA``:
Brand's incremental SVD folds each new batch of boards into the same subspace
without refitting over the corpus, so the signature tracks a map rotation or a
new class without a training run.

The same projection yields the orthogonal residual

    || x - mean - U U^T (x - mean) ||

which is exactly the part of a board the subspace cannot explain. A corrupted
observation (a cell list that lost its walls, a fighter with impossible HP) and
an unseen boss mechanic both land far outside the fitted board manifold before
the solver reads them, so the cost is one width x 32 product: measured at
0.05-0.10 ms, under the 0.2 ms budget.

Fitted on `fight_boards.jsonl` with `tools/pca_probe.py`. Centring only: the
inputs are booleans and ratios in [0, 1], and dividing by a per-column std would
amplify a column that is constant on the recorded maps into pure noise.
"""

from __future__ import annotations

import json
import math
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

from combat.battle_solver import Board
from combat.grid_dataset import _cells, _fighters

DEFAULT_COMPONENTS = 32
#: Percentile of the residual distribution used as the OOD threshold. 99.5
#: flags roughly 1 in 200 *in-distribution* boards; the flag is a warning the
#: solver can drop a board on, not a verdict by itself.
DEFAULT_OOD_PERCENTILE = 99.5
DEFAULT_RESIDUAL_WINDOW = 512
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "models" / "board_pca_v1.json"


def board_matrix(board: Board) -> Optional[np.ndarray]:
    """The flattened feature vector of one board, or None when it cannot be built.

    The same builders the grid dataset uses, so a signature describes exactly the
    features the model consumes.
    """
    me = board.me
    if me is None:
        return None
    try:
        cells = _cells(board, me)
        fighters, _mask = _fighters(board, me)
    except Exception:  # noqa: BLE001 - a malformed board must not stop a corpus pass
        return None
    return np.concatenate([cells.reshape(-1), fighters.reshape(-1)]).astype(np.float32)


def matrix_from_payload(payload: Dict[str, Any]) -> Optional[np.ndarray]:
    try:
        board = Board.from_fight_state(payload)
    except Exception:  # noqa: BLE001
        return None
    return board_matrix(board)


def fit(matrices: Sequence[np.ndarray], components: int = DEFAULT_COMPONENTS) -> Dict[str, Any]:
    """Fit mean + principal directions over a corpus of board matrices."""
    if not matrices:
        raise ValueError("no board matrices to fit")
    stack = np.stack([np.asarray(row, dtype=np.float32) for row in matrices])
    mean = stack.mean(axis=0, dtype=np.float64).astype(np.float32)
    centred = stack - mean
    # Economy SVD: rows are boards, so the spectrum has at most n-1 directions no
    # matter how wide the features are.
    _u, s, vt = np.linalg.svd(centred, full_matrices=False)
    total = float(np.sum(s.astype(np.float64) ** 2))
    k = int(max(0, min(components, vt.shape[0])))
    comps = vt[:k].astype(np.float32)
    explained = (s.astype(np.float64) ** 2)
    ratio = explained / total if total > 0 else np.zeros_like(explained)
    return {
        "version": 1,
        "width": int(stack.shape[1]),
        "rows": int(stack.shape[0]),
        "k": k,
        "mean": mean,
        "components": comps,
        "variance_ratio": [float(v) for v in ratio[:k]],
        "explained_total": float(np.sum(ratio[:k])),
        # kept so `tools/pca_probe.py` can report the full spectrum without refitting
        "spectrum": [float(v) for v in ratio],
    }


def project(model: Dict[str, Any], matrix: np.ndarray) -> np.ndarray:
    mean = np.asarray(model["mean"], dtype=np.float32)
    comps = np.asarray(model["components"], dtype=np.float32)
    vec = np.asarray(matrix, dtype=np.float32).reshape(-1)
    if vec.shape[0] != mean.shape[0]:
        raise ValueError("signature expects %d features, got %d"
                         % (mean.shape[0], vec.shape[0]))
    return np.dot(comps, vec - mean).astype(np.float32)


def reconstruct(model: Dict[str, Any], signature: np.ndarray) -> np.ndarray:
    mean = np.asarray(model["mean"], dtype=np.float32)
    comps = np.asarray(model["components"], dtype=np.float32)
    return mean + np.dot(np.asarray(signature, dtype=np.float32), comps)


def spectrum_for(matrices: Sequence[np.ndarray],
                 fractions: Iterable[float] = (0.90, 0.95, 0.99)) -> Dict[str, Any]:
    """How many components each variance fraction needs, from the full spectrum."""
    if not matrices:
        return {"rows": 0}
    stack = np.stack([np.asarray(row, dtype=np.float32) for row in matrices])
    centred = stack - stack.mean(axis=0, dtype=np.float32)
    _u, s, _vt = np.linalg.svd(centred, full_matrices=False)
    power = s.astype(np.float64) ** 2
    total = float(np.sum(power)) or 1.0
    cumulative = np.cumsum(power) / total
    out: Dict[str, Any] = {"rows": int(stack.shape[0]),
                           "width": int(stack.shape[1]),
                           "max_components": int(len(power)),
                           "cumulative": [float(v) for v in cumulative[:64]]}
    for fraction in fractions:
        hit = np.searchsorted(cumulative, fraction) + 1
        out["k_for_%d" % int(round(fraction * 100))] = int(min(hit, len(power)))
    return out


def save(model: Dict[str, Any], path: Path = DEFAULT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(model)
    payload["mean"] = np.asarray(payload["mean"], dtype=np.float32).tolist()
    payload["components"] = np.asarray(payload["components"], dtype=np.float32).tolist()
    stream = payload.get("stream")
    if isinstance(stream, dict):
        payload["stream"] = {
            key: (np.asarray(value, dtype=np.float64).tolist()
                  if isinstance(value, np.ndarray) else value)
            for key, value in stream.items()
        }
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return path


def load(path: Path = DEFAULT_PATH) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload["mean"] = np.asarray(payload["mean"], dtype=np.float32)
    payload["components"] = np.asarray(payload["components"], dtype=np.float32)
    return payload


def signature_of(payload: Dict[str, Any], model: Dict[str, Any]) -> Optional[List[float]]:
    """Convenience for the live path: board payload -> short signature or None."""
    matrix = matrix_from_payload(payload)
    if matrix is None:
        return None
    return [float(v) for v in project(model, matrix)]


# ---------------------------------------------------------------------------
# Streaming subspace
# ---------------------------------------------------------------------------


class StreamingPCA:
    """Incremental PCA over board matrices, plus the OOD residual.

    Brand (2006), "Fast low-rank modifications of the thin singular value
    decomposition": the new batch is projected onto the current basis, the part
    the basis cannot explain is orthonormalised with a QR, and one small SVD
    over the merged (k + n) x (k + n) core updates both basis and singular
    values. Cost per batch is O(n * d * k), not the O(d^2 k) an eigendecomposition
    of the full covariance would need at d = 8,160.

    The mean is updated exactly (running mean over every row seen). Brand's
    correction term for the mean shift is dropped: at 32 components and a board
    distribution that moves slowly it changes the subspace far less than the
    batch noise, and the alternative is carrying a second basis column for the
    shift on every update.
    """

    def __init__(self, width: int, components: int = DEFAULT_COMPONENTS,
                 window: int = DEFAULT_RESIDUAL_WINDOW) -> None:
        self.width = int(width)
        self.components = int(components)
        self.mean = np.zeros(self.width, dtype=np.float64)
        self.basis = np.zeros((0, self.width), dtype=np.float64)
        self.singular = np.zeros(0, dtype=np.float64)
        self.count = 0
        self.updates = 0
        self.discarded = 0.0
        self.residuals: deque = deque(maxlen=int(window))
        self.residual_center = 0.0
        self.residual_scale = 1.0
        self.threshold = float("inf")
        self.calibrated = False
        self.last_residual = 0.0
        # float32 copies for inference. `astype` on a 32 x 8160 basis is a 1 MB
        # allocation, and doing it per board made the residual 0.7 ms instead of
        # 0.06 ms. Refreshed at the end of every update, never inside score().
        self._mean32 = self.mean.astype(np.float32)
        self._basis32 = self.basis.astype(np.float32)

    def _refresh_inference(self) -> None:
        self._mean32 = self.mean.astype(np.float32)
        self._basis32 = self.basis.astype(np.float32)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_model(cls, model: Dict[str, Any]) -> "StreamingPCA":
        mean = np.asarray(model["mean"], dtype=np.float64).reshape(-1)
        comps = np.asarray(model["components"], dtype=np.float64)
        if comps.ndim == 1:
            comps = comps.reshape(1, -1)
        if comps.shape[1] != mean.shape[0]:
            raise ValueError("model components are %d-wide, mean is %d"
                             % (comps.shape[1], mean.shape[0]))
        state = cls(mean.shape[0], comps.shape[0])
        state.mean = mean
        state.basis = comps
        state.count = int(model.get("rows") or 0)
        stream = model.get("stream") or {}
        if stream:
            singular = stream.get("singular")
            if singular is not None and len(singular):
                state.singular = np.asarray(singular, dtype=np.float64)
            state.count = int(stream.get("count") or state.count)
            state.updates = int(stream.get("updates") or 0)
            state.discarded = float(stream.get("discarded") or 0.0)
            state.residual_center = float(stream.get("residual_center") or 0.0)
            state.residual_scale = float(stream.get("residual_scale") or 1.0)
            state.threshold = float(stream.get("threshold") or float("inf"))
            state.calibrated = bool(stream.get("calibrated"))
        if not state.singular.size:
            state.singular = np.ones(state.basis.shape[0], dtype=np.float64)
        state._refresh_inference()
        return state

    def to_model(self) -> Dict[str, Any]:
        return {
            "version": 2,
            "width": self.width,
            "rows": self.count,
            "k": int(self.basis.shape[0]),
            "mean": self.mean.astype(np.float32),
            "components": self.basis.astype(np.float32),
            "stream": {
                "count": self.count,
                "updates": self.updates,
                "singular": self.singular,
                "discarded": self.discarded,
                "residual_center": self.residual_center,
                "residual_scale": self.residual_scale,
                "threshold": self.threshold,
                "calibrated": self.calibrated,
            },
        }

    # -- update -------------------------------------------------------------

    def update(self, batch: Any) -> Dict[str, Any]:
        """Fold one board or a batch of boards into the subspace."""
        x = np.atleast_2d(np.asarray(batch, dtype=np.float64))
        if x.ndim != 2 or x.shape[1] != self.width:
            raise ValueError("expected [n, %d] board rows, got %r"
                             % (self.width, getattr(batch, "shape", None)))
        if not np.all(np.isfinite(x)):
            # A non-finite row is the corruption this detector exists for; the
            # update must not ingest it and poison every later residual.
            raise ValueError("board matrix contains non-finite values")
        n = x.shape[0]
        total = self.count + n
        self.mean = self.mean + (x.sum(axis=0) - n * self.mean) / total
        centred = x - self.mean

        basis = self.basis
        k = basis.shape[0]
        if k == 0:
            # With n < d the left singular vectors are n x n; the *directions*
            # are the rows of vt, which is (n, d). Using u here was a shape bug
            # that only surfaced once a second batch arrived.
            _u, s, vt = np.linalg.svd(centred, full_matrices=False)
            keep = min(self.components, vt.shape[0])
            self.basis = vt[:keep]
            self.singular = s[:keep]
            self.count = total
            self.updates += 1
            self._refresh_inference()
            return {"rows": n, "k": int(keep), "count": total}

        projection = centred @ basis.T              # n x k
        residual = centred - projection @ basis     # n x d
        q, r_matrix = np.linalg.qr(residual.T)      # d x r, r x n
        r = q.shape[1] if q.size else 0
        core = np.zeros((k + r, k + r), dtype=np.float64)
        core[:k, :k] = np.diag(self.singular)
        core[:k, k:] = projection.T
        if r:
            core[k:, k:] = r_matrix
        u_core, s_new, _vt = np.linalg.svd(core)
        merged = np.vstack([basis, q.T]) if r else basis
        kept = min(self.components, len(s_new))
        new_basis = u_core[:, :kept].T @ merged
        # Re-orthonormalise: a basis that drifts from orthonormal makes the
        # residual (the whole detector) meaningless.
        q_new, _ = np.linalg.qr(new_basis.T)
        self.basis = q_new.T[:kept]
        self.singular = s_new[:kept]
        tail = s_new[kept:]
        self.discarded = float(np.sum(tail * tail)) if tail.size else 0.0
        self.count = total
        self.updates += 1
        self._refresh_inference()
        return {"rows": n, "k": int(kept), "count": total,
                "discarded": self.discarded}

    # -- inference ----------------------------------------------------------

    def residual(self, matrix: Any) -> float:
        """Orthogonal distance from the subspace: ||x - mean - U U^T (x - mean)||."""
        vector = np.asarray(matrix, dtype=np.float32).reshape(-1)
        if vector.shape[0] != self.width:
            raise ValueError("residual expects %d features, got %d"
                             % (self.width, vector.shape[0]))
        centred = vector - self._mean32
        basis = self._basis32
        projection = basis @ centred
        rebuilt = projection @ basis
        value = float(np.linalg.norm(centred - rebuilt))
        self.last_residual = value
        return value

    def calibrate(self, residuals: Optional[Sequence[float]] = None) -> Dict[str, float]:
        """Set threshold/centre/scale from observed residuals (default: the window)."""
        values = np.asarray(list(residuals) if residuals is not None
                            else list(self.residuals), dtype=np.float64)
        if values.size < 8:
            raise ValueError("need at least 8 residuals to calibrate, have %d" % values.size)
        centre = float(np.median(values))
        mad = float(np.median(np.abs(values - centre)))
        self.residual_center = centre
        self.residual_scale = max(1e-6, 1.4826 * mad)
        self.threshold = float(np.percentile(values, DEFAULT_OOD_PERCENTILE))
        self.calibrated = True
        return {"residual_center": centre, "residual_scale": self.residual_scale,
                "threshold": self.threshold, "rows": int(values.size)}

    def score(self, matrix: Any, record: bool = True) -> Dict[str, Any]:
        """Residual + OOD verdict for one board, measured in milliseconds."""
        started = time.perf_counter()
        value = self.residual(matrix)
        if record:
            self.residuals.append(value)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if self.calibrated:
            z = (value - self.residual_center) / self.residual_scale
            ood = bool(value > self.threshold)
        else:
            z = value / max(1.0, math.sqrt(self.width))
            ood = bool(value > 3.0 * math.sqrt(self.width))
        return {"residual": value, "z": float(z), "ood": ood,
                "calibrated": self.calibrated, "ms": elapsed_ms}

    def observe(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Board payload -> {"signature", "ood"} or None when it cannot be built."""
        matrix = matrix_from_payload(payload)
        if matrix is None:
            return None
        centred = matrix - self._mean32
        return {
            "signature": [float(v) for v in self._basis32 @ centred],
            "ood": self.score(matrix),
        }


def ood_check(payload: Dict[str, Any], model: Any,
              record: bool = True) -> Optional[Dict[str, Any]]:
    """The live-path convenience: one call, a flag the caller can act on.

    ``model`` is a ``StreamingPCA`` or the dict a saved model loads into.
    """
    state = model if isinstance(model, StreamingPCA) else StreamingPCA.from_model(model)
    matrix = matrix_from_payload(payload)
    if matrix is None:
        return None
    return state.score(matrix, record=record)
