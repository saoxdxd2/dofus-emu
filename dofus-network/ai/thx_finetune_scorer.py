"""Fast THX-01 fine-tune: train only the marker scorer on frozen representations.

The encoder and the 2-layer task head stay frozen. Marker embeddings are cached
once, then the tiny scorer (LayerNorm -> Linear -> GELU -> Linear) is trained on
Dofus tool decisions. This is the cheap, knowledge-preserving variant of the
fine-tune: it learns the decision boundary without rewriting the language model.

Usage (from ``dofus-network``)::

    python ai/thx_finetune_scorer.py --encode-only
    python ai/thx_finetune_scorer.py --use-cache --epochs 30
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn.functional as F

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai.thx_finetune import (  # noqa: E402
    TOOL_NAMES,
    TOOL_QUESTION,
    _build_items,
    _collect_training_data,
    _save_checkpoint,
    _sft_rows,
)
from thx01.common import collate_items  # noqa: E402


@torch.no_grad()
def cache_markers(model, items: List[Dict[str, Any]], pad_id: int,
                  batch_size: int = 16) -> List[Dict[str, Any]]:
    model.eval()
    cached = []
    order = sorted(range(len(items)), key=lambda i: len(items[i]["ids"]))
    for start in range(0, len(order), batch_size):
        if start and start % (batch_size * 20) == 0:
            print(f"  markers {start}/{len(order)}", flush=True)
        idx = order[start:start + batch_size]
        batch_items = [{k: v for k, v in items[i].items() if k != "target"} for i in idx]
        batch = collate_items([batch_items], pad_id)
        h = model.encoder(
            input_ids=batch["input_ids"],
            attention_mask=batch["attention_mask"],
        ).last_hidden_state
        h = h + model.type_emb(batch["qtype"])[:, None, :]
        pad = ~batch["attention_mask"].bool()
        if model.head is not None:
            for layer in model.head.layers:
                h = layer(h, src_key_padding_mask=pad)
        idx_t = batch["marker_pos"].clamp(min=0)[:, :, None].expand(-1, -1, h.size(-1))
        markers = torch.gather(h, 1, idx_t)
        for j, i in enumerate(idx):
            k = int(batch["marker_mask"][j].sum())
            cached.append({
                "m": markers[j, :k].detach().to(torch.float16).cpu(),
                "target": items[i]["target"],
            })
    return cached


def _collate_markers(cached: List[Dict[str, Any]], device: torch.device):
    k = max(item["m"].size(0) for item in cached)
    dim = cached[0]["m"].size(1)
    b = len(cached)
    m = torch.zeros((b, k, dim), dtype=torch.float32)
    mask = torch.zeros((b, k), dtype=torch.bool)
    target = torch.zeros(b, dtype=torch.long)
    for i, item in enumerate(cached):
        n = item["m"].size(0)
        m[i, :n] = item["m"].float()
        mask[i, :n] = True
        target[i] = item["target"]
    return m.to(device), mask.to(device), target.to(device)


def _logits(model, cached: List[Dict[str, Any]], device: torch.device) -> torch.Tensor:
    m, mask, target = _collate_markers(cached, device)
    logits = model.scorer(m).squeeze(-1)
    return logits.masked_fill(~mask, -1e4), target


@torch.no_grad()
def evaluate(model, cached: List[Dict[str, Any]], device: torch.device) -> Tuple[float, float]:
    model.eval()
    logits, target = _logits(model, cached, device)
    loss = F.cross_entropy(logits, target).item()
    acc = (logits.argmax(-1) == target).float().mean().item()
    return acc, loss


@torch.no_grad()
def fit_temperature(model, cached: List[Dict[str, Any]], device: torch.device) -> float:
    logits, target = _logits(model, cached, device)
    best_t, best = 1.0, float("inf")
    for t in np.linspace(0.5, 5.0, 91):
        nll = F.cross_entropy(logits / float(t), target).item()
        if nll < best:
            best, best_t = nll, float(t)
    return best_t


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Train THX-01's scorer on frozen Dofus markers")
    parser.add_argument("--base-model", default=None)
    parser.add_argument("--output", default=str(REPO_ROOT / "models" / "finetuned" / "thx01-dofus"))
    parser.add_argument("--cache", default=str(REPO_ROOT / "models" / "cache" / "thx01_markers.pt"))
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--encode-only", action="store_true")
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--max-train", type=int, default=0)
    parser.add_argument("--max-val", type=int, default=0)
    args = parser.parse_args(argv)

    from ai.thx_policy import _default_model_path
    base = Path(args.base_model or _default_model_path())
    if not (base / "rl_agent_config.json").exists():
        raise SystemExit(f"not a THX-01 checkpoint: {base}")
    cache_path = Path(args.cache)
    device = torch.device(args.device)

    import thx01
    print(f"loading {base} on {device}", flush=True)
    agent = thx01.load(str(base), device=str(device))
    model, tok = agent.model, agent.tok
    for param in model.parameters():
        param.requires_grad = False
    for param in model.scorer.parameters():
        param.requires_grad = True
    model.eval()

    if args.use_cache and cache_path.exists():
        print(f"loading marker cache {cache_path}", flush=True)
        payload = torch.load(cache_path, map_location="cpu")
        train_cached, val_cached = payload["train"], payload["val"]
    else:
        train_rows, synth_val = _collect_training_data(
            REPO_ROOT / "data" / "agent_sft" / "train.jsonl",
            REPO_ROOT / "trajectories" / "synthetic.jsonl",
        )
        val_rows = _sft_rows(REPO_ROOT / "data" / "agent_sft" / "val.jsonl") + synth_val
        if args.max_train:
            train_rows = train_rows[: args.max_train]
        if args.max_val:
            val_rows = val_rows[: args.max_val]
        print(f"train {len(train_rows)} / val {len(val_rows)}", flush=True)
        internal_q = agent._to_internal(TOOL_QUESTION)
        head_max_len = agent._head_budget(internal_q)
        train_items = _build_items(tok, [g for g, _ in train_rows], [t for _, t in train_rows],
                                   internal_q, int(agent.cfg.get("max_len", 512)), head_max_len)
        val_items = _build_items(tok, [g for g, _ in val_rows], [t for _, t in val_rows],
                                 internal_q, int(agent.cfg.get("max_len", 512)), head_max_len)
        print(f"encoding {len(train_items)} train / {len(val_items)} val markers", flush=True)
        started = time.perf_counter()
        train_cached = cache_markers(model, train_items, tok.pad_token_id, batch_size=16)
        val_cached = cache_markers(model, val_items, tok.pad_token_id, batch_size=16)
        print(f"encoded in {time.perf_counter() - started:.1f}s", flush=True)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save({"train": train_cached, "val": val_cached}, cache_path)
        print(f"cache: {cache_path}", flush=True)
    if args.encode_only:
        return 0

    counts = Counter(TOOL_NAMES[item["target"]] for item in train_cached)
    total = len(train_cached)
    weights = torch.tensor(
        [(total / (len(counts) * max(1, counts[tool]))) ** 0.5 for tool in TOOL_NAMES],
        dtype=torch.float32, device=device)
    weights = weights / weights.mean()

    params = list(model.scorer.parameters())
    optimizer = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.05)
    rng = random.Random(2026)
    best_state = None
    best_acc = -1.0
    best_epoch = 0
    history = []
    for epoch in range(1, args.epochs + 1):
        model.scorer.train()
        order = list(range(total))
        rng.shuffle(order)
        total_loss = 0.0
        steps = 0
        for start in range(0, total, args.batch_size):
            batch = [train_cached[i] for i in order[start:start + args.batch_size]]
            m, mask, target = _collate_markers(batch, device)
            logits = model.scorer(m).squeeze(-1)
            logits = logits.masked_fill(~mask, -1e4)
            loss = F.cross_entropy(logits, target, weight=weights, label_smoothing=0.05)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            optimizer.step()
            total_loss += loss.item()
            steps += 1
        acc, val_loss = evaluate(model, val_cached, device)
        train_loss = total_loss / max(steps, 1)
        history.append({"epoch": epoch, "train_loss": train_loss,
                        "val_acc": acc, "val_loss": val_loss})
        if epoch == 1 or epoch % 5 == 0 or acc >= best_acc:
            print(f"epoch {epoch:02d}  train {train_loss:.4f}  val {acc:.3f}  "
                  f"val_loss {val_loss:.4f}", flush=True)
        if acc > best_acc:
            best_acc = acc
            best_epoch = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.scorer.state_dict().items()}

    if best_state:
        model.scorer.load_state_dict(best_state)
    temperature = fit_temperature(model, val_cached, device)
    meta = {
        "base_model": str(base),
        "method": "scorer-only on frozen encoder+head markers",
        "train_rows": len(train_cached),
        "val_rows": len(val_cached),
        "best_epoch": best_epoch,
        "best_val_acc": best_acc,
        "temperature_choice_11plus": temperature,
        "frozen_encoder": True,
        "frozen_head": True,
        "history": history,
    }
    _save_checkpoint(base, Path(args.output), model, temperature, meta)
    print(f"saved {args.output}  best_val_acc={best_acc:.3f}  best_epoch={best_epoch}  "
          f"temperature={temperature:.2f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
