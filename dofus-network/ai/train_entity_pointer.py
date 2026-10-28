"""Entity-pointer training scaffold.

Reads `data/entity_pointer/{train,val,test}.jsonl` and trains a small
entity-pointer model:

    quest text + self/quest numbers  ->  query
    live entity rows                 ->  candidate keys
    score = query . key              ->  softmax over candidates
    move/progress heads              ->  observable outcome

This is the first stage, not the 80M/40M target. It exists so the dataset,
pointer labels, split and metrics are executable before a large Kaggle run.
Scale with `--hidden` and `--layers` later; the data, not the width, is currently
the limit (4,017 rows, 1,084 non-read actions, 325 moved, 4 progress).

    python -m ai.train_entity_pointer --epochs 2 --batch 32 --include-reads
    python -m ai.train_entity_pointer --data data/entity_pointer --out models/entity_pointer
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO_ROOT / "data" / "entity_pointer"
DEFAULT_OUT = REPO_ROOT / "models" / "entity_pointer"

VOCAB = 4096
MAX_TOKENS = 32
MAX_CANDIDATES = 80
KINDS = ("self", "npc", "interactive", "mob", "reply", "preset", "item",
         "price", "tool", "objective")
KIND_INDEX = {name: i for i, name in enumerate(KINDS)}
ACTION_TOOLS = (
    "knowledge.search", "nav.goto", "nav.follow", "quest.list", "quest.advance",
    "farm.start", "farm.stop", "fight.join", "fight.pass", "inv.list", "inv.sell",
    "inv.equip", "hdv.scan", "hdv.list", "hdv.buy", "hdv.prices",
    "tap.interactive", "chat.say", "state.snapshot", "safe.stop",
    "knowledge.remember", "knowledge.forget", "fight.state", "fight.plan",
    "fight.estimate",
)
_TOKEN = re.compile(r"[A-Za-zÀ-ÿ0-9_'’-]+")


def _tokens(text: Any) -> List[int]:
    if not isinstance(text, str):
        text = str(text or "")
    words = _TOKEN.findall(text.lower())[:MAX_TOKENS]
    return [hash(word) % VOCAB for word in words] or [0]


def _num(value: Any, scale: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(-1.0, min(1.0, number / scale))


def _entity_id(value: Any) -> float:
    try:
        number = abs(int(value))
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, math.log1p(number) / 20.0)


def _candidate(kind: str, ident: Any, text: Any, cell: Any,
               self_cell: Any) -> Dict[str, Any]:
    try:
        distance = abs(float(cell) - float(self_cell)) / 560.0
    except (TypeError, ValueError):
        distance = 0.0
    try:
        raw_id = abs(int(ident))
    except (TypeError, ValueError):
        raw_id = None
    return {
        "kind": kind,
        "id": _entity_id(ident),
        "raw_id": raw_id,
        "text": text,
        "cell": _num(cell, 560.0),
        "distance": max(0.0, min(1.0, distance)),
    }


def _candidates(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    entities = row.get("entities") or {}
    me = entities.get("self") or {}
    self_cell = me.get("cell")
    out: List[Dict[str, Any]] = []
    for npc in entities.get("npcs") or []:
        out.append(_candidate("npc", npc.get("template_id", npc.get("actor_id")),
                              npc.get("name"), npc.get("cell"), self_cell))
    for interactive in entities.get("interactives") or []:
        out.append(_candidate("interactive", interactive.get("element_id"),
                              interactive.get("kind"), interactive.get("cell"), self_cell))
    for mob in entities.get("mobs") or []:
        out.append(_candidate("mob", mob.get("main_mob_id"),
                              mob.get("main_mob_id"), mob.get("cell"), self_cell))
    dialog = entities.get("dialog") or {}
    for reply in dialog.get("replies") or []:
        out.append(_candidate("reply", reply.get("id"), reply.get("text"),
                              me.get("cell"), self_cell))
    inventory = entities.get("inventory") or {}
    for preset in inventory.get("presets") or []:
        out.append(_candidate("preset", preset.get("preset_id"),
                              preset.get("name"), me.get("cell"), self_cell))
    for item in inventory.get("items") or []:
        out.append(_candidate("item", item.get("gid"), item.get("gid"),
                              me.get("cell"), self_cell))
    for price in entities.get("prices") or []:
        if isinstance(price, dict):
            out.append(_candidate("price", price.get("gid", price.get("uid")),
                                  price.get("gid"), me.get("cell"), self_cell))
    for tool in ACTION_TOOLS:
        out.append(_candidate("tool", 0, tool, me.get("cell"), self_cell))
    quest = entities.get("quest") or {}
    if quest.get("objective_id") is not None:
        out.append(_candidate("objective", quest.get("objective_id"),
                              quest.get("text"), quest.get("map_id"), self_cell))
    return out


def _label_index(chosen: Dict[str, Any], candidates: List[Dict[str, Any]],
                 tool: str) -> Optional[int]:
    kind = str(chosen.get("kind") or "")
    if kind == "npc":
        wanted = chosen.get("npc_id")
        for index, cand in enumerate(candidates):
            if cand["kind"] == "npc" and _same_id(cand, wanted):
                return index
    elif kind == "interactive":
        wanted = chosen.get("element_id")
        for index, cand in enumerate(candidates):
            if cand["kind"] == "interactive" and _same_id(cand, wanted):
                return index
    elif kind == "reply":
        wanted = chosen.get("reply")
        texts = str(chosen.get("text") or "").lower()
        for index, cand in enumerate(candidates):
            if cand["kind"] != "reply":
                continue
            if _same_id(cand, wanted):
                return index
            if texts and str(cand.get("text") or "").lower() == texts:
                return index
    elif kind == "preset":
        wanted = chosen.get("preset_id")
        for index, cand in enumerate(candidates):
            if cand["kind"] == "preset" and _same_id(cand, wanted):
                return index
    elif kind == "listing":
        wanted = chosen.get("uid")
        if wanted is None:
            wanted = chosen.get("gid")
        for index, cand in enumerate(candidates):
            if cand["kind"] == "price" and _same_id(cand, wanted):
                return index
    elif kind == "group":
        wanted = chosen.get("group_id")
        for index, cand in enumerate(candidates):
            if cand["kind"] == "mob" and _same_id(cand, wanted):
                return index
    elif kind == "objective":
        wanted = chosen.get("objective_id")
        for index, cand in enumerate(candidates):
            if cand["kind"] == "objective" and _same_id(cand, wanted):
                return index
    for index, cand in enumerate(candidates):
        if cand["kind"] == "tool" and str(cand.get("text")) == tool:
            return index
    return None


def _same_id(candidate: Dict[str, Any], wanted: Any) -> bool:
    if wanted is None:
        return False
    try:
        wanted_number = abs(int(wanted))
    except (TypeError, ValueError):
        return False
    return candidate.get("raw_id") == wanted_number


class PointerRows(Dataset):
    def __init__(self, rows: Iterable[Dict[str, Any]], include_reads: bool) -> None:
        self.items: List[Dict[str, Any]] = []
        for row in rows:
            if row.get("was_read") and not include_reads:
                continue
            candidates = _candidates(row)
            if len(candidates) < 2:
                continue
            chosen = row.get("chosen") or {}
            tool = str(row.get("tool") or "")
            label = _label_index(chosen, candidates, tool)
            if label is None:
                continue
            if label >= MAX_CANDIDATES:
                candidates = [candidates[label]] + candidates[:MAX_CANDIDATES - 1]
                label = 0
            else:
                candidates = candidates[:MAX_CANDIDATES]
            self.items.append({
                "quest": " ".join(str(row.get("goal") or "").split())
                         + " " + str(((row.get("entities") or {}).get("quest") or {}).get("text") or ""),
                "quest_num": [
                    _num(((row.get("entities") or {}).get("self") or {}).get("level"), 200.0),
                    _num(((row.get("entities") or {}).get("self") or {}).get("kamas"), 1_000_000.0),
                    _num(((row.get("entities") or {}).get("self") or {}).get("hp"), 1000.0),
                    _num(((row.get("entities") or {}).get("self") or {}).get("max_hp"), 1000.0),
                    _num(len(((row.get("entities") or {}).get("inventory") or {}).get("items") or []), 120.0),
                    _num(len(((row.get("entities") or {}).get("npcs") or [])), 40.0),
                    _num(len(((row.get("entities") or {}).get("interactives") or [])), 40.0),
                    _num(len(((row.get("entities") or {}).get("mobs") or [])), 20.0),
                ],
                "candidates": candidates,
                "label": label,
                "moved": 1.0 if row.get("moved") else 0.0,
                "progress": 1.0 if row.get("progress") else 0.0,
            })

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        return self.items[index]


def collate(items: List[Dict[str, Any]]) -> Dict[str, torch.Tensor]:
    batch = len(items)
    max_cand = max(len(item["candidates"]) for item in items)
    max_tokens = MAX_TOKENS
    quest = torch.zeros((batch, max_tokens), dtype=torch.long)
    quest_mask = torch.zeros((batch, max_tokens), dtype=torch.float)
    cand = torch.zeros((batch, max_cand, max_tokens), dtype=torch.long)
    cand_tok_mask = torch.zeros((batch, max_cand, max_tokens), dtype=torch.float)
    cand_mask = torch.zeros((batch, max_cand), dtype=torch.float)
    cand_kind = torch.zeros((batch, max_cand), dtype=torch.long)
    cand_num = torch.zeros((batch, max_cand, 4), dtype=torch.float)
    quest_num = torch.zeros((batch, 8), dtype=torch.float)
    labels = torch.zeros(batch, dtype=torch.long)
    moved = torch.zeros(batch, dtype=torch.float)
    progress = torch.zeros(batch, dtype=torch.float)
    for b, item in enumerate(items):
        ids = _tokens(item["quest"])
        quest[b, :len(ids)] = torch.tensor(ids)
        quest_mask[b, :len(ids)] = 1.0
        quest_num[b] = torch.tensor(item["quest_num"], dtype=torch.float)
        labels[b] = int(item["label"])
        moved[b] = item["moved"]
        progress[b] = item["progress"]
        for c, candidate in enumerate(item["candidates"]):
            ids = _tokens(candidate.get("text"))
            cand[b, c, :len(ids)] = torch.tensor(ids)
            cand_tok_mask[b, c, :len(ids)] = 1.0
            cand_mask[b, c] = 1.0
            cand_kind[b, c] = KIND_INDEX.get(candidate["kind"], 0)
            cand_num[b, c] = torch.tensor([
                candidate["id"], candidate["cell"], candidate["distance"],
                1.0 if candidate["kind"] == "npc" else 0.0,
            ], dtype=torch.float)
    return {
        "quest": quest, "quest_mask": quest_mask, "quest_num": quest_num,
        "cand": cand, "cand_tok_mask": cand_tok_mask,
        "cand_mask": cand_mask, "cand_kind": cand_kind,
        "cand_num": cand_num, "labels": labels, "moved": moved,
        "progress": progress,
    }


class EntityPointer(nn.Module):
    def __init__(self, hidden: int = 128, layers: int = 2) -> None:
        super().__init__()
        self.tok = nn.Embedding(VOCAB, hidden)
        self.kind = nn.Embedding(len(KINDS), 16)
        entity_in = hidden + 16 + 4
        self.entity = nn.Sequential(
            nn.Linear(entity_in, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
        )
        self.query = nn.Sequential(
            nn.Linear(hidden + 8, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
        )
        self.move = nn.Sequential(nn.Linear(hidden * 2, hidden // 2), nn.ReLU(),
                                  nn.Linear(hidden // 2, 1))
        self.progress = nn.Sequential(nn.Linear(hidden * 2, hidden // 2), nn.ReLU(),
                                      nn.Linear(hidden // 2, 1))

    @staticmethod
    def _pool(ids: torch.Tensor, mask: torch.Tensor,
              embedding: nn.Embedding) -> torch.Tensor:
        vectors = embedding(ids)
        weights = mask.unsqueeze(-1)
        total = (vectors * weights).sum(1) / weights.sum(1).clamp(min=1.0)
        return total

    def forward(self, batch: Dict[str, torch.Tensor]) -> Tuple[torch.Tensor, ...]:
        q_text = self._pool(batch["quest"], batch["quest_mask"], self.tok)
        q = self.query(torch.cat([q_text, batch["quest_num"]], dim=-1))
        flat_ids = batch["cand"].reshape(-1, MAX_TOKENS)
        flat_mask = batch["cand_tok_mask"].reshape(-1, MAX_TOKENS)
        e_text = self._pool(flat_ids, flat_mask, self.tok)
        e_text = e_text.reshape(batch["cand"].shape[0], batch["cand"].shape[1], -1)
        kind = self.kind(batch["cand_kind"])
        entity = self.entity(torch.cat([e_text, kind, batch["cand_num"]], dim=-1))
        scores = (entity * q.unsqueeze(1)).sum(-1) / math.sqrt(entity.shape[-1])
        weights = batch["cand_mask"].unsqueeze(-1)
        pooled = (entity * weights).sum(1) / weights.sum(1).clamp(min=1.0)
        head = torch.cat([q, pooled], dim=-1)
        return scores, self.move(head).squeeze(-1), self.progress(head).squeeze(-1)


def _auc(labels: torch.Tensor, probs: torch.Tensor) -> Optional[float]:
    labels = labels.detach().cpu()
    probs = probs.detach().cpu()
    positives = probs[labels > 0.5]
    negatives = probs[labels <= 0.5]
    if len(positives) == 0 or len(negatives) == 0:
        return None
    comparisons = (positives[:, None] > negatives[None, :]).float().mean().item()
    ties = (positives[:, None] == negatives[None, :]).float().mean().item()
    return comparisons + 0.5 * ties


def _ece(labels: torch.Tensor, probs: torch.Tensor, bins: int = 10) -> float:
    labels = labels.detach().cpu()
    probs = probs.detach().cpu()
    total = 0.0
    for i in range(bins):
        low, high = i / bins, (i + 1) / bins
        mask = (probs >= low) & (probs < high if i < bins - 1 else probs <= high)
        if mask.sum() == 0:
            continue
        confidence = probs[mask].mean().item()
        accuracy = labels[mask].mean().item()
        total += mask.float().mean().item() * abs(confidence - accuracy)
    return total


def evaluate(model: nn.Module, loader: DataLoader) -> Dict[str, Any]:
    model.eval()
    top1 = correct = total = 0
    moved_labels: List[torch.Tensor] = []
    moved_probs: List[torch.Tensor] = []
    progress_labels: List[torch.Tensor] = []
    progress_probs: List[torch.Tensor] = []
    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(next(model.parameters()).device)
                     for key, value in batch.items()}
            scores, moved, progress = model(batch)
            predictions = scores.argmax(dim=1)
            correct += int((predictions == batch["labels"]).sum())
            total += int(batch["labels"].numel())
            moved_labels.append(batch["moved"])
            moved_probs.append(torch.sigmoid(moved))
            progress_labels.append(batch["progress"])
            progress_probs.append(torch.sigmoid(progress))
    moved_l = torch.cat(moved_labels) if moved_labels else torch.zeros(0)
    moved_p = torch.cat(moved_probs) if moved_probs else torch.zeros(0)
    progress_l = torch.cat(progress_labels) if progress_labels else torch.zeros(0)
    progress_p = torch.cat(progress_probs) if progress_probs else torch.zeros(0)
    return {
        "rows": total,
        "top1": (correct / total) if total else 0.0,
        "moved_base_rate": float(moved_l.mean()) if len(moved_l) else 0.0,
        "moved_auc": _auc(moved_l, moved_p),
        "moved_ece": _ece(moved_l, moved_p) if len(moved_l) else 0.0,
        "progress_base_rate": float(progress_l.mean()) if len(progress_l) else 0.0,
        "progress_auc": _auc(progress_l, progress_p),
        "progress_ece": _ece(progress_l, progress_p) if len(progress_l) else 0.0,
    }


def load_split(data: Path, split: str) -> List[Dict[str, Any]]:
    path = data / (split + ".jsonl")
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--include-reads", action="store_true",
                        help="include state.snapshot rows (default: actions only)")
    parser.add_argument("--limit", type=int, default=0,
                        help="cap train rows for a smoke run (0 = all)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--device", default=None,
                        help="cpu, cuda, or auto (default: cuda when available)")
    args = parser.parse_args(argv)
    torch.manual_seed(args.seed)
    device = torch.device(args.device or ("cuda" if torch.cuda.is_available() else "cpu"))

    train_rows = PointerRows(load_split(args.data, "train"), args.include_reads)
    val_rows = PointerRows(load_split(args.data, "val"), args.include_reads)
    test_rows = PointerRows(load_split(args.data, "test"), args.include_reads)
    if args.limit:
        train_rows.items = train_rows.items[:args.limit]
    if not train_rows:
        print("no training rows; build data/entity_pointer first")
        return 2
    print("rows train %d val %d test %d  hidden %d layers %d  reads %s  device %s"
          % (len(train_rows), len(val_rows), len(test_rows), args.hidden,
             args.layers, args.include_reads, device))
    train_loader = DataLoader(train_rows, batch_size=args.batch, shuffle=True,
                              collate_fn=collate)
    val_loader = DataLoader(val_rows, batch_size=args.batch, shuffle=False,
                            collate_fn=collate)
    test_loader = DataLoader(test_rows, batch_size=args.batch, shuffle=False,
                             collate_fn=collate)
    model = EntityPointer(hidden=args.hidden, layers=args.layers).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    pointer_loss = nn.CrossEntropyLoss()
    outcome_loss = nn.BCEWithLogitsLoss()
    history = []
    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss = 0.0
        for batch in train_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            optimizer.zero_grad()
            scores, moved, progress = model(batch)
            loss = pointer_loss(scores, batch["labels"])
            loss = loss + 0.5 * outcome_loss(moved, batch["moved"])
            loss = loss + 0.5 * outcome_loss(progress, batch["progress"])
            loss.backward()
            optimizer.step()
            total_loss += float(loss.item())
        row = {"epoch": epoch, "loss": total_loss / max(1, len(train_loader))}
        if val_rows:
            row["val"] = evaluate(model, val_loader)
        history.append(row)
        print(json.dumps(row, sort_keys=True))
    metrics = {"train_rows": len(train_rows), "val_rows": len(val_rows),
               "test_rows": len(test_rows), "history": history,
               "config": {"hidden": args.hidden, "layers": args.layers,
                          "include_reads": args.include_reads,
                          "vocab": VOCAB, "max_tokens": MAX_TOKENS,
                          "max_candidates": MAX_CANDIDATES}}
    if test_rows:
        metrics["test"] = evaluate(model, test_loader)
    args.out.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "config": metrics["config"]},
               args.out / "model.pt")
    (args.out / "metrics.json").write_text(json.dumps(metrics, indent=1, sort_keys=True),
                                           encoding="utf-8")
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
