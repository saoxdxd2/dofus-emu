"""Rank the proxy pool by how likely each one is to survive a session.

Why this exists: a public proxy in this pool lasted under a minute in a measured sample of eleven,
so the only useful question about a candidate is not "how fast is it now" but "will it still be
there in five minutes". A single latency probe cannot answer that; a short time series can.

The score below is the same signal a trained model would consume — median latency, jitter, and the
trend across the series — kept transparent and inspectable until there is a model worth trusting.
`models/` has decision-value and encoder checkpoints but nothing trained on network health, so
pretending otherwise would just be a black box with no training behind it.

Output: proxies_ranked.txt, one URL per line, best first. The supervisor reads it and prefers that
order; absent or stale, it falls back to its own ranking.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.pool_runner import read_pool, deep_check  # noqa: E402

SAMPLES = 8
SAMPLE_GAP_S = 0.25


def sample_series(entry: str, samples: int, timeout: float) -> list[float]:
    """Return the latency of each successful probe, in order. Empty list means unusable."""
    series: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        ok, _why = deep_check(entry, timeout)
        if not ok:
            # A single failure is information, not a disqualification: it is the strongest
            # predictor there is that this candidate is on its way out.
            series.append(float("inf"))
            continue
        series.append((time.perf_counter() - started) * 1000.0)
        time.sleep(SAMPLE_GAP_S)
    return series


def score(series: list[float]) -> tuple[float, str]:
    """Lower is better. Returns (score, human-readable reason)."""
    good = [x for x in series if x != float("inf")]
    losses = len(series) - len(good)
    if not good:
        return 1e9, "no successful sample"

    median = statistics.median(good)
    jitter = statistics.pstdev(good) if len(good) > 1 else 0.0

    # Trend: compare the first half against the second. Rising latency is what a proxy looks like
    # shortly before it stops answering.
    half = max(1, len(good) // 2)
    first = statistics.mean(good[:half])
    second = statistics.mean(good[half:]) if len(good) > half else first
    trend = (second - first) / max(first, 1.0)

    # Losses dominate: one failed sample in eight is worse than any amount of jitter.
    total = median * (1.0 + max(0.0, trend)) + jitter * 0.5 + losses * 4000.0
    reason = "median %.0fms jitter %.0fms trend %+.0f%% loss %d/%d" % (
        median, jitter, trend * 100.0, losses, len(series))
    return total, reason


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=SAMPLES)
    ap.add_argument("--timeout", type=float, default=4.0)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "..", "asset_cache", "proxies_ranked.txt"))
    args = ap.parse_args()

    pool = read_pool()
    if not pool:
        print("pool is empty; nothing to rank")
        return 1

    def work(entry: str):
        return entry, score(sample_series(entry, args.samples, args.timeout))

    # Proxies are probed in parallel so the whole ranking fits inside the lifetime of the things
    # being ranked. Serially this would take longer than they last.
    with ThreadPoolExecutor(max_workers=min(16, len(pool))) as ex:
        results = list(ex.map(work, pool))

    results.sort(key=lambda r: r[1][0])
    usable = [(e, s) for e, s in results if s[0] < 1e8]

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# ranked %s — %d of %d usable\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), len(usable), len(pool)))
        for entry, (sc, why) in usable:
            f.write("%s\n" % entry)
            print("%-34s %8.0f  %s" % (entry, sc, why))
    os.replace(tmp, out)
    print("\nwrote %d candidates to %s" % (len(usable), out))
    return 0 if usable else 1


if __name__ == "__main__":
    raise SystemExit(main())
