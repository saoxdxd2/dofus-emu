"""Memory-related regression tests, moved from regression.py.

These tests exercise durable memory, knowledge index, delegated suites,
and related memory/persistence subsystems.
"""

from __future__ import annotations

import unittest

from agent.regression import check, section


def test_durable_memory() -> None:
    section("Durable memory")
    import unittest
    from agent import test_durable_memory as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("durable memory tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_delegated_suites_report_themselves() -> None:
    section("Delegated suites report themselves")
    import unittest
    from agent import test_delegated_suites_report_themselves as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("delegated suites report themselves tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_delegated_suites_cannot_silently_vanish() -> None:
    section("Delegated suites cannot silently vanish")
    import unittest
    from agent import test_delegated_suites_cannot_silently_vanish as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("delegated suites cannot silently vanish tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_knowledge_index_storage() -> None:
    section("Knowledge index storage")
    import unittest
    from agent import test_knowledge_index_storage as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("knowledge index storage tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_knowledge_index() -> None:
    section("Knowledge index")
    import unittest
    from agent import test_knowledge_index as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("knowledge index tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_the_memory_vetoes_what_the_context_refuted() -> None:
    section("The memory vetoes what the context refuted")
    import unittest
    from agent import test_the_memory_vetoes_what_the_context_refuted as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("memory veto tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


__all__ = [
    "test_durable_memory",
    "test_delegated_suites_report_themselves",
    "test_delegated_suites_cannot_silently_vanish",
    "test_knowledge_index_storage",
    "test_knowledge_index",
    "test_the_memory_vetoes_what_the_context_refuted",
]
