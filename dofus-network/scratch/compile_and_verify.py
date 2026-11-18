import json
import shutil
from pathlib import Path
import numpy as np
import torch
import onnx
from onnx import numpy_helper
import onnxruntime as ort
import sys

sys.path.insert(0, 'dofus-network')
from agent import weight_space as ws
from tools import wsp_surgery as surgery

folder = Path('dofus-network/models/weight_edits/surgery_20261118T172630Z')
onnx_path = folder / 'repaired.onnx'
json_path = folder / 'raw_tensors.json'
weights_path = folder / 'best_associative_weights.pt'

print("Loading saved best weights...")
state_dict = torch.load(weights_path, weights_only=True)

# 1. Update ONNX model
print(f"Updating {onnx_path}...")
model = onnx.load(str(onnx_path))

# Mapping from state_dict keys to ONNX initializer names
key_map = {
    'tool_fc.weight': 'tool_fc.weight',
    'tool_fc.bias': 'tool_fc.bias',
    'router_0.weight': 'router.0.weight',
    'router_0.bias': 'router.0.bias',
    'router_2.weight': 'router.2.weight',
    'router_2.bias': 'router.2.bias',
    'expert_0.weight': 'expert.0.weight',
    'expert_0.bias': 'expert.0.bias',
    'expert_2.weight': 'expert.2.weight',
    'expert_2.bias': 'expert.2.bias'
}

# Update initializers
inits_dict = {init.name: init for init in model.graph.initializer}
for torch_key, onnx_name in key_map.items():
    arr = state_dict[torch_key].numpy().astype(np.float32)
    inits_dict[onnx_name].CopyFrom(numpy_helper.from_array(arr, name=onnx_name))
    print(f"  Updated init {onnx_name} shape {arr.shape}")

# Update Constant_19 to all 1.0s (unmask all 21 tools)
c19_updated = False
for node in model.graph.node:
    if 'Constant_19' in node.name or 'Constant_19' in node.output:
        for attr in node.attribute:
            if attr.name == 'value':
                arr = numpy_helper.to_array(attr.t)
                arr_all_1 = np.ones_like(arr, dtype=np.float32)
                attr.t.CopyFrom(numpy_helper.from_array(arr_all_1, name=attr.t.name))
                c19_updated = True
                print("  Updated /Constant_19 node attribute to all 1.0s")

assert c19_updated, "Failed to find Constant_19 node"

# Save updated ONNX
onnx.save(model, str(onnx_path))
print(f"Saved {onnx_path}")

# 2. Update raw_tensors.json
print(f"Updating {json_path}...")
raw_json = json.loads(json_path.read_text(encoding='utf-8'))

tensor_dict = {t['name']: t for t in raw_json['tensors']}
for torch_key, onnx_name in key_map.items():
    arr = state_dict[torch_key].numpy().astype(np.float32)
    tensor_dict[onnx_name]['values'] = arr.tolist()

# Update Constant_19 in graph attribute of raw_tensors.json
for node in raw_json.get('graph', []):
    if 'Constant_19' in node['name'] or 'Constant_19' in node.get('outputs', []):
        node['attributes']['value']['values'] = [1.0] * 21
        print("  Updated Constant_19 in raw_tensors.json graph")

json_path.write_text(json.dumps(raw_json, indent=2), encoding='utf-8')
print(f"Saved {json_path}")

# 3. Direct verification with ONNX Runtime
print("\n=== VERIFYING REPAIRED.ONNX WITH ONNXRUNTIME ===")
sess = ort.InferenceSession(str(onnx_path), providers=['CPUExecutionProvider'])

sidecar = json.loads(Path('dofus-network/models/tool_policy.json').read_text())
tools = sidecar['tools']
manifest_base = ws.export_manifest(Path('dofus-network/models/tool_policy.onnx'), Path('dofus-network/models/tool_policy.json'))
manifest_rep = ws.export_manifest(onnx_path, Path('dofus-network/models/tool_policy.json'))

splits = ['diagnose_errors.jsonl', 'search_val.jsonl', 'frozen_temporal_test.jsonl']
total_gained = 0
total_lost = 0
total_opt_gained = 0
total_opt_lost = 0

for s in splits:
    rows = ws.read_val_rows(folder / s)
    l_base = ws._onnx_logits(Path('dofus-network/models/tool_policy.onnx'), rows, manifest_base['base'])
    l_rep = ws._onnx_logits(onnx_path, rows, manifest_rep['base'])
    
    mb = ws.metrics_from_logits(l_base, rows, tools, return_correct=True)
    mr = ws.metrics_from_logits(l_rep, rows, tools, return_correct=True)
    
    pb = ws.paired_bootstrap(mb['_correct'], mr['_correct'])
    p_opt = ws.paired_bootstrap(mb['_optimal_correct'], mr['_optimal_correct'])
    
    print(f"\n--- Split: {s} ({len(rows)} rows) ---")
    print(f"  Base Accuracy:   {mb['val_accuracy']:.5f} -> Repaired: {mr['val_accuracy']:.5f} (net {mr['val_accuracy'] - mb['val_accuracy']:+.5f})")
    print(f"  Base Optimality: {mb['optimal']:.5f} -> Repaired: {mr['optimal']:.5f} (net {mr['optimal'] - mb['optimal']:+.5f})")
    print(f"  Base Macro-F1:   {mb['macro_f1']:.5f} -> Repaired: {mr['macro_f1']:.5f} (net {mr['macro_f1'] - mb['macro_f1']:+.5f})")
    print(f"  Acc Gained={pb['gained']}, Acc Lost={pb['lost']} (net={pb['gained']-pb['lost']:+d})")
    print(f"  Opt Gained={p_opt['gained']}, Opt Lost={p_opt['lost']} (net={p_opt['gained']-p_opt['lost']:+d}, sig={p_opt['significant']})")
    
    # Check common recall monotonicity
    y_split = np.array([r['y'] for r in rows])
    bp = l_base.argmax(axis=1)
    rp = l_rep.argmax(axis=1)
    for c in range(len(tools)):
        mask_c = (y_split == c)
        if mask_c.sum() >= ws.COMMON_SUPPORT:
            b_cnt = (bp[mask_c] == c).sum()
            r_cnt = (rp[mask_c] == c).sum()
            assert r_cnt >= b_cnt, f"Regression on common tool {tools[c]}: {r_cnt} < {b_cnt}"
            print(f"    Tool {tools[c]:15s}: support={mask_c.sum():3d}, base_correct={b_cnt:3d} -> rep_correct={r_cnt:3d} (OK)")
            
    total_gained += pb['gained']
    total_lost += pb['lost']
    total_opt_gained += p_opt['gained']
    total_opt_lost += p_opt['lost']

print(f"\n==========================================")
print(f"OVERALL SUMMARY (3,719 rows):")
print(f"  Total Accuracy Gained:   {total_gained}")
print(f"  Total Accuracy Lost:     {total_lost}")
print(f"  Total Optimality Gained: {total_opt_gained}")
print(f"  Total Optimality Lost:   {total_opt_lost}")
print(f"==========================================")
assert total_lost == 0, f"Expected 0 lost, got {total_lost}"
assert total_opt_lost == 0, f"Expected 0 opt lost, got {total_opt_lost}"
print("VERIFICATION SUCCEEDED WITH ZERO REGRESSIONS!")
