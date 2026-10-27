"""Command understanding on top of the frozen domain encoder.

The 0.25M word-level BiGRU in ``ai/train_command_model.py`` overfits the 487-row
command corpus: 100% on train, 30-45% on held-out rows, below the rule parser.
Its failure is lexical -- an unseen command word maps to ``<unk>`` -- so the next
step is the domain encoder (``models/encoder30_v15``), whose BPE tokenizer and
24.9M-parameter trunk were trained on the agent's own tool language.  The trunk
stays frozen; only a small residual adapter and three heads train:

- intent (13 classes) from the pooled last-content vector,
- action conditioned on the intent distribution (coarse-to-fine, as before),
- BIO slot tags over the user's own tokens.  Slots are copied as character
  spans, never generated, so an unseen item name can still come back verbatim.

Everything outside the user command (the tool contract prefix, BOS/EOS) is
``-100`` in the tag loss, and evaluation compares against ``rule_parse`` on the
same rows.  The rule stays the live parser until this model beats it on held-out
phrasings *and* survives a shadow session.

    python ai/train_command_encoder.py --data data/commands
    python ai/train_command_encoder.py --predict "vends 5 bouftou wool"
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import torch
from torch import nn
from torch.nn import functional as F

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.commands import INTENTS, rule_parse  # noqa: E402
from ai.encoder_policy import EncoderConfig, _char_range_to_tokens  # noqa: E402
from ai.transformer import build_trunk  # noqa: E402
from ai.tool_policy import BOS, EOS, PAD, _special_id  # noqa: E402
from tokenizers import Tokenizer  # noqa: E402
from ai.train_command_model import (  # noqa: E402
    ALL_CUES,
    CHANNEL_ALIASES,
    DUNGEON_CUES,
    SPAN_SLOTS,
    TAG_ID,
    TAG_NAMES,
    _find_cue,
    _find_span,
    macro_f1,
    normalize_slots,
)

BASE = ROOT / "models" / "encoder30_v15" / "model.pt"
DEFAULT_OUT = ROOT / "models" / "command_encoder30"


def load_base(path: Path):
    """Load the domain encoder's trunk and tokenizer, ignoring head drift.

    The checkpoint predates argument fields and tools that were added to the
    schema since, so the policy heads no longer line up.  This experiment only
    needs ``tok_emb/pos_emb/encoder/norm``, which are loaded strictly.
    """
    blob = torch.load(path, map_location="cpu", weights_only=False)
    config = EncoderConfig(**blob["config"])
    trunk = Trunk(config)
    state = {key: value for key, value in blob["state_dict"].items()
             if key.startswith(("tok_emb", "pos_emb", "encoder", "norm"))}
    trunk.load_state_dict(state, strict=True)
    trunk.eval()
    tokenizer = Tokenizer.from_str(blob["tokenizer_json"])
    return trunk, tokenizer, blob.get("meta") or {}


class Trunk(nn.Module):
    def __init__(self, config: EncoderConfig) -> None:
        super().__init__()
        self.tok_emb, self.pos_emb, self.encoder, self.norm = build_trunk(config)


class CommandHeads(nn.Module):
    """The only trainable part: a pooled adapter plus three heads.

    The adapter deliberately runs on the pooled vector, not on every token: a
    per-token 512->256->512 layer costs ~2 GFLOP per batch on CPU and made an
    epoch slower than the trunk pass it was trying to avoid. The frozen trunk's
    contextual states already carry the token-level signal the tag head needs.
    """

    def __init__(self, dim: int, intents: int, actions: int, tags: int,
                 adapter: int = 256, dropout: float = 0.2) -> None:
        super().__init__()
        self.token_adapter = nn.Sequential(
            nn.Linear(dim, adapter),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(adapter, dim),
        )
        self.pool_norm = nn.LayerNorm(dim)
        self.dropout = nn.Dropout(dropout)
        self.intent_head = nn.Linear(dim, intents)
        self.action_head = nn.Linear(dim + intents, actions)
        self.tag_head = nn.Linear(dim, tags)

    def forward(self, hidden: torch.Tensor, lengths: torch.Tensor,
                user_first: torch.Tensor) -> Dict[str, torch.Tensor]:
        # Adapt only the user command's tokens. Running the same adapter over the
        # 219-token tool-contract prefix cost ~13x more for positions whose tag
        # target is -100 anyway.
        adapted = torch.zeros_like(hidden)
        for row in range(hidden.shape[0]):
            first = int(user_first[row])
            length = int(lengths[row])
            adapted[row, first:length] = self.token_adapter(hidden[row, first:length])
        last_content = (lengths - 2).clamp_min(0)
        pooled = adapted[torch.arange(hidden.shape[0], device=hidden.device), last_content]
        pooled = self.pool_norm(pooled)
        pooled = self.dropout(pooled)
        intent_logits = self.intent_head(pooled)
        intent_probs = F.softmax(intent_logits, dim=-1)
        action_logits = self.action_head(torch.cat([pooled, intent_probs], dim=-1))
        tag_logits = self.tag_head(self.dropout(adapted))
        return {"intent": intent_logits, "action": action_logits, "tags": tag_logits,
                "pooled": pooled}


def _slot_spans(text: str, slots: Dict[str, Any]) -> List[Tuple[str, int, int]]:
    """Character spans of the stated slots inside ``text`` (the command only)."""
    used: set = set()
    out: List[Tuple[str, int, int]] = []
    for slot in SPAN_SLOTS:
        if slot not in slots:
            continue
        value = slots[slot]
        if slot == "all":
            span = _find_cue(text, ALL_CUES, used)
        elif slot == "dungeon_only":
            span = _find_cue(text, DUNGEON_CUES, used)
        elif slot == "channel":
            cues = tuple(alias for alias, canonical in CHANNEL_ALIASES.items()
                         if canonical == value)
            span = _find_cue(text, cues or (str(value),), used)
        else:
            span = _find_span(text, value, used)
        if span is not None:
            out.append((slot, span[0], span[1]))
    return out


def _encode_row(row: Dict[str, Any], tokenizer: Any, system_prompt: str,
                max_seq_len: int, ids: Dict[str, int]) -> Optional[Dict[str, Any]]:
    command = str(row.get("text") or "").strip()
    if not command:
        return None
    prefix = system_prompt + "\n"
    text = prefix + "Goal: " + command
    encoding = tokenizer.encode(text, add_special_tokens=False)
    input_ids = [ids["bos"]] + list(encoding.ids) + [ids["eos"]]
    if len(input_ids) > max_seq_len:
        return None
    offsets = [(0, 0)] + list(encoding.offsets) + [(0, 0)]
    user_start = len(prefix + "Goal: ")
    user_end = len(text)
    tags = [-100] * len(input_ids)
    user_first = None
    for index, (start, end) in enumerate(offsets):
        if end > user_start and start < user_end:
            tags[index] = TAG_ID["O"]
            if user_first is None:
                user_first = index
    for slot, start, end in _slot_spans(command, row.get("slots") or {}):
        token_range = _char_range_to_tokens(
            offsets, start + user_start, end + user_start)
        if token_range is None:
            continue
        first, last = token_range
        for index in range(first, last + 1):
            if tags[index] == -100:
                continue
            tags[index] = TAG_ID[("B-" if index == first else "I-") + slot]
    if user_first is None:
        return None
    return {"text": text, "input_ids": input_ids, "tags": tags,
            "offsets": offsets, "user_start": user_start,
            "user_first": user_first,
            "intent": row["intent"], "action": row.get("slots", {}).get("action"),
            "row": row, "command": command}


class CommandData:
    def __init__(self, rows: List[Dict[str, Any]], tokenizer: Any, system_prompt: str,
                 max_seq_len: int, intents: List[str], actions: List[str]) -> None:
        self.intents = intents
        self.actions = actions
        self.intent_index = {name: i for i, name in enumerate(intents)}
        self.action_index = {name: i for i, name in enumerate(actions)}
        self.pad_id = _special_id(tokenizer, PAD)
        ids = {"bos": _special_id(tokenizer, BOS), "eos": _special_id(tokenizer, EOS)}
        self.items: List[Dict[str, Any]] = []
        self.skipped = 0
        for row in rows:
            item = _encode_row(row, tokenizer, system_prompt, max_seq_len, ids)
            if item is None or item["intent"] not in self.intent_index:
                self.skipped += 1
                continue
            action = str(item["action"] or "none").lower()
            item["intent_id"] = self.intent_index[item["intent"]]
            item["action_id"] = self.action_index.get(action, self.action_index["none"])
            self.items.append(item)

    def batches(self, indices: List[int], batch_size: int,
                shuffle: bool = False, rng: Optional[random.Random] = None):
        order = list(indices)
        if shuffle:
            (rng or random).shuffle(order)
        for offset in range(0, len(order), batch_size):
            chunk = [self.items[index] for index in order[offset:offset + batch_size]]
            width = max(len(item["input_ids"]) for item in chunk)
            input_ids = torch.full((len(chunk), width), self.pad_id, dtype=torch.long)
            attention = torch.zeros((len(chunk), width), dtype=torch.long)
            tags = torch.full((len(chunk), width), -100, dtype=torch.long)
            lengths = torch.zeros(len(chunk), dtype=torch.long)
            for row_index, item in enumerate(chunk):
                length = len(item["input_ids"])
                input_ids[row_index, :length] = torch.tensor(item["input_ids"])
                attention[row_index, :length] = 1
                tags[row_index, :length] = torch.tensor(item["tags"])
                lengths[row_index] = length
            yield {
                "input_ids": input_ids, "attention_mask": attention,
                "tags": tags, "lengths": lengths,
                "intent": torch.tensor([item["intent_id"] for item in chunk]),
                "action": torch.tensor([item["action_id"] for item in chunk]),
            }

    def hidden_batches(self, indices: List[int], batch_size: int,
                       shuffle: bool = False,
                       rng: Optional[random.Random] = None):
        """Batches of the frozen trunk's cached states. The trunk never changes,
        so the whole corpus is encoded once instead of once per epoch."""
        order = list(indices)
        if shuffle:
            (rng or random).shuffle(order)
        for offset in range(0, len(order), batch_size):
            members = order[offset:offset + batch_size]
            chunk = [self.items[index] for index in members]
            states = [self.hidden[index] for index in members]
            width = max(state.shape[0] for state in states)
            dim = states[0].shape[1]
            hidden = torch.zeros((len(chunk), width, dim), dtype=torch.float32)
            tags = torch.full((len(chunk), width), -100, dtype=torch.long)
            lengths = torch.zeros(len(chunk), dtype=torch.long)
            for row_index, (item, state) in enumerate(zip(chunk, states)):
                length = state.shape[0]
                hidden[row_index, :length] = state
                tags[row_index, :length] = torch.tensor(item["tags"])
                lengths[row_index] = length
            yield {
                "hidden": hidden, "tags": tags, "lengths": lengths,
                "user_first": torch.tensor([item["user_first"] for item in chunk]),
                "intent": torch.tensor([item["intent_id"] for item in chunk]),
                "action": torch.tensor([item["action_id"] for item in chunk]),
            }


def cache_hidden(base: nn.Module, data: CommandData, batch_size: int = 64) -> None:
    data.hidden = [None] * len(data.items)
    start = 0
    base.eval()
    with torch.no_grad():
        for batch in data.batches(list(range(len(data.items))), batch_size):
            hidden = _trunk_forward(base, batch["input_ids"], batch["attention_mask"])
            for row_index in range(hidden.shape[0]):
                length = int(batch["lengths"][row_index])
                data.hidden[start + row_index] = hidden[row_index, :length].clone()
            start += hidden.shape[0]
    if start != len(data.items):
        raise RuntimeError(f"cached {start} of {len(data.items)} hidden states")


def _trunk_forward(base: nn.Module, input_ids: torch.Tensor,
                   attention: torch.Tensor) -> torch.Tensor:
    positions = torch.arange(input_ids.shape[1], device=input_ids.device)
    hidden = base.tok_emb(input_ids) + base.pos_emb(positions)
    hidden = base.encoder(hidden, src_key_padding_mask=attention.eq(0))
    return base.norm(hidden)


def _decode_slots(text: str, offsets: List[Tuple[int, int]], tags: List[int],
                  action: str, user_start: int) -> Dict[str, Any]:
    slots: Dict[str, Any] = {}
    limit = min(len(tags), len(offsets))
    index = 0
    while index < limit:
        name = TAG_NAMES[tags[index]] if 0 <= tags[index] < len(TAG_NAMES) else "O"
        if name.startswith("B-") and offsets[index][1] > user_start:
            slot = name[2:]
            start = max(offsets[index][0], user_start)
            end = offsets[index][1]
            cursor = index
            while (cursor + 1 < limit
                   and TAG_NAMES[tags[cursor + 1]] == "I-" + slot
                   and offsets[cursor + 1][1] > user_start):
                cursor += 1
                end = offsets[cursor][1]
            raw = text[start:end].strip()
            if (slot in ("level_min", "price", "quantity", "quest_id", "x", "y")
                    and start > user_start and text[start - 1] in ("-", "−")):
                raw = text[start - 1] + raw
            if slot in ("all", "dungeon_only"):
                slots[slot] = True
            elif slot in ("level_min", "price", "quantity", "quest_id", "x", "y"):
                digits = re.sub(r"[^\d-]", "", raw.replace("−", "-"))
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


def evaluate(heads: CommandHeads, data: CommandData,
             indices: Optional[List[int]] = None, batch_size: int = 32) -> Dict[str, Any]:
    indices = list(range(len(data.items))) if indices is None else indices
    intent_hits = action_hits = slot_hits = full_hits = 0
    rule_intent = rule_slot = rule_full = 0
    base = {"rows": 0, "intent": 0, "slot": 0, "full": 0,
            "rule_intent": 0, "rule_slot": 0, "rule_full": 0}
    pred_intents: List[int] = []
    gold_intents: List[int] = []
    rows: List[Dict[str, Any]] = []
    heads.eval()
    with torch.no_grad():
        for batch in data.hidden_batches(indices, batch_size):
            output = heads(batch["hidden"], batch["lengths"], batch["user_first"])
            pred_intent = output["intent"].argmax(-1).tolist()
            pred_action = output["action"].argmax(-1).tolist()
            pred_tags = output["tags"].argmax(-1).tolist()
            chunk = [data.items[i] for i in
                     indices[len(rows):len(rows) + len(pred_intent)]]
            for position, item in enumerate(chunk):
                intent = data.intents[pred_intent[position]]
                action = data.actions[pred_action[position]]
                offsets = item["offsets"]
                user_start = item["user_start"]
                pred_slots = _decode_slots(item["text"], offsets, pred_tags[position],
                                           action, user_start)
                if intent == "unknown":
                    # The schema forbids slots on "unknown": it is not a command.
                    pred_slots = {}
                gold_slots = normalize_slots(item["row"].get("slots") or {})
                pred_norm = normalize_slots(pred_slots)
                intent_ok = intent == item["intent"]
                slot_ok = pred_norm == gold_slots
                intent_hits += intent_ok
                action_hits += action == str(item["action"] or "none").lower()
                slot_hits += slot_ok
                full_hits += intent_ok and slot_ok
                is_base = "~" not in str(item["row"].get("origin") or "~")
                pred_intents.append(pred_intent[position])
                gold_intents.append(item["intent_id"])
                try:
                    parsed = rule_parse(item["command"])
                except Exception:  # noqa: BLE001 - the comparison is optional
                    parsed = None
                if parsed is not None:
                    rule_slots = normalize_slots(parsed.get("slots") or {})
                    rule_intent_ok = parsed.get("intent") == item["intent"]
                    rule_slot_ok = rule_slots == gold_slots
                    rule_intent += rule_intent_ok
                    rule_slot += rule_slot_ok
                    rule_full += rule_intent_ok and rule_slot_ok
                    if is_base:
                        base["rows"] += 1
                        base["intent"] += intent_ok
                        base["slot"] += slot_ok
                        base["full"] += intent_ok and slot_ok
                        base["rule_intent"] += rule_intent_ok
                        base["rule_slot"] += rule_slot_ok
                        base["rule_full"] += rule_intent_ok and rule_slot_ok
                rows.append({"text": item["command"], "intent": intent,
                             "slots": pred_norm, "gold_intent": item["intent"],
                             "gold_slots": gold_slots})
    total = max(1, len(indices))
    base_total = max(1, base["rows"])
    base_metrics = {
        "rows": base["rows"],
        "intent_accuracy": round(base["intent"] / base_total, 4),
        "slot_exact": round(base["slot"] / base_total, 4),
        "command_exact": round(base["full"] / base_total, 4),
        "rule": {
            "intent_accuracy": round(base["rule_intent"] / base_total, 4),
            "slot_exact": round(base["rule_slot"] / base_total, 4),
            "command_exact": round(base["rule_full"] / base_total, 4),
        },
    }
    return {
        "rows": len(indices),
        "intent_accuracy": round(intent_hits / total, 4),
        "intent_macro_f1": round(macro_f1(pred_intents, gold_intents,
                                          len(data.intents)), 4),
        "action_accuracy": round(action_hits / total, 4),
        "slot_exact": round(slot_hits / total, 4),
        "command_exact": round(full_hits / total, 4),
        "rule": {
            "intent_accuracy": round(rule_intent / total, 4),
            "slot_exact": round(rule_slot / total, 4),
            "command_exact": round(rule_full / total, 4),
        },
        "base_rows": base_metrics,
        "rows_detail": rows,
    }


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _set_enabled(module: nn.Module, enabled: bool) -> None:
    for parameter in module.parameters():
        parameter.requires_grad_(enabled)


def train(args: argparse.Namespace) -> Dict[str, Any]:
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    splits = {name: _read_rows(Path(args.data) / (name + ".jsonl"))
              for name in ("train", "val", "test")}
    if args.limit:
        splits["train"] = splits["train"][:args.limit]
    base, tokenizer, meta = load_base(Path(args.base))
    system_prompt = meta.get("system_prompt") or ""
    if not system_prompt:
        raise SystemExit(f"{args.base} carries no system prompt")
    base.eval()
    _set_enabled(base, False)
    intents = list(INTENTS)
    actions = ["none"] + sorted({str((row.get("slots") or {}).get("action") or "none").lower()
                                 for row in splits["train"]} - {"none"})
    data = {name: CommandData(rows, tokenizer, system_prompt, args.max_seq_len,
                              intents, actions)
            for name, rows in splits.items()}
    if not data["train"].items:
        raise SystemExit("no trainable command rows")
    for name, split in data.items():
        cache_hidden(base, split)
        print(f"cached {name}: {len(split.items)} rows")
    heads = CommandHeads(base.pos_emb.weight.shape[1], len(intents), len(actions),
                         len(TAG_NAMES), adapter=args.adapter, dropout=args.dropout)
    optimizer = torch.optim.AdamW(
        [parameter for parameter in heads.parameters() if parameter.requires_grad],
        lr=args.lr, weight_decay=args.weight_decay)
    train_indices = list(range(len(data["train"].items)))
    val_indices = list(range(len(data["val"].items)))
    best = float("-inf")
    best_state = None
    history: List[Dict[str, Any]] = []
    for epoch in range(1, args.epochs + 1):
        heads.train()
        total_loss = 0.0
        for batch in data["train"].hidden_batches(
                train_indices, args.batch, shuffle=True,
                rng=random.Random(args.seed + epoch)):
            output = heads(batch["hidden"], batch["lengths"], batch["user_first"])
            loss = (F.cross_entropy(output["intent"], batch["intent"],
                                    label_smoothing=0.05)
                    + 0.5 * F.cross_entropy(output["action"], batch["action"],
                                            label_smoothing=0.05)
                    + F.cross_entropy(output["tags"].reshape(-1, len(TAG_NAMES)),
                                      batch["tags"].reshape(-1), ignore_index=-100))
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(heads.parameters(), 1.0)
            optimizer.step()
            total_loss += float(loss.item())
        metrics = evaluate(heads, data["val"], val_indices)
        score = (metrics["intent_macro_f1"] + metrics["slot_exact"]
                 + metrics["command_exact"])
        history.append({"epoch": epoch, "loss": round(total_loss, 4),
                        "intent_accuracy": metrics["intent_accuracy"],
                        "slot_exact": metrics["slot_exact"],
                        "command_exact": metrics["command_exact"]})
        if score > best:
            best = score
            best_state = {key: value.detach().clone()
                          for key, value in heads.state_dict().items()}
    if best_state is not None:
        heads.load_state_dict(best_state)
    result = {
        "base": str(args.base),
        "params_heads": sum(parameter.numel() for parameter in heads.parameters()),
        "params_frozen": sum(parameter.numel() for parameter in base.parameters()),
        "intents": intents,
        "actions": actions,
        "train_rows": len(data["train"].items),
        "val_rows": len(data["val"].items),
        "test_rows": len(data["test"].items),
        "skipped": {name: split.skipped for name, split in data.items()},
        "val": evaluate(heads, data["val"], val_indices),
        "test": evaluate(heads, data["test"]),
        "best_val_score": round(best, 4),
        "history_tail": history[-8:],
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    torch.save({"state": heads.state_dict(), "intents": intents,
                "actions": actions, "tags": TAG_NAMES, "adapter": args.adapter,
                "dropout": args.dropout, "base": str(args.base),
                "system_prompt": system_prompt}, out / "heads.pt")
    (out / "metrics.json").write_text(json.dumps(result, indent=2) + "\n",
                                      encoding="utf-8")
    return result


class CommandEncoderModel:
    """Inference wrapper matching ``parse_command(model=...)``."""

    def __init__(self, path: Path) -> None:
        payload = torch.load(path, map_location="cpu", weights_only=False)
        self.base, self.tokenizer, self.meta = load_base(Path(payload["base"]))
        self.base.eval()
        self.intents = payload["intents"]
        self.actions = payload["actions"]
        self.tags = payload["tags"]
        self.system_prompt = payload["system_prompt"]
        self.max_seq_len = int(self.base.pos_emb.weight.shape[0])
        self.heads = CommandHeads(self.base.pos_emb.weight.shape[1], len(self.intents),
                                  len(self.actions), len(self.tags),
                                  adapter=payload["adapter"], dropout=payload["dropout"])
        self.heads.load_state_dict(payload["state"])
        self.heads.eval()

    def embed(self, text: str) -> Any:
        """The pooled command vector, used by the per-instance router."""
        ids = {"bos": _special_id(self.tokenizer, BOS),
               "eos": _special_id(self.tokenizer, EOS)}
        item = _encode_row({"text": text, "intent": self.intents[0], "slots": {}},
                           self.tokenizer, self.system_prompt, self.max_seq_len, ids)
        if item is None:
            return None
        input_ids = torch.tensor([item["input_ids"]])
        attention = torch.ones_like(input_ids)
        with torch.no_grad():
            hidden = _trunk_forward(self.base, input_ids, attention)
            output = self.heads(hidden, torch.tensor([len(item["input_ids"])]),
                                torch.tensor([item["user_first"]]))
        return output["pooled"][0]

    def predict(self, text: str) -> Dict[str, Any]:
        ids = {"bos": _special_id(self.tokenizer, BOS),
               "eos": _special_id(self.tokenizer, EOS)}
        item = _encode_row({"text": text, "intent": self.intents[0], "slots": {}},
                           self.tokenizer, self.system_prompt, self.max_seq_len, ids)
        if item is None:
            return {"intent": "unknown", "slots": {}, "confidence": 0.0}
        full = item["text"]
        encoding = self.tokenizer.encode(full, add_special_tokens=False)
        offsets = [(0, 0)] + list(encoding.offsets) + [(0, 0)]
        user_start = len(self.system_prompt + "\n" + "Goal: ")
        input_ids = torch.tensor([item["input_ids"]])
        attention = torch.ones_like(input_ids)
        with torch.no_grad():
            hidden = _trunk_forward(self.base, input_ids, attention)
            output = self.heads(hidden, torch.tensor([len(item["input_ids"])]),
                                torch.tensor([item["user_first"]]))
        intent = self.intents[int(output["intent"].argmax(-1))]
        action = self.actions[int(output["action"].argmax(-1))]
        slots = _decode_slots(full, offsets, output["tags"].argmax(-1)[0].tolist(),
                              action, user_start)
        if intent == "unknown":
            slots = {}
        confidence = float(F.softmax(output["intent"], -1).max())
        return {"intent": intent, "slots": slots,
                "confidence": round(confidence, 4)}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(ROOT / "data" / "commands"))
    parser.add_argument("--base", default=str(BASE))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=0.02)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--adapter", type=int, default=256)
    parser.add_argument("--max-seq-len", type=int, default=256)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--limit", type=int, default=0,
                        help="truncate train rows (smoke runs)")
    parser.add_argument("--predict", default=None)
    args = parser.parse_args(argv)
    if args.predict is not None:
        model = CommandEncoderModel(Path(args.out) / "heads.pt")
        print(json.dumps(model.predict(args.predict), indent=2, ensure_ascii=False))
        return 0
    result = train(args)
    print(json.dumps({key: result[key] for key in
                      ("params_heads", "train_rows", "val", "test", "best_val_score")
                      if key not in ("val", "test")}, ensure_ascii=False))
    for split in ("val", "test"):
        print(split, json.dumps({key: value for key, value in result[split].items()
                                 if key != "rows_detail"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
