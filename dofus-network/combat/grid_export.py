"""INT8 ONNX export for the 30M grid encoder.

The training checkpoint is a pickled JAX tree (``models/combat_grid/model.pkl``).
``combat.grid_model.forward`` is the definition of the architecture, in whatever
array backend exists. Neither ships: the embedded interpreter has no jax, and
eager float32 NumPy over 30M parameters is far outside a <10 ms turn budget.

This module writes the same computation as an ONNX graph with INT8 MatMul
weights, the format ``ai/onnx_policy.py`` already deploys, and checks it:

    python -m combat.grid_export --checkpoint models/combat_grid/model.pkl \
        --out models/combat_grid/grid_int8.onnx --selftest
    python -m combat.grid_export --random --hidden 256 --selftest   # no checkpoint

The pipeline is three files, mirroring the encoder export:

    grid_fp32.onnx   float32, exported from the checkpoint
    grid_int8.onnx   dynamic-quantized MatMul weights (the deployment file)
    grid_config.json active-cell budget, heads, param count, tolerances

``--selftest`` runs both graphs through onnxruntime, compares their plan ranking
against ``grid_model.forward`` on the same random board, and reports measured
latency. A graph that has not been compared to the reference is not exported
silently: ``--selftest`` is part of the documented command, and it exits non-zero
when the ranking disagrees.
"""

from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
from torch import nn
import logging
logger = logging.getLogger(__name__)

try:
    from combat import grid_model
except ImportError:  # Kaggle standalone
    import grid_model

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "models" / "combat_grid"
NEG = -1.0e9


def _params_to_torch(params: Any) -> Dict[str, torch.Tensor]:
    """Flatten the nested param tree to ``{"cell_blocks.0.qkv.w": tensor}``."""
    flat: Dict[str, torch.Tensor] = {}

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(prefix + "." + key if prefix else key, value)
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk("%s.%d" % (prefix, index), value)
        else:
            flat[prefix] = torch.as_tensor(np.asarray(node), dtype=torch.float32)

    walk("", params)
    return flat


