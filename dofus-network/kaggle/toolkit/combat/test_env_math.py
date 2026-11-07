"""The encounter env's own arithmetic, witnessed by stepping it.

Measured 2026-11-05: a 595-arm re-sweep reported "0 failing checks" for two mutations
inside `combat/encounter/env.py` -- the cast scatter `hp = at(hp)[rows, tgt].add(mend -
hit)` changed to `.set(...)`, and `spell_mask ... .set(can_cast)` changed to
`.set(True)`. Nothing in the tree steps the env: the trained path is scored from cached
activations, the duel simulator and the battle solver carry their own arithmetic, and
`plan_benchmark` reads masks it never contradicts. Both arms are therefore invisible to
every check, and both are real: overwriting the scatter makes a second cast in the same
turn replace the first instead of adding to it, and a mask that declares every slot
castable hands the policy a spell the actor cannot pay for.

The fixture is deliberately minimal (one ally, two monsters, two spell slots plus pass)
and each case pins the number the mutation moves: 2 casts of 10 damage on a 60-hp
monster leaves 40, and the 20-AP slot is False while the 5-AP slot is True.
"""

from __future__ import annotations

import unittest

import numpy as np

from combat.encounter.backend import XP
from combat.encounter.env import Config, action_masks, initial_state, step

CFG = Config(allies=1, enemies=2, spells=2, max_rounds=16, max_dist=12,
             casts_per_turn=4)

