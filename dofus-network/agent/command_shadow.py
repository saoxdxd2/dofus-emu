"""Read-only shadow of the learned command parser.

The rule still acts. For each goal the loop logs what the rule parsed and what
the learned encoder proposed, whether the two agree, and whether the model's
confidence would have overridden the rule. Nothing here can change a decision:
a shadow that can stop a live session is worse than no shadow.

The model is optional. ``load_command_model`` never raises -- a missing artifact
or a Python without torch/tokenizers yields an error string, and the caller logs
one status row and continues with rules.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from agent.commands import _valid_prediction, intent_tool, rule_parse
from agent.planner import _goal_mode

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = REPO_ROOT / "models" / "command_encoder30" / "heads.pt"
ROUTER_CONFIG = DEFAULT_MODEL.with_name("router.json")
ONNX_CONFIG = DEFAULT_MODEL.with_name("command_onnx.json")


class RoutedCommandModel:
    """Per-instance adapter behind a similarity router.

    The adapter heads are fitted to one user's prompts and are not safe for the
    shared distribution (a head-only fit loses base intents). The router keeps
    the base for every prompt that is not near an adapter exemplar; only a text
    close to the user's recorded goals reaches the adapter. The router uses the
    encoder's own pooled vector, not a keyword list.
    """

    def __init__(self, base_path: Path, adapter_path: Path,
                 exemplars: Sequence[str], threshold: float = 0.9) -> None:
        from ai.train_command_encoder import CommandEncoderModel

        self.base = CommandEncoderModel(Path(base_path))
        self.adapter = CommandEncoderModel(Path(adapter_path))
        self.threshold = float(threshold)
        self.exemplars = [vector for vector in
                          (self.base.embed(text) for text in exemplars)
                          if vector is not None]
        if self.exemplars:
            import torch

            self.matrix = torch.nn.functional.normalize(
                torch.stack(self.exemplars), dim=-1)

    def route_similarity(self, text: str) -> float:
        if not self.exemplars:
            return 0.0
        import torch

        vector = self.base.embed(text)
        if vector is None:
            return 0.0
        vector = torch.nn.functional.normalize(vector.unsqueeze(0), dim=-1)
        return float((self.matrix @ vector.transpose(0, 1)).max())

    def predict(self, text: str) -> Dict[str, Any]:
        if self.route_similarity(text) >= self.threshold:
            return self.adapter.predict(text)
        return self.base.predict(text)


def learned_mode(intent: str) -> str:
    """Map a parsed intent onto the planner's four goal modes.

    The live loop routes on `_goal_mode`, not on `parse_command`, so the honest
    first question about a replacement is whether it would change that routing.
    Fight/travel/stop/... are single actions and stay "order"; only a full quest,
    farm or auto request owns a mode.
    """
    if intent == "auto":
        return "auto"
    if intent == "quest":
        return "quest"
    if intent == "farm":
        return "farm"
    return "order"


def load_command_model(path: Optional[Path] = None) -> Tuple[Any, Optional[str]]:
    """Return ``(model, error)``. Never raises; heavy imports stay lazy."""
    target = Path(path or DEFAULT_MODEL)
    onnx_error = None
    if target == DEFAULT_MODEL and ONNX_CONFIG.is_file():
        try:
            from agent.command_onnx import CommandOnnxModel

            return CommandOnnxModel(ONNX_CONFIG), None
        except Exception as exc:  # noqa: BLE001 - torch fallback below
            onnx_error = f"onnx_failed: {type(exc).__name__}: {str(exc)[:160]}"
    if not target.is_file():
        return None, onnx_error or f"artifact_missing: {target}"
    for name in ("torch", "tokenizers"):
        if importlib.util.find_spec(name) is None:
            return None, onnx_error or f"dependency_missing: {name}"
    from ai.train_command_encoder import CommandEncoderModel

    if target == DEFAULT_MODEL and ROUTER_CONFIG.is_file():
        try:
            config = json.loads(ROUTER_CONFIG.read_text(encoding="utf-8"))
            texts = [row["text"] for row in
                     (json.loads(line) for line in
                      (REPO_ROOT / config["exemplars"]).read_text(
                          encoding="utf-8").splitlines() if line.strip())]
            model = RoutedCommandModel(REPO_ROOT / config["base"],
                                       REPO_ROOT / config["adapter"], texts,
                                       config.get("threshold", 0.9))
            return model, onnx_error
        except Exception as exc:  # noqa: BLE001 - fall back to the base, not to no model
            try:
                return CommandEncoderModel(target), (
                    f"router_failed: {type(exc).__name__}: {str(exc)[:160]}")
            except Exception as inner:  # noqa: BLE001
                return None, f"{type(inner).__name__}: {str(inner)[:200]}"
    try:
        return CommandEncoderModel(target), onnx_error
    except Exception as exc:  # noqa: BLE001 - the shadow must fail open
        return None, onnx_error or f"{type(exc).__name__}: {str(exc)[:200]}"


def adapter_accepts(before: Dict[str, Any], after: Dict[str, Any]) -> bool:
    """A candidate may replace the shared heads only if the held-out base command
    set does not lose an intent or an exact command. Counts, not rates: the two
    splits have different row counts and a rate can hide a lost row."""
    return (int(after.get("intent", 0)) >= int(before.get("intent", 0))
            and int(after.get("exact", 0)) >= int(before.get("exact", 0)))


def canary_accepts(before: Dict[str, Any], after: Dict[str, Any]) -> bool:
    """A per-instance update must not change any action-critical canary prompt.
    The canary is compared to the pre-update predictions, not to gold labels: it
    is a regression fence, the base set remains the accuracy gate."""
    return all(after.get(text) == value for text, value in before.items())


def shadow_row(text: str, model: Any, threshold: float = 0.55) -> Dict[str, Any]:
    """One auditable comparison row for ``text``. Never raises."""
    rule = rule_parse(text)
    rule_view = {"intent": rule.get("intent"),
                 "slots": dict(rule.get("slots") or {})}
    row: Dict[str, Any] = {
        "kind": "command_shadow",
        "text": text,
        "rule": rule_view,
        "model": None,
        "available": model is not None,
        "threshold": threshold,
        "usable": False,
        "agree_intent": None,
        "agree_command": None,
        "would_override": False,
        "rule_mode": _goal_mode(text),
        "model_mode": None,
        "mode_agree": None,
        "would_change_mode": False,
        "expected_tool": None,
    }
    if model is None:
        return row
    try:
        raw = model.predict(text)
        if not isinstance(raw, dict):
            raise TypeError(f"model returned {type(raw).__name__}")
        slots = raw.get("slots")
        model_view = {
            "intent": str(raw.get("intent") or ""),
            "slots": dict(slots) if isinstance(slots, dict) else {},
            "confidence": round(float(raw.get("confidence") or 0.0), 4),
        }
    except Exception as exc:  # noqa: BLE001 - a broken model is data, not a crash
        row["available"] = False
        row["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return row

    usable = _valid_prediction(raw)
    confidence = model_view["confidence"]
    row["model"] = model_view
    row["usable"] = usable is not None
    row["agree_intent"] = model_view["intent"] == rule_view["intent"]
    row["model_mode"] = learned_mode(model_view["intent"])
    row["mode_agree"] = row["model_mode"] == row["rule_mode"]
    row["would_change_mode"] = (usable is not None and confidence >= threshold
                                and not row["mode_agree"])
    if usable is not None:
        row["expected_tool"] = intent_tool(
            usable["intent"], (usable["slots"] or {}).get("action"))
        agree = (usable["intent"] == rule_view["intent"]
                 and usable["slots"] == rule_view["slots"])
        row["agree_command"] = agree
        row["would_override"] = confidence >= threshold and not agree
    return row
