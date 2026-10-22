"""Train a student to pick the tool, so the choice stops being a hardcoded table.

`agent/planner.py` translates objective type -> tool with an `if` chain. It works,
and it is exactly the kind of thing that is right until the day it is wrong: a new
objective type, a renamed parameter, a quest whose first step is unusual, and the
chain silently does the wrong thing with no way to improve except editing code.

This trains a small network to make that choice instead:

    features(objective, state)  ->  tool

The teacher is the current table (it is correct on what it covers), so round one is
imitation. What changes the economics is that from then on the choice is a
*parameter*, and `ai/self_improve.py` can resume, correct and recalibrate it from
outcomes — which is impossible with an `if`.

What is still plumbing, not learned: turning the objective's own parameters into
argument values (`parameter0` is the npc id). That is data movement, and learning it
would only add a way to be wrong.

    python ai/train_planner_student.py --out models/planner_student.onnx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.features import objective_type  # noqa: E402
from agent.planner import QuestPlanner  # noqa: E402

# The tools the planner is allowed to choose. Order is part of the artifact.
TOOLS = ["quest.advance", "nav.goto", "farm.start", "inv.list", "state.snapshot"]

OBJECTIVE_TYPES = [0, 1, 2, 3, 4, 5, 6, 9, 10, 12, 14, 16, 17]


def features(obj: Dict[str, Any], quest_level: int, step_index: int,
             state: Dict[str, Any]) -> List[float]:
    """Everything the teacher looks at, as numbers."""
    raw = obj.get("params")
    params: Dict[str, Any] = {}
    if isinstance(raw, dict):
        params = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            params = json.loads(raw.replace("'", '"'))
        except ValueError:
            params = {}

    t = objective_type(obj)
    vec: List[float] = []
    for known in OBJECTIVE_TYPES:
        vec.append(1.0 if t == known else 0.0)
    vec.append(1.0 if t not in OBJECTIVE_TYPES else 0.0)

    vec.append(1.0 if params.get("parameter0") is not None else 0.0)
    vec.append(1.0 if params.get("parameter1") is not None else 0.0)
    vec.append(min(1.0, len(params) / 3.0))
    vec.append(1.0 if obj.get("map_id") else 0.0)
    vec.append(1.0 if obj.get("coords") else 0.0)

    vec.append(min(1.0, quest_level / 200.0))
    vec.append(min(1.0, step_index / 8.0))

    inv = state.get("inventory") or {}

    # The teacher's decision depends on whether the item this objective needs is
    # already carried. Without that feature the student learned "gather" and then
    # gathered forever: the first student scored 0 quests while the teacher scored
    # 21, purely because one input was missing.
    needed = None
    if t in (2, 3, 12):
        needed = params.get("parameter1") if t == 3 else params.get("parameter0")
    has_needed = 0.0
    if needed is not None:
        has_needed = 1.0 if (inv.get(needed) or inv.get(str(needed))) else 0.0

    target_map = obj.get("map_id") or (params.get("parameter0") if t == 4 else None)
    on_target = 0.0
    if target_map:
        on_target = 1.0 if state.get("map_id") == target_map else 0.0

    vec.append(min(1.0, len(inv) / 20.0))
    vec.append(has_needed)
    vec.append(on_target)
    vec.append(1.0 if state.get("map_id") else 0.0)
    vec.append(min(1.0, (state.get("level") or 1) / 200.0))
    return vec


def _state_variants(obj: Dict[str, Any], level: int) -> List[Dict[str, Any]]:
    """The states in which this objective can be met, as the teacher sees them."""
    raw = obj.get("params")
    params: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    if isinstance(raw, str) and raw.strip():
        try:
            params = json.loads(raw.replace("'", '"'))
        except ValueError:
            params = {}

    t = objective_type(obj)
    target_map = obj.get("map_id") or (params.get("parameter0") if t == 4 else None)
    base = {"level": level, "map_id": None, "inventory": {}}
    variants = [dict(base, inventory={}, map_id=None)]

    if t in (2, 3, 12):
        item = params.get("parameter1") if t == 3 else params.get("parameter0")
        if item is not None:
            variants.append({"level": level, "map_id": None,
                             "inventory": {int(item): 1}})
    if target_map:
        variants.append({"level": level, "map_id": int(target_map), "inventory": {}})
    return variants


def build_dataset(max_quests: int = 1200) -> Tuple[List[List[float]], List[int], List[int]]:
    """(features, tool id, objective type) for every objective the teacher can plan."""
    planner = QuestPlanner(level_hint=200, max_quest_level=200)
    xs: List[List[float]] = []
    ys: List[int] = []
    ts: List[int] = []

    for qid, quest in planner.quests.items():
        if len(xs) >= max_quests * 6:
            break
        level = int(quest.get("level_min") or 1)
        for step_idx, step in enumerate(quest.get("steps") or []):
            for obj in step.get("objectives") or []:
                # Enumerate the states that change the teacher's answer. Training on
                # one empty state taught the student "gather" and nothing else: the
                # teacher's decision depends on whether the item is carried and
                # whether we are already on the target map, so those variants have to
                # exist in the corpus or the boundary is invisible.
                for state in _state_variants(obj, level):
                    calls = planner.plan_objective(quest, step_idx, obj, state)
                    if not calls:
                        continue
                    tool = calls[0]["tool"]
                    if tool not in TOOLS:
                        continue
                    xs.append(features(obj, level, step_idx, state))
                    ys.append(TOOLS.index(tool))
                    ts.append(objective_type(obj))
    return xs, ys, ts


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "models" / "planner_student.onnx")
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--max-quests", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    try:
        import numpy as np
        import torch
    except Exception as exc:  # noqa: BLE001
        print("torch/numpy required (use runtimes\\python): %s" % exc)
        return 2

    torch.manual_seed(args.seed)
    xs, ys, ts = build_dataset(args.max_quests)
    if not xs:
        print("no training rows - is the KB present?")
        return 1

    x = np.asarray(xs, dtype="float32")
    y = np.asarray(ys, dtype="int64")
    # Split by objective type, not by row: the question is whether the student can
    # generalise the *rule*, and a random split would let it memorise instances.
    val_mask = np.isin(np.asarray(ts), [3, 6, 14])
    if val_mask.sum() < 10:
        val_mask = np.zeros(len(y), dtype=bool)
        val_mask[: max(10, len(y) // 5)] = True
    tr, va = ~val_mask, val_mask
    print("rows %d  train %d  val %d (held-out objective types %s)"
          % (len(y), tr.sum(), va.sum(), sorted(set(np.asarray(ts)[va].tolist()))))

    import torch.nn as nn
    model = nn.Sequential(
        nn.Linear(x.shape[1], args.hidden), nn.GELU(),
        nn.Linear(args.hidden, args.hidden), nn.GELU(),
        nn.Linear(args.hidden, len(TOOLS)),
    )
    opt = torch.optim.AdamW(model.parameters(), lr=3e-3, weight_decay=1e-4)
    loss_fn = nn.CrossEntropyLoss()
    tx, ty = torch.from_numpy(x[tr]), torch.from_numpy(y[tr])
    vx, vy = torch.from_numpy(x[va]), torch.from_numpy(y[va])

    best = {"acc": -1.0, "state": None}
    for epoch in range(1, args.epochs + 1):
        model.train()
        opt.zero_grad()
        loss = loss_fn(model(tx), ty)
        loss.backward()
        opt.step()
        if epoch % 20 == 0 or epoch == args.epochs:
            model.eval()
            with torch.no_grad():
                acc_tr = float((model(tx).argmax(1) == ty).float().mean())
                acc_va = float((model(vx).argmax(1) == vy).float().mean()) if va.sum() else 0.0
            print("epoch %3d  loss %.4f  train %.4f  val %.4f" % (epoch, float(loss), acc_tr, acc_va))
            if acc_va > best["acc"]:
                best = {"acc": acc_va, "state": {k: v.clone() for k, v in model.state_dict().items()}}
            model.train()

    if best["state"] is not None:
        model.load_state_dict(best["state"])
    model.eval()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(model, torch.zeros(1, x.shape[1]), str(args.out),
                      input_names=["features"], output_names=["logits"],
                      opset_version=17, dynamo=False,
                      dynamic_axes={"features": {0: "batch"}, "logits": {0: "batch"}})
    print("wrote %s (%.1f KB)" % (args.out, args.out.stat().st_size / 1024.0))

    meta = {"kind": "planner-student", "tools": TOOLS, "features": int(x.shape[1]),
            "val_accuracy": round(best["acc"], 4), "rows": int(len(y))}
    args.out.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
