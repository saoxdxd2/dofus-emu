"""The pricing tool's refusals have to refuse, before it builds a network.

`tools/mech_input_price.py` compares two checkpoints that differ only in what their observation
carries. Every arm-level claim it makes is only as good as its guardrails:

  wrong blindness   a checkpoint whose recorded `blind` is not the arm being compared cannot be
                    scored as that arm -- the width is 81 either way, so nothing else would catch it
  wrong width       a 49-wide checkpoint cannot read an 81-wide board, and a silent pad would score a
                    policy that does not exist
  wrong config      a checkpoint trained with different max_rounds/enemies cannot be evaluated on
                    boards whose fights run under different rules

All three must raise `SystemExit` naming the mismatch *before* `make_eval_rollout` is constructed,
which is why the refusals sit above the parameter conversion in `arm()`. The passing case is not
stubbed here on purpose: the real checkpoints are priced by the tool itself, and a synthetic
payload that passes every guard while the real one is refused is exactly the inverted outcome a
guard test should never certify.

    python tools/test_mech_input_price.py
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import pickle
import sys
import tempfile
import unittest

import numpy as np
import jax

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
sys.path.insert(0, str(BRAIN))
sys.path.insert(0, str(REPO / "tools"))

from combat.encounter import model                                # noqa: E402
from combat.encounter.env import Config                           # noqa: E402
from combat.encounter.train import table_keys                     # noqa: E402

import mech_input_price as price                                  # noqa: E402

TD = pathlib.Path(os.environ.get(
    "DOFUS_EVIDENCE", pathlib.Path.home() / "Documents" / "dofus-evidence")) / "touch-data"
CFG = Config(allies=1, enemies=4, spells=4, max_rounds=24)
REAL = TD / "encounters_boss_swarm_hold_s4_2048.npz"


class RefusalTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.tables = price.load(REAL, 4, CFG)
        params = model.init_params(jax.random.PRNGKey(1), 81, 13, CFG,
                                   hidden=96, layers=2)
        self.art = {"config": CFG.__dict__, "blind": [], "feat_dim": 81, "params": params,
                    "global_dim": 13, "hidden": 96}
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _checkpoint(self, name, **changes):
        payload = dict(self.art)
        payload.update(changes)
        path = pathlib.Path(self.dir.name) / name
        path.write_bytes(pickle.dumps(payload))
        return str(path)

    def test_a_checkpoint_with_the_wrong_blindness_is_refused(self):
        # this is the arm the tool exists to compare: the *visible* arm carries no blind list, and
        # scoring the visible one in the blinded slot would silently measure the same file twice
        path = self._checkpoint("blind.pkl")
        with self.assertRaises(SystemExit) as ctx:
            price.arm(path, self.spec, self.tables, CFG,
                      blind_expected=("mechanics", "summons", "pool"))
        self.assertIn("blind", str(ctx.exception))

    def test_a_checkpoint_at_the_old_width_cannot_read_a_swarm_board(self):
        path = self._checkpoint("old.pkl", feat_dim=49)
        with self.assertRaises(SystemExit) as ctx:
            price.arm(path, self.spec, self.tables, CFG, blind_expected=())
        self.assertIn("feat_dim", str(ctx.exception))
        self.assertIn("49", str(ctx.exception))

    def test_a_checkpoint_trained_on_other_rules_cannot_be_scored_here(self):
        path = self._checkpoint("other.pkl", config={**CFG.__dict__, "max_rounds": 8})
        with self.assertRaises(SystemExit) as ctx:
            price.arm(path, self.spec, self.tables, CFG, blind_expected=())
        self.assertIn("config", str(ctx.exception))

    def test_the_blinded_arm_is_accepted_when_it_records_its_own_blindness(self):
        # the control, and it is the one that catches a guard that refuses everything: with the
        # recorded blindness and matching config the tool must get as far as constructing its
        # rollout, so a refusal would be a false positive rather than a guard
        path = self._checkpoint("blind.pkl", blind=["mechanics", "summons", "pool"])
        try:
            out = price.arm(path, self.spec, self.tables, CFG,
                            blind_expected=("mechanics", "summons", "pool"))
        except (KeyError, AttributeError, TypeError, ValueError):
            self.fail("the blinded arm was rejected before the parameter conversion even though it "
                      "matches its own arm -- the guard is refusing the real use")
        self.assertEqual(out["blind"], ["mechanics", "summons", "pool"])
        self.assertEqual(out["win_flags"].shape, (4,))


class VerdictTests(unittest.TestCase):
    def test_the_margin_is_added_to_the_delta_before_deciding(self):
        cases = [
            (0.05, 0.01, "mechanic input pays at this training budget"),
            (-0.05, 0.01, "mechanic input costs wins at this training budget"),
            (-0.05, 0.06, "no measurable effect"),
        ]
        for delta, margin, expected in cases:
            with self.subTest(delta=delta, margin=margin):
                verdict = price.input_verdict({"delta": delta, "ci95": [-margin, margin]})
                self.assertIn(expected, verdict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
