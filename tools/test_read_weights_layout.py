"""Does `read_weights.input_blocks` name the numbers `observe` actually emits?

The weight inspector claims that the columns it calls `slot0_mechanics` are the five numbers the
client publishes about a cast, and that `slot0_summons` are the bodies. If the mapping were off by
one slot, every conclusion drawn from it ("the encoder puts weight on mechanics") would describe the
wrong weights. So this pins the mapping against the real observation, with distinctive values in
each block, in the same presence-gated order `env.observe` builds them:

  actor base       13 numbers
  per spell slot   9 shipped, then 5 mechanic, then 3 summon -- 17 per slot when both blocks exist
  globals          9 always, then 4 swarm numbers when the pool is in the state

The fixture is the suite's own (`combat.test_env_mechanics`), so this test also fails if somebody
reorders the emission: the observation test there and this one cannot both pass on a bad order.

    python tools/test_read_weights_layout.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

import numpy as np
import jax

REPO = pathlib.Path(__file__).resolve().parent.parent
BRAIN = REPO / "dofus-network"
sys.path.insert(0, str(BRAIN))

from combat.encounter import model                                # noqa: E402
from combat.encounter.env import initial_state, observe            # noqa: E402
from combat.test_env_mechanics import CFG, board, with_summons     # noqa: E402

_TOOL = REPO / "tools" / "read_weights.py"
_spec = importlib.util.spec_from_file_location("read_weights", _TOOL)
read_weights = importlib.util.module_from_spec(_spec)
sys.modules["read_weights"] = read_weights
_spec.loader.exec_module(read_weights)

MECH_VALUES = [1.0, 2.0 / 12.0, -3.0 / 6.0, -1.0 / 4.0, 2.0 / 5.0]
SUMMON_VALUES = [4.0 / 8.0, np.log1p(250.0) / np.log(3001.0), np.log1p(60.0) / np.log(301.0)]


class LayoutTests(unittest.TestCase):
    def test_the_named_blocks_are_the_numbers_observe_emits(self):
        spec, tables = with_summons(
            board(player_hp=500.0, enemy_hp=500.0, spell_damage=25.0, kill=True, push=2.0,
                  ap_delta=-3.0, mp_delta=-1.0, duration=2.0),
            bodies=4.0, hp_each=250.0, dmg_each=60.0)
        obs, glob, _mask = observe(initial_state(spec, tables, CFG), tables, CFG)
        feat_dim = int(np.asarray(obs).shape[-1])
        slots = CFG.spells
        blocks = {label: (lo, hi) for label, lo, hi in
                  read_weights.input_blocks(feat_dim, slots)}
        for j in range(slots):
            # the fixture writes its mechanic and summon values on slot 0 only, so the other slots
            # must read as zeros -- which is also the no-bleed-through arm: a mapping off by one
            # would put slot 0's numbers in slot 1 and fail both halves
            want_mech = MECH_VALUES if j == 0 else [0.0] * 5
            want_summon = SUMMON_VALUES if j == 0 else [0.0] * 3
            m_lo, m_hi = blocks["slot%d_mechanics" % j]
            np.testing.assert_allclose(np.asarray(obs)[0, 1, m_lo:m_hi], want_mech, rtol=1e-6,
                                       err_msg="mechanic slice of slot %d" % j)
            s_lo, s_hi = blocks["slot%d_summons" % j]
            np.testing.assert_allclose(np.asarray(obs)[0, 1, s_lo:s_hi], want_summon, rtol=1e-6,
                                       err_msg="summon slice of slot %d" % j)
        base_lo, base_hi = blocks["actor_base"]
        self.assertEqual((base_lo, base_hi), (0, 13))
        # every slot has exactly 17 numbers, and the first one starts right after the actor block
        self.assertEqual(blocks["slot0_shipped"][0], 13)
        self.assertEqual(blocks["slot0_shipped"][1] - blocks["slot0_shipped"][0], 9)
        self.assertEqual(feat_dim, 13 + 17 * slots)
        # and the pool globals sit at 9..13 of the global vector, not somewhere in the token
        self.assertEqual(int(np.asarray(glob).shape[-1]), 13)

    def test_a_narrow_corpus_keeps_its_nine_number_slots(self):
        # the shipped 49-wide layout (no mechanic, no summon block) must not be read as if the
        # mechanic numbers were hiding inside it -- that misread is how a null result gets invented
        spec, tables = board(player_hp=500.0, enemy_hp=500.0, spell_damage=25.0)
        obs, glob, _mask = observe(initial_state(spec, tables, CFG), tables, CFG)
        feat_dim = int(np.asarray(obs).shape[-1])
        self.assertEqual(feat_dim, 13 + 9 * CFG.spells)
        blocks = {label: (lo, hi) for label, lo, hi in
                  read_weights.input_blocks(feat_dim, CFG.spells)}
        self.assertNotIn("slot0_mechanics", blocks)
        self.assertNotIn("slot0_summons", blocks)
        self.assertEqual(int(np.asarray(glob).shape[-1]), 9)

    def test_initializer_drift_is_zero_at_init_and_catches_blinded_row_writes(self):
        params = model.init_params(jax.random.PRNGKey(7), 81, 13, CFG)
        art = {"params": params, "config": CFG.__dict__, "hidden": 96, "layers": 2,
               "feat_dim": 81, "global_dim": 13,
               "blind": ["mechanics", "summons", "pool"]}
        initial = read_weights.initial_comparison(art, params["enc1"][0], 7, CFG.spells)
        self.assertEqual(initial["blinded_input_l2_from_init"], 0.0)

        enc = np.array(params["enc1"][0])
        enc[22, 0] += 0.25
        params["enc1"] = (enc, params["enc1"][1])
        glob = np.array(params["glob"][0])
        glob[9, 0] += 0.5
        params["glob"] = (glob, params["glob"][1])
        changed = read_weights.initial_comparison(art, params["enc1"][0], 7, CFG.spells)
        self.assertAlmostEqual(changed["blocks"]["slot0_mechanics"]["l2"], 0.25)
        self.assertAlmostEqual(changed["blocks"]["pool_globals"]["l2"], 0.5)
        self.assertAlmostEqual(changed["blinded_input_l2_from_init"], np.sqrt(0.25 ** 2 + 0.5 ** 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
