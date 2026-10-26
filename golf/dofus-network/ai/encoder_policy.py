"""Multi-task encoder policy: one forward pass to tool + argument heads."""

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
from typing import Any, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch
from torch import nn

from ai.transformer import build_trunk, init_weights
from torch.nn import functional as F

from agent import tool_dataset, tool_eval
from agent.schema import CAPABILITY_SOURCE, TOOLS, capability_descriptions
from ai.tool_policy import BOS, EOS, PAD, _resolve_device, _special_id

TOOL_NAMES = [spec.name for spec in TOOLS]
TOOL_INDEX = {name: index for index, name in enumerate(TOOL_NAMES)}
SPOKEN_LANGUAGES = ["auto", "en", "fr", "es", "de"]


def _encoder_signature(tool):
    return ", ".join(
        name + ("*" if param.get("required") else "")
        for name, param in tool.params.items())


ENCODER_SYSTEM_PROMPT = "\n".join(
    ["Tools:"] + [f"- {t.name}({_encoder_signature(t)})" for t in TOOLS])
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


FIELDS = []
FIELDS_BY_TOOL = {}
for _t in TOOLS:
    FIELDS_BY_TOOL[_t.name] = list(range(len(FIELDS), len(FIELDS) + len(_t.params)))
    FIELDS.extend(
        FieldSpec(_t.name, _n, str(_p["type"]), tuple(_p.get("enum") or ()),
                  _p.get("default"), bool(_p.get("required")))
        for _n, _p in _t.params.items())
FIELD_INDEX = {(field.tool, field.name): index for index, field in enumerate(FIELDS)}
SPAN_FIELDS = [i for i, f in enumerate(FIELDS)
               if f.type in ("str", "int", "float", "str_list")]
BOOL_FIELDS = [i for i, f in enumerate(FIELDS) if f.type == "bool"]
ENUM_FIELDS = [i for i, f in enumerate(FIELDS) if f.enum]
ENUM_HEAD_INDEX = {f: h for h, f in enumerate(ENUM_FIELDS)}
_SPAN = frozenset(SPAN_FIELDS)
_DECODE = {name: [(i, FIELDS[i].name, FIELDS[i].type == "bool", FIELDS[i].enum,
                   ENUM_HEAD_INDEX.get(i, 0)) for i in index]
           for name, index in FIELDS_BY_TOOL.items()}


@dataclass
class EncoderConfig:
    vocab_size: int = 4096
    max_seq_len: int = 256
    d_model: int = 512
    n_layers: int = 7
    n_heads: int = 8
    ffn: int = 2048
    dropout: float = 0.1
    use_anchors: bool = False
    anchor_scale: float = 10.0


