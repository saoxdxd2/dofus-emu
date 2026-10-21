"""Planning layer: cost model, AND/OR plan search, and the schedule manager.

Design rule for this package: **intelligence per megabyte of RAM, not per
parameter.** Nothing here generates text. The search is symbolic, the cost model
is deterministic, and the only learned component is a small scorer that reorders
the search frontier.

    kb.py         compact typed-array knowledge base (~4 MB resident)
    economy.py    kamas/minute, drop-rate priors, calibration from runs
    planner.py    AND/OR best-first search: item -> buy | craft | farm | quest
    schedule.py   executes a plan, detects drift, replans
"""

from __future__ import annotations

from typing import Any

_EXPORTS = {
    "KnowledgeBase": ("planning.kb", "KnowledgeBase"),
    "Item": ("planning.kb", "Item"),
    "Monster": ("planning.kb", "Monster"),
    "Economy": ("planning.economy", "Economy"),
    "CostModel": ("planning.economy", "CostModel"),
    "Planner": ("planning.planner", "Planner"),
    "Plan": ("planning.planner", "Plan"),
    "PlanNode": ("planning.planner", "PlanNode"),
    "Goal": ("planning.planner", "Goal"),
    "ScheduleManager": ("planning.schedule", "ScheduleManager"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = _EXPORTS[name]
    from importlib import import_module

    return getattr(import_module(module_name), attribute)