class TorchGrid(nn.Module):
    """``grid_model.forward`` in torch, including the active-cell pruning.

    Every tensor is an ``nn.Parameter`` with ``requires_grad=False`` so the ONNX
    exporter writes it as an initializer and the quantizer can find the MatMul
    weights. The arithmetic is deliberately literal: the same ops, in the same
    order, as ``grid_model`` -- a clever fusion here is a parity bug later.
    """

    def __init__(self, params: Dict[str, torch.Tensor], heads: int,
                 active_cells: int) -> None:
        super().__init__()
        self.heads = int(heads)
        self.active_cells = int(active_cells)
        self.p = nn.ParameterDict({
            name.replace(".", "__"): nn.Parameter(tensor, requires_grad=False)
            for name, tensor in params.items()
        })

    def _w(self, name: str) -> torch.Tensor:
        return self.p[name.replace(".", "__")]

    def _has(self, name: str) -> bool:
        return name.replace(".", "__") in self.p

    def _dense(self, x: torch.Tensor, name: str) -> torch.Tensor:
        # `_dense` stores (weight, bias) as a tuple, so the flattened leaves are
        # `<name>.0` and `<name>.1` -- the same naming the JAX tree uses.
        return x @ self._w(name + ".0") + self._w(name + ".1")

    @staticmethod
    def _ln(x: torch.Tensor, gamma: torch.Tensor, beta: torch.Tensor) -> torch.Tensor:
        mean = x.mean(dim=-1, keepdim=True)
        var = ((x - mean) ** 2).mean(dim=-1, keepdim=True)
        return (x - mean) * torch.rsqrt(var + 1e-5) * gamma + beta

    def _attention(self, prefix: str, x: torch.Tensor,
                   mask: torch.Tensor) -> torch.Tensor:
        batch, tokens, width = x.shape
        qkv = self._dense(x, prefix + ".qkv").reshape(
            batch, tokens, 3, self.heads, width // self.heads)
        q, k, v = qkv[:, :, 0], qkv[:, :, 1], qkv[:, :, 2]
        q = q.transpose(1, 2)
        k = k.transpose(1, 2)
        v = v.transpose(1, 2)
        logits = torch.einsum("bhtd,bhsd->bhts", q, k) / float(
            width // self.heads) ** 0.5
        logits = torch.where(mask[:, None, None, :], logits,
                             torch.full_like(logits, NEG))
        probs = torch.softmax(logits, dim=-1)
        out = torch.einsum("bhts,bhsd->bhtd", probs, v)
        out = out.transpose(1, 2).reshape(batch, tokens, width)
        return self._dense(out, prefix + ".out")

    def _encode(self, prefix: str, x: torch.Tensor,
                mask: torch.Tensor) -> torch.Tensor:
        index = 0
        while self._has(prefix + ".%d.ln1.0" % index):
            block = "%s.%d" % (prefix, index)
            h = self._ln(x, self._w(block + ".ln1.0"), self._w(block + ".ln1.1"))
            x = x + self._attention(block, h, mask)
            h = self._ln(x, self._w(block + ".ln2.0"), self._w(block + ".ln2.1"))
            h = torch.relu(self._dense(h, block + ".mlp1"))
            x = x + self._dense(h, block + ".mlp2")
            index += 1
        return x

    @staticmethod
    def _pool(x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        weights = mask.unsqueeze(-1).to(x.dtype)
        return (x * weights).sum(dim=1) / torch.clamp(weights.sum(dim=1), min=1.0)

    def _prune(self, cells: torch.Tensor, cell_mask: torch.Tensor
               ) -> Tuple[torch.Tensor, torch.Tensor]:
        total = cells.shape[1]
        if self.active_cells <= 0 or self.active_cells >= total:
            return cells, cell_mask
        k = max(grid_model.MIN_ACTIVE_CELLS, self.active_cells)
        k = min(k, total)
        score = (2.0 * cells[..., 4].abs() + 1.5 * cells[..., 13]
                 + 1.0 * cells[..., 12] + 1.0 * cells[..., 3]
                 + 0.5 * cells[..., 2] + 0.5 * (1.0 - cells[..., 6]))
        score = score - torch.arange(total, dtype=cells.dtype) * 1e-6
        score = torch.where(cell_mask, score, torch.full_like(score, NEG))
        index = torch.topk(score, k, dim=1).indices
        gathered = torch.gather(
            cells, 1, index.unsqueeze(-1).expand(-1, -1, cells.shape[-1]))
        return gathered, torch.gather(cell_mask, 1, index)

    def forward(self, cells: torch.Tensor, cell_mask: torch.Tensor,
                fighters: torch.Tensor, fighter_mask: torch.Tensor,
                plans: torch.Tensor, plan_mask: torch.Tensor
                ) -> Tuple[torch.Tensor, torch.Tensor]:
        cells, cell_mask = self._prune(cells, cell_mask)
        cell = torch.relu(self._dense(cells, "cell_in")) + self._w("cell_type")
        cell = self._encode("cell_blocks", cell, cell_mask)
        grid = self._pool(cell, cell_mask)

        fighter = torch.relu(self._dense(fighters, "fighter_in")) + self._w("fighter_type")
        fighter = self._encode("fighter_blocks", fighter, fighter_mask)
        fighter_pool = self._pool(fighter, fighter_mask)

        plan = torch.relu(self._dense(plans, "plan_in")) + self._w("plan_type")
        plan = self._encode("plan_blocks", plan, plan_mask)

        query = torch.relu(self._dense(grid + fighter_pool, "grid_query"))
        query = query + torch.relu(self._dense(fighter_pool, "plan_query"))
        scores = (plan * query[:, None, :]).sum(-1) / float(plan.shape[-1]) ** 0.5
        scores = torch.where(plan_mask, scores, torch.full_like(scores, NEG))
        pooled = torch.relu(self._dense(torch.cat([grid, fighter_pool], dim=-1), "value1"))
        value = torch.tanh(self._dense(pooled, "value2"))[:, 0]
        return scores, value


def load_checkpoint(path: Path) -> Dict[str, Any]:
    with Path(path).open("rb") as handle:
        payload = pickle.load(handle)
    if "params" not in payload:
        raise SystemExit("%s has no 'params' entry; it is not a grid checkpoint" % path)
    return payload


def reference_inputs(batch: int = 1, cells: int = 560, seed: int = 3
                     ) -> Tuple[np.ndarray, ...]:
    """A board with a realistic number of interesting cells.

    Random noise everywhere would make pruning meaningless (every cell scores the
    same) and the parity check vacuous. This builds a walkable map with ~120
    reachable cells, two fighters and a full plan set.
    """
    rng = np.random.default_rng(seed)
    x = np.zeros((batch, cells, grid_model.CELL_FEATS), dtype=np.float32)
    walkable = rng.random((batch, cells)) < 0.28
    x[:, :, 2] = walkable
    x[:, :, 10] = rng.random((batch, cells)) < 0.8
    x[:, :, 11] = rng.random((batch, cells)) < 0.9
    x[:, :, 3] = (walkable & (rng.random((batch, cells)) < 0.55)).astype(np.float32)
    x[:, :, 13] = (walkable & (rng.random((batch, cells)) < 0.22)).astype(np.float32)
    x[:, :, 12] = (walkable & (rng.random((batch, cells)) < 0.15)).astype(np.float32)
    for b in range(batch):
        me = int(rng.integers(0, cells))
        enemy = int(rng.integers(0, cells))
        x[b, me, 2] = 1.0
        x[b, me, 3] = 1.0
        x[b, me, 5] = 1.0
        x[b, me, 13] = 1.0
        x[b, enemy, 4] = -1.0
        x[b, :, 6] = np.minimum(1.0, np.abs(np.arange(cells) - me) / 30.0)
    cell_mask = np.ones((batch, cells), dtype=bool)
    fighters = rng.normal(0, 0.3, (batch, grid_model.MAX_FIGHTERS,
                                   grid_model.FIGHTER_FEATS)).astype(np.float32)
    fighters[:, 0, 7] = 1.0
    fighters[:, 1, 17] = 1.0
    fighter_mask = np.zeros((batch, grid_model.MAX_FIGHTERS), dtype=bool)
    fighter_mask[:, :2] = True
    plans = rng.normal(0, 0.3, (batch, grid_model.MAX_PLANS,
                                grid_model.PLAN_FEATS)).astype(np.float32)
    plan_mask = np.zeros((batch, grid_model.MAX_PLANS), dtype=bool)
    plan_mask[:, :4] = True
    return x, cell_mask, fighters, fighter_mask, plans, plan_mask


def _export(model: TorchGrid, inputs: Sequence[np.ndarray], path: Path) -> Path:
    tensors = tuple(torch.from_numpy(np.ascontiguousarray(x)) for x in inputs)
    names = ["cells", "cell_mask", "fighters", "fighter_mask", "plans", "plan_mask"]
    dynamic = {name: {0: "batch"} for name in names}
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        input_names=names,
        output_names=["scores", "value"],
        dynamic_axes=dynamic,
        opset_version=17,
        do_constant_folding=True,
    )
    try:
        torch.onnx.export(model, tensors, str(path), dynamo=False, **kwargs)
    except TypeError:
        # Older/newer torch builds disagree about `dynamo`; the legacy exporter
        # is the one that writes plain ONNX without an extra dependency.
        torch.onnx.export(model, tensors, str(path), **kwargs)
    return path


def _session(path: Path) -> Any:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(path), sess_options=options,
                                providers=["CPUExecutionProvider"])


def _run(path: Path, inputs: Sequence[np.ndarray]) -> Tuple[np.ndarray, np.ndarray, float]:
    session = _session(path)
    feed = {name: np.ascontiguousarray(value) for name, value in zip(
        ["cells", "cell_mask", "fighters", "fighter_mask", "plans", "plan_mask"], inputs)}
    started = time.perf_counter()
    outputs = session.run(None, feed)
    elapsed = (time.perf_counter() - started) * 1000.0
    return np.asarray(outputs[0]), np.asarray(outputs[1]), elapsed


def quantize(source: Path, target: Path) -> Path:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    target.parent.mkdir(parents=True, exist_ok=True)
    quantize_dynamic(str(source), str(target), weight_type=QuantType.QInt8)
    return target


def build_param_dict(config: Dict[str, Any], seed: int = 0) -> Dict[str, Any]:
    """Random weights with the checkpoint's shape (the no-checkpoint path)."""
    import jax

    return grid_model.init_params(
        jax.random.PRNGKey(seed),
        hidden=int(config.get("hidden", grid_model.DEFAULT_HIDDEN)),
        cell_layers=int(config.get("cell_layers", grid_model.DEFAULT_CELL_LAYERS)),
        fighter_layers=int(config.get("fighter_layers", grid_model.DEFAULT_FIGHTER_LAYERS)),
        plan_layers=int(config.get("plan_layers", grid_model.DEFAULT_PLAN_LAYERS)),
        heads=int(config.get("heads", grid_model.DEFAULT_HEADS)),
    )


def export(checkpoint: Optional[Path], config: Dict[str, Any], out: Path,
           active_cells: int = grid_model.DEFAULT_ACTIVE_CELLS,
           seed: int = 0) -> Dict[str, Any]:
    if checkpoint is not None:
        payload = load_checkpoint(checkpoint)
        params = payload["params"]
        saved = payload.get("config") or {}
        for key in ("hidden", "cell_layers", "fighter_layers", "plan_layers"):
            if key in saved:
                config[key] = saved[key]
    else:
        params = build_param_dict(config, seed)
    heads = int(config.get("heads", grid_model.DEFAULT_HEADS))
    flat = _params_to_torch(params)
    model = TorchGrid(flat, heads, active_cells).eval()
    with torch.no_grad():
        model(torch.zeros(1, 560, grid_model.CELL_FEATS),
              torch.ones(1, 560, dtype=torch.bool),
              torch.zeros(1, grid_model.MAX_FIGHTERS, grid_model.FIGHTER_FEATS),
              torch.ones(1, grid_model.MAX_FIGHTERS, dtype=torch.bool),
              torch.zeros(1, grid_model.MAX_PLANS, grid_model.PLAN_FEATS),
              torch.ones(1, grid_model.MAX_PLANS, dtype=torch.bool))
    inputs = reference_inputs(batch=2)
    fp32 = _export(model, inputs, out.with_name(out.stem.replace("_int8", "") + "_fp32.onnx"))
    int8 = quantize(fp32, out)
    metadata = {
        "active_cells": int(active_cells),
        "heads": heads,
        "hidden": int(config.get("hidden", grid_model.DEFAULT_HIDDEN)),
        "cell_layers": int(config.get("cell_layers", grid_model.DEFAULT_CELL_LAYERS)),
        "params": grid_model.param_count(params),
        "fp32": fp32.name,
        "int8": int8.name,
        "fp32_mb": round(fp32.stat().st_size / 1e6, 2),
        "int8_mb": round(int8.stat().st_size / 1e6, 2),
    }
    out.with_name("grid_config.json").write_text(
        json.dumps(metadata, indent=1, sort_keys=True), encoding="utf-8")
    return metadata


def selftest(checkpoint: Optional[Path], config: Dict[str, Any], out: Path,
             active_cells: int, seed: int = 0) -> int:
    with torch.no_grad():
        torch.manual_seed(seed)
    metadata = export(checkpoint, config, out, active_cells, seed)
    fp32_path = out.with_name(metadata["fp32"])
    int8_path = out.with_name(metadata["int8"])
    inputs = reference_inputs(batch=2, seed=11)
    import jax

    params = (load_checkpoint(checkpoint)["params"] if checkpoint is not None
              else build_param_dict(config, seed))
    reference = grid_model.forward(
        params, jax.numpy.asarray(inputs[0]), jax.numpy.asarray(inputs[1]),
        jax.numpy.asarray(inputs[2]), jax.numpy.asarray(inputs[3]),
        jax.numpy.asarray(inputs[4]), jax.numpy.asarray(inputs[5]),
        heads=int(config.get("heads", grid_model.DEFAULT_HEADS)),
        active_cells=active_cells)
    ref_scores = np.asarray(reference["scores"])
    ref_value = np.asarray(reference["value"])
    fp_scores, fp_value, fp_ms = _run(fp32_path, inputs)
    q_scores, q_value, q_ms = _run(int8_path, inputs)
    mask = inputs[5]
    ref_rank = np.where(mask, ref_scores, -np.inf).argmax(-1)
    fp_rank = np.where(mask, fp_scores, -np.inf).argmax(-1)
    q_rank = np.where(mask, q_scores, -np.inf).argmax(-1)
    report = {
        **metadata,
        "fp32_max_abs": float(np.max(np.abs(fp_scores - ref_scores))),
        "int8_max_abs": float(np.max(np.abs(q_scores - ref_scores))),
        "value_max_abs": float(np.max(np.abs(fq_value := fp_value - ref_value))),
        "fp32_rank_match": float(np.mean(fp_rank == ref_rank)),
        "int8_rank_match": float(np.mean(q_rank == ref_rank)),
        "fp32_ms": round(fp_ms, 2),
        "int8_ms": round(q_ms, 2),
    }
    logger.debug(json.dumps(report, indent=1, sort_keys=True))
    ok = (report["fp32_rank_match"] == 1.0
          and report["int8_rank_match"] == 1.0
          and report["fp32_max_abs"] < 2e-3
          and report["int8_max_abs"] < 5e-1)
    logger.debug("export selftest:", "PASS" if ok else "FAIL",
          "| int8 %.2f ms" % q_ms)
    return 0 if ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT / "grid_int8.onnx")
    parser.add_argument("--active-cells", type=int, default=grid_model.DEFAULT_ACTIVE_CELLS)
    parser.add_argument("--random", action="store_true",
                        help="export random weights with --hidden/--layers (no checkpoint)")
    parser.add_argument("--hidden", type=int, default=grid_model.DEFAULT_HIDDEN)
    parser.add_argument("--cell-layers", type=int, default=grid_model.DEFAULT_CELL_LAYERS)
    parser.add_argument("--fighter-layers", type=int, default=grid_model.DEFAULT_FIGHTER_LAYERS)
    parser.add_argument("--plan-layers", type=int, default=grid_model.DEFAULT_PLAN_LAYERS)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.checkpoint is None and not args.random:
        default = DEFAULT_OUT / "model.pkl"
        if default.is_file():
            args.checkpoint = default
        else:
            parser.error("no checkpoint at %s; pass --random for a shape-only export" % default)
    config = {"hidden": args.hidden, "cell_layers": args.cell_layers,
              "fighter_layers": args.fighter_layers, "plan_layers": args.plan_layers}
    if args.selftest:
        return selftest(args.checkpoint, config, args.out, args.active_cells, args.seed)
    metadata = export(args.checkpoint, config, args.out, args.active_cells, args.seed)
    logger.debug(json.dumps(metadata, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
