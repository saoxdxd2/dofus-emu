"""What the live builder can learn from a fight board.

The interesting case is a monster whose spellbook the client never publishes: the
builder has to price a monster turn from the damage it was seen to deal.

Two producers are covered. `last_actions` is the shape these tests were first
written against, and it is `[]` on all 333 recorded boards. `battle_log` is the
list the page actually fills -- 9,573 rows on 162 boards -- and it carries neither
`action_id` nor `spell_id` on its damage rows, so the id join the first tests
assume cannot work there. `LiveLogShapeTests` uses the measured keys; the older
suites stay because the builder still falls back to that list.
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


def _board(actions, creature_id=123, name="Barbakle", level=5, spells=()):
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
                 "creature_generic_id": creature_id, "spells": list(spells)},
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


class NameKeyedMonsterTests(unittest.TestCase):
    """The client has never sent a species id.

    Measured on the 333 recorded boards: `creature_generic_id` is null for all
    1,396 enemy fighters, while 287 of them do carry their spell list. Keying
    the monster table on the species id therefore discarded the only monster
    mechanics the corpus contains, and the extractor reported zero monsters over
    a corpus that has some.
    """

    def _build(self, rows):
        with tempfile.TemporaryDirectory(prefix="live-data-") as tmp:
            path = Path(tmp) / "boards.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                            encoding="utf-8")
            return build_from_boards(path)

    def test_a_monster_without_a_species_id_is_keyed_by_name(self):
        board = _board([], creature_id=None, name="Merkaptan Leader",
                       spells=[{"id": 6908, "name": "Kremashun", "ap_cost": 4,
                                "min_range": 1, "range": 1}])
        monsters, _actions, _fights, _observed = self._build([board])
        self.assertEqual(len(monsters), 1)
        entry = monsters[0]
        self.assertEqual(entry["key"], "name:Merkaptan Leader")
        self.assertIsNone(entry["creature_generic_id"])
        self.assertEqual(entry["spell_ids"], ["6908"])

    def test_the_name_key_cannot_be_mistaken_for_a_numeric_id(self):
        board = _board([], creature_id=None, name="Merkaptan Leader")
        monsters, _a, _f, _o = self._build([board])
        self.assertTrue(monsters[0]["key"].startswith("name:"))
        self.assertNotEqual(monsters[0]["key"], "Merkaptan Leader")

    def test_a_monster_with_neither_id_nor_name_is_skipped(self):
        board = _board([], creature_id=None, name="")
        monsters, _a, _f, _o = self._build([board])
        self.assertEqual(monsters, [])

    def test_a_numeric_id_still_wins_when_the_client_sends_one(self):
        board = _board([], creature_id=123, name="Barbakle")
        monsters, _a, _f, _o = self._build([board])
        self.assertEqual(monsters[0]["key"], 123)
        self.assertEqual(monsters[0]["creature_generic_id"], 123)


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


def _log_row(kind, **fields):
    """A `battle_log` row with exactly the 14 keys the live client writes.

    Measured over 9,573 rows on the 333 recorded boards: `type, at_action, source,
    target, spell, spell_level, cell, effect, critical, delta, value, loss,
    permanent_damages, shield_loss, fight_id`. No `action_id`, no `spell_id`, no
    `source_id`, no `destination_cell` -- those are the *other* producer's names,
    and a fixture written with them tests a client that does not exist.
    """
    row = {"type": kind, "at_action": 300, "source": None, "target": None,
           "spell": None, "spell_level": None, "cell": None, "effect": None,
           "critical": None, "delta": None, "value": None, "loss": None,
           "permanent_damages": None, "shield_loss": None, "fight_id": None}
    row.update(fields)
    return row


LIVE_CAST = _log_row("GameActionFightSpellCastMessage", at_action=300, source=-1,
                     target=4434090, spell=6908, spell_level=5, effect=0, critical=0)
LIVE_LOSS = _log_row("GameActionFightLifePointsLostMessage", at_action=97, source=-1,
                     target=4434090, effect=0, loss=47, permanent_damages=4)
LIVE_ROUND = _log_row("GameFightNewRoundMessage")


def _log_board(log, fight_id=7, run="r1", round_=2, **kw):
    board = _board([], **kw)
    board["run"] = run
    board["board"]["fight_id"] = fight_id
    board["board"]["round"] = round_
    board["board"]["battle_log"] = log
    return board


def _outcome(run, fight_id, won):
    return {"kind": "fight_outcome", "run": run,
            "outcome": {"kind": "fight_outcome", "fight_id": fight_id, "won": won}}


class LiveLogShapeTests(unittest.TestCase):
    """The list the client actually fills, in the keys it actually writes.

    `last_actions` is `[]` on all 333 recorded boards while `battle_log` carries
    9,573 rows on 162 of them. The builder read only the empty one, so it reported
    0 monster actions and 0 priced spells over a corpus holding 240 casts and 213
    damage rows.
    """

    def _build(self, rows):
        with tempfile.TemporaryDirectory(prefix="live-log-") as tmp:
            path = Path(tmp) / "boards.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                            encoding="utf-8")
            return build_from_boards(path)

    def test_the_live_shape_prices_a_spell_the_id_join_could_not(self):
        # at_action 300 on the cast and 97 on the loss, spell null on the loss:
        # the measured shape, and the one the old action-id join priced as nothing.
        _m, actions, _f, observed = self._build([_log_board([LIVE_CAST, LIVE_LOSS])])
        self.assertEqual(len(observed), 1)
        slot = observed["Barbakle|5|6908"]
        self.assertEqual((slot["hits"], slot["mean"], slot["spell_id"]), (1, 47.0, 6908))
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["spell_id"], 6908)

    def test_the_two_producers_agree_on_the_same_fight(self):
        _m, _a, _f, from_log = self._build([_log_board([LIVE_CAST, LIVE_LOSS])])
        _m2, _a2, _f2, from_actions = self._build([_board([CAST, LOSS])])
        self.assertEqual(from_log, from_actions)

    def test_a_damage_row_with_no_cast_by_its_source_is_not_priced(self):
        # 32 of the 47 recorded damage rows are like this: a tackle or a poison
        # tick. Crediting them to the last spell any monster cast would invent
        # damage for a spell that did not deal it.
        orphan = dict(LIVE_LOSS, source=-99)
        _m, _a, _f, observed = self._build([_log_board([LIVE_CAST, orphan])])
        self.assertEqual(observed, {})

    def test_a_cast_at_another_target_does_not_own_the_damage(self):
        elsewhere = dict(LIVE_LOSS, target=-3)
        _m, _a, _f, observed = self._build([_log_board([LIVE_CAST, elsewhere])])
        self.assertEqual(observed, {})

    def test_a_cast_from_an_earlier_round_does_not_own_the_damage(self):
        _m, _a, _f, observed = self._build(
            [_log_board([LIVE_CAST, LIVE_ROUND, LIVE_LOSS])])
        self.assertEqual(observed, {})

    def test_two_casts_of_one_spell_in_one_round_are_two_hits(self):
        second = dict(LIVE_LOSS, loss=53)
        _m, _a, _f, observed = self._build(
            [_log_board([LIVE_CAST, LIVE_LOSS, LIVE_CAST, second])])
        slot = observed["Barbakle|5|6908"]
        self.assertEqual((slot["hits"], slot["mean"], slot["min"], slot["max"]),
                         (2, 50.0, 47.0, 53.0))

    def test_one_cast_hitting_twice_is_one_cast_with_the_sum(self):
        # `hits` counts casts, not damage rows: an AoE or a shield component adds
        # to what that one cast took off us.
        extra = dict(LIVE_LOSS, loss=3)
        _m, _a, _f, observed = self._build([_log_board([LIVE_CAST, LIVE_LOSS, extra])])
        slot = observed["Barbakle|5|6908"]
        self.assertEqual((slot["hits"], slot["total"]), (1, 50.0))

    def test_the_same_ring_republished_on_a_later_board_is_not_counted_twice(self):
        first = _log_board([LIVE_CAST, LIVE_LOSS])
        later = _log_board([LIVE_CAST, LIVE_LOSS, LIVE_ROUND], round_=3)
        _m, _a, _f, observed = self._build([first, later])
        self.assertEqual(observed["Barbakle|5|6908"]["hits"], 1)

    def test_a_board_with_an_empty_log_falls_back_to_the_older_list(self):
        board = _board([CAST, LOSS])
        board["board"]["battle_log"] = []
        _m, _a, _f, observed = self._build([board])
        self.assertEqual(observed["Barbakle|5|6908"]["hits"], 1)

    def test_our_own_cast_in_the_log_is_not_a_monster_spell(self):
        mine = dict(LIVE_CAST, source=4434090, spell=19)
        loss = dict(LIVE_LOSS, source=4434090)
        _m, _a, _f, observed = self._build([_log_board([mine, loss])])
        self.assertEqual(observed, {})

    def test_the_damage_that_was_priced_is_counted_too(self):
        # The other half of the counter pair: without it, "attributed 0" is the
        # only reading the summary can ever give, and a dead join looks normal.
        ATTRIBUTION["damage_rows"] = 0
        ATTRIBUTION["attributed"] = 0
        self._build([_log_board([LIVE_CAST, LIVE_LOSS])])
        self.assertEqual((ATTRIBUTION["damage_rows"], ATTRIBUTION["attributed"]), (1, 1))

    def test_the_unattributable_damage_is_still_counted(self):
        ATTRIBUTION["damage_rows"] = 0
        ATTRIBUTION["attributed"] = 0
        # A cast is on the log, but both damage rows come from sources that never
        # cast: nothing owns them. The summary must still say damage happened.
        rows = [LIVE_CAST, dict(LIVE_LOSS, source=-99),
                dict(LIVE_LOSS, source=-98, loss=12)]
        _m, _a, _f, observed = self._build([_log_board(rows)])
        self.assertEqual(observed, {})
        self.assertEqual(ATTRIBUTION["damage_rows"], 2)
        self.assertEqual(ATTRIBUTION["attributed"], 0)


class OutcomeJoinTests(unittest.TestCase):
    """One run holds several fights, so the verdict has to be keyed on the fight.

    Measured on the recorded corpus: run 7a871278 carries fights 1356/1358/1359/1482
    and run 452415f8 carries 9349/1640/1641. A run-keyed dict kept the last outcome
    and labelled every fight in the run with it -- 8 of 19 fights carried another
    fight's verdict, every one of them `won: true`.
    """

    def _build(self, rows):
        with tempfile.TemporaryDirectory(prefix="live-join-") as tmp:
            path = Path(tmp) / "boards.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                            encoding="utf-8")
            return build_from_boards(path)

    def test_two_fights_in_one_run_keep_their_own_verdicts(self):
        rows = [_log_board([LIVE_CAST, LIVE_LOSS], fight_id=11),
                _log_board([LIVE_CAST, LIVE_LOSS], fight_id=12),
                _outcome("r1", 11, True), _outcome("r1", 12, False)]
        _m, _a, fights, _o = self._build(rows)
        got = {f["fight_id"]: (f["outcome"] or {}).get("won") for f in fights}
        self.assertEqual(got, {11: True, 12: False})

    def test_a_fight_with_no_outcome_is_unlabelled_not_borrowed(self):
        rows = [_log_board([], fight_id=11), _log_board([], fight_id=12),
                _outcome("r1", 12, True)]
        _m, _a, fights, _o = self._build(rows)
        got = {f["fight_id"]: f["outcome"] for f in fights}
        self.assertIsNone(got[11])
        self.assertEqual(got[12]["won"], True)

    def test_the_same_fight_id_in_another_run_does_not_lend_its_verdict(self):
        rows = [_log_board([], fight_id=11, run="r1"),
                _log_board([], fight_id=11, run="r2"),
                _outcome("r2", 11, True)]
        _m, _a, fights, _o = self._build(rows)
        got = {f["run"]: f["outcome"] for f in fights}
        self.assertIsNone(got["r1"])
        self.assertEqual(got["r2"]["won"], True)


if __name__ == "__main__":
    unittest.main(verbosity=1)
