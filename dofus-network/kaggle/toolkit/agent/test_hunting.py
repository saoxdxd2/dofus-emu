"""Tests for `agent.hunting`: which group to fight, and getting into it.

The decisions are pure and tested as such; `engage` is tested against a fake
executor so the ladder's rungs -- and, more importantly, the cases where it must
*not* claim success -- are pinned down without the game.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import hunting  # noqa: E402


# -- fixtures ---------------------------------------------------------------

def group(gid=-1709, total=24, members=3, cell=387, max_level=8):
    return {"contextualId": gid, "group_id": gid, "cell": cell,
            "member_count": members, "total_level": total, "max_level": max_level,
            "monster_ids": [4221] * members,
            "members": [{"monster_id": 4221, "level": max_level} for _ in range(members)]}


def snapshot(in_fight=False, level=12, in_world=True, fight_id=None, won=None,
             life=None, max_life=None):
    snap = {"in_world": in_world, "in_fight": in_fight,
            "character": {"level": level},
            "position": {"map_id": 146539520, "cell": 329}}
    if life is not None:
        snap["lifePoints"] = life
    if max_life is not None:
        snap["effectiveMaxLifePoints"] = max_life
    if fight_id is not None:
        snap["last_fight_end"] = {"fight_id": fight_id, "won": won}
    return snap


class Result:
    def __init__(self, ok=True, data=None, error=None):
        self.ok = ok
        self.data = data if data is not None else {}
        self.error = error


class FakeExecutor:
    """Records every call and answers from a table keyed on the tool name."""

    def __init__(self, snap=None, groups=None, join=None, nav=None,
                 groups_fail=None, snaps=None):
        self.snap = snap if snap is not None else snapshot()
        self.groups = groups if groups is not None else []
        self.join = join
        self.nav = nav or {}
        self.groups_fail = groups_fail
        # Snapshots handed out in order before falling back to `snap`; this is how a
        # transition that lands on a later poll is modelled.
        self.snaps = list(snaps or [])
        self.reads = 0
        self.calls = []

    def snapshot(self, instance=None):
        # First read is the state the decision was made on; every later read is a
        # poll, which is what a transition has to be judged by.
        self.reads += 1
        if self.reads > 1 and self.snaps:
            return self.snaps.pop(0)
        return self.snap

    def directive(self, tool, args=None, instance=None, source="directive"):
        self.calls.append((tool, dict(args or {})))
        if tool == "debug.groups":
            if self.groups_fail is not None:
                return self.groups_fail
            return Result(True, {"count": len(self.groups), "groups": self.groups})
        raise AssertionError("unexpected directive %r" % tool)

    def execute(self, tool, args=None, **kwargs):
        self.calls.append((tool, dict(args or {})))
        if tool == "farm.start":
            return Result(True, {"auto_fight": True})
        if tool == "farm.stop":
            return Result(True, {"auto_fight": False})
        if tool == "fight.join":
            if self.join is not None:
                return self.join
            return Result(True, {"group_id": (args or {}).get("group_id"),
                                 "cell": 387, "via": "walk then attack"})
        if tool == "nav.goto":
            direction = (args or {}).get("direction")
            answer = self.nav.get(direction)
            if answer is not None:
                return answer
            return Result(False, {"map_id": 146539520, "direction": direction},
                          "no exit to the %s on map 146539520" % direction)
        raise AssertionError("unexpected tool %r" % tool)

    def tools(self):
        return [name for name, _ in self.calls]


# -- reading a group --------------------------------------------------------

class GroupThreatTests(unittest.TestCase):
    def test_total_level_is_used_when_the_client_publishes_it(self):
        self.assertEqual(hunting.group_threat({"total_level": 24, "member_count": 3,
                                               "max_level": 8}), 24.0)

    def test_members_sum_when_total_level_is_missing(self):
        g = {"members": [{"level": 8}, {"level": 9}, {"level": 7}]}
        self.assertEqual(hunting.group_threat(g), 24.0)

    def test_a_partly_known_member_list_falls_back_to_count_times_max(self):
        # One unknown level is not a zero level. Summing only the known ones would
        # price the group as cheaper than it is, which is the direction that loses.
        g = {"members": [{"level": 8}, {}], "member_count": 2, "max_level": 8}
        self.assertEqual(hunting.group_threat(g), 16.0)

    def test_a_group_with_no_readable_members_is_not_free(self):
        g = {"monster_ids": [1, 2, 3]}
        self.assertEqual(hunting.group_threat(g), 3.0)

    def test_an_empty_group_has_no_threat(self):
        self.assertEqual(hunting.group_threat({}), 0.0)

    def test_the_id_is_the_contextual_id(self):
        self.assertEqual(hunting.group_id({"contextualId": -1709, "group_id": -1709}), -1709)

    def test_a_group_without_an_id_is_skipped_rather_than_joined_at_zero(self):
        self.assertIsNone(hunting.group_id({"cell": 387}))


# -- choosing ---------------------------------------------------------------

class ChooseGroupTests(unittest.TestCase):
    def test_the_biggest_affordable_group_wins(self):
        groups = [group(gid=-1, total=6), group(gid=-2, total=11), group(gid=-3, total=4)]
        chosen, why = hunting.choose_group(groups, level=12, budget=1.0)
        self.assertEqual(hunting.group_id(chosen), -2)
        self.assertIn("within budget", why)

    def test_a_group_over_budget_is_refused_with_the_arithmetic(self):
        chosen, why = hunting.choose_group([group(total=24)], level=12, budget=1.0)
        self.assertIsNone(chosen)
        self.assertIn("over budget", why)
        self.assertIn("24", why)
        self.assertIn("12", why)

    def test_the_same_map_always_yields_the_same_group(self):
        # Two groups of equal threat, listed in opposite orders. The tie-break is
        # the group id, so a re-issued repair is idempotent instead of alternating
        # between two fights -- which would halve the xp per walk.
        groups = [group(gid=-9, total=8), group(gid=-2, total=8)]
        first, _ = hunting.choose_group(groups, level=12, budget=1.0)
        second, _ = hunting.choose_group(list(reversed(groups)), level=12, budget=1.0)
        self.assertEqual(hunting.group_id(first), hunting.group_id(second))
        self.assertIn(hunting.group_id(first), (-9, -2))

    def test_an_empty_map_says_so(self):
        chosen, why = hunting.choose_group([], level=12, budget=1.0)
        self.assertIsNone(chosen)
        self.assertIn("no monster group", why)

    def test_a_learned_budget_admits_the_group_that_lost_before(self):
        # The measured loss was 3 x level 8 against a level 12: threat 24, ratio 2.0.
        # At the starting budget it is refused; once the budget is learned up past
        # 2.0 the same group becomes available again.
        chosen, _ = hunting.choose_group([group(total=24)], level=12, budget=2.1)
        self.assertIsNotNone(chosen)


# -- the learned budget -----------------------------------------------------

class HuntPolicyTests(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.path = Path(self.dir.name) / "hunt_policy.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_a_win_raises_the_budget_and_a_loss_lowers_it(self):
        policy = hunting.HuntPolicy(self.path)
        start = policy.budget
        policy.observe(True)
        self.assertGreater(policy.budget, start)
        policy.observe(False)
        self.assertLess(policy.budget, start * hunting.WIN_GAIN)

    def test_the_budget_is_clamped_at_both_ends(self):
        policy = hunting.HuntPolicy(self.path)
        for _ in range(40):
            policy.observe(True)
        self.assertLessEqual(policy.budget, hunting.MAX_BUDGET)
        for _ in range(40):
            policy.observe(False)
        self.assertGreaterEqual(policy.budget, hunting.MIN_BUDGET)

    def test_the_same_fight_is_only_counted_once(self):
        # `last_fight_end` stays on the snapshot until another fight ends. A
        # supervisor tick every 20 s would otherwise fold one loss in dozens of
        # times and drive the budget to the floor in a minute.
        policy = hunting.HuntPolicy(self.path)
        snap = snapshot(fight_id=1203, won=False)
        self.assertIs(policy.sync(snap), False)
        after = policy.budget
        self.assertIsNone(policy.sync(snap))
        self.assertEqual(policy.budget, after)
        self.assertEqual(policy.losses, 1)

    def test_a_new_fight_id_is_counted(self):
        policy = hunting.HuntPolicy(self.path)
        policy.sync(snapshot(fight_id=1203, won=False))
        self.assertIs(policy.sync(snapshot(fight_id=1204, won=True)), True)
        self.assertEqual((policy.wins, policy.losses), (1, 1))

    def test_a_snapshot_without_a_verdict_teaches_nothing(self):
        policy = hunting.HuntPolicy(self.path)
        self.assertIsNone(policy.sync(snapshot()))
        self.assertIsNone(policy.sync({"last_fight_end": {"fight_id": 1}}))
        self.assertEqual(policy.budget, hunting.START_BUDGET)

    def test_the_budget_survives_a_restart(self):
        policy = hunting.HuntPolicy(self.path)
        policy.observe(False)
        policy.observe(False)
        policy.save()
        self.assertAlmostEqual(hunting.HuntPolicy.load(self.path).budget, policy.budget, 6)

    def test_a_missing_file_is_a_fresh_policy_not_a_crash(self):
        policy = hunting.HuntPolicy.load(Path(self.dir.name) / "nope.json")
        self.assertEqual(policy.budget, hunting.START_BUDGET)

    def test_a_corrupt_file_is_a_fresh_policy_not_a_crash(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(hunting.HuntPolicy.load(self.path).budget, hunting.START_BUDGET)

    def test_an_absurd_budget_on_disk_is_clamped(self):
        self.path.write_text(json.dumps({"budget": 99.0}), encoding="utf-8")
        self.assertEqual(hunting.HuntPolicy.load(self.path).budget, hunting.MAX_BUDGET)

    def test_travel_avoids_the_edge_it_came_through(self):
        policy = hunting.HuntPolicy(self.path)
        policy.note_travel("right", 146540032)
        order = policy.travel_order()
        self.assertEqual(order[-1], "left")
        self.assertEqual(len(order), 4)


# -- engaging ---------------------------------------------------------------

class ForcedFightTests(unittest.TestCase):
    """A fight the budget refused must not teach the budget that it was right.

    Live 2026-11-10: 14 losses, 13 of them forced, took the budget from 1.00 to
    0.575 while every group on the map stayed over budget -- so every join stayed
    forced, and the ladder kept feeding its own last rung. The loss confirms a
    decision the budget already made; folding it back in is a loop, not learning.
    """

    def setUp(self):
        self.dir = TemporaryDirectory()
        self.path = Path(self.dir.name) / "hunt_policy.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_a_forced_loss_is_counted_but_leaves_the_budget_alone(self):
        policy = hunting.HuntPolicy(self.path)
        start = policy.budget
        policy.forced_pending = True
        self.assertIs(policy.sync(snapshot(fight_id=77, won=False)), False)
        self.assertEqual(policy.losses, 1)
        self.assertEqual(policy.budget, start)
        self.assertFalse(policy.forced_pending)

    def test_a_forced_win_still_raises_the_budget(self):
        # The budget refused a group that was then beaten: it was too strict, and
        # refusing to learn that is how it stays at the floor for a whole session.
        policy = hunting.HuntPolicy(self.path)
        start = policy.budget
        policy.forced_pending = True
        policy.sync(snapshot(fight_id=78, won=True))
        self.assertGreater(policy.budget, start)

    def test_a_chosen_loss_still_lowers_the_budget(self):
        policy = hunting.HuntPolicy(self.path)
        start = policy.budget
        policy.sync(snapshot(fight_id=79, won=False))
        self.assertLess(policy.budget, start)

    def test_the_flag_covers_one_fight_only(self):
        policy = hunting.HuntPolicy(self.path)
        policy.forced_pending = True
        policy.sync(snapshot(fight_id=80, won=False))
        after_forced = policy.budget
        policy.sync(snapshot(fight_id=81, won=False))
        self.assertLess(policy.budget, after_forced)

    def test_a_forced_join_that_never_started_does_not_label_the_next_fight(self):
        # `engage` only marks a fight forced once one actually started. A join the
        # client refused leaves no fight behind, and a stale flag would excuse the
        # next loss -- one the budget genuinely earned.
        executor = FakeExecutor(groups=[group(total=90, members=3, max_level=30)],
                                snap=snapshot(), join=Result(False, {}, "no group"))
        policy = hunting.HuntPolicy(self.path)
        report = hunting.engage(executor, policy)
        self.assertEqual(report["action"], "join_failed")
        self.assertIn("nowhere to go", report["reason"])
        self.assertFalse(policy.forced_pending)

    def test_a_forced_join_that_started_marks_the_fight(self):
        executor = FakeExecutor(groups=[group(total=90, members=3, max_level=30)],
                                snap=snapshot())
        policy = hunting.HuntPolicy(self.path)
        report = hunting.engage(executor, policy)
        self.assertEqual(report["action"], "forced_join")
        self.assertTrue(policy.forced_pending)


class EngageTests(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.policy = hunting.HuntPolicy(Path(self.dir.name) / "hunt_policy.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_a_fight_already_running_is_left_alone(self):
        executor = FakeExecutor(snap=snapshot(in_fight=True))
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "already_fighting")
        self.assertTrue(report["engaged"])
        self.assertEqual(executor.calls, [])

    def test_it_joins_a_group_the_budget_allows(self):
        executor = FakeExecutor(groups=[group(total=8)], snap=snapshot())
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "join")
        self.assertTrue(report["engaged"])
        self.assertIn(("fight.join", {"group_id": -1709}), executor.calls)

    def test_it_arms_the_bot_before_joining(self):
        executor = FakeExecutor(groups=[group(total=8)], snap=snapshot())
        hunting.engage(executor, self.policy)
        self.assertEqual(executor.tools()[0], "farm.start")
        self.assertIn("fight.join", executor.tools())

    def test_it_never_stops_the_farm_loop(self):
        # The regression: `farm.stop` disables the bot, so the following
        # `farm.start` reset the session counters the health check compares --
        # every repair looked like progress and the check declared recovery.
        executor = FakeExecutor(groups=[group(total=8)], snap=snapshot())
        hunting.engage(executor, self.policy)
        self.assertNotIn("farm.stop", executor.tools())

    def test_a_refused_join_is_not_reported_as_engaged(self):
        executor = FakeExecutor(
            groups=[group(total=8)], snap=snapshot(),
            join=Result(False, {}, "client directive failed: no client instance is running"))
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "join_failed")
        self.assertFalse(report["engaged"])
        self.assertIn("no client instance", report["detail"])

    def test_an_accepted_join_that_started_no_fight_is_not_engaged(self):
        # The page can answer ok=True with nothing but an ack. Reporting that as
        # success is the failure mode this module was written to remove.
        executor = FakeExecutor(groups=[group(total=8)], snap=snapshot(),
                                join=Result(True, {"note": "auto-fight enabled"}))
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "join_failed")
        self.assertFalse(report["engaged"])

    def test_an_over_budget_map_sends_the_character_elsewhere(self):
        arrived = snapshot()
        arrived["position"] = {"map_id": 146540032, "cell": 300}
        executor = FakeExecutor(
            groups=[group(total=24)], snap=snapshot(level=12), snaps=[arrived],
            nav={"right": Result(True, {"from_map": 146539520, "to_map": 146540032,
                                        "via": "isoEngine._requestMapChange"})})
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "travel")
        self.assertTrue(report["engaged"])
        self.assertIn("146540032", report["detail"])
        self.assertEqual(self.policy.last_map, 146540032)

    def test_an_ack_that_did_not_move_the_character_is_not_engaged(self):
        # Measured 2026-11-10: `nav.goto` answered `ok: true` with the requested
        # map id six times while the character stayed put, because the client had
        # published no exit cell and the server dropped `ChangeMapMessage`. Reading
        # the ack as a transition is what made the repair look like it worked.
        executor = FakeExecutor(
            groups=[], snap=snapshot(),
            nav={"right": Result(True, {"from_map": 146539520, "to_map": 146540032,
                                        "via": "isoEngine._requestMapChange"})})
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "travel_failed")
        self.assertFalse(report["engaged"])
        self.assertIn("stayed on 146539520", report["detail"])

    def test_a_transition_that_lands_on_a_later_poll_counts(self):
        arrived = snapshot()
        arrived["position"] = {"map_id": 146539521, "cell": 310}
        executor = FakeExecutor(
            groups=[], snap=snapshot(), snaps=[snapshot(), arrived],
            nav={"right": Result(True, {"to_map": 146539521, "via": "walk"})})
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "travel")
        self.assertTrue(report["engaged"])

    def test_travel_tries_the_other_edges_before_the_one_it_came_through(self):
        # Arriving by moving right means the way back is left, so left must be the
        # last edge tried. Without this the character paces between two empty maps
        # for as long as the supervisor keeps repairing it.
        self.policy.note_travel("right", 146540032)
        self.assertEqual(self.policy.avoid, "left")
        arrived = snapshot()
        arrived["position"] = {"map_id": 146539521, "cell": 310}
        executor = FakeExecutor(
            groups=[], snap=snapshot(), snaps=[arrived],
            nav={"bottom": Result(True, {"to_map": 146539521, "via": "walk"})})
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "travel")
        directions = [args.get("direction") for tool, args in executor.calls
                      if tool == "nav.goto"]
        self.assertNotIn("left", directions)
        self.assertIn("bottom", directions)

    def test_no_way_out_is_reported_rather_than_hidden(self):
        executor = FakeExecutor(groups=[], snap=snapshot())
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "travel_failed")
        self.assertFalse(report["engaged"])
        self.assertIn("no exit", report["detail"])

    def test_a_map_with_nothing_to_fight_and_nowhere_to_go_does_nothing(self):
        # No group, no exit: there is genuinely nothing to attempt, and inventing an
        # action here would be the same lie the old repair told.
        executor = FakeExecutor(groups=[], snap=snapshot())
        report = hunting.engage(executor, self.policy)
        self.assertNotIn("fight.join", executor.tools())
        self.assertIsNone(report["group"])

    def test_nowhere_to_go_forces_the_cheapest_fight(self):
        # Budget refuses the 24, travel answers nothing. Standing still teaches
        # nothing -- the live learner only credits turns inside a fight -- so the
        # weakest group is taken and the report says it was forced.
        executor = FakeExecutor(groups=[group(total=24)], snap=snapshot(level=12))
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "forced_join")
        self.assertTrue(report["engaged"])
        self.assertIn("nowhere to go", report["reason"])
        self.assertIn(("fight.join", {"group_id": -1709}), executor.calls)

    def test_forced_join_picks_the_cheapest_of_several(self):
        executor = FakeExecutor(
            groups=[group(gid=-1, total=40), group(gid=-2, total=24),
                    group(gid=-3, total=60)],
            snap=snapshot(level=12))
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "forced_join")
        self.assertEqual(report["group"]["group_id"], -2)

    def test_travel_can_be_disabled(self):
        executor = FakeExecutor(groups=[], snap=snapshot())
        report = hunting.engage(executor, self.policy, allow_travel=False)
        self.assertEqual(report["action"], "waiting")
        self.assertNotIn("nav.goto", executor.tools())

    def test_a_failed_listing_is_not_an_empty_map(self):
        # The listing goes through the executor's non-vocabulary path, and a
        # refusal there used to be read as `groups or []` -- an error that looked
        # exactly like a map with nothing on it, and sent the repair travelling.
        executor = FakeExecutor(
            groups=[group(total=8)], snap=snapshot(),
            groups_fail=Result(False, {}, "client directive failed: no client instance"))
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "listing_failed")
        self.assertIn("no client instance", report["reason"])
        self.assertNotIn("nav.goto", executor.tools())

    def test_no_snapshot_is_a_report_not_an_exception(self):
        class Blind:
            def snapshot(self, instance=None):
                return None
        report = hunting.engage(Blind(), self.policy)
        self.assertEqual(report["action"], "none")
        self.assertIn("no snapshot", report["reason"])

    def test_an_executor_that_raises_still_returns_a_report(self):
        class Angry(FakeExecutor):
            def execute(self, tool, args=None, **kwargs):
                raise RuntimeError("bridge went away")
        report = hunting.engage(Angry(groups=[group(total=8)]), self.policy)
        self.assertFalse(report["engaged"])
        self.assertIn("bridge went away", report["detail"])

    def test_an_unreadable_level_refuses_to_price_a_group(self):
        executor = FakeExecutor(groups=[group(total=8)], snap=snapshot(level=0))
        report = hunting.engage(executor, self.policy)
        self.assertEqual(report["action"], "none")
        self.assertIn("level is unreadable", report["reason"])

    def test_a_client_out_of_the_world_is_not_engaged(self):
        executor = FakeExecutor(groups=[group(total=8)], snap=snapshot(in_world=False))
        report = hunting.engage(executor, self.policy)
        self.assertFalse(report["engaged"])
        self.assertIn("not in the world", report["reason"])


class DescribeTests(unittest.TestCase):
    def test_a_join_names_the_group_and_its_level(self):
        line = hunting.describe({"action": "join", "engaged": True,
                                 "group": {"group_id": -1709, "members": 3,
                                           "total_level": 24.0},
                                 "detail": "fight.join -> ok=True "})
        self.assertIn("-1709", line)
        self.assertIn("24", line)

    def test_a_refusal_names_the_reason(self):
        line = hunting.describe({"action": "none", "engaged": False,
                                 "group": None,
                                 "reason": "every group here is over budget",
                                 "detail": ""})
        self.assertIn("over budget", line)

    def test_an_empty_report_is_still_a_line(self):
        self.assertTrue(hunting.describe({}))


if __name__ == "__main__":
    unittest.main()
