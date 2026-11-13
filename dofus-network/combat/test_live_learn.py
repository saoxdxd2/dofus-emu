"""The live learner's label, its guard rails, and its persistence.

The important cases are the refusals: a repeated poll is not a turn, a heal is not
damage, and a model that never changes its mind is not a model. Each of those has
produced a wrong number somewhere in this repo before, so each gets a test.
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

from combat.live_learn import (FEATURE_NAMES, MIN_ROWS, WIDTH, LiveLearner,
                               OnlineValue, ShadowAuditor, board_key, credit_turn,
                               enemy_signature, features_of)  # noqa: E402

ME = 4404438


def _board(hp=140, enemies=((110, 110), (110, 110), (110, 110)), round_no=1,
           fight_id=875, cells=(263, 231, 204, 339)):
    fighters = [{"id": ME, "is_me": True, "team": "player", "hp": hp,
                 "max_hp": 140, "ap": 6, "mp": 3, "cell": cells[0], "level": 12}]
    names = ("Ragnarock", "Clobberstone", "Sparkspine")
    for i, (cur, top) in enumerate(enemies):
        fighters.append({
            "id": -(i + 1), "team": "enemy", "alive": cur > 0,
            "hp": cur, "max_hp": top, "level": 8,
            "name": names[i % len(names)], "cell": cells[1 + i],
        })
    return {"fight_id": fight_id, "round": round_no, "my_id": ME,
            "my_cell": cells[0], "fighters": fighters}


class CreditTests(unittest.TestCase):
    def test_a_real_turn_is_credited_with_the_life_it_cost(self):
        credit = credit_turn(_board(hp=140), _board(hp=96, round_no=2))
        self.assertIsNotNone(credit)
        self.assertEqual(credit.damage, 44.0)
        self.assertEqual(credit.my_hp_before, 140.0)
        self.assertEqual(credit.my_hp_after, 96.0)
        self.assertEqual(credit.enemies, 3)

    def test_a_repeated_poll_is_not_a_turn(self):
        # The page asks the brain on every poll. Counting a re-read as a turn would
        # add a zero-damage example for every extra poll and teach the model that
        # enemies do nothing.
        self.assertIsNone(credit_turn(_board(), _board()))

    def test_a_different_fight_is_not_a_turn(self):
        self.assertIsNone(credit_turn(_board(), _board(fight_id=876)))

    def test_a_heal_is_not_damage(self):
        self.assertIsNone(credit_turn(_board(hp=90), _board(hp=120, round_no=2)))

    def test_a_dead_character_labels_nothing(self):
        self.assertIsNone(credit_turn(_board(hp=0), _board(hp=140, round_no=2)))

    def test_a_missing_board_labels_nothing(self):
        self.assertIsNone(credit_turn(None, _board()))


class FeatureTests(unittest.TestCase):
    def test_the_vector_has_the_declared_width(self):
        features = features_of(_board())
        self.assertEqual(len(features), WIDTH)
        self.assertEqual(len(FEATURE_NAMES), WIDTH)

    def test_the_features_describe_the_group(self):
        features = features_of(_board())
        by_name = dict(zip(FEATURE_NAMES, features))
        self.assertEqual(by_name["enemies"], 3.0)
        self.assertEqual(by_name["enemy_levels"], 24.0)
        self.assertEqual(by_name["enemy_hp_frac"], 1.0)
        self.assertEqual(by_name["my_hp_frac"], 1.0)
        self.assertEqual(by_name["my_ap"], 6.0)

    def test_a_killed_enemy_leaves_the_count(self):
        features = features_of(_board(enemies=((0, 110), (110, 110), (110, 110))))
        by_name = dict(zip(FEATURE_NAMES, features))
        self.assertEqual(by_name["enemies"], 2.0)

    def test_the_signature_ignores_enemy_order(self):
        # The client's board order is not stable across fights, and the same three
        # monsters must not look like a different group because they stood differently.
        a = _board()
        b = _board()
        b["fighters"] = [b["fighters"][0]] + list(reversed(b["fighters"][1:]))
        self.assertEqual(enemy_signature(a), enemy_signature(b))

    def test_the_signature_is_stable_across_fights(self):
        self.assertEqual(enemy_signature(_board(fight_id=1)),
                         enemy_signature(_board(fight_id=2)))

    def test_board_key_separates_two_turns_of_the_same_round(self):
        self.assertNotEqual(board_key(_board(hp=140)),
                            board_key(_board(hp=100)))


class ModelTests(unittest.TestCase):
    def test_it_learns_a_pattern_and_beats_the_mean(self):
        # Enemy damage proportional to the number of enemies: learnable.
        model = OnlineValue()
        for _ in range(60):
            for count in (1, 2, 3):
                features = features_of(_board(enemies=tuple((110, 110) for _ in range(count))))
                model.update(features, 20.0 * count)
        report = model.report()
        self.assertTrue(model.ready)
        self.assertGreater(report["improvement"], 0.5)

    def test_a_trained_model_varies_across_boards(self):
        # The inertness guard. `duel_value_v4_0.pkl` spread exactly 0.0 on 12/12
        # boards while being reported as an improvement; this is the assertion that
        # would have caught it.
        model = OnlineValue()
        for _ in range(60):
            for count in (1, 2, 3):
                features = features_of(_board(enemies=tuple((110, 110) for _ in range(count))))
                model.update(features, 20.0 * count)
        boards = [features_of(_board(enemies=tuple((110, 110) for _ in range(c))))
                  for c in (1, 2, 3)]
        self.assertGreater(model.spread(boards), 1.0)

    def test_an_untrained_model_is_inert_and_reported_as_collecting(self):
        model = OnlineValue()
        self.assertFalse(model.ready)
        self.assertEqual(model.report()["status"], "collecting")
        self.assertEqual(model.spread([features_of(_board())]), 0.0)

    def test_it_is_not_ready_before_the_minimum(self):
        model = OnlineValue()
        for _ in range(MIN_ROWS - 1):
            model.update(features_of(_board()), 10.0)
        self.assertFalse(model.ready)
        model.update(features_of(_board()), 10.0)
        self.assertTrue(model.ready)

    def test_a_negative_prediction_is_reported_as_zero(self):
        model = OnlineValue(weights=[-100.0] + [0.0] * (WIDTH - 1))
        self.assertEqual(model.predict(features_of(_board())), 0.0)

    def test_a_vector_of_the_wrong_width_is_ignored(self):
        model = OnlineValue()
        model.update([1.0, 2.0], 5.0)
        self.assertEqual(model.rows, 0)
        self.assertEqual(model.predict([1.0]), 0.0)


class PersistenceTests(unittest.TestCase):
    def test_a_model_survives_a_round_trip(self):
        model = OnlineValue()
        for _ in range(20):
            model.update(features_of(_board()), 33.0)
        restored = OnlineValue.from_dict(json.loads(json.dumps(model.as_dict())))
        self.assertEqual(restored.rows, model.rows)
        # Compared by what it predicts, not by the weight digits: the reload refits
        # from the stored normal equations, which is the same solution up to float
        # rounding, and pinning the digits would fail on a different BLAS.
        for count in (1, 2, 3):
            features = features_of(_board(enemies=tuple((110, 110) for _ in range(count))))
            self.assertAlmostEqual(restored.predict(features),
                                   model.predict(features), places=6)

    def test_a_reloaded_model_still_reports_its_improvement(self):
        # `sq_error_baseline` is not persisted; a reload that did not refit reported
        # `improvement: null`, which is the one number that says whether the model is
        # worth serving -- and it silently vanished on every app restart.
        model = OnlineValue()
        for _ in range(40):
            for count in (1, 2, 3):
                features = features_of(_board(enemies=tuple((110, 110) for _ in range(count))))
                model.update(features, 20.0 * count)
        restored = OnlineValue.from_dict(json.loads(json.dumps(model.as_dict())))
        self.assertIsNotNone(restored.report()["improvement"])
        self.assertGreater(restored.report()["improvement"], 0.5)

    def test_a_model_from_a_different_encoder_is_rejected(self):
        # Silently dotting a 9-weight vector against an 8-feature board would answer
        # with a number that looks like a prediction and is arithmetic on nothing.
        data = OnlineValue().as_dict()
        data["features"] = ["bias", "enemies"]
        data["weights"] = [1.0, 2.0]
        self.assertEqual(OnlineValue.from_dict(data).rows, 0)

    def test_the_learner_persists_and_reloads(self):
        with tempfile.TemporaryDirectory(prefix="live-learn-") as tmp:
            model_path = Path(tmp) / "m.json"
            rows_path = Path(tmp) / "rows.jsonl"
            learner = LiveLearner(model_path=model_path, rows_path=rows_path,
                                  persist_every=2)
            learner.observe(_board(hp=140))
            learner.observe(_board(hp=96, round_no=2))
            learner.observe(_board(hp=60, round_no=3))
            self.assertEqual(learner.credited, 2)
            self.assertEqual(learner.skipped, 1)      # the first board has no predecessor
            self.assertTrue(model_path.exists())
            rows = [json.loads(line) for line in
                    rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["damage"], 44.0)
            self.assertEqual(rows[0]["kind"], "live_turn")

            reloaded = LiveLearner(model_path=model_path, rows_path=rows_path)
            self.assertEqual(reloaded.model.rows, learner.model.rows)

    def test_it_prices_a_board_only_once_it_is_ready(self):
        with tempfile.TemporaryDirectory(prefix="live-learn-") as tmp:
            learner = LiveLearner(model_path=Path(tmp) / "m.json",
                                  rows_path=Path(tmp) / "r.jsonl")
            self.assertIsNone(learner.price(_board()))
            # One fight, one monotone life total: every board after the first is a
            # creditable turn, so the row count is what gates the price, not the shape.
            hp = 400.0
            for i in range((MIN_ROWS + 4) * 2):
                hp -= 3.0
                learner.observe(_board(hp=hp, round_no=i))
            self.assertTrue(learner.model.ready)
            self.assertIsNotNone(learner.price(_board()))


class StorageAndIncrementalAuditorTests(unittest.TestCase):
    def test_live_learner_wired_to_storage_and_appends_to_db(self):
        with tempfile.TemporaryDirectory(prefix="live-learn-store-") as tmp:
            model_path = Path(tmp) / "m.json"
            rows_path = Path(tmp) / "rows.db"
            learner = LiveLearner(model_path=model_path, rows_path=rows_path)
            self.assertTrue(learner.wired_to_storage)
            self.assertIsNotNone(learner.store)
            self.assertTrue(rows_path.exists())

            learner.observe(_board(hp=140))
            learner.observe(_board(hp=96, round_no=2))
            learner.observe(_board(hp=60, round_no=3))

            self.assertEqual(learner.store.count(), 2)
            first_seq, last_seq = learner.store.bounds()
            self.assertEqual(first_seq, 1)
            self.assertEqual(last_seq, 2)
            learner.store.close()

    def test_shadow_auditor_incremental_reads_from_append_store(self):
        from storage.append_store import AppendStore

        with tempfile.TemporaryDirectory(prefix="auditor-store-") as tmp:
            db_path = Path(tmp) / "decisions.db"
            with AppendStore(db_path) as store:
                store.append({"id": "d1", "action": {"type": "cast", "spell_id": 101}}, kind="decision")
                store.append({"id": "d2", "action": {"type": "move", "cell": 120}}, kind="decision")

            auditor = ShadowAuditor(decisions_path=db_path)
            self.assertTrue(auditor.wired_to_storage)
            self.assertEqual(auditor.last_seq, 0)

            # First incremental read fetches all existing rows
            first_batch = auditor.read_incremental()
            self.assertEqual(len(first_batch), 2)
            self.assertEqual(auditor.last_seq, 2)

            # Immediate second read has no new items (WHERE seq > 2)
            second_batch = auditor.read_incremental()
            self.assertEqual(len(second_batch), 0)
            self.assertEqual(auditor.last_seq, 2)

            # Append third record to store
            with AppendStore(db_path) as store:
                store.append({"id": "d3", "action": {"type": "cast", "spell_id": 102}}, kind="decision")

            # Third incremental read returns exactly the single new record
            third_batch = auditor.read_incremental()
            self.assertEqual(len(third_batch), 1)
            self.assertEqual(third_batch[0]["id"], "d3")
            self.assertEqual(auditor.last_seq, 3)


if __name__ == "__main__":
    unittest.main(verbosity=1)


# ---------------------------------------------------------------------------
# Multi-slot: one process, two fights in flight at the same time
# ---------------------------------------------------------------------------


def _slot_board(my_id, hp, round_no, fight_id):
    """A board as a *different* account's slot would publish it."""
    board = _board(hp=hp, round_no=round_no, fight_id=fight_id)
    board["my_id"] = my_id
    board["fighters"][0]["id"] = my_id
    return board


