"""Torch-free inference for the routed command model.

Loads ``command_onnx.json`` next to the command model: one INT8 graph with both
intent/action head sets, the shared trunk/tag head, the tokenizer, and the 16
exemplars. The router compares the pooled vector to the exemplars, exactly as
the torch wrapper does; no PyTorch or the FP32 checkpoint is needed.

The heavy imports (onnxruntime, tokenizers, numpy) stay inside the constructor so
the gate and the module import remain torch/onnxruntime-free.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

CHANNEL_ALIASES = {
    "team": "team", "équipe": "team", "equipe": "team", "equipo": "team",
    "group": "team", "groupe": "team", "grupo": "team",
    "guild": "guild", "guilde": "guild", "gremio": "guild",
    "general": "general", "général": "general",
    "trade": "trade", "échanges": "trade", "comercio": "trade",
}
NUMERIC = ("level_min", "price", "quantity", "quest_id", "x", "y")


def decode_slots(text: str, offsets: Sequence[Tuple[int, int]], tags: Sequence[int],
                 tag_names: Sequence[str], action: str,
                 user_start: int) -> Dict[str, Any]:
    """The same span decode the torch path uses, without torch."""
    slots: Dict[str, Any] = {}
    limit = min(len(tags), len(offsets))
    index = 0
    while index < limit:
        raw_tag = tags[index]
        name = tag_names[raw_tag] if 0 <= raw_tag < len(tag_names) else "O"
        if name.startswith("B-") and offsets[index][1] > user_start:
            slot = name[2:]
            start = max(offsets[index][0], user_start)
            end = offsets[index][1]
            cursor = index
            while (cursor + 1 < limit
                   and tag_names[tags[cursor + 1]] == "I-" + slot
                   and offsets[cursor + 1][1] > user_start):
                cursor += 1
                end = offsets[cursor][1]
            raw = text[start:end].strip()
            if (slot in NUMERIC and start > user_start
                    and text[start - 1] in ("-", "\u2212")):
                raw = text[start - 1] + raw
            if slot in ("all", "dungeon_only"):
                slots[slot] = True
            elif slot in NUMERIC:
                digits = re.sub(r"[^\d-]", "", raw.replace("\u2212", "-"))
                if digits not in ("", "-"):
                    try:
                        slots[slot] = int(digits)
                    except ValueError:
                        pass
            elif slot == "channel":
                slots[slot] = CHANNEL_ALIASES.get(raw.lower(), raw.lower())
            elif raw:
                slots[slot] = raw
            index = cursor + 1
        else:
            index += 1
    if "price" in slots:
        slots.setdefault("price_rule", "fixed")
    if action and action != "none":
        slots["action"] = action
    return slots


class CommandOnnxModel:
    def __init__(self, config_path: Path) -> None:
        import numpy as np
        from tokenizers import Tokenizer

        self.np = np
        self.config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        base = Path(config_path).parent
        self.tokenizer = Tokenizer.from_file(str(base / self.config["tokenizer"]))
        from agent.model_cache import onnx_session

        self.session = onnx_session(str(base / self.config["onnx"]))
        self.intents = self.config["intents"]
        self.actions = self.config["actions"]
        self.tags = self.config["tags"]
        self.system_prompt = self.config["system_prompt"]
        self.width = int(self.config["max_seq_len"])
        self.first = int(self.config["first_token"])
        self.threshold = float(self.config.get("threshold", 0.95))
        matrix = np.stack([self._embed(text) for text in self.config["exemplars"]])
        self.matrix = matrix / np.clip(
            np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9, None)

    def _encode(self, command: str) -> Optional[Dict[str, Any]]:
        prefix = self.system_prompt + "\n"
        text = prefix + "Goal: " + command
        enc = self.tokenizer.encode(text, add_special_tokens=False)
        ids = [self.config["bos_id"]] + list(enc.ids) + [self.config["eos_id"]]
        if len(ids) > self.width:
            return None
        offsets = [(0, 0)] + list(enc.offsets) + [(0, 0)]
        user_start = len(prefix + "Goal: ")
        user_first = next((index for index, (_s, end) in enumerate(offsets)
                           if end > user_start), None)
        if user_first != self.first:
            return None
        input_ids = self.np.zeros((1, self.width), dtype=self.np.int64)
        attention = self.np.zeros((1, self.width), dtype=self.np.int64)
        input_ids[0, :len(ids)] = ids
        attention[0, :len(ids)] = 1
        return {"text": text, "ids": ids, "offsets": offsets,
                "user_start": user_start, "input_ids": input_ids,
                "attention": attention, "last": len(ids) - 2}

    def _run(self, encoded: Dict[str, Any]) -> List[Any]:
        return self.session.run(None, {
            "input_ids": encoded["input_ids"],
            "attention_mask": encoded["attention"],
            "last_index": self.np.asarray([encoded["last"]], dtype=self.np.int64),
        })

    def _embed(self, command: str):
        encoded = self._encode(command)
        if encoded is None:
            return self.np.zeros(512, dtype=self.np.float32)
        pooled = self._run(encoded)[5][0]
        return pooled

    def predict(self, command: str) -> Dict[str, Any]:
        encoded = self._encode(command)
        if encoded is None:
            return {"intent": "unknown", "slots": {}, "confidence": 0.0}
        outputs = self._run(encoded)
        pooled = outputs[5][0]
        similarity = float((self.matrix @ (pooled / max(
            float(self.np.linalg.norm(pooled)), 1e-9))).max())
        if similarity >= self.threshold:
            intent_logits, action_logits = outputs[2][0], outputs[3][0]
        else:
            intent_logits, action_logits = outputs[0][0], outputs[1][0]
        intent_index = int(intent_logits.argmax())
        action = self.actions[int(action_logits.argmax())]
        intent = self.intents[intent_index]
        probabilities = self.np.exp(intent_logits - intent_logits.max())
        confidence = float((probabilities / probabilities.sum()).max())
        tag_ids = [int(value) for value in outputs[4][0].argmax(-1)]
        slots = decode_slots(encoded["text"], encoded["offsets"], tag_ids,
                             self.tags, action, encoded["user_start"])
        if intent == "unknown":
            slots = {}
        return {"intent": intent, "slots": slots,
                "confidence": round(confidence, 4)}
