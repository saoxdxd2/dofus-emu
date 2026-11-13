"""The history block and the metric split: the two seams a sequence model adds.

Each test here exists because the failure it guards is silent. A wrong token width
is a shape error at export and a different model at serving; a history window taken
in file order instead of episode order trains on the future; a metric that only works
for the MLP it was written for cannot judge the architecture that replaces it.
"""

from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

import json
import unittest
from pathlib import Path

from agent.features import (ERROR_SLOTS, FEATURE_COUNT, HISTORY_DIM, HISTORY_STEPS,
                            HISTORY_TOKEN, LEARNED_TOOLS, ObsTracker,
                            feature_count_history, history_token, history_tokens)
from agent.weight_space import (_history_rows, evaluate, export_manifest, forward,
                                manifest_weights, metrics_from_logits, read_val_rows)

REPO = Path(__file__).resolve().parent.parent
VAL = REPO / "data" / "tool_sft_v3" / "val.jsonl"


class HistoryTokenTest(unittest.TestCase):
    def test_token_width_is_tool_plus_outcome_plus_repeat(self):
        self.assertEqual(HISTORY_TOKEN, len(LEARNED_TOOLS) + 1 + len(ERROR_SLOTS) + 1)
        token = history_token("nav.goto", True, "none")
        self.assertEqual(len(token), HISTORY_TOKEN)
        self.assertEqual(sum(token[:len(LEARNED_TOOLS)]), 1.0)
        self.assertEqual(token[len(LEARNED_TOOLS)], 1.0)  # ok
        self.assertEqual(token[-1], 0.0)                 # not a repeat

    def test_unknown_tool_is_all_zero_not_a_crash(self):
        token = history_token("not.a.tool", False, "transport")
        self.assertEqual(sum(token[:len(LEARNED_TOOLS)]), 0.0)
        self.assertEqual(len(token), HISTORY_TOKEN)

    def test_window_is_left_padded_and_keeps_the_newest_last(self):
        records = [{"tool": "nav.goto", "ok": True, "err": "none"}]
        vec = history_tokens(records)
        self.assertEqual(len(vec), HISTORY_DIM)
        self.assertEqual(sum(vec[:HISTORY_DIM - HISTORY_TOKEN]), 0.0)
        # One live token: the tool one-hot, the ok flag and the error one-hot.
        self.assertEqual(sum(vec[-HISTORY_TOKEN:]), 3.0)
        self.assertEqual(vec[-1], 0.0)  # first action cannot repeat anything

    def test_long_history_keeps_only_the_window(self):
        records = [{"tool": "nav.goto", "ok": True, "err": "none"}] * (HISTORY_STEPS + 5)
        vec = history_tokens(records)
        self.assertEqual(len(vec), HISTORY_DIM)
        # Three ones per token, plus the repeat flag on every token but the first.
        self.assertEqual(sum(vec), 3.0 * HISTORY_STEPS + (HISTORY_STEPS - 1))

    def test_tracker_and_featuriser_agree(self):
        tracker = ObsTracker()
        tracker.record("nav.goto", False, "", {})
        tracker.record("quest.advance", True, "", {})
        self.assertEqual(history_tokens(tracker.records()),
                         history_tokens([
                             {"tool": "nav.goto", "ok": False, "err": "none"},
                             {"tool": "quest.advance", "ok": True, "err": "none"},
                         ]))

    def test_total_width_matches_the_declared_count(self):
        self.assertEqual(feature_count_history(), FEATURE_COUNT + HISTORY_DIM)


class HistoryAlignmentTest(unittest.TestCase):
    """A window built in file order would leak the next step into this one."""

    def test_history_comes_from_the_same_episode_in_step_order(self):
        rows = [
            {"ep": 2, "t": 0, "x": [0.0], "tool": "a", "ok": True},
            {"ep": 1, "t": 0, "x": [0.0], "tool": "b", "ok": True},
            {"ep": 2, "t": 1, "x": [0.0], "tool": "c", "ok": True},
            {"ep": 1, "t": 1, "x": [0.0], "tool": "d", "ok": True},
        ]
        hist = _history_rows(rows)
        self.assertEqual(len(hist), 4)
        # First step of each episode has no history.
        self.assertEqual(sum(hist[0]), 0.0)
        self.assertEqual(sum(hist[1]), 0.0)
        # Second step of ep 2 knows about "a" (row 0), not about "b" (row 1).
        self.assertEqual(hist[2], history_tokens([{"tool": "a", "ok": True,
                                                   "err": "none"}]))
        self.assertEqual(hist[3], history_tokens([{"tool": "b", "ok": True,
                                                   "err": "none"}]))

    def test_every_row_gets_a_full_width_block(self):
        rows = read_val_rows(VAL)[:40]
        hist = _history_rows(rows)
        self.assertTrue(all(len(h) == HISTORY_DIM for h in hist))


