"""Multi-task encoder policy: one forward pass to tool + argument heads.

The model is deliberately not autoregressive.  A bidirectional encoder reads the
compact tool contract, the player goal and optional state once.  Heads then
predict the tool, which arguments exist, enum values, booleans, and token spans
for names/numbers.  This is the cheap CPU runtime that replaces the 100M
decoder.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch
from torch import nn

from ai.transformer import build_trunk, init_weights
from torch.nn import functional as F

from agent import tool_dataset, tool_eval
from agent.schema import TOOLS
from ai.tool_policy import BOS, EOS, PAD, _resolve_device, _special_id

TOOL_NAMES: List[str] = [spec.name for spec in TOOLS]
TOOL_INDEX: Dict[str, int] = {name: index for index, name in enumerate(TOOL_NAMES)}
SPOKEN_LANGUAGES: List[str] = ["auto", "en", "fr", "es", "de"]


def _encoder_signature(tool: Any) -> str:
    parts: List[str] = []
    for name, param in tool.params.items():
        label = name + ("*" if param.get("required") else "")
        parts.append(label)
    return ", ".join(parts)


ENCODER_SYSTEM_PROMPT = "\n".join(
    ["Tools:"]
    + [f"- {tool.name}({_encoder_signature(tool)})" for tool in TOOLS]
)
SYSTEM_PROMPTS = {
    "encoder": ENCODER_SYSTEM_PROMPT,
    "compact": tool_dataset.SYSTEM_PROMPT_COMPACT,
    "full": tool_dataset.SYSTEM_PROMPT,
}


@dataclass(frozen=True)
class FieldSpec:
    tool: str
    name: str
    type: str
    enum: Tuple[str, ...]
    default: Any
    required: bool


FIELDS: List[FieldSpec] = []
for _tool in TOOLS:
    for _name, _param in _tool.params.items():
        FIELDS.append(FieldSpec(
            tool=_tool.name,
            name=_name,
            type=str(_param["type"]),
            enum=tuple(_param.get("enum") or ()),
            default=_param.get("default"),
            required=bool(_param.get("required")),
        ))
FIELD_INDEX: Dict[Tuple[str, str], int] = {
    (field.tool, field.name): index for index, field in enumerate(FIELDS)}
FIELDS_BY_TOOL: Dict[str, List[int]] = {
    name: [FIELD_INDEX[(name, arg_name)] for arg_name in TOOLS[TOOL_INDEX[name]].params]
    for name in TOOL_NAMES
}
SPAN_FIELDS: List[int] = [
    index for index, field in enumerate(FIELDS)
    if field.type in ("str", "int", "float", "str_list")]
BOOL_FIELDS: List[int] = [
    index for index, field in enumerate(FIELDS) if field.type == "bool"]
ENUM_FIELDS: List[int] = [
    index for index, field in enumerate(FIELDS) if field.enum]
ENUM_HEAD_INDEX: Dict[int, int] = {
    field_index: head_index for head_index, field_index in enumerate(ENUM_FIELDS)}


@dataclass
class EncoderConfig:
    vocab_size: int = 4096
    max_seq_len: int = 256
    d_model: int = 512
    n_layers: int = 7
    n_heads: int = 8
    ffn: int = 2048
    dropout: float = 0.1
    # Anchor mode: the action head is not a learned classifier over a fixed class
    # list. Its rows are embeddings of the capability *descriptions*, refreshed from
    # the encoder itself. Adding an action = encoding one more description.
    use_anchors: bool = False
    anchor_scale: float = 10.0


class EncoderPolicy(nn.Module):
    def __init__(self, config: EncoderConfig) -> None:
        super().__init__()
        self.config = config
        self.tok_emb, self.pos_emb, self.encoder, self.norm = build_trunk(config)
        self.field_emb = nn.Embedding(len(FIELDS), config.d_model)
        self.presence_bias = nn.Parameter(torch.zeros(len(FIELDS)))
        self.bool_bias = nn.Parameter(torch.zeros(len(FIELDS)))
        self.start_proj = nn.Linear(config.d_model, config.d_model)
        self.end_proj = nn.Linear(config.d_model, config.d_model)
        self.tool_head = nn.Linear(config.d_model, len(TOOL_NAMES))
        self.lex_tool = nn.Linear(config.d_model, len(TOOL_NAMES))
        self.enum_heads = nn.ModuleList([
            nn.Linear(config.d_model, len(FIELDS[field_index].enum))
            for field_index in ENUM_FIELDS
        ])
        self.dropout = nn.Dropout(config.dropout)
        self.apply(init_weights)

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> Dict[str, Any]:
        _batch, length = input_ids.shape
        if length > self.config.max_seq_len:
            raise ValueError(
                f"sequence of {length} exceeds max_seq_len {self.config.max_seq_len}")
        positions = torch.arange(length, device=input_ids.device)
        hidden = self.tok_emb(input_ids) + self.pos_emb(positions)
        padding = attention_mask.eq(0) if attention_mask is not None else None
        hidden = self.encoder(hidden, src_key_padding_mask=padding)
        hidden = self.norm(hidden)
        if attention_mask is not None:
            lengths = attention_mask.sum(dim=1).clamp_min(2)
            last_content = (lengths - 2).clamp_min(0)
            pooled = hidden[
                torch.arange(_batch, device=input_ids.device), last_content]
        else:
            pooled = hidden[:, -1]
        pooled = self.dropout(pooled)
        if self.config.use_anchors:
            # Cosine similarity against capability anchors. The anchor matrix lives
            # in ``tool_head.weight`` so loss, decoding and ONNX export are unchanged.
            weights = F.normalize(self.tool_head.weight, dim=-1)
            tool_logits = F.normalize(pooled, dim=-1) @ weights.t() * self.config.anchor_scale
            lexical_tool = torch.zeros_like(tool_logits)
        else:
            lexical = self.lex_tool(self.tok_emb(input_ids))
            if attention_mask is not None:
                lexical_weight = attention_mask.unsqueeze(-1).to(lexical.dtype)
            else:
                lexical_weight = torch.ones_like(lexical[..., :1])
            lexical_tool = (lexical * lexical_weight).sum(dim=1) / (
                lexical_weight.sum(dim=1).clamp_min(1.0))
            tool_logits = self.tool_head(pooled)
        field_weights = self.field_emb.weight
        start = torch.matmul(self.start_proj(hidden), field_weights.t()).transpose(1, 2)
        end = torch.matmul(self.end_proj(hidden), field_weights.t()).transpose(1, 2)
        presence = torch.matmul(pooled, field_weights.t()) + self.presence_bias
        bool_logits = torch.matmul(pooled, field_weights.t()) + self.bool_bias
        return {
            # ``pooled`` is the sentence vector the capability anchors live in.
            # Exposed so anchor refresh (and any future retrieval) can use it; the
            # ONNX export selects its outputs explicitly and is unaffected.
            "pooled": pooled,
            "tool": tool_logits + lexical_tool,
            "presence": presence,
            "bool": bool_logits,
            "span_start": start,
            "span_end": end,
            "enum": [head(pooled) for head in self.enum_heads],
        }


@torch.no_grad()
def refresh_capability_anchors(
    model: EncoderPolicy,
    tokenizer: Any,
    descriptions: Optional[Sequence[str]] = None,
    batch_size: int = 16,
) -> torch.Tensor:
    """Re-derive the action head from capability descriptions.

    The head's rows become embeddings of ``descriptions`` (default: the capability
    registry order). Called periodically during training so the anchors track the
    encoder, and once at load time for inference. Because the head is derived from
    text, a capability added tomorrow needs no new class id and no retraining —
    encode its description and append a row.
    """
    if descriptions is None:
        from agent.schema import capability_descriptions

        descriptions = capability_descriptions()

    was_training = model.training
    model.eval()
    device = next(model.parameters()).device
    bos_id = _special_id(tokenizer, BOS)
    eos_id = _special_id(tokenizer, EOS)
    vectors: List[torch.Tensor] = []
    for start in range(0, len(descriptions), batch_size):
        chunk = list(descriptions[start:start + batch_size])
        encoded = tokenizer.encode_batch(chunk, add_special_tokens=False)
        width = min(model.config.max_seq_len,
                    max(len(enc.ids) + 2 for enc in encoded))
        ids = torch.full((len(chunk), width), 0, dtype=torch.long, device=device)
        mask = torch.zeros((len(chunk), width), dtype=torch.long, device=device)
        for row, enc in enumerate(encoded):
            seq = list(enc.ids)[: width - 2]
            ids[row, 0] = bos_id
            ids[row, 1:1 + len(seq)] = torch.tensor(seq, dtype=torch.long, device=device)
            ids[row, 1 + len(seq)] = eos_id
            mask[row, : 2 + len(seq)] = 1
        output = model(ids, mask)
        vectors.append(output["pooled"].detach().cpu())

    anchors = torch.cat(vectors, dim=0)
    if anchors.shape[0] != model.tool_head.weight.shape[0]:
        raise ValueError(
            f"capability count mismatch: {anchors.shape[0]} descriptions vs "
            f"{model.tool_head.weight.shape[0]} head rows")
    model.tool_head.weight.copy_(anchors.to(model.tool_head.weight.device))
    if model.tool_head.bias is not None:
        model.tool_head.bias.zero_()
    if was_training:
        model.train()
    return anchors


def grow_capabilities(
    model: EncoderPolicy,
    tokenizer: Any,
    new_descriptions: Sequence[str],
) -> int:
    """Append capabilities to a live model without retraining the trunk."""
    with torch.no_grad():
        existing = model.tool_head.weight.detach().clone()
        count_before = existing.shape[0]
        # Temporarily widen the head so the refresh routine can fill it.
        widened = torch.nn.Linear(model.config.d_model, count_before + len(new_descriptions),
                                  bias=True).to(existing.device)
        widened.weight.data[:count_before] = existing
        model.tool_head = widened
        from agent.schema import capability_descriptions

        refresh_capability_anchors(model, tokenizer, capability_descriptions())
    return count_before + len(new_descriptions)


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_tokenizer_source(path: Path) -> Any:
    from tokenizers import Tokenizer

    if path.suffix.lower() == ".json":
        return Tokenizer.from_file(str(path))
    blob = torch.load(path, map_location="cpu", weights_only=False)
    tokenizer_json = blob.get("tokenizer_json")
    if not tokenizer_json:
        raise SystemExit(f"{path} has no embedded tokenizer")
    return Tokenizer.from_str(tokenizer_json)


def _find_offsets(
    text: str,
    value: Any,
    field: FieldSpec,
    user_start: int,
) -> Optional[Tuple[int, int]]:
    if isinstance(value, bool) or isinstance(value, list) or value is None:
        return None
    needle = str(value).strip()
    if not needle:
        return None
    if field.type in ("int", "float"):
        pattern = r"(?<![\w.])" + re.escape(needle) + r"(?![\w.])"
    else:
        pattern = re.escape(needle)
    for match in re.finditer(pattern, text, re.IGNORECASE):
        if match.start() >= user_start:
            return match.start(), match.end()
    return None


def _char_range_to_tokens(
    offsets: Sequence[Tuple[int, int]],
    start: int,
    end: int,
) -> Optional[Tuple[int, int]]:
    indices = [
        index for index, (token_start, token_end) in enumerate(offsets)
        if token_start < end and token_end > start
    ]
    if not indices:
        return None
    return indices[0], indices[-1]


class EncoderDataset:
    def __init__(
        self,
        rows: Sequence[Dict[str, Any]],
        tokenizer: Any,
        max_seq_len: int,
        system_prompt: str,
        infer: bool = False,
    ) -> None:
        self.tokenizer = tokenizer
        self.max_seq_len = max_seq_len
        self.pad_id = _special_id(tokenizer, PAD)
        self.bos_id = _special_id(tokenizer, BOS)
        self.eos_id = _special_id(tokenizer, EOS)
        self.system_prompt = system_prompt
        self.infer = infer
        self.items: List[Dict[str, Any]] = []
        self.skipped = 0
        self.lengths: List[int] = []
        for row in rows:
            item = self._encode_row(row, infer=infer)
            if item is None:
                self.skipped += 1
                continue
            self.items.append(item)
            self.lengths.append(len(item["input_ids"]))

    def _encode_row(
        self, row: Dict[str, Any], infer: bool = False
    ) -> Optional[Dict[str, Any]]:
        goal = str(row.get("goal") or "").strip()
        tool = str(row.get("tool") or "")
        if not goal:
            return None
        if not infer and (not tool or tool not in TOOL_INDEX):
            # Training/evaluation rows must declare a known tool. Inference rows
            # do not — the tool is predicted by the model, so a live row may
            # legitimately omit ``"tool"``.
            return None
        args = row.get("args") or {}
        user = f"Goal: {goal}"
        observation = tool_dataset._observation_text(row.get("observation"))
        if observation:
            user += f"\nCurrent state: {observation}"
        prefix = self.system_prompt + "\n"
        text = prefix + user
        encoding = self.tokenizer.encode(text, add_special_tokens=False)
        ids = [self.bos_id] + list(encoding.ids) + [self.eos_id]
        if len(ids) > self.max_seq_len:
            return None
        offsets = [(0, 0)] + list(encoding.offsets) + [(0, 0)]
        user_start = len(prefix)
        field_count = len(FIELDS)
        span_start = [-100] * field_count
        span_end = [-100] * field_count
        presence_target = [0.0] * field_count
        presence_mask = [0.0] * field_count
        bool_target = [0.0] * field_count
        bool_mask = [0.0] * field_count
        enum_target = [-100] * field_count
        enum_mask = [0.0] * field_count
        field_indices = FIELDS_BY_TOOL[tool] if tool in TOOL_INDEX else ()
        for field_index in field_indices:
            field = FIELDS[field_index]
            value = args.get(field.name)
            if field.type == "bool":
                bool_mask[field_index] = 1.0
                bool_target[field_index] = float(
                    value if value is not None else (field.default or False))
                continue
            if field.enum:
                chosen = value if value is not None else field.default
                if chosen in field.enum:
                    enum_mask[field_index] = 1.0
                    enum_target[field_index] = field.enum.index(chosen)
                continue
            if field_index not in SPAN_FIELDS:
                continue
            presence_mask[field_index] = 1.0
            if value is None:
                presence_target[field_index] = 0.0
                continue
            found = _find_offsets(text, value, field, user_start)
            if not found:
                presence_target[field_index] = 0.0
                continue
            token_range = _char_range_to_tokens(offsets, found[0], found[1])
            if not token_range:
                presence_target[field_index] = 0.0
                continue
            presence_target[field_index] = 1.0
            span_start[field_index] = token_range[0]
            span_end[field_index] = token_range[1]
        return {
            "id": row.get("id"),
            "goal": goal,
            "tool": tool,
            "args": args,
            "lang": row.get("lang") or "auto",
            "input_ids": ids,
            "span_start": span_start,
            "span_end": span_end,
            "presence_target": presence_target,
            "presence_mask": presence_mask,
            "bool_target": bool_target,
            "bool_mask": bool_mask,
            "enum_target": enum_target,
            "enum_mask": enum_mask,
        }

    def __len__(self) -> int:
        return len(self.items)

    def stats(self) -> Dict[str, Any]:
        if not self.lengths:
            return {"examples": 0, "skipped": self.skipped}
        ordered = sorted(self.lengths)
        return {
            "examples": len(ordered),
            "skipped": self.skipped,
            "min": ordered[0],
            "p50": ordered[len(ordered) // 2],
            "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
            "max": ordered[-1],
        }

    def batches(
        self,
        batch_size: int,
        shuffle: bool = False,
        rng: Optional[random.Random] = None,
    ) -> Iterable[Dict[str, torch.Tensor]]:
        indices = list(range(len(self.items)))
        if shuffle:
            (rng or random).shuffle(indices)
        for offset in range(0, len(indices), batch_size):
            chunk = [self.items[index] for index in indices[offset:offset + batch_size]]
            max_len = max(len(item["input_ids"]) for item in chunk)
            input_ids = torch.full(
                (len(chunk), max_len), self.pad_id, dtype=torch.long)
            attention = torch.zeros((len(chunk), max_len), dtype=torch.long)
            for row_index, item in enumerate(chunk):
                length = len(item["input_ids"])
                input_ids[row_index, :length] = torch.tensor(
                    item["input_ids"], dtype=torch.long)
                attention[row_index, :length] = 1
            yield {
                "input_ids": input_ids,
                "attention_mask": attention,
                "span_start": torch.tensor(
                    [item["span_start"] for item in chunk], dtype=torch.long),
                "span_end": torch.tensor(
                    [item["span_end"] for item in chunk], dtype=torch.long),
                "presence_target": torch.tensor(
                    [item["presence_target"] for item in chunk], dtype=torch.float),
                "presence_mask": torch.tensor(
                    [item["presence_mask"] for item in chunk], dtype=torch.float),
                "bool_target": torch.tensor(
                    [item["bool_target"] for item in chunk], dtype=torch.float),
                "bool_mask": torch.tensor(
                    [item["bool_mask"] for item in chunk], dtype=torch.float),
                "enum_target": torch.tensor(
                    [item["enum_target"] for item in chunk], dtype=torch.long),
                "enum_mask": torch.tensor(
                    [item["enum_mask"] for item in chunk], dtype=torch.float),
                "lengths": torch.tensor(
                    [len(item["input_ids"]) for item in chunk], dtype=torch.long),
                # ``_encode_row(infer=True)`` deliberately lets a live row omit
                # ``"tool"`` -- the tool is what the model predicts. Indexing it
                # here raised ``KeyError: ''`` on every serving call, which the
                # self-test's stubbed ``_predict`` could not see. The sentinel is
                # never a training target: ``compute_loss`` only ever receives
                # rows that declare a tool, and ``decode_batch`` reads the tool
                # from the model's own output.
                "tool": torch.tensor(
                    [TOOL_INDEX.get(item["tool"], -1) for item in chunk],
                    dtype=torch.long),
            }


def _masked_binary_loss(logits: torch.Tensor, target: torch.Tensor,
                        mask: torch.Tensor) -> torch.Tensor:
    loss = F.binary_cross_entropy_with_logits(logits, target, reduction="none")
    return (loss * mask).sum() / mask.sum().clamp_min(1.0)


def compute_loss(
    output: Dict[str, Any],
    batch: Dict[str, torch.Tensor],
    tool_weights: Optional[torch.Tensor] = None,
) -> Tuple[torch.Tensor, Dict[str, float]]:
    tool_loss = F.cross_entropy(
        output["tool"], batch["tool"], weight=tool_weights,
        label_smoothing=0.05)
    span_mask = batch["span_start"].ne(-100)
    if span_mask.any():
        start_loss = F.cross_entropy(
            output["span_start"][span_mask], batch["span_start"][span_mask])
        end_loss = F.cross_entropy(
            output["span_end"][span_mask], batch["span_end"][span_mask])
    else:
        start_loss = output["span_start"].sum() * 0.0
        end_loss = output["span_end"].sum() * 0.0
    presence_loss = _masked_binary_loss(
        output["presence"], batch["presence_target"], batch["presence_mask"])
    bool_loss = _masked_binary_loss(
        output["bool"], batch["bool_target"], batch["bool_mask"])
    enum_losses: List[torch.Tensor] = []
    for head_index, field_index in enumerate(ENUM_FIELDS):
        mask = batch["enum_mask"][:, field_index].bool()
        if mask.any():
            enum_losses.append(F.cross_entropy(
                output["enum"][head_index][mask],
                batch["enum_target"][mask, field_index]))
    enum_loss = torch.stack(enum_losses).mean() if enum_losses else (
        output["tool"].sum() * 0.0)
    total = (
        tool_loss
        + 0.5 * (start_loss + end_loss)
        + 0.2 * presence_loss
        + 0.2 * bool_loss
        + 0.2 * enum_loss
    )
    return total, {
        "loss": float(total.detach()),
        "tool_loss": float(tool_loss.detach()),
        "span_loss": float((start_loss + end_loss).detach() / 2),
        "presence_loss": float(presence_loss.detach()),
        "bool_loss": float(bool_loss.detach()),
        "enum_loss": float(enum_loss.detach()),
    }


@torch.no_grad()
def _best_span(
    start_logits: torch.Tensor,
    end_logits: torch.Tensor,
    length: int,
    max_span: int = 32,
) -> Optional[Tuple[int, int]]:
    start_logits = start_logits[:length].clone()
    end_logits = end_logits[:length].clone()
    start_logits[0] = -1e4
    end_logits[0] = -1e4
    start_values, start_indices = torch.topk(
        start_logits, k=min(5, length))
    end_values, end_indices = torch.topk(
        end_logits, k=min(5, length))
    best: Optional[Tuple[float, int, int]] = None
    for start_value, start_index in zip(start_values.tolist(), start_indices.tolist()):
        for end_value, end_index in zip(end_values.tolist(), end_indices.tolist()):
            if end_index < start_index or end_index - start_index >= max_span:
                continue
            score = start_value + end_value
            if best is None or score > best[0]:
                best = (score, start_index, end_index)
    return None if best is None else (best[1], best[2])


def _parse_span(
    text: str,
    field: FieldSpec,
) -> Optional[Any]:
    text = text.strip()
    if not text:
        return None
    if field.type == "int":
        match = re.search(r"-?\d+", text)
        return int(match.group(0)) if match else None
    if field.type == "float":
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else None
    if field.type == "str_list":
        return [part.strip() for part in re.split(r"[,;]", text) if part.strip()]
    return text


@torch.no_grad()
def decode_batch(
    model: EncoderPolicy,
    tokenizer: Any,
    batch: Dict[str, torch.Tensor],
    threshold: float = 0.5,
    output: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Decode one batch. Pass ``output`` to reuse a forward pass already computed."""
    device = next(model.parameters()).device
    if output is None:
        input_ids = batch["input_ids"].to(device)
        attention_mask = batch["attention_mask"].to(device)
        output = model(input_ids, attention_mask)
    tool_indices = output["tool"].argmax(dim=-1).tolist()
    presence = torch.sigmoid(output["presence"]).tolist()
    bool_values = torch.sigmoid(output["bool"]).tolist()
    results: List[Dict[str, Any]] = []
    for row_index, tool_index in enumerate(tool_indices):
        tool = TOOL_NAMES[tool_index]
        args: Dict[str, Any] = {}
        length = int(batch["lengths"][row_index])
        for field_index in FIELDS_BY_TOOL[tool]:
            field = FIELDS[field_index]
            if field.enum:
                head_index = ENUM_HEAD_INDEX[field_index]
                class_index = int(output["enum"][head_index][row_index].argmax())
                args[field.name] = field.enum[class_index]
                continue
            if field.type == "bool":
                args[field.name] = bool(bool_values[row_index][field_index] >= 0.5)
                continue
            if presence[row_index][field_index] < threshold:
                # Absent. The presence head was *trained* to say 0 here
                # (``presence_target = 0.0``), so emitting the schema default
                # instead would contradict the model's own answer: the call then
                # claims the player stated a value they never stated. That
                # materialisation was costing 1,507 of 3,056 val rows (49 pp) of
                # exact-match accuracy and leaking defaults into every recorded
                # trajectory. ``validate_call`` re-applies the default for the
                # executor, so nothing downstream needs it here.
                continue
            span = _best_span(
                output["span_start"][row_index, field_index],
                output["span_end"][row_index, field_index],
                length,
            )
            if span is None:
                continue
            text = tokenizer.decode(
                batch["input_ids"][row_index, span[0]:span[1] + 1].tolist(),
                skip_special_tokens=True,
            )
            parsed = _parse_span(text, field)
            if parsed is None:
                continue
            args[field.name] = parsed
        results.append({"tool": tool, "args": args})
    return results


