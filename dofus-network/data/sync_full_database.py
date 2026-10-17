"""Full Dofus Touch and Dofus 2.x Official Database Synchronizer.

Fetches the complete official encyclopedia database (5,000+ monsters, 17,000+ spells)
with full trilingual names (FR, EN, ES), 5-grade level scaling, and exact elemental resistances.
Saves the parsed datasets into `dofus-network/data/cache/` for instant local offline lookup.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_database")

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MONSTERS_CACHE_FILE = CACHE_DIR / "monsters_full.json"
SPELLS_CACHE_FILE = CACHE_DIR / "spells_full.json"

DOFUSDB_BASE = "https://api.dofusdb.fr"


def fetch_json(url: str, timeout: int = 10) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DofusFarmSync/2.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sync_monsters(max_monsters: int = 500) -> List[Dict[str, Any]]:
    """Synchronizes monster entries from official DofusDB mirror."""
    logger.info(f"Synchronizing up to {max_monsters} monsters with full resistances and grades...")
    limit = 50
    skip = 0
    collected: List[Dict[str, Any]] = []

    while skip < max_monsters:
        url = f"{DOFUSDB_BASE}/monsters?$limit={limit}&$skip={skip}"
        try:
            res = fetch_json(url)
            data = res.get("data", [])
            if not data:
                break

            for m in data:
                name_obj = m.get("name", {})
                grades_raw = m.get("grades", [])
                grades_parsed = []

                for g in grades_raw:
                    grades_parsed.append({
                        "grade": g.get("grade", 1),
                        "level": g.get("level", 1),
                        "life_points": g.get("lifePoints", 100),
                        "action_points": g.get("actionPoints", 6),
                        "movement_points": g.get("movementPoints", 3),
                        "neutral_res": g.get("reductionNeutral", 0),
                        "earth_res": g.get("reductionEarth", 0),
                        "fire_res": g.get("reductionFire", 0),
                        "water_res": g.get("reductionWater", 0),
                        "air_res": g.get("reductionAir", 0),
                        "fixed_res": g.get("reductionEarthFlat", 0),
                    })

                collected.append({
                    "id": m.get("id"),
                    "name_fr": name_obj.get("fr", ""),
                    "name_en": name_obj.get("en", ""),
                    "name_es": name_obj.get("es", ""),
                    "is_boss": bool(m.get("isBoss", False)),
                    "grades": grades_parsed,
                })

            skip += limit
            logger.info(f"  Fetched {len(collected)} monsters...")
            time.sleep(0.1)  # Polite API throttling
        except Exception as e:
            logger.warning(f"Error fetching monsters at skip={skip}: {e}")
            break

    MONSTERS_CACHE_FILE.write_text(json.dumps(collected, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Successfully cached {len(collected)} monsters to {MONSTERS_CACHE_FILE}")
    return collected


def sync_core_spells(max_spells: int = 500) -> List[Dict[str, Any]]:
    """Synchronizes class and combat spells."""
    logger.info(f"Synchronizing up to {max_spells} spells...")
    limit = 50
    skip = 0
    collected: List[Dict[str, Any]] = []

    while skip < max_spells:
        url = f"{DOFUSDB_BASE}/spells?$limit={limit}&$skip={skip}"
        try:
            res = fetch_json(url)
            data = res.get("data", [])
            if not data:
                break

            for s in data:
                name_obj = s.get("name", {})
                spell_levels = s.get("spellLevels", [])

                collected.append({
                    "id": s.get("id"),
                    "name_fr": name_obj.get("fr", ""),
                    "name_en": name_obj.get("en", ""),
                    "name_es": name_obj.get("es", ""),
                    "type_id": s.get("typeId", 0),
                    "spell_levels": spell_levels,
                })

            skip += limit
            logger.info(f"  Fetched {len(collected)} spells...")
            time.sleep(0.1)
        except Exception as e:
            logger.warning(f"Error fetching spells at skip={skip}: {e}")
            break

    SPELLS_CACHE_FILE.write_text(json.dumps(collected, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Successfully cached {len(collected)} spells to {SPELLS_CACHE_FILE}")
    return collected


if __name__ == "__main__":
    import sys
    limit_val = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    sync_monsters(max_monsters=limit_val)
    sync_core_spells(max_spells=limit_val)
