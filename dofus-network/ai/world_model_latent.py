"""Offline trainer for the decoder-free simplicial latent world model.

The live runtime is `agent/latent_mind.py` and it is pure NumPy. This file may use
PyTorch because it never runs inside the shipped decision path: it trains the
encoder, the K-member dynamics ensemble, the reward/stall heads and the terminal
value head, then exports float32 arrays to `models/latent_world.npz`.

Shape contract (also written into the sidecar):

    encoder input   [x_t (42) | vec(M_t) (4 x 32 = 128) | z_slow (12)] = 182
    latent z        (Delta^7)^8  -> 64 numbers, eight 8-way softmax blocks
    dynamics input  [z (64) | one_hot(a, 28) | arg_summary (8)] = 100
    dynamics output next latent (8 x 8 softmax), reward two-hot (51), stall (1)

The slow state is the Gauss-Seidel closed form
``z_slow <- (z_slow + Pi * mu(x)) / (1 + Pi)`` with a fixed positive precision;
the CfC prior at runtime decays it over the observed elapsed time. The memory is
a four-slot ALER tensor written by a straight-through Gumbel-Softmax slot choice,
so training sees the same stateful input the runtime will.

Data: `data/transitions_temporal/{train,val}.jsonl`. A transition is two rows in
the same segment with adjacent observation vectors. Reward is CACA's terminal
signal, mapped to a bounded scalar (progress 2.0, moved 1.0, else 0.0); the
two-hot head learns symlog(reward) so the distribution is not dominated by zeros.

    .venv/Scripts/python.exe ai/world_model_latent.py --data data/transitions_temporal \
        --out models/latent_world.npz --epochs 12

Then:  python -m unittest agent.test_latent_mind
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LATENT_BLOCKS = 8
LATENT_DIM = 64
BLOCK_WIDTH = LATENT_DIM // LATENT_BLOCKS  # 8
MEMORY_SLOTS = 4
MEMORY_WIDTH = 32
MEMORY_DIM = MEMORY_SLOTS * MEMORY_WIDTH
SLOW_DIM = 12
ACTION_DIM = 28
ARG_DIM = 8
REWARD_BINS = 51
SYMLOG_SCALE = 4.0
FEATURE_DIM = 42
HORIZON = 5
DISCOUNT = 0.9
ENSEMBLE = 5
SLOW_PRECISION = 0.2


def _torch():
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - offline guard
        raise SystemExit("torch is required for offline training: %s" % exc)
    return torch


# -- data --------------------------------------------------------------------

@dataclass
class Step:
    segment: str
    t: int
    x: np.ndarray
    tool: str
    reward: float
    stall: float
    progress: float
    args: Dict[str, Any] = None  # type: ignore[assignment]


def load_steps(path: Path) -> List[Step]:
    steps: List[Step] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            x = row.get("x")
            if not isinstance(x, list) or len(x) != FEATURE_DIM:
                continue
            progress = 1.0 if row.get("progress") else 0.0
            moved = 1.0 if row.get("moved") else 0.0
            reward = 2.0 * progress + moved
            steps.append(Step(
                segment=str(row.get("segment") or ""),
                t=int(row.get("t") or 0),
                x=np.asarray(x, dtype="float32"),
                tool=str(row.get("tool") or ""),
                reward=reward,
                stall=0.0 if moved else 1.0,
                progress=progress,
                args=row.get("args") if isinstance(row.get("args"), dict) else {},
            ))
    return steps


def sequences(steps: Sequence[Step]) -> List[List[Step]]:
    """Consecutive steps inside one segment, ordered by t."""
    by_segment: Dict[str, List[Step]] = {}
    for step in steps:
        if not step.segment:
            continue
        by_segment.setdefault(step.segment, []).append(step)
    out: List[List[Step]] = []
    for rows in by_segment.values():
        rows.sort(key=lambda s: s.t)
        out.append(rows)
    return out


def arg_summary(args: Any) -> np.ndarray:
    """Eight numbers describing the call's arguments, shared with the runtime."""
    out = np.zeros(ARG_DIM, dtype="float32")
    if not isinstance(args, dict):
        return out
    for index, key in enumerate(("map_id", "item_id", "npc_id", "monster_id",
                                 "quest_id", "cell", "x", "y")):
        value = args.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        out[index] = math.copysign(math.log1p(abs(number)) / 12.0, number)
    out = np.clip(out, -1.0, 1.0)
    if args.get("direction"):
        out[6] = 0.5
    return out