@torch.no_grad()
def predict_rows(
    model: EncoderPolicy,
    tokenizer: Any,
    rows: Sequence[Dict[str, Any]],
    system_prompt: str,
    batch_size: int = 16,
    threshold: float = 0.5,
    infer: bool = False,
) -> Dict[str, Optional[Dict[str, Any]]]:
    dataset = EncoderDataset(
        rows, tokenizer, model.config.max_seq_len, system_prompt, infer=infer
    )
    predictions: Dict[str, Optional[Dict[str, Any]]] = {}
    offset = 0
    for batch in dataset.batches(batch_size, shuffle=False):
        decoded = decode_batch(model, tokenizer, batch, threshold=threshold)
        for item, prediction in zip(dataset.items[offset:offset + len(decoded)], decoded):
            predictions[str(item["id"])] = prediction
        offset += len(decoded)
    return predictions


def _tool_weights(rows: Sequence[Dict[str, Any]]) -> torch.Tensor:
    counts = Counter(str(row.get("tool")) for row in rows)
    total = sum(counts.values())
    weights = [
        math.sqrt(total / max(counts.get(name, 0), 1) / len(TOOL_NAMES))
        for name in TOOL_NAMES
    ]
    return torch.tensor(weights, dtype=torch.float)


def _cases_from_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "id": str(row.get("id")),
            "lang": row.get("lang") or "auto",
            "goal": row.get("goal") or "",
            "tool": row.get("tool"),
            "args": row.get("args") or {},
        }
        for row in rows
    ]


