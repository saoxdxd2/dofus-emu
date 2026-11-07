"""Real Dofus Touch monster data, read from the tables the client itself serves.

The fetched corpus lives outside the repository (see the note in
`dofus-evidence/touch-data/manifest.json`) because `dofus-network/data/**` is picked up by
the installer's file walk and the payload budget is 60 MiB.

What is real here: species, boss flags, per-grade level/HP/AP/MP, the five element
resistances, PA/PM dodge, wisdom, and each monster's actual spell kit with its AP cost,
range, line/LoS rules and dice. What is still invented by the caller: initiative,
`power`, engagement distance, and how many monsters stand together -- the client's data
host publishes no monster-team or AI table (probed 2026-11-05: `MonsterTeams`,
`MonsterGroups`, `Teams`, `Groups` all answer 400, and `build/script.js` contains no
`MonsterTeam` string at all).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from combat.battle_solver import ELEMENT_BY_EFFECT, HEAL_EFFECT_IDS
from combat.encounter.mechanics import NAMED_KIND, MechanicVocabulary, mechanic_columns

SUMMON_EFFECTS = frozenset(eid for eid, kind in NAMED_KIND.items() if kind == "summon")

ELEMENTS = ("neutral", "earth", "fire", "water", "air")
ELEMENT_INDEX = {name: i for i, name in enumerate(ELEMENTS)}
DAMAGE_KINDS = {96: "WATER", 97: "EARTH", 98: "AIR", 99: "FIRE", 100: "NEUTRAL"}
# Effect ids read off the client's own Effects table (French text, 2026-11-05):
# 181 "Invoque : #1", 141 "Tue la cible", 401/1091 "Pose un glyphe de rang #2",
# 950 "Etat #3" / 951 "Enleve l'etat #3", 104 "Faire perdre un PA (esquive)",
# 112 "#1 Dommages" with characteristic 16, 792/950/951 carry a state id.
MECHANIC_BY_EFFECT = {
    181: "invoke", 141: "kill", 401: "glyph", 1091: "glyph",
    950: "state_add", 951: "state_remove", 132: "cleanse",
    4: "teleport", 5: "push", 6: "pull", 1041: "pushed", 1042: "pulled", 8: "swap",
    50: "carry", 84: "ap_steal", 101: "ap_loss", 133: "ap_loss", 168: "ap_loss",
    111: "ap_gain", 120: "ap_gain", 128: "mp_gain", 169: "mp_loss", 104: "tackled",
}
# The environment cannot spawn an actor, place a glyph or hold a state yet
# (`battle_solver`: "Traps, glyphs, swap, carry, states and area zones are still not
# planned"), so these are counted and reported instead of being priced as if they were
# ordinary damage. Faking them would teach the policy that a summon is a hit.
UNMODELLED = frozenset({"invoke", "glyph", "state_add", "state_remove", "swap", "carry"})
LETHAL = 9999.0          # retired: an instakill is the `kill` column now, see TouchData.slot
FEATURE_DAMAGE_CEILING = 301.0   # env.py observes log1p(damage)/log(301)


def _expected(roll: Dict[str, Any]) -> float:
    """Expected value of one damage/heal line, or 0.0 when it is not a die.

    `diceNum`/`diceSide` double as payload for non-rolling effects: a summon writes the
    monster id as `diceNum`, a glyph writes `10001d15000`. No Dofus die has four-digit
    sides, so anything outside a real die is not damage and must not be summed.
    """
    dice = int(roll.get("diceNum") or 0)
    side = int(roll.get("diceSide") or 0)
    base = float(roll.get("value") or 0)
    if dice and 1 <= side <= 100 and dice <= 100:
        return base + dice * (side + 1) / 2.0
    if base and base <= 1000.0:
        return base
    return 0.0


class TouchData:
    """The monsters, spell levels and effect vocabulary as the Touch client serves them."""

    def __init__(self, root: Path):
        root = Path(root)
        for name in ("monsters_fr.json", "spelllevels_fr.json", "effects_fr.json"):
            if not (root / name).is_file():
                raise FileNotFoundError("%s missing from %s -- run fetch_touch_data.py"
                                        % (name, root))
        self.monsters = json.loads((root / "monsters_fr.json").read_text(encoding="utf-8"))

        def optional(name: str) -> Dict[str, Any]:
            path = root / name
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

        # Only needed to pair a boss with the level its own dungeon recommends.
        self.subareas = optional("subareas_fr.json")
        self.dungeons = optional("dungeons_fr.json")
        self.levels = json.loads((root / "spelllevels_fr.json").read_text(encoding="utf-8"))
        self.effects = json.loads((root / "effects_fr.json").read_text(encoding="utf-8"))
        # The fetched JSON publishes an effect's fields but not its sentence, and the sentence is
        # the only thing that separates "Dommages réduits de 20" (a shield on the recipient) from
        # "+20 Dommages" (a buff on the dealer): both are characteristic 16, and typing the slot
        # instead of the verb would call a defensive buff a damage increase -- the same class of
        # error as the 9,999 kill sentinel. The names are in the same fetch's touch.db, so merge
        # them in when it is present and type nothing when it is not, rather than guessing.
        db = root / "touch.db"
        if db.is_file():
            import sqlite3
            con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
            try:
                for row in con.execute("select id, name from effects"):
                    key = str(row[0])
                    target = (self.effects.get(key) if isinstance(self.effects, dict)
                              else None)
                    if target is None and isinstance(self.effects, dict):
                        for k, v in self.effects.items():
                            if int(v.get("id") or -1) == row[0]:
                                target = v
                                break
                    if target is not None and row[1]:
                        target["name"] = row[1]
            finally:
                con.close()
        # The effect vocabulary is typed once, in `mechanics`, and shared: this module used to
        # carry its own id map, and two lists that can drift apart is how a summon ended up being
        # priced as a hit.
        self.vocab = MechanicVocabulary(self.effects)
        self.by_spell: Dict[int, List[Dict[str, Any]]] = {}
        for row in self.levels.values():
            self.by_spell.setdefault(int(row["spellId"]), []).append(row)
        for rows in self.by_spell.values():
            rows.sort(key=lambda r: int(r.get("id") or 0))

    def monster(self, monster_id: int) -> Optional[Dict[str, Any]]:
        return self.monsters.get(str(monster_id))

    def grades(self, monster: Dict[str, Any]) -> List[Dict[str, Any]]:
        return list(monster.get("grades") or [])

    def summon_line(self, ids: List[int], level: int) -> Tuple[int, float, float]:
        """(bodies, HP each, damage each) for one summon cast, all from the client's tables.

        A summon writes its species id into `diceNum` and one entry per body, so five entries of one
        id is five bodies. Their HP comes from the summoned species' own grade nearest the
        summoner's level (353 distinct species are summoned by the published monster table, and they
        carry 5-10 grades each, so the level match is real and not a flat guess). Their damage is the
        strongest line of that species' own kit: 212 of the 353 have one; the other 141 are class
        companions whose spell lists are sentinel ids, and for those the body arrives with HP and no
        attack, which is recorded in the corpus counters rather than filled in.
        """
        real = [int(i) for i in (ids or []) if int(i) > 0 and self.monster(int(i))]
        if not real:
            return 0, 0.0, 0.0
        monster = self.monster(real[0]) or {}
        grades = self.grades(monster)
        if not grades:
            return 0, 0.0, 0.0
        nearest = min(grades, key=lambda g: abs(int(g.get("level") or 0) - int(level)))
        grade_no = int(nearest.get("grade") or 1)
        kit = [r for r in self.kit(real[0], 4, grade_no) if r]
        damage = max([r["damage"] for r in kit], default=0.0)
        return len(real), float(nearest.get("lifePoints") or 0.0), float(damage)

    def kit(self, monster_id: int, slots: int, grade: int = 1) -> List[Dict[str, Any]]:
        """The monster's own spells, in the simulator's seven-number slot shape.

        Slot choice is by expected damage (then healing), because the model has a fixed
        number of spell heads; monsters whose kit is wider than `slots` are truncated and
        the caller reports how many were.
        """
        monster = self.monster(monster_id) or {}
        rows = []
        for spell_id in (monster.get("spells") or []):
            level = self._level_for(spell_id, grade)
            if level is None:
                continue
            row = self.slot(level)
            if row is not None:
                rows.append(row)
        rows.sort(key=lambda r: (-r["damage"], -r["heal"],
                                 -any(r.get(k) for k in ("kill", "push", "ap_delta",
                                                         "mp_delta", "summon_ids"))))
        empty = {"ap": 6.0, "rmin": 0.0, "rmax": 0.0, "damage": 0.0,
                 "heal": 0.0, "element": 0, "ally": False,
                 "kill": 0.0, "push": 0.0, "ap_delta": 0.0, "mp_delta": 0.0,
                 "duration": 0.0, "summon_ids": []}
        while len(rows) < slots:
            rows.append(dict(empty))
        return rows[:slots]

    def _level_for(self, spell_id: int, grade: int = 1) -> Optional[Dict[str, Any]]:
        """The spell level the game actually casts: always the first one.

        Monster *grade* is a difficulty tier and spell level is a progression step; mapping
        one onto the other was my error, and it cost 55% of the corpus its enemy damage.
        The live client settles it: 999 of 999 observed monster casts in the player's own
        fight boards carried spell_level 1, including grade-2 and grade-3 monsters.
        """
        rows = self.by_spell.get(int(spell_id)) or []
        return rows[0] if rows else None

    def slot(self, level: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        damage = 0.0
        heal = 0.0
        counts: Dict[str, float] = {}
        mechanics: set = set()
        for effect in (level.get("effects") or []):
            effect_id = int(effect.get("effectId") or -1)
            kind = MECHANIC_BY_EFFECT.get(effect_id)
            if kind:
                mechanics.add(kind)
            # "Tue la cible" used to be written into the damage line as a 9,999 sentinel, because
            # nothing else in the simulator could express removal. `env.step` now applies the kill
            # column for real, so the sentinel is a lie twice over: the fight reads a 9,999-damage
            # spell (which kills anyway, so the mechanic column never mattered and an ablation of
            # every enemy mechanic changed 0 of 1,024 boards' final HP), and the observation shows a
            # network a boss's most decisive spell as a number 33x any real hit. The kill is the
            # `kill` column now, and the damage line stays whatever the client actually priced.
            if int(effect.get("duration") or 0) != 0:
                continue          # over-time buff, not this cast's damage or heal
            amount = _expected(effect)
            if effect_id in HEAL_EFFECT_IDS or effect_id in (81,):
                heal += amount
                counts["HEAL"] = counts.get("HEAL", 0.0) + amount
            elif effect_id in DAMAGE_KINDS or effect_id in ELEMENT_BY_EFFECT:
                damage += amount
                element = DAMAGE_KINDS.get(effect_id) or ELEMENT_BY_EFFECT[effect_id][1]
                counts[element] = counts.get(element, 0.0) + amount
        # `getattr`, not `self.vocab`: `slot` is called in the suite without the constructor (the
        # purity of the row builder is what is under test), and `mechanic_columns` classifies an
        # unknown effect id from the client's own fields when it has no vocabulary, which is the
        # same answer -- just not the cached one.
        columns = mechanic_columns(level, getattr(self, "vocab", None))
        # A summon writes its species id into `diceNum` and one entry per body into `diceSide`, so
        # five entries with the same id is five of the same creature. Carrying the ids here (and not
        # a resolved stat line) is deliberate: the grade that decides the creature's HP belongs to
        # the *summoner's* level, which only the generator knows.
        columns["summon_ids"] = [int(e.get("diceNum") or 0) for e in (level.get("effects") or [])
                                 if int(e.get("effectId") or 0) in SUMMON_EFFECTS]
        summoned = [i for i in columns["summon_ids"] if i > 0 and self.monster(i)]
        has_mechanic = (any(columns[k] for k in ("kill", "push", "ap_delta", "mp_delta"))
                        or bool(summoned))
        if damage <= 0.0 and heal <= 0.0 and not has_mechanic:
            return None
        dominant = max(counts, key=counts.get) if counts else "NEUTRAL"
        element = 0 if dominant == "HEAL" else ELEMENT_INDEX.get(dominant.lower(), 0)
        return {"ap": float(level.get("apCost") or 0),
                "mechanics": sorted(mechanics),
                "unmodelled": sorted(mechanics & UNMODELLED),
                "saturates_feature": damage > FEATURE_DAMAGE_CEILING,
                "rmin": float(level.get("minRange") or 0),
                "rmax": float(level.get("range") or 0),
                "damage": damage, "heal": heal, "element": element,
                # A summon fills an empty cell next to the caster, not a foe: it has no hostile
                # target, and the mask's only two options are "some enemy in range" and "a friendly
                # actor". Reading it as friendly is the one that is true -- the cast ignores range
                # and lands on the caster's own side, which is what `env.step` then spends as
                # bodies. Left hostile instead, a 0-0 range summon would be uncastable, and the
                # mechanic would be typed, priced and never applied.
                "ally": (heal > damage) or bool(summoned), **columns}

    def spec(self, monster_id: int, grade_index: int = 0) -> Optional[Dict[str, Any]]:
        monster = self.monster(monster_id)
        if not monster or not self.grades(monster):
            return None
        grade = self.grades(monster)[min(grade_index, len(self.grades(monster)) - 1)]
        res = [int(grade.get(key) or 0) for key in
               ("neutralResistance", "earthResistance", "fireResistance",
                "waterResistance", "airResistance")]
        return {"level": int(grade.get("level") or 1),
                "hp": float(grade.get("lifePoints") or 1),
                "ap": float(grade.get("actionPoints") or 0),
                "mp": float(grade.get("movementPoints") or 0),
                "res": res, "pa_dodge": grade.get("paDodge"), "pm_dodge": grade.get("pmDodge"),
                "wisdom": grade.get("wisdom"), "boss": bool(monster.get("isBoss")),
                "name": monster.get("nameId"), "race": monster.get("race")}

    def levels_available(self, monster_id: int) -> int:
        monster = self.monster(monster_id) or {}
        return len(self.grades(monster))

    def monster_rows(self) -> List[Dict[str, Any]]:
        """The Touch table in the shape `build_dataset` already reads from the cache.

        The cache is the other game's community database, so the row keys differ; this is
        the one place that translates, and it keeps the generator's existing loop -- which
        indexes grades by level and clips raid-sized HP -- working on real data.
        """
        rows = []
        for monster in self.monsters.values():
            grades = []
            for grade in self.grades(monster):
                grades.append({
                    "grade": grade.get("grade"), "level": grade.get("level"),
                    "life_points": grade.get("lifePoints"),
                    "action_points": grade.get("actionPoints"),
                    "movement_points": grade.get("movementPoints"),
                    "neutral_res": grade.get("neutralResistance"),
                    "earth_res": grade.get("earthResistance"),
                    "fire_res": grade.get("fireResistance"),
                    "water_res": grade.get("waterResistance"),
                    "air_res": grade.get("airResistance"),
                    "fixed_res": 0, "pa_dodge": grade.get("paDodge"),
                    "pm_dodge": grade.get("pmDodge"), "wisdom": grade.get("wisdom"),
                    "grade_xp": grade.get("gradeXp")})
            if grades:
                rows.append({"id": int(monster["id"]), "name_en": monster.get("nameId"),
                             "name_fr": monster.get("nameId"), "is_boss": bool(monster.get("isBoss")),
                             "spells": monster.get("spells") or [], "grades": grades})
        return rows

    @staticmethod
    def _mechanics(level: Dict[str, Any]) -> set:
        out = set()
        for effect in (level.get("effects") or []):
            kind = MECHANIC_BY_EFFECT.get(int(effect.get("effectId") or -1))
            if kind:
                out.add(kind)
        return out

    def mechanics_of(self, monster_id: int) -> set:
        """Every mechanic kind this species' kit can put on the board.

        Reported even for spells the planner cannot execute yet (a summon is not a hit), so
        the corpus says what it does not model instead of quietly dropping it.
        """
        out = set()
        for spell_id in (self.monster(monster_id) or {}).get("spells") or []:
            level = self._level_for(spell_id)
            if level:
                out |= self._mechanics(level)
        return out

    def boss_levels(self) -> Dict[int, List[int]]:
        """Recommended dungeon level -> the boss species actually found in that dungeon.

        Dungeons and subareas are joined through the map ids both list. Without it a corpus
        of "bosses" means putting a 98,000 HP species in front of a level-12 team, which is
        not a hard fight but an arithmetic error.
        """
        subareas_by_map: Dict[int, List[Dict[str, Any]]] = {}
        for sub in self.subareas.values():
            for map_id in (sub.get("mapIds") or []):
                subareas_by_map.setdefault(int(map_id), []).append(sub)
        out: Dict[int, List[int]] = {}
        for dungeon in self.dungeons.values():
            species = set()
            for map_id in (dungeon.get("mapIds") or []):
                for sub in subareas_by_map.get(int(map_id), []):
                    species.update(int(s) for s in (sub.get("monsters") or []))
            level = int(dungeon.get("optimalPlayerLevel") or 0)
            bosses = sorted(s for s in species
                            if (self.monsters.get(str(s)) or {}).get("isBoss"))
            if level and bosses:
                out.setdefault(level, []).extend(b for b in bosses if b not in out[level])
        return out

    def summary(self) -> Dict[str, Any]:
        kits = [len(m.get("spells") or []) for m in self.monsters.values()]
        priced = sum(1 for m in self.monsters.values()
                     for sid in (m.get("spells") or [])
                     if self._level_for(sid, 1) and self.slot(self._level_for(sid, 1)))
        saturated = sum(1 for m in self.monsters.values()
                        for sid in (m.get("spells") or [])
                        for row in [self.slot(self._level_for(sid, 1) or {})]
                        if row and row.get("saturates_feature"))
        boss_ids = [int(m["id"]) for m in self.monsters.values() if m.get("isBoss")]
        mechanics: Dict[str, int] = {}
        for mid in boss_ids:
            for kind in self.mechanics_of(mid):
                mechanics[kind] = mechanics.get(kind, 0) + 1
        return {"monsters": len(self.monsters),
                "with_kit": sum(1 for k in kits if k),
                "bosses": sum(1 for m in self.monsters.values() if m.get("isBoss")),
                "grades": sum(len(m.get("grades") or []) for m in self.monsters.values()),
                "kit_slots_median": sorted(kits)[len(kits) // 2] if kits else 0,
                "kit_widest": max(kits) if kits else 0,
                "monster_spells_with_damage_or_heal": priced,
                "monster_spells_total": sum(kits),
                "effect_vocabulary": len(self.effects),
                "spells_beyond_the_damage_feature_ceiling": saturated,
                "boss_kits_over_4_spells": sum(1 for mid in boss_ids
                                               if len((self.monster(mid) or {}).get("spells") or []) > 4),
                "boss_mechanics": dict(sorted(mechanics.items(), key=lambda kv: -kv[1])),
                "dungeons_with_a_resolved_boss": len(self.boss_levels())}
