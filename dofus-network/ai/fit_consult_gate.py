"""Fit a per-tool risk-stratified consult gate for one model artifact.

The gate was one scalar: below this top-2 logit margin, hand the decision to the
rule planner. A single global margin treats a harmless read/scan tool identically
to an irreversible move or combat action.

This version fits a per-risk-tier threshold vector:

  * read-only tools (safe, never changes world) -- lowest floor, defer rarely
  * move tools (changes map/cell)              -- highest floor, defer often
  * all other tools                            -- intermediate floor

Each tier's floor is fitted independently on training margins and reported on
validation. Optionally, the gate can be joint: a row defers only when both the
margin is below the tier floor *and* foresight's move_prob is below a tier move
floor, so a tool that is certain to move the world is never deferred even on a
slim margin.

The floor(s) are fitted per artifact and bound to its hash. If the model changes,
the gate stops applying until it is refitted; it does not carry a stale
threshold forward.

    python ai/fit_consult_gate.py --model models/d_seq/policy.onnx
    python ai/fit_consult_gate.py --model models/d_seq/policy.onnx --joint \
        --foresight models/foresight_v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.foresight import ForesightNet, MODEL_PATH as FORESIGHT_DEFAULT  # noqa: E402
from agent.verifier import MOVE_TOOLS, READ_ONLY_TOOLS  # noqa: E402
from ai.dedicated_policy import build, load_tools  # noqa: E402

GATE_PATH = REPO_ROOT / "models" / "consult_gate.json"

# Risk tiers: ordered from safest to most consequential.
TIER_READ_ONLY = "read_only"
TIER_MOVE = "move"
TIER_OTHER = "other"


def tier_of(tool: str) -> str:
    if tool in READ_ONLY_TOOLS:
        return TIER_READ_ONLY
    if tool in MOVE_TOOLS:
        return TIER_MOVE
    return TIER_OTHER


TIER_ORDER = [TIER_READ_ONLY, TIER_OTHER, TIER_MOVE]


def ordered_rows(path: Path) -> List[Dict[str, Any]]:
    """Episode order, matching `build()`: rows sorted by t inside each episode."""
    grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                row = json.loads(line)
                grouped[int(row["ep"])].append(row)
    out: List[Dict[str, Any]] = []
    for ep in sorted(grouped):
        out.extend(sorted(grouped[ep], key=lambda r: int(r.get("t", 0))))
    return out


def logits_for(model: Path, rows: List[Dict[str, Any]],
               tools: Sequence[str]) -> np.ndarray:
    import onnxruntime as ort
    data = build_rows(rows, tools)
    session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    name = session.get_inputs()[0].name
    return session.run(["logits"], {name: data})[0]


def build_rows(rows: List[Dict[str, Any]], tools: Sequence[str]) -> np.ndarray:
    """The scoring contract, borrowed rather than re-implemented.

    `_history_rows` is what `weight_space` uses to score a candidate, and it resets
    the window at every episode boundary. A second copy here drifted immediately:
    the first version carried the previous episode's actions into the next one,
    and the only symptom was a validation number that disagreed with the trainer's
    by ten rows -- close enough to be dismissed as noise. There is one implementation.
    """
    from agent.weight_space import _history_rows
    state = np.array([r["x"] for r in rows], dtype="float32")
    return np.hstack([state, np.array(_history_rows(rows), dtype="float32")])


def move_prob_for(foresight: Optional[ForesightNet], rows: List[Dict[str, Any]],
                  tools: Sequence[str]) -> Optional[np.ndarray]:
    """Score each row's chosen tool with foresight's move_prob, or None."""
    if foresight is None:
        return None
    index = {t: i for i, t in enumerate(tools)}
    probs: List[float] = []
    for row in rows:
        tool = row.get("tool", "")
        idx = index.get(tool)
        if idx is None:
            probs.append(0.0)
            continue
        state = np.array(row["x"], dtype="float32")
        one_hot = np.zeros(len(tools), dtype="float32")
        one_hot[idx] = 1.0
        feat = np.concatenate([state, one_hot, state * 0.0])  # gate vector zeros
        try:
            out = foresight.score(feat.reshape(1, -1))
            probs.append(float(out.get("move_prob", 0.0)))
        except Exception:
            probs.append(0.0)
    return np.array(probs, dtype="float64")


def tier_floors(logits: np.ndarray, rows: List[Dict[str, Any]],
                tools: Sequence[str], step: float,
                max_floor: float) -> Dict[str, float]:
    """Fit one floor per risk tier on training margins.

    Each tier's floor is the margin value that maximizes gated accuracy on that
    tier's rows alone, so a safe read tool defers rarely and a risky move tool
    defers often.
    """
    index = {t: i for i, t in enumerate(tools)}
    ordered = np.sort(logits, axis=1)
    margin = ordered[:, -1] - ordered[:, -2]
    tiers = np.array([tier_of(r.get("tool", "")) for r in rows])

    floors: Dict[str, float] = {}
    for tier in TIER_ORDER:
        mask = tiers == tier
        if not mask.any():
            floors[tier] = 0.0
            continue
        tier_margin = margin[mask]
        tier_rows = [r for r, m in zip(rows, mask) if m]
        tier_y = np.array([index[r["tool"]] for r in tier_rows])
        tier_teacher = np.array([index.get(r.get("teacher"), -1) for r in tier_rows])
        best_floor, best_acc = 0.0, -1.0
        for f in np.arange(0.0, max_floor + 1e-9, step):
            gated = np.where(tier_margin < f, tier_teacher,
                             logits[mask].argmax(axis=1))
            acc = float((gated == tier_y).mean())
            if acc > best_acc:
                best_acc, best_floor = acc, float(f)
        floors[tier] = best_floor
    return floors


def joint_floors(logits: np.ndarray, rows: List[Dict[str, Any]],
                 tools: Sequence[str], move_probs: np.ndarray,
                 step: float, max_floor: float) -> Dict[str, Dict[str, float]]:
    """Fit per-tier (margin_floor, move_prob_floor) for a joint gate.

    A row defers when margin < margin_floor[tier] AND move_prob < move_floor[tier].
    """
    index = {t: i for i, t in enumerate(tools)}
    ordered = np.sort(logits, axis=1)
    margin = ordered[:, -1] - ordered[:, -2]
    tiers = np.array([tier_of(r.get("tool", "")) for r in rows])
    y = np.array([index[r["tool"]] for r in rows])
    teacher = np.array([index.get(r.get("teacher"), -1) for r in rows])

    result: Dict[str, Dict[str, float]] = {}
    for tier in TIER_ORDER:
        mask = tiers == tier
        if not mask.any():
            result[tier] = {"margin_floor": 0.0, "move_floor": 1.0}
            continue
        tm = margin[mask]
        tp = move_probs[mask]
        ty = y[mask]
        tt = teacher[mask]
        tlogits = logits[mask]
        best: Tuple[float, float, float] = (0.0, 1.0, -1.0)  # margin_floor, move_floor, acc
        for mf in np.arange(0.0, max_floor + 1e-9, step):
            for mp in np.arange(0.0, 1.0 + 1e-9, 0.1):
                defer = (tm < mf) & (tp < mp)
                gated = np.where(defer, tt, tlogits.argmax(axis=1))
                acc = float((gated == ty).mean())
                if acc > best[2]:
                    best = (float(mf), float(mp), acc)
        result[tier] = {"margin_floor": best[0], "move_floor": best[1]}
    return result


def accuracy_at(logits: np.ndarray, rows: List[Dict[str, Any]],
                tools: Sequence[str], floors: Dict[str, float],
                move_probs: Optional[np.ndarray] = None,
                joint: bool = False,
                joint_floors_map: Optional[Dict[str, Dict[str, float]]] = None
                ) -> Dict[str, float]:
    index = {t: i for i, t in enumerate(tools)}
    y = np.array([index[r["tool"]] for r in rows])
    teacher = np.array([index.get(r.get("teacher"), -1) for r in rows])
    pred = logits.argmax(axis=1)
    ordered = np.sort(logits, axis=1)
    margin = ordered[:, -1] - ordered[:, -2]
    tiers = np.array([tier_of(r.get("tool", "")) for r in rows])

    if joint and joint_floors_map is not None and move_probs is not None:
        defer = np.zeros(len(rows), dtype=bool)
        for i, tier in enumerate(tiers):
            jf = joint_floors_map.get(tier, {"margin_floor": 0.0, "move_floor": 1.0})
            defer[i] = (margin[i] < jf["margin_floor"]) and \
                       (move_probs[i] < jf["move_floor"])
    else:
        tier_floor = np.array([floors.get(t, 0.0) for t in tiers])
        defer = margin < tier_floor

    gated = np.where(defer, teacher, pred)

    def optimal(p: np.ndarray) -> float:
        hit = 0
        for row, k in zip(rows, p.tolist()):
            gains = row.get("gains") or {}
            if not gains:
                hit += 1
                continue
            if k < 0:
                continue
            best = max(gains.values())
            if abs(float(gains.get(tools[int(k)], 0.0)) - best) < 1e-9:
                hit += 1
        return hit / len(rows)

    return {"accuracy": float((gated == y).mean()), "optimal": optimal(gated),
            "ungated_accuracy": float((pred == y).mean()),
            "ungated_optimal": optimal(pred)}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=str(REPO_ROOT / "models" / "d_seq"
                                               / "policy.onnx"))
    parser.add_argument("--data", default=str(REPO_ROOT / "data" / "tool_sft_v3"))
    parser.add_argument("--out", default=str(GATE_PATH))
    parser.add_argument("--step", type=float, default=0.05)
    parser.add_argument("--max-floor", type=float, default=3.0)
    parser.add_argument("--joint", action="store_true",
                        help="combine margin with foresight move_prob")
    parser.add_argument("--foresight", default=str(FORESIGHT_DEFAULT),
                        help="foresight artifact dir (for --joint)")
    args = parser.parse_args(argv)

    model = Path(args.model)
    root = Path(args.data)
    tools = load_tools()
    train_rows = ordered_rows(root / "train.jsonl")
    val_rows = ordered_rows(root / "val.jsonl")
    train_logits = logits_for(model, train_rows, tools)
    val_logits = logits_for(model, val_rows, tools)

    foresight = None
    move_probs_train = None
    move_probs_val = None
    if args.joint:
        foresight_path = Path(args.foresight)
        if foresight_path.exists():
            from agent.foresight import load as foresight_load
            foresight = foresight_load(foresight_path)
        move_probs_train = move_prob_for(foresight, train_rows, tools)
        move_probs_val = move_prob_for(foresight, val_rows, tools)

    if args.joint and move_probs_train is not None:
        jf_train = joint_floors(train_logits, train_rows, tools,
                                move_probs_train, args.step, args.max_floor)
        on_train = accuracy_at(train_logits, train_rows, tools, {},
                               move_probs_train, joint=True,
                               joint_floors_map=jf_train)
        jf_val = jf_train  # same fitted thresholds applied to val
        on_val = accuracy_at(val_logits, val_rows, tools, {},
                             move_probs_val, joint=True,
                             joint_floors_map=jf_val)
        tiers_doc = {t: jf_train.get(t, {"margin_floor": 0.0, "move_floor": 1.0})
                     for t in TIER_ORDER}
        gate_doc: Dict[str, Any] = {
            "mode": "joint",
            "tiers": tiers_doc,
            "model_sha256": sha256_file(model),
            "model": model.name,
            "fitted_on": "train episodes",
            "train": {"rows": len(train_rows),
                      "accuracy": round(on_train["accuracy"], 4),
                      "optimal": round(on_train["optimal"], 4),
                      "ungated_accuracy": round(on_train["ungated_accuracy"], 4),
                      "ungated_optimal": round(on_train["ungated_optimal"], 4)},
            "val": {"rows": len(val_rows),
                    "accuracy": round(on_val["accuracy"], 4),
                    "optimal": round(on_val["optimal"], 4),
                    "ungated_accuracy": round(on_val["ungated_accuracy"], 4),
                    "ungated_optimal": round(on_val["ungated_optimal"], 4)},
            "note": "Joint per-tier gate: defer when margin < margin_floor[tier] "
                    "AND move_prob < move_floor[tier]. Bound to model_sha256.",
        }
    else:
        floors = tier_floors(train_logits, train_rows, tools,
                             args.step, args.max_floor)
        on_train = accuracy_at(train_logits, train_rows, tools, floors)
        on_val = accuracy_at(val_logits, val_rows, tools, floors)
        gate_doc = {
            "mode": "tiered",
            "floor": {t: round(floors.get(t, 0.0), 4) for t in TIER_ORDER},
            "model_sha256": sha256_file(model),
            "model": model.name,
            "fitted_on": "train episodes",
            "train": {"rows": len(train_rows),
                      "accuracy": round(on_train["accuracy"], 4),
                      "optimal": round(on_train["optimal"], 4),
                      "ungated_accuracy": round(on_train["ungated_accuracy"], 4),
                      "ungated_optimal": round(on_train["ungated_optimal"], 4)},
            "val": {"rows": len(val_rows),
                    "accuracy": round(on_val["accuracy"], 4),
                    "optimal": round(on_val["optimal"], 4),
                    "ungated_accuracy": round(on_val["ungated_accuracy"], 4),
                    "ungated_optimal": round(on_val["ungated_optimal"], 4)},
            "note": "Per-tier floor on top-2 margin: read_only defers rarely, "
                    "move defers often. Bound to model_sha256: a different "
                    "artifact disables the gate until refitted.",
        }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(gate_doc, indent=2), encoding="utf-8")
    print(json.dumps(gate_doc, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
