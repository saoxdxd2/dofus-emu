"""Train and export a Sweet Layer Handler (Context-Gated Dynamic Selection Router) for tool_policy.onnx.

Inspired by Qwen / FastForward predictive neuron & expert selection:
Instead of hardcoding heuristic if-checks, a learned context-aware gating router
dynamically modulates tool logits using the uncorrupted state representation.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import onnx
from onnx import numpy_helper
import torch
import torch.nn as nn
import onnxruntime as ort

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent.features import FEATURE_COUNT, HISTORY_STEPS, HISTORY_TOKEN
from agent.weight_space import (
    BASE_ONNX,
    BASE_SIDECAR,
    DEFAULT_VAL,
    MODELS_DIR,
    candidate_verdict,
    export_manifest,
    metrics_from_logits,
    paired_bootstrap,
    pin_provenance,
)
from tools.neuron_transplant import load_dataset


class DedicatedPolicyWithSweetLayer(nn.Module):
    def __init__(self, base_onnx_path: Path, router_hidden: int = 32, expert_hidden: int = 32):
        super().__init__()
        self.base = FEATURE_COUNT  # 42
        self.steps = HISTORY_STEPS # 8
        self.token = HISTORY_TOKEN # 28
        self.gru_dim = 64
        self.hidden = 128
        self.n_tools = 21

        # 1. Base architecture components
        self.state_fc0 = nn.Linear(self.base, self.hidden)
        self.state_fc3 = nn.Linear(self.hidden, self.hidden)
        self.hist_gru = nn.GRU(self.token, self.gru_dim, batch_first=True)
        self.trunk_fc0 = nn.Linear(self.hidden + self.gru_dim, 192)
        self.tool_fc = nn.Linear(192, self.n_tools)
        self.value_fc = nn.Linear(192, 1)
        self.logit_scale = nn.Parameter(torch.tensor([4.0]))

        # Load weights from base ONNX
        self._load_base_weights(base_onnx_path)

        # Freeze base parameters
        for p in [self.state_fc0, self.state_fc3, self.hist_gru, self.trunk_fc0, self.tool_fc, self.value_fc]:
            for param in p.parameters():
                param.requires_grad = False
        self.logit_scale.requires_grad = False

        # 2. Sweet Layer: Context-Aware Gating Router & Residual Expert
        # Router: evaluates state (42 dims) to produce dynamic selection gates g in [0, 1]
        self.router = nn.Sequential(
            nn.Linear(self.base, router_hidden),
            nn.SiLU(),
            nn.Linear(router_hidden, self.n_tools),
            nn.Sigmoid()
        )

        # Expert: evaluates trunk representation h (192 dims) to compute logit adjustments
        self.expert = nn.Sequential(
            nn.Linear(192, expert_hidden),
            nn.SiLU(),
            nn.Linear(expert_hidden, self.n_tools)
        )

        # Initialize expert output layer to zeros so initial output matches base model exactly
        nn.init.zeros_(self.expert[2].weight)
        nn.init.zeros_(self.expert[2].bias)

    def _load_base_weights(self, onnx_path: Path):
        m = onnx.load(str(onnx_path))
        inits = {t.name: numpy_helper.to_array(t).copy() for t in m.graph.initializer}
        
        # State MLP
        self.state_fc0.weight.data.copy_(torch.from_numpy(inits["state.0.weight"]))
        self.state_fc0.bias.data.copy_(torch.from_numpy(inits["state.0.bias"]))
        self.state_fc3.weight.data.copy_(torch.from_numpy(inits["state.3.weight"]))
        self.state_fc3.bias.data.copy_(torch.from_numpy(inits["state.3.bias"]))
        
        # GRU
        w_gru = inits["onnx::GRU_175"].squeeze(0)
        r_gru = inits["onnx::GRU_176"].squeeze(0)
        b_gru = inits["onnx::GRU_177"].squeeze(0)
        
        def onnx_to_torch_gru(mat, hidden_size):
            z, r, h = np.split(mat, 3, axis=0)
            return np.concatenate([r, z, h], axis=0)
            
        w_torch = onnx_to_torch_gru(w_gru, self.gru_dim)
        r_torch = onnx_to_torch_gru(r_gru, self.gru_dim)
        b_w, b_r = np.split(b_gru, 2)
        b_w_torch = onnx_to_torch_gru(b_w, self.gru_dim)
        b_r_torch = onnx_to_torch_gru(b_r, self.gru_dim)
        
        self.hist_gru.weight_ih_l0.data.copy_(torch.from_numpy(w_torch))
        self.hist_gru.weight_hh_l0.data.copy_(torch.from_numpy(r_torch))
        self.hist_gru.bias_ih_l0.data.copy_(torch.from_numpy(b_w_torch))
        self.hist_gru.bias_hh_l0.data.copy_(torch.from_numpy(b_r_torch))
        
        # Trunk
        self.trunk_fc0.weight.data.copy_(torch.from_numpy(inits["trunk.0.weight"]))
        self.trunk_fc0.bias.data.copy_(torch.from_numpy(inits["trunk.0.bias"]))
        
        # Heads
        self.tool_fc.weight.data.copy_(torch.from_numpy(inits["tool.weight"]))
        self.tool_fc.bias.data.copy_(torch.from_numpy(inits["tool.bias"]))
        self.value_fc.weight.data.copy_(torch.from_numpy(inits["value.weight"]))
        self.value_fc.bias.data.copy_(torch.from_numpy(inits["value.bias"]))
        self.logit_scale.data.copy_(torch.from_numpy(inits["logit_scale"]))

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        # x is (B, 266)
        state_in = x[:, :self.base]
        
        # State MLP
        s = nn.functional.gelu(self.state_fc0(state_in))
        s = nn.functional.gelu(self.state_fc3(s))
        
        # History GRU
        cut = self.base + self.steps * self.token
        h_seq = x[:, self.base:cut].reshape(-1, self.steps, self.token)
        _, hn = self.hist_gru(h_seq)
        gru_out = hn[-1]
        
        # Trunk
        fused = torch.cat([s, gru_out], dim=1)
        h_trunk = nn.functional.gelu(self.trunk_fc0(fused))
        
        # Base outputs
        base_logits = self.tool_fc(h_trunk) * self.logit_scale
        value = self.value_fc(h_trunk)
        
        # Sweet Layer: Dynamic Context-Aware Gating
        gate = self.router(state_in)       # (B, 21) in [0, 1]
        delta = self.expert(h_trunk)       # (B, 21) candidate corrections
        
        # Final modulated logits
        logits = base_logits + gate * delta
        return logits, value


def train_and_export(epochs: int = 7, lr: float = 6e-4, batch_size: int = 256) -> Path:
    print("Loading datasets...")
    X_val, y_val, rows_val, tools = load_dataset()
    X_tr, y_tr, rows_tr, _ = load_dataset(REPO / "data" / "tool_sft_v3" / "train.jsonl")

    session = ort.InferenceSession(str(BASE_ONNX), providers=["CPUExecutionProvider"])
    base_val_logits = session.run(["logits"], {"x": X_val})[0]
    base_m = metrics_from_logits(base_val_logits, rows_val, tools, return_correct=True)
    base_reachable = set(base_m["reachable_tools"])
    print(f"Baseline: Val Acc={base_m['val_accuracy']:.5f}, Opt={base_m['optimal']:.5f}, F1={base_m['macro_f1']:.5f}, Reachable={len(base_reachable)}")

    # Compute smoothed inverse frequency weights to guarantee reachability preservation
    counts = np.bincount(y_tr, minlength=len(tools)).astype(float)
    weights = 1.0 / (counts ** 0.3)
    weights /= weights.mean()
    weights_t = torch.from_numpy(weights).float()

    torch.manual_seed(42)
    np.random.seed(42)
    model = DedicatedPolicyWithSweetLayer(BASE_ONNX, router_hidden=32, expert_hidden=32)

    optimizer = torch.optim.AdamW(
        list(model.router.parameters()) + list(model.expert.parameters()),
        lr=lr,
        weight_decay=1e-3
    )
    criterion = nn.CrossEntropyLoss(weight=weights_t)

    train_dataset = torch.utils.data.TensorDataset(
        torch.from_numpy(X_tr),
        torch.from_numpy(y_tr)
    )
    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    x_val_t = torch.from_numpy(X_val)

    best_state = None
    best_opt = base_m["optimal"]
    best_acc = base_m["val_accuracy"]

    print(f"Training Sweet Layer for {epochs} epochs...")
    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        for bx, by in train_loader:
            optimizer.zero_grad()
            logits, _ = model(bx)
            loss = criterion(logits, by)
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * len(by)
            
        model.eval()
        with torch.no_grad():
            val_logits, _ = model(x_val_t)
            val_logits = val_logits.numpy()
            
        val_m = metrics_from_logits(val_logits, rows_val, tools, return_correct=True)
        bs_acc = paired_bootstrap(base_m["_correct"], val_m["_correct"])
        bs_opt = paired_bootstrap(base_m["_optimal_correct"], val_m["_optimal_correct"])
        cand_reachable = set(val_m["reachable_tools"])
        lost_reachable = base_reachable - cand_reachable
        
        print(f"Epoch {epoch:2d}: Loss={total_loss/len(X_tr):.4f} | "
              f"Acc={val_m['val_accuracy']:.5f} (G={bs_acc['gained']:2d}, L={bs_acc['lost']:2d}, net={bs_acc['gained']-bs_acc['lost']:+2d}), "
              f"Opt={val_m['optimal']:.5f} (G={bs_opt['gained']:2d}, L={bs_opt['lost']:2d}, net={bs_opt['gained']-bs_opt['lost']:+2d}, sig={bs_opt['significant']}), "
              f"Reach={len(cand_reachable)}/{len(base_reachable)} (Lost={list(lost_reachable)}), F1={val_m['macro_f1']:.5f}")
        
        if val_m["optimal"] >= best_opt and len(lost_reachable) == 0 and bs_acc["gained"] >= bs_acc["lost"]:
            best_opt = val_m["optimal"]
            best_acc = val_m["val_accuracy"]
            best_state = copy.deepcopy(model.state_dict())

    if best_state is not None:
        model.load_state_dict(best_state)
        print(f"Loaded best checkpoint with 0 lost tools (Opt={best_opt:.5f}, Acc={best_acc:.5f})")

    # Export to ONNX
    cand_onnx = MODELS_DIR / "sweet_layer.candidate.onnx"
    cand_sidecar = MODELS_DIR / "sweet_layer.candidate.json"
    dummy_input = torch.zeros(1, 266, dtype=torch.float32)

    model.eval()
    print(f"Exporting fused ONNX model to {cand_onnx}...")
    torch.onnx.export(
        model,
        dummy_input,
        str(cand_onnx),
        input_names=["x"],
        output_names=["logits", "value"],
        dynamic_axes={
            "x": {0: "batch"},
            "logits": {0: "batch"},
            "value": {0: "batch"}
        },
        opset_version=17,
        dynamo=False
    )

    # Write matching sidecar
    base_sidecar_data = json.loads(Path(BASE_SIDECAR).read_text(encoding="utf-8"))
    base_sidecar_data["sweet_layer"] = {
        "router_hidden": 32,
        "expert_hidden": 32,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": "Context-Gated Dynamic Selection Router (Qwen/FastForward inspired)"
    }
    cand_sidecar.write_text(json.dumps(base_sidecar_data, indent=2), encoding="utf-8")
    print(f"Exported sidecar to {cand_sidecar}")

    # Evaluate Candidate via candidate_verdict
    print("Evaluating candidate through gate...")
    verdict = candidate_verdict(cand_onnx)
    print("Gate Verdict:", json.dumps(verdict, indent=2))
    return cand_onnx


if __name__ == "__main__":
    train_and_export()
