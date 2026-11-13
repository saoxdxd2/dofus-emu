"""Navigation-related regression tests, moved from regression.py.

These tests exercise map crossing, exit routing, door cells, and
navigation adjacency logic.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from agent.regression import REPO_ROOT, check, section


def test_doorless_map_asks_for_the_neighbour() -> None:
    section("Doorless map asks for the neighbour")
    import unittest
    from navigation import test_map_transitions as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("doorless map neighbour tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_free_form_searches_new_maps() -> None:
    section("Free-form search explores new maps")
    import unittest
    from navigation import test_free_form as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("free-form search tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_objective_travel_is_bounded() -> None:
    section("Objective travel is bounded")
    import unittest
    from navigation import test_objective_travel as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("objective travel tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_return_bound() -> None:
    section("Return bound")
    import unittest
    from navigation import test_return_bound as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("return bound tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_the_search_borders_are_bounded() -> None:
    section("The search borders are bounded")
    import unittest
    from navigation import test_search_borders as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("search border tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_search_prefers_fresh_maps() -> None:
    section("Search prefers fresh maps")
    import unittest
    from navigation import test_search_fresh as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("search fresh map tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


__all__ = [
    "test_doorless_map_asks_for_the_neighbour",
    "test_free_form_searches_new_maps",
    "test_objective_travel_is_bounded",
    "test_return_bound",
    "test_the_search_borders_are_bounded",
    "test_search_prefers_fresh_maps",
]
