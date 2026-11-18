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
n_tools = len(tools) # 21
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
s_np = out[2] # (3719, 42)
h_np = out[3] # (3719, 192)

y_np = np.array([r['y'] for r in all_rows], dtype=np.int64)
base_preds = base_logits_np.argmax(axis=1)

base_metrics = [ws.metrics_from_logits(base_logits_np[sl], split_rows[i], tools, return_correct=True) for i, sl in enumerate(split_slices)]

print("Baseline Metrics across splits:")
for i, s in enumerate(splits):
    bm = base_metrics[i]
    print(f"  {s}: Acc={bm['val_accuracy']:.5f}, Opt={bm['optimal']:.5f}, F1={bm['macro_f1']:.5f}")

# Load initializers from repaired.onnx
m_rep = onnx.load(str(m_rep_path))
inits_rep = {t.name: numpy_helper.to_array(t) for t in m_rep.graph.initializer}

class SweetPolicyHead(nn.Module):
    def __init__(self, inits):
        super().__init__()
        self.tool_fc = nn.Linear(192, 21)
        self.tool_fc.weight.data.copy_(torch.from_numpy(inits['tool_fc.weight']))
        self.tool_fc.bias.data.copy_(torch.from_numpy(inits['tool_fc.bias']))
        self.logit_scale = float(inits['logit_scale'][0])
        
        self.router_0 = nn.Linear(42, 32)
        self.router_0.weight.data.copy_(torch.from_numpy(inits['router.0.weight']))
        self.router_0.bias.data.copy_(torch.from_numpy(inits['router.0.bias']))
        
        self.router_2 = nn.Linear(32, 21)
        self.router_2.weight.data.copy_(torch.from_numpy(inits['router.2.weight']))
        self.router_2.bias.data.copy_(torch.from_numpy(inits['router.2.bias']))
        
        self.expert_0 = nn.Linear(192, 32)
        self.expert_0.weight.data.copy_(torch.from_numpy(inits['expert.0.weight']))
        self.expert_0.bias.data.copy_(torch.from_numpy(inits['expert.0.bias']))
        
        self.expert_2 = nn.Linear(32, 21)
        self.expert_2.weight.data.copy_(torch.from_numpy(inits['expert.2.weight']))
        self.expert_2.bias.data.copy_(torch.from_numpy(inits['expert.2.bias']))

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

# Verify initial output matches repaired.onnx
with torch.no_grad():
    init_logits = model(s_t, h_t).numpy()

for i, sl in enumerate(split_slices):
    m_init = ws.metrics_from_logits(init_logits[sl], split_rows[i], tools, return_correct=True)
    pb = ws.paired_bootstrap(base_metrics[i]['_correct'], m_init['_correct'])
    p_opt = ws.paired_bootstrap(base_metrics[i]['_optimal_correct'], m_init['_optimal_correct'])
    print(f"Init {splits[i]}: Gained={pb['gained']}, Lost={pb['lost']} | OptGained={p_opt['gained']}, OptLost={p_opt['lost']}")

print("Setup completed successfully.")
