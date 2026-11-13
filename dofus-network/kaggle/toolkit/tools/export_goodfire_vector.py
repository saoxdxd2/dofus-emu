import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path("dofus-network").resolve()))

import numpy as np
import onnx
import onnxruntime as ort
from sklearn.metrics import roc_auc_score

def export_goodfire_vector():
    model_path = "dofus-network/models/tool_policy.onnx"
    model = onnx.load(model_path)
    output_name = "/Mul_5_output_0"
    
    output_names = [o.name for o in model.graph.output]
    if output_name not in output_names:
        vi = onnx.helper.make_tensor_value_info(output_name, onnx.TensorProto.FLOAT, [None, 192])
        model.graph.output.append(vi)
        
    session = ort.InferenceSession(model.SerializeToString())
    
    train_path = "dofus-network/data/transitions_v2/train.jsonl"
    rows = [json.loads(line) for line in open(train_path, "r", encoding="utf-8") if line.strip()]
    valid_rows = [r for r in rows if "x" in r and isinstance(r["x"], list) and len(r["x"]) == 42 and "moved" in r]
    
    X_state = np.asarray([r["x"] for r in valid_rows], dtype=np.float32)
    X_full = np.pad(X_state, ((0, 0), (0, 224)), mode='constant')
    
    input_name = session.get_inputs()[0].name
    res = session.run([output_name], {input_name: X_full})
    trunk_acts = res[0]  # [N, 192]
    
    moved = np.asarray([1.0 if r["moved"] else 0.0 for r in valid_rows])
    
    h_inert = trunk_acts[moved == 0]
    h_active = trunk_acts[moved == 1]
    
    mu_inert = h_inert.mean(axis=0)
    mu_active = h_active.mean(axis=0)
    
    v_inert = mu_inert - mu_active
    norm = float(np.linalg.norm(v_inert))
    v_unit = v_inert / (norm + 1e-9)
    
    projections = trunk_acts @ v_unit
    auc = float(roc_auc_score(1 - moved, projections))
    
    # Identify top causal neurons
    top_indices = [int(i) for i in np.argsort(np.abs(v_inert))[-10:][::-1]]
    top_details = [
        {
            "neuron": int(i),
            "delta": round(float(v_inert[i]), 5),
            "inert_mean": round(float(mu_inert[i]), 5),
            "active_mean": round(float(mu_active[i]), 5)
        }
        for i in top_indices
    ]
    
    artifact = {
        "kind": "goodfire-steering-vector",
        "version": 1,
        "source_layer": output_name,
        "dim": 192,
        "norm": round(norm, 5),
        "discrimination_auc": round(auc, 4),
        "vector": [round(float(v), 6) for v in v_unit],
        "top_causal_neurons": top_details,
        "metadata": {
            "dataset": "data/transitions_v2/train.jsonl",
            "samples": len(valid_rows),
            "inert_samples": int((moved == 0).sum()),
            "active_samples": int((moved == 1).sum()),
            "method": "Mechanistic Mean-Difference Direction (Goodfire / Takehi et al. 2026)"
        }
    }
    
    out_path = Path("dofus-network/models/goodfire_inert_vector.json")
    out_path.write_text(json.dumps(artifact, indent=1), encoding="utf-8")
    print(f"Exported Goodfire vector to {out_path} with AUC={auc:.4f}, norm={norm:.4f}")

if __name__ == "__main__":
    export_goodfire_vector()