def tool_onehot(tool: str, tools: Sequence[str]) -> np.ndarray:
    out = np.zeros(ACTION_DIM, dtype="float32")
    if tool in tools:
        out[tools.index(tool)] = 1.0
    return out


def two_hot(value: float, bins: int = REWARD_BINS,
            scale: float = SYMLOG_SCALE) -> np.ndarray:
    out = np.zeros(bins, dtype="float32")
    y = math.copysign(math.log1p(abs(value)), value)
    y = max(-scale, min(scale, y))
    position = (y + scale) / (2 * scale) * (bins - 1)
    low = int(math.floor(position))
    high = min(bins - 1, low + 1)
    frac = position - low
    out[low] += 1.0 - frac
    out[high] += frac
    return out


def two_hot_decode(probs: np.ndarray, scale: float = SYMLOG_SCALE) -> float:
    bins = probs.shape[-1]
    centers = np.linspace(-scale, scale, bins)
    value = float(np.sum(probs * centers, axis=-1))
    return float(math.copysign(math.expm1(abs(value)), value))


# -- model -------------------------------------------------------------------

def _mlp(torch, fan_in: int, hidden: int, fan_out: int):
    nn = torch.nn
    return nn.Sequential(nn.Linear(fan_in, hidden), nn.GELU(),
                         nn.Linear(hidden, hidden), nn.GELU(),
                         nn.Linear(hidden, fan_out))


def simnorm(pre, torch):
    shaped = pre.reshape(*pre.shape[:-1], LATENT_BLOCKS, BLOCK_WIDTH)
    return torch.softmax(shaped, dim=-1).reshape(*pre.shape)


@dataclass
class Config:
    hidden: int = 128
    ensemble: int = ENSEMBLE
    memory_slots: int = MEMORY_SLOTS
    memory_width: int = MEMORY_WIDTH
    slow_dim: int = SLOW_DIM


