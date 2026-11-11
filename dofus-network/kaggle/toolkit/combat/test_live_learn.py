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
                               OnlineValue, board_key, credit_turn,
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
