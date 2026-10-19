"""
THX-01 ModernBERT Combat Decision AI Server for Dofus Touch.

Loads dofus-network/models/dofus-thx01/model.safetensors via HuggingFace Transformers
and serves tactical combat decisions over HTTP (127.0.0.1:8892).
"""

from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "dofus-network") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "dofus-network"))

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL_DIR = REPO_ROOT / "dofus-network" / "models" / "dofus-thx01"

print("==========================================================")
print("   DOFUS THX-01 COMBAT DECISION AI SERVER               ")
print("==========================================================")
print(f"Loading tokenizer from: {MODEL_DIR}")
tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR))

print(f"Loading ModernBERT sequence classifier from: {MODEL_DIR / 'model.safetensors'}")
device = "cuda" if torch.cuda.is_available() else "cpu"
model = AutoModelForSequenceClassification.from_pretrained(str(MODEL_DIR), use_safetensors=True)
model.to(device)
model.eval()
print(f"Model loaded on {device.upper()}! Parameters: {sum(p.numel() for p in model.parameters()):,}")

# Load labels
LABELS_PATH = MODEL_DIR / "labels.json"
label_map = {}
stances = ["BURST_LETHAL", "BUFF_AND_ENGAGE", "TRAP_AND_KITE", "SHIELD_DEFENSE"]
if LABELS_PATH.exists():
    with open(LABELS_PATH, "r", encoding="utf-8") as f:
        lbl_data = json.load(f)
        label_map = lbl_data.get("label_map", {})
        stances = lbl_data.get("stances", stances)

id2stance = {i: stances[i % len(stances)] for i in range(len(stances))}


def evaluate_combat_turn(payload: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.perf_counter()

    player = payload.get("player", {})
    monsters = payload.get("monsters", [])
    
    char_hp = player.get("hp", 100)
    char_max_hp = player.get("maxHp", 100)
    char_ap = player.get("ap", 6)
    char_mp = player.get("mp", 3)
    char_cell = player.get("cellId", 250)
    breed = player.get("breed", "Cra")

    # Pick primary monster target
    target_monster = monsters[0] if monsters else {"id": -1, "name": "Monster", "hp": 500, "cellId": 284}
    mob_hp = target_monster.get("hp", 500)
    mob_cell = target_monster.get("cellId", 284)
    mob_name = target_monster.get("name", "Unknown")

    # Formulate state description for THX-01 ModernBERT
    state_prompt = (
        f"COMBAT | Class: {breed} | Self HP: {char_hp}/{char_max_hp} | "
        f"AP: {char_ap} | MP: {char_mp} | Target: {mob_name} (HP: {mob_hp}, Cell: {mob_cell}) | "
        f"Distance: {abs(char_cell - mob_cell) // 14}"
    )

    inputs = tokenizer(state_prompt, return_tensors="pt", max_length=512, truncation=True)
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        logits = model(**inputs).logits
        probs = torch.softmax(logits, dim=-1)[0]

    pred_idx = int(probs.argmax())
    confidence = float(probs[pred_idx])

    # Map prediction to tactical stance
    if char_hp < char_max_hp * 0.35:
        stance = "SHIELD_DEFENSE"
    elif mob_hp <= 400:
        stance = "BURST_LETHAL"
    else:
        stance = id2stance.get(pred_idx % len(stances), "BURST_LETHAL")

    # Plan actions based on stance & AP
    actions = []
    rem_ap = char_ap

    # Default spell selection (Cra / AOE)
    default_spells = player.get("spells", [])
    aoe_spell = 164  # Flèche Explosive
    buff_spell = 161 # Tir Puissant

    if stance == "BUFF_AND_ENGAGE" and rem_ap >= 6:
        # Cast buff then attack
        actions.append({"type": "CAST", "spellId": buff_spell, "cellId": char_cell})
        rem_ap -= 2
        while rem_ap >= 4:
            actions.append({"type": "CAST", "spellId": aoe_spell, "cellId": mob_cell})
            rem_ap -= 4
    else:
        # Direct burst lethal
        while rem_ap >= 4:
            actions.append({"type": "CAST", "spellId": aoe_spell, "cellId": mob_cell})
            rem_ap -= 4

    # Calculate safe kite retreat if MP remaining
    if char_mp > 0 and (stance in ["TRAP_AND_KITE", "SHIELD_DEFENSE"] or breed == "Cra"):
        # Simple retreat away from enemy cell
        retreat_cell = char_cell - (14 * min(char_mp, 2))
        if retreat_cell > 0:
            actions.append({"type": "MOVE", "cellId": retreat_cell})

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        "status": "ok",
        "stance": stance,
        "confidence": round(confidence, 4),
        "targetMonsterId": target_monster.get("id"),
        "targetCell": mob_cell,
        "actions": actions,
        "latency_ms": round(elapsed_ms, 2)
    }


class CombatRequestHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "model": "dofus-thx01-modernbert", "device": device}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/v1/combat/decide":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body) if body else {}
                response_data = evaluate_combat_turn(data)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(response_data).encode("utf-8"))
            except Exception as ex:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(ex)}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress noisy standard logs, print concise summary
        pass


def run_server(port: int = 8892):
    server = ThreadingHTTPServer(("127.0.0.1", port), CombatRequestHandler)
    print(f"\n[OK] THX-01 Combat AI Server active on http://127.0.0.1:{port}")
    print("Endpoints:")
    print(f"  GET  http://127.0.0.1:{port}/health")
    print(f"  POST http://127.0.0.1:{port}/v1/combat/decide")
    print("Ready for real-time game client requests.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Combat AI server...")
        server.shutdown()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8892
    run_server(port)
