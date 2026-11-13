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

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.features import DELIVER_TYPES, FIGHT_TYPES, required_map
from agent.planner import TALK_TYPES, QuestPlanner, _params, args_for_tool
from agent import consult_gate

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = Path(os.environ.get("DOFUS_TOOL_POLICY_MODEL") or (REPO_ROOT / "models" / "tool_policy.onnx"))


class NNPlanner(QuestPlanner):
    head: str = "mlp"
    capability_tools: List[str] = []
    trained_tools: List[str] = []
    untrained_penalty: float = 0.0
    descriptor_input: str = ""
    _descriptor_matrix: Any = None

    def __init__(self, model_path: Optional[Path] = None,
                 tool_penalty: Optional[Dict[str, float]] = None,
                 stack_mode: str = "shadow",
                 stack_path: Optional[Path] = None,
                 **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tools: List[str] = []
        self.session = None
        self.input_name = "features"
        self.expected_features = 0
        self.arch = "mlp"
        self.history_steps = 0
        # Dynamic capability head: the sidecar names the descriptor-scored
        # contract. ``tools`` then covers the whole live registry, not the 21
        # learned labels, and the graph takes a second ``descriptors`` input.
        self.head = "mlp"
        self.capability_tools: List[str] = []
        self.trained_tools: List[str] = []
        self.untrained_penalty = 0.0
        self.descriptor_input = ""
        self._descriptor_matrix: Any = None
        # Whether each history token also carries that step's observation summary.
        # Read from the sidecar, never inferred: a 33-wide token and a 28-wide one
        # both fit a float32 row, so a wrong guess would score silently.
        self.history_obs = False
        self.model_sha256: Optional[str] = None
        self._warned = False
        self._model_loaded = False
        self.model_path = Path(model_path or DEFAULT_MODEL)
        # Loading onnxruntime and creating the session is ~100 ms and not
        # needed for farm/order goals that never ask the head for a tool.
        self.note = "tool policy not loaded (lazy)"
        # A prior on how much a tool costs when it is the wrong call. The network
        # outputs a distribution over tools; the *decision* is not the argmax of that
        # distribution unless every mistake costs the same, and they do not: a spurious
        # nav.goto burns up to three steps in a world where travel is charged, while a
        # spurious farm.start burns one. Subtracting the cost before the argmax is the
        # decision-theoretic choice, and it is measurable.
        self.tool_penalty = dict(tool_penalty or {})
        self.stack_mode = stack_mode if stack_mode in ("off", "shadow", "act") else "shadow"
        self.stack_path = Path(stack_path) if stack_path else None
        self.stack = None
        self.foresight = None
        self._stack_loaded = False
        self.stack_note = "stack not loaded"
        self.last_stack: Optional[Dict[str, Any]] = None
        # The fitted consult gate's two readings for this call, kept so a shadow row
        # can say how sure the head was and that the rules took it over.
        self.last_margin: Optional[float] = None
        self.last_deferral: Optional[float] = None
        # Predicted probability that the next action advances the objective, when the
        # loaded model has a value head. None for the shipped 21-logit MLP.
        self.last_value: Optional[float] = None

    def _ensure_model(self) -> None:
        if self._model_loaded:
            return
        self._model_loaded = True
        path = self.model_path
        if not path.exists():
            self.note = "no model (deterministic planner)"
            return
        try:
            from agent.model_cache import onnx_session
            self.session = onnx_session(str(path))
            self.model_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            self.input_name = self.session.get_inputs()[0].name
            self.expected_features = int(self.session.get_inputs()[0].shape[1])
            meta = path.with_suffix(".json")
            if meta.exists():
                payload = json.loads(meta.read_text(encoding="utf-8"))
                self.tools = payload.get("tools") or []
                self.expected_features = int(payload.get("features")
                                             or self.expected_features)
                # A sidecar that declares an architecture is the only thing allowed
                # to change the input contract: the 42-wide head and a 266-wide
                # sequence policy must not be distinguishable by guesswork.
                self.arch = str(payload.get("arch") or "mlp")
                self.head = str(payload.get("head") or "mlp")
                self.trained_tools = list(payload.get("trained_tools") or self.tools)
                self.untrained_penalty = float(payload.get("untrained_penalty") or 0.0)
                if self.head == "capability":
                    self.capability_tools = list(
                        payload.get("capability_tools") or self.tools)
                    self.tools = list(self.capability_tools)
                    inputs = self.session.get_inputs()
                    if len(inputs) < 2:
                        raise ValueError("capability sidecar but graph has one input")
                    self.descriptor_input = inputs[1].name
                    if not self.trained_tools:
                        self.trained_tools = list(payload.get("tools") or [])
                self.history_steps = int((payload.get("history") or {}).get("steps") or 0)
                from agent.features import HISTORY_TOKEN_OBS
                self.history_obs = (
                    int((payload.get("history") or {}).get("token") or 0)
                    == HISTORY_TOKEN_OBS)
            self.note = "tool policy %s (%d tools%s%s)" % (
                path.name, len(self.tools),
                ", +history" if self.history_steps else "",
                ", capability head" if self.head == "capability" else "")
        except Exception as exc:  # noqa: BLE001
            self.note = "model unusable (%s); deterministic fallback" % exc

    # -- stacked choice ----------------------------------------------------

    def _descriptors(self) -> Any:
        """Descriptor matrix for the live tool order, rebuilt from the registry.

        A capability appended after training has no row in the checkpoint, but
        it has an entry in ``data/capabilities.json``; encoding it here is what
        makes the head grow without a retrain. The matrix is cached because it
        only changes when the registry does.
        """
        if self._descriptor_matrix is not None:
            return self._descriptor_matrix
        import numpy as np
        from ai.capability_head import descriptor_table, load_capability_records

        records = load_capability_records()
        _, matrix = descriptor_table(self.tools, records)
        self._descriptor_matrix = np.asarray(matrix, dtype=np.float32)
        return self._descriptor_matrix

    def _ensure_stack(self) -> None:
        if self._stack_loaded or self.stack_mode == "off":
            return
        self._stack_loaded = True
        from agent.ensemble import ToolStack
        from agent.foresight import load as load_foresight

        self.stack = ToolStack.load(self.stack_path)
        self.foresight = load_foresight()
        if self.stack is None:
            self.stack_note = "stack artifact unavailable; policy alone"
        elif self.foresight is None:
            self.stack_note = "foresight unavailable; policy alone"
        else:
            self.stack_note = self.stack.note

    def _stack_choice(self, x: Any, logits: Any) -> Optional[Dict[str, Any]]:
        if self.stack_mode == "off":
            return None
        self._ensure_stack()
        if self.stack is None or self.foresight is None:
            return None
        try:
            width = getattr(self.foresight, "width", 42)
            x_state = x[:width] if hasattr(x, "__len__") and len(x) > width else x
            cells = {row["tool"]: row for row in
                     self.foresight.rank(x_state, self.tools)}
            if len(cells) < 2:
                return None
            context = self.stack.context(x) if hasattr(self.stack, "context") else None
            ranked = self.stack.rank(logits, cells, self.tools, context=context)
            if not ranked:
                return None
            for row in ranked:
                row["score"] -= float(self.tool_penalty.get(row["tool"], 0.0))
            ranked.sort(key=lambda row: row["score"], reverse=True)
            chosen = ranked[0]
            streak = getattr(self.tracker, "streak", 0)
            preempted = False
            top_tool = ranked[0]["tool"]
            top_p = cells.get(top_tool, {}).get("move_prob", 1.0)
            max_p = max((cells.get(r["tool"], {}).get("move_prob", 0.0) for r in ranked), default=0.0)
            all_inert = (max_p < 0.20)
            if streak >= 1:
                if top_p < 0.20:
                    for alt in ranked[1:]:
                        alt_tool = alt["tool"]
                        alt_p = cells.get(alt_tool, {}).get("move_prob", 0.0)
                        if alt_p >= 0.25:
                            chosen = alt
                            preempted = True
                            break
                    if not preempted and all_inert:
                        last_tool = getattr(self.tracker, "last_tool", None)
                        if last_tool != "state.snapshot":
                            chosen = {"tool": "state.snapshot", "score": 0.0}
                            preempted = True
            self.last_stack = {
                "mode": self.stack_mode,
                "active": bool(self.stack.active),
                "stack": chosen["tool"],
                "preempted": preempted,
                "all_inert": all_inert,
                "max_move_prob": round(float(max_p), 4),
                "scores": [{key: row[key] for key in ("tool", "score")}
                           for row in ranked[:5]],
            }
            return chosen
        except Exception as exc:  # noqa: BLE001 - stacking must fail open
            self.stack_note = "stack failed open: %s: %s" % (type(exc).__name__, exc)
            return None

    # -- learned choice ----------------------------------------------------

    def _choose_tool(self, quest: Dict[str, Any], step_idx: int,
                     obj: Dict[str, Any], obs: Dict[str, Any]) -> Optional[str]:
        self._ensure_model()
        if self.session is None or not self.tools:
            return None
        try:
            import numpy as np
            from agent.features import objective_features
            level = int(quest.get("level_min") or 1)
            ctx = self.tracker.ctx()
            if self.history_steps:
                # The sequence contract: state vector followed by the decision
                # window. `records()` is the same buffer the trainer replays, so the
                # model sees at serving time what it saw during training.
                from agent.features import objective_features_history
                x = np.asarray([objective_features_history(
                    obj, level, step_idx, obs, ctx, self.tracker.records(),
                    obs=self.history_obs)],
                    dtype="float32")
                # Attach this step's observation to the record that `note_result`
                # will append, taken from the vector just scored rather than from the
                # later `obs`, so the token matches the corpus one exactly.
                self.tracker.note_observation(x[0])
            else:
                x = np.asarray([objective_features(obj, level, step_idx, obs, ctx)],
                               dtype="float32")
            if x.shape[1] != self.expected_features:
                raise ValueError("model wants %d features, featuriser gives %d"
                                 % (self.expected_features, x.shape[1]))
            feeds = {self.input_name: x}
            if self.head == "capability":
                feeds[self.descriptor_input] = self._descriptors()
            outputs = self.session.run(None, feeds)
            logits = outputs[0][0]
            if len(logits) != len(self.tools):
                raise ValueError("model scored %d tools, sidecar names %d"
                                 % (len(logits), len(self.tools)))
            # A capability the corpus never labelled has a descriptor and a
            # score but no calibration. Penalise it by a fixed scalar instead
            # of masking it: it can still win when the state matches it
            # strongly, which is the point of a growable head.
            if self.head == "capability" and self.untrained_penalty:
                trained = set(self.trained_tools)
                logits = logits.copy()
                for index, name in enumerate(self.tools):
                    if name not in trained:
                        logits[index] -= self.untrained_penalty
            # Legality is applied to the logits, before any softmax or argmax.
            # Doing it afterwards lets an illegal tool keep probability mass and
            # corrupts both the choice and the confidence the gate reads.
            try:
                from ai.guard_policy import legality_mask
                logits = legality_mask(logits, self.tools, obs)
            except Exception:  # noqa: BLE001 - legality must never break planning
                pass
            # The dedicated model also predicts whether this step advances the
            # objective. It is not used to decide here -- it is recorded, because a
            # value that is logged can be calibrated against outcomes later, and a
            # value that is never read is decoration.
            if len(outputs) > 1 and getattr(outputs[1], "size", 0):
                try:
                    raw = float(outputs[1].reshape(-1)[0])
                    self.last_value = 1.0 / (1.0 + math.exp(-raw))
                except (TypeError, ValueError, OverflowError):
                    self.last_value = None
            scores = list(logits)
            if self.tool_penalty:
                for name, cost in self.tool_penalty.items():
                    if name in self.tools:
                        scores[self.tools.index(name)] -= cost
            policy_tool = self.tools[int(max(range(len(scores)),
                                             key=lambda i: scores[i]))]
            stacked = self._stack_choice(x[0], logits)
            if self.last_stack is not None:
                self.last_stack["policy"] = policy_tool
                self.last_stack["chosen"] = (
                    stacked["tool"] if stacked is not None
                    and self.stack is not None and self.stack.active
                    and self.stack_mode == "act" else policy_tool)
            if (stacked is not None and self.stack is not None
                    and self.stack.active and self.stack_mode == "act"):
                return str(stacked["tool"])
            # The consult gate, fitted and cross-validated on the episode-held-out
            # split: where the head's top-2 logit margin is small, the rule planner
            # answers better than the head (0.9099 vs 0.9043, 26 rescued against 5
            # broken, paired-sign p=0.0002, and a random override at the same gate
            # scores 0.8884 -- so the second opinion is what pays, not the gate).
            # Returning no tool hands the objective to QuestPlanner's own rules.
            self.last_margin = consult_gate.margin(logits)
            # Only a gate fitted for *this* artifact is allowed to defer. The floor
            # is a property of one model's logit scale; `applies_to` checks the hash
            # so a new architecture acts on its own answer instead of being judged
            # by a threshold nobody measured for it.
            if consult_gate.applies_to(self.model_sha256, self.arch) \
                    and consult_gate.should_defer(gap=self.last_margin):
                self.last_deferral = self.last_margin
                return None
            return policy_tool
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
        try:
            obj_type = int(obj.get("type_id"))
        except (TypeError, ValueError):
            obj_type = None
        # Safety net, not a decision rule: farm.start is a standing order, and
        # starting one inside a quest changes what the session is doing. The
        # corpus now excludes it on kill objectives, so the installed head only
        # chooses it to gather a delivery item; the gate stays so an older or
        # regenerated head cannot silently turn a quest into a farm session.
        if tool == "farm.start" and obj_type not in DELIVER_TYPES:
            return super().plan_objective(quest, step_idx, obj, obs)
        if obj_type in FIGHT_TYPES:
            monster = _params(obj).get("parameter0")
            if self._visible_target(obs, monster) is False and self._map_npcs(obs):
                # A scripted fight with no roaming group is started by a map NPC
                # dialogue. The learned head cannot see the group list in its
                # features, so it kept exploring an empty edge; the deterministic
                # layer owns this interaction.
                return super().plan_objective(quest, step_idx, obj, obs)
        if tool == "quest.advance" and obj_type in self._server_counted_types:
            # The head was trained on labels that predate "the server completes
            # this type"; claiming one always fails. The constraint is not
            # learnable from the state, so the deterministic layer owns it.
            return super().plan_objective(quest, step_idx, obj, obs)
        if tool == "fight.join":
            # The corpus states carry no map block, so the head can learn that an
            # objective is a fight but not whether the target stands on this map.
            # The live tutorial proved the cost: join, refuse, forever. Let the
            # deterministic planner search or wait when no group is visible.
            monster = _params(obj).get("parameter0")
            if self._visible_target(obs, monster) is False:
                return super().plan_objective(quest, step_idx, obj, obs)
        if tool == "nav.goto":
            # The learned head chooses *whether* to travel; the planner still owns
            # *how*. Bypassing this sent `nav.goto {map_id: 698953}` nine times to a
            # client that only crosses an adjacent exit. A named target goes through
            # `_travel_call` (which falls back to an edge when the graph does not
            # know it); a generic DiscoverMap walks an observed exit.
            need = required_map(obj)
            if need is None and obj.get("type_id") in TALK_TYPES:
                npc = _params(obj).get("parameter0")
                if npc is not None and self.store is not None:
                    try:
                        need = self.store.get("npc_map", "npc:%d" % int(npc))
                    except Exception:  # noqa: BLE001
                        need = None
            if need is not None:
                try:
                    need = int(need) or None
                except (TypeError, ValueError):
                    need = None
            if need is not None:
                if obs.get("map_id") == need:
                    # Already at the objective's place; let the deterministic planner
                    # do the thing (talk/fight) instead of pacing around the map.
                    return super().plan_objective(quest, step_idx, obj, obs)
                call = self._travel_call(obj, need, obs)
            else:
                call = self._explore_call(obj, obs)
            if call is None:
                return []
            return [{"tool": call["tool"], "args": dict(call["args"]),
                     "why": "learned choice: " + call["why"]}]
        args = self._args_for(tool, quest, obj, obs)
        if not args and tool in ("quest.advance", "fight.join", "farm.start"):
            # These three are meaningless without the thing they act on, and the client
            # only says so after a round trip. `args_for_tool` returns empty when the
            # objective's own class declares no such parameter, and an invented target is
            # worse than no call: the loop hunts a creature that cannot exist and reads
            # the refusal as a map problem.
            return []
        return [{"tool": tool, "args": args, "why": "learned choice"}]
