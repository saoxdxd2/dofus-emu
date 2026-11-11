"""What the live builder can learn from a fight board.

The interesting case is a monster whose spellbook the client never publishes. The
page now records the enemy's casts in `last_actions` and the damage they did in the
same rows, so the builder can price a monster turn from observation alone. These
tests use a hand-written board because the recorded corpus predates that fix: it has
`last_actions: []` on all 333 boards, which is exactly the bug being covered.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from combat.live_data import ATTRIBUTION, build_from_boards  # noqa: E402


def _board(actions, creature_id=123, name="Barbakle", level=5):
    return {
        "kind": "fight_board",
        "run": "r1",
        "board": {
            "fight_id": 7,
            "round": 2,
            "my_id": 4434090,
            "my_cell": 300,
            "is_my_turn": False,
            "fighters": [
                {"id": 4434090, "team": "player", "cell": 300, "hp": 400,
                 "max_hp": 500, "level": 5},
                {"id": -1, "team": "enemy", "cell": 320, "hp": 140, "max_hp": 140,
                 "level": level, "name": name,
                 "creature_generic_id": creature_id, "spells": []},
            ],
            "last_actions": actions,
        },
    }


CAST = {"type": "GameActionFightSpellCastMessage", "action_id": 41,
        "source_id": -1, "target_id": 4434090, "spell_id": 6908,
        "spell_level": 5, "destination_cell": None, "critical": 0}
LOSS = {"type": "GameActionFightLifePointsLostMessage", "action_id": 41,
        "source_id": -1, "target_id": 4434090, "spell_id": 6908,
        "loss": 47, "delta": -47}


class ObservedDamageTests(unittest.TestCase):
    def _build(self, rows):
        with tempfile.TemporaryDirectory(prefix="live-data-") as tmp:
            path = Path(tmp) / "boards.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                            encoding="utf-8")
            return build_from_boards(path)

    def test_a_monster_cast_is_priced_from_the_damage_it_dealt(self):
        monsters, actions, _fights, observed = self._build([_board([CAST, LOSS])])
        self.assertEqual(len(observed), 1)
        slot = observed["Barbakle|5|6908"]
        self.assertEqual(slot["spell_id"], 6908)
        self.assertEqual(slot["hits"], 1)
        self.assertEqual(slot["mean"], 47.0)
        self.assertEqual(slot["min"], 47.0)
        self.assertEqual(slot["max"], 47.0)
        # The creature id is what the monster table is keyed on, and it is present
        # here because the page reads it off the fighter object now.
        self.assertEqual(len(monsters), 1)
        self.assertEqual(monsters[0]["creature_generic_id"], 123)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["spell_id"], 6908)

    def test_several_hits_are_averaged_and_the_spread_kept(self):
        # Distinct rounds, because a repoll of the same action is deduped on purpose.
        rows = [_board([CAST, LOSS]),
                _board([dict(CAST, action_id=42), dict(LOSS, action_id=42, loss=53)])]
        rows[1]["board"]["round"] = 3
        _monsters, _actions, _fights, observed = self._build(rows)
        slot = observed["Barbakle|5|6908"]
        self.assertEqual(slot["hits"], 2)
        self.assertEqual(slot["mean"], 50.0)
        self.assertEqual(slot["min"], 47.0)
        self.assertEqual(slot["max"], 53.0)

    def test_a_repolled_action_is_not_counted_twice(self):
        # The same board twice is the same observation, not two hits.
        _monsters, _actions, _fights, observed = self._build([_board([CAST, LOSS]),
                                                              _board([CAST, LOSS])])
        self.assertEqual(observed["Barbakle|5|6908"]["hits"], 1)

    def test_a_cast_with_no_recorded_loss_is_not_priced(self):
        # Half the observation is worse than none: a spell with no damage row would
        # otherwise be filed as zero damage, which reads as "harmless".
        _monsters, _actions, _fights, observed = self._build([_board([CAST])])
        self.assertEqual(observed, {})

    def test_a_board_without_actions_learns_nothing(self):
        # The recorded corpus: `last_actions` is [] on all 333 boards, so this is the
        # shape that produced an empty monster table.
        _monsters, _actions, _fights, observed = self._build([_board([])])
        self.assertEqual(observed, {})

    def test_our_own_cast_is_not_a_monster_spell(self):
        mine = dict(CAST, source_id=4434090, spell_id=19)
        _monsters, _actions, _fights, observed = self._build([_board([mine, LOSS])])
        self.assertEqual(observed, {})

    def test_a_missing_file_returns_empty_datasets(self):
        monsters, actions, fights, observed = build_from_boards(
            Path(tempfile.gettempdir()) / "definitely-not-here.jsonl")
        self.assertEqual((monsters, actions, fights, observed), ([], [], [], {}))


class MeasuredPayloadShapeTests(unittest.TestCase):
    """The payload as the live client actually emits it, not as the join assumes.

    Recorded from fight 875 on 2026-11-10. Every cast row carried `action_id: 300`
    and every life-points-lost row carried 97 or 98 with `spell_id: null`. The
    builder joined on `action_id`, so it matched nothing and `observed_damage` came
    out empty for every board -- which is indistinguishable from "no damage happened"
    unless the counters say so. These tests pin the measured shape down.
    """

    MEASURED_CAST = {"type": "GameActionFightSpellCastMessage", "action_id": 300,
                     "source_id": -1, "target_id": 4434090, "spell_id": 7141,
                     "spell_level": 1, "destination_cell": None, "critical": 0}
    MEASURED_LOSS = {"type": "GameActionFightLifePointsLostMessage", "action_id": 97,
                     "source_id": -1, "target_id": -3, "spell_id": None,
                     "loss": 40, "permanent_damages": 4}

    def _build(self, rows):
        with tempfile.TemporaryDirectory(prefix="live-shape-") as tmp:
            path = Path(tmp) / "boards.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                            encoding="utf-8")
            return build_from_boards(path)

    def test_a_cast_id_that_does_not_match_the_loss_id_prices_nothing(self):
        ATTRIBUTION["damage_rows"] = 0
        ATTRIBUTION["attributed"] = 0
        _m, _a, _f, observed = self._build(
            [_board([self.MEASURED_CAST, self.MEASURED_LOSS])])
        self.assertEqual(observed, {})

    def test_the_damage_that_could_not_be_attributed_is_counted(self):
        # The point of the counters: "we saw damage and could not price it" must not
        # look the same in the summary as "the fight had no damage in it".
        ATTRIBUTION["damage_rows"] = 0
        ATTRIBUTION["attributed"] = 0
        self._build([_board([self.MEASURED_CAST, self.MEASURED_LOSS])])
        self.assertEqual(ATTRIBUTION["damage_rows"], 1)
        self.assertEqual(ATTRIBUTION["attributed"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