class LatentWorld:
    """The trainable module factory: importing this file stays torch-free."""

    @staticmethod
    def build(cfg: Config = Config()):
        torch = _torch()
        nn = torch.nn

        class _LatentWorld(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.cfg = cfg
                enc_in = FEATURE_DIM + cfg.memory_slots * cfg.memory_width + cfg.slow_dim
                self.encoder = _mlp(torch, enc_in, cfg.hidden, LATENT_DIM)
                self.mu = nn.Linear(FEATURE_DIM, cfg.slow_dim)
                self.memory_key = nn.Linear(LATENT_DIM, cfg.memory_slots)
                self.memory_value = nn.Linear(LATENT_DIM, cfg.memory_width)
                self.memory_gate = nn.Linear(LATENT_DIM, 1)
                dyn_in = LATENT_DIM + ACTION_DIM + ARG_DIM
                self.dynamics = nn.ModuleList([
                    _mlp(torch, dyn_in, cfg.hidden, LATENT_DIM + REWARD_BINS + 1)
                    for _ in range(cfg.ensemble)])
                self.value = _mlp(torch, LATENT_DIM, cfg.hidden // 2, 1)
                self.prior_log_rate = nn.Parameter(torch.zeros(cfg.slow_dim))

            # -- state pieces ------------------------------------------------
            def init_state(self, batch: int, device):
                memory = torch.zeros(batch, cfg.memory_slots, cfg.memory_width,
                                     device=device)
                slow = torch.zeros(batch, cfg.slow_dim, device=device)
                return memory, slow

            def encode(self, x, memory, slow):
                return simnorm(self.encoder(
                    torch.cat([x, memory.reshape(x.shape[0], -1), slow], dim=-1)),
                    torch)

            def update_slow(self, slow, x):
                mu = torch.tanh(self.mu(x))
                return (slow + SLOW_PRECISION * mu) / (1.0 + SLOW_PRECISION)

            def update_memory(self, memory, z, hard: bool = False):
                logits = self.memory_key(z)
                if hard:
                    slot = torch.argmax(logits, dim=-1)
                    onehot = torch.nn.functional.one_hot(
                        slot, cfg.memory_slots).to(z.dtype)
                else:
                    onehot = torch.nn.functional.gumbel_softmax(
                        logits, tau=1.0, hard=True, dim=-1)
                value = torch.tanh(self.memory_value(z))
                gate = torch.sigmoid(self.memory_gate(z))
                written = memory * (1.0 - onehot.unsqueeze(-1)) \
                    + onehot.unsqueeze(-1) * value.unsqueeze(1)
                return memory + gate.unsqueeze(-1) * (written - memory)

            def dyn_input(self, z, action, args):
                return torch.cat([z, action, args], dim=-1)

            def predict(self, z, action, args):
                """All ensemble members: (latent mean [K,B,64], reward probs, stall)."""
                out = [m(self.dyn_input(z, action, args)) for m in self.dynamics]
                stacked = torch.stack(out, dim=0)
                latents = stacked[..., :LATENT_DIM]
                reward_logits = stacked[..., LATENT_DIM:LATENT_DIM + REWARD_BINS]
                stall_logits = stacked[..., -1]
                latents = simnorm(latents, torch)
                return latents, reward_logits, stall_logits

            def terminal_value(self, z):
                return torch.sigmoid(self.value(z)).squeeze(-1)

        return _LatentWorld()


def export(model, path: Path, cfg: Config, tools: Sequence[str],
           stats: Dict[str, Any]) -> None:
    torch = _torch()
    payload: Dict[str, np.ndarray] = {}
    for name, tensor in model.state_dict().items():
        payload["w/" + name] = tensor.detach().cpu().numpy().astype("float32")
    payload["meta/tools"] = np.asarray(list(tools))
    payload["meta/config"] = np.asarray(json.dumps({
        "hidden": cfg.hidden, "ensemble": cfg.ensemble,
        "memory_slots": cfg.memory_slots, "memory_width": cfg.memory_width,
        "slow_dim": cfg.slow_dim, "latent_dim": LATENT_DIM,
        "blocks": LATENT_BLOCKS, "block_width": BLOCK_WIDTH,
        "reward_bins": REWARD_BINS, "arg_dim": ARG_DIM,
        "feature_dim": FEATURE_DIM, "horizon": HORIZON,
        "slow_precision": SLOW_PRECISION, "symlog_scale": SYMLOG_SCALE,
    }))
    for key, value in stats.items():
        payload["stat/" + key] = np.asarray(value)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **payload)


# -- evaluation --------------------------------------------------------------

def _auc(scores: np.ndarray, labels: np.ndarray) -> float:
    pos = scores[labels > 0.5]
    neg = scores[labels <= 0.5]
    if not pos.size or not neg.size:
        return 0.0
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(order.size, dtype="float64")
    ranks[order] = np.arange(1, order.size + 1)
    return float((ranks[:pos.size].sum() - pos.size * (pos.size + 1) / 2)
                 / (pos.size * neg.size))


def make_windows(seqs: Sequence[Sequence[Step]], horizon: int = HORIZON):
    windows = []
    for rows in seqs:
        for start in range(0, len(rows) - horizon):
            windows.append(rows[start:start + horizon + 1])
    return windows


def evaluate(model, val_path: Path, tools: Sequence[str], device) -> Dict[str, Any]:
    torch = _torch()
    model.eval()
    steps = load_steps(val_path)
    seqs = sequences(steps)
    windows = make_windows(seqs)
    if not windows:
        return {"windows": 0}
    cosines: List[float] = []
    rewards_pred: List[float] = []
    rewards_true: List[float] = []
    stall_pred: List[float] = []
    stall_true: List[float] = []
    value_pred: List[float] = []
    value_true: List[float] = []
    with torch.no_grad():
        for start in range(0, len(windows), 64):
            batch = windows[start:start + 64]
            x = torch.as_tensor(np.stack([w[0].x for w in batch]), device=device)
            memory, slow = model.init_state(len(batch), device)
            z = model.encode(x, memory, slow)
            z0 = z.clone()
            for step_index in range(HORIZON):
                actions = torch.as_tensor(np.stack([
                    tool_onehot(w[step_index].tool, tools) for w in batch]),
                    device=device)
                args = torch.as_tensor(np.stack([
                    arg_summary(w[step_index].args if hasattr(w, 'args') else {}) for w in batch]), device=device)
                latents, reward_logits, stall_logits = model.predict(z, actions, args)
                z = latents.mean(dim=0)
                probs = torch.softmax(reward_logits.mean(dim=0), dim=-1).cpu().numpy()
                rewards_pred.extend([two_hot_decode(row) for row in probs])
                rewards_true.extend([w[step_index + 1].reward for w in batch])
                stall_pred.extend(torch.sigmoid(stall_logits.mean(dim=0)).cpu().tolist())
                stall_true.extend([w[step_index + 1].stall for w in batch])
                slow = model.update_slow(slow, x)
                memory = model.update_memory(memory, z, hard=True)
                next_x = torch.as_tensor(np.stack([
                    w[step_index + 1].x for w in batch]), device=device)
                x = next_x
            target_x = torch.as_tensor(np.stack([w[HORIZON].x for w in batch]),
                                       device=device)
            target = model.encode(target_x, memory, slow)
            cosine = torch.nn.functional.cosine_similarity(z, target, dim=-1)
            cosines.extend(cosine.cpu().tolist())
            value_pred.extend(model.terminal_value(z0).cpu().tolist())
            value_true.extend([max(w[i].progress for i in range(1, HORIZON + 1))
                               for w in batch])
    return {
        "windows": len(windows),
        "cosine_5": round(float(np.mean(cosines)), 4),
        "reward_auc": round(_auc(np.asarray(rewards_pred), np.asarray(rewards_true)), 4),
        "reward_rmse": round(float(np.sqrt(np.mean(
            (np.asarray(rewards_pred) - np.asarray(rewards_true)) ** 2))), 4),
        "stall_auc": round(_auc(np.asarray(stall_pred), np.asarray(stall_true)), 4),
        "value_auc": round(_auc(np.asarray(value_pred), np.asarray(value_true)), 4),
        "reward_base_rate": round(float(np.mean(np.asarray(rewards_true) > 0)), 4),
    }


# -- training ----------------------------------------------------------------

def train(data: Path, out: Path, epochs: int = 12, batch: int = 64,
          hidden: int = 128, seed: int = 0, device_name: str = "cpu",
          verbose: bool = True) -> Dict[str, Any]:
    torch = _torch()
    from agent.features import tool_registry

    tools = tool_registry()
    cfg = Config(hidden=hidden)
    device = torch.device(device_name)
    torch.manual_seed(seed)
    np.random.seed(seed)

    train_steps = load_steps(Path(data) / "train.jsonl")
    windows = make_windows(sequences(train_steps))
    if not windows:
        raise SystemExit("no training windows in %s" % data)
    model = LatentWorld.build(cfg).to(device)
    ema = LatentWorld.build(cfg).to(device)
    ema.load_state_dict(model.state_dict())
    for parameter in ema.parameters():
        parameter.requires_grad_(False)
    optimizer = torch.optim.Adam(model.parameters(), lr=3e-4)
    log = []
    for epoch in range(1, epochs + 1):
        model.train()
        rng = np.random.default_rng(seed + epoch)
        order = rng.permutation(len(windows))
        totals = {"latent": 0.0, "reward": 0.0, "stall": 0.0, "value": 0.0}
        count = 0
        started = time.time()
        for offset in range(0, len(order), batch):
            batch_windows = [windows[i] for i in order[offset:offset + batch]]
            x = torch.as_tensor(np.stack([w[0].x for w in batch_windows]), device=device)
            memory, slow = model.init_state(len(batch_windows), device)
            z = model.encode(x, memory, slow)
            z0 = z
            loss_latent = torch.zeros((), device=device)
            loss_reward = torch.zeros((), device=device)
            loss_stall = torch.zeros((), device=device)
            for step_index in range(HORIZON):
                actions = torch.as_tensor(np.stack([
                    tool_onehot(w[step_index].tool, tools) for w in batch_windows]),
                    device=device)
                args = torch.as_tensor(np.stack([
                    arg_summary(w[step_index].args if hasattr(w, 'args') else {}) for w in batch_windows]), device=device)
                latents, reward_logits, stall_logits = model.predict(z, actions, args)
                next_x = torch.as_tensor(np.stack([
                    w[step_index + 1].x for w in batch_windows]), device=device)
                with torch.no_grad():
                    target = ema.encode(next_x, memory, slow)
                weight = DISCOUNT ** step_index
                loss_latent = loss_latent + weight * torch.mean(
                    (latents - target.unsqueeze(0)) ** 2)
                reward_target = torch.as_tensor(np.stack([
                    two_hot(w[step_index + 1].reward) for w in batch_windows]),
                    device=device)
                log_probs = torch.log_softmax(reward_logits, dim=-1)
                loss_reward = loss_reward + weight * (-(reward_target.unsqueeze(0)
                                                        * log_probs).sum(-1).mean())
                stall_target = torch.as_tensor(np.asarray([
                    w[step_index + 1].stall for w in batch_windows], dtype="float32"),
                    device=device)
                loss_stall = loss_stall + weight * torch.nn.functional.binary_cross_entropy_with_logits(
                    stall_logits, stall_target.unsqueeze(0).expand_as(stall_logits))
                z = latents.mean(dim=0)
                slow = model.update_slow(slow, x)
                memory = model.update_memory(memory, z)
                x = next_x
            progress_target = torch.as_tensor(np.asarray([
                max(w[i].progress for i in range(1, HORIZON + 1))
                for w in batch_windows], dtype="float32"), device=device)
            loss_value = torch.nn.functional.binary_cross_entropy(
                model.terminal_value(z0), progress_target)
            loss = loss_latent + 0.5 * loss_reward + 0.2 * loss_stall + 0.1 * loss_value
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            # EMA target encoder (tau = 0.01) in parameter space.
            with torch.no_grad():
                for target_parameter, parameter in zip(ema.parameters(),
                                                       model.parameters()):
                    target_parameter.mul_(0.99).add_(parameter.detach(), alpha=0.01)
            totals["latent"] += float(loss_latent)
            totals["reward"] += float(loss_reward)
            totals["stall"] += float(loss_stall)
            totals["value"] += float(loss_value)
            count += 1
        metrics = evaluate(model, Path(data) / "val.jsonl", tools, device)
        row = {"epoch": epoch,
               "loss": {key: round(value / max(1, count), 5) for key, value in totals.items()},
               "val": metrics,
               "seconds": round(time.time() - started, 1)}
        log.append(row)
        if verbose:
            print(json.dumps(row), flush=True)
    # Surprise calibration on the training split's one-step residuals.
    stats = surprise_calibration(model, windows, tools, device)
    stats["val"] = evaluate(model, Path(data) / "val.jsonl", tools, device)
    stats["train_windows"] = len(windows)
    export(model, out, cfg, tools, stats)
    report = {"config": cfg.__dict__, "epochs": log, "stats": stats,
              "artifact": str(out)}
    Path(str(out).replace(".npz", "_report.json")).write_text(
        json.dumps(report, indent=1), encoding="utf-8")
    return report


def surprise_calibration(model, windows, tools: Sequence[str], device) -> Dict[str, Any]:
    """Mean/std of one-step latent surprise on the training distribution."""
    torch = _torch()
    model.eval()
    surprises: List[float] = []
    with torch.no_grad():
        for start in range(0, len(windows), 128):
            batch = windows[start:start + 128]
            x = torch.as_tensor(np.stack([w[0].x for w in batch]), device=device)
            memory, slow = model.init_state(len(batch), device)
            z = model.encode(x, memory, slow)
            actions = torch.as_tensor(np.stack([
                tool_onehot(w[0].tool, tools) for w in batch]), device=device)
            args = torch.as_tensor(np.stack([arg_summary(w[0].args) for w in batch]),
                                   device=device)
            latents, _, _ = model.predict(z, actions, args)
            next_x = torch.as_tensor(np.stack([w[1].x for w in batch]), device=device)
            target = model.encode(next_x, memory, slow)
            residual = (target - latents.mean(dim=0)).cpu().numpy()
            surprises.extend(np.mean(residual ** 2, axis=1).tolist())
    values = np.asarray(surprises, dtype="float64")
    return {"surprise_mean": float(values.mean()),
            "surprise_std": float(values.std()),
            "surprise_p95": float(np.percentile(values, 95)),
            "surprise_samples": int(values.size)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(REPO_ROOT / "data" / "transitions_temporal"))
    parser.add_argument("--out", default=str(REPO_ROOT / "models" / "latent_world.npz"))
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args(argv)
    report = train(Path(args.data), Path(args.out), epochs=args.epochs,
                   batch=args.batch, hidden=args.hidden, seed=args.seed,
                   device_name=args.device)
    print(json.dumps(report["stats"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
