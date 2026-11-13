"""Dialog-related regression tests, moved from regression.py.

These tests exercise dialog ranking, prompt fidelity, named mob focus,
and related dialog/subsystem tests.
"""

from __future__ import annotations

import unittest

from agent.regression import check, section


def test_dialog_ranking() -> None:
    section("Dialog ranking")
    import unittest
    from agent import test_dialog_ranking as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("dialog ranking tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_prompt_fidelity() -> None:
    section("Prompt fidelity")
    import unittest
    from agent import test_prompt_fidelity as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("prompt fidelity tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_named_mob_focus() -> None:
    section("Named mob focus")
    import unittest
    from agent import test_named_mob_focus as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("named mob focus tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_dialog_labels() -> None:
    section("Dialog labels")
    import unittest
    from agent import test_dialog_labels as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("dialog label tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_dialog_repeat_guard() -> None:
    section("Dialog repeat guard")
    import unittest
    from agent import test_dialog_repeat_guard as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("dialog repeat guard tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_dialog_label_reader() -> None:
    section("Dialog label reader")
    import unittest
    from agent import test_dialog_label_reader as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("dialog label reader tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


def test_talk_needs_resolved_actor() -> None:
    section("Talk needs resolved actor")
    import unittest
    from agent import test_talk_needs_resolved_actor as module
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    check("talk needs resolved actor tests execute and pass",
          result.testsRun >= 3 and result.wasSuccessful(),
          {"tests": result.testsRun, "failures": result.failures,
           "errors": result.errors})


__all__ = [
    "test_dialog_ranking",
    "test_prompt_fidelity",
    "test_named_mob_focus",
    "test_dialog_labels",
    "test_dialog_repeat_guard",
    "test_dialog_label_reader",
    "test_talk_needs_resolved_actor",
]
