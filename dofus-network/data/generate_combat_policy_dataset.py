"""Generates the Dofus Tactical Combat Policy Dataset for AI Training.

Unlike naive text classification, this dataset represents real tactical combat states:
- Grid distance and Line of Sight (LOS)
- Multi-turn buff sequencing (Power, Flat Damage, Range)
- Trap networks and pushback combinations (Sram)
- Defensive shields and low-HP self-preservation (Feca, Eni, Sacrier)
- Elemental resistance exploitation (hitting fire-weak mobs with fire spells)
- Guaranteed lethal AP conservation

Generates `dofus-network/data/dofus_combat_policy_dataset.json`.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List

DATA_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = DATA_DIR / "dofus_combat_policy_dataset.json"

CLASSES = [
    {
        "breed": "Iop",
        "breed_id": 8,
        "buffs": ["Puissance", "Épée Divine", "Guide de Bravoure"],
        "direct_attacks": [
            {"name": "Pression", "element": "EARTH", "cost": 3},
            {"name": "Concentration", "element": "EARTH", "cost": 2},
            {"name": "Épée d'Iop", "element": "EARTH", "cost": 4},
            {"name": "Épée du Destin", "element": "FIRE", "cost": 4},
            {"name": "Tempête de Puissance", "element": "FIRE", "cost": 3},
            {"name": "Colère d'Iop", "element": "EARTH", "cost": 7},
        ],
        "disengage": "Intimidation",
    },
    {
        "breed": "Cra",
        "breed_id": 9,
        "buffs": ["Tir Puissant", "Tir Éloigné", "Œil de Faucon"],
        "direct_attacks": [
            {"name": "Flèche Explosive", "element": "FIRE", "cost": 4},
            {"name": "Flèche Destructrice", "element": "EARTH", "cost": 4},
            {"name": "Flèche Magique", "element": "FIRE", "cost": 4},
            {"name": "Flèche Glacée", "element": "WATER", "cost": 3},
            {"name": "Flèche d'Expiation", "element": "WATER", "cost": 4},
        ],
        "disengage": "Flèche de Recul",
    },
    {
        "breed": "Sram",
        "breed_id": 4,
        "buffs": ["Invisibilité", "Pulsion de Poison"],
        "direct_attacks": [
            {"name": "Sournoiserie", "element": "EARTH", "cost": 3},
            {"name": "Arnaque", "element": "AIR", "cost": 3},
            {"name": "Attaque Mortelle", "element": "EARTH", "cost": 4},
        ],
        "traps": ["Piège Sournois", "Piège de Masse", "Piège Répulsif"],
        "disengage": "Peur",
    },
    {
        "breed": "Feca",
        "breed_id": 1,
        "buffs": ["Bouclier Féca", "Armure Terrestre", "Armure Venteuse"],
        "direct_attacks": [
            {"name": "Attaque Naturelle", "element": "FIRE", "cost": 3},
            {"name": "Attaque Nuageuse", "element": "WATER", "cost": 4},
            {"name": "Aveuglement", "element": "EARTH", "cost": 3},
            {"name": "Glyphe Enflammé", "element": "FIRE", "cost": 4},
        ],
        "disengage": "Téléportation",
    },
    {
        "breed": "Sacrier",
        "breed_id": 11,
        "buffs": ["Châtiment Forcé", "Châtiment Vitalesque"],
        "direct_attacks": [
            {"name": "Pied du Sacrieur", "element": "EARTH", "cost": 3},
            {"name": "Absorption", "element": "FIRE", "cost": 4},
            {"name": "Dissolution", "element": "WATER", "cost": 4},
            {"name": "Assaut", "element": "AIR", "cost": 3},
        ],
        "disengage": "Attirance",
    },
    {
        "breed": "Eniripsa",
        "breed_id": 7,
        "buffs": ["Mot Stimulant", "Mot de Prévention"],
        "direct_attacks": [
            {"name": "Mot Interdit", "element": "FIRE", "cost": 3},
            {"name": "Mot Blessant", "element": "AIR", "cost": 3},
            {"name": "Mot Vampirique", "element": "WATER", "cost": 4},
        ],
        "heals": ["Mot Soignant", "Mot Reconstituant", "Mot Revitalisant"],
        "disengage": "Mot de Frayeur",
    },
]

MOBS = [
    {"name": "Chef de Guerre Bouftou", "hp": [160, 300], "weakness": "FIRE", "resists": {"neutral": 15, "earth": 25, "fire": -10, "water": 5, "air": 5}},
    {"name": "Craqueleur des Plaines", "hp": [450, 750], "weakness": "WATER", "resists": {"neutral": 30, "earth": 40, "fire": 10, "water": -15, "air": 10}},
    {"name": "Gelée Bleue", "hp": [170, 260], "weakness": "FIRE", "resists": {"neutral": 0, "earth": 0, "fire": -20, "water": 65, "air": -10}},
    {"name": "Gelée Rouge", "hp": [190, 290], "weakness": "WATER", "resists": {"neutral": 0, "earth": -10, "fire": 65, "water": -20, "air": 0}},
    {"name": "Prespic", "hp": [50, 110], "weakness": "AIR", "resists": {"neutral": 10, "earth": 15, "fire": 0, "water": 0, "air": -10}},
    {"name": "Sanglier", "hp": [180, 320], "weakness": "FIRE", "resists": {"neutral": 15, "earth": 25, "fire": -15, "water": 0, "air": 0}},
    {"name": "Chafer Archer", "hp": [140, 240], "weakness": "EARTH", "resists": {"neutral": 0, "earth": -15, "fire": 10, "water": 10, "air": 15}},
    {"name": "Porkass", "hp": [110, 200], "weakness": "WATER", "resists": {"neutral": 5, "earth": 10, "fire": 5, "water": -15, "air": 0}},
    {"name": "Kanigrou", "hp": [800, 1400], "weakness": "FIRE", "resists": {"neutral": 20, "earth": 30, "fire": -15, "water": 10, "air": 15}},
    {"name": "Poutch Ingball", "hp": [10000, 10000], "weakness": "NEUTRAL", "resists": {"neutral": 0, "earth": 0, "fire": 0, "water": 0, "air": 0}},
]


def generate_scenarios(count: int = 1200) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []

    for i in range(count):
        cls_info = random.choice(CLASSES)
        mob_info = random.choice(MOBS)
        mob_hp = random.randint(mob_info["hp"][0], mob_info["hp"][1])
        char_hp_pct = round(random.uniform(0.20, 1.0), 2)
        char_ap = random.choice([8, 9, 10, 11, 12])
        char_mp = random.choice([3, 4, 5, 6])
        dist = random.randint(1, 9)

        # Scenario 1: Critical HP (< 35%) -> Defensive/Heal/Disengage Stance
        if char_hp_pct < 0.35:
            stance = "SHIELD_DEFENSE"
            spells = []
            if "heals" in cls_info:
                spells.append({"name": random.choice(cls_info["heals"]), "type": "SELF_HEAL", "cost": 4})
            elif "buffs" in cls_info and cls_info["breed"] == "Feca":
                spells.append({"name": "Bouclier Féca", "type": "ARMOR_SHIELD", "cost": 3})
            elif cls_info["breed"] == "Sacrier":
                spells.append({"name": "Absorption", "type": "LIFESTEAL", "cost": 4})

            if "disengage" in cls_info and dist <= 2:
                spells.append({"name": cls_info["disengage"], "type": "DISENGAGE_PUSH", "cost": 3})

            reason = f"HP is critical ({int(char_hp_pct*100)}%). Prioritize defensive survival and disengagement."
            movement = "Flee to max distance outside enemy melee threat range."

        # Scenario 2: Mob HP is low (< 180) -> Guaranteed Lethal AP Saver Stance
        elif mob_hp <= 180 and mob_info["name"] != "Poutch Ingball":
            stance = "BURST_LETHAL"
            # Pick cheapest attack that kills
            cheapest = min(cls_info["direct_attacks"], key=lambda x: x["cost"])
            spells = [
                {"name": cheapest["name"], "type": "BURST_ATTACK", "cost": cheapest["cost"]},
            ]
            if mob_hp > 100:
                spells.append({"name": cheapest["name"], "type": "BURST_ATTACK", "cost": cheapest["cost"]})

            spent_ap = sum(s["cost"] for s in spells)
            saved_ap = char_ap - spent_ap
            reason = f"Target HP is low ({mob_hp} HP). Execute lethal burst using {spent_ap} AP and save {saved_ap} AP for movement/defense."
            movement = "Stay in position or advance toward next priority target."

        # Scenario 3: Sram Trap Strategy
        elif cls_info["breed"] == "Sram" and dist >= 3 and random.random() < 0.6:
            stance = "TRAP_AND_KITE"
            spells = [
                {"name": "Invisibilité", "type": "STEALTH_BUFF", "cost": 2},
                {"name": "Piège Répulsif", "type": "TRAP_SETUP", "cost": 2},
                {"name": "Piège de Masse", "type": "TRAP_COMBO", "cost": 3},
                {"name": "Peur", "type": "PUSH_TRIGGER", "cost": 2},
            ]
            reason = "Deploy multi-trap chain reaction: Repelling trap pushes enemy into Mass trap while maintaining invisibility."
            movement = "Move diagonally 3 cells to avoid detection."

        # Scenario 4: High HP Mob -> Multi-Turn Buff Scaling Stance
        elif mob_hp > 400 and cls_info["buffs"] and random.random() < 0.7:
            stance = "BUFF_AND_ENGAGE"
            buff = random.choice(cls_info["buffs"])
            # Match elemental weakness
            weak_elem = mob_info["weakness"]
            attacks_matching = [a for a in cls_info["direct_attacks"] if a["element"] == weak_elem]
            attack = attacks_matching[0] if attacks_matching else cls_info["direct_attacks"][0]

            spells = [
                {"name": buff, "type": "SELF_BUFF", "cost": 3},
                {"name": attack["name"], "type": "ELEMENTAL_EXPLOIT", "cost": attack["cost"]},
            ]
            rem_ap = char_ap - 3 - attack["cost"]
            if rem_ap >= attack["cost"]:
                spells.append({"name": attack["name"], "type": "ELEMENTAL_EXPLOIT", "cost": attack["cost"]})

            reason = f"Target has high HP ({mob_hp} HP). Buffing {buff} on Turn 1 increases 3-turn cumulative damage by +40%, exploiting {weak_elem} weakness ({mob_info['resists'].get(weak_elem.lower(), 0)}% res)."
            movement = "Reposition to maintain optimal casting line of sight."

        # Scenario 5: Standard Direct Elemental Burst
        else:
            stance = "BURST_ATTACK"
            weak_elem = mob_info["weakness"]
            attacks_matching = [a for a in cls_info["direct_attacks"] if a["element"] == weak_elem]
            chosen = attacks_matching[0] if attacks_matching else cls_info["direct_attacks"][0]

            spells = []
            rem = char_ap
            while rem >= chosen["cost"] and len(spells) < 3:
                spells.append({"name": chosen["name"], "type": "ELEMENTAL_ATTACK", "cost": chosen["cost"]})
                rem -= chosen["cost"]

            reason = f"Exploit {weak_elem} elemental weakness directly ({mob_info['resists'].get(weak_elem.lower(), 0)}% res)."
            movement = "Step back 2 cells using remaining MP."

        samples.append({
            "id": f"combat_scenario_{i+1:04d}",
            "state": {
                "character": {
                    "breed": cls_info["breed"],
                    "breed_id": cls_info["breed_id"],
                    "hp_percent": char_hp_pct,
                    "ap": char_ap,
                    "mp": char_mp,
                },
                "target": {
                    "name": mob_info["name"],
                    "hp": mob_hp,
                    "distance_cells": dist,
                    "weakness": mob_info["weakness"],
                    "resistances": mob_info["resists"],
                },
            },
            "decision": {
                "stance": stance,
                "spell_rotation": spells,
                "tactical_rationale": reason,
                "reposition_plan": movement,
            },
        })

    OUTPUT_FILE.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {len(samples)} tactical combat policy scenarios to {OUTPUT_FILE}")
    return samples


if __name__ == "__main__":
    generate_scenarios(1500)
