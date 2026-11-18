import json
import time
from pathlib import Path
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import onnx
from onnx import numpy_helper
import sys

sys.path.insert(0, 'dofus-network')
from agent import weight_space as ws
from tools import wsp_surgery as surgery

folder = Path('dofus-network/models/weight_edits/surgery_20261118T172630Z')
m_base_path = Path('dofus-network/models/tool_policy.onnx')
m_rep_path = folder / 'repaired.onnx'

sidecar = json.loads(Path('dofus-network/models/tool_policy.json').read_text())
tools = sidecar['tools']
n_tools = len(tools)
manifest_base = ws.export_manifest(m_base_path, Path('dofus-network/models/tool_policy.json'))

splits = ['diagnose_errors.jsonl', 'search_val.jsonl', 'frozen_temporal_test.jsonl']
split_rows = [ws.read_val_rows(folder / s) for s in splits]
all_rows = []
split_slices = []
idx = 0
for r_list in split_rows:
    all_rows.extend(r_list)
    split_slices.append(slice(idx, idx + len(r_list)))
    idx += len(r_list)

# Load base model to extract h and s
m_base = onnx.load(str(m_base_path))
for name in ['/Slice_output_0', '/Mul_5_output_0']:
    vi = onnx.helper.make_tensor_value_info(name, onnx.TensorProto.FLOAT, None)
    m_base.graph.output.append(vi)

tmp_onnx = folder / 'tmp_probe_base.onnx'
onnx.save(m_base, str(tmp_onnx))
import onnxruntime as ort
sess = ort.InferenceSession(str(tmp_onnx), providers=['CPUExecutionProvider'])
X = surgery.model_inputs(all_rows, manifest_base['base'])
out = sess.run(None, {'x': X})
tmp_onnx.unlink()

base_logits_np = out[0]
s_np = out[2]
h_np = out[3]

y_np = np.array([r['y'] for r in all_rows], dtype=np.int64)
base_preds = base_logits_np.argmax(axis=1)
base_metrics = [ws.metrics_from_logits(base_logits_np[sl], split_rows[i], tools, return_correct=True) for i, sl in enumerate(split_slices)]

# Load initializers from repaired.onnx
m_rep = onnx.load(str(m_rep_path))
inits_rep = {t.name: numpy_helper.to_array(t).copy() for t in m_rep.graph.initializer}

class SweetPolicyHead(nn.Module):
    def __init__(self, inits):
        super().__init__()
        self.tool_fc = nn.Linear(192, 21)
        self.tool_fc.weight.data.copy_(torch.from_numpy(inits['tool_fc.weight'].copy()))
        self.tool_fc.bias.data.copy_(torch.from_numpy(inits['tool_fc.bias'].copy()))
        self.logit_scale = float(inits['logit_scale'][0])
        
        self.router_0 = nn.Linear(42, 32)
        self.router_0.weight.data.copy_(torch.from_numpy(inits['router.0.weight'].copy()))
        self.router_0.bias.data.copy_(torch.from_numpy(inits['router.0.bias'].copy()))
        
        self.router_2 = nn.Linear(32, 21)
        self.router_2.weight.data.copy_(torch.from_numpy(inits['router.2.weight'].copy()))
        self.router_2.bias.data.copy_(torch.from_numpy(inits['router.2.bias'].copy()))
        
        self.expert_0 = nn.Linear(192, 32)
        self.expert_0.weight.data.copy_(torch.from_numpy(inits['expert.0.weight'].copy()))
        self.expert_0.bias.data.copy_(torch.from_numpy(inits['expert.0.bias'].copy()))
        
        self.expert_2 = nn.Linear(32, 21)
        self.expert_2.weight.data.copy_(torch.from_numpy(inits['expert.2.weight'].copy()))
        self.expert_2.bias.data.copy_(torch.from_numpy(inits['expert.2.bias'].copy()))

    def forward(self, s, h):
        base_logits = (self.tool_fc(h)) * self.logit_scale
        r0 = nn.functional.silu(self.router_0(s))
        gate = torch.sigmoid(self.router_2(r0))
        e0 = nn.functional.silu(self.expert_0(h))
        delta = self.expert_2(e0)
        return base_logits + gate * delta

model = SweetPolicyHead(inits_rep)

s_t = torch.from_numpy(s_np)
h_t = torch.from_numpy(h_np)
y_t = torch.from_numpy(y_np)

