"""PCA signature of a combat board.

A board the grid core consumes is 560 cells x 12 features plus 16 fighters x 20
features, 9,920 numbers before any padding. Most of that is redundant: three of
the cell columns are constant flags, occupancy is zero on all but a handful of
cells, and line of sight is a function of the same geometry for every board on
one map. A PCA fitted once over recorded boards turns each board into a short
vector that is cheap to log, cheap to compare and cheap to feed a learned head.

This is a compression and similarity surface, not a replacement for the grid
model: the transformer still sees the full tokens. What the signature adds is
(a) a fixed-width state id for the decision log, so boards can be clustered and
de-duplicated afterwards, and (b) a projection that is a few hundred thousand
multiply-adds, cheap enough for the live path at 40 instances.

Fitted on `fight_boards.jsonl` with `tools/pca_probe.py`. Centring only: the
inputs are booleans and ratios in [0, 1], and dividing by a per-column std would
amplify a column that is constant on the recorded maps into pure noise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

from combat.battle_solver import Board
from combat.grid_dataset import _cells, _fighters

DEFAULT_COMPONENTS = 32
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
