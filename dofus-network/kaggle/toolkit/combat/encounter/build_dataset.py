"""Build a batched encounter dataset from the game data already in the repo.

Sources, and what is real in each row:

* class spells come from ``data/cache/official_class_spells.json`` (official
  Dofus Touch encyclopedia): AP cost, range, element, damage and heal rolls;
* monster HP, AP, MP, level and the five resistances come from
  ``data/cache/monsters_full.json`` grades;
* only the monsters' attack *shape* is modelled: an enemy has a melee, a
  ranged, a heavy and a long line whose base damage scales with its grade
  level. The cache does not map monsters to spell ids, and inventing that
  mapping here would be worse than naming it. Live fights remain the authority.

Output is one ``.npz`` with the environment tables [B,N,S] and the initial
spec [B,N,...], so the TPU kernel does not need the multi-megabyte caches.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from combat.encounter.mechanics import columns_from_record, encode_official

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPELLS_PATH = REPO_ROOT / "data" / "cache" / "official_class_spells.json"
MONSTERS_PATH = REPO_ROOT / "data" / "cache" / "monsters_full.json"
DEFAULT_OUT = REPO_ROOT / "kaggle" / "tpu_selfplay" / "dataset" / "encounters.npz"

ELEMENTS = ("neutral", "earth", "fire", "water", "air")


def _avg(roll: Dict[str, Any]) -> float:
    return (float(roll.get("min_roll") or 0) + float(roll.get("max_roll") or 0)) / 2.0


def _spell_at_level(spell: Dict[str, Any], level: int) -> Optional[Dict[str, Any]]:
    levels = spell.get("levels") or []
    if not levels:
        return None
    eligible = [row for row in levels
                if int(row.get("required_level") or 0) <= level]
    return max(eligible, key=lambda row: int(row.get("required_level") or 0)) \
        if eligible else levels[0]


def _spell_row(spell: Dict[str, Any], level_data: Dict[str, Any]) -> Dict[str, Any]:
    """Turn one official spell level into the simulator's five numbers."""
    damage_by_element: Dict[str, float] = {}
    heal = 0.0
    for effect in level_data.get("normal_effects") or []:
        roll = effect.get("damage") or {}
        element = str(roll.get("element") or "").upper()
        if element == "HEAL":
            heal += _avg(roll)
        elif element in {e.upper() for e in ELEMENTS}:
            damage_by_element[element] = damage_by_element.get(element, 0.0) + _avg(roll)
        h = effect.get("heal") or {}
        if h:
            heal += _avg(h)
    if damage_by_element:
        element = max(damage_by_element, key=damage_by_element.get)
        damage = damage_by_element[element]
        elem_index = [e.upper() for e in ELEMENTS].index(element)
        ally = False
    else:
        damage = 0.0
        elem_index = 0
        ally = heal > 0
    return {
        "id": spell.get("id"),
        "name": spell.get("name"),
        "ap": float(level_data.get("ap_cost") or 6),
        "rmin": float(level_data.get("min_range") or 0),
        "rmax": float(level_data.get("max_range") or 0),
        "damage": damage,
        "heal": heal if ally else 0.0,
        "element": elem_index,
        "ally": ally,
    }


def load_class_loadouts(level: int,
                        payload: Optional[Dict[str, Any]] = None
                        ) -> List[Tuple[str, List[Dict[str, Any]]]]:
    if payload is None:
        payload = json.loads(SPELLS_PATH.read_text(encoding="utf-8"))
    out: List[Tuple[str, List[Dict[str, Any]]]] = []
    for cls in payload.get("classes") or []:
        rows: List[Dict[str, Any]] = []
        for spell in cls.get("spells") or []:
            lv = _spell_at_level(spell, level)
            if lv is None:
                continue
            row = _spell_row(spell, lv)
            if row["damage"] > 0 or row["heal"] > 0:
                # The same sentence the environment reads for a monster, read for the player:
                # 264 of 1,980 class spell levels push and 36 steal MP. `encode_official` prices
                # damage identically to `_spell_row` on all 1,980, so this is one cast described
                # twice, not a second opinion.
                row.update(columns_from_record(encode_official(lv)))
                rows.append(row)
        if rows:
            out.append((cls.get("name") or str(cls.get("breed_id")), rows))
    return out


