import os
import sys
import json
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

# Determine paths relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
MODEL_PATH = os.path.join(REPO_ROOT, "dofus-network", "models", "dofus-thx01")

_tokenizer = None
_model = None
_id2label = None

def get_model():
    global _tokenizer, _model, _id2label
    if _model is None:
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(f"THX-01 model directory not found at: {MODEL_PATH}")
        
        from transformers import AutoTokenizer, ModernBertForSequenceClassification, ModernBertConfig
        import safetensors.torch
        
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
        cfg = ModernBertConfig.from_pretrained(MODEL_PATH)
        _model = ModernBertForSequenceClassification(cfg)
        
        weights_file = os.path.join(MODEL_PATH, "model.safetensors")
        weights = safetensors.torch.load_file(weights_file, device="cpu")
        _model.load_state_dict(weights, strict=False)
        _model.eval()
        
        labels_file = os.path.join(MODEL_PATH, "labels.json")
        with open(labels_file, "r", encoding="utf-8") as f:
            meta = json.load(f)
        _id2label = {int(v): k for k, v in meta["label_map"].items()}
        
    return _tokenizer, _model, _id2label

def classify(text: str) -> dict:
    tokenizer, model, id2label = get_model()
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=128)
    with torch.no_grad():
        outputs = model(**inputs)
        logits = outputs.logits
        probs = torch.softmax(logits, dim=-1)[0]
        pred_id = int(torch.argmax(probs).item())
        pred_label = id2label.get(pred_id, "NONE")
        confidence = float(probs[pred_id].item())
        
    return {
        "label": pred_label,
        "confidence": confidence
    }

def call_api(prompt, options=None, context=None):
    """
    Promptfoo Python provider entrypoint.
    Returns a dict with 'output' key for assertions.
    """
    try:
        # prompt may be passed as string or dict
        text = prompt if isinstance(prompt, str) else str(prompt.get("raw", prompt))
        res = classify(text)
        return {
            "output": res["label"],
            "metadata": {
                "confidence": res["confidence"]
            }
        }
    except Exception as e:
        return {
            "error": str(e)
        }

if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        result = classify(query)
        print(json.dumps(result), flush=True)
    else:
        # Quick sanity self-test
        sample = "recolte tout le ble sur cette map"
        print(f"Self-test query: '{sample}' ->", classify(sample), flush=True)
