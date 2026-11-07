"""The mechanic columns have to change the fight, or they are decoration.

`combat/encounter/env.py` now carries seven optional spell columns -- `spell_kill`, `spell_push`,
`spell_ap_delta` / `spell_mp_delta` with `spell_duration`, and `spell_summon_bodies` /
`spell_summon_hp` / `spell_summon_damage` -- sourced from effect ids the client
states unambiguously: 141 "Kills the target", 5 "Pushes back #1 cell(s)", 101 "-#1 AP", 127 "-#1
MP". 46 boss species kill, 148 instances push, 100 drain AP. Nothing in the previous arithmetic
could express any of them: a kill was a damage number, a push was nothing at all, and an AP
reduction had nowhere to be subtracted from that survived the victim's next refill.

Each arm therefore asserts the *opposite* outcome with the column zeroed, which is the mutation
that a formula ignoring its input would survive:

  kill        a 2,000 HP player dies to a 10-damage instant kill, and does not die to the same
              spell with `kill` cleared
  push        a pushed actor leaves its target's range window, and so loses the legal cast
  ap_delta    a timed reduction bites on the victim's own turn -- the only turn it can -- and
              expires, restoring the AP the spell needs
  summon      bodies intercept the damage aimed at the boss, strike back for their own kit, die,
              and halve what they deal as they die -- and a summon row is castable without an
              enemy in range, which is what makes the difference between typing a mechanic and
              applying it
  parity      a board without the columns plays bit-for-bit like the same board with explicit
              zeros, so every corpus and checkpoint predating this change is unaffected
"""
from __future__ import annotations

import unittest

import numpy as np

from combat.encounter.backend import XP
from combat.encounter.env import (Config, action_masks, initial_state, observe,
                                  step)

CFG = Config(allies=1, enemies=1, spells=2, max_rounds=8, max_dist=12,
             casts_per_turn=4)


def board(player_hp=200.0, enemy_hp=200.0, player_ap=12.0, enemy_ap=6.0,
          player_dist=3.0, enemy_dist=3.0, spell_damage=10.0, spell_ap=6.0,
          spell_rmax=6.0, player_mp=6.0, **mechanics):
    """One ally and one enemy, two spell slots, in the shape the corpus stores."""
    spec = {
        "hp": XP.asarray([[player_hp, enemy_hp]]),
        "max_hp": XP.asarray([[max(player_hp, 1.0), max(enemy_hp, 1.0)]]),
        "ap_max": XP.asarray([[player_ap, enemy_ap]]),
        "mp_max": XP.asarray([[player_mp, 3.0]]),
        "power": XP.asarray([[0.0, 0.0]]),
        "res": XP.zeros((1, 2, 5)),
        "dist": XP.asarray([[player_dist, enemy_dist]]),
        "initiative": XP.asarray([[0.0, 1.0]]),
        "alive": XP.asarray([[True, True]]),
        "first_team": XP.asarray([1], dtype="int32"),
    }
    # Both actor rows carry the same kit: numpy raises on the out-of-range slot index that jax
    # silently clamps, so a fixture shaped for one backend is a bug waiting for the other.
    def row(value, dtype=np.float32):
        arr = np.full((1, 2, CFG.spells), value, dtype)
        arr[0, :, 1] = 0                            # slot 1 is the free "pass" column
        return XP.asarray(arr)

    tables = {
        "spell_ap": row(spell_ap),
        "spell_rmin": row(1.0),
        "spell_rmax": row(spell_rmax),
        "spell_damage": row(spell_damage),
        "spell_heal": row(0.0),
        "spell_element": row(0, np.int32),
        "spell_ally": row(False, bool),
    }
    for key, value in mechanics.items():
        arr = np.zeros((1, 2, CFG.spells), bool if key == "kill" else np.float32)
        arr[0, :, 0] = value                        # the enemy's first spell carries it
        tables["spell_" + key] = XP.asarray(arr)
    return spec, tables


