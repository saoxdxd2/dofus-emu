"""Streaming PCA: does the subspace converge, and does the residual catch a bad board?

``StreamingPCA`` replaces the one-shot fit on the live path, so it has to be as
good as the batch reference and the OOD number has to be cheap enough to run per
decision. These checks use synthetic boards with a known 8-dimensional structure
plus noise, which is the only way to know that a small residual means "explained"
rather than "the basis memorised this row".
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from combat import board_pca  # noqa: E402

WIDTH = 560 * 14 + 16 * 20   # 8,160: the real board matrix width
RANK = 8


def _corpus(rows: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    basis = rng.normal(size=(RANK, WIDTH)).astype(np.float32)
    basis /= np.linalg.norm(basis, axis=1, keepdims=True)
    weights = rng.normal(size=(rows, RANK)).astype(np.float32)
    noise = 0.01 * rng.normal(size=(rows, WIDTH)).astype(np.float32)
    return weights @ basis + noise


def _subspace_overlap(a: np.ndarray, b: np.ndarray) -> float:
    """Mean squared cosine between the row spaces (1.0 = identical)."""
    qa, _ = np.linalg.qr(a.T)
    qb, _ = np.linalg.qr(b.T)
    return float(np.mean(np.sum((qa.T @ qb) ** 2, axis=1)))


def test_streaming_converges_to_the_batch_subspace() -> None:
    corpus = _corpus(96)
    batch = board_pca.fit([row for row in corpus], components=RANK)
    state = board_pca.StreamingPCA(WIDTH, components=RANK)
    for start in range(0, 96, 8):
        state.update(corpus[start:start + 8])
    assert state.basis.shape == (RANK, WIDTH)
    assert state.count == 96
    overlap = _subspace_overlap(state.basis, batch["components"])
    assert overlap > 0.98, "streaming basis diverged from the batch fit: %.4f" % overlap


def test_basis_stays_orthonormal() -> None:
    state = board_pca.StreamingPCA(WIDTH, components=RANK)
    corpus = _corpus(64)
    for start in range(0, 64, 8):
        state.update(corpus[start:start + 8])
    gram = state.basis @ state.basis.T
    off = gram - np.eye(RANK)
    assert float(np.max(np.abs(off))) < 1e-4, np.max(np.abs(off))


def test_residual_separates_a_corrupt_board() -> None:
    corpus = _corpus(80)
    state = board_pca.StreamingPCA(WIDTH, components=RANK)
    state.update(corpus)
    # An in-distribution board: almost all of it lies in the subspace.
    good = float(np.mean([state.residual(row) for row in corpus[:8]]))
    # A corrupted observation: a cell map that became all-walkable/all-seeing is
    # dense where the real boards are sparse, so the basis cannot explain it.
    corrupt = np.zeros(WIDTH, dtype=np.float32)
    corrupt[:560 * 14] = 1.0
    bad = state.residual(corrupt)
    assert bad > 5.0 * good, (good, bad)
    # Calibration makes that a boolean the solver can drop a board on.
    state.calibrate([state.residual(row) for row in corpus])
    assert state.calibrated
    assert not state.score(corpus[0])["ood"]
    assert state.score(corrupt)["ood"]


def test_score_is_under_the_latency_budget() -> None:
    corpus = _corpus(64)
    state = board_pca.StreamingPCA(WIDTH, components=32)
    state.update(corpus)
    state.calibrate([state.residual(row) for row in corpus])
    state.score(corpus[0])  # warm the float32 casts
    timings = []
    for row in corpus[:32]:
        started = time.perf_counter()
        state.residual(row)
        timings.append((time.perf_counter() - started) * 1000.0)
    median = float(np.median(timings))
    assert median < 0.2, "residual median %.3f ms exceeds the 0.2 ms budget" % median


def test_model_roundtrip_keeps_the_threshold() -> None:
    import tempfile

    corpus = _corpus(48)
    state = board_pca.StreamingPCA(WIDTH, components=RANK)
    state.update(corpus)
    state.calibrate([state.residual(row) for row in corpus])
    with tempfile.TemporaryDirectory() as tmp:
        path = board_pca.save(state.to_model(), Path(tmp) / "pca.json")
        loaded = board_pca.load(path)
        again = board_pca.StreamingPCA.from_model(loaded)
    assert again.calibrated
    assert abs(again.threshold - state.threshold) < 1e-6
    assert abs(again.residual(corpus[0]) - state.residual(corpus[0])) < 1e-3
    assert np.allclose(again.basis, state.basis, atol=1e-5)


def test_non_finite_rows_are_refused() -> None:
    state = board_pca.StreamingPCA(WIDTH, components=4)
    row = np.zeros((1, WIDTH), dtype=np.float32)
    row[0, 3] = np.nan
    try:
        state.update(row)
    except ValueError:
        pass
    else:
        raise AssertionError("a NaN board was folded into the subspace")
    assert state.count == 0


def test_ood_check_wrapper_is_a_no_op_for_unparseable_payloads() -> None:
    state = board_pca.StreamingPCA(WIDTH, components=4)
    assert board_pca.ood_check({"not": "a board"}, state) is None


def _main() -> int:
    names = sorted(name for name in globals() if name.startswith("test_"))
    failed = 0
    for name in names:
        try:
            globals()[name]()
        except AssertionError as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
        else:
            print("ok   %s" % name)
    print("%d streaming-pca checks, %d failed" % (len(names), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
