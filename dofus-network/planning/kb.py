"""Compact, RAM-first knowledge base for the planner.

The problem
-----------
``data/cache/*.json`` is 40 MB on disk. Loading it with ``json.load`` costs
**121 MB of RSS** (measured: 3.0x overhead — every record becomes a dict, every
string a Python object, every number a boxed int). That alone exceeds the whole
150 MB agent budget before a single model is loaded.

What this module does instead
-----------------------------
Packs the same information into flat typed arrays plus one UTF-8 name blob:

    items      21,554 x (id, level, price, name_ref, recipe_ref, drop_ref)
    recipes     ~25k int32 edges (item -> ingredient)
    drops       ~11k int32 edges (item -> monster)
    monsters    2,000 x (id, level, hp, name_ref)
    quests      1,976 x (id, level_min, level_max, is_dungeon, name_ref)
    names       one bytes blob, "\x1f"-separated localisations

That is ~4 MB resident for the same queries, and it loads from a single ``.npz``
in ~40 ms. Strings are decoded on demand, never stored as objects.

Everything the planner needs is an integer lookup, which is also why a 30M
encoder is enough for the language part: the world model is *not* in the model.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "data" / "cache"
DEFAULT_KB = REPO_ROOT / "data" / "kb" / "dofus_kb.npz"
DEFAULT_NAMES = REPO_ROOT / "data" / "kb" / "dofus_names.bin"

LANGS = ("fr", "en", "es", "de", "pt")
NAME_SEP = "\x1f"


def _localised(record: Dict[str, Any], lang_order: Sequence[str] = LANGS) -> str:
    name = record.get("name")
    parts: List[str] = []
    if isinstance(name, dict):
        for lang in lang_order:
            value = name.get(lang)
            parts.append(value.strip() if isinstance(value, str) else "")
    elif isinstance(name, str):
        parts.append(name.strip())
    while len(parts) < len(lang_order):
        parts.append("")
    for idx, lang in enumerate(lang_order):
        if not parts[idx]:
            flat = record.get(f"name_{lang}")
            if isinstance(flat, str):
                parts[idx] = flat.strip()
    return NAME_SEP.join(parts)


class _NameTable:
    """Append-only UTF-8 blob; entries addressed by (offset, length)."""

    __slots__ = ("blob",)

    def __init__(self) -> None:
        self.blob = bytearray()

    def add(self, text: str) -> Tuple[int, int]:
        offset = len(self.blob)
        raw = text.encode("utf-8")
        self.blob.extend(raw)
        return offset, len(raw)

    def get(self, offset: int, length: int, lang: str = "fr") -> str:
        if length <= 0:
            return ""
        text = self.blob[offset:offset + length].decode("utf-8", "replace")
        parts = text.split(NAME_SEP)
        try:
            index = LANGS.index(lang)
        except ValueError:
            index = 0
        if index < len(parts) and parts[index]:
            return parts[index]
        for part in parts:
            if part:
                return part
        return ""


@dataclass
class Item:
    __slots__ = ("id", "level", "price", "name", "recipe", "drops")

    id: int
    level: int
    price: int
    name: str
    recipe: List[int]
    drops: List[int]


@dataclass
class Monster:
    __slots__ = ("id", "level", "hp", "name", "is_boss")

    id: int
    level: int
    hp: int
    name: str
    is_boss: bool


@dataclass
class Quest:
    __slots__ = ("id", "level_min", "level_max", "is_dungeon", "name")

    id: int
    level_min: int
    level_max: int
    is_dungeon: bool
    name: str


class KnowledgeBase:
    """Typed-array knowledge base. Build once, load in ~4 MB."""

    def __init__(self, path: Path = DEFAULT_KB, names_path: Path = DEFAULT_NAMES) -> None:
        blob = np.load(path)
        self._arrays: Dict[str, np.ndarray] = {key: blob[key] for key in blob.files}
        self._names = _NameTable()
        self._names.blob = bytearray(names_path.read_bytes())

        self.item_id = self._arrays["item_id"]
        self.monster_id = self._arrays["monster_id"]
        self.quest_id = self._arrays["quest_id"]

        # Sorted-id lookup instead of a Python dict: 21,554 dict entries cost
        # ~2.5 MB, two int32/int64 arrays cost ~260 KB and search in O(log n).
        self._item_sorted = np.sort(self.item_id)
        self._item_order = np.argsort(self.item_id).astype(np.int32)
        self._monster_sorted = np.sort(self.monster_id)
        self._monster_order = np.argsort(self.monster_id).astype(np.int32)

    @staticmethod
    def _lookup(sorted_ids: np.ndarray, order: np.ndarray, value: int) -> Optional[int]:
        position = int(np.searchsorted(sorted_ids, value))
        if position < len(sorted_ids) and int(sorted_ids[position]) == int(value):
            return int(order[position])
        return None

    # -- build ---------------------------------------------------------------

    @classmethod
    def build(
        cls,
        cache_dir: Path = CACHE_DIR,
        out: Path = DEFAULT_KB,
        names_out: Path = DEFAULT_NAMES,
    ) -> Dict[str, Any]:
        out.parent.mkdir(parents=True, exist_ok=True)
        names = _NameTable()

        items = json.loads((cache_dir / "items_full.json").read_text(encoding="utf-8"))
        monsters = json.loads((cache_dir / "monsters_full.json").read_text(encoding="utf-8"))
        quests = json.loads((cache_dir / "quests_full.json").read_text(encoding="utf-8"))

        item_id: List[int] = []
        item_level: List[int] = []
        item_price: List[int] = []
        item_name_ref: List[Tuple[int, int]] = []
        recipe_off: List[int] = []
        recipe_len: List[int] = []
        drop_off: List[int] = []
        drop_len: List[int] = []
        recipe_edges: List[int] = []
        drop_edges: List[int] = []

        for record in items:
            if not isinstance(record, dict):
                continue
            item_id.append(int(record.get("id") or 0))
            item_level.append(int(record.get("level") or 0))
            price = record.get("price")
            item_price.append(int(price) if isinstance(price, (int, float)) else 0)
            item_name_ref.append(names.add(_localised(record)))

            recipe = [int(x) for x in (record.get("recipe_ids") or []) if isinstance(x, (int, float))]
            recipe_off.append(len(recipe_edges))
            recipe_len.append(len(recipe))
            recipe_edges.extend(recipe)

            drops = [int(x) for x in (record.get("drop_monster_ids") or [])
                     if isinstance(x, (int, float))]
            drop_off.append(len(drop_edges))
            drop_len.append(len(drops))
            drop_edges.extend(drops)

        monster_id: List[int] = []
        monster_level: List[int] = []
        monster_hp: List[int] = []
        monster_boss: List[int] = []
        monster_name_ref: List[Tuple[int, int]] = []
        for record in monsters:
            if not isinstance(record, dict):
                continue
            grades = record.get("grades") or []
            level = 0
            hp = 0
            if grades and isinstance(grades[0], dict):
                level = int(grades[0].get("level") or 0)
                hp = int(grades[0].get("life_points") or 0)
            monster_id.append(int(record.get("id") or 0))
            monster_level.append(level)
            monster_hp.append(hp)
            monster_boss.append(1 if record.get("is_boss") else 0)
            monster_name_ref.append(names.add(_localised(record)))

        quest_id: List[int] = []
        quest_min: List[int] = []
        quest_max: List[int] = []
        quest_dungeon: List[int] = []
        quest_name_ref: List[Tuple[int, int]] = []
        for record in quests:
            if not isinstance(record, dict):
                continue
            quest_id.append(int(record.get("id") or 0))
            quest_min.append(int(record.get("level_min") or 0))
            quest_max.append(int(record.get("level_max") or 0))
            quest_dungeon.append(1 if record.get("is_dungeon") else 0)
            quest_name_ref.append(names.add(_localised(record)))

        def refs(pairs: Sequence[Tuple[int, int]], which: int) -> np.ndarray:
            return np.asarray([p[which] for p in pairs], dtype=np.int32)

        payload = {
            "item_id": np.asarray(item_id, dtype=np.int32),
            "item_level": np.asarray(item_level, dtype=np.int16),
            "item_price": np.asarray(item_price, dtype=np.int32),
            "item_name_off": refs(item_name_ref, 0),
            "item_name_len": refs(item_name_ref, 1).astype(np.int16),
            "item_recipe_off": np.asarray(recipe_off, dtype=np.int32),
            "item_recipe_len": np.asarray(recipe_len, dtype=np.int16),
            "item_drop_off": np.asarray(drop_off, dtype=np.int32),
            "item_drop_len": np.asarray(drop_len, dtype=np.int16),
            "recipe_edges": np.asarray(recipe_edges, dtype=np.int32),
            "drop_edges": np.asarray(drop_edges, dtype=np.int32),
            "monster_id": np.asarray(monster_id, dtype=np.int32),
            "monster_level": np.asarray(monster_level, dtype=np.int16),
            "monster_hp": np.asarray(monster_hp, dtype=np.int32),
            "monster_boss": np.asarray(monster_boss, dtype=np.int8),
            "monster_name_off": refs(monster_name_ref, 0),
            "monster_name_len": refs(monster_name_ref, 1).astype(np.int16),
            "quest_id": np.asarray(quest_id, dtype=np.int32),
            "quest_level_min": np.asarray(quest_min, dtype=np.int16),
            "quest_level_max": np.asarray(quest_max, dtype=np.int16),
            "quest_dungeon": np.asarray(quest_dungeon, dtype=np.int8),
            "quest_name_off": refs(quest_name_ref, 0),
            "quest_name_len": refs(quest_name_ref, 1).astype(np.int16),
            "langs": np.frombuffer(NAME_SEP.join(LANGS).encode("utf-8"), dtype=np.uint8),
        }
        np.savez_compressed(out, **payload)
        names_out.write_bytes(bytes(names.blob))

        return {
            "items": len(item_id),
            "recipes": len(recipe_edges),
            "drops": len(drop_edges),
            "monsters": len(monster_id),
            "quests": len(quest_id),
            "npz_bytes": out.stat().st_size,
            "names_bytes": names_out.stat().st_size,
            "resident_mb": round((out.stat().st_size + names_out.stat().st_size) / 1e6, 2),
        }

    # -- item access ---------------------------------------------------------

    def has_item(self, item_id: int) -> bool:
        return self._lookup(self._item_sorted, self._item_order, item_id) is not None

    def item(self, item_id: int, lang: str = "fr") -> Optional[Item]:
        index = self._lookup(self._item_sorted, self._item_order, item_id)
        if index is None:
            return None
        recipe_off = int(self._arrays["item_recipe_off"][index])
        recipe_len = int(self._arrays["item_recipe_len"][index])
        drop_off = int(self._arrays["item_drop_off"][index])
        drop_len = int(self._arrays["item_drop_len"][index])
        return Item(
            id=int(self.item_id[index]),
            level=int(self._arrays["item_level"][index]),
            price=int(self._arrays["item_price"][index]),
            name=self._names.get(
                int(self._arrays["item_name_off"][index]),
                int(self._arrays["item_name_len"][index]), lang),
            recipe=[int(v) for v in self._arrays["recipe_edges"][recipe_off:recipe_off + recipe_len]],
            drops=[int(v) for v in self._arrays["drop_edges"][drop_off:drop_off + drop_len]],
        )

    def item_level_price(self, item_id: int) -> Optional[Tuple[int, int]]:
        """(level, npc price) without decoding any name — hot path for the cost model."""
        index = self._lookup(self._item_sorted, self._item_order, item_id)
        if index is None:
            return None
        return int(self._arrays["item_level"][index]), int(self._arrays["item_price"][index])

    def find_items(self, query: str, lang: str = "fr", limit: int = 8) -> List[Item]:
        from ai.data_splits import normalize

        needle = normalize(query)
        if len(needle) < 2:
            return []
        hits: List[Tuple[int, int, int]] = []
        offsets = self._arrays["item_name_off"]
        lengths = self._arrays["item_name_len"]
        for index in range(len(self.item_id)):
            name = self._names.get(int(offsets[index]), int(lengths[index]), lang)
            flat = normalize(name)
            if not flat:
                continue
            if flat == needle:
                rank = 0
            elif flat.startswith(needle):
                rank = 1
            elif needle in flat:
                rank = 2
            else:
                continue
            hits.append((rank, int(self.item_id[index]), index))
        hits.sort()
        return [self.item(item_id, lang) for _rank, item_id, _index in hits[:limit]]  # type: ignore[misc]

    # -- monster access ------------------------------------------------------

    def monster(self, monster_id: int, lang: str = "fr") -> Optional[Monster]:
        index = self._lookup(self._monster_sorted, self._monster_order, monster_id)
        if index is None:
            return None
        return Monster(
            id=int(self.monster_id[index]),
            level=int(self._arrays["monster_level"][index]),
            hp=int(self._arrays["monster_hp"][index]),
            name=self._names.get(
                int(self._arrays["monster_name_off"][index]),
                int(self._arrays["monster_name_len"][index]), lang),
            is_boss=bool(self._arrays["monster_boss"][index]),
        )

    def monsters_for_item(self, item_id: int, lang: str = "fr") -> List[Monster]:
        item = self.item(item_id, lang)
        if item is None:
            return []
        found = [self.monster(mid, lang) for mid in item.drops]
        return [m for m in found if m is not None]

    # -- quest access --------------------------------------------------------

    def quests_for_level(self, level: int, dungeon_only: bool = False,
                         limit: int = 20, lang: str = "fr") -> List[Quest]:
        mins = self._arrays["quest_level_min"]
        maxs = self._arrays["quest_level_max"]
        mask = (mins <= level) & ((maxs == 0) | (maxs >= level))
        if dungeon_only:
            mask &= self._arrays["quest_dungeon"].astype(bool)
        result: List[Quest] = []
        for index in np.nonzero(mask)[0][:limit]:
            result.append(Quest(
                id=int(self.quest_id[index]),
                level_min=int(mins[index]),
                level_max=int(maxs[index]),
                is_dungeon=bool(self._arrays["quest_dungeon"][index]),
                name=self._names.get(
                    int(self._arrays["quest_name_off"][index]),
                    int(self._arrays["quest_name_len"][index]), lang),
            ))
        return result

    # -- introspection -------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {
            "items": int(len(self.item_id)),
            "monsters": int(len(self.monster_id)),
            "quests": int(len(self.quest_id)),
            "recipe_edges": int(len(self._arrays["recipe_edges"])),
            "drop_edges": int(len(self._arrays["drop_edges"])),
            "arrays_mb": round(sum(a.nbytes for a in self._arrays.values()) / 1e6, 2),
            "names_mb": round(len(self._names.blob) / 1e6, 2),
        }


def load(path: Path = DEFAULT_KB) -> KnowledgeBase:
    return KnowledgeBase(path)
