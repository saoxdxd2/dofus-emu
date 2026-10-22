"""The planner with a learned tool choice instead of an if-chain.

`QuestPlanner.plan_objective` decides with `if t in MAP_TYPES: ... elif t in TALK_TYPES:
...`. This class keeps the same interface but asks a small network which tool to use,
so the choice can be corrected by training rather than by editing code.

What is learned: **which tool**.
What is still plumbing: turning the objective's own parameters into arguments
(`parameter0` is the npc id for a talk objective). That is data movement; learning it
would only add a way to be wrong.

The model is optional. Without it the deterministic planner is used, which is what
makes this safe to adopt gradually: point `--planner-model` at a candidate, compare
the simulator score, and keep whichever is better.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.planner import QuestPlanner, _params, args_for_tool

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = REPO_ROOT / "models" / "tool_policy.onnx"


class NNPlanner(QuestPlanner):
    def __init__(self, model_path: Optional[Path] = None,
                 tool_penalty: Optional[Dict[str, float]] = None,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tools: List[str] = []
        self.session = None
        self.input_name = "features"
        self.expected_features = 0
        self._warned = False
        # A prior on how much a tool costs when it is the wrong call. The network
        # outputs a distribution over tools; the *decision* is not the argmax of that
        # distribution unless every mistake costs the same, and they do not: a spurious
        # nav.goto burns up to three steps in a world where travel is charged, while a
        # spurious farm.start burns one. Subtracting the cost before the argmax is the
        # decision-theoretic choice, and it is measurable.
        self.tool_penalty = dict(tool_penalty or {})
        self.note = "no model (deterministic planner)"
        path = Path(model_path or DEFAULT_MODEL)
        if path.exists():
            try:
                import onnxruntime as ort
                self.session = ort.InferenceSession(str(path),
                                                    providers=["CPUExecutionProvider"])
                self.input_name = self.session.get_inputs()[0].name
                self.expected_features = int(self.session.get_inputs()[0].shape[1])
                meta = path.with_suffix(".json")
                if meta.exists():
                    payload = json.loads(meta.read_text(encoding="utf-8"))
                    self.tools = payload.get("tools") or []
                    self.expected_features = int(payload.get("features")
                                                 or self.expected_features)
                self.note = "tool policy %s (%d tools)" % (path.name, len(self.tools))
            except Exception as exc:  # noqa: BLE001
                self.note = "model unusable (%s); deterministic fallback" % exc

    # -- learned choice ----------------------------------------------------

    def _choose_tool(self, quest: Dict[str, Any], step_idx: int,
                     obj: Dict[str, Any], obs: Dict[str, Any]) -> Optional[str]:
        if self.session is None or not self.tools:
            return None
        try:
            import numpy as np
            from agent.features import objective_features
            level = int(quest.get("level_min") or 1)
            x = np.asarray([objective_features(obj, level, step_idx, obs,
                                               self.tracker.ctx())], dtype="float32")
            if x.shape[1] != self.expected_features:
                raise ValueError("model wants %d features, featuriser gives %d"
                                 % (self.expected_features, x.shape[1]))
            logits = self.session.run(None, {self.input_name: x})[0][0]
            scores = list(logits)
            if self.tool_penalty:
                for name, cost in self.tool_penalty.items():
                    if name in self.tools:
                        scores[self.tools.index(name)] -= cost
            return self.tools[int(max(range(len(scores)), key=lambda i: scores[i]))]
        except Exception as exc:  # noqa: BLE001
            # A shape mismatch used to be swallowed here and the planner silently fell
            # back to the if-chain, which looks like "the model is no better than the
            # rules" instead of "the model is not running". Say it once, out loud.
            if not self._warned:
                self.note = "model unusable (%s); deterministic fallback" % exc
                self._warned = True
            return None

    # -- arguments (plumbing) ----------------------------------------------

    def _args_for(self, tool: str, quest: Dict[str, Any], obj: Dict[str, Any],
                  obs: Dict[str, Any]) -> Dict[str, Any]:
        return args_for_tool(tool, quest, obj, obs)

    def plan_objective(self, quest: Dict[str, Any], step_idx: int,
                       obj: Dict[str, Any], obs: Dict[str, Any]) -> List[Dict[str, Any]]:
        tool = self._choose_tool(quest, step_idx, obj, obs)
        if tool is None:
            return super().plan_objective(quest, step_idx, obj, obs)
        args = self._args_for(tool, quest, obj, obs)
        if tool in ("nav.goto", "quest.advance") and not args:
            return []
        return [{"tool": tool, "args": args, "why": "learned choice"}]