def _evaluate_rows(
    model: EncoderPolicy,
    tokenizer: Any,
    rows: Sequence[Dict[str, Any]],
    system_prompt: str,
    batch_size: int,
) -> Dict[str, Any]:
    predictions = predict_rows(
        model, tokenizer, rows, system_prompt, batch_size=batch_size)
    return tool_eval.evaluate(predictions, _cases_from_rows(rows))


@torch.no_grad()
def _evaluate_loss(
    model: EncoderPolicy,
    data: EncoderDataset,
    tool_weights: torch.Tensor,
    batch_size: int,
    device: torch.device,
) -> Dict[str, float]:
    model.eval()
    totals: List[float] = []
    parts: Dict[str, List[float]] = {}
    for batch in data.batches(batch_size, shuffle=False):
        batch = {key: value.to(device) for key, value in batch.items()}
        output = model(batch["input_ids"], batch["attention_mask"])
        loss, values = compute_loss(output, batch, tool_weights)
        totals.append(float(loss))
        for key, value in values.items():
            parts.setdefault(key, []).append(value)
    if not totals:
        return {"loss": float("inf")}
    report = {"loss": sum(totals) / len(totals)}
    for key, values in parts.items():
        report[key] = sum(values) / len(values)
    return report


def _save(
    path: Path,
    model: EncoderPolicy,
    tokenizer: Any,
    meta: Dict[str, Any],
) -> None:
    torch.save({
        "config": asdict(model.config),
        "state_dict": model.state_dict(),
        "tokenizer_json": tokenizer.to_str(),
        "meta": meta,
    }, path)


