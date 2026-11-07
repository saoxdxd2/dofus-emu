"""Pre-fight planning for a named Dofus Touch boss, from the client's own tables.

Why this module exists and what it refuses to do
------------------------------------------------
The fight brain can be scored after the fact and edited afterwards; what it could not do until now
is answer the question a player asks *before* entering a room: "can I win this, and if not, what
would make it winnable". The measured reason that question matters is in ARCH_REASSESSMENT section
N.11.8: on the Touch boss corpus the monsters kill a solo character before it ever gets a decision on
38.4% of the boards where they open, and those boards end with a **0.000** win rate -- no policy,
trained or edited, gets a turn there. The same section also shows that one turn of kit arithmetic
predicts the simulator's winner at AUC 0.824, so a plan does not have to roll the fight out to say
something quantitative.

So this module does two things and keeps them apart:

* the **arithmetic** (opening damage, time-to-kill both ways, hp ratio) computed from the client's
  monster grades and spell kits via `TouchData`, and the character's own numbers via the single
  shared definition `build_dataset.player_stats` -- not a second copy;
* the **verification**: the board is assembled as an encounter spec/tables batch and played to a
  terminal result with the shipped action heads (numpy branch, so it runs on an installed copy)
  against the scripted monster policy.

The estimate is only reported alongside the outcome it predicted. A verdict backed by neither is a
guess, and `readiness()` raises rather than produces one. What is still *not* here: a real monster
AI (the scripted heuristic is ours), a party, summons/states/glyphs, or a live fight log; those are
the fidelity gaps named in the returned `limitations` field, because a plan that silently assumes
them away is how you get a confident wrong answer.

    # from <repo>/dofus-network, with the Touch tables where fetch_touch_data.py put them:
    python -m combat.preflight --touch-dir ../../dofus-evidence/touch-data \
        --dungeon 1 --level 60
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from combat.encounter.backend import XP, tree_map
from combat.encounter.build_dataset import ELEMENTS, load_class_loadouts, pick_loadout, player_stats
from combat.encounter.checkpoint import load_params_config
from combat.encounter.env import (Config, action_masks, initial_state, observe,
                                  step)
from combat.encounter.heuristic import heuristic_actions
from combat.encounter.model import forward, greedy_actions
from combat.encounter.strike import pre_fight, round_strikes
from combat.encounter.touch_source import TouchData

# This file sits at <repo>/dofus-network/combat/preflight.py, and the brain's own data lives under
# dofus-network/, so the walk stops one level above the repository root. Getting this wrong points
# the planner at a models/ directory that does not exist.
BRAIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE_DIR = BRAIN_ROOT / "data" / "cache"
DEFAULT_CHECKPOINT = BRAIN_ROOT / "models" / "encounter_selfplay.pkl"
MAX_DIST = 12

# The distance a planner assumes, in cells. The corpus draws the opening distance uniformly from
# 1..8 (build_dataset), so 4 is its expectation rounded; distance is the assumption that most
# changes an opening-damage estimate, so it is a parameter, reported in the output, and never
# buried in a formula.
DEFAULT_DISTANCE = 4

LIMITATIONS = (
    "monster side is combat.encounter.heuristic, not the client's monster AI (unobserved: #127)",
    "solo character only: no party, no summons, no consumables",
    "summons, states, glyphs and push/swap mechanics are in the kits but not modelled by the env",
    "player per-element resistance is not published by the client; assumed 0",
    "outcome is the abstract encounter simulator, not a live fight",
    "distance is actor-local, not pairwise geometry or a verified client placement cell",
    "strike clocks are experimental diagnostics, not validated real fight durations",
)


def _rng_free_loadout(rows: List[Dict[str, Any]], slots: int, level: int
                      ) -> List[Dict[str, Any]]:
    """The deterministic build for a class at a level: the corpus's own picker, fixed seed.

    `pick_loadout` randomises which element the character focuses. A pre-fight plan must not move
    between two calls, so the seed is pinned and the same build is reproducible; the element choice
    is reported so the plan says *which* build it priced.
    """
    return pick_loadout(rows, slots, np.random.default_rng(11), level)


class Preflight:
    def __init__(self, touch_dir: pathlib.Path, cache_dir: pathlib.Path = CACHE_DIR,
                 checkpoint: pathlib.Path = DEFAULT_CHECKPOINT, slots: int = 4,
                 lang: str = "en", enemies: int = 4):
        self.touch = TouchData(pathlib.Path(touch_dir))
        self.cache_dir = pathlib.Path(cache_dir)
        self.slots = int(slots)
        self.lang = lang
        self.cfg = Config(allies=1, enemies=enemies, spells=slots, max_rounds=24,
                           max_dist=MAX_DIST)
        self.params, params_cfg = load_params_config(pathlib.Path(checkpoint))
        if (params_cfg.spells, params_cfg.enemies) != (self.cfg.spells, self.cfg.enemies):
            raise ValueError("checkpoint geometry %s does not match the planner's %s"
                             % ((params_cfg.spells, params_cfg.enemies),
                                (self.cfg.spells, self.cfg.enemies)))
        self.checkpoint = pathlib.Path(checkpoint)
        self._dungeons = None
        self._quests = None

    # -- client tables ---------------------------------------------------------------
    def dungeons(self) -> List[Dict[str, Any]]:
        if self._dungeons is None:
            path = self.cache_dir / "dungeons_full.json"
            if not path.is_file():
                raise FileNotFoundError("%s missing -- it is the dungeon to boss join" % path)
            self._dungeons = json.loads(path.read_text(encoding="utf-8"))
        return self._dungeons

    def quests(self) -> List[Dict[str, Any]]:
        if self._quests is None:
            path = self.cache_dir / "quests_full.json"
            self._quests = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        return self._quests

    def dungeon(self, dungeon_id: int) -> Dict[str, Any]:
        for row in self.dungeons():
            if int(row.get("id") or -1) == int(dungeon_id):
                return row
        raise KeyError("dungeon %s is not in dungeons_full.json" % dungeon_id)

    def _name(self, row: Dict[str, Any]) -> str:
        name = row.get("name")
        if isinstance(name, dict):
            return str(name.get(self.lang) or name.get("en") or name.get("fr") or "?")
        return str(name or "?")

    def challenges(self, dungeon_id: int) -> List[Dict[str, Any]]:
        """Quest steps that point at this dungeon, with their machine-readable objectives.

        Joining on map ids rather than on names: `dungeons_full.map_ids` and the quest step
        objective `map_id` are the same id space, and a name join across five languages is what
        made an earlier resolution rate read 0%.
        """
        room = {int(m) for m in (self.dungeon(dungeon_id).get("map_ids") or [])}
        room |= {int(self.dungeon(dungeon_id).get("entrance_map_id") or 0)}
        hits = []
        for quest in self.quests():
            if not quest.get("is_dungeon"):
                continue
            for step_row in (quest.get("steps") or []):
                maps = {int(o.get("map_id")) for o in (step_row.get("objectives") or [])
                        if o.get("map_id")}
                if room & maps:
                    hits.append({"quest_id": int(quest["id"]), "quest": self._name(quest),
                                 "step_id": step_row.get("id"), "step": self._name(step_row),
                                 "optimal_level": step_row.get("optimal_level"),
                                 "party": bool(quest.get("is_party")),
                                 "objective_types": sorted({int(o.get("type_id") or -1)
                                                            for o in (step_row.get("objectives") or [])})})
        return hits

    # -- board assembly --------------------------------------------------------------
    def board(self, monster_ids: List[int], player_level: int,
              player_class: Optional[str] = None, distance: int = DEFAULT_DISTANCE,
              grade_index: int = 0, player_opens: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """One deterministic encounter batch: this roster, this character, at this distance.

        Shaped exactly like `build_dataset`'s output so the env, the model and the harness all accept
        it unchanged -- a plan that invented its own tensor layout would drift from the game it plans.
        Built as numpy and handed over through the backend shim at the end: the assembly writes into
        the arrays slot by slot, which jax arrays refuse to do, while `env.step` needs whatever type
        the active branch expects (mixing them is what shows up as `AttributeError: 'numpy.ndarray'
        object has no attribute 'at'`).
        """
        n = self.cfg.n_actors
        spec = {k: np.zeros((1, n), dtype=np.float32) for k in
                ("hp", "max_hp", "ap_max", "mp_max", "power", "level", "initiative", "dist")}
        spec["res"] = np.zeros((1, n, len(ELEMENTS)), dtype=np.float32)
        spec["alive"] = np.zeros((1, n), dtype=bool)
        spec["first_team"] = np.zeros((1,), dtype=np.int32)
        tables = {
            "spell_ap": np.zeros((1, n, self.cfg.spells), dtype=np.float32),
            "spell_rmin": np.zeros((1, n, self.cfg.spells), dtype=np.float32),
            "spell_rmax": np.zeros((1, n, self.cfg.spells), dtype=np.float32),
            "spell_damage": np.zeros((1, n, self.cfg.spells), dtype=np.float32),
            "spell_heal": np.zeros((1, n, self.cfg.spells), dtype=np.float32),
            "spell_element": np.zeros((1, n, self.cfg.spells), dtype=np.int32),
            "spell_ally": np.zeros((1, n, self.cfg.spells), dtype=bool),
        }

        classes = load_class_loadouts(player_level)
        if not classes:
            raise ValueError("no class spells at level %s" % player_level)
        if player_class:
            chosen = [c for c in classes if c[0].lower() == player_class.lower()]
            if not chosen:
                raise ValueError("%s is not one of %s" % (player_class, sorted(c[0] for c in classes)))
        else:
            # The class with the highest expected one-cast damage at that level: a planner that
            # picked an arbitrary class would be reporting an accident of table order.
            chosen = [max(classes, key=lambda c: max((r["damage"] for r in c[1]), default=0.0))]
        cls_name, rows = chosen[0]
        stats = player_stats(player_level)
        spec["hp"][0, 0] = spec["max_hp"][0, 0] = stats["hp"]
        spec["ap_max"][0, 0] = stats["ap_max"]
        spec["mp_max"][0, 0] = stats["mp_max"]
        spec["power"][0, 0] = stats["power"]
        spec["level"][0, 0] = stats["level"]
        spec["dist"][0, 0] = distance
        spec["alive"][0, 0] = True
        loadout = _rng_free_loadout(rows, self.cfg.spells, player_level)
        for s, row in enumerate(loadout):
            tables["spell_ap"][0, 0, s] = row["ap"]
            tables["spell_rmin"][0, 0, s] = row["rmin"]
            tables["spell_rmax"][0, 0, s] = row["rmax"]
            tables["spell_damage"][0, 0, s] = row["damage"]
            tables["spell_heal"][0, 0, s] = row["heal"]
            tables["spell_element"][0, 0, s] = row["element"]
            tables["spell_ally"][0, 0, s] = row["ally"]

        # Initiative: the env resolves the acting team's whole turn in slot order, so "who opens"
        # is the difference between the player taking a free hit and dealing one.
        spec["initiative"][0, 0] = player_level + (10 if player_opens else 0)
        enemy_inits = []
        placed = 0
        for mid in monster_ids:
            if placed >= self.cfg.enemies:
                break
            monster = self.touch.spec(int(mid), grade_index)
            if monster is None:
                continue
            slot = 1 + placed
            kit = self.touch.kit(int(mid), self.cfg.spells, 1)
            if not any(r["damage"] for r in kit):
                # A monster with nothing to throw would make the room look safe for the wrong
                # reason; the planner's roster is the dungeon's, so it is reported, not padded.
                placed += 1
                continue
            spec["hp"][0, slot] = spec["max_hp"][0, slot] = monster["hp"]
            spec["ap_max"][0, slot] = min(12.0, max(3.0, monster["ap"]))
            spec["mp_max"][0, slot] = min(6.0, max(1.0, monster["mp"]))
            spec["power"][0, slot] = 10.0 + 1.0 * monster["level"]
            spec["res"][0, slot] = monster["res"][:len(ELEMENTS)]
            spec["dist"][0, slot] = distance
            spec["initiative"][0, slot] = monster["level"]
            spec["alive"][0, slot] = True
            spec["level"][0, slot] = monster["level"]
            for s, row in enumerate(kit):
                tables["spell_ap"][0, slot, s] = row["ap"]
                tables["spell_rmin"][0, slot, s] = row["rmin"]
                tables["spell_rmax"][0, slot, s] = row["rmax"]
                tables["spell_damage"][0, slot, s] = row["damage"]
                tables["spell_heal"][0, slot, s] = row["heal"]
                tables["spell_element"][0, slot, s] = row["element"]
                tables["spell_ally"][0, slot, s] = row["ally"]
            enemy_inits.append(float(monster["level"]))
            placed += 1
        if placed == 0:
            raise ValueError("roster %s produced no modellable monster" % list(monster_ids))
        spec["first_team"][0] = 0 if player_opens else 1
        self.board_roster = [(int(mid), self.touch.spec(int(mid), grade_index))
                             for mid in monster_ids
                             if self.touch.spec(int(mid), grade_index) is not None]
        self.board_class = cls_name
        self.board_loadout = loadout
        return (tree_map(XP.asarray, spec), tree_map(XP.asarray, tables))

    # -- estimate and verification ---------------------------------------------------
    def _scripted(self, state, tables):
        """The scripted opponent, in the branch's array type.

        `heuristic_actions` imports jax by name; under DOFUS_FIGHT_BACKEND=numpy its arrays have to
        come back to numpy before `step`, whose indexed updates are numpy in-place writes.
        """
        return {k: XP.asarray(v) for k, v in heuristic_actions(state, tables, self.cfg).items()}

    def _policy(self, state, tables):
        obs, glob, attention = observe(state, tables, self.cfg)
        logits = forward(self.params, obs, glob, attention)
        return greedy_actions(logits, action_masks(state, tables, self.cfg), self.cfg)

    def play(self, spec, tables, seeds: int = 1) -> Dict[str, Any]:
        """Play this board to a terminal result: the shipped heads versus the scripted monsters."""
        state = initial_state(spec, tables, self.cfg)
        steps = 0
        while not bool(np.asarray(state["done"]).all()) and steps < 2 * self.cfg.max_rounds:
            policy = self._policy if int(np.asarray(state["acting_team"])[0]) == 0 else self._scripted
            state = step(state, policy(state, tables), tables, self.cfg)
            steps += 1
        winner = int(np.asarray(state["winner"])[0])
        if not bool(np.asarray(state["done"]).all()) or winner not in (0, 1, 2):
            raise ValueError("the board did not reach a terminal state in %d turns" % steps)
        return {"winner": winner, "outcome": {0: "win", 1: "loss", 2: "draw"}[winner],
                "turns": steps, "rounds": int(np.asarray(state["round"])[0]),
                "player_hp_left": round(float(np.asarray(state["hp"])[0, 0]), 1),
                "seeds": seeds}

    def estimate(self, spec, tables, player_opens: bool) -> Dict[str, Any]:
        """Each side's one-turn output, measured from the fresh state with the env's own primitives.

        Both numbers come from a single `step` of the real environment -- the monsters' scripted turn
        and the shipped heads' turn, each from the untouched opening state -- so neither side's
        estimate depends on what the other side just did. `player_opens` then only decides whether
        the first monster turn arrives before the player has acted, which is the ambush question
        section N.11.8 measured.
        """
        state = initial_state(spec, tables, self.cfg)
        hp0 = np.asarray(state["hp"])[0].astype(float)
        enemy_slots = [s for s in range(1, len(hp0)) if hp0[s] > 0]
        monster_spec = dict(spec, first_team=XP.ones_like(spec["first_team"]))
        monster_state = initial_state(monster_spec, tables, self.cfg)
        after_monsters = step(monster_state, self._scripted(monster_state, tables), tables, self.cfg)
        hp_m = np.asarray(after_monsters["hp"])[0].astype(float)
        monster_turn = float(max(0.0, hp0[0] - hp_m[0]))
        player_spec = dict(spec, first_team=XP.zeros_like(spec["first_team"]))
        player_state = initial_state(player_spec, tables, self.cfg)
        after_player = step(player_state, self._policy(player_state, tables), tables, self.cfg)
        hp_p = np.asarray(after_player["hp"])[0].astype(float)
        player_turn = float(sum(max(0.0, hp0[s] - hp_p[s]) for s in enemy_slots))
        enemy_total = float(sum(hp0[s] for s in enemy_slots))
        opening = monster_turn if not player_opens else 0.0
        strike = round_strikes(spec, tables)
        strike_verdict = pre_fight(spec, tables)[0]
        return {
            "player_opens": player_opens,
            "player_hp": round(hp0[0], 1),
            "enemy_hp_total": round(enemy_total, 1),
            "opening_damage_taken": round(opening, 1),
            "survives_opening": bool(hp0[0] - opening > 0),
            "monster_turn_damage": round(monster_turn, 1),
            "player_turn_damage": round(player_turn, 1),
            "hp_over_opening_damage": round(hp0[0] / max(opening or monster_turn, 1.0), 2),
            "turns_to_kill_enemy": round(enemy_total / max(player_turn, 1.0), 2),
            "turns_to_die": round(hp0[0] / max(monster_turn, 1.0), 2),
            # The tables' own race, measured on the whole build rather than on the turn the current
            # head happens to choose. `tools/wsp_refuse_rule.py` scored both against terminal
            # outcomes on 2,048 boss boards: the table race reads AUC 0.8071 against the stepped
            # quantity's 0.7479 and the shipped value head's 0.5439, so this is the field the
            # verdict keys on and the stepped numbers are the report.
            "strike_verdict": strike_verdict[0],
            "strike_rounds_to_kill": round(float(strike["rounds_to_kill"][0]), 2),
            "strike_rounds_to_die": round(float(strike["rounds_to_die"][0]), 2),
        }

    def readiness(self, monster_ids: List[int], player_level: int,
                  player_class: Optional[str] = None,
                  distance: int = DEFAULT_DISTANCE, grade_index: int = 0) -> Dict[str, Any]:
        """Price both initiative cases, then verify the governing one by playing it.

        Who opens is not known before the fight, so both are measured and the worse is reported:
        the ambush case is what section N.11.8 found to be decisive (0.000 win rate when the opening
        kills), and an optimistic plan that only priced the favourable case would hide it.
        """
        cases = []
        for opens in (False, True):
            spec, tables = self.board(monster_ids, player_level, player_class, distance,
                                      grade_index, player_opens=opens)
            line = self.estimate(spec, tables, player_opens=opens)
            played = self.play(spec, tables)
            line.update(verified_outcome=played["outcome"],
                        verified_turns=played["turns"],
                        verified_rounds=played["rounds"],
                        verified_player_hp_left=played["player_hp_left"])
            cases.append(line)
        governing = min(cases, key=lambda c: (
            c["survives_opening"], {"loss": 0, "draw": 1, "win": 2}[c["verified_outcome"]],
            c["hp_over_opening_damage"]))
        verdict, reasons = self._verdict(governing)
        return {"estimate_and_verification": governing, "cases": cases,
                "governing_case": "ambushed" if not governing["survives_opening"]
                                  else "worst_verified_outcome",
                "verdict": verdict, "reasons": reasons, "limitations": list(LIMITATIONS)}

    def _verdict(self, line: Dict[str, Any]) -> Tuple[str, List[str]]:
        reasons = []
        if not line["survives_opening"]:
            reasons.append("the monsters' opening turn takes %s of %s hp: no decision gets played"
                           % (line["opening_damage_taken"], line["player_hp"]))
            return "refuse", reasons
        if line["verified_outcome"] != "win":
            reasons.append("the simulator rollout ends in %s, not a verified win"
                           % line["verified_outcome"])
            return "not_ready", reasons
        if line["strike_verdict"] == "refuse":
            reasons.append("outdamaged on the race the tables give: %s rounds to kill vs %s to die"
                           % (line["strike_rounds_to_kill"], line["strike_rounds_to_die"]))
            return "not_ready", reasons
        if line["strike_verdict"] == "unknown":
            # Neither side has an affordable damaging spell: the race has no clock to compare, and
            # the honest verdict is that the plan cannot finish the fight, not that it is favourable.
            reasons.append("no affordable damaging spell on either side, so the fight cannot be "
                           "won by the arithmetic (%s rounds to kill vs %s to die)"
                           % (line["strike_rounds_to_kill"], line["strike_rounds_to_die"]))
            return "not_ready", reasons
        reasons.append("simulator rollout wins; approximate damage race is %s rounds to kill "
                       "against %s to die" % (line["strike_rounds_to_kill"],
                                              line["strike_rounds_to_die"]))
        return "favourable", reasons

    def plan(self, dungeon_id: int, player_level: int,
             player_class: Optional[str] = None, distance: int = DEFAULT_DISTANCE,
             grade_index: int = 0) -> Dict[str, Any]:
        room = self.dungeon(dungeon_id)
        roster = list(room.get("boss_monster_ids") or []) or list(room.get("monster_ids") or [])
        out = self.readiness(roster, player_level, player_class, distance, grade_index)
        spec, tables = self.board(roster, player_level, player_class, distance,
                                  grade_index, player_opens=False)
        enemy_hp = np.asarray(spec["hp"])[0, 1:].astype(float)
        alive = np.asarray(spec["alive"])[0, 1:]
        order = [i + 1 for i in sorted(range(len(enemy_hp)),
                                       key=lambda i: enemy_hp[i] if alive[i] else 1e9)
                 if alive[i]]
        ap = float(np.asarray(spec["ap_max"])[0, 0])
        damage = np.asarray(tables["spell_damage"])[0, 0]
        cost = np.asarray(tables["spell_ap"])[0, 0]
        spells = [{"slot": int(s), "damage": float(damage[s]), "ap": float(cost[s]),
                   "damage_per_ap": round(float(damage[s] / max(cost[s], 1.0)), 2)}
                  for s in range(self.cfg.spells) if damage[s] > 0]
        spells.sort(key=lambda r: -r["damage_per_ap"])
        budget, sequence = ap, []
        for row in spells:
            while budget >= row["ap"] and len(sequence) < self.cfg.casts_per_turn * 2:
                sequence.append(row["slot"])
                budget -= row["ap"]
        mechanics = sorted({m for mid in roster for m in self.touch.mechanics_of(int(mid))})
        return {
            "dungeon_id": int(dungeon_id), "dungeon": self._name(room),
            "dungeon_level_min": room.get("level_min"),
            "dungeon_optimal_level": room.get("optimal_level"),
            "requested_level": player_level,
            "character_class": getattr(self, "board_class", None),
            "roster": [{"monster_id": int(mid), "name": self._name(self.touch.monster(int(mid)) or {}),
                        "level": (self.touch.spec(int(mid), grade_index) or {}).get("level"),
                        "hp": (self.touch.spec(int(mid), grade_index) or {}).get("hp"),
                        "boss": bool((self.touch.monster(int(mid)) or {}).get("isBoss"))}
                       for mid, _ in getattr(self, "board_roster", [])],
            "required_objects": room.get("required_objects") or [],
            "unmodelled_mechanics_in_roster": mechanics,
            "challenges": self.challenges(int(dungeon_id)),
            "focus_order_slots": order,
            "cast_sequence_slots": sequence,
            "ap_budget": ap,
            **out,
        }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--touch-dir", required=True, type=pathlib.Path)
    ap.add_argument("--dungeon", type=int, required=True)
    ap.add_argument("--level", type=int, required=True)
    ap.add_argument("--class", dest="player_class", default=None)
    ap.add_argument("--distance", type=int, default=DEFAULT_DISTANCE)
    ap.add_argument("--slots", type=int, default=4)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    pf = Preflight(args.touch_dir, slots=args.slots, lang=args.lang)
    plan = pf.plan(args.dungeon, args.level, args.player_class, args.distance)
    if args.json:
        print(json.dumps(plan, indent=1, sort_keys=True, default=str))
    else:
        print("%s (dungeon %d, optimal level %s): %s"
              % (plan["dungeon"], plan["dungeon_id"], plan["dungeon_optimal_level"],
                 plan["verdict"]))
        for reason in plan["reasons"]:
            print("  - %s" % reason)
        print("  simulated outcome: %s after %s team turns"
              % (plan["estimate_and_verification"]["verified_outcome"],
                 plan["estimate_and_verification"]["verified_turns"]))
        for line in plan["limitations"]:
            print("  limitation: %s" % line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
