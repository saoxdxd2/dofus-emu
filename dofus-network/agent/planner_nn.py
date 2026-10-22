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

from agent.planner import QuestPlanner, _params

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = REPO_ROOT / "models" / "planner_student.onnx"


class NNPlanner(QuestPlanner):
    def __init__(self, model_path: Optional[Path] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tools: List[str] = []
        self.session = None
        self.input_name = "features"
        self.note = "no model (deterministic planner)"
        path = Path(model_path or DEFAULT_MODEL)
        if path.exists():
            try:
                import onnxruntime as ort
                self.session = ort.InferenceSession(str(path),
                                                    providers=["CPUExecutionProvider"])
                self.input_name = self.session.get_inputs()[0].name
                meta = path.with_suffix(".json")
                if meta.exists():
                    self.tools = json.loads(meta.read_text(encoding="utf-8")).get("tools") or []
                self.note = "planner student %s" % path.name
            except Exception as exc:  # noqa: BLE001
                self.note = "model unusable (%s); deterministic fallback" % exc

    # -- learned choice ----------------------------------------------------

    def _choose_tool(self, quest: Dict[str, Any], step_idx: int,
                     obj: Dict[str, Any], obs: Dict[str, Any]) -> Optional[str]:
        if self.session is None or not self.tools:
            return None
        try:
            import numpy as np
            from ai.train_planner_student import features
            level = int(quest.get("level_min") or 1)
            x = np.asarray([features(obj, level, step_idx, obs)], dtype="float32")
            logits = self.session.run(None, {self.input_name: x})[0][0]
            return self.tools[int(max(range(len(logits)), key=lambda i: logits[i]))]
        except Exception:  # noqa: BLE001
            return None

    # -- arguments (plumbing) ----------------------------------------------

    def _args_for(self, tool: str, quest: Dict[str, Any], obj: Dict[str, Any],
                  obs: Dict[str, Any]) -> Dict[str, Any]:
        p = _params(obj)
        t = obj.get("type_id")
        if tool == "nav.goto":
            target = p.get("parameter0") if t in (4,) else obj.get("map_id")
            return {"map_id": int(target)} if target else {}
        if tool == "quest.advance":
            npc = p.get("parameter0") if t in (1, 9, 3) else p.get("parameter1")
            args: Dict[str, Any] = {"quest_id": int(quest["id"])}
            if npc is not None:
                args["npc_id"] = int(npc)
            return args
        if tool == "farm.start":
            if t in (2, 3, 12):
                item = p.get("parameter1") if t == 3 else p.get("parameter0")
                return {"item": int(item)} if item is not None else {}
            monster = p.get("parameter0")
            return {"monster": int(monster)} if monster is not None else {}
        return {}

    def plan_objective(self, quest: Dict[str, Any], step_idx: int,
                       obj: Dict[str, Any], obs: Dict[str, Any]) -> List[Dict[str, Any]]:
        tool = self._choose_tool(quest, step_idx, obj, obs)
        if tool is None:
            return super().plan_objective(quest, step_idx, obj, obs)
        args = self._args_for(tool, quest, obj, obs)
        if tool in ("nav.goto", "quest.advance") and not args:
            return []
        return [{"tool": tool, "args": args, "why": "learned choice"}]
