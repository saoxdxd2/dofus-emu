"""Incremental Monster Spellbook Compiler.

Reads enemy spellbooks from AppendStore records (``kind='fight_board'``) and
Dofus Touch client tables, deduplicates on
``(monster_id, grade, spell_id, min_range, max_range, global_range_63_mask,
effect_dice)``, and persists a compact, versioned
``data/cache/monster_spells_touch.npz`` that ``monster_mech.py`` loads in under
a millisecond. After every fight, ``enrich_after_fight`` folds new observed
spells into the same npz without a full rebuild.

The npz layout (version 2)
--------------------------

Each row is one observed spell at one grade:

``monster_id, grade, spell_id, min_range, max_range, global_range_63_mask,
ap_cost, crit_probability, max_cast_per_turn, damage_count, source``

plus one ``damage_flat`` row per spell holding up to ``damage_count``
``(element_code, low, high)`` triples padded with -1.

``global_range_63_mask`` is 1 when the spell's published max range is 63
(the client's sentinel for "any range") -- such spells are excluded from the
damaging-reach pool exactly like ``Burst of Motivation`` at range 63.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from storage.append_store import AppendStore

logger = logging.getLogger("dofus.combat.spellbook")

SPELLBOOK_VERSION = 2
TOUCH_CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "cache" / "monster_spells_touch.npz"

GLOBAL_RANGE = 63

ELEMENTS = ("neutral", "earth", "fire", "water", "air")

# Dofus Touch effect ids that carry damage, mirrored from
# ``combat.monster_mech.DAMAGE_EFFECTS`` (kept here because this module must not
# import ``combat.monster_mech`` -- that would pull in ``combat.threat`` via
# ``battle_solver`` and close an import cycle).
DAMAGE_EFFECT_IDS = frozenset({91, 92, 93, 94, 95, 96, 97, 98, 99, 100})

ELEMENT_BY_EFFECT_ID = {
    91: "water", 92: "earth", 93: "air", 94: "fire", 95: "neutral",   # steal
    96: "water", 97: "earth", 98: "air", 99: "fire", 100: "neutral",  # damage
}

_ELEMENT_CODE = {element: i for i, element in enumerate(ELEMENTS)}


@dataclass(frozen=True)
class SpellKey:
    """The dedup key for one spell at one grade.

    ``global_range_63_mask`` is true when max_range is the client sentinel 63.
    ``effect_dice`` is a short hex digest of the damage dice lines so two
    casts of the same spell with different observed rolls don't collide.
    """

    monster_id: int
    grade: int
    spell_id: int
    min_range: int
    max_range: int
    global_range_63_mask: bool
    effect_dice: str

    def as_tuple(self) -> Tuple[Any, ...]:
        return (self.monster_id, self.grade, self.spell_id, self.min_range,
                self.max_range, self.global_range_63_mask, self.effect_dice)


@dataclass
class SpellEntry:
    key: SpellKey
    ap_cost: int
    crit_probability: int
    max_cast_per_turn: int
    damage_lines: List[Tuple[str, int, int]]  # (element, min, max)
    observed_from: str  # "append_store" | "client_table"
    monster_level: int = 0  # actual monster level (from client table grades), 0 for spell-only
    name: str = ""  # display name of the monster, if known


def _dice_hash(damage_lines: Sequence[Tuple[str, int, int]]) -> str:
    payload = "|".join("%s:%d:%d" % (e, lo, hi)
                       for e, lo, hi in sorted(damage_lines))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _global_range_63_mask(max_range: int) -> bool:
    return max_range >= GLOBAL_RANGE


def _extract_damage_lines(spell: Dict[str, Any]) -> List[Tuple[str, int, int]]:
    """Damage lines of one spell from the client effect payload.

    Mirrors ``combat.monster_mech._live_damage`` conventions: ``dice_num`` is
    the low end, ``dice_side`` the high end, a zero side means a fixed roll, and
    an explicit positive ``value`` is used when there is no dice pair.
    """
    lines: List[Tuple[str, int, int]] = []
    for effect in spell.get("effects") or ():
        if not isinstance(effect, dict):
            continue
        eff_id = int(effect.get("effect_id") or 0)
        if eff_id not in DAMAGE_EFFECT_IDS:
            continue
        element = ELEMENT_BY_EFFECT_ID[eff_id]
        low = int(effect.get("dice_num") or 0)
        high = int(effect.get("dice_side") or 0)
        if low > 0:
            high = high if high else low
        else:
            value = int(effect.get("value") or 0)
            if value <= 0:
                continue
            low = high = value
        if low > high:
            low, high = high, low
        lines.append((element, low, high))
    return lines

def _spell_entries_from_record(record: Dict[str, Any]) -> List[SpellEntry]:
    """Turn one AppendStore fighter record into zero or more SpellEntries."""
    if not isinstance(record, dict):
        return []
    if not (record.get("team") == "enemy" or record.get("is_monster")):
        return []
    monster_id = int(record.get("monster_id") or record.get("creature_generic_id") or 0)
    if monster_id <= 0:
        return []
    grade = int(record.get("grade") or record.get("level") or 1)
    spells = record.get("spells")
    if not isinstance(spells, list) or not spells:
        return []
    entries: List[SpellEntry] = []
    for spell in spells:
        if not isinstance(spell, dict):
            continue
        spell_id = int(spell.get("id") or 0)
        if spell_id <= 0:
            continue
        damage_lines = _extract_damage_lines(spell)
        key = SpellKey(
            monster_id=monster_id, grade=grade, spell_id=spell_id,
            min_range=int(spell.get("min_range") or 0),
            max_range=int(spell.get("range") or spell.get("max_range") or 0),
            global_range_63_mask=_global_range_63_mask(
                int(spell.get("range") or spell.get("max_range") or 0)),
            effect_dice=_dice_hash(damage_lines),
        )
        entries.append(SpellEntry(
            key=key,
            ap_cost=int(spell.get("ap_cost") or 0),
            crit_probability=int(spell.get("critical_hit_probability") or 0),
            max_cast_per_turn=int(spell.get("max_cast_per_turn") or 0),
            damage_lines=damage_lines,
            observed_from="append_store",
        ))
    return entries


def _client_spell_rows(path: Path) -> List[Dict[str, Any]]:
    """Yield flat spell rows from a Dofus Touch client spell table.

    Handles ``official_class_spells.json``: ``classes → spells → levels``.
    Each row carries ``breed_id`` (the monster class), ``spell_id``, per-level
    stats, and damage lines.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for breed in data.get("classes") or ():
        if not isinstance(breed, dict):
            continue
        breed_id = int(breed.get("breed_id") or 0)
        for spell in breed.get("spells") or ():
            if not isinstance(spell, dict):
                continue
            spell_id = int(spell.get("id") or 0)
            if spell_id <= 0:
                continue
            for level in spell.get("levels") or ():
                if not isinstance(level, dict):
                    continue
                damage_lines: List[Tuple[str, int, int]] = []
                for eff_list in ("normal_effects", "critical_effects"):
                    for effect in level.get(eff_list) or ():
                        if not isinstance(effect, dict):
                            continue
                        roll = effect.get("damage") or effect.get("heal")
                        if not isinstance(roll, dict):
                            continue
                        element = str(roll.get("element") or "").lower()
                        if element not in _ELEMENT_CODE:
                            continue
                        lo = int(roll.get("min_roll") or 0)
                        hi = int(roll.get("max_roll") or 0)
                        if lo <= 0 or hi <= 0:
                            continue
                        if lo > hi:
                            lo, hi = hi, lo
                        damage_lines.append((element, lo, hi))
                rows.append({
                    "monster_id": breed_id,
                    "breed_id": breed_id,
                    "spell_id": spell_id,
                    "grade": int(level.get("level") or 1),
                    "min_range": int(level.get("min_range") or 0),
                    "max_range": int(level.get("max_range") or 0),
                    "ap_cost": int(level.get("ap_cost") or 0),
                    "critical_hit_probability": int(
                        level.get("characteristics", {}).get("critical_hit_percent", 0) or 0),
                    "max_cast_per_turn": int(
                        level.get("characteristics", {}).get("max_cast_per_turn") or 0),
                    "damage_lines": damage_lines,
                })
    return rows