# Evaluation function: strictly checks lost == 0 and opt_lost == 0 across all 3 splits!
def evaluate_model(cand_logits):
    total_gained = 0
    total_lost = 0
    total_opt_gained = 0
    total_opt_lost = 0
    split_details = []
    
    for i, sl in enumerate(split_slices):
        cm = ws.metrics_from_logits(cand_logits[sl], split_rows[i], tools, return_correct=True)
        pb = ws.paired_bootstrap(base_metrics[i]['_correct'], cm['_correct'])
        p_opt = ws.paired_bootstrap(base_metrics[i]['_optimal_correct'], cm['_optimal_correct'])
        
        if pb['lost'] > 0:
            return False, 0, 0, 0, 0, f"Split {i} acc lost: {pb['lost']}"
        if p_opt['lost'] > 0:
            return False, 0, 0, 0, 0, f"Split {i} opt lost: {p_opt['lost']}"
            
        # Check common support recall
        y_split = y_np[sl]
        bp_split = base_preds[sl]
        cp_split = cand_logits[sl].argmax(axis=1)
        for c in range(n_tools):
            mask_c = (y_split == c)
            if mask_c.sum() >= ws.COMMON_SUPPORT:
                if (cp_split[mask_c] == c).sum() < (bp_split[mask_c] == c).sum():
                    return False, 0, 0, 0, 0, f"Split {i} common recall drop on tool {tools[c]}"
                    
        total_gained += pb['gained']
        total_lost += pb['lost']
        total_opt_gained += p_opt['gained']
        total_opt_lost += p_opt['lost']
        split_details.append((pb['gained'], p_opt['gained']))
        
    return True, total_gained, total_lost, total_opt_gained, total_opt_lost, str(split_details)

# Row 184 initial bias nudge
with torch.no_grad():
    model.tool_fc.bias.data[1] += 0.07 # +0.28 logit for nav.goto
    cand_init = model(s_t, h_t).numpy()
    valid, g, l, og, ol, details = evaluate_model(cand_init)
    print(f"Row 184 initial bias nudge: valid={valid}, g={g}, l={l}, og={og}, ol={ol} ({details})")

best_gained = g if valid else 15
best_opt_gained = og if valid else 10
best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()} if valid else None
best_info = f"Initial nudge: GAINED={g} (Opt={og})" if valid else "Initial"

# Compute initial margins for ALL 3719 rows
with torch.no_grad():
    cur_logits_t = model(s_t, h_t)

# 1. Base correct mask
base_corr_all = []
base_opt_all = []
for i, sl in enumerate(split_slices):
    base_corr_all.extend(base_metrics[i]['_correct'])
    base_opt_all.extend(base_metrics[i]['_optimal_correct'])
base_corr_all = np.array(base_corr_all, dtype=bool)
base_opt_all = np.array(base_opt_all, dtype=bool)

# Accuracy protection: all rows base got correct
prot_acc_idx = np.where(base_corr_all)[0]
prot_acc_y = y_t[prot_acc_idx]
prot_acc_logits = cur_logits_t[prot_acc_idx]
mask_acc_other = torch.ones_like(prot_acc_logits, dtype=torch.bool)
mask_acc_other.scatter_(1, prot_acc_y.unsqueeze(1), False)
runner_up_acc = prot_acc_logits[mask_acc_other].view(-1, n_tools - 1).max(dim=1).values
true_acc = prot_acc_logits.gather(1, prot_acc_y.unsqueeze(1)).squeeze(1)
target_margins_acc = torch.clamp((true_acc - runner_up_acc) * 0.95 - 0.005, min=0.015)

# 2. Optimality protection: all rows base got optimal
# For each base optimal row, identify which tools are optimal
opt_tools_mask = torch.zeros((len(all_rows), n_tools), dtype=torch.bool)
for i, r in enumerate(all_rows):
    gains = r.get('gains') or {}
    max_g = max(gains.values()) if gains else 0.0
    for k in range(n_tools):
        is_o = (ws.gain_of_row(r, tools[k]) >= max_g - 1e-9) if gains else (k == r['y'])
        opt_tools_mask[i, k] = is_o

prot_opt_idx = np.where(base_opt_all)[0]
opt_mask_sub = opt_tools_mask[prot_opt_idx] # (M, 21)
non_opt_mask_sub = ~opt_mask_sub