# slot 0: 5 AP, 10 damage, reaches the monsters. slot 1: 20 AP -- more than the actor
# has, so it is unaffordable at the start of the turn and stays unaffordable.
SLOT_AP = [[5.0, 20.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
SLOT_RMIN = [[1.0, 1.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
SLOT_RMAX = [[12.0, 12.0, 12.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
SLOT_DAMAGE = [[10.0, 50.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
SLOT_HEAL = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
SLOT_ELEMENT = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
SLOT_ALLY = [[False, False, False], [False, False, False], [False, False, False]]


# XP, not np: the env's scatters go through `at(array)`, which is jax's `array.at` on the
# training box and the shim's own update proxy under DOFUS_FIGHT_BACKEND=numpy. A plain
# numpy array here fails on the first cast regardless of what the env does.
def _a(table, dtype="float32"):
    return XP.asarray([table], dtype=dtype)  # [1, N, S]


def _b(row, dtype="float32"):
    return XP.asarray([row], dtype=dtype)    # [1, N]


def fixture():
    spec = {
        "hp": _b([100.0, 60.0, 60.0]),
        "max_hp": _b([100.0, 100.0, 100.0]),
        "ap_max": _b([12.0, 6.0, 6.0]),
        "mp_max": _b([6.0, 3.0, 3.0]),
        "power": _b([0.0, 0.0, 0.0]),
        "res": _a([[0.0], [0.0], [0.0]]),   # [1, N, E]
        "dist": _b([5.0, 5.0, 5.0]),
        "initiative": _b([0, 1, 2], "int32"),
        "alive": _b([1.0, 1.0, 1.0]),
        "first_team": XP.asarray([0], dtype="int32"),
    }
    tables = {
        "spell_ap": _a(SLOT_AP),
        "spell_rmin": _a(SLOT_RMIN),
        "spell_rmax": _a(SLOT_RMAX),
        "spell_damage": _a(SLOT_DAMAGE),
        "spell_heal": _a(SLOT_HEAL),
        "spell_element": _a(SLOT_ELEMENT, "int32"),
        "spell_ally": _a(SLOT_ALLY, "bool"),
    }
    return spec, tables


def actions(spell, target, move=1):
    n = CFG.n_actors
    return {
        "move": XP.asarray([[move] + [1] * (n - 1)], dtype="int32"),
        "spell": XP.asarray([[spell] + [0] * (n - 1)], dtype="int32"),
        "target": XP.asarray([[target] + [0] * (n - 1)], dtype="int32"),
    }


def with_range(tables, slot, rmin, rmax):
    """Rewrite the ally's spell range for one slot, leaving the rest of the fixture alone."""
    lo = np.array(SLOT_RMIN, dtype=np.float32); hi = np.array(SLOT_RMAX, dtype=np.float32)
    lo[0][slot], hi[0][slot] = rmin, rmax
    tables["spell_rmin"] = _a(lo)
    tables["spell_rmax"] = _a(hi)
    return tables


class MoveThenCastTests(unittest.TestCase):
    """The client's turn is walk-then-cast, so the mask admits reachable spells.

    Section N.11.8 measured why this matters: gating range on the distance the actor already
    stands at left 1,651 of 2,048 boss opening boards with no castable real spell at all, so on
    four boards in five the spell choice was the mask's, not the policy's. These arms pin both
    halves of the fix together -- the mask must admit what MP can reach, and `step` must spend MP
    before resolving casts and land on a legal reachable distance -- because either alone is a
    different game.
    """

    def test_a_spell_only_reachable_by_walking_is_admitted_and_lands(self):
        spec, tables = fixture()
        tables = with_range(tables, 0, 1.0, 3.0)      # dist 5, mp 6: must close the gap
        state = initial_state(spec, tables, CFG)
        self.assertTrue(bool(np.asarray(action_masks(state, tables, CFG)["spell"])[0, 0, 0]),
                        "the mask refused a spell the actor could walk into range")
        after = step(state, actions(0, 1, move=0), tables, CFG)
        hp = np.asarray(after["hp"])[0]
        self.assertEqual(hp[1], 40.0, str(hp))
        self.assertEqual(float(np.asarray(after["dist"])[0, 0]), 1.0, str(after["dist"]))

    def test_holding_position_does_not_get_the_unreachable_cast(self):
        # The optimistic mask is not a free lunch: an actor that picks the reachable spell and
        # then holds still lands nothing. Mask legality and step legality must stay different
        # questions, or the mask would be telling the policy something the actor cannot do.
        spec, tables = fixture()
        tables = with_range(tables, 0, 1.0, 3.0)
        state = initial_state(spec, tables, CFG)
        after = step(state, actions(0, 1, move=1), tables, CFG)
        self.assertEqual(np.asarray(after["hp"])[0][1], 60.0, "a hold put the actor in range")
        self.assertEqual(float(np.asarray(after["dist"])[0, 0]), 5.0)

    def test_a_walk_out_of_a_legal_range_snaps_back_instead_of_wasting_the_cast(self):
        # 4..6 with the actor at 5: approaching with 6 MP would land it at 1, outside the range.
        # The reorder must not turn "walk" into "forfeit this spell", which is what the old
        # pre-move gate relied on, so it lands on the nearest reachable legal distance instead.
        spec, tables = fixture()
        tables = with_range(tables, 0, 4.0, 6.0)
        state = initial_state(spec, tables, CFG)
        after = step(state, actions(0, 1, move=0), tables, CFG)
        self.assertEqual(np.asarray(after["hp"])[0][1], 40.0, str(after["hp"]))
        self.assertEqual(float(np.asarray(after["dist"])[0, 0]), 4.0, str(after["dist"]))

    def test_a_spell_beyond_every_reachable_distance_refuses_and_lands_nothing(self):
        # dist 1 with 3 MP reaches [1, 4]; a 10..12 spell is outside every landing distance,
        # so the reachability gate must refuse it and the step must land nothing when forced.
        spec, tables = fixture()
        spec["dist"] = _b([1.0, 5.0, 5.0])
        spec["mp_max"] = _b([3.0, 3.0, 3.0])
        tables = with_range(tables, 0, 10.0, 12.0)
        state = initial_state(spec, tables, CFG)
        self.assertFalse(bool(np.asarray(action_masks(state, tables, CFG)["spell"])[0, 0, 0]))
        self.assertEqual(np.asarray(step(state, actions(0, 1, move=2), tables, CFG)["hp"])[0][1],
                         60.0, "an unreachable spell was cast anyway")
        # non-vacuity: the refusal is the MP budget talking, not a broken table -- 12 MP can
        # reach 10..12 from distance 1, and then the same spell is admitted and lands.
        spec2, tables2 = fixture()
        spec2["dist"] = _b([1.0, 5.0, 5.0])
        spec2["mp_max"] = _b([12.0, 3.0, 3.0])
        tables2 = with_range(tables2, 0, 10.0, 12.0)
        state2 = initial_state(spec2, tables2, CFG)
        self.assertTrue(bool(np.asarray(action_masks(state2, tables2, CFG)["spell"])[0, 0, 0]))
        self.assertEqual(np.asarray(step(state2, actions(0, 1, move=2), tables2, CFG)["hp"])[0][1],
                         40.0)


class EnvMathTests(unittest.TestCase):
    def test_the_ally_side_is_the_one_that_acts(self):
        spec, tables = fixture()
        state = initial_state(spec, tables, CFG)
        self.assertEqual(np.asarray(state["team"]).tolist(), [[0, 1, 1]])
        self.assertEqual(np.asarray(state["ap"]).tolist(), [[12.0, 0.0, 0.0]])

    def test_repeated_casts_accumulate_on_the_target(self):
        # 12 AP and a 5-AP spell: the repeat loop fires twice (12 -> 7 -> 2) and stops.
        # Two casts of 10 damage on a 60-hp monster is 40, and a scatter that overwrites
        # instead of adding leaves 50 -- or 0 once the clipped last write is counted.
        spec, tables = fixture()
        state = initial_state(spec, tables, CFG)
        after = step(state, actions(0, 1), tables, CFG)
        hp = np.asarray(after["hp"])[0]
        self.assertEqual(hp[1], 40.0, str(hp))
        self.assertEqual(hp[2], 60.0, "a cast touched a monster nobody targeted: %s" % hp)
        self.assertEqual(hp[0], 100.0, str(hp))
        self.assertTrue(bool(np.asarray(after["alive"])[0][1]))
        # The ally spends its AP inside the turn and the monsters receive theirs at the
        # handover (env.py:235), so per-cast payment is not readable after step() -- the
        # two casts are what the hp above proves.
        self.assertEqual(np.asarray(after["ap"]).tolist(), [[0.0, 6.0, 6.0]],
                         "the turn did not hand AP to the team that acts next")
        self.assertEqual(int(np.asarray(after["acting_team"])[0]), 1)

    def test_the_mask_refuses_the_spell_the_actor_cannot_afford(self):
        spec, tables = fixture()
        state = initial_state(spec, tables, CFG)
        masks = action_masks(state, tables, CFG)
        spell = np.asarray(masks["spell"])[0].tolist()
        self.assertEqual(spell, [[True, False, True],
                                 [False, False, False],
                                 [False, False, False]], str(spell))
        # non-vacuity: the refusal has to be a decision, not an all-False column
        self.assertTrue(spell[0][0] and spell[0][2])

    def test_a_slot_the_mask_refuses_lands_nothing_when_forced(self):
        # The step re-reads AP, so an unaffordable spell picked against the mask costs
        # the turn without hurting anybody. This is the pair to the mask check above:
        # mask and legality must agree, and a mask mutation alone breaks one of them.
        spec, tables = fixture()
        state = initial_state(spec, tables, CFG)
        after = step(state, actions(1, 1), tables, CFG)
        hp = np.asarray(after["hp"])[0]
        self.assertEqual(hp[1], 60.0, str(hp))
        self.assertTrue(bool(np.asarray(after["alive"])[0][1]))

    def test_a_kill_floors_at_zero_and_leaves_the_other_monster_alone(self):
        # A dead target cannot absorb the repetitions that follow it: the loop re-reads
        # `alive` each time, so the overkill stays at the floor of 0 and the monster
        # nobody targeted is untouched. A scatter that overwrites instead of adds passes
        # this too -- it is here to bound the damage, not to prove the accumulation.
        spec, tables = fixture()
        spec["hp"] = _b([100.0, 10.0, 60.0])
        state = initial_state(spec, tables, CFG)
        after = step(state, actions(0, 1), tables, CFG)
        hp = np.asarray(after["hp"])[0]
        self.assertEqual(hp[1], 0.0, str(hp))
        self.assertEqual(hp[2], 60.0, str(hp))
        self.assertFalse(bool(np.asarray(after["alive"])[0][1]))


if __name__ == "__main__":
    unittest.main()