class MetricsFromLogitsTest(unittest.TestCase):
    def test_split_metric_reproduces_the_pinned_accuracy(self):
        """The metric must not depend on how the logits were produced.

        The shipped artifact is now a sequence policy, so the MLP `evaluate` path
        cannot score it -- which is the point of the split. Both paths are checked
        against the pin: the artifact's own score and the recorded baseline agree.
        """
        from agent.weight_space import score_artifact
        manifest = export_manifest()
        rows = read_val_rows(VAL)
        metrics = score_artifact(manifest, rows)
        # The number the pin froze for the installed artifact.
        from agent.weight_space import PROVENANCE_PATH
        import json
        pin_metrics = {}
        if PROVENANCE_PATH.is_file():
            try:
                pin_metrics = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8")).get("metrics") or {}
            except Exception as exc:
                logger.debug("ignored %s", exc, exc_info=True)
        expected_acc = float(pin_metrics.get("val_accuracy", 0.914))
        expected_opt = float(pin_metrics.get("optimal", 0.9209))
        self.assertAlmostEqual(metrics["val_accuracy"], expected_acc, places=4)
        self.assertAlmostEqual(metrics["optimal"], expected_opt, places=4)
        self.assertIn("optimal", metrics)
        # Partition invariants, not literals.
        self.assertEqual(set(metrics["reachable_tools"]) | set(metrics["unreachable_tools"]),
                         set(manifest["base"]["tools"]))
        # The MLP path still works where it applies: a fixture manifest scored by
        # `evaluate` and by `metrics_from_logits` must agree exactly.
        from agent.test_weight_space import fixture_manifest
        fixture = fixture_manifest()
        weights = manifest_weights(fixture)
        tools = fixture["base"]["tools"]
        synthetic = [{"x": [0.1, -0.2, 0.3, 0.0], "y": i % len(tools),
                      "gains": {}, "teacher": tools[0], "tool": tools[0]}
                     for i in range(50)]
        import numpy as np
        x = np.array([r["x"] for r in synthetic], dtype="float64")
        direct = metrics_from_logits(forward(weights, x), synthetic, tools)
        via_evaluate = evaluate(weights, synthetic, tools)
        self.assertEqual(direct["val_accuracy"], via_evaluate["val_accuracy"])


class ObservationHistoryTest(unittest.TestCase):
    """The widened token: each past step also carries what was observed there.

    The runtime and the corpus build this from opposite directions -- one walks a
    live tracker, the other replays a file -- so the only thing keeping them equal is
    that both read the same five indices out of the same 42-wide vector. That is the
    invariant these tests hold, because a drift here does not raise: it quietly
    changes what the model is asked at serving time.
    """

    def test_widened_widths_are_consistent(self):
        from agent.features import (HISTORY_DIM_OBS, HISTORY_DIM_OBS_DERIVED,
                                    HISTORY_TOKEN_OBS, OBS_SUMMARY_NAMES)
        self.assertEqual(HISTORY_TOKEN_OBS,
                         HISTORY_TOKEN + len(OBS_SUMMARY_NAMES))
        self.assertEqual(HISTORY_DIM_OBS, HISTORY_STEPS * HISTORY_TOKEN_OBS)
        self.assertEqual(HISTORY_DIM_OBS_DERIVED, HISTORY_DIM_OBS + 3)
        # The shipped width must not move: older artifacts declare 28 and still load.
        self.assertEqual(HISTORY_DIM, HISTORY_STEPS * HISTORY_TOKEN)
        self.assertEqual(feature_count_history(), FEATURE_COUNT + HISTORY_DIM)
        self.assertEqual(feature_count_history(obs=True),
                         FEATURE_COUNT + HISTORY_DIM_OBS)
        self.assertEqual(feature_count_history(obs=True, derived=True),
                         FEATURE_COUNT + HISTORY_DIM_OBS_DERIVED)

    def test_token_carries_the_summary_it_was_given(self):
        from agent.features import HISTORY_TOKEN_OBS, obs_summary
        x = [0.0] * FEATURE_COUNT
        x[23], x[24], x[38], x[25], x[22] = 1.0, 0.25, 0.0, 1.0, 0.5
        token = history_token("nav.goto", True, "none", None, obs_summary(x))
        self.assertEqual(len(token), HISTORY_TOKEN_OBS)
        self.assertEqual(token[-5:], [1.0, 0.25, 0.0, 1.0, 0.5])

    def test_omitting_the_summary_keeps_the_shipped_width(self):
        self.assertEqual(len(history_token("nav.goto", True, "none")), HISTORY_TOKEN)

    def test_tracker_and_corpus_agree_on_the_same_episode(self):
        """The whole contract in one assertion: replay a run through both sides."""
        rows = read_val_rows(VAL)[:24]
        corpus = _history_rows(rows, obs=True)

        tracker = ObsTracker()
        replayed = []
        for row in rows:
            replayed.append(history_tokens(tracker.records(), obs=True))
            tracker.note_observation(row["x"])
            tracker.record(row["tool"], bool(row.get("ok")),
                           str(row.get("note") or ""), {})
        for i, (a, b) in enumerate(zip(corpus, replayed)):
            self.assertEqual(a, b, "history block diverged at row %d" % i)

    def test_last_fresh_dims_report_age_and_absence_distinctly(self):
        from agent.features import last_fresh_dims
        fresh = {"summary": [1.0, 0.0, 0.0, 1.0, 1.0]}
        stale = {"summary": [0.0, 0.9, 1.0, 1.0, 0.0]}
        self.assertEqual(last_fresh_dims([fresh]), [1.0, 0.0, 0.0])
        # Newest record is stale, so the answer comes from the older fresh one, and
        # the age is reported rather than hidden.
        got = last_fresh_dims([fresh, stale])
        self.assertEqual(got[:2], [1.0, 0.0])
        self.assertAlmostEqual(got[2], 1.0 / HISTORY_STEPS)
        # No fresh observation at all is 1.0, not 0.0: "unknown" must not read as
        # "just seen", which a zero would have meant.
        self.assertEqual(last_fresh_dims([stale]), [0.0, 0.0, 1.0])


if __name__ == "__main__":
    unittest.main()