def act(spell=0, target=1, move=1):
    return {"move": XP.asarray([[move, move]], dtype="int32"),
            "spell": XP.asarray([[spell, spell]], dtype="int32"),
            "target": XP.asarray([[target, target]], dtype="int32")}


def legal_player_spell(state, tables, spell=0):
    masks = action_masks(state, tables, CFG)
    return bool(np.asarray(masks["spell"])[0, 0, spell])


class KillTests(unittest.TestCase):
    def test_instant_kill_kills_past_any_health_bar(self):
        spec, tables = board(player_hp=2000.0, spell_damage=10.0, kill=True)
        final = step(initial_state(spec, tables, CFG), act(spell=0, target=0), tables, CFG)
        self.assertEqual(float(np.asarray(final["hp"])[0, 0]), 0.0)
        self.assertEqual(int(np.asarray(final["winner"])[0]), 1,
                         "a 2,000 HP player survived a kill effect")

    def test_the_same_spell_without_the_flag_only_damages(self):
        spec, tables = board(player_hp=2000.0, spell_damage=10.0, kill=False)
        final = step(initial_state(spec, tables, CFG), act(spell=0, target=0), tables, CFG)
        self.assertGreater(float(np.asarray(final["hp"])[0, 0]), 1900.0)
        self.assertEqual(int(np.asarray(final["winner"])[0]), -1)


class PushTests(unittest.TestCase):
    def test_push_moves_the_target_and_that_is_the_whole_of_the_geometry(self):
        spec, tables = board(player_dist=3.0, spell_rmax=6.0, push=4.0)
        state = initial_state(spec, tables, CFG)
        pushed = step(state, act(spell=0, target=0), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(pushed["dist"])[0, 0]), 7.0, places=5,
                               msg="the push column reached the environment and moved nothing")

    def test_pushed_past_the_range_window_with_no_movement_left_the_cast_gone(self):
        # The legality rule is walk-aware on purpose, so a push only costs a cast when there is no
        # MP left to walk back: that is the coupling between the two heads, stated as a number.
        spec, tables = board(player_dist=3.0, spell_rmax=6.0, push=4.0, player_mp=0.0)
        pushed = step(initial_state(spec, tables, CFG), act(spell=0, target=0), tables, CFG)
        self.assertFalse(legal_player_spell(pushed, tables),
                         "pushback moved the target and nothing about legality followed")

    def test_the_same_push_number_cleared_keeps_the_cast_available(self):
        spec, tables = board(player_dist=3.0, spell_rmax=6.0, push=0.0, player_mp=0.0)
        pushed = step(initial_state(spec, tables, CFG), act(spell=0, target=0), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(pushed["dist"])[0, 0]), 3.0, places=5)
        self.assertTrue(legal_player_spell(pushed, tables))


class ActionPointTests(unittest.TestCase):
    def test_timed_reduction_bites_on_the_victims_own_turn(self):
        spec, tables = board(player_ap=12.0, enemy_ap=12.0, spell_ap=10.0, ap_delta=-6.0, duration=2.0)
        state = initial_state(spec, tables, CFG)
        drained = step(state, act(spell=0, target=0), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(drained["ap"])[0, 0]), 6.0, places=5,
                               msg="the victim's turn start ignored the reduction")
        self.assertFalse(legal_player_spell(drained, tables),
                         "6 AP cannot afford a 10-AP spell, yet the mask still offered it")

    def test_the_reduction_expires_and_the_spell_comes_back(self):
        spec, tables = board(player_ap=12.0, enemy_ap=12.0, spell_ap=10.0, ap_delta=-6.0, duration=1.0)
        state = initial_state(spec, tables, CFG)
        drained = step(state, act(spell=0, target=0), tables, CFG)
        self.assertFalse(legal_player_spell(drained, tables))
        played = step(drained, act(spell=1, target=1, move=1), tables, CFG)   # player passes
        # Slot 1 is the free column, so the enemy's second turn cannot re-apply the reduction: if
        # this step cast spell 0 again the AP would legitimately still be 6 and the test would
        # prove nothing about expiry.
        restored = step(played, act(spell=1, target=0), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(restored["ap"])[0, 0]), 12.0, places=5,
                               msg="a one-turn reduction outlasted its turn")
        self.assertTrue(legal_player_spell(restored, tables))

    def test_with_no_column_at_all_the_refill_is_the_old_arithmetic(self):
        spec, tables = board(player_ap=12.0, enemy_ap=12.0, spell_ap=10.0)
        self.assertNotIn("spell_ap_delta", tables)
        drained = step(initial_state(spec, tables, CFG), act(spell=0, target=0), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(drained["ap"])[0, 0]), 12.0, places=5)


