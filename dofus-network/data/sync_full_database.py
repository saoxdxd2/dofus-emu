"""Full Dofus Touch and Dofus 2.x Official Database Synchronizer.

Fetches the complete official encyclopedia database (5,000+ monsters, 17,000+ spells)
with full trilingual names (FR, EN, ES), 5-grade level scaling, and exact elemental resistances.
Saves the parsed datasets into `dofus-network/data/cache/` for instant local offline lookup.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_database")

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MONSTERS_CACHE_FILE = CACHE_DIR / "monsters_full.json"
SPELLS_CACHE_FILE = CACHE_DIR / "spells_full.json"
ITEMS_CACHE_FILE = CACHE_DIR / "items_full.json"
QUESTS_CACHE_FILE = CACHE_DIR / "quests_full.json"
NPCS_CACHE_FILE = CACHE_DIR / "npcs_full.json"
ACHIEVEMENTS_CACHE_FILE = CACHE_DIR / "achievements_full.json"
DUNGEONS_CACHE_FILE = CACHE_DIR / "dungeons_full.json"
MAPS_CACHE_FILE = CACHE_DIR / "maps_full.json"
SUBAREAS_CACHE_FILE = CACHE_DIR / "subareas_full.json"
AREAS_CACHE_FILE = CACHE_DIR / "areas_full.json"

DOFUSDB_BASE = "https://api.dofusdb.fr"

# DofusDB localises names in these languages (verified live for quests, NPCs,
# achievements, dungeons and items — German included).
LANG_KEYS = ("fr", "en", "es", "de", "pt")


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


# ---------------------------------------------------------------------------
# Generic collection sync (v2): items, quests, NPCs, achievements, dungeons,
# maps, subareas, areas. Same polite-pagination contract as monsters/spells:
# never fatal on a network error, never overwrite a good cache with an empty
# pull, and the parsed record shape is stable enough to index offline.
# ---------------------------------------------------------------------------


def _loc(value: Any) -> Dict[str, str]:
    """Normalise a DofusDB localised field into ``{lang: text}``.

    The API returns ``{"fr": "...", "en": "...", ...}`` for names and
    descriptions but occasionally a plain string; both are accepted. Empty
    values are dropped so a missing German name does not create a ``de: ""``
    entry that later fingerprints as a real translation.
    """
    if isinstance(value, dict):
        out: Dict[str, str] = {}
        for lang in LANG_KEYS:
            text = value.get(lang)
            if isinstance(text, str) and text.strip():
                out[lang] = text
        return out
    if isinstance(value, str) and value.strip():
        return {"fr": value}
    return {}


def fetch_page(endpoint: str, limit: int, skip: int, timeout: int = 20) -> Dict[str, Any]:
    sep = "&" if "?" in endpoint else "?"
    return fetch_json(f"{DOFUSDB_BASE}{endpoint}{sep}$limit={limit}&$skip={skip}", timeout=timeout)


def sync_collection(
    name: str,
    endpoint: str,
    parser: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    out_file: Path,
    max_records: int = 100000,
    page_size: int = 50,
    sleep_seconds: float = 0.1,
    resume: bool = False,
    retries: int = 2,
) -> List[Dict[str, Any]]:
    """Paginated sync for one DofusDB collection with a per-record parser.

    ``resume=True`` continues a previously interrupted pull: the already-cached
    records are kept, the next page offset comes from a sidecar ``.meta.json``
    (written every 10 pages and at the end), with a file-length fallback when
    the meta file is missing. Transient page failures are retried before the
    sync degrades to a partial cache.
    """
    logger.info(f"Synchronizing {name} from {endpoint} (up to {max_records})...")
    meta_file = out_file.with_name(out_file.stem + ".meta.json")

    existing: List[Dict[str, Any]] = []
    skip = 0
    if resume and out_file.exists():
        try:
            payload = json.loads(out_file.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                existing = payload
        except (json.JSONDecodeError, OSError):
            existing = []
        if existing:
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                skip = int(meta.get("next_skip", len(existing)))
            except (json.JSONDecodeError, OSError, ValueError):
                skip = (len(existing) // page_size) * page_size
            logger.info(f"Resuming {name} at skip={skip} ({len(existing)} records already cached)")

    collected: List[Dict[str, Any]] = []
    saw_total: Optional[int] = None
    page = 0

    while len(existing) + len(collected) < max_records:
        response: Optional[Dict[str, Any]] = None
        for attempt in range(retries + 1):
            try:
                response = fetch_page(endpoint, page_size, skip)
                break
            except Exception as exc:  # noqa: BLE001 - retried, then degraded
                if attempt >= retries:
                    logger.warning(f"Error fetching {name} at skip={skip}: {exc}")
                else:
                    time.sleep(1.0 + attempt)
        if response is None:
            break
        if saw_total is None and isinstance(response.get("total"), int):
            saw_total = response["total"]

        data = response.get("data") or []
        if not data:
            break
        for record in data:
            if len(existing) + len(collected) >= max_records:
                break
            try:
                parsed = parser(record)
            except Exception as exc:  # noqa: BLE001 - one bad record never kills the sync
                logger.debug(f"Skipping malformed {name} record: {exc}")
                continue
            if parsed:
                collected.append(parsed)
        skip += page_size
        page += 1
        if page % 10 == 0:
            logger.info(f"  {name}: {len(existing) + len(collected)} records...")
            meta_file.write_text(
                json.dumps({"next_skip": skip, "fetched": skip}), encoding="utf-8")
        time.sleep(sleep_seconds)

    merged = existing + collected
    if merged or (not out_file.exists() and isinstance(saw_total, int) and saw_total > 0):
        seen_ids = set()
        deduped: List[Dict[str, Any]] = []
        for record in merged:
            record_id = record.get("id") if isinstance(record, dict) else None
            if record_id is not None:
                if record_id in seen_ids:
                    continue
                seen_ids.add(record_id)
            deduped.append(record)
        out_file.write_text(
            json.dumps(deduped, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")
        meta_file.write_text(
            json.dumps({"next_skip": skip, "fetched": skip}), encoding="utf-8")
        logger.info(f"Cached {len(deduped)} {name} to {out_file}")
    elif not merged and saw_total == 0:
        logger.warning(f"{name}: this mirror reports 0 records; nothing cached")
    else:
        logger.warning(f"{name}: sync produced nothing; keeping existing {out_file}")
    return merged


def _parse_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item.get("id"),
        "type_id": item.get("typeId", 0),
        "level": item.get("level", 0),
        "name": _loc(item.get("name")),
        "description": _loc(item.get("description")),
        "price": item.get("price", 0),
        "item_set_id": item.get("itemSetId", -1),
        "is_saleable": bool(item.get("isSaleable", False)),
        "is_legendary": bool(item.get("isLegendary", False)),
        "drop_monster_ids": item.get("dropMonsterIds") or [],
        "recipe_ids": item.get("recipeIds") or [],
        "effects": [
            {
                "effect_id": e.get("effectId"),
                "value": e.get("value"),
                "dice_num": e.get("diceNum"),
                "dice_side": e.get("diceSide"),
            }
            for e in (item.get("possibleEffects") or [])
            if isinstance(e, dict)
        ][:16],
    }


def _parse_quest(quest: Dict[str, Any]) -> Dict[str, Any]:
    steps: List[Dict[str, Any]] = []
    for step in quest.get("steps") or []:
        if not isinstance(step, dict):
            continue
        objectives: List[Dict[str, Any]] = []
        for objective in step.get("objectives") or []:
            if not isinstance(objective, dict):
                continue
            params = objective.get("parameters") or {}
            objectives.append({
                "id": objective.get("id"),
                "type_id": objective.get("typeId"),
                "text": _loc(objective.get("text")),
                "map_id": objective.get("mapId", 0),
                "coords": objective.get("coords"),
                "params": {
                    key: params.get(key)
                    for key in ("parameter0", "parameter1")
                    if params.get(key)
                },
            })
        steps.append({
            "id": step.get("id"),
            "name": _loc(step.get("name")),
            "description": _loc(step.get("description")),
            "optimal_level": step.get("optimalLevel", 0),
            "objectives": objectives,
        })
    return {
        "id": quest.get("id"),
        "name": _loc(quest.get("name")),
        "category_id": quest.get("categoryId", 0),
        "level_min": quest.get("levelMin", 0),
        "level_max": quest.get("levelMax", 0),
        "is_dungeon": bool(quest.get("isDungeonQuest", False)),
        "is_event": bool(quest.get("isEvent", False)),
        "is_party": bool(quest.get("isPartyQuest", False)),
        "start_criterion": quest.get("startCriterion", ""),
        "start_position": quest.get("startPosition") or [],
        "steps": steps,
    }


def _parse_npc(npc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": npc.get("id"),
        "name": _loc(npc.get("name")),
        "gender": npc.get("gender", 0),
        "actions": npc.get("actions") or [],
        "dialog_ids": [
            d.get("id")
            for d in (npc.get("dialogData") or [])
            if isinstance(d, dict)
        ][:64],
    }


def _parse_achievement(achievement: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": achievement.get("id"),
        "name": _loc(achievement.get("name")),
        "description": _loc(achievement.get("description")),
        "points": achievement.get("points", 0),
        "level": achievement.get("level", 0),
        "category_id": achievement.get("categoryId", 0),
        "objective_ids": achievement.get("objectiveIds") or [],
        "reward_ids": achievement.get("rewardIds") or [],
    }


def _parse_dungeon(dungeon: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": dungeon.get("id"),
        "name": _loc(dungeon.get("name")),
        "level_min": dungeon.get("minLevel", 0),
        "optimal_level": dungeon.get("optimalPlayerLevel", 0),
        "entrance_map_id": dungeon.get("entranceMapId", 0),
        "exit_map_id": dungeon.get("exitMapId", 0),
        "map_ids": dungeon.get("mapIds") or [],
        "boss_monster_ids": dungeon.get("bosses") or [],
        "monster_ids": dungeon.get("monsters") or [],
        "required_objects": dungeon.get("requiredObjects") or [],
    }


_HEAVY_KEYS = ("cells", "atlasLayout", "graphicsPositions", "mapPositions", "compressedCells")
_META_KEYS = ("_id", "m_id", "m_flags", "className", "createdAt", "updatedAt")


def _parse_raw_lite(record: Dict[str, Any]) -> Dict[str, Any]:
    """Generic parser for reference collections (maps/subareas/areas).

    Keeps every field except the heavy map-geometry blobs and Mongo metadata,
    so the cache stays small while remaining forward-compatible with whatever
    extra fields the API exposes. Localised fields are still normalised.
    """
    parsed: Dict[str, Any] = {}
    for key, value in record.items():
        if key in _HEAVY_KEYS or key in _META_KEYS:
            continue
        if key in ("name", "description", "slug") and isinstance(value, dict):
            parsed[key] = _loc(value)
        else:
            parsed[key] = value
    return parsed


def sync_items(max_items: int = 25000, resume: bool = False) -> List[Dict[str, Any]]:
    return sync_collection("items", "/items", _parse_item, ITEMS_CACHE_FILE, max_items, resume=resume)


def sync_quests(max_quests: int = 3000, resume: bool = False) -> List[Dict[str, Any]]:
    return sync_collection("quests", "/quests", _parse_quest, QUESTS_CACHE_FILE, max_quests, resume=resume)


def sync_npcs(max_npcs: int = 8000, resume: bool = False) -> List[Dict[str, Any]]:
    return sync_collection("npcs", "/npcs", _parse_npc, NPCS_CACHE_FILE, max_npcs, resume=resume)


def sync_achievements(max_achievements: int = 4000, resume: bool = False) -> List[Dict[str, Any]]:
    return sync_collection(
        "achievements", "/achievements", _parse_achievement,
        ACHIEVEMENTS_CACHE_FILE, max_achievements, resume=resume)


def sync_dungeons(max_dungeons: int = 500, resume: bool = False) -> List[Dict[str, Any]]:
    return sync_collection(
        "dungeons", "/dungeons", _parse_dungeon, DUNGEONS_CACHE_FILE, max_dungeons, resume=resume)


def sync_maps(max_maps: int = 30000, resume: bool = False) -> List[Dict[str, Any]]:
    return sync_collection(
        "maps", "/maps", _parse_raw_lite, MAPS_CACHE_FILE, max_maps, resume=resume)


def sync_subareas(max_subareas: int = 3000, resume: bool = False) -> List[Dict[str, Any]]:
    return sync_collection(
        "subareas", "/subareas", _parse_raw_lite, SUBAREAS_CACHE_FILE, max_subareas, resume=resume)


def sync_areas(max_areas: int = 1000, resume: bool = False) -> List[Dict[str, Any]]:
    return sync_collection("areas", "/areas", _parse_raw_lite, AREAS_CACHE_FILE, max_areas, resume=resume)


COLLECTIONS: Dict[str, Callable[..., List[Dict[str, Any]]]] = {
    "monsters": sync_monsters,
    "spells": sync_core_spells,
    "items": sync_items,
    "quests": sync_quests,
    "npcs": sync_npcs,
    "achievements": sync_achievements,
    "dungeons": sync_dungeons,
    "maps": sync_maps,
    "subareas": sync_subareas,
    "areas": sync_areas,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync_full_database",
        description="Sync the DofusDB encyclopedia into dofus-network/data/cache.",
    )
    parser.add_argument(
        "target", nargs="?", default=None,
        help="Collection to sync (items, quests, npcs, achievements, dungeons, maps, "
             "subareas, areas, monsters, spells) or 'all'. Omitted: legacy behaviour "
             "(monsters + spells with --limit).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max records (legacy default: 200, full syncs use the per-collection default)")
    parser.add_argument("--all", action="store_true",
                        help="Sync every collection with its default cap")
    parser.add_argument("--resume", action="store_true",
                        help="Continue an interrupted full sync instead of restarting it")
    return parser


def _call_collection(name: str, limit: int, resume: bool) -> None:
    """Call a collection sync, forwarding ``resume`` only to v2 collections."""
    if name in ("monsters", "spells"):
        COLLECTIONS[name](limit)
    else:
        COLLECTIONS[name](limit, resume=resume)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.all:
        for name in COLLECTIONS:
            try:
                _call_collection(name, COLLECTIONS_DEFAULT_LIMITS.get(name, 100000), args.resume)
            except Exception as exc:  # noqa: BLE001 - one collection must not kill the rest
                logger.warning(f"{name} sync failed (continuing): {exc}")
        return 0

    target = args.target
    if target is None:
        # Legacy invocation: monsters + spells only, default limit 200.
        legacy_limit = args.limit if args.limit is not None else 200
        sync_monsters(max_monsters=legacy_limit)
        sync_core_spells(max_spells=legacy_limit)
        return 0

    if target not in COLLECTIONS:
        logger.error(f"Unknown collection '{target}'. Known: {', '.join(COLLECTIONS)}")
        return 2

    limit = args.limit if args.limit is not None else COLLECTIONS_DEFAULT_LIMITS.get(target, 200)
    _call_collection(target, limit, args.resume)
    return 0


COLLECTIONS_DEFAULT_LIMITS: Dict[str, int] = {
    "monsters": 6000,
    "spells": 18000,
    "items": 25000,
    "quests": 3000,
    "npcs": 8000,
    "achievements": 4000,
    "dungeons": 500,
    "maps": 30000,
    "subareas": 3000,
    "areas": 1000,
}


if __name__ == "__main__":
    raise SystemExit(main())
