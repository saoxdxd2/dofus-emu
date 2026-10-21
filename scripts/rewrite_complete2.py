# Read the current file
with open('scripts/complete_osa_spells.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find where "def main():" starts
main_idx = content.find('def main():')
if main_idx < 0:
    print("ERROR: def main(): not found!")
else:
    # Keep everything from def main(): onwards
    main_part = content[main_idx:]

    # New header
    new_header = '''"""
Complete osa_spells.json by adding spell IDs from dofus-touch.com.

Updated: Matched 13 spells from spells_full.json by French/English name matching
against the 22 dofus-touch Osamadas spell names. 12 spells are dofus-touch only
(not found in spells_full.json) and are handled separately in DOFUS_TOUCH_ONLY.

Usage:
    1. Run: python scripts/complete_osa_spells.py
    2. Manually add dofus-touch-only spells using scripts/add_dofus_touch_only.py
    3. Run: python scripts/validate_json.py
"""

import json
from pathlib import Path

DATA = Path("dofus-network/data/cache")
OUT = Path("scripts/output")

# ---------------------------------------------------------------------------
# STEP 1: Osamadas spell IDs from spells_full.json (matched by name to dofus-touch)
# ---------------------------------------------------------------------------
# 4 verified by exact match: 201, 202, 214, 224
# 9 matched by French/English name + type_id=425: 628, 629, 604, 779, 1706, 603, 2369, 1535, 524
OSA_SPELL_IDS = [
    201, 202, 214, 224,
    628, 629, 604, 779, 1706, 603, 2369, 1535, 524,
]

# Dofus-touch only spells (IDs not in spells_full.json)
# These need to be added manually to osa_spells.json
DOFUS_TOUCH_ONLY = [
    {"touch_id": 4714, "name_en": "Lashing Claw", "name_fr": "Griffe Cinglante", "summon": False},
    {"touch_id": 4718, "name_en": "Feline Movement", "name_fr": "Déplacement Féline", "summon": False},
    {"touch_id": 4720, "name_en": "Ghostly Claw", "name_fr": "Griffe Spectrale", "summon": False},
    {"touch_id": 4726, "name_en": "Bestial Heal", "name_fr": "Soin Bestial", "summon": False},
    {"touch_id": 4728, "name_en": "Whip", "name_fr": "Fouet", "summon": False},
    {"touch_id": 4730, "name_en": "Substitution", "name_fr": "Substitution", "summon": False},
    {"touch_id": 4734, "name_en": "Tumult", "name_fr": "Tumulte", "summon": False},
    {"touch_id": 4736, "name_en": "Favouritism", "name_fr": "Favoritisme", "summon": False},
    {"touch_id": 4748, "name_en": "Summoning of Red Wyrmling", "name_fr": "Invocation de Dragonnet Rouge", "summon": True, "summon_name": "Red Wyrmling"},
    {"touch_id": 4750, "name_en": "Determination", "name_fr": "Acharnement", "summon": False},
    {"touch_id": 4752, "name_en": "Symbiosa", "name_fr": "Symbiose", "summon": False},
    {"touch_id": 6341, "name_en": "Summoning of Osamodas Dopple", "name_fr": "Invocation de Dopeul Osamadas", "summon": True, "summon_name": "Osamodas Dopple"},
]


'''

    full_content = new_header + main_part
    with open('scripts/complete_osa_spells.py', 'w', encoding='utf-8') as f:
        f.write(full_content)
    print("File updated successfully")
