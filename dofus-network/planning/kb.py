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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
        self._quest_sorted = np.sort(self.quest_id)
        self._quest_order = np.argsort(self.quest_id).astype(np.int32)

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

    def _name_hits(self, needle: str, ids: Any, table: str,
                   lang: str) -> List[Tuple[int, int, int]]:
        from ai.data_splits import normalize

        offsets = self._arrays[f"{table}_off"]
        lengths = self._arrays[f"{table}_len"]
        hits: List[Tuple[int, int, int]] = []
        for index in range(len(ids)):
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
            hits.append((rank, int(ids[index]), index))
        hits.sort()
        return hits

    def find_items(self, query: str, lang: str = "fr", limit: int = 8) -> List[Item]:
        from ai.data_splits import normalize

        needle = normalize(query)
        if len(needle) < 2:
            return []
        hits = self._name_hits(needle, self.item_id, "item_name", lang)
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

    def _quest_name_index(self) -> Dict[str, Dict[str, List[int]]]:
        """``{lang: {normalised name: [quest ids]}}``, built on first use.

        ``find_quests`` is called once per name-bearing row when a corpus is
        sanitised, and a linear scan over 1,976 quests per call made that pass
        take four minutes. An exact-name index makes the common case O(1); the
        ranking scan below still handles prefixes and containment.

        The language list comes from ``LANGS``, the same constant the name blob
        was built with. It used to be read back from the ``langs`` array, which
        stores ``NAME_SEP.join(LANGS)`` as raw bytes -- ``tolist()`` there yields
        integers, so the decoded list was nonsense and the index silently
        covered French only. ``quests_named_in`` needs every language, because a
        goal in German names a quest by its German name.
        """
        cached = getattr(self, "_quest_name_index_cache", None)
        if cached is not None:
            return cached
        from ai.data_splits import normalize

        index: Dict[str, Dict[str, List[int]]] = {}
        offsets = self._arrays["quest_name_off"]
        lengths = self._arrays["quest_name_len"]
        for lang in LANGS:
            table: Dict[str, List[int]] = {}
            for position in range(len(self.quest_id)):
                name = self._names.get(int(offsets[position]),
                                       int(lengths[position]), lang)
                flat = normalize(name)
                if flat:
                    table.setdefault(flat, []).append(int(self.quest_id[position]))
            index[lang] = table
        self._quest_name_index_cache = index  # type: ignore[attr-defined]
        return index

    def quests_named_in(self, goal: str, lang: str = "fr") -> List[Tuple[str, int]]:
        """``(name, quest_id)`` for every quest whose name appears inside ``goal``.

        This is the reverse of ``find_quests`` and it is the direction a real
        goal needs. ``find_quests`` answers "which quest is this span?" once the
        caller has already isolated the span; a goal is a whole sentence --
        ``mach mit Quest Ghulsaison weiter``, ``fais l'etape suivante de la
        quete Offrande`` -- and no amount of lead-verb stripping reliably
        recovers the span from it, because the quest noun can sit anywhere in
        the sentence and the name can contain commas and apostrophes.

        Scanning the *other* way -- for each known name, ask whether it occurs
        in the goal at a word boundary -- needs no span extraction at all and
        cannot be fooled by a verb phrase. It is also how a player reads it.

        The result is a candidate list, not an answer: the caller applies the
        longest-match rule and refuses when two quests share it. Maximal munch
        is a tokenisation principle, not a guess -- when the goal says "Rose a
        lys", the quest literally named "Rose a lys" is the one it means.
        """
        from ai.data_splits import bounded_match, normalize

        folded = normalize(goal)
        if not folded:
            return []
        found: List[Tuple[str, int]] = []
        for table_lang, table in self._quest_name_index().items():
            if lang and table_lang != lang:
                continue
            for flat, ids in table.items():
                if len(flat) < 2:
                    continue
                if bounded_match(folded, flat):
                    found.extend((flat, quest_id) for quest_id in ids)
        return found

    def resolve_quest_reference(self, goal: str, lang: str = "fr") -> Optional[int]:
        """The quest a goal names, or ``None`` when it names none or several.

        Longest match wins; ties are refused. ``None`` therefore means either
        "this goal does not name a quest" or "it names two quests equally
        well", and both must cost the caller the fill -- a resolver that picks
        one of two candidates is guessing.
        """
        candidates = self.quests_named_in(goal, lang)
        if not candidates:
            return None
        longest = max(len(name) for name, _quest_id in candidates)
        best = {quest_id for name, quest_id in candidates if len(name) == longest}
        return best.pop() if len(best) == 1 else None

    def find_quests(self, query: str, lang: str = "fr", limit: int = 8) -> List[Quest]:
        """Quests whose *name* matches ``query``, best first.

        This is the KB half of the entity-reference resolver: the model proposes
        a surface span, and this maps a name the corpus never showed to its id.
        Ranking mirrors ``find_items`` (exact, then prefix, then containment) so
        there is one matching convention in the KB rather than two.

        The caller must still decide what to do with ambiguity -- resolving a
        quest id from several ranked hits is a guess, and the post-processor
        refuses to guess.
        """
        from ai.data_splits import normalize

        needle = normalize(query)
        if len(needle) < 2:
            return []
        exact = self._quest_name_index().get(lang, {}).get(needle)
        if exact is not None:
            return [self.quest(quest_id, lang) for quest_id in exact[:limit]]
        hits = self._name_hits(needle, self.quest_id, "quest_name", lang)
        return [self.quest(int(quest_id), lang) for _rank, quest_id, _index in hits[:limit]]  # type: ignore[misc]

    def quest(self, quest_id: int, lang: str = "fr") -> Optional[Quest]:
        """One quest by id, or ``None``."""
        index = self._lookup(self._quest_sorted, self._quest_order, quest_id)
        if index is None:
            return None
        return Quest(
            id=int(self.quest_id[index]),
            level_min=int(self._arrays["quest_level_min"][index]),
            level_max=int(self._arrays["quest_level_max"][index]),
            is_dungeon=bool(self._arrays["quest_dungeon"][index]),
            name=self._names.get(
                int(self._arrays["quest_name_off"][index]),
                int(self._arrays["quest_name_len"][index]), lang),
        )

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
