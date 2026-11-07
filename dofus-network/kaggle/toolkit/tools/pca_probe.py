"""Measure what a PCA signature of a combat board actually buys.

The board the grid core consumes is 9,920 numbers. This reports, on whatever
boards are recorded, the full variance spectrum, the reconstruction error at a
few widths, how many near-duplicate boards a corpus holds once compressed, and
the per-board projection cost on this machine. It fits nothing downstream: with
the recorded corpus at a handful of actionable boards, any accuracy claim about
a learned head would be noise, and the label coverage is printed so a reader can
see when that changes.

    python -m tools.pca_probe --boards %LOCALAPPDATA%/DofusFarm/fight_boards.jsonl
    python -m tools.pca_probe --boards <file> --out models/board_pca_v1.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from combat import board_pca


def load_boards(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            board = row.get("board") if isinstance(row, dict) else None
            if isinstance(board, dict):
                out.append(board)
    return out


def duplicate_fraction(signatures: np.ndarray, relative: float = 1e-3) -> Dict[str, float]:
    """Fraction of pairs closer than `relative` of the median pair distance."""
    if len(signatures) < 2:
        return {"pairs": 0.0, "duplicates": 0.0, "median": 0.0}
    diff = signatures[:, None, :] - signatures[None, :, :]
    dist = np.sqrt(np.sum(diff * diff, axis=-1))
    upper = dist[np.triu_indices(len(signatures), 1)]
    median = float(np.median(upper))
    if median <= 0:
        return {"pairs": float(len(upper)), "duplicates": 1.0, "median": 0.0}
    threshold = median * relative
    dup = float(np.mean(upper <= threshold))
    return {"pairs": float(len(upper)), "duplicates": dup, "median": median,
            "threshold": float(threshold)}


def report(boards: List[Dict[str, Any]], out: Optional[Path], k: int) -> Dict[str, Any]:
    matrices = []
    parse_errors = 0
    for payload in boards:
        matrix = board_pca.matrix_from_payload(payload)
        if matrix is None:
            parse_errors += 1
            continue
        matrices.append(matrix)
    labels = sum(1 for payload in boards
                 if isinstance(payload.get("my_stats"), dict)
                 and payload.get("is_my_turn")
                 and (payload.get("spells") or []))
    result: Dict[str, Any] = {
        "boards": len(boards),
        "matrices": len(matrices),
        "parse_errors": parse_errors,
        "turn_boards_with_spells": labels,
    }
    if len(matrices) < 2:
        result["verdict"] = "not enough boards to fit a signature"
        return result

    spec = board_pca.spectrum_for(matrices)
    result.update({
        "width": spec["width"],
        "max_components": spec["max_components"],
        "k_for_90": spec.get("k_for_90"),
        "k_for_95": spec.get("k_for_95"),
        "k_for_99": spec.get("k_for_99"),
        "cumulative_head": [round(v, 4) for v in spec["cumulative"][:12]],
    })
    model = board_pca.fit(matrices, components=k)
    signatures = np.stack([board_pca.project(model, row) for row in matrices])
    result["k"] = model["k"]
    result["explained_at_k"] = round(model["explained_total"], 4)
    for width in (4, 8, 16, 32):
        if width > model["k"]:
            continue
        narrow = dict(model)
        narrow["components"] = model["components"][:width]
        err = []
        for matrix in matrices:
            sig = board_pca.project(narrow, matrix)
            err.append(float(np.mean((board_pca.reconstruct(narrow, sig) - matrix) ** 2)))
        result["reconstruction_mse_k%d" % width] = float(statistics.mean(err))
    result["duplicates"] = {key: round(value, 6)
                            for key, value in duplicate_fraction(signatures).items()}

    # Per-board cost of the live path: build + project.
    samples = min(len(matrices), 32)
    started = time.perf_counter()
    for index in range(samples):
        sig = board_pca.project(model, matrices[index])
        _ = float(sig[0])
    result["project_ms_per_board"] = round((time.perf_counter() - started) * 1000 / samples, 4)

    result["verdict"] = (
        "signature is usable: %d components carry %.1f%% of the variance"
        % (model["k"], 100 * model["explained_total"]))
    if out is not None:
        path = board_pca.save(model, out)
        result["saved"] = str(path)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boards", required=True,
                        help="JSONL file whose rows carry a `board` payload")
    parser.add_argument("--out", default=None,
                        help="where to write the fitted model (optional)")
    parser.add_argument("--k", type=int, default=board_pca.DEFAULT_COMPONENTS)
    args = parser.parse_args(argv)
    path = Path(args.boards)
    if not path.exists():
        print("no boards at %s" % path)
        return 2
    boards = load_boards(path)
    result = report(boards, Path(args.out) if args.out else None, args.k)
    print(json.dumps(result, indent=2))
    return 0 if result.get("matrices", 0) >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
