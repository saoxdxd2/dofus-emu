"""The online loop filters its evidence and refuses to promote without it."""
import json
import random
import tempfile
import unittest
from pathlib import Path

from combat import online_learn as online
from combat.fight_estimate import FightCalibrator

FEATURES = {"level_gap": 0.0, "group_size": 1.0, "turns_to_kill": 1.0,
            "turns_to_die": 20.0, "hp_ratio": 1.0, "hp_loss_fraction": 0.0,
            "has_boss": 0.0, "unknown_members": 0.0}
SCRIPTED = "tutorial authorisation: the scripted scenario guarantees this fight"


def fight(index, won, turns_to_kill, raw=0.5, source="live", session="s",
          scripted=False, unknown=0.0):
    group = -1000 - index
    outcome = {"kind": "fight_outcome", "at": index, "started_at": index - 10,
               "session": session, "group_id": group, "won": won,
               "source": source,
               "features": dict(FEATURES, turns_to_kill=turns_to_kill,
                                unknown_members=unknown),
               "estimate": {"raw_score": raw}}
    start = {"kind": "fight_start", "at": index - 10, "session": session,
             "group_id": group,
             "estimate": {"assumptions": [SCRIPTED] if scripted else []}}
    return outcome, start


def signal(index):
    return fight(index, (index % 3) != 0, 0.5 if (index % 3) != 0 else 6.0)


def outcomes(count=80):
    return [signal(index)[0] for index in range(count)]


def rows(count=80):
    items = [signal(index) for index in range(count)]
    return [start for _outcome, start in items] + [o for o, _s in items]


def paths(tmp):
    base = Path(tmp)
    model_dir = base / "models"
    return online.Paths(
        rows=base / "fights.jsonl",
        calibrator=model_dir / "fight_calibrator.json",
        versions=model_dir / "fight_calibrator.versions",
        history=model_dir / "fight_calibrator.history.jsonl",
        pointer=model_dir / "fight_calibrator.incumbent.json")


def write_rows(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item) + "\n" for item in items),
                    encoding="utf-8")


class TerminalSamplesTest(unittest.TestCase):
    def test_keeps_only_resolved_fights_in_time_order(self):
        items = [fight(2, True, 1.0), fight(1, False, 5.0),
                 fight(3, None, 2.0)]
        got = online.terminal_samples([outcome for outcome, _s in items])
        self.assertEqual([item["_at"] for item in got], [1.0, 2.0])
        self.assertEqual([item["won"] for item in got], [False, True])

    def test_split_is_time_ordered(self):
        samples = online.terminal_samples(outcomes(40))
        train, evaluation = online.split(samples, min_eval=12)
        self.assertEqual(len(evaluation), 12)
        self.assertLess(train[-1]["_at"], evaluation[0]["_at"])


class FilterTest(unittest.TestCase):
    def selection(self, items, filters=online.Filters()):
        samples = online.terminal_samples([o for o, _s in items])
        return online.filter_samples(samples, [i for item in items for i in item],
                                     filters)

    def test_scripted_fight_is_dropped_by_default(self):
        got = self.selection([fight(1, True, 1.0, scripted=True)])
        self.assertEqual(got["kept_n"], 0)
        self.assertEqual(got["dropped"], {"tutorial_authorised": 1})
        relaxed = self.selection([fight(1, True, 1.0, scripted=True)],
                                 online.Filters(drop_scripted=False))
        self.assertEqual(relaxed["kept_n"], 1)

    def test_unstarted_fight_is_dropped(self):
        outcome, _start = fight(1, True, 1.0)
        got = online.filter_samples(online.terminal_samples([outcome]), [outcome])
        self.assertEqual(got["dropped"], {"no_start": 1})

    def test_non_live_fight_is_dropped(self):
        got = self.selection([fight(1, True, 1.0, source="sim")])
        self.assertEqual(got["dropped"], {"not_live": 1})

    def test_duplicate_outcome_is_counted_once(self):
        items = [fight(1, True, 1.0)]
        got = self.selection(items + items)
        self.assertEqual(got["kept_n"], 1)
        self.assertEqual(got["dropped"], {"duplicate": 1})

    def test_strict_members_drops_unpriced_monsters(self):
        items = [fight(1, True, 1.0, unknown=2.0)]
        default = self.selection(items)
        self.assertEqual(default["kept_n"], 1)
        self.assertEqual(default["heuristic_members"], 1)
        strict = self.selection(items, online.Filters(strict_members=True))
        self.assertEqual(strict["dropped"], {"unknown_members": 1})


class GateTest(unittest.TestCase):
    def test_collects_below_minimum(self):
        verdict = online.evaluate_gate(online.terminal_samples(outcomes(10)))
        self.assertEqual(verdict["status"], "collecting")
        self.assertEqual(verdict["need"], online.MIN_FIGHTS)

    def test_refuses_without_signal(self):
        rng = random.Random(7)
        items = [fight(index, rng.random() < 0.5, 1.0 + (index % 5))
                 for index in range(80)]
        samples = online.terminal_samples([o for o, _s in items])
        verdict = online.evaluate_gate(samples)
        self.assertEqual(verdict["status"], "refused")
        self.assertTrue(verdict["reasons"])

    def test_no_artifact_means_no_probability(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = paths(tmp)
            self.assertIsNone(FightCalibrator.load(state.calibrator))
            self.assertEqual(online.shadow_report(state)["status"], "no_incumbent")

    def test_not_due_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = paths(tmp)
            write_rows(state.rows, rows(10))
            verdict = online.run(state, write=True, force=False)
            self.assertEqual(verdict["action"], "skipped_not_due")
            self.assertFalse(state.calibrator.exists())

    def test_scripted_corpus_keeps_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = paths(tmp)
            items = [fight(index, True, 1.0, scripted=True) for index in range(45)]
            write_rows(state.rows, [i for item in items for i in item])
            verdict = online.run(state, write=False, force=True)
            self.assertEqual(verdict["status"], "collecting")
            self.assertEqual(verdict["n"], 0)
            self.assertEqual(verdict["filters"]["dropped"],
                             {"tutorial_authorised": 45})


class PromotionTest(unittest.TestCase):
    def test_promotes_with_signal_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = paths(tmp)
            write_rows(state.rows, rows(80))
            first = online.run(state, write=True, force=True)
            self.assertEqual(first["action"], "promoted")
            self.assertEqual(first["status"], "passed")
            model = FightCalibrator.load(state.calibrator)
            self.assertIsNotNone(model)
            self.assertEqual(model.source, "live_online")
            self.assertGreater(model.metrics["delta_auc"], 0)
            probability = model.predict(dict(FEATURES))
            self.assertIsInstance(probability, float)
            self.assertTrue(0.0 <= probability <= 1.0)
            first_text = state.calibrator.read_text(encoding="utf-8")

            write_rows(state.rows, rows(82))
            second = online.run(state, write=True, force=True)
            self.assertEqual(second["action"], "promoted")
            self.assertNotEqual(first["version"], second["version"])

            restored = online.rollback(state)
            self.assertEqual(restored["status"], "rolled_back")
            self.assertEqual(state.calibrator.read_text(encoding="utf-8"),
                             first_text)
            self.assertIsNotNone(FightCalibrator.load(state.calibrator))


if __name__ == "__main__":
    unittest.main()
