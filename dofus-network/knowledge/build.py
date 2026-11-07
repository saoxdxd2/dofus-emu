"""Build the canonical knowledge store from the cached French data.

The build is a transaction: write a temporary SQLite file, populate it, then
replace the live file in one step. A failed build never leaves a half-written
store behind.

Run:
    python -m knowledge.build
    python -m knowledge.build --cache data/cache --out data/knowledge/dofus_knowledge.sqlite
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .schema import SCHEMA, SCHEMA_VERSION

LANGUAGES = ("fr", "en", "es", "de", "pt")
SOURCE = "dofusdb_cache"


def _cache_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "cache"


def _default_out(cache: Path) -> Path:
    return cache.parent / "knowledge" / "dofus_knowledge.sqlite"


def _version(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).strftime("%Y-%m-%d")
    except OSError:
        return None


def _load_list(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in (
            "data", "items", "quests", "monsters", "npcs",
            "achievements", "subareas", "areas",
        ):
            value = data.get(key)
            if isinstance(value, list):
                return value
        values = [v for v in data.values() if isinstance(v, dict)]
        if values and all("id" in v for v in values):
            return values
    raise ValueError(f"{path}: expected a list or an id-keyed object")


def _localized(record: dict[str, Any], key: str = "name") -> dict[str, str]:
    value = record.get(key)
    if isinstance(value, dict):
        return {lang: value[lang] for lang in LANGUAGES if value.get(lang)}
    if isinstance(value, str):
        return {"fr": value}
    parts: dict[str, str] = {}
    for lang in LANGUAGES:
        v = record.get(f"{key}_{lang}")
        if v:
            parts[lang] = str(v)
    return parts


def _name_columns(names: dict[str, str]) -> tuple:
    return tuple(names.get(lang) for lang in LANGUAGES)


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _insert_many(
    con: sqlite3.Connection, sql: str, rows: Iterable[tuple], chunk: int = 2000
) -> int:
    batch: list[tuple] = []
    count = 0
    for row in rows:
        batch.append(row)
        if len(batch) >= chunk:
            con.executemany(sql, batch)
            count += len(batch)
            batch.clear()
    if batch:
        con.executemany(sql, batch)
        count += len(batch)
    return count


def build_store(
    cache_dir: str | Path | None = None,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    cache = Path(cache_dir) if cache_dir else _cache_dir()
    out = Path(out_path) if out_path else _default_out(cache)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    if tmp.exists():
        tmp.unlink()

    counts: dict[str, int] = {}
    versions: dict[str, str | None] = {}
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)

        quest_path = cache / "quests_full.json"
        quests = _load_list(quest_path)
        versions["quests"] = _version(quest_path)

        entity_rows: list[tuple] = []
        quest_rows: list[tuple] = []
        step_rows: list[tuple] = []
        objective_rows: list[tuple] = []
        seen_objectives: set[int] = set()
        duplicates = 0

        for quest in quests:
            qid = int(quest["id"])
            names = _localized(quest, "name")
            entity_rows.append(
                ("quest", qid, *_name_columns(names), SOURCE,
                 versions["quests"], 0, _json({"level_min": quest.get("level_min")}))
            )
            quest_rows.append(
                (
                    qid, _json(names), quest.get("level_min"), quest.get("level_max"),
                    quest.get("category_id"), int(bool(quest.get("is_dungeon"))),
                    int(bool(quest.get("is_event"))), int(bool(quest.get("is_party"))),
                    _json(quest.get("start_position")), SOURCE, versions["quests"], 0,
                )
            )
            for step_index, step in enumerate(quest.get("steps") or []):
                step_rows.append(
                    (
                        qid, step_index, step.get("id"),
                        _json(_localized(step, "name")),
                        _json(_localized(step, "description")),
                        step.get("optimal_level"), SOURCE,
                    )
                )
                for objective_index, obj in enumerate(step.get("objectives") or []):
                    oid = obj.get("id")
                    if oid is None:
                        continue
                    oid = int(oid)
                    if oid in seen_objectives:
                        duplicates += 1
                        continue
                    seen_objectives.add(oid)
                    objective_rows.append(
                        (
                            oid, qid, step_index, objective_index, obj.get("type_id"),
                            _json(obj.get("text")), obj.get("map_id"),
                            _json(obj.get("coords")), _json(obj.get("params")), SOURCE,
                        )
                    )

        achievement_path = cache / "achievements_full.json"
        achievements = _load_list(achievement_path)
        versions["achievements"] = _version(achievement_path)
        achievement_rows: list[tuple] = []
        achievement_objective_rows: list[tuple] = []
        for ach in achievements:
            aid = int(ach["id"])
            names = _localized(ach, "name")
            entity_rows.append(
                ("achievement", aid, *_name_columns(names), SOURCE,
                 versions["achievements"], 0, None)
            )
            achievement_rows.append(
                (
                    aid, _json(names), _json(_localized(ach, "description")),
                    ach.get("points"), ach.get("level"), ach.get("category_id"), SOURCE,
                )
            )
            for oid in ach.get("objective_ids") or []:
                achievement_objective_rows.append(
                    (aid, int(oid), None, SOURCE)
                )

        monster_path = cache / "monsters_full.json"
        monsters = _load_list(monster_path)
        versions["monsters"] = _version(monster_path)
        monster_rows: list[tuple] = []
        for monster in monsters:
            mid = int(monster["id"])
            names = _localized(monster, "name")
            entity_rows.append(
                ("monster", mid, *_name_columns(names), SOURCE,
                 versions["monsters"], 0, None)
            )
            monster_rows.append(
                (mid, _json(names), int(bool(monster.get("is_boss"))),
                 _json(monster.get("grades")), SOURCE)
            )

        npc_path = cache / "npcs_full.json"
        npcs = _load_list(npc_path)
        versions["npcs"] = _version(npc_path)
        npc_rows: list[tuple] = []
        for npc in npcs:
            nid = int(npc["id"])
            names = _localized(npc, "name")
            entity_rows.append(
                ("npc", nid, *_name_columns(names), SOURCE,
                 versions["npcs"], 0, None)
            )
            npc_rows.append(
                (nid, _json(names), _json(npc.get("actions")),
                 _json(npc.get("dialog_ids")), SOURCE)
            )

        item_path = cache / "items_full.json"
        items = _load_list(item_path)
        versions["items"] = _version(item_path)
        item_rows: list[tuple] = []
        for item in items:
            iid = int(item["id"])
            names = _localized(item, "name")
            entity_rows.append(
                ("item", iid, *_name_columns(names), SOURCE,
                 versions["items"], 0, None)
            )
            item_rows.append(
                (
                    iid, item.get("type_id"), item.get("level"), _json(names),
                    _json(_localized(item, "description")), item.get("price"),
                    item.get("item_set_id"),
                    None if item.get("is_saleable") is None
                    else int(bool(item.get("is_saleable"))),
                    None if item.get("is_legendary") is None
                    else int(bool(item.get("is_legendary"))),
                    SOURCE,
                )
            )

        map_names: dict[int, dict[str, str]] = {}
        for filename, kind in (("subareas_full.json", "subarea"),
                               ("areas_full.json", "area")):
            path = cache / filename
            rows = _load_list(path)
            versions[kind] = _version(path)
            for row in rows:
                rid = int(row["id"])
                names = _localized(row, "name")
                entity_rows.append(
                    (kind, rid, *_name_columns(names), SOURCE,
                     versions[kind], 0, None)
                )
                if kind == "subarea":
                    for map_id in row.get("mapIds") or []:
                        map_names.setdefault(int(map_id), names)
        for map_id, names in map_names.items():
            entity_rows.append(
                ("map", map_id, *_name_columns(names), "subarea_map_index",
                 versions.get("subarea"), 0,
                 _json({"name_from": "subarea"}))
            )

        counts["entities"] = _insert_many(
            con,
            "INSERT OR REPLACE INTO entities "
            "(kind, entity_id, name_fr, name_en, name_es, name_de, name_pt, "
            " source, source_version, verified_live, data_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            entity_rows,
        )
        counts["quests"] = _insert_many(
            con,
            "INSERT OR REPLACE INTO quests "
            "(quest_id, name_json, level_min, level_max, category_id, is_dungeon, "
            " is_event, is_party, start_json, source, source_version, verified_live) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            quest_rows,
        )
        counts["quest_steps"] = _insert_many(
            con,
            "INSERT OR REPLACE INTO quest_steps "
            "(quest_id, step_index, step_id, name_json, description_json, "
            " optimal_level, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            step_rows,
        )
        counts["quest_objectives"] = _insert_many(
            con,
            "INSERT OR IGNORE INTO quest_objectives "
            "(objective_id, quest_id, step_index, objective_index, type_id, "
            " text_json, map_id, coords_json, params_json, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            objective_rows,
        )
        counts["achievements"] = _insert_many(
            con,
            "INSERT OR REPLACE INTO achievements "
            "(achievement_id, name_json, description_json, points, level, "
            " category_id, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            achievement_rows,
        )
        counts["achievement_objectives"] = _insert_many(
            con,
            "INSERT OR REPLACE INTO achievement_objectives "
            "(achievement_id, objective_id, details_json, source) "
            "VALUES (?, ?, ?, ?)",
            achievement_objective_rows,
        )
        counts["monsters"] = _insert_many(
            con,
            "INSERT OR REPLACE INTO monsters "
            "(monster_id, name_json, is_boss, grades_json, source) "
            "VALUES (?, ?, ?, ?, ?)",
            monster_rows,
        )
        counts["npcs"] = _insert_many(
            con,
            "INSERT OR REPLACE INTO npcs "
            "(npc_id, name_json, actions_json, dialog_ids_json, source) "
            "VALUES (?, ?, ?, ?, ?)",
            npc_rows,
        )
        counts["items"] = _insert_many(
            con,
            "INSERT OR REPLACE INTO items "
            "(item_id, type_id, level, name_json, description_json, price, "
            " item_set_id, is_saleable, is_legendary, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            item_rows,
        )
        counts["duplicate_objective_ids"] = duplicates

        meta = {
            "schema_version": str(SCHEMA_VERSION),
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": SOURCE,
            "cache_dir": str(cache.resolve()),
        }
        for key, value in versions.items():
            meta[f"source_version.{key}"] = value or ""
        for key, value in counts.items():
            meta[f"count.{key}"] = str(value)
        con.executemany(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            list(meta.items()),
        )
        con.commit()
    except Exception:
        con.close()
        if tmp.exists():
            tmp.unlink()
        raise
    con.close()

    # Windows will not replace a file that is still open; the connection is
    # closed above, so the swap is a single atomic rename.
    os.replace(tmp, out)
    return {"path": str(out), **counts, "versions": versions}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    result = build_store(args.cache, args.out)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