def _boosted(row: Dict[str, Any], stat: float) -> Dict[str, Any]:
    """A copy of a spell row with the character's elemental stat folded in.

    The environment has one global ``power``; a real build puts most of its
    stats into one or two elements. Folding the elemental stat into the spell
    damage makes the focused spell stronger in the observation, which is the
    tradeoff the policy has to learn: a focused element can beat a weaker
    off-element spell even when the target resists it.
    """
    out = dict(row)
    out["damage"] = float(row["damage"]) * (100.0 + stat) / 100.0
    return out


def pick_loadout(rows: List[Dict[str, Any]], slots: int = 4,
                 rng: Optional[np.random.Generator] = None,
                 level: int = 60) -> List[Dict[str, Any]]:
    """A character build: one focused element usually, two sometimes, three rarely.

    Real builds put their stats into one or two elements, so the loadout is not
    spread across every element. The focused spells carry the character's
    elemental stat; a secondary element is weaker; off-element spells are kept
    only when the class has nothing better (or as the heal/utility slot).
    """
    if rng is None:
        rng = np.random.default_rng(0)
    damage = [r for r in rows if not r["ally"] and r["damage"] > 0]
    heals = [r for r in rows if r["ally"] and r["heal"] > 0]
    heals.sort(key=lambda r: (r["heal"] / max(r["ap"], 1.0)), reverse=True)
    by_element: Dict[int, List[Dict[str, Any]]] = {}
    for row in damage:
        by_element.setdefault(int(row["element"]), []).append(row)
    for element, group in by_element.items():
        group.sort(key=lambda r: (r["damage"] / max(r["ap"], 1.0)), reverse=True)
    elements = sorted(by_element)
    if not elements:
        return [{"ap": 6.0, "rmin": 0.0, "rmax": 0.0, "damage": 0.0,
                 "heal": 0.0, "element": 0, "ally": False} for _ in range(slots)]

    draw = float(rng.random())
    focus_count = 1 if draw < 0.70 else 2 if draw < 0.95 else 3
    focus_count = min(focus_count, len(elements))
    primary = int(rng.choice(elements))
    focus = [primary]
    others = [e for e in elements if e != primary]
    if focus_count > 1 and others:
        extra = rng.choice(others, size=min(focus_count - 1, len(others)),
                           replace=False)
        focus.extend(int(e) for e in np.atleast_1d(extra))

    primary_stat = 30.0 + float(rng.uniform(2.5, 4.5)) * level
    stats = {primary: primary_stat}
    for element in focus[1:]:
        stats[element] = primary_stat * float(rng.uniform(0.25, 0.50))

    damage_slots = slots - (1 if heals else 0)
    picked: List[Dict[str, Any]] = []
    picked_ids = set()
    secondary_slots = len(focus) - 1
    primary_slots = max(1, damage_slots - secondary_slots)
    for element in focus:
        take = primary_slots if element == primary else 1
        for row in by_element.get(element, [])[:take]:
            key = row.get("id") or id(row)
            if key in picked_ids:
                continue
            picked_ids.add(key)
            picked.append(_boosted(row, stats[element]))

    # A class with only one spell in its focused element still needs a full
    # bar. Fill from the same element first, then from the other elements at
    # their unh boosted damage.
    fill_order = [e for e in focus] + [e for e in elements if e not in focus]
    for element in fill_order:
        for row in by_element.get(element, []):
            if len(picked) >= damage_slots:
                break
            key = row.get("id") or id(row)
            if key in picked_ids:
                continue
            picked_ids.add(key)
            picked.append(_boosted(row, stats.get(element, 0.0)))
        if len(picked) >= damage_slots:
            break

    if heals:
        picked = picked[:slots - 1] + [heals[0]]
    while len(picked) < slots:
        picked.append({"ap": 6.0, "rmin": 0.0, "rmax": 0.0, "damage": 0.0,
                       "heal": 0.0, "element": 0, "ally": False})
    return picked[:slots]


