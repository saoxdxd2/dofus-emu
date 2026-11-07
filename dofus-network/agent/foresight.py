"""Foresight: predict whether a candidate tool would move the world, before issuing it.

The rest of the stack decides by habit. `QuestPlanner.plan_objective` emits the one action the
rules derive from the objective type, and `ai/train_tool_policy.py` classifies the state into a
single tool. Neither can say "this candidate is inert here", which is precisely the failure the
logs measure: on the real transitions, 14.0% of issued actions changed anything and 1.5% changed
a quest counter (`data/transitions/manifest.json`).

This module is the torch-free inference side of that missing judgement. A trained net is a JSON
file of small arrays; scoring one candidate is a 105 -> 128 -> 128 -> 7 matmul in numpy, so a
whole ranking pass costs well under a millisecond and the installed client needs no python
training stack, no ONNX runtime and no service.

Layout of a scored example (identical on both sides of the sim/live boundary, because the state
vector comes from `agent.features.objective_features` in both):

    [ x_norm (42) | candidate one-hot (|TOOLS|) | x_norm * gate[candidate] (42) ]

Outputs: move logit (the world changes), expected gain (objectives per step, the harness fork
score) and the cumulative curve over DEPTH steps. The curve is what makes an escalation decision
possible: if every candidate is predicted flat, the honest action is to look again or replan, not
to try harder.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from agent.features import TOOLS, objective_features
from agent.observe import flatten
from agent.verifier import READ_ONLY_TOOLS

DEPTH = 5
HIDDEN = 128
MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "foresight_v1"


def np_shape(value: Any) -> List[int]:
    """Shape of a nested list (or an ndarray) without importing numpy at module load."""
    shape = getattr(value, "shape", None)
    if shape is not None:
        return [int(d) for d in shape]
    dims: List[int] = []
    node = value
    while isinstance(node, (list, tuple)):
        dims.append(len(node))
        node = node[0] if node else None
    return dims


class ForesightNet:
    """Numpy inference for the candidate-conditioned progress model."""

    def __init__(self, arrays: Dict[str, Any], tools: Optional[Sequence[str]] = None,
                 meta: Optional[Dict[str, Any]] = None) -> None:
        self.a = arrays
        self.tools: List[str] = list(tools or arrays.get("tools") or TOOLS)
        self.slots = {name: i for i, name in enumerate(self.tools)}
        self.width = int(np_shape(arrays["x_mean"])[-1])
        self.depth = int((meta or {}).get("depth") or arrays.get("depth") or DEPTH)
        self.meta = meta or {}
        # one fitted scalar: a ranker used as an abstention signal has to mean what it says
        self.temperature = max(1e-6, float(self.meta.get("move_temperature") or 1.0))

    # -- basics ------------------------------------------------------------

    def normalise(self, x: Sequence[float]) -> Any:
        import numpy as np
        vec = np.asarray(x, dtype=np.float32)
        if vec.shape[-1] != self.width:
            raise ValueError("foresight expects %d features, got %d" % (self.width, vec.shape[-1]))
        mean = np.asarray(self.a["x_mean"], dtype=np.float32)
        std = np.asarray(self.a["x_std"], dtype=np.float32)
        return (vec - mean) / std

    def vector(self, x: Sequence[float], tool: str) -> Any:
        import numpy as np
        xn = self.normalise(x)
        onehot = np.zeros(len(self.tools), dtype=np.float32)
        slot = self.slots.get(tool)
        if slot is None:
            return None
        onehot[slot] = 1.0
        gate = np.asarray(self.a["tool_gate"], dtype=np.float32)[slot]
        return np.concatenate([xn, onehot, xn * gate]).astype(np.float32)

    # -- scoring -----------------------------------------------------------

    def score(self, x: Sequence[float], tool: str) -> Optional[Dict[str, Any]]:
        vec = self.vector(x, tool)
        if vec is None:
            return None
        return self.score_vector(vec[None, :])[0]

    def score_vector(self, batch: Any) -> List[Dict[str, Any]]:
        import numpy as np
        a = self.a

        def relu(v):
            return np.maximum(v, 0.0)

        h = relu(np.dot(batch, np.asarray(a["w1"], dtype=np.float32))
                 + np.asarray(a["b1"], dtype=np.float32))
        h = relu(np.dot(h, np.asarray(a["w2"], dtype=np.float32))
                 + np.asarray(a["b2"], dtype=np.float32))
        out = np.dot(h, np.asarray(a["w3"], dtype=np.float32))
        out = out + np.asarray(a["b3"], dtype=np.float32)
        rows: List[Dict[str, Any]] = []
        # clipped: a large negative logit overflows exp() and would raise inside a decision loop
        # that is supposed to fail open
        probs = 1.0 / (1.0 + np.exp(-np.clip(out[:, 0] / self.temperature, -30.0, 30.0)))
        for index, raw in enumerate(out):
            curve = [float(v) for v in raw[2:2 + self.depth]]
            rows.append({
                "move_prob": float(probs[index]),
                "gain": float(raw[1]),
                "curve": curve,
                "flat": bool(all(c <= 1e-6 for c in curve)),
            })
        return rows

    def rank(self, x: Sequence[float], candidates: Iterable[str]) -> List[Dict[str, Any]]:
        """Candidates ordered by predicted gain, then by predicted movement. Reads never
        outrank a world-changing action, and a read is only ever scored as a read."""
        import numpy as np
        vectors, kept = [], []
        for tool in candidates:
            vec = self.vector(x, tool)
            if vec is None:
                continue
            vectors.append(vec)
            kept.append(tool)
        if not vectors:
            return []
        scored = self.score_vector(np.asarray(vectors, dtype=np.float32))
        out: List[Dict[str, Any]] = []
        for tool, cell in zip(kept, scored):
            out.append({"tool": tool, "read": tool in READ_ONLY_TOOLS, **cell})
        out.sort(key=lambda r: (r["read"], -r["gain"], -r["move_prob"]))
        return out


def load(path: Optional[Path] = None) -> Optional[ForesightNet]:
    """Load the artifact, or return None. Never raises: a missing model means the loop runs the
    rules, exactly as it did before this component existed."""
    import numpy as np
    target = Path(path) if path else MODEL_PATH / "foresight.json"
    try:
        with target.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None
    try:
        arrays = {k: np.asarray(v, dtype=np.float32)
                  for k, v in (raw.get("arrays") or {}).items()}
        required = ("w1", "b1", "w2", "b2", "w3", "b3", "x_mean", "x_std", "tool_gate")
        if any(name not in arrays for name in required):
            return None
        return ForesightNet(arrays, tools=raw.get("tools"), meta=raw.get("meta") or {})
    except Exception:  # noqa: BLE001 - fail open, the rule path is authoritative
        return None


def features_of(obs: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Optional[List[float]]:
    """The live 42-vector for an observation, or None when no objective is readable.

    Deliberately the *same* call the sim corpus and `agent/planner_nn.py` use, so a number
    measured on harness forks means the same thing on a real snapshot.
    """
    if not isinstance(obs, dict):
        return None
    active = obs.get("quests_active")
    level = 0
    if isinstance(active, list):
        for entry in active:
            if isinstance(entry, dict):
                try:
                    level = int(entry.get("level") or 0)
                except (TypeError, ValueError):
                    level = 0
                break
    obj = obs.get("current_objective")
    if not isinstance(obj, dict):
        ready = obs.get("ready_objectives")
        obj = next((r for r in ready if isinstance(r, dict)), None) if isinstance(ready, list) else None
    if not isinstance(obj, dict):
        return None
    try:
        vec = objective_features(obj, level, int(ctx.get("t") or 0) if ctx else 0,
                                 flatten(obs), ctx or {})
    except Exception:  # noqa: BLE001 - an unreadable state is no state at all
        return None
    return list(vec)
