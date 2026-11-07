"""The pre-fight refusal rule, witnessed by boards whose answer is known.

`combat/encounter/strike.py` is the first mechanism in this line that moves the win rate by points
rather than fractions of a point (AUC 0.8071 against terminal outcomes on 2,048 boss boards, versus
the shipped value head's 0.5439), and it is shipped as a *decision*, so each quantity it reads has
to be shown to change the verdict -- not merely to exist. Every arm below is a two-line board built
so the right answer can be stated in advance, and each flips exactly one number:

  accept/refuse          a race I win by arithmetic, and its mirror
  damage                 one spell's damage moves the verdict, so the best-affordable-spell rule
                         is really being applied rather than a health ratio in disguise
  resistance             50% resistance to the element I actually cast must slow my clock; if the
                         element lookup were wrong the verdict would not move
  dead enemy             removing one enemy's output must move the incoming clock, which is the
                         only witness that `alive` is read at all
  unaffordable           a spell costing more AP than the actor has is no damage at all, and a
                         board where neither side can strike must report `unknown` rather than
                         picking a winner

The last two are the arms that would silently pass a `-> 0` mutation: a formula that ignores
`alive`, or treats an unaffordable spell as castable, still returns a plausible number.
"""
from __future__ import annotations

import unittest

import numpy as np

from combat.encounter.strike import pre_fight, round_strikes

E = 5      # resistance columns: neutral, earth, fire, water, air
S = 2      # spell slots


def board(player_ap, player_dmg, player_el, player_hp, enemy_hp, enemy_ap=3.0,
          enemy_dmg=30.0, enemy_alive=True, res_enemy=0, res_player=0):
    """[1 ally, 1 enemy] as the corpus stores it: one row per actor, one column per slot."""
    spec = {
        "hp": np.array([[player_hp, enemy_hp]]),
        "ap_max": np.array([[player_ap, enemy_ap]]),
        "alive": np.array([[True, enemy_alive]]),
        "res": np.zeros((1, 2, E)) + np.array([[res_player, res_enemy]])[:, :, None],
    }
    tables = {
        "spell_ap": np.array([[[3.0, 3.0], [enemy_ap, enemy_ap]]]),
        "spell_damage": np.array([[[player_dmg, 0.0], [enemy_dmg, 0.0]]]),
        "spell_element": np.array([[[player_el, 0], [0, 0]]]),
    }
    return spec, tables


