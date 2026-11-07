"""What the mechanic-aware head must never lose, and the invariance that proves why it exists.

`tools/wsp_expressivity.py structure` measured the defect on 2,048 real boss boards: in
`combat/encounter/model.py` the difference between the target rankings of two spells is exactly
`t_bias[s1] - t_bias[s2]`, a learned constant, with standard deviation 0.000 across boards. That
is not a training shortfall -- "the dispel goes on whoever holds the state" has no representation
in that head at any weights. `mech_net` scores a pair as `q(spell) . k(target)`, so the ranking is
a function of both, and the same measurement comes out the other way (gap standard deviation
0.477 on the same boards).

These tests pin both halves: the new head must keep pairing spell with target, and the shipped
head must keep failing to, because the argument that justified the new architecture rests on that
invariance. If either stops holding, the docstring in `mech_net` is no longer true and the test
says so.

The third arm is the property the WSP write depends on: `t_q = M, t_k = I` must reproduce
`s^T M a` to float precision, because `tools/wsp_program_target.py` authors `M` in closed form and
installs it rather than training it. An approximation there is a different network than the one
that was solved.
"""
from __future__ import annotations

import unittest

import jax
import jax.numpy as jnp
import numpy as np

from combat.encounter import mech_net, model
from combat.encounter.env import (Config, action_masks, initial_state, observe)
from combat.test_env_math import CFG, fixture


def typed(obs, glob, attn, tables, cfg, spell_feats=9):
    """`observe` lays out every table column; the head is defined over the spells `cfg` allows."""
    columns = tables["spell_ap"].shape[2]
    base = obs.shape[-1] - columns * spell_feats
    return {"actor": obs[..., :base],
            "spell": obs[..., base:].reshape(obs.shape[0], obs.shape[1], columns,
                                             spell_feats)[:, :, :cfg.spells],
            "glob": glob, "mask": attn}


class ContractTests(unittest.TestCase):
    def setUp(self):
        spec, tables = fixture()
        self.cfg = Config(allies=CFG.allies, enemies=CFG.enemies, spells=CFG.spells,
                          max_rounds=CFG.max_rounds, max_dist=CFG.max_dist,
                          casts_per_turn=CFG.casts_per_turn)
        self.state = initial_state(spec, tables, self.cfg)
        self.tables = tables
        self.obs, self.glob, self.attn = observe(self.state, tables, self.cfg)
        key = jax.random.PRNGKey(3)
        self.old = model.init_params(key, self.obs.shape[-1], self.glob.shape[-1], self.cfg)
        self.feats = typed(self.obs, self.glob, self.attn, self.tables, self.cfg)
        self.new = mech_net.init_params(key, self.feats["actor"].shape[-1],
                                        self.feats["spell"].shape[-1],
                                        self.glob.shape[-1], self.cfg)

    def test_logits_have_the_shapes_the_env_and_trainer_expect(self):
        a = model.forward(self.old, self.obs, self.glob, self.attn)
        b = mech_net.forward(self.new, self.feats)
        for key in ("move", "spell", "target", "value"):
            self.assertEqual(a[key].shape, b[key].shape,
                             "%s differs, so the rollout or the loss would not run on both" % key)

    def test_greedy_action_is_always_legal(self):
        masks = action_masks(self.state, self.tables, self.cfg)
        acts = mech_net.greedy_actions(mech_net.forward(self.new, self.feats), masks, self.cfg)
        s, t = np.asarray(acts["spell"])[0, 0], np.asarray(acts["target"])[0, 0]
        self.assertTrue(bool(np.asarray(masks["spell"])[0, 0, s]), "picked an illegal spell")
        if s < self.cfg.spells:
            self.assertTrue(bool(np.asarray(masks["target"])[0, 0, s, t]),
                            "picked a target this spell cannot legally take")

    def test_target_logits_carry_a_pass_column(self):
        """The write and the masks index `cfg.spells` as pass; a head without that column is a
        silent off-by-one that would make the pass spell unselectable."""
        logits = mech_net.forward(self.new, self.feats)
        self.assertEqual(logits["target"].shape[2], self.cfg.n_spells)


