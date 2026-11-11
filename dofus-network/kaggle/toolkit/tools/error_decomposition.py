"""Where does the remaining 8.3% of suboptimal decisions actually live?

The 13 unemitted tools look alarming in a coverage table, but they are the label on
only ~140 of 19,686 rows. If the model's errors were concentrated there, the fix is
corpus work; if they are concentrated on the four frequent tools, the fix is
discrimination, and adding rows for `safe.stop` would move nothing.

This decomposes the suboptimal rows two ways -- by the row's true label and by what the
model chose instead -- and separately tests whether the rare tools are ever *uniquely*
best or only tied at the top. A tool that only ever ties costs nothing to skip: the
`optimal` metric already counts a tie as correct, so the model can pick any of them.

Usage: python tools/error_decomposition.py [--model models/tool_policy.onnx]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from agent.weight_space import _onnx_logits, gain_of_row  # noqa: E402

RARE = ("knowledge.search", "nav.follow", "farm.stop", "fight.pass", "hdv.scan",
        "hdv.list", "chat.say", "safe.stop", "knowledge.remember",
        "knowledge.forget", "fight.state", "fight.plan", "fight.estimate")


def load(path: Path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/tool_policy.onnx")
    ap.add_argument("--sidecar", default="models/tool_policy.json")
    ap.add_argument("--val", default="data/tool_sft_v3/val.jsonl")
    args = ap.parse_args()

    sidecar = json.loads(Path(args.sidecar).read_text(encoding="utf-8"))
    tools = list(sidecar["tools"])
    rows = load(Path(args.val))
    logits = np.asarray(_onnx_logits(Path(args.model), rows, sidecar), dtype=np.float64)
    pred = np.argmax(logits, axis=1)

    # Is each rare tool ever uniquely best, or only tied at the top?
    unique_best = Counter()
    tied_best = Counter()
    for r in rows:
        gains = r.get("gains") or {}
        if not gains:
            continue
        top = max(gains.values())
        winners = [n for n, g in gains.items() if abs(g - top) < 1e-9]
        for n in winners:
            (unique_best if len(winners) == 1 else tied_best)[n] += 1

    suboptimal = 0
    by_label = Counter()
    by_pred = Counter()
    by_label_rows = Counter()
    lost_gain = 0.0
    rare_touch = 0
    for r, p in zip(rows, pred.tolist()):
        gains = r.get("gains") or {}
        chosen = tools[p]
        if not gains:
            continue
        top = max(gains.values())
        by_label_rows[r.get("tool")] += 1
        if abs(gain_of_row(r, chosen) - top) < 1e-9:
            continue
        suboptimal += 1
        lost_gain += top - gain_of_row(r, chosen)
        by_label[r.get("tool")] += 1
        by_pred[chosen] += 1
        if any(n in RARE for n in gains if abs(gains[n] - top) < 1e-9):
            rare_touch += 1

    n = len(rows)
    print(f"rows {n}, suboptimal {suboptimal} ({suboptimal / n:.2%}), "
          f"mean gain lost per suboptimal row {lost_gain / max(1, suboptimal):.4f}")
    print(f"suboptimal rows where a rare tool also sat at max gain: "
          f"{rare_touch} ({rare_touch / max(1, suboptimal):.1%} of errors, "
          f"{rare_touch / n:.2%} of rows)")

    print("\nrare tools: uniquely best vs only tied at the top")
    print(f"{'tool':<20}{'unique':>8}{'tied':>8}")
    for name in RARE:
        print(f"{name:<20}{unique_best.get(name,0):>8}{tied_best.get(name,0):>8}")

    print("\nsuboptimal rows by true label (top 10)")
    print(f"{'label':<20}{'wrong':>7}{'rows':>7}{'rate':>8}")
    for name, c in by_label.most_common(10):
        tot = by_label_rows.get(name, 0)
        print(f"{str(name):<20}{c:>7}{tot:>7}{c / max(1, tot):>8.1%}")

    print("\nwhat the model chose instead (top 10)")
    for name, c in by_pred.most_common(10):
        print(f"  {name:<20}{c:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