class StrikeTests(unittest.TestCase):
    def test_i_outrace_them_and_the_rule_accepts(self):
        # 6 AP buys two casts of 30 = 60/round against 100 enemy hp -> 1.67 rounds.
        # Their 3 AP buys one cast of 30 = 30/round against my 200 hp -> 6.67 rounds.
        s = round_strikes(*board(6.0, 30.0, 1, 200.0, 100.0))
        self.assertAlmostEqual(float(s["player_damage_per_round"][0]), 60.0, places=6)
        self.assertAlmostEqual(float(s["rounds_to_kill"][0]), 100.0 / 60.0, places=6)
        self.assertAlmostEqual(float(s["rounds_to_die"][0]), 200.0 / 30.0, places=6)
        self.assertEqual(pre_fight(*board(6.0, 30.0, 1, 200.0, 100.0))[0][0], "accept")

    def test_the_mirror_board_refuses(self):
        s = pre_fight(*board(3.0, 20.0, 1, 100.0, 100.0, enemy_ap=3.0, enemy_dmg=50.0))[0][0]
        self.assertEqual(s, "refuse", "a race I lose by 5x still got accepted")

    def test_one_damage_number_flips_the_verdict(self):
        # Same board, one spell changed: ttk 5.0 -> 1.67 rounds-to-kill crosses ttd of 2.0.
        refuse = pre_fight(*board(6.0, 20.0, 1, 100.0, 100.0, enemy_dmg=50.0))[0][0]
        accept = pre_fight(*board(6.0, 60.0, 1, 100.0, 100.0, enemy_dmg=50.0))[0][0]
        self.assertEqual((refuse, accept), ("refuse", "accept"),
                         "damage output does not reach the verdict")

    def test_resistance_slows_my_clock_and_moves_the_verdict(self):
        # 6 AP buys two casts of 30 fire damage: 60/round, a 1.67-round kill against their 2.86.
        # Half-resisting the element I actually cast halves that to 30/round, so the same fight
        # takes 3.33 rounds -- longer than I survive, and the verdict has to change.
        free, s_res = (round_strikes(*board(6.0, 30.0, 2, 100.0, 100.0, enemy_dmg=35.0)),
                       round_strikes(*board(6.0, 30.0, 2, 100.0, 100.0, enemy_dmg=35.0,
                                            res_enemy=50)))
        self.assertAlmostEqual(float(free["player_damage_per_round"][0]), 60.0, places=6)
        self.assertAlmostEqual(float(s_res["player_damage_per_round"][0]), 30.0, places=6,
                               msg="the 50% resistance on the element I cast was not applied")
        self.assertEqual(pre_fight(*board(6.0, 30.0, 2, 100.0, 100.0, enemy_dmg=35.0))[0][0],
                         "accept")
        self.assertEqual(pre_fight(*board(6.0, 30.0, 2, 100.0, 100.0, enemy_dmg=35.0,
                                          res_enemy=50))[0][0], "refuse",
                         "halving my output changed nothing: the element lookup is wrong")

    def test_a_dead_enemy_stops_contributing(self):
        # Non-vacuity for `alive`: with the enemy down there is no incoming damage, so the race is
        # won on the numbers, and a formula that ignored `alive` would keep counting its 50/round.
        hurt = round_strikes(*board(6.0, 30.0, 1, 100.0, 100.0, enemy_dmg=50.0))
        dead = round_strikes(*board(6.0, 30.0, 1, 100.0, 100.0, enemy_dmg=50.0,
                                    enemy_alive=False))
        self.assertAlmostEqual(float(hurt["incoming_damage_per_round"][0]), 50.0, places=6)
        self.assertAlmostEqual(float(dead["incoming_damage_per_round"][0]), 0.0, places=6)
        self.assertEqual(float(dead["rounds_to_die"][0]), 1e9)

    def test_a_spell_the_actor_cannot_pay_for_is_no_damage(self):
        # 6 AP cannot buy a 7-AP spell; neither side deals damage, so the board must say "unknown"
        # rather than award the fight to whoever the formula happened to divide by first.
        spec, tables = board(6.0, 40.0, 1, 100.0, 100.0)
        tables["spell_ap"] = np.array([[[7.0, 7.0], [7.0, 7.0]]])
        s = round_strikes(spec, tables)
        self.assertEqual(float(s["player_damage_per_round"][0]), 0.0)
        self.assertEqual(float(s["incoming_damage_per_round"][0]), 0.0)
        self.assertEqual(pre_fight(spec, tables)[0][0], "unknown")

    def test_partial_payment_counts_whole_casts_only(self):
        # 8 AP with a 3-AP spell is two casts (6 AP), not 2.67 -- the turn does not spend AP it does
        # not have, and a formula that rounded up would overstate every low-cost build.
        s = round_strikes(*board(8.0, 10.0, 1, 100.0, 100.0))
        self.assertAlmostEqual(float(s["player_damage_per_round"][0]), 20.0, places=6)


class VerdictWiringTests(unittest.TestCase):
    """`preflight._verdict` now keys on the shipped race. These arms exist because the previous
    gate was `hp_over_opening_damage < 3.0`, a constant with no measured operating point, and a
    rewrite that quietly kept the old behaviour would still print a plausible verdict."""

    @staticmethod
    def line(**over):
        base = {"verified_outcome": "win", "survives_opening": True,
                "opening_damage_taken": 0.0, "player_hp": 780.0,
                "hp_over_opening_damage": 40.0, "turns_to_die": 5.0, "turns_to_kill_enemy": 2.0,
                "strike_verdict": "accept", "strike_rounds_to_kill": 1.67,
                "strike_rounds_to_die": 6.0}
        base.update(over)
        return base

    def test_losing_the_race_is_not_ready(self):
        from combat.preflight import Preflight

        verdict, reasons = Preflight._verdict(None, self.line(strike_verdict="refuse",
                                                              strike_rounds_to_kill=9.0,
                                                              strike_rounds_to_die=2.0))
        self.assertEqual(verdict, "not_ready")
        self.assertIn("9.0", reasons[-1])

    def test_winning_the_race_is_favourable(self):
        from combat.preflight import Preflight

        verdict, _ = Preflight._verdict(None, self.line())
        self.assertEqual(verdict, "favourable")

    def test_a_race_with_no_clock_is_not_called_favourable(self):
        # `unknown` means neither side can pay for damage, so there is no clock to compare. Falling
        # through to "favourable" would be the planner recommending a fight it cannot win.
        from combat.preflight import Preflight

        verdict, reasons = Preflight._verdict(None, self.line(strike_verdict="unknown",
                                                             strike_rounds_to_kill=1e9,
                                                             strike_rounds_to_die=1e9))
        self.assertEqual(verdict, "not_ready")
        self.assertIn("cannot be won by the arithmetic", reasons[-1])

    def test_the_ambush_still_refuses_however_good_the_race_is(self):
        # Ordering witness: being killed before acting outranks a favourable damage race, and the
        # new branch must not be able to reach it -- which is why "refuse" is asserted separately.
        from combat.preflight import Preflight

        line = self.line(survives_opening=False, opening_damage_taken=780.0)
        verdict, reasons = Preflight._verdict(None, line)
        self.assertEqual(verdict, "refuse")
        self.assertIn("no decision gets played", reasons[-1])