class MultiSlotTests(unittest.TestCase):
    """Two slots alternate turns through one process.

    `combat_ai_server.py` is a single process serving every running slot, and
    both `LiveLearner` and `_ThreatObserver` used to keep exactly one scalar
    "current fight". With two slots in combat the boards interleave A, B, A, B,
    so the single slot of memory was overwritten before it could ever be read:

    * the learner saw `prev.fight_id != cur.fight_id` on every call and credited
      nothing at all (`self.credited == 0`), i.e. multi-account mode learned
      nothing;
    * the threat observer reset its `_log` on every turn, so every message was
      "new" and the same monster hits were folded in again and again.

    Both are silent: no error, no wrong-looking number, just a model that never
    improves.
    """

    def test_two_slots_alternating_both_learn(self):
        with tempfile.TemporaryDirectory(prefix="live-learn-multi-") as tmp:
            learner = LiveLearner(model_path=Path(tmp) / "m.json",
                                  rows_path=Path(tmp) / "r.jsonl")
            # Slot 1 fight 875, slot 2 fight 876. Each publishes a turn in turn.
            hp_a, hp_b = 140.0, 140.0
            for round_no in range(1, 6):
                hp_a -= 10.0
                learner.observe(_slot_board(ME, hp_a, round_no, 875))
                hp_b -= 10.0
                learner.observe(_slot_board(ME + 1, hp_b, round_no, 876))
            # Four creditable turns per slot (the first board of each has no
            # predecessor), so eight in total.
            self.assertEqual(learner.credited, 8,
                             "credited %d, skipped %d" % (learner.credited,
                                                          learner.skipped))

    def test_a_second_slot_does_not_break_the_first_slots_credit(self):
        """The damage credited must be that slot's own, not the other's."""
        with tempfile.TemporaryDirectory(prefix="live-learn-multi-") as tmp:
            rows_path = Path(tmp) / "r.jsonl"
            learner = LiveLearner(model_path=Path(tmp) / "m.json",
                                  rows_path=rows_path)
            learner.observe(_slot_board(ME, 140.0, 1, 875))
            learner.observe(_slot_board(ME + 1, 300.0, 1, 876))
            # Slot 1 takes 44, slot 2 takes 7 in the same wall-clock window.
            learner.observe(_slot_board(ME, 96.0, 2, 875))
            learner.observe(_slot_board(ME + 1, 293.0, 2, 876))
            rows = [json.loads(line) for line in
                    rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            damages = sorted(row["damage"] for row in rows)
            self.assertEqual(damages, [7.0, 44.0], rows)
            by_fight = {row["fight_id"]: row["damage"] for row in rows}
            self.assertEqual(by_fight, {875: 44.0, 876: 7.0})

    def test_a_finished_slot_does_not_inherit_the_other_slots_memory(self):
        """Slot 2's first board must not be paired with slot 1's last board."""
        with tempfile.TemporaryDirectory(prefix="live-learn-multi-") as tmp:
            learner = LiveLearner(model_path=Path(tmp) / "m.json",
                                  rows_path=Path(tmp) / "r.jsonl")
            learner.observe(_slot_board(ME, 140.0, 1, 875))
            # A brand-new fight on the *other* slot arrives before slot 1's next
            # turn: it has no predecessor and must be skipped, not credited
            # across fights.
            learner.observe(_slot_board(ME + 1, 260.0, 1, 877))
            self.assertEqual(learner.credited, 0)
            self.assertEqual(learner.skipped, 2)

    def test_the_memory_is_bounded(self):
        """A long-running process must not accumulate one entry per fight."""
        from combat.live_learn import MAX_TRACKED_FIGHTS

        with tempfile.TemporaryDirectory(prefix="live-learn-multi-") as tmp:
            learner = LiveLearner(model_path=Path(tmp) / "m.json",
                                  rows_path=Path(tmp) / "r.jsonl")
            for fight in range(MAX_TRACKED_FIGHTS * 3):
                learner.observe(_slot_board(ME, 140.0, 1, fight))
            self.assertLessEqual(len(learner._prev_by_fight), MAX_TRACKED_FIGHTS)

    def test_concurrent_slots_do_not_lose_a_credit(self):
        """`observe` is called from the HTTP handler, so it can be concurrent."""
        import threading

        with tempfile.TemporaryDirectory(prefix="live-learn-multi-") as tmp:
            learner = LiveLearner(model_path=Path(tmp) / "m.json",
                                  rows_path=Path(tmp) / "r.jsonl")

            def slot(fight, account):
                hp = 400.0
                for round_no in range(1, 41):
                    hp -= 1.0
                    learner.observe(_slot_board(account, hp, round_no, fight))

            threads = [threading.Thread(target=slot, args=(900 + i, ME + i))
                       for i in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            # 40 boards per slot, one of which is the first and credits nothing.
            self.assertEqual(learner.credited, 4 * 39,
                             "credited %d" % learner.credited)


class ThreatObserverMultiSlotTests(unittest.TestCase):
    """The same interleave, on the per-monster threat table.

    The failure here is worse than a dropped credit: a reset `_log` makes every
    message look new, so one monster hit is folded in on every turn it survives.
    A fight's twelve hits were counted ten to forty times, which does not read as
    a bug -- it reads as a monster that hits much harder than it does.
    """

    @staticmethod
    def _payload(fight_id, my_id, messages, names):
        return {"fight_id": fight_id, "my_id": my_id,
                "fighters": [{"id": my_id, "name": "me", "is_me": True},
                             {"id": -1, "name": names[0]},
                             {"id": -2, "name": names[1]}],
                "battle_log": messages}

    @staticmethod
    def _hit(source, target, loss):
        return [{"type": "GameActionFightSpellCastMessage", "source": source,
                 "cell": 100, "destination_cell": 110},
                {"type": "GameActionFightLifePointsLostMessage", "source": source,
                 "target": target, "loss": loss}]

    def test_two_slots_do_not_relearn_each_others_messages(self):
        from combat.combat_ai_server import _ThreatObserver

        with tempfile.TemporaryDirectory(prefix="threat-multi-") as tmp:
            observer = _ThreatObserver(path=Path(tmp) / "threat.json")
            names = ("Ragnarock", "Clobberstone")
            me_a, me_b = ME, ME + 1
            log_a, log_b = [], []
            learned = 0
            for _round in range(6):
                # Slot 1's monster lands one new hit, then slot 2's does.
                log_a = log_a + self._hit(-1, me_a, 12)
                learned += observer.observe(
                    self._payload(875, me_a, list(log_a), names))
                log_b = log_b + self._hit(-2, me_b, 9)
                learned += observer.observe(
                    self._payload(876, me_b, list(log_b), names))
            # Six hits per slot, one per round, and nothing counted twice.
            self.assertEqual(learned, 12, "learned %d hits for 12 real ones"
                             % learned)

    def test_one_slot_replaying_its_own_log_still_learns_once(self):
        from combat.combat_ai_server import _ThreatObserver

        with tempfile.TemporaryDirectory(prefix="threat-single-") as tmp:
            observer = _ThreatObserver(path=Path(tmp) / "threat.json")
            names = ("Ragnarock", "Clobberstone")
            log = []
            learned = 0
            for _round in range(5):
                log = log + self._hit(-1, ME, 12)
                learned += observer.observe(self._payload(875, ME, list(log), names))
                # The page polls more than once per turn; a repeat is not a hit.
                observer.observe(self._payload(875, ME, list(log), names))
            self.assertEqual(learned, 5, "learned %d for 5 real hits" % learned)

    def test_its_memory_is_bounded(self):
        from combat.combat_ai_server import _MAX_TRACKED_FIGHTS, _ThreatObserver

        with tempfile.TemporaryDirectory(prefix="threat-multi-") as tmp:
            observer = _ThreatObserver(path=Path(tmp) / "threat.json")
            names = ("Ragnarock", "Clobberstone")
            for fight in range(_MAX_TRACKED_FIGHTS * 3):
                observer.observe(self._payload(fight, ME, self._hit(-1, ME, 5),
                                               names))
            self.assertLessEqual(len(observer._logs), _MAX_TRACKED_FIGHTS)


class ShadowResidualStreamTests(unittest.TestCase):
    """`shadow_residuals` must pair a cast with *its own* fight's board.

    Sorting the interleaved rows of two slots by time put fight A's pending
    casts next to fight B's baseline HP, so a hit landed in A was priced against
    a board from B. The number that comes out is a real number about nothing.
    """

    @staticmethod
    def _row(my_id, fight_id, time, target_hp, *, cast=None, round_no=1):
        fighters = [{"id": my_id, "is_me": True, "hp": 140, "max_hp": 140},
                    {"id": -1, "hp": target_hp, "max_hp": 500, "level": 8,
                     "name": "Ragnarock", "spells": [
                         {"id": 442, "effects": [{"kind": "damage",
                                                  "min": 10, "max": 10}]}]}]
        row = {"my_id": my_id, "fight_id": fight_id, "time": time,
               "round": round_no, "observation": {"fight": {"fight_id": fight_id},
                                                  "self": fighters[0],
                                                  "enemies": [fighters[1]]},
               "decision": {"action": {}}}
        if cast is not None:
            row["decision"]["action"] = {"type": "cast", "spellId": 442,
                                         "targetId": -1}
        return row

    def test_a_cast_is_paired_with_its_own_fights_update(self):
        from combat.live_learn import shadow_residuals

        # Two slots alternate. Each casts exactly 10 damage; each target loses
        # exactly 10. A correct pairing gives every residual 0.0.
        rows = [
            self._row(ME, 875, 1.0, 500, cast=True, round_no=1),
            self._row(ME + 1, 876, 1.1, 500, cast=True, round_no=1),
            self._row(ME, 875, 1.2, 490, round_no=2),
            self._row(ME + 1, 876, 1.3, 490, round_no=2),
        ]
        out = shadow_residuals(rows)
        self.assertEqual(len(out), 2, out)
        for item in out:
            self.assertAlmostEqual(item["residual"], 0.0, places=4,
                                   msg="crossed streams: %r" % (item,))

    def test_each_residual_is_tagged_with_the_fight_it_belongs_to(self):
        from combat.live_learn import shadow_residuals

        rows = [
            self._row(ME, 875, 1.0, 500, cast=True, round_no=1),
            self._row(ME + 1, 876, 1.1, 500, cast=True, round_no=1),
            self._row(ME, 875, 1.2, 400, round_no=2),
            self._row(ME + 1, 876, 1.3, 450, round_no=2),
        ]
        out = shadow_residuals(rows)
        by_fight = {item["fight_id"]: item for item in out}
        self.assertEqual(sorted(by_fight), [875, 876])
        self.assertAlmostEqual(by_fight[875]["actual"], 100.0, places=4)
        self.assertAlmostEqual(by_fight[876]["actual"], 50.0, places=4)

    def test_a_single_fight_is_unaffected_by_the_grouping(self):
        from combat.live_learn import shadow_residuals

        rows = [
            self._row(ME, 875, 1.0, 500, cast=True, round_no=1),
            self._row(ME, 875, 1.1, 400, round_no=2),
        ]
        out = shadow_residuals(rows)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["actual"], 100.0, places=4)
        self.assertAlmostEqual(out[0]["residual"], 90.0, places=4)
