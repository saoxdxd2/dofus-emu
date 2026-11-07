"""Unit checks for ExplorationPolicy (no live client, no simulator)."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

from agent.explore import ExplorationPolicy
from agent.runtime import ToolCall


class _Twin:
    def __init__(self, alt):
        self.alt = alt
        self._skipped = set()

    def choose(self, goal, obs, tools):
        return self.alt


class _Planner:
    def __init__(self, alt, quest=4242):
        self.alt = alt
        self.current_quest = quest
        self.forked = 0

    def fork(self):
        self.forked += 1
        return _Twin(self.alt)


class _Inner:
    def __init__(self, call, planner):
        self.call = call
        self.planner = planner
        self.noted = []

    def choose(self, goal, obs, tools):
        return self.call

    def note_result(self, tool, ok, note, obs):
        self.noted.append((tool, ok))


class _AlwaysZero:
    def random(self):
        return 0.0


def _policy(alt, epsilon=0.5, obs=None, call=None):
    teacher = call or ToolCall("quest.advance", {"quest_id": 1, "npc_id": 2})
    inner = _Inner(teacher, _Planner(alt))
    policy = ExplorationPolicy(inner, epsilon=epsilon)
    policy.rng = _AlwaysZero()
    return policy, inner, teacher, obs or {"map_id": 1}


def test_epsilon_must_be_a_fraction():
    for bad in (0.0, -0.1, 0.9):
        try:
            ExplorationPolicy(_Inner(None, _Planner(None)), epsilon=bad)
        except ValueError:
            continue
        raise AssertionError("epsilon %r was accepted" % (bad,))
    ExplorationPolicy(_Inner(None, _Planner(None)), epsilon=0.5)


def test_when_the_coin_says_explore_the_alternative_is_taken_and_tagged():
    alt = ToolCall("nav.goto", {"map_id": 77})
    policy, inner, teacher, obs = _policy(alt)
    call = policy.choose("do all the quests", obs, None)
    assert call is alt
    assert call.meta["explored"] is True
    assert call.meta["arm"] == "explore"
    assert call.meta["p_explore"] == 0.5
    assert call.meta["teacher_tool"] == teacher.tool
    assert policy.explored == 1 and policy.offered == 1


def test_when_the_coin_says_no_the_teacher_is_taken():
    alt = ToolCall("nav.goto", {"map_id": 77})
    policy, inner, teacher, obs = _policy(alt)
    policy.rng = type("R", (), {"random": lambda self: 0.99})()
    assert policy.choose("do all the quests", obs, None) is teacher
    assert policy.explored == 0
    # The second plan is still computed: the teacher row must say whether it was
    # even possible to explore here, or the arms cannot be compared.
    assert inner.planner.forked == 1
    assert teacher.meta["has_alternative"] is True
    assert teacher.meta["arm"] == "teacher"
    assert teacher.meta["p_explore"] == 0.5


def test_a_fight_or_dialog_or_tutorial_keeps_the_teacher():
    alt = ToolCall("nav.goto", {"map_id": 77})
    for block in ({"in_fight": True}, {"in_dialog": True},
                  {"tutorial": {"active": True}}):
        policy, inner, teacher, _ = _policy(alt)
        obs = {"map_id": 1, **block}
        assert policy.choose("do all the quests", obs, None) is teacher
        assert inner.planner.forked == 0
        assert teacher.meta is None


def test_an_identical_second_plan_is_not_exploration():
    teacher = ToolCall("quest.advance", {"quest_id": 1, "npc_id": 2})
    alt = ToolCall("quest.advance", {"quest_id": 1, "npc_id": 2})
    policy, inner, teacher, obs = _policy(alt, call=teacher)
    assert policy.choose("do all the quests", obs, None) is teacher
    assert policy.skipped == 1 and policy.explored == 0
    assert teacher.meta["has_alternative"] is False
    assert teacher.meta["p_explore"] == 0.0


def test_no_current_quest_means_no_alternative():
    alt = ToolCall("nav.goto", {"map_id": 77})
    teacher = ToolCall("quest.advance", {"quest_id": 1})
    inner = _Inner(teacher, _Planner(alt, quest=None))
    policy = ExplorationPolicy(inner, epsilon=0.5)
    policy.rng = _AlwaysZero()
    assert policy.choose("do all the quests", {"map_id": 1}, None) is teacher
    assert teacher.meta is None and policy.offered == 0


def test_note_result_is_delegated():
    policy, inner, _, _ = _policy(None)
    policy.note_result("nav.goto", True, "ok", {})
    assert inner.noted == [("nav.goto", True)]


def test_the_recorder_and_the_builder_keep_the_tag():
    from agent.recorder import TrajectoryRecorder

    with tempfile.TemporaryDirectory() as tmp:
        recorder = TrajectoryRecorder(root=Path(tmp), instance="unit")
        row = recorder.step(tool="quest.advance", args={"quest_id": 1}, ok=True,
                            explored=True,
                            meta={"explored": True, "teacher_tool": "nav.goto"})
    assert row["explored"] is True
    assert row["meta"]["teacher_tool"] == "nav.goto"

    spec = importlib.util.spec_from_file_location(
        "build_live_transitions", Path(__file__).resolve().parent.parent
        / "tools" / "build_live_transitions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    obs = {"map": {"map_id": 100, "cell": 3}}
    transition = module.label_steps({
        "source": "unit", "instance": "unit", "goal": "g",
        "steps": [{"kind": "step", "ts": 1, "iso": "2026-10-29T00:00:00",
                   "tool": "quest.advance", "args": {}, "ok": True,
                   "observation": obs, "result": {"ok": True, "snapshot": obs},
                   "explored": True, "meta": {"teacher_tool": "nav.goto"}}],
    })[0]
    assert transition["explored"] is True
    assert transition["teacher_tool"] == "nav.goto"
