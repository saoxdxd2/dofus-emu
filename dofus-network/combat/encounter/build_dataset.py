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


def build(samples: int, seed: int, max_enemies: int = 4,
          level_min: int = 1, level_max: int = 60,
          allies: int = 1, enemies: int = 4, slots: int = 4,
          boss_prob: float = 0.15,
          group_weights: Tuple[float, ...] = (0.25, 0.35, 0.25, 0.15),
          enemy_level_bonus: int = 4) -> Dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    monsters = json.loads(MONSTERS_PATH.read_text(encoding="utf-8"))
    monsters = [m for m in monsters if m.get("grades")]
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

    n = allies + enemies
    table_keys = ("spell_ap", "spell_rmin", "spell_rmax", "spell_damage",
                  "spell_heal", "spell_element", "spell_ally")
    tables = {k: np.zeros((samples, n, slots), dtype=np.float32)
              for k in table_keys}
    tables["spell_element"] = np.zeros((samples, n, slots), dtype=np.int32)
    tables["spell_ally"] = np.zeros((samples, n, slots), dtype=bool)

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

    for b in range(samples):
        level = int(levels[int(rng.integers(len(levels)))])
        classes = by_level[level]
        cls_name, rows = classes[int(rng.integers(len(classes)))]
        meta_classes.append(cls_name)
        loadout = pick_loadout(rows, slots, rng, level)
        for s, row in enumerate(loadout):
            tables["spell_ap"][b, 0, s] = row["ap"]
            tables["spell_rmin"][b, 0, s] = row["rmin"]
            tables["spell_rmax"][b, 0, s] = row["rmax"]
            tables["spell_damage"][b, 0, s] = row["damage"]
            tables["spell_heal"][b, 0, s] = row["heal"]
            tables["spell_element"][b, 0, s] = row["element"]
            tables["spell_ally"][b, 0, s] = row["ally"]

        player_hp = 60.0 + 12.0 * level
        spec["hp"][b, 0] = player_hp
        spec["max_hp"][b, 0] = player_hp
        spec["ap_max"][b, 0] = min(12.0, 6.0 + level // 8)
        spec["mp_max"][b, 0] = min(6.0, 3.0 + level // 20)
        spec["power"][b, 0] = 10.0 + 1.0 * level
        spec["res"][b, 0] = rng.integers(0, 20, size=5)
        spec["dist"][b, 0] = rng.integers(1, 9)
        spec["initiative"][b, 0] = level + rng.integers(0, 10)
        spec["alive"][b, 0] = True
        spec["level"][b, 0] = level

        # Most live encounters are the tutorial's one leader or a small group.
        # The old 50/30/15/5 mix made half the data a 1v1 the trained policy
        # almost always won; this mix spends more rows on the groups that are
        # actually hard, and a boss is a boss plus at most one add.
        weights = np.asarray(group_weights, dtype=float)[:max_enemies]
        weights = weights / weights.sum()
        group = int(rng.choice(np.arange(1, max_enemies + 1), p=weights))
        boss_slot = allies if (boss_grade_levels and rng.random() < boss_prob) else -1
        if boss_slot >= 0:
            group = min(group, 2)
        spec["group"][b] = group
        enemy_inits = []
        for e in range(group):
            slot = allies + e
            target = max(1, level + int(rng.integers(-1, enemy_level_bonus + 1)))
            is_boss = slot == boss_slot
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
            pool = boss_by_grade_level if is_boss else by_grade_level
            monster, grade = pool[bucket][
                int(rng.integers(len(pool[bucket])))]
            glevel = float(grade.get("level") or 1)
            hp = float(grade.get("life_points") or (30 + 15 * glevel))
            # A few species carry raid-sized HP at a low grade (a level-2 Germe
            # has 5000). The live tutorial leader does not; clip the outliers so
            # the dataset is about the fights the agent meets.
            hp = min(hp, 60.0 + (30.0 if is_boss else 20.0) * glevel)
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
            for s, row in enumerate(enemy_loadout(int(glevel), slots)):
                tables["spell_ap"][b, slot, s] = row["ap"]
                tables["spell_rmin"][b, slot, s] = row["rmin"]
                tables["spell_rmax"][b, slot, s] = row["rmax"]
                tables["spell_damage"][b, slot, s] = row["damage"]
                tables["spell_heal"][b, slot, s] = row["heal"]
                tables["spell_element"][b, slot, s] = row["element"]
                tables["spell_ally"][b, slot, s] = row["ally"]
        fastest = max(enemy_inits) if enemy_inits else -1.0
        spec["first_team"][b] = 0 if spec["initiative"][b, 0] >= fastest else 1

    return {**tables, **spec, "_classes": np.asarray(meta_classes)}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--samples", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-enemies", type=int, default=4)
    parser.add_argument("--level-min", type=int, default=1)
    parser.add_argument("--level-max", type=int, default=60)
    parser.add_argument("--boss-prob", type=float, default=0.15)
    parser.add_argument("--group-weights", default="0.25,0.35,0.25,0.15")
    parser.add_argument("--enemy-level-bonus", type=int, default=4)
    args = parser.parse_args(argv)

    group_weights = tuple(float(part) for part in args.group_weights.split(","))
    data = build(args.samples, args.seed, max_enemies=args.max_enemies,
                 level_min=args.level_min, level_max=args.level_max,
                 boss_prob=args.boss_prob, group_weights=group_weights,
                 enemy_level_bonus=args.enemy_level_bonus)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, **data)
    meta = {
        "samples": args.samples, "seed": args.seed,
        "max_enemies": args.max_enemies,
        "level_min": args.level_min, "level_max": args.level_max,
        "boss_prob": args.boss_prob,
        "group_weights": list(group_weights),
        "enemy_level_bonus": args.enemy_level_bonus,
        "source_spells": str(SPELLS_PATH.relative_to(REPO_ROOT)),
        "source_monsters": str(MONSTERS_PATH.relative_to(REPO_ROOT)),
        "enemy_spells": "melee/ranged/heavy scaled by grade level; monster-to-spell"
                        " ids are not in the cache",
        "player_build": "one focused element 70%, two 25%, three 5%; elemental"
                        " stat folded into the spell damage",
    }
    args.out.with_suffix(".json").write_text(
        json.dumps(meta, indent=1), encoding="utf-8")
    print(f"wrote {args.out} ({args.samples} encounters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
