"""Proxy/egress-related regression tests, moved from regression.py.

These tests exercise command routing, bridge fast-fail, egress preflight,
host launch guard, and auto-live guards.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agent.regression_suites.common import REPO_ROOT, OPTIONAL_IMPORTS, check, section


def test_command_routing_and_bridge_fast_fail() -> None:
    section("Command routing and bridge fast-fail")
    import unittest
    from agent import test_command_routing as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("command routing tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_egress_preflight() -> None:
    section("Egress preflight")
    import unittest
    from agent import test_egress_preflight as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("egress preflight tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_host_launch_guard() -> None:
    section("Host launch guard")
    import unittest
    from agent import test_host_launch_guard as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("host launch guard tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_auto_live_guards() -> None:
    section("Auto live guards")
    import unittest
    from agent import test_auto_live_guards as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("auto live guards tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_auto_live_summarise() -> None:
    section("Auto live summarise")
    import unittest
    from agent import test_auto_live_summarise as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("auto live summarise tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


__all__ = [
    "test_command_routing_and_bridge_fast_fail",
    "test_egress_preflight",
    "test_host_launch_guard",
    "test_auto_live_guards",
    "test_auto_live_summarise",
]
