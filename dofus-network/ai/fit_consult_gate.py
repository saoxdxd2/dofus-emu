"""Fit the consult gate for one model artifact, on train, reported on val.

The gate is one scalar: below this top-2 logit margin, hand the decision to the rule
planner. It was fitted for the 42-wide head and shipped as 0.75, which produced 0.9099
against 0.9043 ungated. A different architecture produces a different margin scale, so
reusing that number would defer on a quantity it was never fitted against -- either
never (the gate silently disappears) or always (the model silently stops deciding).

So the floor is fitted per artifact and bound to its hash. If the model changes, the
gate stops applying until it is refitted; it does not carry a stale threshold forward.

Fitting uses the corpus's `teacher` column, which is what the rule planner answered on
that state -- exactly what the runtime gets back when the head defers. The floor is
chosen on TRAIN and reported on VAL, so the number in the artifact is not the number
that was selected for.

    python ai/fit_consult_gate.py --model models/d_seq/policy.onnx
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.dedicated_policy import build, load_tools  # noqa: E402

GATE_PATH = REPO_ROOT / "models" / "consult_gate.json"


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
    the first version carried the previous episode's actions into the next one, and
    the only symptom was a validation number that disagreed with the trainer's by
    ten rows -- close enough to be dismissed as noise. There is one implementation.
    """
    from agent.weight_space import _history_rows
    state = np.array([r["x"] for r in rows], dtype="float32")
    return np.hstack([state, np.array(_history_rows(rows), dtype="float32")])


def accuracy_at(logits: np.ndarray, rows: List[Dict[str, Any]],
                tools: Sequence[str], floor: float) -> Dict[str, float]:
    index = {t: i for i, t in enumerate(tools)}
    y = np.array([index[r["tool"]] for r in rows])
    teacher = np.array([index.get(r.get("teacher"), -1) for r in rows])
    pred = logits.argmax(axis=1)
    ordered = np.sort(logits, axis=1)
    margin = ordered[:, -1] - ordered[:, -2]
    gated = np.where(margin < floor, teacher, pred)
    # Task optimality is scored the same way: a deferred row gets the teacher's tool.
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
            if abs(float(gains.get(tools[k], 0.0)) - best) < 1e-9:
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
    args = parser.parse_args(argv)

    model = Path(args.model)
    root = Path(args.data)
    tools = load_tools()
    train_rows = ordered_rows(root / "train.jsonl")
    val_rows = ordered_rows(root / "val.jsonl")
    train_logits = logits_for(model, train_rows, tools)
    val_logits = logits_for(model, val_rows, tools)

    floors = np.arange(0.0, args.max_floor + 1e-9, args.step)
    scored = [accuracy_at(train_logits, train_rows, tools, float(f)) for f in floors]
    best_i = int(np.argmax([s["accuracy"] for s in scored]))
    floor = float(floors[best_i])
    on_train = scored[best_i]
    on_val = accuracy_at(val_logits, val_rows, tools, floor)

    doc = {
        "floor": round(floor, 4),
        "model_sha256": sha256_file(model),
        "model": model.name,
        "fitted_on": "train episodes",
        "train": {"rows": len(train_rows), "accuracy": round(on_train["accuracy"], 4)},
        "val": {"rows": len(val_rows),
                "accuracy": round(on_val["accuracy"], 4),
                "optimal": round(on_val["optimal"], 4),
                "ungated_accuracy": round(on_val["ungated_accuracy"], 4),
                "ungated_optimal": round(on_val["ungated_optimal"], 4)},
        "note": "Below this top-2 margin the rule planner answers instead. Bound to "
                "model_sha256: a different artifact disables the gate until refitted.",
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