class PreflightRolloutTests(unittest.TestCase):
    def fixture(self, first):
        from combat.encounter.backend import XP
        from combat.preflight import Preflight
        from combat.test_env_math import CFG, fixture

        spec, tables = fixture()
        spec["first_team"] = XP.asarray([first], dtype="int32")
        tables["spell_ap"] = XP.asarray([[[5., 20., 0.], [6., 0., 0.], [6., 0., 0.]]])
        tables["spell_damage"] = XP.asarray([[[10., 50., 0.], [7., 0., 0.], [7., 0., 0.]]])
        tables["spell_rmax"] = XP.full((1, 3, 3), 12.0)
        planner = Preflight.__new__(Preflight)
        planner.cfg = CFG
        calls = []

        def policy(state):
            self.assertEqual(int(np.asarray(state["acting_team"])[0]), 0)
            calls.append(0)
            return {"move": XP.asarray([[1, 1, 1]]),
                    "spell": XP.asarray([[0, 0, 0]]),
                    "target": XP.asarray([[1, 0, 0]])}

        def scripted(state):
            self.assertEqual(int(np.asarray(state["acting_team"])[0]), 1)
            calls.append(1)
            return {"move": XP.asarray([[1, 1, 1]]),
                    "spell": XP.asarray([[0, 0, 0]]),
                    "target": XP.asarray([[1, 0, 0]])}

        planner._policy = lambda state, tables: policy(state)
        planner._scripted = lambda state, tables: scripted(state)
        return planner, spec, tables, calls

    def test_play_routes_both_opening_orders_to_the_correct_policy(self):
        for first in (0, 1):
            with self.subTest(first=first):
                planner, spec, tables, calls = self.fixture(first)
                result = planner.play(spec, tables)
                self.assertGreater(len(calls), 1)
                self.assertEqual(calls, [(first + i) % 2 for i in range(len(calls))])
                self.assertEqual(result["turns"], len(calls))
                self.assertIn(result["outcome"], ("win", "loss", "draw"))

    def test_terminal_opening_counts_as_one_team_turn(self):
        from combat.encounter.backend import XP

        planner, spec, tables, calls = self.fixture(1)
        spec["hp"] = XP.asarray([[5., 60., 60.]])
        result = planner.play(spec, tables)
        self.assertEqual(result["outcome"], "loss")
        self.assertEqual(result["turns"], 1)
        self.assertEqual(calls, [1])

    def test_verified_draw_is_not_favourable(self):
        from combat.preflight import Preflight

        verdict, _ = Preflight._verdict(None, VerdictWiringTests.line(verified_outcome="draw"))
        self.assertEqual(verdict, "not_ready")

    def test_estimates_initialize_each_side_independently(self):
        for first in (0, 1):
            with self.subTest(first=first):
                planner, spec, tables, calls = self.fixture(first)
                hp = np.asarray(spec["hp"]).copy()
                result = planner.estimate(spec, tables, player_opens=first == 0)
                self.assertEqual(calls, [1, 0])
                self.assertEqual(result["monster_turn_damage"], 14.0)
                self.assertEqual(result["player_turn_damage"], 20.0)
                self.assertEqual(result["opening_damage_taken"], 14.0 if first else 0.0)
                np.testing.assert_array_equal(spec["hp"], hp)
                self.assertEqual(int(np.asarray(spec["first_team"])[0]), first)

    def test_verified_loss_overrides_an_optimistic_race(self):
        from combat.preflight import Preflight

        line = VerdictWiringTests.line(verified_outcome="loss")
        verdict, reasons = Preflight._verdict(None, line)
        self.assertEqual(verdict, "not_ready")
        self.assertIn("loss", reasons[0])

    def test_readiness_uses_the_losing_case_even_with_more_hp_margin(self):
        from combat.preflight import Preflight

        planner = Preflight.__new__(Preflight)
        planner.board = lambda *args, **kwargs: (kwargs["player_opens"], None)
        planner.estimate = lambda spec, tables, player_opens: VerdictWiringTests.line(
            player_opens=player_opens, hp_over_opening_damage=50.0 if player_opens else 4.0)
        planner.play = lambda spec, tables: {
            "outcome": "loss" if spec else "win", "rounds": 3, "turns": 7,
            "player_hp_left": 0.0}
        result = planner.readiness([1], 30)
        self.assertEqual(result["verdict"], "not_ready")
        self.assertEqual(result["estimate_and_verification"]["verified_outcome"], "loss")


if __name__ == "__main__":
    unittest.main()
