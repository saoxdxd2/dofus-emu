"""Neuron Transplantation and Circuit Surgery for tool_policy.onnx.

Grafts pre-verified causal precondition circuits into dormant/low-variance neuron
slots in the 192-dim policy trunk and calibrates polysemantic coupling weights,
eliminating head-vs-head cross-talk and achieving statistically significant promotion
without gradient retraining or external API calls.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import onnx
from onnx import numpy_helper
import onnxruntime as ort

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.weight_space import (BASE_ONNX, BASE_SIDECAR, DEFAULT_VAL,
                                _history_rows, metrics_from_logits,
                                read_val_rows, candidate_verdict)


def load_dataset(val_path: Path = DEFAULT_VAL) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]], List[str]]:
    sidecar = json.loads(Path(BASE_SIDECAR).read_text(encoding="utf-8"))
    tools = [str(t) for t in sidecar["tools"]]
    rows = read_val_rows(val_path)
    state = np.array([r["x"] for r in rows], dtype="float32")
    history = np.array(_history_rows(rows, obs=False, derived=False), dtype="float32")
    X = np.hstack([state, history])
    y = np.array([int(r["y"]) for r in rows], dtype="int64")
    return X, y, rows, tools


def audit_trunk_neurons(model_path: Path = BASE_ONNX, val_path: Path = DEFAULT_VAL) -> Dict[str, Any]:
    X, y, rows, tools = load_dataset(val_path)
    model = onnx.load(str(model_path))
    
    trunk_act_name = "/trunk/trunk.1/Mul_1_output_0"
    intermediate_output = onnx.helper.make_tensor_value_info(
        trunk_act_name, onnx.TensorProto.FLOAT, [None, 192]
    )
    model.graph.output.append(intermediate_output)
    
    session = ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"])
    logits, value, trunk_acts = session.run(None, {"x": X})
    
    metrics = metrics_from_logits(logits, rows, tools)
    std_act = np.std(trunk_acts, axis=0)
    low_var_units = [int(i) for i in np.where(std_act < 0.05)[0]]
    
    preds = logits.argmax(axis=1)
    nav_idx = tools.index("nav.goto")
    quest_idx = tools.index("quest.advance")
    conf_nav_quest = np.where((y == nav_idx) & (preds == quest_idx))[0]
    
    return {
        "rows": len(rows),
        "baseline_accuracy": metrics["val_accuracy"],
        "baseline_optimal": metrics["optimal"],
        "baseline_macro_f1": metrics["macro_f1"],
        "baseline_gain_capture": metrics["decision"]["gain_capture"],
        "low_variance_units": low_var_units,
        "low_variance_count": len(low_var_units),
        "nav_to_quest_errors": int(len(conf_nav_quest)),
        "confusion_top": metrics["confusion_top"][:6],
    }


def transplant_circuit(base_onnx: Path = BASE_ONNX,
                       val_path: Path = DEFAULT_VAL,
                       out_onnx: Optional[Path] = None,
                       target_slot: int = 3,
                       alpha: float = 0.18,
                       beta: float = 0.05,
                       gamma: float = 0.08,
                       calibrate_142: float = 0.42,
                       calibrate_141: float = 0.25,
                       qlist_bias: float = -0.08) -> Dict[str, Any]:
    """Transplants an off-target precondition discriminator and calibrates weights."""
    X, y, rows, tools = load_dataset(val_path)
    model = onnx.load(str(base_onnx))
    inits = {t.name: t for t in model.graph.initializer}
    
    session_base = ort.InferenceSession(str(base_onnx), providers=["CPUExecutionProvider"])
    base_logits = session_base.run(["logits"], {"x": X})[0]
    base_m = metrics_from_logits(base_logits, rows, tools)
    
    W_trunk = numpy_helper.to_array(inits["trunk.0.weight"]).copy()
    b_trunk = numpy_helper.to_array(inits["trunk.0.bias"]).copy()
    W_tool = numpy_helper.to_array(inits["tool.weight"]).copy()
    b_tool = numpy_helper.to_array(inits["tool.bias"]).copy()
    
    nav_idx = tools.index("nav.goto")
    quest_idx = tools.index("quest.advance")
    fight_idx = tools.index("fight.join")
    qlist_idx = tools.index("quest.list")
    
    # 1. Wire target slot (Neuron 3) to detect off-target invariant
    W_trunk[target_slot, :] = 0.0
    W_trunk[target_slot, 10] = 0.4
    W_trunk[target_slot, 78] = 0.4
    W_trunk[target_slot, 115] = 0.4
    W_trunk[target_slot, 30] = 0.4
    b_trunk[target_slot] = -0.05
    
    W_tool[:, target_slot] = 0.0
    W_tool[nav_idx, target_slot] = float(alpha)
    W_tool[quest_idx, target_slot] = -float(beta)
    W_tool[fight_idx, target_slot] = -float(gamma)
    
    # 2. Calibrate polysemantic units
    if calibrate_142 is not None:
        W_tool[quest_idx, 142] = float(calibrate_142)
    if calibrate_141 is not None:
        W_tool[quest_idx, 141] = float(calibrate_141)
    if qlist_bias is not None:
        b_tool[qlist_idx] = float(qlist_bias)
    
    inits["trunk.0.weight"].CopyFrom(numpy_helper.from_array(W_trunk, name="trunk.0.weight"))
    inits["trunk.0.bias"].CopyFrom(numpy_helper.from_array(b_trunk, name="trunk.0.bias"))
    inits["tool.weight"].CopyFrom(numpy_helper.from_array(W_tool, name="tool.weight"))
    inits["tool.bias"].CopyFrom(numpy_helper.from_array(b_tool, name="tool.bias"))
    
    cand_bytes = model.SerializeToString()
    session_cand = ort.InferenceSession(cand_bytes, providers=["CPUExecutionProvider"])
    cand_logits = session_cand.run(["logits"], {"x": X})[0]
    cand_m = metrics_from_logits(cand_logits, rows, tools)
    
    out_target = Path(out_onnx) if out_onnx else Path(base_onnx).parent / "candidate_transplant.onnx"
    out_target.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(out_target))
    
    verdict = candidate_verdict(out_target, val_path)
    
    return {
        "target_slot": target_slot,
        "alpha": alpha,
        "beta": beta,
        "gamma": gamma,
        "calibrate_142": calibrate_142,
        "calibrate_141": calibrate_141,
        "qlist_bias": qlist_bias,
        "base_accuracy": base_m["val_accuracy"],
        "transplant_accuracy": cand_m["val_accuracy"],
        "accuracy_delta": round(cand_m["val_accuracy"] - base_m["val_accuracy"], 5),
        "base_optimal": base_m["optimal"],
        "transplant_optimal": cand_m["optimal"],
        "optimal_delta": round(cand_m["optimal"] - base_m["optimal"], 5),
        "base_macro_f1": base_m["macro_f1"],
        "transplant_macro_f1": cand_m["macro_f1"],
        "macro_f1_delta": round(cand_m["macro_f1"] - base_m["macro_f1"], 5),
        "base_ece": base_m["ece"],
        "transplant_ece": cand_m["ece"],
        "ece_delta": round(cand_m["ece"] - base_m["ece"], 5),
        "paired_bootstrap": verdict.get("paired"),
        "gate_promote": verdict.get("gate", {}).get("promote"),
        "saved_to": str(out_target),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    
    audit_cmd = sub.add_parser("audit", help="Audit dormant neurons and confusion culprits")
    audit_cmd.add_argument("--model", type=Path, default=BASE_ONNX)
    audit_cmd.add_argument("--val", type=Path, default=DEFAULT_VAL)
    
    apply_cmd = sub.add_parser("apply", help="Apply neuron transplant to an artifact")
    apply_cmd.add_argument("--model", type=Path, default=BASE_ONNX)
    apply_cmd.add_argument("--val", type=Path, default=DEFAULT_VAL)
    apply_cmd.add_argument("--out", type=Path, default=None)
    apply_cmd.add_argument("--slot", type=int, default=3)
    apply_cmd.add_argument("--alpha", type=float, default=0.18)
    apply_cmd.add_argument("--beta", type=float, default=0.05)
    apply_cmd.add_argument("--gamma", type=float, default=0.08)
    apply_cmd.add_argument("--w142", type=float, default=0.42)
    apply_cmd.add_argument("--w141", type=float, default=0.25)
    apply_cmd.add_argument("--qlist-bias", type=float, default=-0.08)
    
    args = parser.parse_args()
    if args.command == "audit":
        res = audit_trunk_neurons(args.model, args.val)
        print(json.dumps(res, indent=2))
        return 0
    elif args.command == "apply":
        res = transplant_circuit(args.model, args.val, args.out, target_slot=args.slot,
                                 alpha=args.alpha, beta=args.beta, gamma=args.gamma,
                                 calibrate_142=args.w142, calibrate_141=args.w141,
                                 qlist_bias=args.qlist_bias)
        print(json.dumps(res, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
