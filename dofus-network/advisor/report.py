"""One read-only report that joins element, spells, stats, gear and market.

This is the advisor's public surface. It answers, in order:

* what element the character should build around, and why;
* which spell grades buy the most per-turn value for the points on hand;
* what the next characteristic points are worth in damage;
* which sets and slots are worth farming, crafting or buying, and at what
  price;
* which cached market offers are cheap for this character and which owned
  items are worth listing.

It never equips, spends a point, buys or sells. Those are human actions with
no undo, and the report is deliberately only advice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .elements import ElementDecision, choose_element, marginal_primary_damage
from .gear import GearAdvisor
from .market import MarketReport, analyze_market
from .models import ELEMENT_LABEL, CharacterSnapshot, MarketSnapshot
from .repository import Repository
from .spells import SpellPlan, plan_spells

CAVEATS = (
    "Read-only: the advisor never spends spell or stat points, equips gear, "
    "buys or sells. Every recommendation is a human action.",
    "Spell damage comes from the shared combat calculator against a zero-resist "
    "dummy; utility lines (AP/MP removal, range, shields) use explicit weights.",
    "Gear scores are linear weights over the recovered stat keys, not a fight "
    "simulation. Item rolls are not visible in the live template report.",
    "A market listing is supply, not revenue. Buying spends kamas with no undo.",
    "The egress guard is untouched: the advisor only reads through the local bridge.",
)


def _dedupe(lines: List[str]) -> List[str]:
    seen = set()
    out = []
    for line in lines:
        text = str(line).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


@dataclass
class AdvisorReport:
    character: Dict[str, Any]
    element: ElementDecision
    spells: SpellPlan
    stats: Dict[str, Any]
    gear: Dict[str, Any]
    market: MarketReport
    warnings: List[str] = field(default_factory=list)
    caveats: List[str] = field(default_factory=lambda: list(CAVEATS))

    # -- summaries ---------------------------------------------------------
    def summary(self) -> List[str]:
        lines = [
            "Element: %s - %s"
            % (ELEMENT_LABEL.get(self.element.chosen, self.element.chosen),
               self.element.reason),
        ]
        plan = self.spells
        if plan.upgrades:
            lines.append(
                "Spells: %d upgrade(s), %d/%d points, %+.1f rotation damage"
                % (len(plan.upgrades), plan.spent, plan.budget,
                   plan.actual_damage_gain))
        else:
            lines.append(
                "Spells: no positive upgrade within %d point(s)" % plan.budget)
        top_set = next((row for row in self.gear.get("set_candidates") or []
                        if row.get("action") not in ("keep_current", "owned")), None)
        if top_set:
            lines.append(
                "Gear: %s (%s) for %+.1f score, %d missing piece(s) - %s"
                % (top_set["name"], top_set["action"], top_set["delta_full"],
                   top_set["missing_pieces"],
                   (top_set.get("acquisition") or {}).get("note", "")))
        elif self.gear.get("slot_upgrades"):
            top = self.gear["slot_upgrades"][0]
            lines.append(
                "Gear: %s in %s for %+.1f score (%s)"
                % (top["candidate_name"], top["slot"], top["delta"],
                   top.get("action")))
        else:
            lines.append("Gear: no scored upgrade found in the encyclopedia dump")
        market = self.market
        if market.average_count:
            lines.append(
                "Market: %d average(s), %d buy / %d sell signal(s), owned "
                "liquidation %.0f kamas"
                % (market.average_count, len(market.buy_signals),
                   len(market.sell_signals), market.owned_liquidation_value))
        else:
            lines.append("Market: no cached prices; capture while logged in")
        return lines

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary(),
            "character": self.character,
            "element": self.element.to_dict(),
            "spells": self.spells.to_dict(),
            "stats": self.stats,
            "gear": self.gear,
            "market": self.market.to_dict(),
            "warnings": self.warnings,
            "caveats": self.caveats,
        }

    # -- text --------------------------------------------------------------
    def render_text(self) -> str:
        c = self.character
        lines: List[str] = []
        lines.append(
            "Advisor report - %s, level %s %s"
            % (c.get("name") or "unnamed", c.get("level"),
               c.get("breed_name") or "class #%s" % c.get("breed_id")))
        if c.get("position"):
            position = c["position"]
            lines.append(
                "Position: map %s cell %s (%s, %s)"
                % (position.get("map_id"), position.get("cell"),
                   position.get("x"), position.get("y")))
        lines.append(
            "Points: AP %s, MP %s, HP %s/%s, kamas %s"
            % (c.get("ap"), c.get("mp"), c.get("life"), c.get("max_hp"),
               c.get("kamas")))
        lines.append("")
        lines.extend("  " + line for line in self.summary())

        lines.append("")
        lines.append("ELEMENT")
        lines.append("  chosen: %s (%s)"
                     % (ELEMENT_LABEL.get(self.element.chosen, self.element.chosen),
                        self.element.reason))
        lines.append("  %-8s %10s %10s %10s %s"
                     % ("element", "stat", "cur dmg", "pot dmg", "best spell"))
        for score in self.element.scores:
            lines.append("  %-8s %10.0f %10.1f %10.1f %s (g%s)"
                         % (score.element, score.current_stat,
                            score.current_damage, score.potential_damage,
                            score.best_spell or "-", score.best_grade or "-"))

        plan = self.spells
        lines.append("")
        lines.append("SPELLS")
        lines.append("  budget: %d point(s) from %s - %s"
                     % (plan.budget, plan.budget_source, plan.budget_assumption))
        if plan.upgrades:
            for upgrade in plan.upgrades:
                lines.append(
                    "  - %s: grade %d -> %d, %d point(s), %+.1f value, %+.1f damage"
                    % (upgrade.name, upgrade.current_grade, upgrade.recommended_grade,
                       upgrade.cost, upgrade.estimated_value_gain,
                       upgrade.estimated_damage_gain))
        else:
            lines.append("  - no upgrade recommended")
        lines.append("  before: %s"
                     % _rotation_text(plan.rotation_before))
        lines.append("  after:  %s"
                     % _rotation_text(plan.rotation_after))
        lines.append("  spent %d, unspent %d"
                     % (plan.spent, plan.unspent))
        if plan.unscored:
            lines.append("  unscored live spells: %s"
                         % ", ".join(str(row.get("spell_id")) for row in plan.unscored))
        for scenario in plan.budget_scenarios:
            lines.append("  scenario %-32s %5d pts -> %6d spent, %+.1f value"
                         % (scenario["label"], scenario["budget"],
                            scenario["spent"], scenario["estimated_gain"]))

        lines.append("")
        lines.append("STATS")
        primary = self.stats.get("primary") or {}
        for element in ("earth", "fire", "water", "air"):
            row = primary.get(element)
            if row:
                lines.append("  %-8s +%.2f damage per %s point"
                             % (element, row["damage_per_point"],
                                row["primary_stat"]))
        if self.stats.get("stat_points_raw") is not None:
            lines.append("  unspent stat points (raw field): %s"
                         % self.stats["stat_points_raw"])

        gear = self.gear
        lines.append("")
        lines.append("GEAR")
        lines.append("  current score %.1f (slots %.1f + sets %.1f), margin +%d levels"
                     % (gear.get("current_score", 0.0),
                        gear.get("current_score", 0.0) - gear.get("current_bonus_score", 0.0),
                        gear.get("current_bonus_score", 0.0),
                        gear.get("level_margin", 0)))
        for active in gear.get("active_sets") or []:
            lines.append("  active set %s: %d pieces, bonus score %.1f%s"
                         % (active["name"], active["equipped_count"],
                            active["active_bonus_score"],
                            ", missing %d" % len(active["missing_ids"])
                            if active["missing_ids"] else ""))
        if gear.get("set_candidates"):
            lines.append("  set candidates:")
            for candidate in gear["set_candidates"][:5]:
                acquisition = candidate.get("acquisition") or {}
                lines.append(
                    "  - %-28s lvl %3d  %+7.1f full / %+7.1f owned  %s  %s"
                    % (candidate["name"], candidate["level"],
                       candidate["delta_full"], candidate["delta_owned"],
                       candidate["action"], acquisition.get("note", "")))
        if gear.get("slot_upgrades"):
            lines.append("  slot upgrades:")
            for upgrade in gear["slot_upgrades"][:6]:
                lines.append(
                    "  - %-22s %-9s %+7.1f  %s  %s"
                    % (upgrade["candidate_name"], upgrade["slot"],
                       upgrade["delta"], upgrade["action"],
                       (upgrade.get("acquisition") or {}).get("note", "")))

        market = self.market
        lines.append("")
        lines.append("MARKET")
        lines.append("  averages %d, listings %d, price median %s"
                     % (market.average_count, market.listing_count,
                        _kamas(market.price_median)))
        if market.buy_signals:
            lines.append("  buy:")
            for signal in market.buy_signals[:8]:
                lines.append(
                    "  - %-30s %s vs average %s (%+.0f%%)"
                    % (signal.name, _kamas(signal.listing.min if signal.listing else None),
                       _kamas(signal.average), (signal.discount or 0.0) * 100.0))
        if market.sell_signals:
            lines.append("  sell:")
            for signal in market.sell_signals[:8]:
                lines.append(
                    "  - %-30s x%d  %s  quick %s / patient %s"
                    % (signal.name, signal.owned_quantity, signal.sell_verdict,
                       _kamas(signal.quick_sale), _kamas(signal.patient_price)))
        if not market.buy_signals and not market.sell_signals:
            lines.append("  no buy or sell signal cleared the thresholds")

        if self.warnings:
            lines.append("")
            lines.append("WARNINGS")
            for warning in self.warnings:
                lines.append("  - " + warning)
        lines.append("")
        lines.append("CAVEATS")
        for caveat in self.caveats:
            lines.append("  - " + caveat)
        return "\n".join(lines)


def _rotation_text(rotation) -> str:
    if not rotation.casts:
        return "no castable spell"
    parts = []
    for cast in rotation.casts:
        parts.append("%s g%d x%d" % (cast.name, cast.grade, cast.casts))
    return ("%s | %.1f damage, %.1f heal, %d AP"
            % (", ".join(parts), rotation.damage, rotation.heal, rotation.ap_used))


def _kamas(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return "%.0f" % value


def character_summary(repo: Repository, character: CharacterSnapshot) -> Dict[str, Any]:
    return {
        "id": character.character_id,
        "name": character.name,
        "level": character.level,
        "breed_id": character.breed_id,
        "breed_name": repo.breed_names.get(character.breed_id),
        "sex": character.sex,
        "captured_at": character.captured_at,
        "position": dict(character.position or {}),
        "ap": character.ap_budget,
        "mp": int(character.stat("movementPoints", "movement_points") or 0),
        "life": character.stat("lifePoints"),
        "max_hp": character.max_hp or None,
        "kamas": (character.inventory or {}).get("kamas"),
        "primary": {element: round(value, 1)
                    for element, value in character.primary_totals.items()},
        "spell_count": len([spell for spell in character.spells if not spell.is_item]),
        "equipped_count": len(character.equipment),
        "active_set_count": len(character.sets),
        "owned_gid_count": len(character.owned_gids),
    }


def build_report(
    repo: Repository,
    character: CharacterSnapshot,
    market: Optional[MarketSnapshot] = None,
    focus: str = "auto",
    budget: Optional[int] = None,
    level_margin: int = 5,
    min_price: float = 100.0,
    min_discount: float = 0.15,
) -> AdvisorReport:
    market = market or MarketSnapshot()
    element = choose_element(repo, character, focus)
    spells = plan_spells(repo, character, element.chosen, budget)
    gear = GearAdvisor(
        repo, character, element.chosen, market, level_margin=level_margin).to_dict()
    market_report = analyze_market(
        repo, character, market, min_price=min_price, min_discount=min_discount)
    stats = marginal_primary_damage(repo, character, element.chosen)

    warnings = _dedupe(
        list(element.warnings)
        + list(spells.warnings)
        + list(gear.get("warnings") or [])
        + list(market_report.warnings))

    return AdvisorReport(
        character=character_summary(repo, character),
        element=element,
        spells=spells,
        stats=stats,
        gear=gear,
        market=market_report,
        warnings=warnings,
    )