def _client_monster_rows(path: Path) -> List[Dict[str, Any]]:
    """Yield flat monster rows from ``monsters_full.json``.

    Each row carries ``id``, name fields, ``is_boss``, and one grade's stats.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    rows: List[Dict[str, Any]] = []
    for m in data:
        if not isinstance(m, dict):
            continue
        mid = int(m.get("id") or 0)
        if mid <= 0:
            continue
        for g in m.get("grades") or ():
            if not isinstance(g, dict):
                continue
            rows.append({
                "id": mid,
                "monster_id": mid,
                "name_en": m.get("name_en") or "",
                "name_fr": m.get("name_fr") or "",
                "name_es": m.get("name_es") or "",
                "is_boss": bool(m.get("is_boss")),
                "grade": int(g.get("grade") or 1),
                "level": int(g.get("level") or 1),
                "life": int(g.get("life_points") or 0),
                "ap": int(g.get("action_points") or 0),
                "mp": int(g.get("movement_points") or 0),
                "neutral_res": int(g.get("neutral_res") or 0),
                "earth_res": int(g.get("earth_res") or 0),
                "fire_res": int(g.get("fire_res") or 0),
                "water_res": int(g.get("water_res") or 0),
                "air_res": int(g.get("air_res") or 0),
            })
    return rows

def compile_spellbook(
    store_paths: Optional[Sequence[Path]] = None,
    client_tables: Optional[Sequence[Path]] = None,
    output: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build the monster spellbook npz from AppendStore records and client tables.

    Client tables are loaded first so every Dofus Touch breed has its published
    spellbook; live AppendStore observations override published values on
    ``max_range`` (the field the threat model actually prices). Rows are
    deduplicated on ``SpellKey.as_tuple()``.

    Returns a small summary dict. Writes ``output`` (default
    ``data/cache/monster_spells_touch.npz``).
    """
    output = output or TOUCH_CACHE_FILE
    output.parent.mkdir(parents=True, exist_ok=True)

    deduped: Dict[Tuple[Any, ...], SpellEntry] = {}

    # 1) Client tables first (published spellbook for every breed).
    for path in list(client_tables or []):
        if path is None:
            continue
        p = Path(path)
        if not p.exists():
            continue
        name = p.name
        # monsters_full.json → monster grade rows
        if name == "monsters_full.json":
            for row in _client_monster_rows(p):
                mid = int(row["monster_id"])
                grade = int(row["grade"])
                mname = row.get("name_en") or row.get("name_fr") or row.get("name_es") or "monster_%d" % mid
                key = SpellKey(
                    monster_id=mid, grade=grade, spell_id=0,
                    min_range=0, max_range=0,
                    global_range_63_mask=False, effect_dice="")
                existing = deduped.get(key.as_tuple())
                if existing is None:
                    deduped[key.as_tuple()] = SpellEntry(
                        key=key, ap_cost=0, crit_probability=0,
                        max_cast_per_turn=0, damage_lines=[],
                        observed_from="client_table",
                        monster_level=int(row["level"]),
                        name=mname,
                    )
        # official_class_spells.json → spell level rows
        elif name == "official_class_spells.json":
            for row in _client_spell_rows(p):
                breed_id = int(row["breed_id"])
                spell_id = int(row["spell_id"])
                grade = int(row["grade"])
                damage_lines = row.get("damage_lines") or []
                key = SpellKey(
                    monster_id=breed_id, grade=grade, spell_id=spell_id,
                    min_range=int(row.get("min_range") or 0),
                    max_range=int(row.get("max_range") or 0),
                    global_range_63_mask=_global_range_63_mask(
                        int(row.get("max_range") or 0)),
                    effect_dice=_dice_hash(damage_lines),
                )
                existing = deduped.get(key.as_tuple())
                if existing is None:
                    deduped[key.as_tuple()] = SpellEntry(
                        key=key,
                        ap_cost=int(row.get("ap_cost") or 0),
                        crit_probability=int(row.get("critical_hit_probability") or 0),
                        max_cast_per_turn=int(row.get("max_cast_per_turn") or 0),
                        damage_lines=damage_lines,
                        observed_from="client_table",
                    )
        else:
            # Generic: try both parsers.
            for row in _client_monster_rows(p):
                mid = int(row["monster_id"])
                grade = int(row["grade"])
                key = SpellKey(
                    monster_id=mid, grade=grade, spell_id=0,
                    min_range=0, max_range=0,
                    global_range_63_mask=False, effect_dice="")
                existing = deduped.get(key.as_tuple())
                if existing is None:
                    deduped[key.as_tuple()] = SpellEntry(
                        key=key, ap_cost=0, crit_probability=0,
                        max_cast_per_turn=0, damage_lines=[],
                        observed_from="client_table",
                    )
            for row in _client_spell_rows(p):
                breed_id = int(row["breed_id"])
                spell_id = int(row["spell_id"])
                grade = int(row["grade"])
                damage_lines = row.get("damage_lines") or []
                key = SpellKey(
                    monster_id=breed_id, grade=grade, spell_id=spell_id,
                    min_range=int(row.get("min_range") or 0),
                    max_range=int(row.get("max_range") or 0),
                    global_range_63_mask=_global_range_63_mask(
                        int(row.get("max_range") or 0)),
                    effect_dice=_dice_hash(damage_lines),
                )
                existing = deduped.get(key.as_tuple())
                if existing is None:
                    deduped[key.as_tuple()] = SpellEntry(
                        key=key,
                        ap_cost=int(row.get("ap_cost") or 0),
                        crit_probability=int(row.get("critical_hit_probability") or 0),
                        max_cast_per_turn=int(row.get("max_cast_per_turn") or 0),
                        damage_lines=damage_lines,
                        observed_from="client_table",
                    )

    # 2) AppendStore live observations override published on max_range.
    for store_path in list(store_paths or []):
        if store_path is None:
            continue
        p = Path(store_path)
        if not p.exists():
            continue
        try:
            with AppendStore(p, payload_format="msgpack") as store:
                for _seq, _ts, _kind, record in store.iter_records(kind="fight_board"):
                    for entry in _spell_entries_from_record(record):
                        existing = deduped.get(entry.key.as_tuple())
                        if existing is None:
                            deduped[entry.key.as_tuple()] = entry
                        elif entry.key.max_range > existing.key.max_range:
                            deduped[entry.key.as_tuple()] = SpellEntry(
                                key=entry.key,
                                ap_cost=entry.ap_cost,
                                crit_probability=entry.crit_probability,
                                max_cast_per_turn=entry.max_cast_per_turn,
                                damage_lines=entry.damage_lines,
                                observed_from="append_store",
                            )
        except (OSError, ValueError, ImportError) as exc:
            logger.warning("spellbook: skipping store %s: %s", p, exc)
            continue

    # 3) Build the in-memory structures and persist them as pickled dicts
    #    inside the npz for sub-millisecond loading.
    monsters: Dict[str, Any] = {}
    levels: Dict[str, List[Dict[str, Any]]] = {}
    element_by_code = {i: e for e, i in _ELEMENT_CODE.items()}

    for entry in deduped.values():
        key = entry.key
        mid = key.monster_id
        grade = key.grade
        sid = key.spell_id
        mn = key.min_range
        mx = key.max_range
        ap = entry.ap_cost
        crit = entry.crit_probability
        max_cast = entry.max_cast_per_turn

        damage_lines: List[Tuple[str, int, int]] = []
        for element, low, high in entry.damage_lines:
            damage_lines.append((element, low, high))

        # Monster key: name-prefixed when we have a name (matches
        # ``from_live_corpus`` so ``threat._corpus_reach`` can look up by name),
        # otherwise the bare id string.
        mkey = str(mid)
        mname = entry.name or "monster_%d" % mid

        if sid == 0:
            # Monster grade-only entry (from monsters_full.json): register the
            # grade but no spell. Keyed by both id and name-prefixed alias so
            # that ``from_live_corpus``-style name lookups in ``threat`` work.
            entry_m = monsters.setdefault(
                mkey, {"name": mname, "is_boss": False, "grades": [], "spells": []})
            if not entry_m["name"] or entry_m["name"] == mname:
                entry_m["name"] = mname
            name_key = "name:%s" % mname.lower()
            if name_key != mkey and name_key not in monsters:
                monsters[name_key] = entry_m
            grow = next((g for g in entry_m["grades"] if g["grade"] == grade), None)
            if grow is None:
                grow = {"grade": grade, "level": entry.monster_level, "life": 0,
                        "ap": 0, "mp": 0,
                        "neutral_res": 0, "earth_res": 0, "fire_res": 0,
                        "water_res": 0, "air_res": 0}
                entry_m["grades"].append(grow)
            grow["ap"] = max(int(grow.get("ap", 0) or 0), ap)
        else:
            # Spell entry: register the spell under its monster and add a level row.
            entry_m = monsters.setdefault(
                mkey, {"name": mname, "is_boss": False, "grades": [], "spells": []})
            if not entry_m["name"] or entry_m["name"] == mname:
                entry_m["name"] = mname
            name_key = "name:%s" % mname.lower()
            if name_key != mkey and name_key not in monsters:
                monsters[name_key] = entry_m
            # Ensure a grade row exists for this spell entry.
            grow = next((g for g in entry_m["grades"] if g["grade"] == grade), None)
            if grow is None:
                grow = {"grade": grade, "level": entry.monster_level or grade,
                        "life": 0, "ap": ap, "mp": 0,
                        "neutral_res": 0, "earth_res": 0, "fire_res": 0,
                        "water_res": 0, "air_res": 0}
                entry_m["grades"].append(grow)
            grow["ap"] = max(int(grow.get("ap", 0) or 0), ap)
            if sid not in entry_m["spells"]:
                entry_m["spells"].append(sid)
            level_row = {
                "grade": grade, "ap_cost": ap,
                "min_range": mn, "max_range": mx,
                "crit_probability": crit,
                "max_cast_per_turn": max_cast,
                "damage": [{"element": e, "min": lo, "max": hi}
                           for e, lo, hi in damage_lines],
            }
            levels.setdefault(str(sid), []).append(level_row)

    # Persist as pickled dicts inside the npz for sub-millisecond loading.
    np.savez_compressed(
        str(output),
        version=np.array([SPELLBOOK_VERSION], dtype=np.int64),
        monsters_pkl=np.array([pickle.dumps(monsters)], dtype="bytes"),
        levels_pkl=np.array([pickle.dumps(levels)], dtype="bytes"),
    )

    return {
        "version": SPELLBOOK_VERSION,
        "entries": len(deduped),
        "output": str(output),
    }