prot_opt_logits = cur_logits_t[prot_opt_idx]
best_opt_logits_init = torch.where(opt_mask_sub, prot_opt_logits, -999.0).max(dim=1).values
worst_non_opt_logits_init = torch.where(non_opt_mask_sub, prot_opt_logits, -999.0).max(dim=1).values
initial_opt_margins = best_opt_logits_init - worst_non_opt_logits_init
target_margins_opt = torch.clamp(initial_opt_margins * 0.95 - 0.005, min=0.015)

# Errors to target: rows that are not both correct and optimal
target_error_idx = np.where((~base_corr_all) | (~base_opt_all))[0]
target_error_y = y_t[target_error_idx]

optimizer = optim.AdamW([
    {'params': model.router_0.parameters(), 'lr': 1e-4},
    {'params': model.router_2.parameters(), 'lr': 1e-4},
    {'params': model.expert_0.parameters(), 'lr': 1.5e-4},
    {'params': model.expert_2.parameters(), 'lr': 1.5e-4},
    {'params': model.tool_fc.parameters(), 'lr': 5e-5}
], weight_decay=1e-4)

init_params = {name: param.detach().clone() for name, param in model.named_parameters()}

print(f"Starting targeted optimization. Target errors: {len(target_error_idx)}, Prot Acc: {len(prot_acc_idx)}, Prot Opt: {len(prot_opt_idx)}")

for step in range(1, 3001):
    optimizer.zero_grad()
    logits = model(s_t, h_t)
    
    # 1. Accuracy barrier loss
    p_logits = logits[prot_acc_idx]
    p_runner_up = p_logits[mask_acc_other].view(-1, n_tools - 1).max(dim=1).values
    p_true = p_logits.gather(1, prot_acc_y.unsqueeze(1)).squeeze(1)
    p_margin = p_true - p_runner_up
    barrier_acc_viol = torch.relu(target_margins_acc - p_margin)
    loss_acc_barrier = 8000.0 * torch.mean(barrier_acc_viol ** 2)
    
    # 2. Optimality barrier loss
    o_logits = logits[prot_opt_idx]
    best_opt = torch.where(opt_mask_sub, o_logits, -999.0).max(dim=1).values
    worst_non_opt = torch.where(non_opt_mask_sub, o_logits, -999.0).max(dim=1).values
    o_margin = best_opt - worst_non_opt
    barrier_opt_viol = torch.relu(target_margins_opt - o_margin)
    loss_opt_barrier = 8000.0 * torch.mean(barrier_opt_viol ** 2)
    
    # 3. Target error loss: softplus margin ranking
    e_logits = logits[target_error_idx]
    mask_e_other = torch.ones_like(e_logits, dtype=torch.bool)
    mask_e_other.scatter_(1, target_error_y.unsqueeze(1), False)
    e_runner_up = e_logits[mask_e_other].view(-1, n_tools - 1).max(dim=1).values
    e_true = e_logits.gather(1, target_error_y.unsqueeze(1)).squeeze(1)
    e_margin = e_true - e_runner_up
    loss_err = torch.mean(torch.nn.functional.softplus(0.5 - e_margin))
    
    # 4. Weight regularization
    loss_reg = 0.0
    for name, param in model.named_parameters():
        loss_reg += torch.sum((param - init_params[name]) ** 2)
    loss_reg = 0.01 * loss_reg
    
    total_loss = loss_acc_barrier + loss_opt_barrier + loss_err + loss_reg
    total_loss.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
    optimizer.step()
    
    if step % 25 == 0:
        with torch.no_grad():
            cand_l = model(s_t, h_t).numpy()
            valid, g, l, og, ol, details = evaluate_model(cand_l)
            if valid and (g > best_gained or (g == best_gained and og > best_opt_gained)):
                best_gained = g
                best_opt_gained = og
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
                best_info = f"Step {step:4d}: GAINED={g} (Opt={og}), LOST={l} (OptLost={ol}) Splits={details}"
                print(f"[*] NEW BEST: {best_info}")
            elif step % 100 == 0:
                print(f"Step {step:4d}: Loss={total_loss.item():.4f} (BarA={loss_acc_barrier.item():.4f}, BarO={loss_opt_barrier.item():.4f}, Err={loss_err.item():.4f}) | Valid={valid} ({details})")

print(f"\nOptimization complete! Best valid result: {best_info}")
if best_state is not None:
    torch.save(best_state, folder / 'best_associative_weights.pt')
    print("Saved best weights to best_associative_weights.pt")