class ParityTests(unittest.TestCase):
    def test_a_plain_exchange_is_unchanged_by_the_new_branches(self):
        """Every corpus and checkpoint predating these columns must see the same fight: the
        absent-column trajectory and the explicit-zero trajectory have to agree field by field."""
        spec, tables = board(player_hp=300.0, spell_damage=25.0)
        without = step(initial_state(spec, tables, CFG), act(spell=0, target=0), tables, CFG)
        zed = dict(tables)
        for key, value in (("spell_ap_delta", 0.0), ("spell_mp_delta", 0.0),
                           ("spell_push", 0.0), ("spell_duration", 0.0)):
            zed[key] = XP.asarray(np.zeros((1, 2, CFG.spells), np.float32))
        zed["spell_kill"] = XP.asarray(np.zeros((1, 2, CFG.spells)).astype(bool))
        with_zero = step(initial_state(spec, zed, CFG), act(spell=0, target=0), zed, CFG)
        for key in ("hp", "ap", "mp", "dist", "alive"):
            np.testing.assert_allclose(np.asarray(without[key]), np.asarray(with_zero[key]),
                                       err_msg="%s moved when the columns were added as zeros" % key)
        self.assertAlmostEqual(float(np.asarray(without["hp"])[0, 0]), 275.0, places=5)


def with_summons(board_pair, bodies=4.0, hp_each=200.0, dmg_each=0.0,
                 actor=1, slot=0, ally=True):
    """Give one actor a summon in one slot, the other actor nothing.

    `board()` deliberately shares one kit between both actors, which is harmless for a mechanic
    only the acting side reads and fatal for a pool: a player with a swarm of its own would
    intercept the very strike the test is trying to measure.
    """
    spec, tables = board_pair
    out = dict(tables)

    def col(value):
        arr = np.zeros((1, CFG.n_actors, CFG.spells), np.float32)
        arr[0, actor, slot] = value
        return XP.asarray(arr)

    out["spell_summon_bodies"] = col(bodies)
    out["spell_summon_hp"] = col(hp_each)
    out["spell_summon_damage"] = col(dmg_each)
    mask = np.asarray(tables["spell_ally"]).copy()
    mask[0, actor, slot] = ally
    out["spell_ally"] = XP.asarray(mask)
    return spec, out