def load_checkpoint(
    path: Path,
    device: Optional[torch.device] = None,
) -> Tuple[EncoderPolicy, Any, Dict[str, Any]]:
    blob = torch.load(path, map_location="cpu", weights_only=False)
    config = EncoderConfig(**blob["config"])
    model = EncoderPolicy(config)
    try:
        model.load_state_dict(blob["state_dict"])
    except RuntimeError as exc:
        # Checkpoints do not carry an architecture version, so a head added after
        # the run makes the file silently unusable. Say so instead of crashing
        # with a bare key list.
        message = str(exc)
        missing = [line.strip() for line in message.splitlines() if "Missing key(s)" in line]
        raise SystemExit(
            f"{path} was trained with an older EncoderPolicy architecture.\n"
            f"{missing[0] if missing else message.splitlines()[0]}\n"
            "Retrain with the current code, or load with strict=False if you know "
            "the missing heads are unused.") from exc
    if device is not None:
        model.to(device)
    model.eval()
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_str(blob["tokenizer_json"])
    return model, tokenizer, blob.get("meta") or {}


def train(args: argparse.Namespace) -> Dict[str, Any]:
    if args.smoke:
        args.dim = 128
        args.layers = 2
        args.heads = 4
        args.ffn = 256
        args.batch_size = 4
        args.epochs = 1
        args.max_steps = 20
        args.eval_every = 10
        args.patience = 2
        args.target_loss = 0.0
        args.lr = 1e-3
        args.limit = args.limit or 200
    if args.threads:
        torch.set_num_threads(args.threads)
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    train_rows = _read_rows(Path(args.sft_dir) / "train.jsonl")
    val_rows = _read_rows(Path(args.sft_dir) / "val.jsonl")
    if args.limit:
        train_rows = train_rows[:args.limit]
        val_rows = val_rows[:max(1, args.limit // 4)]
    system_prompt = SYSTEM_PROMPTS[args.system]
    tokenizer = load_tokenizer_source(Path(args.tokenizer))
    # Continuation. The checkpoint's tokenizer and architecture win over the
    # command line: resuming with a different vocab trains the trunk against a
    # head that can never line up with it, and the loss curve looks plausible
    # while it happens. Weights are loaded with strict=False so a checkpoint
    # written before a head existed still resumes, with the gap printed.
    resume_blob: Optional[Dict[str, Any]] = None
    resume_meta: Dict[str, Any] = {}
    if getattr(args, "resume", None):
        from tokenizers import Tokenizer as _ResumeTokenizer

        resume_blob = torch.load(Path(args.resume), map_location="cpu",
                                 weights_only=False)
        resume_meta = resume_blob.get("meta") or {}
        tokenizer = _ResumeTokenizer.from_str(resume_blob["tokenizer_json"])
        print(f"resume    : {args.resume} (step {resume_meta.get('step')}, "
              f"tool {resume_meta.get('tool_accuracy')}, "
              f"call {resume_meta.get('call_accuracy')})")
    train_data = EncoderDataset(
        train_rows, tokenizer, args.max_seq_len, system_prompt)
    val_data = EncoderDataset(
        val_rows, tokenizer, args.max_seq_len, system_prompt)
    if not train_data.items:
        raise SystemExit("every training sequence was too long")
    config = EncoderConfig(
        vocab_size=tokenizer.get_vocab_size(),
        max_seq_len=args.max_seq_len,
        d_model=args.dim,
        n_layers=args.layers,
        n_heads=args.heads,
        ffn=args.ffn,
        dropout=args.dropout,
        use_anchors=bool(getattr(args, "anchor_mode", False)),
    )
    if resume_blob is not None:
        saved = EncoderConfig(**resume_blob["config"])
        mismatched = [name for name in ("d_model", "n_layers", "n_heads", "ffn",
                                        "vocab_size", "max_seq_len", "use_anchors")
                      if getattr(saved, name) != getattr(config, name)]
        if mismatched:
            print("resume    : architecture taken from the checkpoint "
                  f"({', '.join(mismatched)} differed on the command line)")
        config = saved
    model = EncoderPolicy(config)
    if resume_blob is not None:
        missing, unexpected = model.load_state_dict(resume_blob["state_dict"], strict=False)
        if missing or unexpected:
            print(f"resume    : loaded with gaps -- missing {sorted(missing)[:4]}, "
                  f"unexpected {sorted(unexpected)[:4]}")
        else:
            print("resume    : weights loaded, no key mismatch")
    if config.use_anchors:
        # The action head is derived from capability descriptions, not learned as a
        # class list: freeze it so the encoder learns to align with the anchors, and
        # refresh the anchors periodically so they track the encoder.
        model.tool_head.weight.requires_grad_(False)
        if model.tool_head.bias is not None:
            model.tool_head.bias.requires_grad_(False)
    params = sum(parameter.numel() for parameter in model.parameters())
    device = _resolve_device(args.device)
    model.to(device)
    if config.use_anchors and resume_blob is None:
        # On a resume the saved head *is* the anchor set from the previous run;
        # recomputing it here would move the training target on step 0, which is
        # the exact non-stationarity that made v9/v10 oscillate.
        from agent.schema import capability_descriptions

        anchors = refresh_capability_anchors(model, tokenizer, capability_descriptions())
        print(f"anchors   : {tuple(anchors.shape)} from capability descriptions "
              f"(source: registry)")
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=args.lr, weight_decay=args.weight_decay)
    planned_steps = math.ceil(len(train_data) / args.batch_size) * max(args.epochs, 1)
    if args.max_steps > 0:
        planned_steps = min(planned_steps, args.max_steps)
    # On a resume the counters start at the checkpoint's step, so the schedule has
    # to be planned in the same coordinate system. v15 was planned for 2,748 steps
    # while running from 1,500 to 3,000, so the cosine never annealed and the run
    # plateaued at 0.704 instead of converging.
    planned_steps += int((resume_meta or {}).get("step") or 0)
    # Linear warmup before the cosine decay. ``docs/BENCH_30M.md`` asked for this
    # in the same breath as the lower peak lr, and only the lr was implemented:
    # run v9 jumped straight to 1e-4 from step 0, thrashed for the first ~600
    # steps (train_loss 2.39 -> 1.44 -> 1.69 -> 1.52) and never recovered. With no
    # warmup the Adam moments are still cold at peak lr, so the first few hundred
    # updates are effectively random and every later epoch inherits the damage.
    warmup_steps = max(1, int(round(args.warmup_frac * planned_steps)))
    cosine_steps = max(planned_steps - warmup_steps, 1)
    warmup = torch.optim.lr_scheduler.LinearLR(
        optimizer, start_factor=0.05, end_factor=1.0, total_iters=warmup_steps)
    cosine = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cosine_steps)
    scheduler = torch.optim.lr_scheduler.SequentialLR(
        optimizer, schedulers=[warmup, cosine], milestones=[warmup_steps])
    # Inverse-frequency weighting is opt-in. It upweights rare tools, and with a
    # short run the head collapses onto exactly those classes — which is what
    # produced the degenerate checkpoints. Off by default in the training recipe.
    if str(getattr(args, "tool_weights", "on")) == "on":
        tool_weights = _tool_weights(train_rows).to(device)
    else:
        tool_weights = None
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tokenizer.save(str(out_dir / "tokenizer.json"))
    best_path = out_dir / "model.pt"
    best_val_loss = float("inf")
    best_metric = float("-inf") if getattr(args, "early_stop", "loss") != "loss" else float("inf")
    best_tool_accuracy = 0.0
    best_call_accuracy = 0.0
    best_step = 0
    best_epoch = 0
    stale = 0
    stopped = False
    stop_reason = ""
    epochs_completed = 0
    history: List[Dict[str, Any]] = []
    started = time.time()
    step = 0
    if resume_meta:
        # Continue the counters, not just the weights. Restarting them at zero
        # means the first eval of the resumed run always "improves" on nothing,
        # so a continuation can never be stopped early for being worse than the
        # checkpoint it started from.
        step = int(resume_meta.get("step") or 0)
        best_step = step
        best_epoch = int(resume_meta.get("epoch") or 0)
        best_tool_accuracy = float(resume_meta.get("tool_accuracy") or 0.0)
        best_call_accuracy = float(resume_meta.get("call_accuracy") or 0.0)
        best_val_loss = float(resume_meta.get("val_loss") or float("inf"))
        metric_name = str(getattr(args, "early_stop", "loss"))
        if metric_name == "call":
            best_metric = best_call_accuracy
        elif metric_name == "tool":
            best_metric = best_tool_accuracy
        else:
            best_metric = best_val_loss
        print(f"resume    : continuing from step {step} "
              f"(best tool {best_tool_accuracy:.3f} / call {best_call_accuracy:.3f})")
    model.train()
    print(f"train     : {json.dumps(train_data.stats())}")
    print(f"val       : {json.dumps(val_data.stats())}")
    print(f"model     : {params / 1e6:.2f}M params d={config.d_model} "
          f"L={config.n_layers} vocab={config.vocab_size}")
    print(f"device    : {device}")
    print(f"schedule  : {planned_steps} steps, eval every {args.eval_every}, "
          f"patience {args.patience}, min_delta {args.min_delta}")
    for epoch in range(1, max(args.epochs, 1) + 1):
        for batch in train_data.batches(
                args.batch_size, shuffle=True, rng=random.Random(args.seed + epoch)):
            if args.max_steps > 0 and step >= args.max_steps:
                break
            batch = {key: value.to(device) for key, value in batch.items()}
            output = model(batch["input_ids"], batch["attention_mask"])
            loss, parts = compute_loss(output, batch, tool_weights)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            step += 1
            if step == 1 or step % args.log_every == 0:
                print(f"  epoch {epoch} step {step}/{planned_steps} "
                      f"loss {parts['loss']:.4f} tool {parts['tool_loss']:.4f} "
                      f"span {parts['span_loss']:.4f} presence {parts['presence_loss']:.4f}")
            anchor_every = int(getattr(args, "anchor_every", 0) or 0)
            if config.use_anchors and anchor_every and step % anchor_every == 0:
                from agent.schema import capability_descriptions

                refresh_capability_anchors(model, tokenizer, capability_descriptions())
            if args.eval_every and (step % args.eval_every == 0 or step == planned_steps):
                val_loss_report = _evaluate_loss(
                    model, val_data, tool_weights, args.batch_size, device)
                report = _evaluate_rows(
                    model, tokenizer, val_rows, system_prompt, args.batch_size)
                totals = report["totals"]
                call_accuracy = totals["call_ok"] / max(totals["cases"], 1)
                tool_accuracy = totals["tool_ok"] / max(totals["cases"], 1)
                val_loss = val_loss_report["loss"]
                record = {
                    "epoch": epoch,
                    "step": step,
                    "train_loss": parts["loss"],
                    "val_loss": val_loss,
                    "val_tool_loss": val_loss_report.get("tool_loss"),
                    "val_span_loss": val_loss_report.get("span_loss"),
                    "val_presence_loss": val_loss_report.get("presence_loss"),
                    "tool_accuracy": tool_accuracy,
                    "call_accuracy": call_accuracy,
                }
                history.append(record)
                print(f"  val step {step} loss {val_loss:.4f} "
                      f"tool {tool_accuracy:.3f} call {call_accuracy:.3f}")
                # Early stopping on the metric that matters. Val loss improves while
                # tool accuracy stays flat — that is exactly how the previous run
                # "converged" to chance.
                metric = str(getattr(args, "early_stop", "loss"))
                if metric == "tool":
                    score = tool_accuracy
                    improved = score > best_metric + args.min_delta
                elif metric == "call":
                    score = call_accuracy
                    improved = score > best_metric + args.min_delta
                else:
                    score = val_loss
                    improved = score < best_metric - args.min_delta
                if improved:
                    best_metric = score
                    best_val_loss = val_loss
                    best_tool_accuracy = tool_accuracy
                    best_call_accuracy = call_accuracy
                    best_step = step
                    best_epoch = epoch
                    stale = 0
                    _save(best_path, model, tokenizer, {
                        "step": step,
                        "epoch": epoch,
                        "params": params,
                        "val_loss": val_loss,
                        "tool_accuracy": tool_accuracy,
                        "call_accuracy": call_accuracy,
                        "system_prompt": system_prompt,
                    })
                else:
                    stale += 1
                model.train()
                if args.target_loss > 0 and val_loss <= args.target_loss:
                    stopped = True
                    stop_reason = "target_loss"
                    break
                if stale >= args.patience:
                    stopped = True
                    stop_reason = "loss_plateau"
                    break
        epochs_completed = epoch
        if stopped or (args.max_steps > 0 and step >= args.max_steps):
            break
    if not best_path.exists():
        _save(best_path, model, tokenizer, {
            "step": step,
            "epoch": epochs_completed,
            "params": params,
            "system_prompt": system_prompt,
        })
    metrics = {
        "steps": step,
        "planned_steps": planned_steps,
        "epochs_requested": args.epochs,
        "epochs_completed": epochs_completed,
        "params": params,
        "config": asdict(config),
        "capability_source": __import__("agent.schema", fromlist=["x"]).CAPABILITY_SOURCE,
        "train": train_data.stats(),
        "val": val_data.stats(),
        "best_val_loss": None if best_val_loss == float("inf") else best_val_loss,
        "early_stop_metric": str(getattr(args, "early_stop", "loss")),
        "best_tool_accuracy": best_tool_accuracy,
        "best_call_accuracy": best_call_accuracy,
        "best_step": best_step,
        "best_epoch": best_epoch,
        "stopped_early": stopped,
        "stop_reason": stop_reason,
        "history": history,
        "elapsed_seconds": round(time.time() - started, 1),
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return metrics


def evaluate(args: argparse.Namespace) -> Dict[str, Any]:
    model, tokenizer, meta = load_checkpoint(
        Path(args.checkpoint), device=_resolve_device(args.device))
    system_prompt = meta.get("system_prompt") or ENCODER_SYSTEM_PROMPT
    if args.cases:
        rows = _read_rows(Path(args.cases))
    else:
        rows = list(tool_eval.GOLD)
    if args.limit:
        rows = rows[:args.limit]
    predictions = predict_rows(
        model, tokenizer, rows, system_prompt, batch_size=args.batch_size)
    report = tool_eval.evaluate(predictions, _cases_from_rows(rows))
    report["checkpoint"] = str(args.checkpoint)
    report["predictions"] = [
        {"id": key, "predicted": value} for key, value in predictions.items()]
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["totals"], ensure_ascii=False))
    for failure in (report.get("failures") or [])[:args.show_failures]:
        print(json.dumps(failure, ensure_ascii=False))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    train_parser = sub.add_parser("train")
    train_parser.add_argument("--sft-dir", default=str(REPO_ROOT / "data" / "core_sft_v3"))
    train_parser.add_argument("--tokenizer", required=True)
    train_parser.add_argument("--out", default=str(REPO_ROOT / "models" / "encoder_policy"))
    train_parser.add_argument("--resume", default=None,
                              help="checkpoint to continue from. Its tokenizer and "
                                   "architecture win over the command line; the "
                                   "optimizer restarts (fresh AdamW moments) and the "
                                   "step/metric counters continue from the checkpoint.")
    train_parser.add_argument("--system", choices=("encoder", "compact", "full"), default="encoder")
    train_parser.add_argument("--max-seq-len", type=int, default=256)
    train_parser.add_argument("--dim", type=int, default=512)
    train_parser.add_argument("--layers", type=int, default=7)
    train_parser.add_argument("--heads", type=int, default=8)
    train_parser.add_argument("--ffn", type=int, default=2048)
    train_parser.add_argument("--dropout", type=float, default=0.1)
    train_parser.add_argument("--batch-size", type=int, default=16)
    train_parser.add_argument("--epochs", type=int, default=1)
    train_parser.add_argument("--max-steps", type=int, default=0)
    train_parser.add_argument("--eval-every", type=int, default=25)
    train_parser.add_argument("--patience", type=int, default=2)
    train_parser.add_argument("--min-delta", type=float, default=0.01)
    train_parser.add_argument("--target-loss", type=float, default=0.0)
    train_parser.add_argument("--lr", type=float, default=3e-4)
    train_parser.add_argument("--warmup-frac", type=float, default=0.05,
                              help="fraction of planned steps spent warming the "
                                   "lr up linearly to its peak; 0 disables warmup")
    train_parser.add_argument("--weight-decay", type=float, default=0.01)
    train_parser.add_argument("--seed", type=int, default=7)
    train_parser.add_argument("--threads", type=int, default=0)
    train_parser.add_argument("--device", default="auto")
    train_parser.add_argument("--limit", type=int, default=0)
    train_parser.add_argument("--log-every", type=int, default=20)
    train_parser.add_argument("--tool-weights", choices=("on", "off"), default="off",
                              help="inverse-frequency weighting of the tool loss (off: "
                                   "the previous run collapsed onto rare tools with it on)")
    train_parser.add_argument("--early-stop", choices=("loss", "tool", "call"), default="loss",
                              help="metric that drives early stopping and best-checkpoint selection")
    train_parser.add_argument("--anchor-mode", action="store_true",
                              help="derive the action head from capability descriptions")
    train_parser.add_argument("--anchor-every", type=int, default=50,
                              help="refresh capability anchors every N steps")
    train_parser.add_argument("--smoke", action="store_true")

    eval_parser = sub.add_parser("eval")
    eval_parser.add_argument("--checkpoint", required=True)
    eval_parser.add_argument("--cases", default=None)
    eval_parser.add_argument("--limit", type=int, default=0)
    eval_parser.add_argument("--batch-size", type=int, default=16)
    eval_parser.add_argument("--device", default="auto")
    eval_parser.add_argument("--out", default=None)
    eval_parser.add_argument("--show-failures", type=int, default=10)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "train":
        train(args)
    elif args.command == "eval":
        evaluate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
