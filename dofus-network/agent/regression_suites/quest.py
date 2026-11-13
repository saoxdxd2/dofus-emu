"""Quest-related regression tests, moved from regression.py.

These tests exercise objective buckets, quest advancement, travel,
and related quest-progression subsystems.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from agent.regression_suites.common import REPO_ROOT, check, section


def test_objective_buckets() -> None:
    section("Objective buckets")
    import unittest
    from agent import test_objective_buckets as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("objective bucket tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_objective_parameter_table() -> None:
    section("Objective parameter table")
    import unittest
    from agent import test_objective_parameter_table as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("objective parameter table tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_stuck_objective_retired() -> None:
    section("Stuck objective retired")
    import unittest
    from agent import test_stuck_objective as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("stuck objective tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_quest_advance_shapes() -> None:
    section("Quest advance shapes")
    import unittest
    from quest import test_quest_advance_shapes as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("quest advance shape tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_quest_area_rejects_the_prompt_echo() -> None:
    section("Quest area rejects the prompt echo")
    import unittest
    from quest import test_quest_area as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("quest area tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_live_transitions() -> None:
    section("Live transitions")
    import unittest
    from agent import test_live_transitions as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("live transition tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_quest_data_wait() -> None:
    section("Quest data wait")
    import unittest
    from agent import test_quest_data_wait as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("quest data wait tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_quest_accept_planner() -> None:
    section("Quest accept planner")
    import unittest
    from agent import test_quest_accept_planner as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("quest accept planner tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


__all__ = [
    "test_objective_buckets",
    "test_objective_parameter_table",
    "test_stuck_objective_retired",
    "test_quest_advance_shapes",
    "test_quest_area_rejects_the_prompt_echo",
    "test_live_transitions",
    "test_quest_data_wait",
    "test_quest_accept_planner",
]
