"""Compare two artifacts on val, paired, with the staleness stratum broken out.

The observation-aware variant was built to fix one specific thing: the policy is
0.960 optimal on fresh observations and 0.791 on stale ones, and 95.4% of stale rows
have a fresh observation inside the 8-step window that the old token never carried. So
a real improvement should show up in the stale stratum and be roughly neutral in the
fresh one. If the gain is spread evenly instead, the mechanism was something else and
the result is more likely to be noise that happens to favour this seed.

Paired throughout: both arms see the same rows, so the interval is on the difference,
not on two independent point estimates.

Usage: python tools/compare_artifacts.py <base.onnx> <cand.onnx>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from agent.features import FEATURE_NAMES  # noqa: E402
from agent.weight_space import (  # noqa: E402
    _onnx_logits, gain_of_row, paired_bootstrap,
)

STALE = FEATURE_NAMES.index("obs_stale")


def load(path: Path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def correctness(rows, tools, logits):
    pred = np.argmax(logits, axis=1)
    lab, opt, gain = [], [], []
    for r, p in zip(rows, pred.tolist()):
        tool = tools[int(p)]
        gains = r.get("gains") or {}
        lab.append(tool == r.get("tool"))
        opt.append((not gains) or abs(gain_of_row(r, tool) - max(gains.values())) < 1e-9)
        gain.append(gain_of_row(r, tool))
    return np.asarray(lab, bool), np.asarray(opt, bool), np.asarray(gain)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("cand")
    ap.add_argument("--val", default="data/tool_sft_v3/val.jsonl")
    args = ap.parse_args()

    rows = load(Path(args.val))
    base_side = json.loads(Path(args.base).with_suffix(".json").read_text(encoding="utf-8"))
    cand_side = json.loads(Path(args.cand).with_suffix(".json").read_text(encoding="utf-8"))
    tools = list(base_side["tools"])
    assert tools == list(cand_side["tools"]), "tool order differs between artifacts"

    lb, ob, gb = correctness(rows, tools, np.asarray(
        _onnx_logits(Path(args.base), rows, base_side), dtype=np.float64))
    lc, oc, gc = correctness(rows, tools, np.asarray(
        _onnx_logits(Path(args.cand), rows, cand_side), dtype=np.float64))

    print(f"{'':<10}{'accuracy':>10}{'optimal':>10}{'mean_gain':>11}")
    print(f"{'base':<10}{lb.mean():>10.4f}{ob.mean():>10.4f}{gb.mean():>11.4f}")
    print(f"{'cand':<10}{lc.mean():>10.4f}{oc.mean():>10.4f}{gc.mean():>11.4f}")

    print("\npaired deltas (cand - base):")
    for name, b, c in (("accuracy", lb, lc), ("optimal", ob, oc)):
        d = paired_bootstrap(b, c)
        print(f"  {name:<9} {d['delta']:+.5f}  CI [{d['lo']:+.5f}, {d['hi']:+.5f}]  "
              f"+{d['gained']}/-{d['lost']}  significant={d['significant']}")

    print("\nby observation stratum:")
    stale = np.asarray([r["x"][STALE] > 0.5 for r in rows])
    for label, mask in (("fresh", ~stale), ("stale", stale)):
        d_opt = paired_bootstrap(ob[mask], oc[mask])
        print(f"  {label:<6} n={int(mask.sum()):>4}  optimal {ob[mask].mean():.4f} -> "
              f"{oc[mask].mean():.4f}  delta {d_opt['delta']:+.5f} "
              f"CI [{d_opt['lo']:+.5f}, {d_opt['hi']:+.5f}] +{d_opt['gained']}/-{d_opt['lost']}")

    dg = gc - gb
    print(f"\nper-row gain: improved {(dg > 1e-9).sum()}, worsened {(dg < -1e-9).sum()}, "
          f"unchanged {(np.abs(dg) <= 1e-9).sum()}, mean {dg.mean():+.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
