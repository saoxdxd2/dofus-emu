"""Turn the game's own fight traffic into combat training data.

Two read-only sources:

* ``play_dofus-*.jsonl`` contains the client's received fight messages. The
  spell-cast messages carry the real Touch spell id, source actor, target and
  destination cell. That is an observed-action corpus, not a guess.
* ``fight_boards.jsonl`` is written by the autopilot. The client's ``fight.state``
  now includes each fighter's spell descriptors, states and recent actions, so
  this builder can produce a monster -> spell catalogue and state/action rows.

Nothing here writes to the game. The play logs may contain account data
elsewhere, so only the extracted cast fields are copied; no raw lines are
written.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from combat.cli import run_main

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLAY_LOG = REPO_ROOT / "trajectories" / "play_dofus-05.jsonl"
DEFAULT_BOARDS = REPO_ROOT / "trajectories" / "fight_boards.jsonl"
DEFAULT_OUT = REPO_ROOT / "trajectories" / "live"

# How much damage the boards carried, and how much of it could be tied to the cast
# that produced it. Kept module-level so a run can report it: an empty
# `observed_damage` caused by a join that never matches reads exactly like a fight
# with no damage in it, and that ambiguity hid a broken join for a whole session.
ATTRIBUTION: Dict[str, int] = {"damage_rows": 0, "attributed": 0}

CAST_RE = re.compile(
    r'"sourceId":(?P<source>-?\d+).*?'
    r'"targetId":(?P<target>-?\d+).*?'
    r'"destinationCellId":(?P<cell>-?\d+).*?'
    r'"critical":(?P<critical>-?\d+).*?'
    r'"spellId":(?P<spell>\d+).*?'
    r'"spellLevel":(?P<level>\d+)'
)
AUTHOR_RE = re.compile(r'"authorId":(?P<author>-?\d+)')


def parse_play_log(path: Path, max_lines: Optional[int] = None
                   ) -> List[Dict[str, Any]]:
    """Extract received spell casts from a play-log JSONL."""
    rows: List[Dict[str, Any]] = []
    seen = set()
    with Path(path).open(encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            if max_lines is not None and number > max_lines:
                break
            if "GameActionFightSpellCastMessage" not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            event = row.get("event") or {}
            if event.get("kind") != "recv":
                continue
            args = event.get("args") or {}
            preview = args.get("preview")
            if not isinstance(preview, str):
                continue
            match = CAST_RE.search(preview)
            if not match:
                continue
            author = AUTHOR_RE.search(preview)
            cast = {
                "at": row.get("at"),
                "session": row.get("session"),
                "source_id": int(match.group("source")),
                "author_id": int(author.group("author")) if author else None,
                "target_id": int(match.group("target")),
                "destination_cell": int(match.group("cell")),
                "critical": int(match.group("critical")),
                "spell_id": int(match.group("spell")),
                "spell_level": int(match.group("level")),
            }
            key = tuple(cast[k] for k in ("at", "source_id", "spell_id",
                                          "target_id", "destination_cell"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(cast)
    return rows


def _spell_descriptor(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    if not raw.get("id"):
        return None
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "level": raw.get("level"),
        "ap_cost": raw.get("ap_cost"),
        "min_range": raw.get("min_range"),
        "range": raw.get("range"),
        "cast_in_line": raw.get("cast_in_line"),
        "cast_in_diagonal": raw.get("cast_in_diagonal"),
        "requires_los": raw.get("requires_los"),
        "max_cast_per_turn": raw.get("max_cast_per_turn"),
        "max_cast_per_target": raw.get("max_cast_per_target"),
        "min_cast_interval": raw.get("min_cast_interval"),
        "critical_hit_probability": raw.get("critical_hit_probability"),
        "effects": raw.get("effects") or [],
        "critical_effects": raw.get("critical_effects") or [],
    }


def build_from_boards(path: Path) -> Tuple[List[Dict[str, Any]],
                                           List[Dict[str, Any]],
                                           List[Dict[str, Any]],
                                           Dict[str, Dict[str, Any]]]:
    """Read the autopilot's board/outcome JSONL into four datasets.

    The fourth is ``observed_damage``: what an enemy spell actually took off us,
    keyed by ``name|level|spell_id``. It exists because the client never publishes
    a monster's spellbook for some monsters -- `Barbakle` carried one in 0 of 1,018
    recorded rows -- while it *does* publish the damage those spells dealt. The
    value ranker needs the damage, not the spellbook, so a spell id plus its
    observed hits is enough to price an enemy turn (ARCH_REASSESSMENT XII.18).
    """
    boards: Dict[Tuple[str, int], List[Dict[str, Any]]] = defaultdict(list)
    outcomes: Dict[str, Dict[str, Any]] = {}
    observed: Dict[str, Dict[str, Any]] = {}
    if not Path(path).exists():
        return [], [], [], {}
    with Path(path).open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("kind") == "fight_board":
                board = record.get("board") or {}
                fight_id = board.get("fight_id")
                if fight_id is None:
                    continue
                boards[(record.get("run") or "", int(fight_id))].append(record)
            elif record.get("kind") == "fight_outcome":
                run = record.get("run") or ""
                if run:
                    outcomes[run] = record

    monsters: Dict[int, Dict[str, Any]] = {}
    actions: List[Dict[str, Any]] = []
    fights: List[Dict[str, Any]] = []
    seen_actions = set()
    for (run, fight_id), records in boards.items():
        records.sort(key=lambda item: item.get("ts") or 0)
        deduped = []
        seen_states = set()
        for record in records:
            board = record.get("board") or {}
            key = json.dumps({
                "round": board.get("round"),
                "turn": board.get("current_fighter_id"),
                "fighters": [[f.get("id"), f.get("hp"), f.get("cell")]
                             for f in (board.get("fighters") or [])],
            }, sort_keys=True)
            if key in seen_states:
                continue
            seen_states.add(key)
            deduped.append(record)
            for fighter in board.get("fighters") or []:
                if fighter.get("team") != "enemy":
                    continue
                creature_id = fighter.get("creature_generic_id")
                if not creature_id:
                    continue
                entry = monsters.setdefault(int(creature_id), {
                    "creature_generic_id": int(creature_id),
                    "names": set(),
                    "levels": set(),
                    "is_boss": False,
                    "spells": {},
                    "observed_casts": 0,
                })
                if fighter.get("name"):
                    entry["names"].add(str(fighter["name"]))
                if fighter.get("level") is not None:
                    entry["levels"].add(int(fighter["level"]))
                entry["is_boss"] = entry["is_boss"] or bool(fighter.get("is_boss"))
                for raw in fighter.get("spells") or []:
                    descriptor = _spell_descriptor(raw)
                    if descriptor:
                        entry["spells"].setdefault(str(descriptor["id"]), descriptor)
            # What each cast actually took off us.
            #
            # This used to join the cast row to its life-points-lost row on `action_id`,
            # on the reading that the client stamps one id on both. Measured live on
            # 2026-11-10 against fight 875: all 12 cast rows carried action_id 300 while
            # all 8 damage rows carried 97 or 98, and every damage row had spell_id null.
            # The two are separate id spaces, so the join matched nothing and
            # `observed_damage` came out empty for every board -- silently, because an
            # empty join and a fight with no damage look the same in the output.
            #
            # The counters below are what tell them apart. Until a join key that exists
            # in the payload is found, a run reports how much damage it saw and how much
            # it could attribute, instead of returning nothing and saying nothing.
            damage_by_action: Dict[Any, float] = {}
            for row in board.get("last_actions") or []:
                action_id = row.get("action_id")
                loss = row.get("loss")
                if loss is not None:
                    ATTRIBUTION["damage_rows"] += 1
                if action_id is None or loss is None:
                    continue
                try:
                    damage_by_action[action_id] = (
                        damage_by_action.get(action_id, 0.0) + abs(float(loss)))
                except (TypeError, ValueError):
                    continue
            for action in board.get("last_actions") or []:
                spell_id = action.get("spell_id")
                source_id = action.get("source_id")
                if not spell_id or source_id is None:
                    continue
                fighters = {f.get("id"): f for f in (board.get("fighters") or [])}
                source = fighters.get(source_id)
                if not source or source.get("team") != "enemy":
                    continue
                key = (run, fight_id, board.get("round"), source_id, spell_id,
                       action.get("target_id"), action.get("destination_cell"))
                if key in seen_actions:
                    continue
                seen_actions.add(key)
                dealt = damage_by_action.get(action.get("action_id"))
                # Only a cast row owns the damage. The life-points-lost row that
                # carries the same action id also carries `spell_id`, so attributing
                # from every row would file the same hit twice -- and would file a
                # hit whose caster we never saw cast.
                action_type = str(action.get("type") or "")
                if dealt and (not action_type or "SpellCast" in action_type):
                    ATTRIBUTION["attributed"] += 1
                    # Keyed on name+level rather than the creature id: the id is what
                    # we want, but every row in the current corpus predates the page
                    # fix and carries null, and a damage sample is still a real
                    # observation under the weaker key.
                    slot_key = "%s|%s|%s" % (source.get("name") or "",
                                             source.get("level"), spell_id)
                    slot = observed.setdefault(slot_key, {
                        "name": source.get("name"),
                        "level": source.get("level"),
                        "creature_generic_id": source.get("creature_generic_id"),
                        "spell_id": int(spell_id),
                        "hits": 0, "total": 0.0, "min": None, "max": None,
                    })
                    slot["hits"] += 1
                    slot["total"] += dealt
                    slot["min"] = dealt if slot["min"] is None else min(slot["min"], dealt)
                    slot["max"] = dealt if slot["max"] is None else max(slot["max"], dealt)
                creature_id = source.get("creature_generic_id")
                actions.append({
                    "run": run,
                    "fight_id": fight_id,
                    "round": board.get("round"),
                    "turn": board.get("current_fighter_id"),
                    "source_id": source_id,
                    "creature_generic_id": creature_id,
                    "spell_id": spell_id,
                    "spell_level": action.get("spell_level"),
                    "target_id": action.get("target_id"),
                    "destination_cell": action.get("destination_cell"),
                    "critical": action.get("critical"),
                    "state": {
                        "my_cell": board.get("my_cell"),
                        "my_ap": board.get("my_ap"),
                        "my_mp": board.get("my_mp"),
                        "fighters": [{
                            "id": f.get("id"), "team": f.get("team"),
                            "cell": f.get("cell"), "hp": f.get("hp"),
                            "max_hp": f.get("max_hp"), "ap": f.get("ap"),
                            "mp": f.get("mp"), "level": f.get("level"),
                            "states": f.get("states"),
                        } for f in (board.get("fighters") or [])],
                    },
                })
                entry = monsters.get(int(creature_id)) if creature_id else None
                if entry is not None:
                    entry["observed_casts"] += 1
        fights.append({
            "run": run,
            "fight_id": fight_id,
            "outcome": (outcomes.get(run) or {}).get("outcome"),
            "boards": deduped,
        })

    for entry in monsters.values():
        entry["names"] = sorted(entry["names"])
        entry["levels"] = sorted(entry["levels"])
        entry["spell_ids"] = sorted(entry["spells"])
    for slot in observed.values():
        slot["mean"] = round(slot["total"] / max(1, slot["hits"]), 2)
        slot["total"] = round(slot["total"], 2)
    return list(monsters.values()), actions, fights, observed


def run(args: argparse.Namespace) -> int:
    started = time.time()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    casts = parse_play_log(Path(args.play_log)) if args.play_log else []
    if casts:
        with (out / "observed_casts.jsonl").open("w", encoding="utf-8") as handle:
            for row in casts:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    monsters, actions, fights, observed = build_from_boards(Path(args.boards))
    if monsters or observed:
        # Written when either half exists: on the current corpus every enemy row
        # predates the `creature_generic_id` fix, so `monsters` is empty while
        # `observed_damage` is not -- and the observed damage is the part the value
        # ranker can use.
        (out / "monster_spells.json").write_text(
            json.dumps({"source": "live_client", "monsters": monsters,
                        "observed_damage": [observed[key]
                                            for key in sorted(observed)]},
                       indent=1, ensure_ascii=False), encoding="utf-8")
    if actions:
        with (out / "monster_actions.jsonl").open("w", encoding="utf-8") as handle:
            for row in actions:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    if fights:
        with (out / "fights.jsonl").open("w", encoding="utf-8") as handle:
            for row in fights:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "play_log": str(args.play_log) if args.play_log else None,
        "boards": str(args.boards),
        "casts": len(casts),
        "unique_cast_spells": sorted({row["spell_id"] for row in casts}),
        "cast_sources": dict(Counter(row["source_id"] for row in casts)),
        "monsters": len(monsters),
        "observed_damage_spells": len(observed),
        # Damage seen vs damage that could be tied to a cast. If these disagree the
        # join key is wrong, not the fight: report both so an empty observed_damage
        # cannot be mistaken for "the enemies did no damage".
        "damage_rows_seen": ATTRIBUTION["damage_rows"],
        "damage_rows_attributed": ATTRIBUTION["attributed"],
        "monster_actions": len(actions),
        "fights": len(fights),
        "elapsed_s": round(time.time() - started, 1),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1),
                                      encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--play-log", default=str(DEFAULT_PLAY_LOG))
    p.add_argument("--boards", default=str(DEFAULT_BOARDS))
    p.add_argument("--out", default=str(DEFAULT_OUT))
    return p


def main(argv=None):
    return run_main(build_parser, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