class EncoderPolicy(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.tok_emb, self.pos_emb, self.encoder, self.norm = build_trunk(config)
        d = config.d_model
        self.field_emb = nn.Embedding(len(FIELDS), d)
        self.presence_bias = nn.Parameter(torch.zeros(len(FIELDS)))
        self.bool_bias = nn.Parameter(torch.zeros(len(FIELDS)))
        self.start_proj = nn.Linear(d, d)
        self.end_proj = nn.Linear(d, d)
        self.tool_head = nn.Linear(d, len(TOOL_NAMES))
        self.lex_tool = nn.Linear(d, len(TOOL_NAMES))
        self.enum_heads = nn.ModuleList(
            [nn.Linear(d, len(FIELDS[i].enum)) for i in ENUM_FIELDS])
        self.dropout = nn.Dropout(config.dropout)
        self.apply(init_weights)

    def forward(self, input_ids, attention_mask=None):
        length = input_ids.shape[1]
        if length > self.config.max_seq_len:
            raise ValueError(
                f"sequence of {length} exceeds max_seq_len {self.config.max_seq_len}")
        emb = self.tok_emb(input_ids)
        hidden = emb + self.pos_emb(torch.arange(length, device=input_ids.device))
        padding = None if attention_mask is None else attention_mask.eq(0)
        hidden = self.norm(self.encoder(hidden, src_key_padding_mask=padding))
        if attention_mask is None:
            pooled = hidden[:, -1]
        else:
            index = (attention_mask.sum(dim=1).clamp_min(2) - 2).clamp_min(0)
            pooled = hidden.gather(1, index.unsqueeze(-1)).squeeze(1)
        pooled = self.dropout(pooled)
        if self.config.use_anchors:
            weights = F.normalize(self.tool_head.weight, dim=-1)
            tool_logits = F.normalize(pooled, dim=-1) @ weights.t() * self.config.anchor_scale
            lexical_tool = torch.zeros_like(tool_logits)
        else:
            lexical = self.lex_tool(emb)
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
        projection = torch.matmul(pooled, field_weights.t())
        return {
            "pooled": pooled,
            "tool": tool_logits + lexical_tool,
            "presence": projection + self.presence_bias,
            "bool": projection + self.bool_bias,
            "span_start": start,
            "span_end": end,
            "enum": [head(pooled) for head in self.enum_heads],
        }


@torch.no_grad()
def refresh_capability_anchors(model, tokenizer, descriptions=None, batch_size=16):
    if descriptions is None:
        descriptions = capability_descriptions()
    was_training = model.training
    model.eval()
    device = next(model.parameters()).device
    bos_id = _special_id(tokenizer, BOS)
    eos_id = _special_id(tokenizer, EOS)
    max_len = model.config.max_seq_len
    vectors = []
    for start in range(0, len(descriptions), batch_size):
        chunk = list(descriptions[start:start + batch_size])
        encoded = tokenizer.encode_batch(chunk, add_special_tokens=False)
        width = min(max_len, max(len(enc.ids) + 2 for enc in encoded))
        rows = []
        masks = []
        for enc in encoded:
            seq = list(enc.ids)[:width - 2]
            used = len(seq) + 2
            rows.append([bos_id] + seq + [eos_id] + [0] * (width - used))
            masks.append([1] * used + [0] * (width - used))
        output = model(torch.tensor(rows, dtype=torch.long, device=device),
                       torch.tensor(masks, dtype=torch.long, device=device))
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


def grow_capabilities(model, tokenizer, new_descriptions):
    with torch.no_grad():
        existing = model.tool_head.weight.detach().clone()
        count_before = existing.shape[0]
        widened = torch.nn.Linear(model.config.d_model, count_before + len(new_descriptions),
                                  bias=True).to(existing.device)
        widened.weight.data[:count_before] = existing
        model.tool_head = widened
        refresh_capability_anchors(model, tokenizer, capability_descriptions())
    return count_before + len(new_descriptions)


def _read_rows(path):
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_tokenizer_source(path):
    from tokenizers import Tokenizer

    if path.suffix.lower() == ".json":
        return Tokenizer.from_file(str(path))
    blob = torch.load(path, map_location="cpu", weights_only=False)
    tokenizer_json = blob.get("tokenizer_json")
    if not tokenizer_json:
        raise SystemExit(f"{path} has no embedded tokenizer")
    return Tokenizer.from_str(tokenizer_json)


def _find_offsets(text, value, field, user_start):
    if isinstance(value, (bool, list)) or value is None:
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


def _char_range_to_tokens(offsets, start, end):
    first = last = None
    for index, (token_start, token_end) in enumerate(offsets):
        if token_start < end and token_end > start:
            if first is None:
                first = index
            last = index
    return None if first is None else (first, last)


class EncoderDataset:
    def __init__(self, rows, tokenizer, max_seq_len, system_prompt, infer=False):
        self.tokenizer = tokenizer
        self.max_seq_len = max_seq_len
        self.pad_id = _special_id(tokenizer, PAD)
        self.bos_id = _special_id(tokenizer, BOS)
        self.eos_id = _special_id(tokenizer, EOS)
        self.system_prompt = system_prompt
        self.infer = infer
        self.items = []
        self.skipped = 0
        self.lengths = []
        encode = self._encode_row
        items = self.items
        lengths = self.lengths
        skipped = 0
        for row in rows:
            item = encode(row, infer=infer)
            if item is None:
                skipped += 1
                continue
            items.append(item)
            lengths.append(len(item["input_ids"]))
        self.skipped = skipped

    def _encode_row(self, row, infer=False):
        goal = str(row.get("goal") or "").strip()
        tool = str(row.get("tool") or "")
        if not goal:
            return None
        if not infer and (not tool or tool not in TOOL_INDEX):
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
        count = len(FIELDS)
        span_start = [-100] * count
        span_end = [-100] * count
        presence_target = [0.0] * count
        presence_mask = [0.0] * count
        bool_target = [0.0] * count
        bool_mask = [0.0] * count
        enum_target = [-100] * count
        enum_mask = [0.0] * count
        for index in FIELDS_BY_TOOL.get(tool, ()):
            field = FIELDS[index]
            value = args.get(field.name)
            if field.type == "bool":
                bool_mask[index] = 1.0
                bool_target[index] = float(
                    value if value is not None else (field.default or False))
                continue
            enum = field.enum
            if enum:
                chosen = value if value is not None else field.default
                if chosen in enum:
                    enum_mask[index] = 1.0
                    enum_target[index] = enum.index(chosen)
                continue
            if index not in _SPAN:
                continue
            presence_mask[index] = 1.0
            if value is None:
                continue
            found = _find_offsets(text, value, field, user_start)
            if not found:
                continue
            token_range = _char_range_to_tokens(offsets, found[0], found[1])
            if not token_range:
                continue
            presence_target[index] = 1.0
            span_start[index] = token_range[0]
            span_end[index] = token_range[1]
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

    def __len__(self):
        return len(self.items)

    def stats(self):
        if not self.lengths:
            return {"examples": 0, "skipped": self.skipped}
        ordered = sorted(self.lengths)
        count = len(ordered)
        return {
            "examples": count,
            "skipped": self.skipped,
            "min": ordered[0],
            "p50": ordered[count // 2],
            "p95": ordered[min(count - 1, int(count * 0.95))],
            "max": ordered[-1],
        }

    def batches(self, batch_size, shuffle=False, rng=None):
        indices = list(range(len(self.items)))
        if shuffle:
            (rng or random).shuffle(indices)
        items = self.items
        pad = self.pad_id
        for offset in range(0, len(indices), batch_size):
            chunk = [items[index] for index in indices[offset:offset + batch_size]]
            lengths = [len(item["input_ids"]) for item in chunk]
            width = max(lengths)
            input_ids = torch.tensor(
                [item["input_ids"] + [pad] * (width - n)
                 for item, n in zip(chunk, lengths)], dtype=torch.long)
            attention = torch.tensor(
                [[1] * n + [0] * (width - n) for n in lengths], dtype=torch.long)
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
                "lengths": torch.tensor(lengths, dtype=torch.long),
                "tool": torch.tensor(
                    [TOOL_INDEX.get(item["tool"], -1) for item in chunk],
                    dtype=torch.long),
            }


def _masked_binary_loss(logits, target, mask):
    loss = F.binary_cross_entropy_with_logits(logits, target, reduction="none")
    return (loss * mask).sum() / mask.sum().clamp_min(1.0)


def compute_loss(output, batch, tool_weights=None):
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
    enum_output = output["enum"]
    enum_target = batch["enum_target"]
    enum_mask = batch["enum_mask"]
    enum_losses = []
    for head_index, field_index in enumerate(ENUM_FIELDS):
        mask = enum_mask[:, field_index].bool()
        if mask.any():
            enum_losses.append(F.cross_entropy(
                enum_output[head_index][mask], enum_target[mask, field_index]))
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
def _best_span(start_logits, end_logits, length, max_span=32):
    starts = start_logits[:length].clone()
    ends = end_logits[:length].clone()
    starts[0] = -1e4
    ends[0] = -1e4
    k = 5 if length > 5 else length
    top_starts, start_indices = torch.topk(starts, k)
    top_ends, end_indices = torch.topk(ends, k)
    start_values = top_starts.tolist()
    end_values = top_ends.tolist()
    best = None
    for start_value, start_index in zip(start_values, start_indices.tolist()):
        for end_value, end_index in zip(end_values, end_indices.tolist()):
            if end_index < start_index or end_index - start_index >= max_span:
                continue
            score = start_value + end_value
            if best is None or score > best[0]:
                best = (score, start_index, end_index)
    return None if best is None else (best[1], best[2])


def _parse_span(text, field):
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
def decode_batch(model, tokenizer, batch, threshold=0.5, output=None):
    if output is None:
        device = next(model.parameters()).device
        output = model(batch["input_ids"].to(device), batch["attention_mask"].to(device))
    tool_indices = output["tool"].argmax(dim=-1).tolist()
    presence = torch.sigmoid(output["presence"]).tolist()
    bool_values = torch.sigmoid(output["bool"]).tolist()
    enum_values = [head.argmax(dim=-1).tolist() for head in output["enum"]]
    lengths = batch["lengths"].tolist()
    input_ids = batch["input_ids"]
    span_start, span_end = output["span_start"], output["span_end"]
    decode = tokenizer.decode
    results = []
    for row_index, tool_index in enumerate(tool_indices):
        tool = TOOL_NAMES[tool_index]
        args = {}
        length = lengths[row_index]
        row_presence = presence[row_index]
        row_bools = bool_values[row_index]
        for index, name, is_bool, enum, head_index in _DECODE[tool]:
            if enum:
                args[name] = enum[enum_values[head_index][row_index]]
            elif is_bool:
                args[name] = row_bools[index] >= 0.5
            elif row_presence[index] >= threshold:
                span = _best_span(span_start[row_index, index],
                                  span_end[row_index, index], length)
                if span is None:
                    continue
                text = decode(
                    input_ids[row_index, span[0]:span[1] + 1].tolist(),
                    skip_special_tokens=True,
                )
                parsed = _parse_span(text, FIELDS[index])
                if parsed is not None:
                    args[name] = parsed
        results.append({"tool": tool, "args": args})
    return results


@torch.no_grad()
def predict_rows(model, tokenizer, rows, system_prompt, batch_size=16,
                 threshold=0.5, infer=False):
    dataset = EncoderDataset(
        rows, tokenizer, model.config.max_seq_len, system_prompt, infer=infer
    )
    predictions = {}
    items = iter(dataset.items)
    for batch in dataset.batches(batch_size, shuffle=False):
        for item, prediction in zip(items, decode_batch(
                model, tokenizer, batch, threshold=threshold)):
            predictions[str(item["id"])] = prediction
    return predictions


def _tool_weights(rows):
    counts = Counter(str(row.get("tool")) for row in rows)
    total = sum(counts.values())
    return torch.tensor(
        [math.sqrt(total / max(counts.get(name, 0), 1) / len(TOOL_NAMES))
         for name in TOOL_NAMES], dtype=torch.float)


def _cases_from_rows(rows):
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


def _evaluate_rows(model, tokenizer, rows, system_prompt, batch_size):
    predictions = predict_rows(
        model, tokenizer, rows, system_prompt, batch_size=batch_size)
    return tool_eval.evaluate(predictions, _cases_from_rows(rows))


@torch.no_grad()
def _evaluate_loss(model, data, tool_weights, batch_size, device):
    model.eval()
    totals = []
    parts = {}
    for batch in data.batches(batch_size, shuffle=False):
        batch = {key: value.to(device) for key, value in batch.items()}
        loss, values = compute_loss(
            model(batch["input_ids"], batch["attention_mask"]),
            batch, tool_weights)
        totals.append(float(loss))
        for key, value in values.items():
            parts.setdefault(key, []).append(value)
    if not totals:
        return {"loss": float("inf")}
    report = {"loss": sum(totals) / len(totals)}
    for key, values in parts.items():
        report[key] = sum(values) / len(values)
    return report


def _save(path, model, tokenizer, meta):
    torch.save({
        "config": asdict(model.config),
        "state_dict": model.state_dict(),
        "tokenizer_json": tokenizer.to_str(),
        "meta": meta,
    }, path)


def load_checkpoint(path, device=None):
    blob = torch.load(path, map_location="cpu", weights_only=False)
    config = EncoderConfig(**blob["config"])
    model = EncoderPolicy(config)
    try:
        model.load_state_dict(blob["state_dict"])
    except RuntimeError as exc:
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


def train(args):
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
    resume_blob = None
    resume_meta = {}
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
        model.tool_head.weight.requires_grad_(False)
        if model.tool_head.bias is not None:
            model.tool_head.bias.requires_grad_(False)
    params = sum(parameter.numel() for parameter in model.parameters())
    device = _resolve_device(args.device)
    model.to(device)
    if config.use_anchors and resume_blob is None:
        anchors = refresh_capability_anchors(model, tokenizer, capability_descriptions())
        print(f"anchors   : {tuple(anchors.shape)} from capability descriptions "
              f"(source: registry)")
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=args.lr, weight_decay=args.weight_decay)
    planned_steps = math.ceil(len(train_data) / args.batch_size) * max(args.epochs, 1)
    if args.max_steps > 0:
        planned_steps = min(planned_steps, args.max_steps)
    planned_steps += int((resume_meta or {}).get("step") or 0)
    warmup_steps = max(1, int(round(args.warmup_frac * planned_steps)))
    cosine_steps = max(planned_steps - warmup_steps, 1)
    warmup = torch.optim.lr_scheduler.LinearLR(
        optimizer, start_factor=0.05, end_factor=1.0, total_iters=warmup_steps)
    cosine = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cosine_steps)
    scheduler = torch.optim.lr_scheduler.SequentialLR(
        optimizer, schedulers=[warmup, cosine], milestones=[warmup_steps])
    if str(getattr(args, "tool_weights", "on")) == "on":
        tool_weights = _tool_weights(train_rows).to(device)
    else:
        tool_weights = None
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tokenizer.save(str(out_dir / "tokenizer.json"))
    best_path = out_dir / "model.pt"
    best_val_loss = float("inf")
    metric_name = str(getattr(args, "early_stop", "loss"))
    best_metric = float("inf") if metric_name == "loss" else float("-inf")
    best_tool_accuracy = 0.0
    best_call_accuracy = 0.0
    best_step = 0
    best_epoch = 0
    stale = 0
    stopped = False
    stop_reason = ""
    epochs_completed = 0
    history = []
    started = time.time()
    step = 0
    if resume_meta:
        step = int(resume_meta.get("step") or 0)
        best_step = step
        best_epoch = int(resume_meta.get("epoch") or 0)
        best_tool_accuracy = float(resume_meta.get("tool_accuracy") or 0.0)
        best_call_accuracy = float(resume_meta.get("call_accuracy") or 0.0)
        best_val_loss = float(resume_meta.get("val_loss") or float("inf"))
        best_metric = {"call": best_call_accuracy,
                       "tool": best_tool_accuracy}.get(metric_name, best_val_loss)
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
    anchor_every = int(getattr(args, "anchor_every", 0) or 0)
    for epoch in range(1, max(args.epochs, 1) + 1):
        for batch in train_data.batches(
                args.batch_size, shuffle=True, rng=random.Random(args.seed + epoch)):
            if args.max_steps > 0 and step >= args.max_steps:
                break
            batch = {key: value.to(device) for key, value in batch.items()}
            loss, parts = compute_loss(
                model(batch["input_ids"], batch["attention_mask"]),
                batch, tool_weights)
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
            if config.use_anchors and anchor_every and step % anchor_every == 0:
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
                if metric_name == "tool":
                    score = tool_accuracy
                    improved = score > best_metric + args.min_delta
                elif metric_name == "call":
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
        "capability_source": CAPABILITY_SOURCE,
        "train": train_data.stats(),
        "val": val_data.stats(),
        "best_val_loss": None if best_val_loss == float("inf") else best_val_loss,
        "early_stop_metric": metric_name,
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


def evaluate(args):
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
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["totals"], ensure_ascii=False))
    for failure in (report.get("failures") or [])[:args.show_failures]:
        print(json.dumps(failure, ensure_ascii=False))
    return report


def build_parser():
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


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "train":
        train(args)
    elif args.command == "eval":
        evaluate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
