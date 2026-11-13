"""Checks for the pointer's InfoNCE loss, focal heads and goal augmentation.

    python ai/test_pointer_train.py

The corpus (`data/entity_pointer`) is gated on live coverage, so these checks
build their own rows: the point is the loss and augmentation contracts, not the
dataset's size.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import torch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai import train_entity_pointer as t  # noqa: E402
from ai.pointer_augment import Augmenter  # noqa: E402


class TestPointerTrain(unittest.TestCase):
    def test_row_variants_share_the_label(self) -> None:
        row = self._rows(1)[0]
        base = t.PointerRows._item(row, row["goal"])
        variant = t.PointerRows._item(row, "trouve le marchand 0")
        self.assertEqual(base["label"], variant["label"],
                         "%s vs %s" % (base["label"], variant["label"]))
        self.assertEqual((base["moved"], base["progress"]),
                         (variant["moved"], variant["progress"]))
        self.assertNotEqual(base["quest"], variant["quest"])

    def test_info_nce_prefers_a_correct_ranking(self) -> None:
        rows = self._rows(12)
        batch = self._batch(rows)
        model = t.EntityPointer(hidden=32, priors=(0.25, 0.08))
        model.eval()
        with torch.no_grad():
            _scores, _moved, _progress, entity, q = model(batch, return_embeddings=True)
            good = t.info_nce_loss(q, entity, batch["cand_mask"], batch["labels"],
                                   hard_negatives=4)
        self.assertTrue(torch.isfinite(good), "%.3f" % float(good))

        # A ranking where the labelled candidate really is on top must cost less than
        # one where a sibling outranks it. Shifting the labels instead would only
        # rename an equally-plausible candidate and prove nothing.
        batch_rows, candidates, width = 6, 5, 8
        q = torch.eye(width)[:batch_rows]
        entity = torch.zeros(batch_rows, candidates, width)
        mask = torch.ones(batch_rows, candidates)
        labels = torch.zeros(batch_rows, dtype=torch.long)
        entity[:, 0] = q
        entity[:, 1] = -q
        aligned = t.info_nce_loss(q, entity, mask, labels, hard_negatives=3)
        entity[:, 0] = -q
        entity[:, 1] = q
        inverted = t.info_nce_loss(q, entity, mask, labels, hard_negatives=3)
        self.assertGreater(float(inverted), float(aligned),
                           "wrong ranking should cost more")

    def test_focal_downweights_easy_examples(self) -> None:
        hard = t.focal_bce(torch.tensor([0.0]), torch.tensor([1.0]), gamma=2.0)
        easy = t.focal_bce(torch.tensor([6.0]), torch.tensor([1.0]), gamma=2.0)
        plain_hard = torch.nn.functional.binary_cross_entropy_with_logits(
            torch.tensor([0.0]), torch.tensor([1.0]))
        self.assertLess(float(hard), float(plain_hard))
        self.assertLess(float(easy), 0.01 * float(hard),
                        "%.5f vs %.4f" % (float(easy), float(hard)))
        balanced = t.focal_bce(torch.tensor([0.0, 0.0]), torch.tensor([1.0, 0.0]),
                               gamma=0.0, balance=0.9)
        self.assertAlmostEqual(float(balanced), -math.log(0.5) * 0.5, places=5,
                               msg="%.4f" % float(balanced))

    def test_priors_seed_the_bias(self) -> None:
        model = t.EntityPointer(hidden=16, priors=(0.25, 0.01))
        move = float(model.move[-1].bias)
        progress = float(model.progress[-1].bias)
        self.assertAlmostEqual(move, math.log(0.25 / 0.75), places=5,
                               msg="%.4f" % move)
        self.assertAlmostEqual(progress, math.log(0.01 / 0.99), places=5,
                               msg="%.4f" % progress)
        self.assertTrue(math.isfinite(float(
            t.EntityPointer(hidden=16, priors=(0.0, 1.0)).progress[-1].bias)))

    def test_augmenter_is_deterministic_and_rephrases(self) -> None:
        rows = self._rows(3)
        augmenter = Augmenter(kb=None, gazetteer=None)
        first = augmenter.variants(rows[0], 5, seed=0)
        again = augmenter.variants(rows[0], 5, seed=0)
        self.assertEqual(first, again, repr(first[:2]))
        self.assertEqual(len(set(first)), len(first), "%d" % len(first))
        self.assertTrue(all(v.strip().lower() != rows[0]["goal"] for v in first))
        self.assertTrue(all("marchand 0" in v for v in first), repr(first))

    def test_augmenter_localises_through_the_kb(self) -> None:
        class FakeKB:
            def item(self, item_id, lang="fr"):
                class Record:
                    name = {"fr": "Laine de Bouftou", "en": "Bouftou Wool"}[lang if lang in
                                                                             ("fr", "en") else "fr"]
                return Record()

            def monster(self, monster_id, lang="fr"):
                class Record:
                    name = "Bouftou"
                return Record()

            def quest(self, quest_id, lang="fr"):
                return None

        augmenter = Augmenter(kb=FakeKB(), gazetteer=None)
        row = {"goal": "trouve la Laine de Bouftou",
               "chosen": {"kind": "item", "gid": 384}}
        found = [v for v in augmenter.variants(row, 8, seed=3) if "Bouftou Wool" in v]
        self.assertTrue(found, repr(found[:1]))

    def test_augmenter_swaps_gazetteer_surfaces(self) -> None:
        class FakeGazetteer:
            payload = {"names": {"item": {"laine de bouftou": "Laine de Bouftou",
                                          "laine du bouftou": "Laine de Bouftou"}}}

        augmenter = Augmenter(kb=None, gazetteer=FakeGazetteer())
        row = {"goal": "trouve laine de bouftou", "chosen": {"kind": "item", "gid": 384}}
        goals = [v for v in augmenter.variants(row, 10, seed=1) if "laine du bouftou" in v]
        self.assertTrue(goals, repr(goals[:1]))
        self.assertTrue(all("lainedu" not in g for g in goals))

    @staticmethod
    def _rows(count: int = 12) -> list:
        rows = []
        for index in range(count):
            rows.append({
                "goal": "va voir le marchand %d" % (index % 3),
                "tool": "fight.plan",
                "chosen": {},
                "entities": {
                    "self": {"cell": 100 + index, "level": 60},
                    "npcs": [{"template_id": 1000 + index % 3, "name": "Marchand",
                              "cell": 200 + index}],
                    "mobs": [{"main_mob_id": 7000 + index % 2, "cell": 300 + index}],
                },
                "moved": index % 4 == 0,
                "progress": index % 12 == 0,
            })
        return rows

    @staticmethod
    def _batch(rows: list) -> dict:
        return t.collate([t.PointerRows._item(row, row["goal"]) for row in rows])


if __name__ == "__main__":
    unittest.main()