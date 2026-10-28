"""Train the command model: prompt text -> intent + action + slot spans.

Small on purpose: a word embedding, a BiGRU, an intent head, an action head
conditioned on the intent distribution, and a BIO slot tagger. The action is the
coarse-to-fine middle level (intent -> action -> slots); the slots are copied from
the user's own text as spans, not generated, so an unseen item name can still be
returned correctly.

It trains from `data/commands/*.jsonl` only, so the same file runs on Kaggle:

    python ai/train_command_model.py --data data/commands --out models/command_encoder
    python ai/train_command_model.py --predict "sell 5 bouftou wool"

Reports, per split: intent accuracy + macro-F1, action accuracy, exact-slot match,
and exact-command match, next to the rule baseline on the same rows. The corpus is
small (hundreds of rows); treat the numbers as a pipeline check, not a live claim.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.commands import INTENTS, SLOT_KEYS  # noqa: E402

TOKEN_RE = re.compile(r"-?\d+(?:[.,]\d+)?|[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]*|[^\s]",
                      re.UNICODE)
NUM_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")
SPAN_SLOTS = ("item", "mob", "area", "query", "zaap", "channel", "message",
              "quest_id", "price", "quantity", "x", "y", "all", "dungeon_only",
              "level_min")
CHANNEL_ALIASES = {
    "team": "team", "équipe": "team", "equipe": "team", "equipo": "team",
    "group": "team", "groupe": "team", "grupo": "team",
    "guild": "guild", "guilde": "guild", "gremio": "guild",
    "general": "general", "général": "general", "general": "general",
    "trade": "trade", "échanges": "trade", "comercio": "trade",
}
ALL_CUES = ("all", "everything", "every", "tout", "tous", "toute", "toutes",
            "todo", "todos", "todas", "ganze", "alles")
DUNGEON_CUES = ("dungeon", "donjon", "mazmorra", "dungeon")
TAG_NAMES = ["O"] + [prefix + slot for slot in SPAN_SLOTS
                     for prefix in ("B-", "I-")]
TAG_ID = {name: index for index, name in enumerate(TAG_NAMES)}


def tokenize(text: str) -> List[Tuple[str, int, int]]:
    tokens = []
    for match in TOKEN_RE.finditer(text or ""):
        raw = match.group(0)
        key = "<num>" if NUM_RE.match(raw) else raw.lower()
        tokens.append((key, match.start(), match.end()))
    return tokens


def _find_span(text: str, value: Any, used: set) -> Optional[Tuple[int, int]]:
    needle = str(value)
    if not needle:
        return None
    start = text.lower().find(needle.lower())
    while start >= 0:
        end = start + len(needle)
        if not any(start < e and end > s for s, e in used):
            return start, end
        start = text.lower().find(needle.lower(), start + 1)
    return None


def _find_cue(text: str, cues: Tuple[str, ...], used: set) -> Optional[Tuple[int, int]]:
    lowered = text.lower()
    for cue in cues:
        start = lowered.find(cue)
        while start >= 0:
            end = start + len(cue)
            if not any(start < e and end > s for s, e in used):
                return start, end
            start = lowered.find(cue, start + 1)
    return None


def align_tags(text: str, tokens: List[Tuple[str, int, int]],
               slots: Dict[str, Any]) -> List[int]:
    tags = [TAG_ID["O"]] * len(tokens)
    used: set = set()

    def mark(span: Optional[Tuple[int, int]], slot: str) -> None:
        if span is None:
            return
        start, end = span
        first = True
        for index, (_key, tok_start, tok_end) in enumerate(tokens):
            if tok_start < end and tok_end > start:
                name = ("B-" if first else "I-") + slot
                tags[index] = TAG_ID[name]
                first = False
        used.add(span)

    for slot in SPAN_SLOTS:
        if slot not in slots:
            continue
        value = slots[slot]
        if slot == "all":
            mark(_find_cue(text, ALL_CUES, used), slot)
        elif slot == "dungeon_only":
            mark(_find_cue(text, DUNGEON_CUES, used), slot)
        elif slot == "channel":
            cues = tuple(alias for alias, canonical in CHANNEL_ALIASES.items()
                         if canonical == value)
            mark(_find_cue(text, cues or (str(value),), used), slot)
        else:
            mark(_find_span(text, value, used), slot)
    return tags


def normalize_slots(slots: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (slots or {}).items():
        if key in ("all", "dungeon_only"):
            out[key] = bool(value)
        elif key in ("level_min", "price", "quantity", "quest_id", "x", "y"):
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                out[key] = value
        elif key == "channel":
            out[key] = CHANNEL_ALIASES.get(str(value).lower(), str(value).lower())
        elif key == "action":
            out[key] = str(value).lower()
        elif key == "price_rule":
            out[key] = str(value).lower()
        else:
            out[key] = str(value).strip().lower()
    return out


class CommandModel(nn.Module):
    def __init__(self, vocab: int, intents: int, actions: int, tags: int,
                 emb: int = 128, hidden: int = 128, dropout: float = 0.2):
        super().__init__()
        self.embedding = nn.Embedding(vocab, emb, padding_idx=0)
        self.encoder = nn.GRU(emb, hidden, batch_first=True, bidirectional=True)
        self.dropout = nn.Dropout(dropout)
        self.intent_head = nn.Linear(hidden * 2, intents)
        self.action_head = nn.Linear(hidden * 2 + intents, actions)
        self.tag_head = nn.Linear(hidden * 2, tags)

    def forward(self, ids, lengths):
        mask = (ids != 0).unsqueeze(-1).float()
        embedded = self.dropout(self.embedding(ids))
        encoded, _ = self.encoder(embedded)
        encoded = self.dropout(encoded)
        pooled = (encoded * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1.0)
        intent_logits = self.intent_head(pooled)
        intent_probs = F.softmax(intent_logits, dim=-1)
        action_logits = self.action_head(torch.cat([pooled, intent_probs], dim=-1))
        tag_logits = self.tag_head(encoded)
        return intent_logits, action_logits, tag_logits


class Data:
    def __init__(self, rows: List[Dict[str, Any]], vocab: Dict[str, int],
                 intents: List[str], actions: List[str]):
        self.rows = rows
        self.vocab = vocab
        self.intents = intents
        self.actions = actions
        self.intent_index = {name: i for i, name in enumerate(intents)}
        self.action_index = {name: i for i, name in enumerate(actions)}
        self.samples = []
        for row in rows:
            tokens = tokenize(row.get("text") or "")
            ids = [vocab.get(key, vocab["<unk>"]) for key, _s, _e in tokens] or [vocab["<pad>"]]
            tags = align_tags(row.get("text") or "", tokens, row.get("slots") or {})
            action = str((row.get("slots") or {}).get("action") or "none").lower()
            if action not in self.action_index:
                action = "none"
            self.samples.append({
                "ids": ids,
                "tags": tags,
                "intent": self.intent_index[row["intent"]],
                "action": self.action_index[action],
                "row": row,
                "tokens": tokens,
            })

    def batch(self, indices):
        items = [self.samples[i] for i in indices]
        width = max(len(item["ids"]) for item in items)
        ids = torch.zeros(len(items), width, dtype=torch.long)
        tags = torch.full((len(items), width), -100, dtype=torch.long)
        lengths = torch.zeros(len(items), dtype=torch.long)
        for i, item in enumerate(items):
            length = len(item["ids"])
            ids[i, :length] = torch.tensor(item["ids"])
            tags[i, :length] = torch.tensor(item["tags"])
            lengths[i] = length
        return (ids, tags, lengths,
                torch.tensor([item["intent"] for item in items]),
                torch.tensor([item["action"] for item in items]))


def decode_slots(text: str, tokens, tag_ids, action: str) -> Dict[str, Any]:
    slots: Dict[str, Any] = {}
    index = 0
    while index < len(tokens):
        name = TAG_NAMES[tag_ids[index]]
        if name.startswith("B-"):
            slot = name[2:]
            start = tokens[index][1]
            end = tokens[index][2]
            cursor = index
            while (cursor + 1 < len(tokens)
                   and TAG_NAMES[tag_ids[cursor + 1]] == "I-" + slot):
                cursor += 1
                end = tokens[cursor][2]
            raw = text[start:end].strip()
            if slot in ("all", "dungeon_only"):
                slots[slot] = True
            elif slot in ("level_min", "price", "quantity", "quest_id", "x", "y"):
                try:
                    slots[slot] = int(re.sub(r"[^\d-]", "", raw))
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


def macro_f1(pred: List[int], gold: List[int], n: int) -> float:
    scores = []
    for label in range(n):
        tp = sum(1 for p, g in zip(pred, gold) if p == label and g == label)
        fp = sum(1 for p, g in zip(pred, gold) if p == label and g != label)
        fn = sum(1 for p, g in zip(pred, gold) if p != label and g == label)
        if tp + fn == 0:
            continue
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(0.0 if precision + recall == 0 else
                      2 * precision * recall / (precision + recall))
    return sum(scores) / len(scores) if scores else 0.0


def evaluate(model: CommandModel, data: Data, indices: List[int],
             rule: bool = True) -> Dict[str, Any]:
    model.eval()
    ids, tags, lengths, intents, actions = data.batch(indices)
    with torch.no_grad():
        intent_logits, action_logits, tag_logits = model(ids, lengths)
    pred_intent = intent_logits.argmax(-1).tolist()
    pred_action = action_logits.argmax(-1).tolist()
    pred_tags = tag_logits.argmax(-1).tolist()
    gold_intent = intents.tolist()
    gold_action = actions.tolist()
    intent_hits = sum(p == g for p, g in zip(pred_intent, gold_intent))
    action_hits = sum(p == g for p, g in zip(pred_action, gold_action))
    slot_hits = full_hits = 0
    rule_intent_hits = rule_slot_hits = rule_full_hits = 0
    for position, index in enumerate(indices):
        sample = data.samples[index]
        row = sample["row"]
        tokens = sample["tokens"]
        text = row.get("text") or ""
        pred_slots = decode_slots(text, tokens, pred_tags[position],
                                  data.actions[pred_action[position]])
        gold_slots = normalize_slots(row.get("slots") or {})
        pred_norm = normalize_slots(pred_slots)
        slot_hits += pred_norm == gold_slots
        full_hits += (pred_norm == gold_slots
                      and data.intents[pred_intent[position]] == row["intent"])
        if rule:
            try:
                from agent.commands import rule_parse

                parsed = rule_parse(text)
            except Exception:      # noqa: BLE001 - the rule comparison is optional
                parsed = None
            if parsed is not None:
                rule_slots = normalize_slots(parsed.get("slots") or {})
                rule_intent_hits += parsed.get("intent") == row["intent"]
                rule_slot_hits += rule_slots == gold_slots
                rule_full_hits += (parsed.get("intent") == row["intent"]
                                   and rule_slots == gold_slots)
    total = max(1, len(indices))
    out = {
        "rows": len(indices),
        "intent_accuracy": round(intent_hits / total, 4),
        "intent_macro_f1": round(macro_f1(pred_intent, gold_intent,
                                          len(data.intents)), 4),
        "action_accuracy": round(action_hits / total, 4),
        "slot_exact": round(slot_hits / total, 4),
        "command_exact": round(full_hits / total, 4),
    }
    if rule:
        out["rule"] = {
            "intent_accuracy": round(rule_intent_hits / total, 4),
            "slot_exact": round(rule_slot_hits / total, 4),
            "command_exact": round(rule_full_hits / total, 4),
        }
    return out


def load_rows(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def train(args: argparse.Namespace) -> Dict[str, Any]:
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    splits = {name: load_rows(args.data / (name + ".jsonl"))
              for name in ("train", "val", "test")}
    counter = Counter()
    for row in splits["train"]:
        for key, _s, _e in tokenize(row.get("text") or ""):
            counter[key] += 1
    vocab = {"<pad>": 0, "<unk>": 1, "<num>": 2}
    for token, _count in counter.most_common():
        if token not in vocab:
            vocab[token] = len(vocab)
    intents = list(INTENTS)
    actions = ["none"] + sorted({str((row.get("slots") or {}).get("action") or "none").lower()
                                 for row in splits["train"]}
                                - {"none"})
    data = {name: Data(rows, vocab, intents, actions) for name, rows in splits.items()}
    model = CommandModel(len(vocab), len(intents), len(actions), len(TAG_NAMES))
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr,
                                  weight_decay=args.weight_decay)
    train_indices = list(range(len(data["train"].samples)))
    val_indices = list(range(len(data["val"].samples)))
    best = None
    best_state = None
    history = []
    for epoch in range(args.epochs):
        model.train()
        random.shuffle(train_indices)
        total_loss = 0.0
        for start in range(0, len(train_indices), args.batch):
            batch = train_indices[start:start + args.batch]
            ids, tags, lengths, intents_t, actions_t = data["train"].batch(batch)
            optimizer.zero_grad()
            intent_logits, action_logits, tag_logits = model(ids, lengths)
            loss = (F.cross_entropy(intent_logits, intents_t)
                    + 0.5 * F.cross_entropy(action_logits, actions_t)
                    + F.cross_entropy(tag_logits.reshape(-1, len(TAG_NAMES)),
                                      tags.reshape(-1), ignore_index=-100))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += float(loss.item())
        metrics = evaluate(model, data["val"], val_indices)
        score = metrics["intent_macro_f1"] + metrics["slot_exact"] + metrics["command_exact"]
        history.append({"epoch": epoch + 1, "loss": round(total_loss, 4), **metrics})
        if best is None or score > best:
            best = score
            best_state = {key: value.detach().clone()
                          for key, value in model.state_dict().items()}
    if best_state is not None:
        model.load_state_dict(best_state)
    result = {
        "params": sum(p.numel() for p in model.parameters()),
        "vocab": len(vocab),
        "actions": actions,
        "val": evaluate(model, data["val"], val_indices),
        "test": evaluate(model, data["test"], list(range(len(data["test"].samples)))),
        "best_val_score": round(best or 0.0, 4),
        "epochs": args.epochs,
        "history_tail": history[-5:],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    torch.save({"state": model.state_dict(), "vocab": vocab, "intents": intents,
                "actions": actions, "tags": TAG_NAMES}, args.out / "model.pt")
    (args.out / "meta.json").write_text(json.dumps({
        "vocab": vocab, "intents": intents, "actions": actions, "tags": TAG_NAMES,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.out / "metrics.json").write_text(json.dumps(result, indent=2) + "\n",
                                           encoding="utf-8")
    return result


def predict(text: str, out: Path) -> Dict[str, Any]:
    payload = torch.load(out / "model.pt", map_location="cpu", weights_only=False)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    vocab = meta["vocab"]
    intents = meta["intents"]
    actions = meta["actions"]
    tokens = tokenize(text)
    ids = [vocab.get(key, vocab["<unk>"]) for key, _s, _e in tokens] or [vocab["<pad>"]]
    model = CommandModel(len(vocab), len(intents), len(actions), len(meta["tags"]))
    model.load_state_dict(payload["state"])
    model.eval()
    tensor = torch.tensor([ids])
    with torch.no_grad():
        intent_logits, action_logits, tag_logits = model(tensor, torch.tensor([len(ids)]))
    intent = intents[int(intent_logits.argmax(-1))]
    action = actions[int(action_logits.argmax(-1))]
    slots = decode_slots(text, tokens, tag_logits.argmax(-1)[0].tolist(), action)
    return {"text": text, "intent": intent, "slots": slots,
            "confidence": round(float(F.softmax(intent_logits, -1).max()), 4)}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "commands")
    parser.add_argument("--out", type=Path, default=ROOT / "models" / "command_encoder")
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--predict", type=str, default=None)
    args = parser.parse_args(argv)
    if args.predict is not None:
        print(json.dumps(predict(args.predict, args.out), indent=2,
                         ensure_ascii=False))
        return 0
    result = train(args)
    print(json.dumps({key: result[key] for key in
                      ("params", "val", "test", "best_val_score")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