class SpellConditioningTests(unittest.TestCase):
    """The claim under test: the new head's target ranking depends on which spell is chosen."""

    def setUp(self):
        spec, tables = fixture()
        self.cfg = Config(allies=CFG.allies, enemies=CFG.enemies, spells=CFG.spells,
                          max_rounds=CFG.max_rounds, max_dist=CFG.max_dist,
                          casts_per_turn=CFG.casts_per_turn)
        n = self.cfg.n_actors
        key = jax.random.PRNGKey(5)
        self.actor = jax.random.normal(key, (2, n, 13)).astype(jnp.float32)
        self.actor = self.actor.at[1].set(jnp.flip(self.actor[0], axis=0))
        self.spell = jax.random.normal(jax.random.PRNGKey(6), (2, n, self.cfg.spells, 9))
        self.glob = jax.random.normal(jax.random.PRNGKey(7), (2, 9))
        self.mask = jnp.ones((2, n), bool)
        self.new = mech_net.init_params(key, 13, 9, 9, self.cfg)
        obs = jnp.concatenate([self.actor, self.spell.reshape(2, n, -1)], axis=-1)
        self.old = model.init_params(key, obs.shape[-1], 9, self.cfg)
        self.old_obs = obs

    def _gap(self, target, s1=0, s2=1):
        return np.asarray(target)[:, 0, s1, :] - np.asarray(target)[:, 0, s2, :]

    def test_the_shipped_head_cross_spell_gap_is_a_constant(self):
        """Two different boards, same actor count: the shipped gap cannot move, and that is the
        defect, so the test fails the moment the head learns to pair spell with target."""
        bias = np.asarray(self.old["t_bias"])
        gap = self._gap(model.forward(self.old, self.old_obs, self.glob, self.mask)["target"])
        np.testing.assert_allclose(gap[0], bias[0] - bias[1], rtol=1e-4, atol=1e-4)
        np.testing.assert_allclose(gap[0], gap[1], rtol=1e-4, atol=1e-4,
                                   err_msg="two boards produced two different cross-spell gaps")
        self.assertLess(gap.std(axis=0).max(), 1e-4)

    def test_the_new_head_cross_spell_gap_follows_the_board(self):
        gap = self._gap(mech_net.forward(self.new, {"actor": self.actor, "spell": self.spell,
                                                   "glob": self.glob,
                                                   "mask": self.mask})["target"])
        self.assertGreater(float(gap.std(axis=0).mean()), 1e-3,
                           "the relational head stopped conditioning the target on the spell")
        self.assertGreater(float(np.abs(gap[0] - gap[1]).max()), 1e-3,
                           "the gap did not follow the actors when their features moved")


class ClosedFormInstallTests(unittest.TestCase):
    def test_the_written_matrix_reproduces_the_solved_joint_exactly(self):
        spec, tables = fixture()
        cfg = Config(allies=CFG.allies, enemies=CFG.enemies, spells=CFG.spells,
                     max_rounds=CFG.max_rounds, max_dist=CFG.max_dist,
                     casts_per_turn=CFG.casts_per_turn)
        state = initial_state(spec, tables, cfg)
        obs, glob, attn = observe(state, tables, cfg)
        feats = typed(obs, glob, attn, tables, cfg)
        hidden = 12
        params = mech_net.init_params(jax.random.PRNGKey(9), feats["actor"].shape[-1],
                                      feats["spell"].shape[-1], glob.shape[-1], cfg,
                                      hidden=hidden, heads=4)
        actors, spell_tokens, _ = mech_net.trunk(params, feats["actor"], feats["spell"],
                                                 glob, attn, heads=4)
        rng = np.random.default_rng(0)
        M = rng.normal(scale=0.3, size=(hidden, hidden)).astype(np.float32)
        written = mech_net.install_relational(params, M, hidden)
        joint = np.asarray(mech_net.forward(written, feats)["target"])[:, 0, :cfg.spells, :]
        solved = np.einsum("bkd,de,bje->bkj", np.asarray(spell_tokens)[:, 0], M,
                           np.asarray(actors))
        np.testing.assert_allclose(joint, solved, rtol=1e-4, atol=1e-4,
                                   err_msg="the closed-form solve is not what the network computes")


if __name__ == "__main__":
    unittest.main()
