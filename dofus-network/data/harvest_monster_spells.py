"""Harvest monster spell lists and spell levels from the DofusDB mirror.

Why this exists
---------------
`data/cache/monsters_full.json` carries HP, AP, MP and resistances per grade and
nothing else. A live board is worse: every enemy fighter arrives with
``spell_ids: []`` and ``spells: []``, and often with ``mp: null`` as well. So at
the moment a fight is decided, the system does not know what an enemy can cast
or how far it can reach. It cannot answer "is this cell inside that monster's
range", which is the question every positioning decision turns on.

The consequence is measurable and was measured on monster 31, Blue Larva: it
casts ``10198`` "Disabling Splutter" at 3 AP with range 1-6 and it has 2 MP, so
its reach is eight cells. The character's longest spell reaches five. There is
no distance at which that monster cannot answer, and nothing in the encoder
says so.

What this writes
----------------
``data/cache/monster_spells.json``

    monsters[<id>] = {name, is_boss, grades:[{grade, level, life, ap, mp,
                      resists}], spells:[<spell id>]}
    spell_levels[<spell id>] = [{grade, ap_cost, min_range, max_range,
                                 crit_probability, max_cast_per_turn,
                                 damage:[{element, min, max}]}]

and the same two tables into ``data/knowledge/dofus_knowledge.sqlite`` so a
query can join a monster to its reach without loading the JSON.

Provenance
----------
The source is ``api.dofusdb.fr``, the same mirror the monster cache already
came from. That it is the Touch dataset is checked rather than assumed: the
harvester compares every monster's grade-1 stats against the existing cache and
reports how many disagree. A source that drifts from the client's own numbers
is a source that will teach the model the wrong reach.

    python -m data.harvest_monster_spells --check      # compare only, no write
    python -m data.harvest_monster_spells --write
    python -m data.harvest_monster_spells --write --limit 300
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

CACHE_DIR = Path(__file__).resolve().parent / "cache"
KNOWLEDGE = Path(__file__).resolve().parent / "knowledge" / "dofus_knowledge.sqlite"
OUT_FILE = CACHE_DIR / "monster_spells.json"

BASE = "https://api.dofusdb.fr"
PAGE = 50
SPELL_BATCH = 40

# The client reaches the internet through a SOCKS tunnel; a direct connection is
# refused by the host. An empty value means "go direct", which is what a CI box
# with ordinary egress would use.
PROXY = os.environ.get("DOFUS_PROXY", "socks5h://127.0.0.1:40000")


def fetch(path: str, params: Optional[Sequence[Tuple[str, str]]] = None,
          timeout: int = 40, attempts: int = 3) -> Dict[str, Any]:
    """One API call, retried. `path` is relative to the mirror root."""
    url = BASE + path
    args = ["curl", "-sS", "--max-time", str(timeout), "-H", "Accept: application/json"]
    if PROXY:
        args += ["-x", PROXY]
    if params:
        args += ["-G"]
        for key, value in params:
            args += ["--data-urlencode", "%s=%s" % (key, value)]
    args.append(url)
    last = ""
    for attempt in range(attempts):
        try:
            out = subprocess.run(args, capture_output=True, timeout=timeout + 10)
            text = out.stdout.decode("utf-8", "replace")
            if out.returncode == 0 and text.strip().startswith("{"):
                return json.loads(text)
            last = text[:200] or out.stderr.decode("utf-8", "replace")[:200]
        except Exception as exc:  # pragma: no cover - network
            last = "%s: %s" % (type(exc).__name__, exc)
        time.sleep(0.6 * (attempt + 1))
    raise RuntimeError("fetch failed for %s: %s" % (path, last))


def _paged(path: str, total_limit: Optional[int] = None) -> Iterable[Dict[str, Any]]:
    skip = 0
    seen = 0
    while True:
        page = fetch(path, [("$limit", str(PAGE)), ("$skip", str(skip))])
        rows = page.get("data") or []
        if not rows:
            return
        for row in rows:
            yield row
            seen += 1
            if total_limit is not None and seen >= total_limit:
                return
        skip += PAGE
        total = int(page.get("total") or 0)
        if skip >= total:
            return


def _grade_row(grade: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "grade": int(grade.get("grade") or 1),
        "level": int(grade.get("level") or 1),
        "life": int(grade.get("lifePoints") or 0),
        "ap": int(grade.get("actionPoints") or 0),
        "mp": int(grade.get("movementPoints") or 0),
        "neutral_res": int(grade.get("reductionNeutral") or 0),
        "earth_res": int(grade.get("reductionEarth") or 0),
        "fire_res": int(grade.get("reductionFire") or 0),
        "water_res": int(grade.get("reductionWater") or 0),
        "air_res": int(grade.get("reductionAir") or 0),
    }


# Action ids that carry damage. Dofus encodes an element as a separate
# `effectElement` field, so the element is read from there rather than from the
# id, which is the mistake that makes "damage" look element-less.
DAMAGE_ACTIONS = {96, 97, 98, 99, 100, 101, 102, 103, 104, 105,
                  108, 109, 110, 111, 112, 113, 114, 115,
                  116, 117, 118, 119, 120, 121, 122, 123, 124, 125,
                  126, 127, 128, 129, 130, 131, 132, 133, 134, 135,
                  136, 137, 138, 139, 140, 141, 142, 143, 144, 145,
                  146, 147, 148, 149, 150, 151, 152, 153, 154, 155}
ELEMENT_NAMES = {0: "neutral", 1: "earth", 2: "fire", 3: "water", 4: "air"}


def _damage_rows(effects: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for effect in effects or ():
        action = int(effect.get("actionId") or 0)
        if action not in DAMAGE_ACTIONS:
            continue
        element = ELEMENT_NAMES.get(int(effect.get("effectElement") or 0))
        if element is None:
            continue
        # `diceNum` rolls of `diceSide` faces, plus a flat `value`. A level with
        # neither is a percentage effect and is not damage.
        dice_num = int(effect.get("diceNum") or 0)
        dice_side = int(effect.get("diceSide") or 0)
        value = int(effect.get("value") or 0)
        if dice_num <= 0 or dice_side <= 0:
            if value <= 0:
                continue
            rows.append({"element": element, "min": value, "max": value})
            continue
        rows.append({"element": element, "min": dice_num, "max": dice_num * dice_side})
    return rows


def _level_row(level: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "grade": int(level.get("grade") or 1),
        "ap_cost": int(level.get("apCost") or 0),
        "min_range": int(level.get("minRange") or 0),
        "max_range": int(level.get("range") or 0),
        "crit_probability": int(level.get("criticalHitProbability") or 0),
        "max_cast_per_turn": int(level.get("maxCastPerTurn") or 0),
        "damage": _damage_rows(level.get("effects") or ()),
    }


def harvest(limit: Optional[int] = None, log=print) -> Dict[str, Any]:
    monsters: Dict[str, Any] = {}
    spell_ids: set = set()
    log("harvesting monsters ...")
    for index, row in enumerate(_paged("/monsters", limit)):
        mid = str(row.get("id"))
        name = (row.get("name") or {}).get("en") or (row.get("name") or {}).get("fr") or mid
        spells = [int(s) for s in (row.get("spells") or [])]
        spell_ids.update(spells)
        monsters[mid] = {
            "name": name,
            "is_boss": bool(row.get("isBoss")),
            "grades": [_grade_row(g) for g in (row.get("grades") or [])],
            "spells": spells,
        }
        if index % 500 == 0 and index:
            log("  %d monsters, %d distinct spells" % (index, len(spell_ids)))
    log("monsters: %d, distinct spells: %d" % (len(monsters), len(spell_ids)))

    ids = sorted(spell_ids)
    levels: Dict[str, List[Dict[str, Any]]] = {}
    log("harvesting %d spell level sets ..." % len(ids))
    for start in range(0, len(ids), SPELL_BATCH):
        batch = ids[start:start + SPELL_BATCH]
        # 40 ids times up to ~10 grades is 400 rows; asking for 160 silently
        # truncated whole spells and left monsters with no reach at all.
        params: List[Tuple[str, str]] = [("$limit", "1000")]
        params += [("spellId[$in][]", str(sid)) for sid in batch]
        page = fetch("/spell-levels", params)
        for row in page.get("data") or []:
            sid = str(row.get("spellId"))
            levels.setdefault(sid, []).append(_level_row(row))
        if start % (SPELL_BATCH * 10) == 0:
            log("  %d/%d spell ids, %d level rows"
                % (start + len(batch), len(ids), sum(len(v) for v in levels.values())))
    for rows in levels.values():
        rows.sort(key=lambda r: r["grade"])
    log("spell levels: %d spells" % len(levels))
    return {
        "source": BASE,
        "retrieved_on": time.strftime("%Y-%m-%d"),
        "monsters": monsters,
        "spell_levels": levels,
    }


def compare(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Check the harvested stats against the cache the client already trusts."""
    existing = {}
    path = CACHE_DIR / "monsters_full.json"
    if path.exists():
        for row in json.loads(path.read_text(encoding="utf-8")):
            existing[str(row.get("id"))] = row
    agree = disagree = absent = 0
    examples: List[str] = []
    for mid, monster in payload["monsters"].items():
        old = existing.get(mid)
        if not old or not old.get("grades") or not monster["grades"]:
            absent += 1
            continue
        a = old["grades"][0]
        b = monster["grades"][0]
        same = (int(a.get("level") or 0) == b["level"]
                and int(a.get("life_points") or 0) == b["life"]
                and int(a.get("action_points") or 0) == b["ap"]
                and int(a.get("movement_points") or 0) == b["mp"])
        if same:
            agree += 1
        else:
            disagree += 1
            if len(examples) < 5:
                examples.append("%s %s: cache L%d/%dHP/%dAP/%dMP vs mirror L%d/%dHP/%dAP/%dMP"
                                % (mid, monster["name"], a.get("level"), a.get("life_points"),
                                   a.get("action_points"), a.get("movement_points"),
                                   b["level"], b["life"], b["ap"], b["mp"]))
    with_spells = sum(1 for m in payload["monsters"].values() if m["spells"])
    resolved = 0
    unresolved = 0
    for monster in payload["monsters"].values():
        if not monster["spells"]:
            continue
        missing = [sid for sid in monster["spells"]
                   if str(sid) not in payload["spell_levels"]]
        if missing:
            unresolved += 1
        else:
            resolved += 1
    return {
        "agree": agree,
        "disagree": disagree,
        "absent": absent,
        "monsters_with_spells": with_spells,
        "monsters": len(payload["monsters"]),
        "spell_level_sets": len(payload["spell_levels"]),
        "reach_known": resolved,
        "reach_unknown": unresolved,
        "examples": examples,
    }


