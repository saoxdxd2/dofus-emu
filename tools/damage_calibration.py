"""Compare the solver's damage estimate with the server's own numbers.

Two logs already exist for every live fight:

* ``trajectories/live_policy_decisions.jsonl`` -- one row per decision, with the
  bound slots and the damage the solver expects from each of the character's
  spells (``decision.slots[].damage``).
* ``data/live_evidence/fight_outcomes.jsonl`` (or any outcome log) -- a row per
  resolved fight whose ``end.battle_log`` is the client's own fight-message
  stack: ``GameActionFightSpellCastMessage`` for the cast and
  ``GameActionFightLifePointsLostMessage`` for the server's ``loss``.

Both logs carry Dofus action ids, but the cast and its effects do **not** share one
on this client: live fight 133 (2026-10-28) logged every cast with `at_action: 300`
and the damage as `GameActionFightLifePointsLostMessage` with ids 96/97. The join is
therefore the message order: a loss belongs to the most recent cast by my side (my
character or a summon) before it in the log, and an enemy source or a non-enemy
target is excluded. A self-targeted cast is a non-damage cast, counted separately,
not scored as a miss.

The report is deliberately blunt: per-spell pairs, mean absolute error, bias,
and how many casts could not be paired. A calibration that hides its unmatched
casts would look accurate by ignoring everything that disagreed.

    python tools/damage_calibration.py --decisions trajectories/live_policy_decisions.jsonl \\
        --outcomes data/live_evidence/fight_outcomes.jsonl --out data/live_evidence/damage_calibration.json

With no live casts yet the report says so and exits 0; it becomes evidence on
the first fight the page records.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DECISIONS = REPO_ROOT / "trajectories" / "live_policy_decisions.jsonl"
DEFAULT_OUTCOMES = REPO_ROOT / "dofus-network" / "data" / "live_evidence" / "fight_outcomes.jsonl"

CAST = "GameActionFightSpellCastMessage"
LOSS = "GameActionFightLifePointsLostMessage"


def _iter_rows(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def load_decisions(path: Path) -> Dict[str, Dict[str, Any]]:
    """Per fight: who I am, the enemies seen, and the predicted damage per spell.

    A decision is logged before the cast happens, so several decisions can name
    the same spell; the per-spell prediction is their median (the estimate moves
    with remaining AP and target, and the median is the estimate the fight was
    actually played from).
    """
    fights: Dict[str, Dict[str, Any]] = {}
    for row in _iter_rows(path):
        decision = row.get("decision") if isinstance(row, dict) else None
        if not isinstance(decision, dict):
            continue
        fight_id = row.get("fight_id")
        key = str(fight_id) if fight_id is not None else "unknown"
        entry = fights.setdefault(key, {"my_id": row.get("my_id"),
                                        "predicted": {}, "enemies": set()})
        if entry["my_id"] is None and row.get("my_id") is not None:
            entry["my_id"] = row.get("my_id")
        my_id = row.get("my_id")
        for slot in decision.get("slots") or []:
            if not isinstance(slot, dict) or slot.get("empty"):
                continue
            try:
                spell = int(slot.get("spell_id"))
                damage = float(slot.get("damage") or 0.0)
            except (TypeError, ValueError):
                continue
            if damage <= 0:
                continue
            entry["predicted"].setdefault(spell, []).append(damage)
        for target in decision.get("targets") or []:
            if not isinstance(target, dict):
                continue
            fighter_id = target.get("fighter_id")
            if fighter_id is None:
                continue
            try:
                fighter_id = int(fighter_id)
            except (TypeError, ValueError):
                continue
            if my_id is not None and fighter_id == my_id:
                continue
            entry["enemies"].add(fighter_id)
    for entry in fights.values():
        entry["predicted"] = {
            spell: statistics.median(values)
            for spell, values in entry["predicted"].items() if values
        }
    return fights


def pair_fight(battle_log: List[Dict[str, Any]], entry: Dict[str, Any]
               ) -> Tuple[List[Dict[str, Any]], int, int]:
    """(matched pairs, unmatched damage casts, non-damage casts) for one fight.

    A cast is matched only when the log carries its action id and at least one
    loss message shares it. Anything else is counted as unmatched rather than
    dropped: a calibration over the easy half is not a calibration.

    The action id is **not** shared between the cast and its losses: live fight 133
    (2026-10-28) logged every cast with `at_action: 300` while the damage arrived
    as `GameActionFightLifePointsLostMessage` with ids 96 and 97, because the
    client numbers the *effect*, not the spell. Nor is it unique per fight (300
    was reused for a self-buff and then the damage spell). So each loss is
    attached to the most recent cast by my side before it in the log; a cast ends
    when the next one starts. That is the only ordering the messages guarantee,
    and it keeps two casts with the same id from being summed into one.
    """
    if not isinstance(battle_log, list):
        return [], 0, 0
    enemies = entry.get("enemies") or set()
    my_id = entry.get("my_id")
    predicted = entry.get("predicted") or {}
    casts: List[Dict[str, Any]] = []
    last_cast: Optional[Dict[str, Any]] = None
    for row in battle_log:
        if not isinstance(row, dict):
            continue
        kind = row.get("type")
        if kind == CAST:
            source = row.get("source")
            if enemies and source is not None and int(source) in enemies:
                continue
            try:
                spell = int(row.get("spell"))
            except (TypeError, ValueError):
                continue
            target = row.get("target")
            self_cast = my_id is not None and target is not None and int(target) == int(my_id)
            last_cast = {"spell": spell, "target": target, "self": self_cast,
                         "actual": 0.0, "losses": 0}
            casts.append(last_cast)
            continue
        if kind != LOSS or last_cast is None:
            continue
        # The caster is my character or one of my summons (negative fighter ids on
        # this client: fight 133 logged loss 26 from source -22358 on the enemy the
        # character had just hit). An enemy source is not my damage.
        source = row.get("source")
        if enemies and source is not None and int(source) in enemies:
            continue
        target = row.get("target")
        if enemies and target is not None and int(target) not in enemies:
            continue
        try:
            loss = float(row.get("loss") or 0.0)
        except (TypeError, ValueError):
            continue
        if loss <= 0:
            continue
        last_cast["actual"] += loss
        last_cast["losses"] += 1
    pairs: List[Dict[str, Any]] = []
    unmatched = 0
    non_damage = 0
    for cast in casts:
        if cast["self"]:
            non_damage += 1
            continue
        if cast["actual"] <= 0 or cast["spell"] not in predicted:
            unmatched += 1
            continue
        pairs.append({
            "spell": cast["spell"],
            "target": cast["target"],
            "losses": cast["losses"],
            "predicted": round(float(predicted[cast["spell"]]), 2),
            "actual": round(float(cast["actual"]), 2),
        })
    return pairs, unmatched, non_damage


def _spell_stats(pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_spell: Dict[int, List[Dict[str, Any]]] = {}
    for pair in pairs:
        by_spell.setdefault(int(pair["spell"]), []).append(pair)
    out: Dict[str, Any] = {}
    for spell, rows in sorted(by_spell.items()):
        errors = [abs(row["actual"] - row["predicted"]) for row in rows]
        biases = [row["actual"] - row["predicted"] for row in rows]
        out[str(spell)] = {
            "casts": len(rows),
            "mae": round(statistics.fmean(errors), 2),
            "bias": round(statistics.fmean(biases), 2),
            "median_error": round(statistics.median(errors), 2),
        }
    return out


def report(decisions: Dict[str, Dict[str, Any]],
           outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    fights: List[Dict[str, Any]] = []
    all_pairs: List[Dict[str, Any]] = []
    unmatched_total = 0
    non_damage_total = 0
    for row in outcomes:
        # Both evidence logs wrap the outcome: `fight_outcome` rows in
        # fight_boards.jsonl are {kind, run, outcome, observation}, and the
        # captured rows from capture_fight_outcome.py use the same shape.
        if isinstance(row, dict) and isinstance(row.get("outcome"), dict):
            row = row["outcome"]
        end = row.get("end") if isinstance(row, dict) else None
        if not isinstance(end, dict):
            continue
        fight_id = row.get("fight_id", end.get("fight_id"))
        key = str(fight_id) if fight_id is not None else "unknown"
        entry = decisions.get(key)
        if not entry:
            continue
        pairs, unmatched, non_damage = pair_fight(end.get("battle_log") or [], entry)
        unmatched_total += unmatched
        non_damage_total += non_damage
        all_pairs.extend(pairs)
        if pairs or unmatched:
            fights.append({
                "fight_id": fight_id,
                "casts": len(pairs) + unmatched + non_damage,
                "matched": len(pairs),
                "unmatched": unmatched,
                "non_damage": non_damage,
                "won": row.get("won"),
                "spells": _spell_stats(pairs),
            })
    errors = [abs(pair["actual"] - pair["predicted"]) for pair in all_pairs]
    biases = [pair["actual"] - pair["predicted"] for pair in all_pairs]
    summary = {
        "fights": len(fights),
        "matched_casts": len(all_pairs),
        "unmatched_casts": unmatched_total,
        "non_damage_casts": non_damage_total,
        "overall_mae": round(statistics.fmean(errors), 2) if errors else None,
        "overall_bias": round(statistics.fmean(biases), 2) if biases else None,
        "per_spell": _spell_stats(all_pairs),
        "status": "ok" if len(all_pairs) >= 5 else "not_enough_casts",
    }
    return {"summary": summary, "fights": fights,
            "pairs": all_pairs[:500]}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", default=str(DEFAULT_DECISIONS))
    parser.add_argument("--outcomes", nargs="+", default=[str(DEFAULT_OUTCOMES)])
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    decisions = load_decisions(Path(args.decisions))
    outcomes: List[Dict[str, Any]] = []
    for path in args.outcomes:
        outcomes.extend(_iter_rows(Path(path)))
    result = report(decisions, outcomes)
    summary = result["summary"]
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    if summary["status"] != "ok":
        print("damage calibration: %d matched cast(s), %d unmatched -- %s"
              % (summary["matched_casts"], summary["unmatched_casts"],
                 "no live fight has been recorded with a battle log yet"
                 if not decisions else "not enough paired casts to judge"))
        return 0
    print("damage calibration: %d fights, %d casts (%d unmatched), MAE %.2f, bias %+.2f"
          % (summary["fights"], summary["matched_casts"],
             summary["unmatched_casts"], summary["overall_mae"],
             summary["overall_bias"]))
    for spell, stats in summary["per_spell"].items():
        print("  spell %s: %d casts, MAE %.2f, bias %+.2f"
              % (spell, stats["casts"], stats["mae"], stats["bias"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
