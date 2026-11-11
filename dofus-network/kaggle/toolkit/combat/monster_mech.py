"""What a monster can actually do: its spells, its reach, its damage per turn.

Why this exists
---------------
A live board describes an enemy with HP, a cell, a level and nothing else. The
fighter payload carries ``spell_ids: []`` and ``spells: []`` for every monster,
and often ``mp: null`` too, so the decision layer is asked to avoid a reach it
was never told. `data/cache/monsters_full.json` does not help: it has HP, AP, MP
and resistances per grade and no spells at all. `combat/fight_estimate.py` says
so in its own docstring -- "no monster spell damage, so incoming damage is an
explicit level/AP assumption".

`data/harvest_monster_spells.py` fills that hole from the same mirror the
monster cache came from, and this module is the read side: monster id plus
level in, a list of ``(min_range, max_range, expected_damage)`` out, which is
exactly what ``map_field.EnemyThreat`` consumes.

The number that matters most is the reach. Measured on monster 31, Blue Larva:
2 MP and a 3-AP spell at range 1-6, so it reaches eight cells, while the
character's longest spell reaches five. Nothing about that fight is a
positioning puzzle the policy can solve by keeping its distance -- and until
this data existed, nothing in the system knew it.

Missing is not zero
-------------------
Every lookup returns ``None`` when the monster or grade is unknown, and the
callers are expected to say so. Reading an unknown reach as zero would mark
every cell safe against a monster that can hit from eight away, which is the
one error that loses a fight silently.

Coverage, measured
------------------
Two loaders, and only one of them has data.

``from_sqlite`` / ``--write`` read ``api.dofusdb.fr``, which is a Dofus 2 roster:
of the three distinct species in the recorded boards, one is present
(`Barbakle`). So that table is empty exactly where the decision needs it.

``from_live_corpus`` reads the client's own spellbook back out of the fights
already recorded, and it covers every species the client has ever shown a spell
list for -- `Merkaptan Leader` reaches ten cells, `Ayin Spyr`'s only spell is a
global buff and so contributes no reach at all. That is the loader `combat/threat`
consults, because it is the only one whose reach is observed rather than assumed.

Where the corpus has no row the answer stays ``None``, and that is the correct
answer rather than a defect: the data genuinely is not there, and `threat` falls
back to its level prior. Missing is not zero.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_FILE = DATA_DIR / "cache" / "monster_spells.json"
KNOWLEDGE = DATA_DIR / "knowledge" / "dofus_knowledge.sqlite"
# The recorded fights, which carry the client's own spellbook for each enemy.
LIVE_CORPUS = DATA_DIR.parent / "trajectories" / "fight_boards.jsonl"

ELEMENTS = ("neutral", "earth", "fire", "water", "air")

# The client's sentinel for "any range". `Burst of Motivation` is a self/ally
# buff at range 63; read as a damage range it would put every cell on the map
# inside the caster's reach, which is the opposite of useful.
GLOBAL_RANGE = 63

# The five damage effects, element by element. `battle_solver.ELEMENT_BY_EFFECT`
# is the authority and this module must not import it: `battle_solver` imports
# `combat.threat` at module level, so the dependency would close a cycle through
# this file. The copy is pinned by
# `test_monster_mech.test_the_damage_effects_agree_with_the_solver`.
DAMAGE_EFFECTS: Dict[int, str] = {
    91: "water", 92: "earth", 93: "air", 94: "fire", 95: "neutral",   # steal
    96: "water", 97: "earth", 98: "air", 99: "fire", 100: "neutral",  # damage
}


def _key(ref: Any) -> str:
    """The table key for a monster reference.

    A decimal string is a roster id, anything else is a name. The two namespaces
    are prefixed apart so a name can never answer for an id: the harvested table
    is keyed by id, the live corpus by name, and a lookup that silently crossed
    them would price the wrong monster.
    """
    if isinstance(ref, int):
        return str(ref)
    text = str(ref).strip()
    if text.isdigit():
        return str(int(text))
    return "name:%s" % text.lower()


@dataclass(frozen=True)
class SpellRow:
    """One spell at one grade, reduced to what a decision needs."""

    spell_id: int
    ap_cost: int
    min_range: int
    max_range: int
    damage: Tuple[Tuple[str, int, int], ...]  # (element, min, max)

    @property
    def expected_damage(self) -> float:
        """Mean damage of one cast, summed over its damage lines."""
        return float(sum(0.5 * (low + high) for _element, low, high in self.damage))

    def damage_against(self, resist_percent: Dict[str, int],
                       resist_fixed: Dict[str, int] = None) -> float:
        """Mean damage after the target's resistances, per the client formula.

        ``max(0, roll - fixed) * (1 - percent/100)``. Resistance is a property
        of the target, so this belongs on the spell rather than on the monster.
        """
        resist_fixed = resist_fixed or {}
        total = 0.0
        for element, low, high in self.damage:
            roll = 0.5 * (low + high)
            fixed = float(resist_fixed.get(element, 0))
            percent = float(resist_percent.get(element, 0))
            total += max(0.0, roll - fixed) * (1.0 - percent / 100.0)
        return total


@dataclass(frozen=True)
class Grade:
    grade: int
    level: int
    life: int
    ap: int
    mp: int
    resists: Tuple[Tuple[str, int], ...]


class MonsterMechanics:
    """Read side of the harvested monster database."""

    def __init__(self, monsters: Dict[str, Any], levels: Dict[str, Any]):
        self._monsters = monsters
        self._levels = levels

    # -- loading ------------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "MonsterMechanics":
        path = path or CACHE_FILE
        if not path.exists():
            raise FileNotFoundError(
                "%s is missing; run `python -m data.harvest_monster_spells --write`" % path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(payload.get("monsters") or {}, payload.get("spell_levels") or {})

    @classmethod
    def from_sqlite(cls, path: Optional[Path] = None) -> "MonsterMechanics":
        """Same object, read from the knowledge base instead of the JSON."""
        path = path or KNOWLEDGE
        con = sqlite3.connect(str(path))
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        monsters: Dict[str, Any] = {}
        for row in cur.execute("SELECT * FROM monster_spells"):
            mid = str(row["monster_id"])
            entry = monsters.setdefault(mid, {"name": row["name"],
                                              "is_boss": bool(row["is_boss"]),
                                              "grades": [], "spells": []})
            entry["grades"].append({"grade": row["grade"], "level": row["level"],
                                    "life": row["life"], "ap": row["ap"], "mp": row["mp"],
                                    "neutral_res": 0, "earth_res": 0, "fire_res": 0,
                                    "water_res": 0, "air_res": 0})
            entry["spells"] = json.loads(row["spell_ids"] or "[]")
        levels: Dict[str, Any] = {}
        for row in cur.execute("SELECT * FROM spell_levels"):
            levels.setdefault(str(row["spell_id"]), []).append({
                "grade": row["grade"], "ap_cost": row["ap_cost"],
                "min_range": row["min_range"], "max_range": row["max_range"],
                "crit_probability": row["crit_probability"],
                "max_cast_per_turn": row["max_cast_per_turn"],
                "damage": json.loads(row["damage_json"] or "[]"),
            })
        con.close()
        return cls(monsters, levels)

    @classmethod
    def from_live_corpus(cls, paths: Optional[Sequence[Path]] = None) -> "MonsterMechanics":
        """The client's own spellbook, read back out of the fights already recorded.

        Why this exists. The roster harvest (`from_sqlite`, `--write`) reads a Dofus 2
        mirror, and of the species actually fought one in eight is on it -- so the table
        is empty exactly where the decision needs it. Meanwhile the page has been sending
        the resolved spellbook all along: `mobile-disguise.js` copies `spells` off the
        fighter object, which the client has already loaded to render the cast. It is on
        the boards. `trajectories/fight_boards.jsonl` carries, for `Merkaptan Leader`,
        ``Thwack`` at AP 1 / range 10 / ``requires_los: true`` -- a level-5 monster with
        0 MP that reaches ten cells, where the level prior this system used assumed five.

        This reads that back with no network and no client, so it also works on every
        board already recorded, and it is keyed by name because the fighter object
        carries no ``creatureGenericId`` (measured: null on all 1,396 enemy fighters).

        Damage lines are reduced per element, then the strongest element is kept. A
        spell with three element variants -- `Deviousness` carries earth rolls 12-14,
        15-17 and 18-20 -- deals one of them, so summing all three would triple-count
        the cast; but three lines of the *same* element are three hits and do add.

        What is trusted, and what is not. The **reach** is observed directly: the client
        states the spell's range and the fighter's MP, and neither is inferred. The
        **damage scale is not trusted**, because nothing in the corpus can check it --
        no recorded fight carries a positive ``loss`` anywhere in
        ``trajectories/play_dofus-*.jsonl``, so there is no measured hit to calibrate
        against, and `Thwack` reads as 400 damage for 1 AP, which is not credible for a
        level-5 monster. Callers should take the reach from here and leave damage to
        `ThreatMemory`, which learns it from fights that were actually fought.
        """
        paths = list(paths or [LIVE_CORPUS])
        monsters: Dict[str, Any] = {}
        levels: Dict[str, Any] = {}
        for path in paths:
            if not path.exists():
                continue
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or '"spells"' not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    board = row.get("board") if isinstance(row, dict) else None
                    if not isinstance(board, dict):
                        continue
                    for fighter in board.get("fighters") or ():
                        cls._absorb_live_fighter(fighter, monsters, levels)
        return cls(monsters, levels)

    @staticmethod
    def _absorb_live_fighter(fighter: Any, monsters: Dict[str, Any],
                             levels: Dict[str, Any]) -> None:
        if not isinstance(fighter, dict):
            return
        if not (fighter.get("team") == "enemy" or fighter.get("is_monster")):
            return
        name = str(fighter.get("name") or "").strip()
        spells = fighter.get("spells")
        if not name or not isinstance(spells, list) or not spells:
            return
        rich = [s for s in spells if isinstance(s, dict) and "requires_los" in s]
        if not rich:
            return
        level = int(fighter.get("level") or 1)
        # Always name-prefixed, never `_key(name)`: a monster whose name is all
        # digits would otherwise be stored under an id and answer for that id.
        key = "name:%s" % name.lower()
        entry = monsters.setdefault(key, {"name": name, "is_boss": bool(fighter.get("is_boss")),
                                          "grades": [], "spells": []})
        # The board updates as the fight goes on, so the same grade is seen with
        # different AP/MP/HP. Keep the largest of each: a monster at full strength
        # is the one the threat has to price.
        grade = next((g for g in entry["grades"] if g["level"] == level), None)
        if grade is None:
            resist = fighter.get("resist_percent") or {}
            grade = {"grade": level, "level": level, "life": 0, "ap": 0, "mp": 0}
            for element in ELEMENTS:
                grade["%s_res" % element] = int(resist.get(element) or 0)
            entry["grades"].append(grade)
        grade["life"] = max(grade["life"], int(fighter.get("max_hp") or fighter.get("hp") or 0))
        grade["ap"] = max(grade["ap"], int(fighter.get("ap") or fighter.get("max_ap") or 0))
        grade["mp"] = max(grade["mp"], int(fighter.get("mp") or fighter.get("max_mp") or 0))
        for spell in rich:
            spell_id = int(spell.get("id") or 0)
            if spell_id <= 0:
                continue
            if spell_id not in entry["spells"]:
                entry["spells"].append(spell_id)
            if str(spell_id) in levels:
                continue
            levels[str(spell_id)] = [{
                "grade": int(spell.get("level") or 1),
                "ap_cost": int(spell.get("ap_cost") or 0),
                "min_range": int(spell.get("min_range") or 0),
                "max_range": int(spell.get("range") or 0),
                "crit_probability": int(spell.get("critical_hit_probability") or 0),
                "max_cast_per_turn": int(spell.get("max_cast_per_turn") or 0),
                "damage": [{"element": element, "min": low, "max": high}
                           for element, low, high in _live_damage(spell.get("effects"))],
            }]

    # -- queries ------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._monsters)

    def name(self, monster_id: int) -> Optional[str]:
        entry = self._monsters.get(_key(monster_id))
        return entry.get("name") if entry else None

    def grades(self, monster_id: int) -> List[Grade]:
        entry = self._monsters.get(_key(monster_id))
        if not entry:
            return []
        out: List[Grade] = []
        for grade in entry.get("grades") or ():
            out.append(Grade(
                grade=int(grade.get("grade") or 1),
                level=int(grade.get("level") or 1),
                life=int(grade.get("life") or 0),
                ap=int(grade.get("ap") or 0),
                mp=int(grade.get("mp") or 0),
                resists=tuple((element, int(grade.get("%s_res" % element) or 0))
                              for element in ELEMENTS),
            ))
        return out

    def grade_at_level(self, monster_id: int, level: int) -> Optional[Grade]:
        """The grade whose level is nearest. The client sends a level, not a grade."""
        grades = self.grades(monster_id)
        if not grades:
            return None
        return min(grades, key=lambda g: (abs(g.level - int(level)), g.grade))

    def spells(self, monster_id: int, level: int) -> List[SpellRow]:
        """Every spell the monster can cast at this level, with its stats."""
        entry = self._monsters.get(_key(monster_id))
        if not entry:
            return []
        grade = self.grade_at_level(monster_id, level)
        if grade is None:
            return []
        out: List[SpellRow] = []
        for sid in entry.get("spells") or ():
            rows = self._levels.get(str(sid)) or []
            if not rows:
                continue
            # A spell's level rows are ordered by grade; use the nearest one.
            row = min(rows, key=lambda r: abs(int(r.get("grade") or 1) - grade.grade))
            damage = tuple((str(d.get("element")), int(d.get("min") or 0),
                            int(d.get("max") or 0))
                           for d in (row.get("damage") or ())
                           if d.get("element") in ELEMENTS)
            out.append(SpellRow(spell_id=int(sid),
                                ap_cost=int(row.get("ap_cost") or 0),
                                min_range=int(row.get("min_range") or 0),
                                max_range=int(row.get("max_range") or 0),
                                damage=damage))
        return out

    def reach(self, monster_id: int, level: int) -> Optional[int]:
        """Furthest cell the monster can damage in one turn: MP + longest range.

        Only damaging spells count, because the question this answers is "how
        far away am I still hit from". A monster whose spells are all buffs
        falls back to its longest *local* range so the answer is a number rather
        than a misleading ``None`` -- but a global-range spell is excluded from
        that fallback. Read literally, `Burst of Motivation` (a 4-AP ally buff at
        range 63) would give a 3-MP caster a reach of 66 and mark every cell on
        the map as threatened, which prices cover and distance as worthless.

        ``None`` when the monster is unknown or has only global-range spells,
        which is different from zero.
        """
        grade = self.grade_at_level(monster_id, level)
        if grade is None:
            return None
        spells = self.spells(monster_id, level)
        if not spells:
            return None
        local = [s for s in spells if s.max_range < GLOBAL_RANGE]
        if not local:
            return None
        damaging = [s for s in local if s.damage]
        pool = damaging or local
        return int(grade.mp + max(s.max_range for s in pool))

    def damage_per_turn(self, monster_id: int, level: int,
                        target_resist: Optional[Dict[str, int]] = None) -> Optional[float]:
        """Best damage the monster can deal in one turn on its AP budget.

        Greedy: repeatedly cast the spell with the highest damage per AP until
        AP runs out. A monster casts at most one spell per turn in the client
        unless ``max_cast_per_turn`` says otherwise, and the harvested data
        carries that as 0 for "no limit", so the budget is the binding
        constraint. This is an upper bound on a single target and is labelled
        as such -- it is not a threat model, it is the input to one.
        """
        grade = self.grade_at_level(monster_id, level)
        if grade is None:
            return None
        spells = [s for s in self.spells(monster_id, level) if s.ap_cost > 0]
        if not spells:
            return None
        resist = target_resist or {}
        per_ap = []
        for spell in spells:
            damage = spell.damage_against({}, resist) if resist else spell.expected_damage
            per_ap.append((damage / spell.ap_cost, spell.ap_cost, damage))
        per_ap.sort(reverse=True)
        budget = grade.ap
        total = 0.0
        while budget > 0:
            usable = [row for row in per_ap if row[1] <= budget]
            if not usable:
                break
            _rate, cost, damage = usable[0]
            total += damage
            budget -= cost
        return total


def _live_damage(effects: Any) -> List[Tuple[str, int, int]]:
    """Damage lines of one spell, from the client's effect payload.

    The roll convention is `battle_solver._roll`'s: ``dice_num`` is the low end
    and ``dice_side`` the high end, a zero side means a fixed roll, and an
    explicit positive ``value`` is used when there is no dice pair. Only the
    strongest element is returned -- see `from_live_corpus` for why.
    """
    per_element: Dict[str, Tuple[int, int]] = {}
    for effect in effects or ():
        if not isinstance(effect, dict):
            continue
        element = DAMAGE_EFFECTS.get(int(effect.get("effect_id") or 0))
        if element is None:
            continue
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
        seen = per_element.get(element, (0, 0))
        per_element[element] = (seen[0] + low, seen[1] + high)
    if not per_element:
        return []
    best = max(per_element, key=lambda key: sum(per_element[key]))
    return [(best, per_element[best][0], per_element[best][1])]


@lru_cache(maxsize=1)
def mechanics() -> MonsterMechanics:
    """Process-wide instance; the cache file is 10-20 MB."""
    return MonsterMechanics.load()


def threat_for(monster_id: int, level: int, cell: int,
               engine: Optional[MonsterMechanics] = None):
    """``map_field.EnemyThreat`` for one monster at one cell, or ``None``.

    Returning ``None`` rather than an empty threat is the whole point: an
    unknown monster must not be treated as harmless.
    """
    from combat.map_field import EnemyThreat

    engine = engine or mechanics()
    grade = engine.grade_at_level(monster_id, level)
    if grade is None:
        return None
    spells = [s for s in engine.spells(monster_id, level) if s.expected_damage > 0]
    if not spells:
        return None
    return EnemyThreat(cell=int(cell), mp=int(grade.mp),
                       spells=tuple((s.min_range, s.max_range, s.expected_damage)
                                    for s in spells))
