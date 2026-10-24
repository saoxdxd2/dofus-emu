"""Read-only spell, stat, gear and market advisor."""

from .elements import ElementDecision, choose_element, marginal_primary_damage
from .gear import GearAdvisor
from .market import MarketReport, analyze_market
from .models import (
    ELEMENTS,
    PRIMARY_STAT,
    CharacterSnapshot,
    MarketSnapshot,
    merge_market,
)
from .report import AdvisorReport, build_report, character_summary
from .repository import Repository
from .spells import SpellPlan, choose_budget, plan_spells

__all__ = [
    "ELEMENTS",
    "PRIMARY_STAT",
    "AdvisorReport",
    "CharacterSnapshot",
    "ElementDecision",
    "GearAdvisor",
    "MarketReport",
    "MarketSnapshot",
    "Repository",
    "SpellPlan",
    "analyze_market",
    "build_report",
    "character_summary",
    "choose_budget",
    "choose_element",
    "marginal_primary_damage",
    "merge_market",
    "plan_spells",
]