def load_spellbook(path: Optional[Path] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load the npz and return ``(monsters, levels)`` for ``MonsterMechanics``.

    The returned shapes match what ``MonsterMechanics(monsters, levels)``
    expects: ``monsters`` keyed by id (string) with ``name``, ``is_boss``,
    ``grades`` and ``spells``; ``levels`` keyed by spell id (string) with a list
    of per-grade rows.

    The dicts are stored pre-built (pickled) inside the npz, so loading is
    sub-millisecond -- no per-row Python loop.
    """
    path = path or TOUCH_CACHE_FILE
    if not path.exists():
        raise FileNotFoundError(
            "%s is missing; run `python -m combat.monster_spellbook --write`" % path)
    with np.load(str(path), allow_pickle=False) as archive:
        if int(archive["version"][0]) != SPELLBOOK_VERSION:
            raise ValueError("spellbook npz version %s != %s"
                             % (archive["version"][0], SPELLBOOK_VERSION))
        monsters = pickle.loads(bytes(archive["monsters_pkl"][0]))
        levels = pickle.loads(bytes(archive["levels_pkl"][0]))
    return monsters, levels

def enrich_after_fight(
    store_path: Path,
    last_seq: int,
    output: Optional[Path] = None,
) -> Dict[str, Any]:
    """Incrementally fold new ``fight_board`` records into the spellbook npz.

    Reads ``store_path`` for records with ``seq > last_seq`` and kind
    ``fight_board``, merges any new spell observations, and rewrites the npz.
    Returns the new last seq and a summary.

    This is the hook the combat server calls after every fight so the threat
    model never prices with yesterday's spellbook.
    """
    output = output or TOUCH_CACHE_FILE
    store_path = Path(store_path)

    deduped: Dict[Tuple[Any, ...], SpellEntry] = {}
    if output.exists():
        try:
            monsters, levels = load_spellbook(output)
            for mid_key, entry in monsters.items():
                # Skip name-prefixed alias keys; they point to the same entry
                # object as the numeric key and would be processed twice.
                if not mid_key.isdigit():
                    continue
                mid = int(mid_key)
                for grade in entry.get("grades") or ():
                    g = int(grade.get("grade") or 1)
                    # Preserve grade-only entries (monster stats without spell links).
                    key = SpellKey(
                        monster_id=mid, grade=g, spell_id=0,
                        min_range=0, max_range=0,
                        global_range_63_mask=False, effect_dice="")
                    deduped[key.as_tuple()] = SpellEntry(
                        key=key,
                        ap_cost=int(grade.get("ap") or 0),
                        crit_probability=0,
                        max_cast_per_turn=0,
                        damage_lines=[],
                        observed_from="npz",
                        monster_level=int(grade.get("level") or g),
                        name=entry.get("name") or "monster_%d" % mid,
                    )
                    # Preserve spell links.
                    for sid in entry.get("spells") or ():
                        rows = levels.get(str(sid)) or []
                        row = min(rows, key=lambda r: abs(int(r.get("grade") or 1) - g))
                        damage = [(d["element"], int(d["min"]), int(d["max"]))
                                  for d in (row.get("damage") or ())]
                        key = SpellKey(
                            monster_id=mid, grade=g, spell_id=int(sid),
                            min_range=int(row.get("min_range") or 0),
                            max_range=int(row.get("max_range") or 0),
                            global_range_63_mask=_global_range_63_mask(
                                int(row.get("max_range") or 0)),
                            effect_dice=_dice_hash(damage),
                        )
                        deduped[key.as_tuple()] = SpellEntry(
                            key=key,
                            ap_cost=int(row.get("ap_cost") or 0),
                            crit_probability=int(row.get("crit_probability") or 0),
                            max_cast_per_turn=int(row.get("max_cast_per_turn") or 0),
                            damage_lines=damage,
                            observed_from="npz",
                            name=entry.get("name") or "monster_%d" % mid,
                        )
        except (FileNotFoundError, ValueError, KeyError, OSError):
            deduped = {}

    new_last_seq = last_seq
    added = 0
    try:
        with AppendStore(store_path, payload_format="msgpack") as store:
            for seq, _ts, _kind, record in store.iter_records(start_seq=last_seq + 1,
                                                               kind="fight_board"):
                new_last_seq = max(new_last_seq, int(seq))
                for entry in _spell_entries_from_record(record):
                    existing = deduped.get(entry.key.as_tuple())
                    if existing is None:
                        deduped[entry.key.as_tuple()] = entry
                        added += 1
                    elif entry.key.max_range > existing.key.max_range:
                        deduped[entry.key.as_tuple()] = SpellEntry(
                            key=entry.key,
                            ap_cost=entry.ap_cost,
                            crit_probability=entry.crit_probability,
                            max_cast_per_turn=entry.max_cast_per_turn,
                            damage_lines=entry.damage_lines,
                            observed_from="append_store",
                        )
                        added += 1
    except (OSError, ValueError, ImportError) as exc:
        logger.warning("spellbook: enrich skipped store %s: %s", store_path, exc)

    # Build in-memory structures and persist as pickled dicts.
    monsters: Dict[str, Any] = {}
    levels: Dict[str, List[Dict[str, Any]]] = {}
    element_by_code = {i: e for e, i in _ELEMENT_CODE.items()}

    for entry in deduped.values():
        key = entry.key
        mid = key.monster_id
        grade = key.grade
        sid = key.spell_id
        mn = key.min_range
        mx = key.max_range
        ap = entry.ap_cost
        crit = entry.crit_probability
        max_cast = entry.max_cast_per_turn

        damage_lines: List[Tuple[str, int, int]] = []
        for element, low, high in entry.damage_lines:
            damage_lines.append((element, low, high))

        if sid == 0:
            mkey = str(mid)
            mname = entry.name or "monster_%d" % mid
            entry_m = monsters.setdefault(
                mkey, {"name": mname, "is_boss": False, "grades": [], "spells": []})
            if not entry_m["name"] or entry_m["name"] == mname:
                entry_m["name"] = mname
            name_key = "name:%s" % mname.lower()
            if name_key != mkey and name_key not in monsters:
                monsters[name_key] = entry_m
            grow = next((g for g in entry_m["grades"] if g["grade"] == grade), None)
            if grow is None:
                grow = {"grade": grade, "level": entry.monster_level, "life": 0,
                        "ap": 0, "mp": 0,
                        "neutral_res": 0, "earth_res": 0, "fire_res": 0,
                        "water_res": 0, "air_res": 0}
                entry_m["grades"].append(grow)
            grow["ap"] = max(int(grow.get("ap", 0) or 0), ap)
        else:
            mkey = str(mid)
            mname = entry.name or "monster_%d" % mid
            entry_m = monsters.setdefault(
                mkey, {"name": mname, "is_boss": False, "grades": [], "spells": []})
            if not entry_m["name"] or entry_m["name"] == mname:
                entry_m["name"] = mname
            name_key = "name:%s" % mname.lower()
            if name_key != mkey and name_key not in monsters:
                monsters[name_key] = entry_m
            # Ensure a grade row exists for this spell entry.
            grow = next((g for g in entry_m["grades"] if g["grade"] == grade), None)
            if grow is None:
                grow = {"grade": grade, "level": entry.monster_level or grade,
                        "life": 0, "ap": ap, "mp": 0,
                        "neutral_res": 0, "earth_res": 0, "fire_res": 0,
                        "water_res": 0, "air_res": 0}
                entry_m["grades"].append(grow)
            grow["ap"] = max(int(grow.get("ap", 0) or 0), ap)
            if sid not in entry_m["spells"]:
                entry_m["spells"].append(sid)
            level_row = {
                "grade": grade, "ap_cost": ap,
                "min_range": mn, "max_range": mx,
                "crit_probability": crit,
                "max_cast_per_turn": max_cast,
                "damage": [{"element": e, "min": lo, "max": hi}
                           for e, lo, hi in damage_lines],
            }
            levels.setdefault(str(sid), []).append(level_row)

    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        str(output),
        version=np.array([SPELLBOOK_VERSION], dtype=np.int64),
        monsters_pkl=np.array([pickle.dumps(monsters)], dtype="bytes"),
        levels_pkl=np.array([pickle.dumps(levels)], dtype="bytes"),
    )

    return {
        "version": SPELLBOOK_VERSION,
        "entries": len(deduped),
        "added": added,
        "last_seq": new_last_seq,
        "output": str(output),
    }

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Build the monster spellbook npz")
    parser.add_argument("--write", action="store_true",
                        help="write data/cache/monster_spells_touch.npz")
    parser.add_argument("--store", action="append", type=Path,
                        help="AppendStore .db path to read fight_board records from")
    parser.add_argument("--client-table", action="append", type=Path,
                        help="client spell table json path")
    args = parser.parse_args()
    if args.write or args.store or args.client_table:
        summary = compile_spellbook(store_paths=args.store,
                                    client_tables=args.client_table,
                                    output=TOUCH_CACHE_FILE)
        print("spellbook: %d entries -> %s" % (summary["entries"], summary["output"]))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()