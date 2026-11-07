"""Dofus Spatial Navigation, Resource Knowledge Graph, and NLP Intent Engine.

Public names are imported lazily: a caller that only needs ``CellPathfinder``
or ``DofusWorldGraph`` should not pay for the quest cache and the NLP engine.
"""

# ruff: noqa: F401 - the TYPE_CHECKING block documents lazy exports

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from _lazy_exports import lazy_export

_EXPORTS = {
    "CellPathfinder": "navigation.world_graph",
    "Direction": "navigation.world_graph",
    "DofusWorldGraph": "navigation.world_graph",
    "MapCoord": "navigation.world_graph",
    "NavigationPath": "navigation.world_graph",
    "NavigationStep": "navigation.world_graph",
    "ZaapLocation": "navigation.world_graph",
    "FarmLoop": "navigation.resource_graph",
    "ResourceKnowledgeGraph": "navigation.resource_graph",
    "ResourceNode": "navigation.resource_graph",
    "ResourceSpot": "navigation.resource_graph",
    "QuestGuide": "navigation.quest_graph",
    "QuestKnowledgeGraph": "navigation.quest_graph",
    "QuestStep": "navigation.quest_graph",
    "DofusNLPEngine": "navigation.nlp_intent_engine",
    "NLPIntent": "navigation.nlp_intent_engine",
    "NLPQueryResult": "navigation.nlp_intent_engine",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    return lazy_export(globals(), _EXPORTS, __name__, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


if TYPE_CHECKING:  # pragma: no cover - type checkers only
    from navigation.nlp_intent_engine import DofusNLPEngine, NLPIntent, NLPQueryResult
    from navigation.quest_graph import QuestGuide, QuestKnowledgeGraph, QuestStep
    from navigation.resource_graph import (
        FarmLoop,
        ResourceKnowledgeGraph,
        ResourceNode,
        ResourceSpot,
    )
    from navigation.world_graph import (
        CellPathfinder,
        Direction,
        DofusWorldGraph,
        MapCoord,
        NavigationPath,
        NavigationStep,
        ZaapLocation,
    )
