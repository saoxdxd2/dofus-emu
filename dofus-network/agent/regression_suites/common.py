"""Shared helpers for regression test suites.

Holds the module-level state and helpers that both ``regression.py`` and the
domain suites under ``regression_suites/`` need, so the domain suites no
longer import from ``agent.regression`` (which created a circular import:
``regression`` → ``regression_suites`` → ``regression``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent      # .../dofus-network
ROOT = REPO_ROOT.parent                                        # .../dofus-emu

_CHECKS = 0
_FAILURES: List[str] = []

# Wheels this repository treats as optional.
OPTIONAL_IMPORTS = ("jax", "sklearn")


def check(label: str, condition: Any, detail: Any = "") -> None:
    global _CHECKS
    _CHECKS += 1
    if condition:
        return
    # One `%s` per element, not a nested interpolation of the detail: a tuple
    # detail used to raise "not all arguments converted during string
    # formatting" inside the helper, so reporting a failure killed the run
    # that was reporting it. The fix belongs here rather than in every call
    # site.
    text = "%s: %s" % (label, detail) if detail else label
    _FAILURES.append(text)
    logger.debug("FAIL %s" % text)


def section(title: str) -> None:
    logger.debug("\n== %s ==" % title)
