"""Fine-tune the THX-01 decision head on Dofus tool-use data.

The encoder is frozen. Only the task head (the transformer head, the type
embedding and the marker scorer) is trained. That is deliberate:

* it adapts the *decision skill* (which tool, which argument shape) without
  rewriting the multilingual language model;
* it is cheap enough to run on CPU;
* entity-swapped examples force the head to use intent and sentence structure
  rather than memorising item/NPC names.

Usage (from ``dofus-network``)::

    python ai/thx_finetune.py --output models/finetuned/thx01-dofus --epochs 12
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn.functional as F
from safetensors.torch import save_file

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.thx_policy import (  # noqa: E402
    TOOL_CRITERIA,
    _default_model_path,
    _find_entity,
)
from thx01.common import build_sequence, collate_items  # noqa: E402

TOOL_NAMES = list(TOOL_CRITERIA)
TOOL_INDEX = {name: i for i, name in enumerate(TOOL_NAMES)}
TOOL_QUESTION = {
    "type": "choice",
    "instructions": "Which tool should be called to carry out the goal in `state`?",
    "criteria": TOOL_CRITERIA,
}

FAKE_NAMES = {
    "item": ["Zorvath Wool", "Vexium Ore", "Quorlight Shard", "Blafiber", "Torvane Essence"],
    "monster": ["Zorvath", "Vexium", "Quorlight", "Blafang", "Torvane"],
    "npc": ["Zorvath the Merchant", "Vexium the Guard", "Quorlight the Guide",
            "Blafang the Elder", "Torvane the Scout"],
    "area": ["Zorvath Island", "Vexium Forest", "Quorlight Desert", "Blafang Valley",
             "Torvane Coast"],
    "dungeon": ["Zorvath Dungeon", "Vexium Lair", "Quorlight Crypt", "Blafang Sanctum",
                "Torvane Ruin"],
}


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _sft_rows(path: Path) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for row in _read_jsonl(path):
        messages = row.get("messages") or []
        if len(messages) < 2:
            continue
        goal = messages[1].get("content") or ""
        if goal.startswith("Goal: "):
            goal = goal[6:]
        try:
            target = json.loads(messages[-1].get("content") or "{}")
        except json.JSONDecodeError:
            continue
        tool = target.get("tool")
        if tool in TOOL_INDEX and goal.strip():
            rows.append((goal.strip(), tool))
    return rows


def _trajectory_rows(path: Path) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for row in _read_jsonl(path):
        goal = (row.get("goal") or "").strip()
        tool = row.get("tool")
        if tool in TOOL_INDEX and goal:
            rows.append((goal, tool))
    return rows


def _collect_training_data(sft: Path, synthetic: Path,
                           distill: Optional[Path] = None) -> Tuple[List[Tuple[str, str]],
                                                                    List[Tuple[str, str]]]:
    rows = _sft_rows(sft) + _trajectory_rows(synthetic)
    if distill is not None:
        rows += _trajectory_rows(distill)
    seen = set()
    unique: List[Tuple[str, str]] = []
    for goal, tool in rows:
        key = (re.sub(r"\s+", " ", goal).strip().casefold(), tool)
        if key in seen:
            continue
        seen.add(key)
        unique.append((goal, tool))
    # Hold out every tenth synthetic row for validation; the SFT val file is
    # also used, so early stopping sees both real and generative phrasing.
    train: List[Tuple[str, str]] = []
    val: List[Tuple[str, str]] = []
    for goal, tool in unique:
        digest = hashlib.sha1(goal.encode("utf-8")).digest()[0]
        (val if digest % 10 == 0 else train).append((goal, tool))
    return train, val


def _entity_kind(goal: str) -> Optional[Tuple[str, str]]:
    for kind in ("monster", "item", "npc", "area", "dungeon"):
        found = _find_entity(goal, kind)
        if found:
            return kind, found
    return None


def _swap_entity(goal: str, kind: str, found: str, index: int) -> Optional[str]:
    fake = FAKE_NAMES[kind][index % len(FAKE_NAMES[kind])]
    pattern = re.compile(r"(?<!\w)" + re.escape(found) + r"(?!\w)", re.IGNORECASE)
    if not pattern.search(goal):
        return None
    swapped = pattern.sub(fake, goal, count=1)
    return swapped if swapped != goal else None


def _augment(rows: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    augmented = list(rows)
    for i, (goal, tool) in enumerate(rows):
        found = _entity_kind(goal)
        if not found:
            continue
        kind, name = found
        variant = _swap_entity(goal, kind, name, i)
        if variant:
            augmented.append((variant, tool))
    return augmented


def _build_items(tok, goals: Sequence[str], tools: Sequence[str], question: Dict[str, Any],
                 max_len: int, head_max_len: int) -> List[Dict[str, Any]]:
    items = []
    for goal, tool in zip(goals, tools):
        seq, markers = build_sequence(tok, goal, question, max_len, head_max_len)
        if not markers:
            continue
        items.append({
            "ids": seq,
            "markers": markers,
            "qtype": 0,
            "target": TOOL_INDEX[tool],
        })
    return items


@torch.no_grad()
def _cache_encoder(model, items: List[Dict[str, Any]], pad_id: int,
                   batch_size: int = 16) -> List[Dict[str, Any]]:
    cached = []
    order = sorted(range(len(items)), key=lambda i: len(items[i]["ids"]))
    for start in range(0, len(order), batch_size):
        idx = order[start:start + batch_size]
        batch_items = [{k: v for k, v in items[i].items() if k != "target"} for i in idx]
        batch = collate_items([batch_items], pad_id)
        device = next(model.parameters()).device
        batch = {key: value.to(device) if torch.is_tensor(value) else value
                 for key, value in batch.items()}
        h = model.encoder(
            input_ids=batch["input_ids"],
            attention_mask=batch["attention_mask"],
        ).last_hidden_state
        for j, i in enumerate(idx):
            length = int(batch["attention_mask"][j].sum())
            cached.append({
                "h": h[j, :length].detach().to(torch.float16).cpu(),
                "mask": batch["attention_mask"][j, :length].bool().cpu(),
                "marker_pos": batch["marker_pos"][j][batch["marker_mask"][j]].cpu(),
                "target": items[i]["target"],
            })
    return cached


def _collate_cached(cached: List[Dict[str, Any]], device: torch.device):
    max_len = max(item["h"].size(0) for item in cached)
    k = max(item["marker_pos"].numel() for item in cached)
    b = len(cached)
    h = torch.zeros((b, max_len, cached[0]["h"].size(1)), dtype=torch.float32)
    mask = torch.zeros((b, max_len), dtype=torch.bool)
    pos = torch.zeros((b, k), dtype=torch.long)
    marker_mask = torch.zeros((b, k), dtype=torch.bool)
    target = torch.zeros(b, dtype=torch.long)
    for i, item in enumerate(cached):
        n = item["h"].size(0)
        h[i, :n] = item["h"].float()
        mask[i, :n] = item["mask"].bool()
        m = item["marker_pos"].numel()
        pos[i, :m] = item["marker_pos"]
        marker_mask[i, :m] = True
        target[i] = item["target"]
    return h.to(device), mask.to(device), pos.to(device), marker_mask.to(device), target.to(device)


def _head_logits(model, cached: List[Dict[str, Any]], device: torch.device) -> torch.Tensor:
    h, mask, pos, marker_mask, _ = _collate_cached(cached, device)
    h = h + model.type_emb(torch.zeros(h.size(0), dtype=torch.long, device=device))[:, None, :]
    pad = ~mask
    if model.head is not None:
        for layer in model.head.layers:
            h = layer(h, src_key_padding_mask=pad)
    idx = pos.clamp(min=0)[:, :, None].expand(-1, -1, h.size(-1))
    m = torch.gather(h, 1, idx)
    logits = model.scorer(m).squeeze(-1)
    return logits.masked_fill(~marker_mask, -1e4)


@torch.no_grad()
def _accuracy(model, cached: List[Dict[str, Any]], device: torch.device,
              batch_size: int = 32) -> Tuple[float, float]:
    if not cached:
        return 0.0, 0.0
    model.eval()
    logits_parts = []
    targets_parts = []
    for start in range(0, len(cached), batch_size):
        batch = cached[start:start + batch_size]
        logits_parts.append(_head_logits(model, batch, device).cpu())
        targets_parts.extend(c["target"] for c in batch)
    logits = torch.cat(logits_parts, dim=0)
    targets = torch.tensor(targets_parts, dtype=torch.long)
    loss = F.cross_entropy(logits, targets).item()
    acc = (logits.argmax(-1) == targets).float().mean().item()
    return acc, loss


def _fit_temperature(model, cached: List[Dict[str, Any]], device: torch.device,
                     batch_size: int = 32) -> float:
    if not cached:
        return 1.0
    model.eval()
    logits_parts = []
    targets_parts = []
    with torch.no_grad():
        for start in range(0, len(cached), batch_size):
            batch = cached[start:start + batch_size]
            logits_parts.append(_head_logits(model, batch, device).cpu())
            targets_parts.extend(c["target"] for c in batch)
    logits = torch.cat(logits_parts, dim=0)
    targets = torch.tensor(targets_parts, dtype=torch.long)
    best_t, best_nll = 1.0, float("inf")
    for t in np.linspace(0.5, 5.0, 91):
        nll = F.cross_entropy(logits / float(t), targets).item()
        if nll < best_nll:
            best_t, best_nll = float(t), nll
    return best_t


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _save_checkpoint(base: Path, output: Path, model, temperature: float,
                     meta: Dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    state = {k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()}
    save_file(state, str(output / "model.safetensors"))
    _copy_tree(base / "rl_agent_config.json", output / "rl_agent_config.json")
    _copy_tree(base / "config.json", output / "config.json")
    _copy_tree(base / "tokenizer", output / "tokenizer")
    _copy_tree(base / "encoder" / "config.json", output / "encoder" / "config.json")
    cfg_path = output / "rl_agent_config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        buckets = cfg.setdefault("temperature_by_options", {})
        buckets["choice:11+"] = float(temperature)
        cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "training_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fine-tune THX-01's decision head on Dofus data")
    parser.add_argument("--base-model", default=None)
    parser.add_argument("--sft", default=str(REPO_ROOT / "data" / "agent_sft" / "train.jsonl"))
    parser.add_argument("--sft-val", default=str(REPO_ROOT / "data" / "agent_sft" / "val.jsonl"))
    parser.add_argument("--synthetic", default=str(REPO_ROOT / "trajectories" / "synthetic.jsonl"))
    parser.add_argument("--distill", default=None,
                        help="extra goal/tool JSONL (core-skill teacher data)")
    parser.add_argument("--output", default=str(REPO_ROOT / "models" / "finetuned" / "thx01-dofus"))
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--no-augment", action="store_true")
    parser.add_argument("--max-train", type=int, default=0, help="smoke-test limit")
    parser.add_argument("--max-val", type=int, default=0, help="smoke-test limit")
    args = parser.parse_args(argv)

    base = Path(args.base_model or _default_model_path())
    if not (base / "rl_agent_config.json").exists():
        raise SystemExit(f"not a THX-01 checkpoint: {base}")

    import thx01
    device = torch.device(args.device)
    print(f"loading {base} on {device}", flush=True)
    agent = thx01.load(str(base), device=str(device))
    model, tok = agent.model, agent.tok
    model.float()
    for param in model.encoder.parameters():
        param.requires_grad = False
    model.encoder.eval()

    train_rows, synth_val = _collect_training_data(
        Path(args.sft), Path(args.synthetic),
        Path(args.distill) if args.distill else None,
    )
    train_rows = _augment(train_rows) if not args.no_augment else train_rows
    val_rows = _sft_rows(Path(args.sft_val)) + synth_val
    if not train_rows or not val_rows:
        raise SystemExit("no training/validation rows found")
    if args.max_train:
        train_rows = train_rows[: args.max_train]
    if args.max_val:
        val_rows = val_rows[: args.max_val]
    counts = Counter(tool for _, tool in train_rows)
    print(f"train {len(train_rows)} / val {len(val_rows)}  tools={dict(sorted(counts.items()))}",
          flush=True)

    max_len = int(agent.cfg.get("max_len", 512))
    internal_question = agent._to_internal(TOOL_QUESTION)
    head_max_len = agent._head_budget(internal_question)
    train_items = _build_items(tok, [g for g, _ in train_rows], [t for _, t in train_rows],
                               internal_question, max_len, head_max_len)
    val_items = _build_items(tok, [g for g, _ in val_rows], [t for _, t in val_rows],
                             internal_question, max_len, head_max_len)
    print(f"encoding {len(train_items)} train / {len(val_items)} val states (frozen encoder)",
          flush=True)
    started = time.perf_counter()
    train_cached = _cache_encoder(model, train_items, tok.pad_token_id, batch_size=16)
    val_cached = _cache_encoder(model, val_items, tok.pad_token_id, batch_size=16)
    print(f"encoded in {time.perf_counter() - started:.1f}s", flush=True)

    params = (list(model.head.parameters()) + list(model.type_emb.parameters())
              + list(model.scorer.parameters()))
    optimizer = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.01)
    total = len(train_cached)
    weights = torch.tensor(
        [(total / (len(counts) * max(1, counts[tool]))) ** 0.5 for tool in TOOL_NAMES],
        dtype=torch.float32, device=device)
    weights = weights / weights.mean()
    rng = random.Random(2026)
    best_state = None
    best_acc = -1.0
    best_epoch = 0
    history = []
    for epoch in range(1, args.epochs + 1):
        model.head.train()
        model.type_emb.train()
        model.scorer.train()
        order = list(range(total))
        rng.shuffle(order)
        total_loss = 0.0
        steps = 0
        started = time.perf_counter()
        for start in range(0, total, args.batch_size):
            batch = [train_cached[i] for i in order[start:start + args.batch_size]]
            h, mask, pos, marker_mask, target = _collate_cached(batch, device)
            h = h + model.type_emb(torch.zeros(h.size(0), dtype=torch.long, device=device))[:, None, :]
            pad = ~mask
            if model.head is not None:
                for layer in model.head.layers:
                    h = layer(h, src_key_padding_mask=pad)
            idx = pos.clamp(min=0)[:, :, None].expand(-1, -1, h.size(-1))
            m = torch.gather(h, 1, idx)
            logits = model.scorer(m).squeeze(-1)
            logits = logits.masked_fill(~marker_mask, -1e4)
            loss = F.cross_entropy(logits, target, weight=weights, label_smoothing=0.05)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            optimizer.step()
            total_loss += loss.item()
            steps += 1
        acc, val_loss = _accuracy(model, val_cached, device)
        train_loss = total_loss / max(steps, 1)
        history.append({"epoch": epoch, "train_loss": train_loss,
                        "val_acc": acc, "val_loss": val_loss})
        print(f"epoch {epoch:02d}  train {train_loss:.4f}  val {acc:.3f}  "
              f"({time.perf_counter() - started:.1f}s)", flush=True)
        if acc > best_acc:
            best_acc = acc
            best_epoch = epoch
            best_state = {k: v.detach().cpu().clone()
                          for k, v in model.state_dict().items()
                          if k.startswith(("head.", "type_emb.", "scorer."))}

    if best_state:
        current = model.state_dict()
        current.update(best_state)
        model.load_state_dict(current)
    temperature = _fit_temperature(model, val_cached, device)
    meta = {
        "base_model": str(base),
        "train_rows": len(train_rows),
        "val_rows": len(val_rows),
        "best_epoch": best_epoch,
        "best_val_acc": best_acc,
        "temperature_choice_11plus": temperature,
        "frozen_encoder": True,
        "augmentation": not args.no_augment,
        "history": history,
    }
    _save_checkpoint(base, Path(args.output), model, temperature, meta)
    print(f"saved {args.output}  best_val_acc={best_acc:.3f}  best_epoch={best_epoch}  "
          f"temperature={temperature:.2f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
