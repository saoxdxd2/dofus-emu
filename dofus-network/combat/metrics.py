"""Shared probability metrics for the offline benchmarks."""

from __future__ import annotations

import numpy as np


def auc(y: np.ndarray, p: np.ndarray) -> float:
    order = np.argsort(p)
    y, p = y[order], p[order]
    ranks = np.empty(len(p), dtype=float)
    i = 0
    while i < len(p):
        j = i
        while j + 1 < len(p) and p[j + 1] == p[i]:
            j += 1
        ranks[i:j + 1] = (i + j) / 2.0 + 1.0
        i = j + 1
    positives = float(y.sum())
    negatives = float(len(y) - positives)
    if positives <= 0 or negatives <= 0:
        return float("nan")
    return float((ranks[y == 1].sum() - positives * (positives + 1) / 2.0)
                 / (positives * negatives))


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    for k in range(bins):
        upper = edges[k + 1] if k < bins - 1 else edges[k + 1] + 1e-9
        mask = (p >= edges[k]) & (p < upper)
        if mask.any():
            total += mask.mean() * abs(p[mask].mean() - y[mask].mean())
    return float(total)