def enemy_loadout(level: int, slots: int = 4) -> List[Dict[str, Any]]:
    """A named abstraction: melee / ranged / heavy / long, scaled by level."""
    return [
        {"ap": 3.0, "rmin": 1.0, "rmax": 1.0, "damage": 5.0 + 0.5 * level,
         "heal": 0.0, "element": 0, "ally": False},
        {"ap": 3.0, "rmin": 2.0, "rmax": 5.0, "damage": 4.0 + 0.35 * level,
         "heal": 0.0, "element": 0, "ally": False},
        {"ap": 4.0, "rmin": 1.0, "rmax": 2.0, "damage": 8.0 + 0.75 * level,
         "heal": 0.0, "element": 0, "ally": False},
        {"ap": 5.0, "rmin": 3.0, "rmax": 6.0, "damage": 6.0 + 0.6 * level,
         "heal": 0.0, "element": 0, "ally": False},
    ][:slots]


def player_stats(level: int) -> Dict[str, Any]:
    """The one place a character's numbers are defined, shared by the corpus and the planner.

    The generator used to write these as inline expressions; a pre-fight plan that wanted the same
    character had to duplicate them, and two copies of a stat table is how a planner starts disagreeing
    with the thing it plans for. `res` stays out: no client table gives a player's per-element
    resistance, so the corpus randomises it and the planner refuses to invent it.
    """
    hp = 60.0 + 12.0 * level
    return {
        "hp": hp,
        "ap_max": min(12.0, 6.0 + level // 8),
        "mp_max": min(6.0, 3.0 + level // 20),
        "power": 10.0 + 1.0 * level,
        "level": float(level),
    }


def build(samples: int, seed: int, max_enemies: int = 4,
          level_min: int = 1, level_max: int = 60,
          allies: int = 1, enemies: int = 4, slots: int = 4,
          boss_prob: float = 0.15,
          group_weights: Tuple[float, ...] = (0.25, 0.35, 0.25, 0.15),
          enemy_level_bonus: int = 4, enemy_level_low: int = -1, touch=None,
          stats: Optional[Dict[str, Any]] = None) -> Dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    stats = stats if stats is not None else {}
    stats.update({"source": "dofus touch client data tables" if touch else
                  "data/cache (community database + invented enemy loadouts)",
                  "clamped_low_hp": 0, "truncated_kits": 0, "real_kit_slots": 0,
                  "invented_slots": 0})
    monsters = touch.monster_rows() if touch is not None else json.loads(
        MONSTERS_PATH.read_text(encoding="utf-8"))
    monsters = [m for m in monsters if m.get("grades")]
    if touch is not None:
        # Some species have no kit, or only summons, heals and repositioning buffs. Keeping
        # them would teach the policy enemies that cannot threaten the player, and hide how
        # many were dropped -- so the pool needs at least one damaging line, and it is counted.
        kept = [m for m in monsters
                if any(r["damage"] for r in touch.kit(int(m["id"]), slots, 1))]
        stats["monsters_used"] = len(kept)
        stats["monsters_dropped_no_priced_spell"] = len(monsters) - len(kept)
        monsters = kept
    payload = json.loads(SPELLS_PATH.read_text(encoding="utf-8"))
    by_level = {lvl: load_class_loadouts(lvl, payload)
                for lvl in range(level_min, level_max + 1)}
    by_level = {lvl: rows for lvl, rows in by_level.items() if rows}
    if not by_level:
        raise SystemExit("no class spells for the requested level range")
    levels = sorted(by_level)

    # Index every real grade by its level. Picking the "nearest" grade of a
    # chosen monster was not enough: a species whose first grade is level 90
    # became a level-2 enemy, which made the dataset nonsense (28k HP duels).
    by_grade_level: Dict[int, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    boss_by_grade_level: Dict[int, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    for monster in monsters:
        bucket = boss_by_grade_level if monster.get("is_boss") else by_grade_level
        for grade in monster["grades"]:
            bucket.setdefault(int(grade.get("level") or 0), []).append(
                (monster, grade))
    grade_levels = sorted(by_grade_level)
    boss_grade_levels = sorted(boss_by_grade_level)
    # Real bosses are not "a taller random monster near your level": a species is met in a
    # specific dungeon that recommends a specific level. Use that pairing when it is known.
    boss_by_dungeon_level: Dict[int, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    if touch is not None:
        for dungeon_level, species_ids in touch.boss_levels().items():
            for species_id in species_ids:
                monster = next((m for m in monsters if int(m["id"]) == species_id), None)
                if monster:
                    grade = min(monster["grades"], key=lambda g: abs(
                        int(g.get("level") or 0) - dungeon_level) or 0)
                    boss_by_dungeon_level.setdefault(int(dungeon_level), []).append(
                        (monster, grade))
    stats["dungeon_levels_with_bosses"] = len(boss_by_dungeon_level)

    n = allies + enemies
    table_keys = ("spell_ap", "spell_rmin", "spell_rmax", "spell_damage",
                  "spell_heal", "spell_element", "spell_ally",
                  "spell_kill", "spell_push", "spell_ap_delta", "spell_mp_delta",
                  "spell_duration", "spell_summon_bodies", "spell_summon_hp",
                  "spell_summon_damage")
    tables = {k: np.zeros((samples, n, slots), dtype=np.float32)
              for k in table_keys}
    tables["spell_element"] = np.zeros((samples, n, slots), dtype=np.int32)
    tables["spell_ally"] = np.zeros((samples, n, slots), dtype=bool)
    # The mechanic columns. The player's rows stay zero for now: the class-spell database describes
    # those effects in prose and the encoder that would type them has not been wired into this
    # generator, so a player spell that dispels is still invisible here -- recorded rather than
    # silently assumed, because an absent column and a zero column mean the same thing to the
    # environment and only one of them is true.
    for key in ("spell_kill", "spell_push", "spell_ap_delta", "spell_mp_delta",
                "spell_duration", "spell_summon_bodies", "spell_summon_hp",
                "spell_summon_damage"):
        tables[key] = np.zeros((samples, n, slots), dtype=np.float32)

    spec = {
        "hp": np.zeros((samples, n), dtype=np.float32),
        "max_hp": np.zeros((samples, n), dtype=np.float32),
        "ap_max": np.zeros((samples, n), dtype=np.float32),
        "mp_max": np.zeros((samples, n), dtype=np.float32),
        "power": np.zeros((samples, n), dtype=np.float32),
        "res": np.zeros((samples, n, 5), dtype=np.float32),
        "dist": np.zeros((samples, n), dtype=np.float32),
        "initiative": np.zeros((samples, n), dtype=np.float32),
        "alive": np.zeros((samples, n), dtype=bool),
        "first_team": np.zeros((samples,), dtype=np.int32),
        "level": np.zeros((samples, n), dtype=np.float32),
        "monster_id": np.zeros((samples, n), dtype=np.int32),
        "boss": np.zeros((samples, n), dtype=bool),
        "group": np.zeros((samples,), dtype=np.int32),
    }
    meta_classes: List[str] = []
    # Only ever written into the file when a party exists: an `allies=1` corpus must come out with
    # the same key set it had before, or the parity check is a different file, not the same boards.
    party_classes = np.zeros((samples, max(allies, 1)), dtype="<U24")
    if allies > 1:
        party_classes[:, 0] = ""

    for b in range(samples):
        level = int(levels[int(rng.integers(len(levels)))])
        for a in range(allies):
            # Ally 0 is the character every published number describes. The rest of the party is
            # drawn with the same calls, in the same order, per actor -- so with `allies=1` the
            # board consumes exactly the random numbers it used to and every existing corpus stays
            # bit-for-bit reproducible. That is the parity gate, not a nicety: a generator that
            # quietly re-rolls the solo boards invalidates every paired delta measured on them.
            classes = by_level[level]
            cls_name, rows = classes[int(rng.integers(len(classes)))]
            if a == 0:
                meta_classes.append(cls_name)
            else:
                party_classes[b, a] = cls_name
            loadout = pick_loadout(rows, slots, rng, level)
            for s, row in enumerate(loadout):
                tables["spell_ap"][b, a, s] = row["ap"]
                tables["spell_rmin"][b, a, s] = row["rmin"]
                tables["spell_rmax"][b, a, s] = row["rmax"]
                tables["spell_damage"][b, a, s] = row["damage"]
                tables["spell_heal"][b, a, s] = row["heal"]
                tables["spell_element"][b, a, s] = row["element"]
                tables["spell_ally"][b, a, s] = row["ally"]
                for key in ("kill", "push", "ap_delta", "mp_delta", "duration"):
                    value = float(row.get(key, 0.0))
                    tables["spell_" + key][b, a, s] = value
                    if key != "duration" and value:
                        stats["player_slots_with_a_forced_effect"] = \
                            stats.get("player_slots_with_a_forced_effect", 0) + 1

            # `pstats`, not `stats`: this name shadowed the counters dict the caller passes in, so
            # the first truncated kit in the run raised KeyError and the meta's `source_stats`
            # described only the last board. The generator could not run at all until it moved.
            pstats = player_stats(level)
            player_hp = pstats["hp"]
            spec["hp"][b, a] = player_hp
            spec["max_hp"][b, a] = player_hp
            spec["ap_max"][b, a] = pstats["ap_max"]
            spec["mp_max"][b, a] = pstats["mp_max"]
            spec["power"][b, a] = pstats["power"]
            spec["res"][b, a] = rng.integers(0, 20, size=5)
            spec["dist"][b, a] = rng.integers(1, 9)
            spec["initiative"][b, a] = level + rng.integers(0, 10)
            spec["alive"][b, a] = True
            spec["level"][b, a] = level

        # Most live encounters are the tutorial's one leader or a small group.
        # The old 50/30/15/5 mix made half the data a 1v1 the trained policy
        # almost always won; this mix spends more rows on the groups that are
        # actually hard, and a boss is a boss plus at most one add.
        # The enemy bar has to describe a fight a party can be in: at 4 allies a fixed 1-4 group
        # mix would be 4 actors against 1-4 monsters with nothing to coordinate about, so the
        # weights renormalise over the group sizes this configuration can actually hold. With
        # `enemies == max_enemies` this is the identical draw set as today, which is what keeps the
        # solo corpora reproducible.
        sizes = np.arange(1, min(max_enemies, enemies) + 1)
        weights = np.asarray(group_weights, dtype=float)[:sizes.size]
        weights = weights / weights.sum()
        group = int(rng.choice(sizes, p=weights))
        can_boss = bool(boss_grade_levels or boss_by_dungeon_level)
        boss_slot = allies if (can_boss and rng.random() < boss_prob) else -1
        if boss_slot >= 0:
            group = min(group, 2)
        spec["group"][b] = group
        enemy_inits = []
        for e in range(group):
            slot = allies + e
            target = max(1, level + int(rng.integers(enemy_level_low,
                                                     enemy_level_bonus + 1)))
            is_boss = slot == boss_slot
            if is_boss and boss_by_dungeon_level:
                # A boss comes from the dungeon that recommends it, at that dungeon's level.
                nearest = min(boss_by_dungeon_level, key=lambda l: abs(l - target))
                pool_rows = boss_by_dungeon_level[nearest]
                stats["boss_rows_from_dungeon_table"] = \
                    stats.get("boss_rows_from_dungeon_table", 0) + 1
            else:
                levels_for_pick = boss_grade_levels if is_boss else grade_levels
                if not levels_for_pick:
                    is_boss = False
                    levels_for_pick = grade_levels
                nearby = [g for g in levels_for_pick if abs(g - target) <= 2]
                if not nearby:
                    nearby = [g for g in levels_for_pick if abs(g - target) <= 5]
                if not nearby:
                    nearest = min(levels_for_pick, key=lambda g: abs(g - target))
                    nearby = [nearest]
                bucket = int(rng.choice(nearby))
                pool_rows = (boss_by_grade_level if is_boss else by_grade_level)[bucket]
            monster, grade = pool_rows[int(rng.integers(len(pool_rows)))]
            glevel = float(grade.get("level") or 1)
            hp = float(grade.get("life_points") or (30 + 15 * glevel))
            # A few species carry raid-sized HP at a low grade (a level-2 Germe has 5000).
            # The clip exists for the community cache's invented grades; the client's own
            # grades are real, and clipping a 5,900 HP boss to 1,860 is not caution, it is
            # a different monster. So the clip is off on the touch path and counted here.
            if touch is None:
                clipped = min(hp, 60.0 + (30.0 if is_boss else 20.0) * glevel)
                if clipped < hp:
                    stats["clamped_low_hp"] += 1
                hp = clipped
            spec["hp"][b, slot] = hp
            spec["max_hp"][b, slot] = hp
            spec["ap_max"][b, slot] = min(12.0, max(3.0, float(grade.get("action_points") or 4)))
            spec["mp_max"][b, slot] = min(6.0, max(1.0, float(grade.get("movement_points") or 3)))
            spec["power"][b, slot] = (10.0 + 1.5 * glevel) if is_boss else 0.0
            spec["res"][b, slot] = [
                float(grade.get(f"{e_}_res") or 0) for e_ in ELEMENTS]
            spec["dist"][b, slot] = float(np.clip(
                spec["dist"][b, 0] + rng.integers(-1, 3), 1, 12))
            spec["initiative"][b, slot] = glevel + rng.integers(0, 10)
            spec["alive"][b, slot] = True
            spec["level"][b, slot] = glevel
            spec["monster_id"][b, slot] = int(monster.get("id") or 0)
            spec["boss"][b, slot] = bool(is_boss)
            enemy_inits.append(spec["initiative"][b, slot])
            kit = (touch.kit(int(monster.get("id") or 0), slots,
                             int(grade.get("grade") or 1)) if touch is not None else None)
            if kit is not None:
                if len(monster.get("spells") or []) > slots:
                    stats["truncated_kits"] += 1
                stats["real_kit_slots"] += sum(
                    1 for r in kit
                    if r["damage"] or r["heal"]
                    or any(r.get(k) for k in ("kill", "push", "ap_delta", "mp_delta",
                                              "summon_ids")))
            for s, row in enumerate(kit or enemy_loadout(int(glevel), slots)):
                tables["spell_ap"][b, slot, s] = row["ap"]
                tables["spell_rmin"][b, slot, s] = row["rmin"]
                tables["spell_rmax"][b, slot, s] = row["rmax"]
                tables["spell_damage"][b, slot, s] = row["damage"]
                tables["spell_heal"][b, slot, s] = row["heal"]
                tables["spell_element"][b, slot, s] = row["element"]
                tables["spell_ally"][b, slot, s] = row["ally"]
                for key in ("kill", "push", "ap_delta", "mp_delta", "duration"):
                    tables["spell_" + key][b, slot, s] = float(row.get(key, 0.0))
                    if key != "duration" and row.get(key):
                        stats["enemy_slots_with_a_forced_effect"] = (
                            stats.get("enemy_slots_with_a_forced_effect", 0) + 1)
                # Bodies, per body's HP, per body's best damage line -- resolved here rather than in
                # `slot()` because the grade that decides them is the *summoner's*, which this loop
                # knows and the row builder does not.
                bodies, hp_each, dmg_each = (
                    touch.summon_line(row.get("summon_ids") or [], int(glevel))
                    if touch is not None else (0, 0.0, 0.0))
                tables["spell_summon_bodies"][b, slot, s] = float(bodies)
                tables["spell_summon_hp"][b, slot, s] = hp_each
                tables["spell_summon_damage"][b, slot, s] = dmg_each
                if bodies:
                    stats["enemy_slots_with_a_summon"] = (
                        stats.get("enemy_slots_with_a_summon", 0) + 1)
                    stats["summon_bodies"] = stats.get("summon_bodies", 0) + bodies
                    if dmg_each == 0.0:
                        stats["summon_slots_that_cannot_attack"] = (
                            stats.get("summon_slots_that_cannot_attack", 0) + 1)
        for a in range(1, allies):
            # The party is drawn *after* the monsters, deliberately. Ally 0's draws above are the
            # exact sequence every published corpus was built with, so an `allies=1` board still
            # consumes the same randoms and stays bit-identical; putting the extra allies at the end
            # keeps that guarantee instead of trading it for a prettier ordering.
            #
            # Their initiative is drawn here too, in the same scale as ally 0's, because
            # `first_team` compares against it below -- an ally given a constant initiative would
            # decide the opening turn by fiat, and a party corpus whose initiative is synthetic
            # would make every coordination gain an artifact of who acts first.
            cls_name, rows = by_level[level][int(rng.integers(len(by_level[level])))]
            party_classes[b, a] = cls_name
            loadout = pick_loadout(rows, slots, rng, level)
            for s, row in enumerate(loadout):
                tables["spell_ap"][b, a, s] = row["ap"]
                tables["spell_rmin"][b, a, s] = row["rmin"]
                tables["spell_rmax"][b, a, s] = row["rmax"]
                tables["spell_damage"][b, a, s] = row["damage"]
                tables["spell_heal"][b, a, s] = row["heal"]
                tables["spell_element"][b, a, s] = row["element"]
                tables["spell_ally"][b, a, s] = row["ally"]
                for key in ("kill", "push", "ap_delta", "mp_delta", "duration"):
                    value = float(row.get(key, 0.0))
                    tables["spell_" + key][b, a, s] = value
                    if key != "duration" and value:
                        stats["player_slots_with_a_forced_effect"] = \
                            stats.get("player_slots_with_a_forced_effect", 0) + 1
            pstats = player_stats(level)
            spec["hp"][b, a] = pstats["hp"]
            spec["max_hp"][b, a] = pstats["hp"]
            spec["ap_max"][b, a] = pstats["ap_max"]
            spec["mp_max"][b, a] = pstats["mp_max"]
            spec["power"][b, a] = pstats["power"]
            spec["res"][b, a] = rng.integers(0, 20, size=5)
            spec["dist"][b, a] = rng.integers(1, 9)
            spec["initiative"][b, a] = level + rng.integers(0, 10)
            spec["alive"][b, a] = True
            spec["level"][b, a] = level

        fastest = max(enemy_inits) if enemy_inits else -1.0
        # The client gives initiative to the *side*, so with a party the opening turn is the best
        # roller among the allies, not just the leader's -- otherwise a 4v4 would be decided by one
        # actor's number and the coordination gain would be confounded with who gets to act first.
        best_ally = float(np.asarray(spec["initiative"][b, :allies]).max())
        spec["first_team"][b] = 0 if best_ally >= fastest else 1

    data = {**tables, **spec, "_classes": np.asarray(meta_classes)}
    if allies > 1:
        data["_party_classes"] = party_classes
    return data


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--samples", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-enemies", type=int, default=4)
    parser.add_argument("--allies", type=int, default=1,
                        help="party size on the player's side; 1 is every published corpus and 4 is "
                             "a real group. The monster bar keeps its published width (4) at any "
                             "party size, so --allies 4 is a 4v4 on 8 actor slots and --allies 4 "
                             "--max-enemies 1 is a 4v1 boss kill. ally 0's draws are unchanged at "
                             "any value, which is what the parity test checks.")
    parser.add_argument("--level-min", type=int, default=1)
    parser.add_argument("--level-max", type=int, default=60)
    parser.add_argument("--boss-prob", type=float, default=0.15)
    parser.add_argument("--slots", type=int, default=4,
                        help="spell heads per actor; the client's kits are wider than 4 "
                             "for 31.4%% of bosses, so a boss corpus wants 6")
    parser.add_argument("--group-weights", default="0.25,0.35,0.25,0.15")
    parser.add_argument("--enemy-level-bonus", type=int, default=4)
    parser.add_argument("--enemy-level-low", type=int, default=-1,
                        help="lowest level offset for an enemy, relative to the player. "
                             "Negative means the player out-levels the pack, which is how "
                             "the readiness question a pre-fight plan has to answer is priced.")
    parser.add_argument("--touch-dir", type=Path, default=None,
                        help="fetched Dofus Touch tables (fetch_touch_data.py output); without"
                             " it the enemy attack lines stay invented")
    args = parser.parse_args(argv)

    touch = None
    if args.touch_dir is not None:
        from combat.encounter.touch_source import TouchData
        touch = TouchData(args.touch_dir)
    group_weights = tuple(float(part) for part in args.group_weights.split(","))
    stats: Dict[str, Any] = {}
    data = build(args.samples, args.seed, max_enemies=args.max_enemies,
                 level_min=args.level_min, level_max=args.level_max,
                 boss_prob=args.boss_prob, group_weights=group_weights,
                 enemy_level_bonus=args.enemy_level_bonus,
                 enemy_level_low=args.enemy_level_low, slots=args.slots,
                 allies=args.allies,
                 touch=touch, stats=stats)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, **data)
    meta = {
        "samples": args.samples, "seed": args.seed,
        "allies": args.allies, "enemies": args.max_enemies,
        # Recorded here and not in the npz: `Config(allies=..)` is what makes a corpus readable, so
        # the number has to live next to the knobs that produced it -- a 4v4 file scored with a 1v4
        # config is 4 dead allies and a wrong team split, and nothing in the arrays would say so.
        "max_enemies": args.max_enemies, "slots": args.slots,
        "level_min": args.level_min, "level_max": args.level_max,
        "boss_prob": args.boss_prob,
        "group_weights": list(group_weights),
        "enemy_level_bonus": args.enemy_level_bonus,
        "enemy_level_low": args.enemy_level_low,
        "source_spells": str(SPELLS_PATH.relative_to(REPO_ROOT)),
        "source_monsters": str(args.touch_dir) if touch else str(
            MONSTERS_PATH.relative_to(REPO_ROOT)),
        "enemy_spells": ("each monster's own kit from the client's SpellLevels table"
                         if touch else
                         "melee/ranged/heavy scaled by grade level; monster-to-spell"
                         " ids are not in the cache"),
        "still_invented": ("initiative, power, engagement distance, group size"
                           if touch else
                           "initiative, power, engagement distance, group size,"
                           " every enemy attack line"),
        "source_stats": stats,
        "touch_summary": touch.summary() if touch else None,
        "player_build": "one focused element 70%, two 25%, three 5%; elemental"
                        " stat folded into the spell damage",
    }
    args.out.with_suffix(".json").write_text(
        json.dumps(meta, indent=1), encoding="utf-8")
    print(f"wrote {args.out} ({args.samples} encounters)")
    print(json.dumps({"source": stats.get("source"),
                      "monsters_used": stats.get("monsters_used"),
                      "dropped_no_priced_spell": stats.get("monsters_dropped_no_priced_spell"),
                      "real_kit_slots": stats.get("real_kit_slots"),
                      "truncated_kits": stats.get("truncated_kits"),
                      "clamped_low_hp": stats.get("clamped_low_hp"),
                      "player_slots_with_a_forced_effect":
                          stats.get("player_slots_with_a_forced_effect"),
                      "enemy_slots_with_a_forced_effect":
                          stats.get("enemy_slots_with_a_forced_effect"),
                      "enemy_slots_with_a_summon": stats.get("enemy_slots_with_a_summon"),
                      "summon_bodies": stats.get("summon_bodies"),
                      "summon_slots_that_cannot_attack":
                          stats.get("summon_slots_that_cannot_attack")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