class SummonTests(unittest.TestCase):
    """A summon has to put bodies on the board, or the 34% of boss levels that summon describe
    a mechanic the fight cannot express -- which is the same failure the kill column had before
    `spell_kill` existed, and the reason the ablation of every enemy mechanic read 0 of 1,024.

    The model is one mean-field pool per team, so the assertions are about what a pool does to a
    fight: it intercepts damage aimed at the boss behind it, it strikes back for its own damage
    spread over whoever is standing, it dies, and its dying changes who the player can hit.
    """

    def hp_of(self, state, index):
        return float(np.asarray(state["hp"])[0, index])

    def test_the_bodies_take_the_hit_that_was_meant_for_the_boss(self):
        spec, tables = with_summons(
            board(player_hp=500.0, enemy_hp=500.0, spell_damage=100.0, spell_ap=6.0),
            bodies=4.0, hp_each=50.0)                   # 200 HP of swarm
        summoned = step(initial_state(spec, tables, CFG), act(spell=0, target=1), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(summoned["pool_hp"])[0, 1]), 200.0, places=4)
        hit_once = step(summoned, act(spell=0, target=1), tables, CFG)   # player: 2 casts of 100
        self.assertEqual(self.hp_of(hit_once, 1), 500.0,
                         "200 damage went straight through 200 HP of summoned bodies")
        self.assertAlmostEqual(float(np.asarray(hit_once["pool_hp"])[0, 1]), 0.0, places=4)
        passed = step(hit_once, act(spell=1, target=1), tables, CFG)
        hit_twice = step(passed, act(spell=0, target=1), tables, CFG)
        self.assertEqual(self.hp_of(hit_twice, 1), 300.0,
                         "the boss was still shielded after its swarm was dead")

    def test_the_same_casts_with_no_bodies_damage_the_boss_immediately(self):
        # The control for the arm above, and the mutation a formula ignoring `bodies` would
        # survive: same board, same casts, the summon columns zeroed instead of absent.
        spec, tables = with_summons(
            board(player_hp=500.0, enemy_hp=500.0, spell_damage=100.0, spell_ap=6.0),
            bodies=0.0, hp_each=50.0)
        summoned = step(initial_state(spec, tables, CFG), act(spell=0, target=1), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(summoned["pool_hp"])[0, 1]), 0.0, places=4)
        hit_once = step(summoned, act(spell=0, target=1), tables, CFG)
        self.assertEqual(self.hp_of(hit_once, 1), 300.0)

    def test_the_swarm_strikes_back_for_its_own_damage_line(self):
        spec, tables = with_summons(
            board(player_hp=500.0, enemy_hp=500.0, spell_damage=0.0),
            bodies=4.0, hp_each=100.0, dmg_each=40.0)   # 400 HP, 160 damage
        summoned = step(initial_state(spec, tables, CFG), act(spell=0, target=1), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(summoned["pool_hp"])[0, 1]), 400.0, places=4)
        self.assertEqual(self.hp_of(summoned, 0), 340.0,
                         "160 damage from 4 bodies of 40 did not reach the player")

    def test_a_swarm_with_no_attack_of_its_own_leaves_the_player_unhurt(self):
        # 141 of the 353 summoned species are class companions whose published kits are sentinel
        # ids, so a body with HP and no damage line is a real row in this corpus, not a gap: it
        # must shield and must not hit.
        spec, tables = with_summons(
            board(player_hp=500.0, enemy_hp=500.0, spell_damage=0.0),
            bodies=4.0, hp_each=100.0, dmg_each=0.0)
        summoned = step(initial_state(spec, tables, CFG), act(spell=0, target=1), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(summoned["pool_hp"])[0, 1]), 400.0, places=4)
        self.assertEqual(self.hp_of(summoned, 0), 500.0)

    def test_destroying_half_the_swarm_halves_what_it_deals(self):
        spec, tables = with_summons(
            board(player_hp=500.0, enemy_hp=500.0, spell_damage=100.0, spell_ap=6.0),
            bodies=4.0, hp_each=100.0, dmg_each=40.0)   # 400 HP, 160 damage per turn
        summoned = step(initial_state(spec, tables, CFG), act(spell=0, target=1), tables, CFG)
        self.assertEqual(self.hp_of(summoned, 0), 340.0)          # 500 - 160
        halved = step(summoned, act(spell=0, target=1), tables, CFG)   # 200 absorbed: half gone
        self.assertAlmostEqual(float(np.asarray(halved["pool_hp"])[0, 1]), 200.0, places=4)
        self.assertAlmostEqual(float(np.asarray(halved["pool_dmg"])[0, 1]), 80.0, places=4)
        restrike = step(halved, act(spell=1, target=1), tables, CFG)
        self.assertEqual(self.hp_of(restrike, 0), 260.0,
                         "a half-destroyed swarm still dealt its full damage")

    def test_a_summon_needs_no_enemy_in_range_to_be_cast(self):
        # A summon fills a cell beside the caster. The mask has only two target grammars -- "an
        # enemy in range" and "a friendly actor" -- so the row is typed friendly, which is the one
        # that ignores range. Typed hostile, a 0-0 range summon would be uncastable: typed, priced,
        # and never applied.
        spec, tables = with_summons(
            board(player_hp=500.0, enemy_hp=500.0, player_dist=12.0, enemy_dist=12.0),
            bodies=4.0, hp_each=100.0, ally=True)
        for key in ("spell_rmin", "spell_rmax"):
            arr = np.asarray(tables[key]).copy()
            arr[0, :, 0] = 0.0
            tables[key] = XP.asarray(arr)
        enemy = action_masks(initial_state(spec, tables, CFG), tables, CFG)
        self.assertTrue(bool(np.asarray(enemy["spell"])[0, 1, 0]),
                        "a zero-range summon was not offered to its own caster")
        summoned = step(initial_state(spec, tables, CFG), act(spell=0, target=1), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(summoned["pool_hp"])[0, 1]), 400.0, places=4)

    def test_a_hostile_summon_row_would_not_be_castable_from_far(self):
        # The other half of the arm above: the same board with the row typed hostile has no legal
        # cast at 12 cells, so the 400 HP of bodies never arrive. Without this pair the first test
        # only proves that some cast happened.
        spec, tables = with_summons(
            board(player_hp=500.0, enemy_hp=500.0, player_dist=12.0, enemy_dist=12.0),
            bodies=4.0, hp_each=100.0, ally=False)
        for key in ("spell_rmin", "spell_rmax"):
            arr = np.asarray(tables[key]).copy()
            arr[0, :, 0] = 0.0
            tables[key] = XP.asarray(arr)
        state = initial_state(spec, tables, CFG)
        self.assertFalse(bool(np.asarray(action_masks(state, tables, CFG)["spell"])[0, 1, 0]))
        summoned = step(state, act(spell=0, target=1), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(summoned["pool_hp"])[0, 1]), 0.0, places=4)

    def test_the_pool_never_outbulksthe_team_that_summoned_it(self):
        # 8 bodies of 200 would be 1,600 HP in front of a 500 HP boss, which is not a summon spell
        # but a second fight. The cap is that team's own total HP, and it is the number `observe`
        # divides by, so a saturated pool reads as exactly 1.0.
        spec, tables = with_summons(
            board(player_hp=500.0, enemy_hp=500.0, spell_damage=0.0),
            bodies=8.0, hp_each=200.0)
        summoned = step(initial_state(spec, tables, CFG), act(spell=0, target=1), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(summoned["pool_hp"])[0, 1]), 500.0, places=4)
        _obs, glob, _mask = observe(summoned, tables, CFG)
        self.assertAlmostEqual(float(np.asarray(glob)[0, 10]), 1.0, places=5)

    def test_the_pool_cap_is_a_batch_shape_and_not_a_lucky_one(self):
        # A per-row gather used as the second argument of the cap made `minimum` see [B,2] against
        # [B]. That broadcasts when B is 1 or 2 -- every fixture in this file -- and raises at B=64,
        # which is how the defect reached the corpus runner instead of the suite.
        spec, tables = with_summons(
            board(player_hp=500.0, enemy_hp=500.0, spell_damage=0.0),
            bodies=8.0, hp_each=200.0)
        batch = 4
        spec = {k: XP.asarray(np.repeat(np.asarray(v), batch, axis=0))
                for k, v in spec.items()}
        tables = {k: XP.asarray(np.repeat(np.asarray(v), batch, axis=0))
                  for k, v in tables.items()}
        summoned = step(initial_state(spec, tables, CFG),
                        {k: XP.asarray(np.repeat(np.asarray(v), batch, axis=0))
                         for k, v in act(spell=0, target=1).items()}, tables, CFG)
        pool = np.asarray(summoned["pool_hp"])
        self.assertEqual(pool.shape, (batch, 2))
        np.testing.assert_allclose(pool[:, 1], 500.0, rtol=1e-5)

    def test_a_corpus_without_the_columns_plays_the_old_fight_exactly(self):
        spec, tables = board(player_hp=500.0, enemy_hp=500.0, spell_damage=100.0, spell_ap=6.0)
        self.assertNotIn("spell_summon_bodies", tables)
        # the enemy acts first on this board, so one step is its one 6-AP cast of 100 on the player
        without = step(initial_state(spec, tables, CFG), act(spell=0, target=0), tables, CFG)
        zed_spec, zed = with_summons((spec, tables), bodies=0.0, hp_each=0.0, dmg_each=0.0,
                                     ally=False)
        with_zero = step(initial_state(zed_spec, zed, CFG), act(spell=0, target=0), zed, CFG)
        for key in ("hp", "ap", "mp", "dist", "alive"):
            np.testing.assert_allclose(np.asarray(without[key]), np.asarray(with_zero[key]),
                                       err_msg="%s moved when the summon columns were added" % key)
        self.assertEqual(self.hp_of(with_zero, 0), 400.0)
        self.assertNotIn("pool_hp", without)
        self.assertIn("pool_hp", with_zero)


