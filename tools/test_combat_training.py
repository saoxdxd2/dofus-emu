import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from prepare_combat_training import partition, row_fingerprints
from wsp_fight import compare


class SplitTests(unittest.TestCase):
    def test_duplicates_cannot_cross_splits(self):
        original = np.arange(64, dtype=np.float32).reshape(32, 2)
        arrays = {"hp": np.concatenate([original, original]), "alive": np.ones((64, 2), bool)}
        splits, manifest = partition(arrays, 42)
        self.assertEqual(manifest["removed_duplicates"], 32)
        seen = set()
        for name in ("train", "edit", "dev", "test"):
            rows = set(row_fingerprints(splits[name]))
            self.assertTrue(rows)
            self.assertFalse(seen & rows)
            seen |= rows
        self.assertEqual(len(seen), 32)

    def test_splits_are_deterministic(self):
        arrays = {"hp": np.arange(64).reshape(32, 2)}
        self.assertEqual(partition(arrays, 7)[1], partition(arrays, 7)[1])
        self.assertNotEqual(partition(arrays, 7)[1], partition(arrays, 8)[1])


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.doc = {"config": {"data_sha256": "fit", "seed": 7, "matchup_seed": 11,
                               "beam_width": 8, "max_actions": 2, "plan_k": 6},
                    "summary": {"network_win_rate": 0.5, "arm": "test"},
                    "results": [{"matchup": [1, 2], "network_score": 0.5}]}

    def pair(self, other):
        a, b = self.root / "a.json", self.root / "b.json"
        a.write_text(json.dumps(self.doc), encoding="utf-8")
        b.write_text(json.dumps(other), encoding="utf-8")
        return compare(a, b)

    def test_matching_run_compares(self):
        other = copy.deepcopy(self.doc)
        other["results"][0]["network_score"] = 1.0
        self.assertEqual(self.pair(other)["mean_score_delta"], 0.5)

    def test_different_data_refused(self):
        other = copy.deepcopy(self.doc)
        other["config"]["data_sha256"] = "test"
        with self.assertRaisesRegex(ValueError, "table"):
            self.pair(other)

    def test_different_seed_or_search_refused(self):
        for field in ("seed", "matchup_seed", "beam_width", "max_actions", "plan_k"):
            other = copy.deepcopy(self.doc)
            other["config"][field] += 1
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                self.pair(other)

    def test_empty_or_partial_pairs_refused(self):
        for rows in ([], [{"matchup": [3, 4], "network_score": 0.5}]):
            other = copy.deepcopy(self.doc)
            other["results"] = rows
            with self.assertRaisesRegex(ValueError, "nonempty"):
                self.pair(other)


if __name__ == "__main__":
    unittest.main()
