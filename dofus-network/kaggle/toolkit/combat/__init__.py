# dofus-network/combat package
"""Combat building blocks.

The public names are imported lazily.  ``combat.battle_solver`` and the live
policy need none of the tactical/reasoner stack, and importing that stack pulls
in torch (about two seconds and a large resident set) before the first decision.
PEP 562 keeps ``from combat import TacticalCombatEngine`` working while paying
for a module only when its name is actually used.
"""

# ruff: noqa: F401 - the TYPE_CHECKING block documents lazy exports

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from _lazy_exports import lazy_export

_EXPORTS = {
    "CharacterStats": "combat.damage_calculator",
    "DamageBreakdown": "combat.damage_calculator",
    "DofusDamageCalculator": "combat.damage_calculator",
    "OptimalRotationResult": "combat.damage_calculator",
    "SpellDamageResult": "combat.damage_calculator",
    "SpellRotationOptimizer": "combat.damage_calculator",
    "TargetStats": "combat.damage_calculator",
    "EpisodicSkill": "combat.episodic_memory",
    "EpisodicSkillMemory": "combat.episodic_memory",
    "RecursiveDecision": "combat.recursive_reasoner",
    "SingletonCombatReasoner": "combat.recursive_reasoner",
    "TacticalStateEncoder": "combat.recursive_reasoner",
    "TinyRecursiveReasoner": "combat.recursive_reasoner",
    "ActiveBuff": "combat.tactical_engine",
    "TacticalCombatEngine": "combat.tactical_engine",
    "TacticalStance": "combat.tactical_engine",
    "TacticalTurnPlan": "combat.tactical_engine",
    "CombatState": "combat.world_model",
    "DofusWorldModel": "combat.world_model",
    "StepResult": "combat.world_model",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    return lazy_export(globals(), _EXPORTS, __name__, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


if TYPE_CHECKING:  # pragma: no cover - type checkers only
    from combat.damage_calculator import (
        CharacterStats,
        DamageBreakdown,
        DofusDamageCalculator,
        OptimalRotationResult,
        SpellDamageResult,
        SpellRotationOptimizer,
        TargetStats,
    )
    from combat.episodic_memory import EpisodicSkill, EpisodicSkillMemory
    from combat.recursive_reasoner import (
        RecursiveDecision,
        SingletonCombatReasoner,
        TacticalStateEncoder,
        TinyRecursiveReasoner,
    )
    from combat.tactical_engine import (
        ActiveBuff,
        TacticalCombatEngine,
        TacticalStance,
        TacticalTurnPlan,
    )
    from combat.world_model import CombatState, DofusWorldModel, StepResult
