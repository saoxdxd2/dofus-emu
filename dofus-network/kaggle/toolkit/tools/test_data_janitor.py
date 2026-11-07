"""What the janitor must refuse, and what it must never hide.

Each fixture row isolates one reason. The assertions are about the *named*
exclusion, because the failure mode this tool exists to prevent is a corpus that
silently trains on scripted, unpaired or stale rows and reports a number nobody
can trace.
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

from agent.features import FEATURE_COUNT, FEATURE_NAMES  # noqa: E402
from tools import data_janitor  # noqa: E402


def row(index: int, segment: str, tool: str = "quest.advance", **over):
    base = {
        "id": "%s#%d" % (segment, index), "segment": segment, "tool": tool,
        "source": "unit.jsonl", "goal": "unit goal", "instance": "unit",
        "t": index, "at": 1_760_000_000.0 + index, "ok": True, "error": None,
        "refused": None, "latency_ms": 10, "dt_ms": 1000, "obs_age_ms": 200,
        "obs_source": "fresh", "objective_type": 14,
        "was_read": tool == "state.snapshot", "claim": "quest",
        "claim_met": True, "miss_streak": 0, "repeat_streak": 0,
        "repeat_streak_norm": 0, "explored": False, "p_explore": 0.0,
        "has_pair": True, "changed": ["cell"], "moved": True, "progress": False,
        "advance": False, "moved_keys": ["cell"], "progress_keys": [],
        "advance_keys": [], "objectives_completed": 0,
        "x": [0.0] * FEATURE_COUNT, "x_source": "objective_features",
        "context": {"character": {"id": 1, "level": 3, "breed": 8},
                    "tutorial": False},
    }
    base.update(over)
    return base


def split_map(by_split):
    train, val, test = by_split["train"], by_split["val"], by_split["test"]
    report = data_janitor.clean({"train": train, "val": val, "test": test},
                                reference=None, now=1_760_500_000.0)
    kept = {name: [item["id"] for item in rows]
            for name, rows in report["kept_rows"].items()}
    return report, kept


class JanitorTests(unittest.TestCase):

    def test_each_exclusion_is_named(self):
        by_split = {
            "train": [
                row(1, "s1"), row(2, "s1"),
                row(1, "s1"),                      # duplicate id of s1#1
                row(1, "s2"),                      # leak: also in val/test
                row(1, "s3", context={"character": {"id": 1, "level": 3},
                                      "tutorial": True}),
                row(1, "s4", has_pair=False),
                row(1, "s5", x=None),
                row(1, "s6", x=[0.0] * (FEATURE_COUNT - 1)),
                {key: value for key, value in row(1, "s7").items()
                 if key not in ("advance", "claim", "refused", "at", "context")},
                row(1, "s8", tool="state.snapshot"),
                row(1, "s9", at=1_900_000_000.0),  # future timestamp
            ],
            "val": [row(1, "s2")],
            "test": [row(1, "s2")],
        }
        report, kept = split_map(by_split)
        reasons = report["quarantine"]["by_reason"]
        # A tie between splits keeps the row in train (the keeper order), so the
        # leaked segment still supplies one trainable row and loses the other two.
        self.assertEqual(kept["train"], ["s1#1", "s1#2", "s2#1", "s8#1"])
        self.assertEqual(kept["val"], [])
        self.assertEqual(kept["test"], [])
        self.assertEqual(report["kept"]["actions"], 3)
        self.assertEqual(report["kept"]["reads"], 1)
        for reason in ("duplicate_id", "split_leak", "tutorial", "unpaired",
                       "no_features", "feature_width", "stale_schema",
                       "future_timestamp"):
            self.assertGreaterEqual(reasons.get(reason, 0), 1, reason)
        self.assertEqual(reasons["split_leak"], 2)
        self.assertIn("feature_width", " ".join(report["schema"]["fatal"]))
        self.assertEqual(report["leaks"]["segments"], 1)
        self.assertEqual(report["provenance"]["characters"], {"1": 4})
        self.assertEqual(report["provenance"]["levels"], {"3": 4})
        self.assertEqual(report["provenance"]["sessions"], 1)

    def test_a_wholly_stale_corpus_is_fatal(self):
        stale = {key: value for key, value in row(1, "old").items()
                 if key not in ("advance", "claim", "refused", "at", "context")}
        report, kept = split_map({"train": [stale], "val": [], "test": []})
        self.assertEqual(report["kept"]["total"], 0)
        self.assertEqual(report["schema"]["stale_rows"], 1)
        self.assertIn("stale_corpus", " ".join(report["schema"]["fatal"]))
        self.assertEqual(report["quarantine"]["by_reason"].get("stale_schema"), 1)

    def test_two_splits_of_one_segment_keep_the_bigger_one(self):
        report, kept = split_map({"train": [row(1, "s")],
                                  "val": [row(2, "s"), row(3, "s")],
                                  "test": []})
        self.assertEqual(kept["train"], [])
        self.assertEqual(len(kept["val"]), 2)
        self.assertEqual(report["quarantine"]["by_reason"].get("split_leak"), 1)

    def test_drift_names_the_feature_and_the_range(self):
        def vector(feature0: float, feature1: float, feature2: float):
            out = [0.0] * FEATURE_COUNT
            out[0], out[1], out[2] = feature0, feature1, feature2
            return out

        reference = [vector(0.0, 1.0, 5.0), vector(1.0, 1.0, 5.0),
                     vector(2.0, 1.0, 5.0)]
        live = [vector(10.0, 1.0, 0.0), vector(12.0, 1.0, 0.0),
                vector(14.0, 1.0, 0.0)]
        report = data_janitor.drift(live, reference, {"path": "ref"})
        self.assertEqual(report["status"], "compared")
        self.assertFalse(report["in_coverage"])
        flagged = {cell["name"]: cell for cell in report["flags"]}
        # Feature 0 is a genuine mean shift with a defined reference std.
        self.assertIn(FEATURE_NAMES[0], flagged)
        self.assertGreater(flagged[FEATURE_NAMES[0]]["z"], 10.0)
        self.assertEqual(flagged[FEATURE_NAMES[0]]["out_of_range_share"], 1.0)
        # Feature 1 is the same constant on both sides: not drift.
        self.assertNotIn(FEATURE_NAMES[1], flagged)
        # Feature 2 is constant in the reference at a value live never shows:
        # z is undefined, so the range is what catches it.
        self.assertIn(FEATURE_NAMES[2], flagged)
        self.assertTrue(flagged[FEATURE_NAMES[2]]["reference_constant"])
        self.assertEqual(flagged[FEATURE_NAMES[2]]["out_of_range_share"], 1.0)

    def test_history_trends_only_real_measurements(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary) / "closes.jsonl"
            rows = [
                {"at": "A", "steps": {"janitor": {
                    "ok": True, "kept": {"total": 500, "actions": 390},
                    "quarantine": {"total": 9160, "by_reason": {"tutorial": 8865}},
                    "in_coverage": False, "drift_flags": 8}}},
                {"at": "B", "steps": {"janitor": {"ok": True,
                                                   "skipped": "interval"}}},
                {"at": "C", "steps": {"janitor": {
                    "ok": True, "kept": {"total": 700, "actions": 520},
                    "quarantine": {"total": 9300, "by_reason": {"tutorial": 9000}},
                    "in_coverage": True, "drift_flags": 5}}},
            ]
            ledger.write_text("\n".join(json.dumps(row) for row in rows),
                              encoding="utf-8")
            trend = data_janitor.history(ledger)
            self.assertEqual([row["at"] for row in trend["rows"]], ["A", "C"])
            self.assertEqual(trend["trend"]["kept_delta"], 200)
            self.assertEqual(trend["trend"]["drift_delta"], -3)
            self.assertTrue(trend["trend"]["coverage_now"])
            self.assertEqual(trend["rows"][0]["by_reason"]["tutorial"], 8865)

    def test_history_of_a_missing_ledger_is_empty_not_an_error(self):
        trend = data_janitor.history(Path(tempfile.gettempdir()) / "no-such-close.jsonl")
        self.assertEqual(trend["rows"], [])
        self.assertEqual(trend["trend"]["closes"], 0)

    def test_write_is_atomic_and_leaves_the_source_alone(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus = root / "corpus"
            corpus.mkdir()
            source = {"train": [row(1, "s1"), row(1, "s2", x=None)],
                      "val": [row(1, "s3")], "test": [row(1, "s4")]}
            before = {}
            for name, rows in source.items():
                path = corpus / ("%s.jsonl" % name)
                with path.open("w", encoding="utf-8") as handle:
                    for item in rows:
                        handle.write(json.dumps(item) + "\n")
                before[name] = path.read_text(encoding="utf-8")
            report = data_janitor.run(corpus, root / "clean", None, write_out=True)
            self.assertEqual(report["written"], {"train": 1, "val": 1, "test": 1})
            for name in ("train", "val", "test"):
                self.assertEqual((corpus / ("%s.jsonl" % name)).read_text(
                    encoding="utf-8"), before[name])
            self.assertTrue((root / "clean" / "quarantine.jsonl").is_file())
            manifest = json.loads((root / "clean" / "manifest.json").read_text(
                encoding="utf-8"))
            self.assertNotIn("kept_rows", manifest)
            self.assertNotIn("_quarantine", manifest)
            self.assertEqual(manifest["written"]["train"], 1)
            again = data_janitor.run(corpus, root / "clean", None, write_out=True)
            self.assertEqual(again["written"], report["written"],
                             "a second pass over the same input must be idempotent")

    def test_the_labelling_identity_survives_cleaning(self):
        """G92's staleness class, one step downstream: the trainer reads the clean directory.

        If the janitor drops the source manifest's `labeler_sha`, nothing in
        `data/transitions_clean` says which labelling code produced the rows the move head
        trains on -- so the corpus can go stale behind a green gate, exactly as G92 found
        for the built corpus.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus = root / "corpus"
            corpus.mkdir()
            with (corpus / "train.jsonl").open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(row(1, "s1")) + "\n")
            (corpus / "manifest.json").write_text(json.dumps({
                "rows": 1, "label_version": 3, "split_policy": "temporal",
                "labeler_sha": "abc123def456",
                "label_rule": "a change observed in a run of reads is credited"}),
                encoding="utf-8")
            report = data_janitor.run(corpus, root / "clean", None, write_out=True)
            self.assertEqual(report["labeler_sha"], "abc123def456")
            manifest = json.loads((root / "clean" / "manifest.json").read_text(
                encoding="utf-8"))
            self.assertEqual(manifest["labeler_sha"], "abc123def456",
                             "the clean corpus must say who labelled it")
            self.assertEqual(manifest["label_rule"],
                             "a change observed in a run of reads is credited")
            self.assertEqual(manifest["source_manifest"]["label_version"], 3)

            # A corpus written without the identity stays honest: nothing is invented.
            (corpus / "manifest.json").write_text(json.dumps({"rows": 1}),
                                                  encoding="utf-8")
            bare = data_janitor.run(corpus, root / "clean2", None, write_out=True)
            self.assertIsNone(bare["labeler_sha"],
                              "a missing identity is reported as missing, not guessed")


if __name__ == "__main__":
    unittest.main(verbosity=1)