class ObservationTests(unittest.TestCase):
    """The head can only route on a mechanic it is told about, so the token is a second contract.

    The width is what decides which architecture can read a corpus: a 49-number actor vector is the
    shipped checkpoint's input, 41 here (2 spell slots instead of 4) is the same board with the
    mechanic block appended. The order is pinned too -- `tools/wsp_program_target.py` solves a
    relational matrix inside this token, and a silent reorder makes the solved matrix describe a
    different spell than the one it is installed onto.
    """

    def test_no_columns_keeps_the_nine_number_spell_token(self):
        spec, tables = board(spell_damage=25.0)
        obs, _glob, _mask = observe(initial_state(spec, tables, CFG), tables, CFG)
        self.assertEqual(int(np.asarray(obs).shape[-1]), 13 + CFG.spells * 9)

    def test_the_mechanic_block_is_appended_in_the_published_order(self):
        spec, tables = board(spell_damage=25.0, kill=True, push=2.0, ap_delta=-3.0,
                             mp_delta=-1.0, duration=2.0)
        obs, _glob, _mask = observe(initial_state(spec, tables, CFG), tables, CFG)
        arr = np.asarray(obs)
        self.assertEqual(arr.shape[-1], 13 + CFG.spells * 14)
        want = [1.0, 2.0 / 12.0, -3.0 / 6.0, -1.0 / 4.0, 2.0 / 5.0]
        for actor in range(2):
            # slot 0 is the nine shipped numbers then the five mechanic ones; slot 1 follows
            np.testing.assert_allclose(arr[0, actor, 13 + 9:13 + 14], want, rtol=1e-6,
                                       err_msg="spell slot 0 mechanic block, actor %d" % actor)
            # the free pass column has no mechanic, and must not borrow the neighbour's
            np.testing.assert_allclose(arr[0, actor, 13 + 9 + 14:13 + 14 + 14],
                                       [0.0] * 5, atol=1e-7)

    def test_the_summon_block_follows_the_mechanic_block(self):
        # Width is the contract a checkpoint is loaded against, and the order is the contract the
        # solved relational matrix is written against; both are pinned here rather than by luck.
        spec, tables = with_summons(
            board(player_hp=500.0, enemy_hp=500.0, spell_damage=25.0, kill=True, push=2.0,
                  ap_delta=-3.0, mp_delta=-1.0, duration=2.0),
            bodies=4.0, hp_each=250.0, dmg_each=60.0)
        state = initial_state(spec, tables, CFG)
        obs, glob, _mask = observe(state, tables, CFG)
        self.assertEqual(np.asarray(obs).shape[-1], 13 + CFG.spells * 17)
        want = [4.0 / 8.0, np.log1p(250.0) / np.log(3001.0),
                np.log1p(60.0) / np.log(301.0)]
        np.testing.assert_allclose(np.asarray(obs)[0, 1, 13 + 14:13 + 17], want, rtol=1e-6)
        np.testing.assert_allclose(np.asarray(obs)[0, 1, 13 + 17:13 + 34], [0.0] * 17, atol=1e-7)
        self.assertEqual(np.asarray(glob).shape[-1], 13)
        np.testing.assert_allclose(np.asarray(glob)[0, 9:], [0.0] * 4, atol=1e-7)
        struck = step(state, act(spell=0, target=1), tables, CFG)
        _obs2, glob2, _m2 = observe(struck, tables, CFG)
        # the enemy's own total HP is the ceiling `step` caps its swarm with, so the pool reads as
        # a share of it; asserted against the state rather than a literal to keep the pin on the
        # feature, not on this board's arithmetic
        self.assertAlmostEqual(float(np.asarray(glob2)[0, 10]),
                               float(np.asarray(struck["pool_hp"])[0, 1]) / 500.0, places=5)
        self.assertGreater(float(np.asarray(glob2)[0, 10]), 0.0)


