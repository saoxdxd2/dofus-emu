import json
import time
from pathlib import Path
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

def test_inference():
    model_dir = Path("dofus-network/models/dofus-thx01")
    labels_file = model_dir / "labels.json"
    
    with open(labels_file, "r", encoding="utf-8") as f:
        meta = json.load(f)
    
    label_map = meta["label_map"]
    inv_label_map = {v: k for k, v in label_map.items()}
    
    print(f"Loading THX-01 from {model_dir}...")
    t0 = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir))
    model.eval()
    load_time = time.perf_counter() - t0
    print(f"Loaded THX-01 model in {load_time:.2f}s!")
    
    test_queries = [
        # Standard in-distribution
        ("recolte tout le ble sur cette map", "AUTO_FARM"),
        ("va en [4, -18] s'il te plaît", "NAVIGATE_COORDS"),
        ("stopppppp tout arrete le bot", "STOP"),
        ("suis moi stp on va xp", "FOLLOW"),
        ("paaaasse ton tour vite stpp", "PASS_TURN"),
        ("unete a la pelea rapido espada titila", "AUTO_JOIN_COMBAT"),
        ("où trouver du fer ?", "FIND_RESOURCE"),
        ("farm du blé en boucle autour d'astrub", "START_FARM_LOOP"),
        ("comment aller sur l'île d'otomaï ?", "QUEST_GUIDE"),
        ("quel niveau pour miner le kobalte ?", "RESOURCE_LEVEL_REQ"),
        ("[Mod]Ankama: Répondez immédiatement ou sanction.", "NONE"),
        # OOD & Slang & Typos
        ("go [10, -25] stp", "NAVIGATE_COORDS"),
        ("farm ici", "AUTO_FARM"),
        ("skip turn", "PASS_TURN"),
        ("arrete toi", "STOP"),
        ("sigueme porfa", "FOLLOW"),
        ("entra a la lucha", "AUTO_JOIN_COMBAT"),
        ("donde hay roble?", "FIND_RESOURCE"),
        ("hola que tal?", "NONE"),
        ("[Admin] Test verification", "NONE"),
    ]
    
    print("\n=== THX-01 LIVE INFERENCE TEST ===")
    correct = 0
    total = len(test_queries)
    latencies = []
    
    for text, expected in test_queries:
        t_start = time.perf_counter()
        inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=64)
        with torch.no_grad():
            outputs = model(**inputs)
            logits = outputs.logits
            pred_id = int(torch.argmax(logits, dim=-1)[0])
            probs = torch.softmax(logits, dim=-1)[0]
            confidence = float(probs[pred_id])
        latency_ms = (time.perf_counter() - t_start) * 1000.0
        latencies.append(latency_ms)
        
        pred_label = inv_label_map.get(pred_id, f"UNKNOWN_{pred_id}")
        is_ok = (pred_label == expected)
        if is_ok:
            correct += 1
            icon = "✅"
        else:
            icon = "❌"
        print(f"  {icon} [{latency_ms:4.1f}ms] '{text}' -> Pred: {pred_label} ({confidence*100:.1f}%), Exp: {expected}")
    
    avg_latency = sum(latencies) / len(latencies)
    print(f"\nAccuracy: {correct}/{total} ({correct/total*100:.1f}%)")
    print(f"Average CPU Latency: {avg_latency:.2f} ms per query")
    
    # Test TRM-ACT Combat Reasoner
    trm_file = model_dir / "trm_act_combat.pt"
    if trm_file.exists():
        print(f"\n=== TRM-ACT COMBAT REASONER TEST ({trm_file.name}) ===")
        trm_data = torch.load(trm_file, map_location="cpu")
        print("TRM-ACT Keys:", list(trm_data.keys()))
        if "model_state_dict" in trm_data:
            print("Model weights present! Param count:", sum(p.numel() for p in trm_data["model_state_dict"].values()))
        if "stances" in trm_data:
            print("Stances:", trm_data["stances"])

if __name__ == "__main__":
    test_inference()