def write_sqlite(payload: Dict[str, Any], path: Path) -> Dict[str, int]:
    con = sqlite3.connect(str(path))
    cur = con.cursor()
    cur.execute("DROP TABLE IF EXISTS monster_spells")
    cur.execute("DROP TABLE IF EXISTS spell_levels")
    cur.execute("""CREATE TABLE monster_spells (
        monster_id INTEGER, name TEXT, is_boss INTEGER,
        grade INTEGER, level INTEGER, life INTEGER, ap INTEGER, mp INTEGER,
        spell_ids TEXT)""")
    cur.execute("""CREATE TABLE spell_levels (
        spell_id INTEGER, grade INTEGER, ap_cost INTEGER,
        min_range INTEGER, max_range INTEGER, crit_probability INTEGER,
        max_cast_per_turn INTEGER, damage_json TEXT)""")
    monster_rows = []
    for mid, monster in payload["monsters"].items():
        ids = json.dumps(monster["spells"])
        for grade in monster["grades"]:
            monster_rows.append((int(mid), monster["name"], int(monster["is_boss"]),
                                 grade["grade"], grade["level"], grade["life"],
                                 grade["ap"], grade["mp"], ids))
    cur.executemany("INSERT INTO monster_spells VALUES (?,?,?,?,?,?,?,?,?)", monster_rows)
    level_rows = []
    for sid, levels in payload["spell_levels"].items():
        for level in levels:
            level_rows.append((int(sid), level["grade"], level["ap_cost"],
                               level["min_range"], level["max_range"],
                               level["crit_probability"], level["max_cast_per_turn"],
                               json.dumps(level["damage"])))
    cur.executemany("INSERT INTO spell_levels VALUES (?,?,?,?,?,?,?,?)", level_rows)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_monster_spells ON monster_spells(monster_id, grade)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_spell_levels ON spell_levels(spell_id, grade)")
    con.commit()
    con.close()
    return {"monster_rows": len(monster_rows), "level_rows": len(level_rows)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the cache and the tables")
    parser.add_argument("--check", action="store_true", help="compare against the existing cache")
    parser.add_argument("--limit", type=int, default=None, help="stop after N monsters")
    args = parser.parse_args(argv)

    payload = harvest(limit=args.limit)
    report = compare(payload)
    print("\n=== provenance check against data/cache/monsters_full.json")
    print("  monsters compared        : %d" % (report["agree"] + report["disagree"]))
    print("  grade-1 stats agree      : %d" % report["agree"])
    print("  grade-1 stats disagree   : %d" % report["disagree"])
    print("  not in the cache         : %d" % report["absent"])
    print("  monsters with spells     : %d / %d"
          % (report["monsters_with_spells"], report["monsters"]))
    print("  reach fully resolved     : %d" % report["reach_known"])
    print("  reach still unknown      : %d" % report["reach_unknown"])
    print("  spell level sets         : %d" % report["spell_level_sets"])
    for line in report["examples"]:
        print("    ! %s" % line)

    if not args.write:
        print("\n(dry run: pass --write to store)")
        return 0

    OUT_FILE.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print("\nwrote %s (%.1f MB)" % (OUT_FILE, OUT_FILE.stat().st_size / 1e6))
    if KNOWLEDGE.exists():
        counts = write_sqlite(payload, KNOWLEDGE)
        print("wrote %d monster rows and %d spell-level rows into %s"
              % (counts["monster_rows"], counts["level_rows"], KNOWLEDGE.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