class BlindingTests(unittest.TestCase):
    """`observe(..., blind=...)` is how the mechanic input gets ablated, so it has to be surgical.

    The alternative -- zeroing the table columns in the corpus -- is not an input ablation: `step`
    spends those columns and `heuristic.py` prices an instant kill at 1e6, so a "blind" corpus is a
    different world. Measured 2026-11-06: the same 64 boards under the scripted line produced
    different winners once the eight columns were cleared. Blinding therefore happens in the
    observation only, and these arms are what keep it there.
    """

    def _wide(self):
        spec, tables = with_summons(
            board(player_hp=500.0, enemy_hp=500.0, spell_damage=25.0, kill=True, push=2.0,
                  ap_delta=-3.0, mp_delta=-1.0, duration=2.0),
            bodies=4.0, hp_each=100.0, dmg_each=40.0)
        return spec, tables

    def test_blinding_keeps_the_width_and_empties_only_the_named_blocks(self):
        spec, tables = self._wide()
        state = initial_state(spec, tables, CFG)
        obs, glob, _m = observe(state, tables, CFG)
        b_obs, b_glob, _m2 = observe(state, tables, CFG,
                                     blind=("mechanics", "summons", "pool"))
        self.assertEqual(obs.shape, b_obs.shape)
        self.assertEqual(glob.shape, b_glob.shape)
        self.assertGreater(float(np.abs(np.asarray(obs)[0, 1, 13 + 9:13 + 14]).max()), 0.0,
                           "the fixture has no mechanic signal to blind")
        np.testing.assert_allclose(np.asarray(b_obs)[0, 1, 13 + 9:13 + 17], [0.0] * 8, atol=1e-7)
        np.testing.assert_allclose(np.asarray(b_glob)[0, 9:], [0.0] * 4, atol=1e-7)
        # the shipped nine numbers per spell and the first nine globals are the same arrays; the
        # token is [B, N, F] -- spell blocks are flattened into F, not left as a fourth axis
        np.testing.assert_array_equal(np.asarray(obs)[0, :, :13 + 9],
                                      np.asarray(b_obs)[0, :, :13 + 9])
        np.testing.assert_array_equal(np.asarray(glob)[0, :9], np.asarray(b_glob)[0, :9])

    def test_a_swarm_visible_at_the_start_becomes_visible_after_a_cast(self):
        # The pool globals are state, not tables: nothing is emitted at all unless the corpus carries
        # the columns, and the arm above would pass trivially on a corpus that does not.
        spec, tables = self._wide()
        state = initial_state(spec, tables, CFG)
        _o, glob0, _m = observe(state, tables, CFG)
        self.assertEqual(np.asarray(glob0).shape[-1], 13)
        struck = step(state, act(spell=0, target=1), tables, CFG)
        _o2, glob1, _m2 = observe(struck, tables, CFG)
        self.assertGreater(float(np.asarray(glob1)[0, 10]), 0.0,
                           "the swarm never reached the observation")
        _o3, glob3, _m3 = observe(struck, tables, CFG, blind=("pool",))
        self.assertEqual(float(np.asarray(glob3)[0, 10]), 0.0)

    def test_blinding_does_not_touch_the_tables_or_the_fight(self):
        spec, tables = self._wide()
        before = {k: np.asarray(v).copy() for k, v in tables.items()}
        state = initial_state(spec, tables, CFG)
        observe(state, tables, CFG, blind=("mechanics", "summons", "pool"))
        for k, v in tables.items():
            np.testing.assert_array_equal(np.asarray(v), before[k],
                                         err_msg="observe(blind=...) mutated %s" % k)
        played = step(state, act(spell=0, target=1), tables, CFG)
        self.assertAlmostEqual(float(np.asarray(played["pool_hp"])[0, 1]), 400.0, places=4,
                              msg="the blinded observation changed what step spent")


class LoaderTests(unittest.TestCase):
    """A corpus the loader cannot see is a fight the policy is never told about.

    `train.py` and `encounter/data.py` iterated a fixed seven-name table list, so `--data
    encounters_boss_mech_*` loaded 7 of 12 spell tables and trained on a world where no spell
    pushes -- silently, with a loss curve that looked fine. The witnesses below are the two halves:
    the columns arrive when the file has them, and a file missing a base column fails loudly rather
    than training on a different observation than its checkpoint.
    """

    def _write(self, path, keys):
        slots, actors = CFG.spells, CFG.n_actors
        arrays = {k: np.zeros((3, actors, slots), np.float32) for k in keys
                  if k.startswith("spell_")}
        arrays.update({
            "hp": np.full((3, actors), 100.0, np.float32),
            "max_hp": np.full((3, actors), 100.0, np.float32),
            "ap_max": np.full((3, actors), 12.0, np.float32),
            "mp_max": np.full((3, actors), 6.0, np.float32),
            "power": np.zeros((3, actors), np.float32),
            "res": np.zeros((3, actors, 5), np.float32),
            "dist": np.full((3, actors), 3.0, np.float32),
            "initiative": np.zeros((3, actors), np.float32),
            "alive": np.ones((3, actors), bool),
            "first_team": np.ones((3,), np.int32)})
        np.savez(path, **arrays)

    def test_the_mechanic_columns_survive_the_loader(self):
        import pathlib
        import tempfile
        from combat.encounter.train import TABLE_KEYS, load_encounters, table_keys
        base = list(TABLE_KEYS)
        with tempfile.TemporaryDirectory() as d:
            plain, mech = d + "/plain.npz", d + "/mech.npz"
            swarmed = d + "/swarm.npz"
            self._write(plain, base)
            self._write(mech, base + ["spell_" + k for k in
                                      ("kill", "push", "ap_delta", "mp_delta", "duration")])
            self._write(swarmed, base + ["spell_" + k for k in
                                         ("kill", "push", "ap_delta", "mp_delta", "duration",
                                          "summon_bodies", "summon_hp", "summon_damage")])
            for path, want in ((plain, 7), (mech, 12), (swarmed, 15)):
                tables, _spec = load_encounters(pathlib.Path(path))
                self.assertEqual(len(tables), want, path)
            with self.assertRaises(KeyError):
                table_keys(type("V", (), {"files": ["spell_ap", "spell_rmin"]})())

    def test_the_base_list_is_the_shipped_seven(self):
        """The witness for the class: if a column is added to the fixed list instead of derived,
        `table_keys` would double it and this assertion is where the duplication is caught."""
        from combat.encounter.train import TABLE_KEYS
        self.assertEqual(len(TABLE_KEYS), 7)
        self.assertNotIn("spell_push", TABLE_KEYS)


if __name__ == "__main__":
    unittest.main()
