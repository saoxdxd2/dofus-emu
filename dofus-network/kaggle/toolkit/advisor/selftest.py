"""Offline checks for the advisor. No client, no network, no spending.

Run with `python -m advisor selftest` from `dofus-network/`. The fixtures use
the real encyclopedia/spell data and synthetic bridge payloads, so a shape
change in either side fails here before it reaches a live character.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .elements import choose_element, marginal_primary_damage
from .gear import GearAdvisor
from .market import analyze_market
from .models import (
    CharacterSnapshot,
    MarketSnapshot,
    effect_number,
    merge_market,
)
from .report import build_report
from .repository import OfficialSpell, Repository, effect_roll
from .spells import (
    budget_options,
    choose_budget,
    effective_spell_states,
    parse_spell_power,
    plan_spells,
)

_FAILURES: List[str] = []
_CHECKS = 0


def check(label: str, condition: Any, detail: Any = "") -> None:
    global _CHECKS
    _CHECKS += 1
    if condition:
        return
    text = "%s%s" % (label, (": %s" % detail) if detail else "")
    _FAILURES.append(text)
    print("FAIL %s" % text)


def _stat(total: float) -> Dict[str, float]:
    return {"total": total, "base": total, "equipment": 0, "bonus": 0, "additional": 0}


def character_payload(
    level: int = 60,
    breed: int = 8,
    spells: Optional[List[Dict[str, Any]]] = None,
    points: Optional[Dict[str, Any]] = None,
    equipment: Optional[List[Dict[str, Any]]] = None,
    inventory_gids: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    return {
        "at": 1700000000000,
        "character": {"id": 42, "name": "Selftest", "level": level,
                      "breed": breed, "sex": "male"},
        "position": {"map_id": 123, "cell": 456, "x": 3, "y": -7},
        "stats": {
            "strength": _stat(200),
            "intelligence": _stat(120),
            "chance": _stat(80),
            "agility": _stat(150),
            "actionPoints": 9,
            "movementPoints": 4,
            "lifePoints": 900,
            "effectiveMaxLifePoints": 1200,
            "damageBonus": 8,
            "criticalHit": 5,
        },
        "points": points if points is not None else {
            "level": level,
            "used_spell_points": 43,
            "character_budget": level - 1,
            "character_cap_left": 16,
            "raw_spells_points": 16,
            "raw_account_spells_points": 0,
            "remaining_if_raw_is_balance": 16,
            "stats_points_raw": 0,
        },
        "spells": spells or [],
        "equipment": equipment or [],
        "sets": [],
        "inventory": {"kamas": 12345,
                      "gids": inventory_gids or {}},
        "db": {"counts": {"Items": 2235, "Effects": 45}},
    }


def spell_row(spell_id: int, grade: int, cap: int, breed: int = 8) -> Dict[str, Any]:
    return {
        "spell_id": spell_id,
        "breed_id": breed,
        "is_item": False,
        "level": grade,
        "max_grade": 6,
        "max_grade_for_level": cap,
        "grades": [],
    }


def _cost_checks(repo: Repository) -> None:
    check("cost 1->2", repo.grade_costs(1, 2) == 1)
    check("cost 2->4", repo.grade_costs(2, 4) == 5)
    check("cost 1->6", repo.grade_costs(1, 6) == 15)
    check("cost 4->5", repo.grade_costs(4, 5) == 4)
    check("cost never negative", repo.grade_costs(5, 4) == 0)
    check("cost from zero", repo.grade_costs(0, 1) == 0)


def _repository_checks(repo: Repository) -> None:
    check("15 breeds", len(repo.by_breed) == 15, len(repo.by_breed))
    check("330 spells", len(repo.spells) == 330, len(repo.spells))
    check("iop spells", len(repo.by_breed.get(8, [])) == 22)
    check("breed names", repo.breed_names.get(8, "").lower() == "iop",
          repo.breed_names.get(8))
    check("items loaded", len(repo.items) > 2000, len(repo.items))
    check("sets loaded", len(repo.sets) > 300, len(repo.sets))

    pursuit = repo.spell(8105)
    check("pursuit exists", pursuit is not None)
    if pursuit is None:
        return
    check("cap at 1", repo.grade_cap(pursuit, None, 1) == 4)
    check("cap at 50", repo.grade_cap(pursuit, None, 50) == 4)
    check("cap at 60", repo.grade_cap(pursuit, None, 60) == 5)
    check("cap at 101", repo.grade_cap(pursuit, None, 101) == 6)

    class Live:
        level = 3
        max_grade_for_level = 3

    check("live cap wins", repo.grade_cap(pursuit, Live(), 101) == 3)
    Live.level = 0
    check("locked spell cap", repo.grade_cap(pursuit, Live(), 101) == 0)

    check("neutral maps to earth",
          effect_roll({"text": "10 to 12 (Neutral damage)"}) == ("earth", 10, 12))
    check("structured damage",
          effect_roll({"damage": {"element": "water", "min_roll": 3, "max_roll": 5}})
          == ("water", 3, 5))
    check("heal is not damage", effect_roll({"heal": {"min_roll": 1, "max_roll": 2}}) is None)
    check("effect average", effect_number({"dice_num": 10, "dice_side": 20}) == 15.0)
    check("fixed effect value", effect_number({"value": 7, "dice_num": 1,
                                              "dice_side": 3}) == 7.0)


def _element_checks(repo: Repository) -> None:
    character = CharacterSnapshot.from_bridge(character_payload())
    decision = choose_element(repo, character, "auto")
    check("element chosen", decision.chosen in ("earth", "fire", "water", "air"),
          decision.chosen)
    check("element reason", bool(decision.reason))
    check("element has scores", len(decision.scores) == 4)
    check("synthetic spellbook warned", any("live spellbook" in w.lower()
                                            for w in decision.warnings),
          decision.warnings)

    requested = choose_element(repo, character, "earth")
    check("requested element honored", requested.chosen == "earth", requested.chosen)
    unknown = choose_element(repo, character, "banana")
    check("unknown element falls back", unknown.chosen in
          ("earth", "fire", "water", "air"))
    check("unknown element warns", any("unknown element" in w.lower()
                                       for w in unknown.warnings))

    stats = marginal_primary_damage(repo, character, decision.chosen)
    check("marginal primary shape", isinstance(stats.get("primary"), dict)
          and len(stats["primary"]) == 4)
    check("marginal preferred", stats["primary"][decision.chosen]["preferred"] is True)
    check("marginal positive", stats["primary"][decision.chosen]["damage_per_point"] > 0)


def _budget_checks() -> None:
    points = {
        "character_cap_left": 5,
        "raw_spells_points": 3,
        "raw_account_spells_points": 2,
        "remaining_if_raw_is_balance": 5,
    }
    options = budget_options(points)
    chosen = choose_budget(options)
    check("merged budget keeps cap token", "character_cap_left" in chosen.label.split(" | "),
          chosen.label)
    check("merged budget value", chosen.points == 5, chosen.points)
    check("merged budget assumptions",
          "client's own cap" in chosen.assumption
          and "raw spellsPoints" in chosen.assumption,
          chosen.assumption)

    zero_cap = {
        "character_cap_left": 0,
        "raw_spells_points": 2,
        "raw_account_spells_points": 4,
        "remaining_if_raw_is_balance": 6,
    }
    chosen_zero = choose_budget(budget_options(zero_cap))
    check("zero cap prefers account pool", chosen_zero.points == 4, chosen_zero.points)

    unknown = choose_budget(budget_options({}))
    check("unknown budget", unknown.points == 0 and unknown.label == "unknown")


def _spell_checks(repo: Repository) -> None:
    character = CharacterSnapshot.from_bridge(character_payload(
        spells=[
            spell_row(8105, 4, 5),   # Pursuit, air
            spell_row(8121, 3, 5),   # Concentration, earth
            spell_row(8129, 2, 5),   # Sentence, fire
            spell_row(8135, 1, 5),   # Strengthstorm, fire
        ]))
    stats = character.to_character_stats()
    power = parse_spell_power(repo.spell(8105), 4, stats, "air", character.max_hp)
    check("spell parses damage", power.damage > 0, power.damage)
    check("spell element", power.element == "air", power.element)
    check("spell has a rotation line", power.damage_lines >= 1)

    # Pressure lists its hit twice ("Earth damage" + "Earth steal"). That is
    # one hit that heals, not two hits.
    pressure = parse_spell_power(repo.spell(8139), 1, stats, "earth",
                                 character.max_hp)
    check("steal line folded into its twin", pressure.damage_lines == 1,
          pressure.damage_lines)
    check("steal heals for the hit", pressure.heal > 0, pressure.heal)

    precipitation = parse_spell_power(repo.spell(8289), 1, stats, "fire",
                                      character.max_hp)
    check("utility-only spell still prices", precipitation.utility > 0,
          precipitation.utility)

    live, synthetic, _warnings = effective_spell_states(repo, character)
    check("live spellbook used", not synthetic and len(live) == 4, len(live))

    plan = plan_spells(repo, character, "air", budget=10)
    check("plan respects budget", plan.spent <= plan.budget, (plan.spent, plan.budget))
    check("plan improves rotation",
          plan.rotation_after.value >= plan.rotation_before.value - 1e-9,
          (plan.rotation_before.value, plan.rotation_after.value))
    check("plan has scenarios", len(plan.budget_scenarios) >= 1)
    check("plan budget from caller", plan.budget_source == "explicit")

    synthetic_character = CharacterSnapshot.from_bridge(character_payload())
    plan2 = plan_spells(repo, synthetic_character, "air")
    check("synthetic plan labelled", plan2.synthetic_spellbook is True)
    check("synthetic plan budget", plan2.budget == 16, plan2.budget)
    check("synthetic plan spends within budget", plan2.spent <= plan2.budget,
          (plan2.spent, plan2.budget))
    check("synthetic plan can upgrade", bool(plan2.upgrades))

    # A duplicated effect dict used to be matched by list.index(), which
    # always returns the first copy. Two identical rows must both be scored.
    raw = {
        "id": 999001,
        "name_en": "Duplicate Probe",
        "breed_id": 8,
        "levels": [{
            "level": 1,
            "required_level": 1,
            "ap_cost": 3,
            "min_range": 1,
            "max_range": 5,
            "characteristics": {"max_cast_per_turn": 2,
                                "max_cast_per_target": 2,
                                "critical_hit_percent": 0.0},
            "normal_effects": [{"text": "10 to 12 (Fire damage)"},
                               {"text": "10 to 12 (Fire damage)"}],
            "critical_effects": [],
        }],
    }
    probe = OfficialSpell.from_raw(raw)
    probe_power = parse_spell_power(probe, 1, stats, "fire", character.max_hp)
    check("duplicate effect rows both scored", probe_power.damage_lines == 2,
          probe_power.damage_lines)
    check("duplicate effect damage", probe_power.damage > 0, probe_power.damage)


def _gear_checks(repo: Repository) -> None:
    character = CharacterSnapshot.from_bridge(character_payload())
    advisor = GearAdvisor(repo, character, "fire", MarketSnapshot())
    data = advisor.to_dict()
    check("gear has all slots", len(data["current_slots"]) == 14,
          len(data["current_slots"]))
    check("gear no equipment", data["current_score"] == 0.0, data["current_score"])
    check("gear weapon warning", any("weapon" in w.lower()
                                     for w in data["warnings"]))
    check("gear inventory warning", any("inventory gid" in w.lower()
                                        for w in data["warnings"]))
    check("gear set candidates", isinstance(data["set_candidates"], list))
    for candidate in data["set_candidates"]:
        check("set candidate coverage", 0.0 < candidate["coverage"] <= 1.0,
              candidate["coverage"])
        check("set candidate action", bool(candidate["action"]))
        check("set candidate counts",
              candidate["piece_count"] >= candidate["known_pieces"] >= 0
              and candidate["owned_pieces"] + candidate["missing_pieces"]
              == candidate["known_pieces"],
              (candidate["piece_count"], candidate["known_pieces"],
               candidate["owned_pieces"], candidate["missing_pieces"]))

    item = next((row for row in repo.items.values()
                 if row.level <= 60 and row.set_ids and row.ingredients), None)
    if item is not None:
        equipped = CharacterSnapshot.from_bridge(character_payload(
            equipment=[{
                "gid": item.id,
                "position": 6,
                "template": {"name_id": 1, "level": item.level,
                             "type_id": item.type_id,
                             "set_ids": list(item.set_ids)},
            }],
            inventory_gids={str(item.id): 1}))
        owned = GearAdvisor(repo, equipped, "fire", MarketSnapshot()).to_dict()
        check("equipped item scores", owned["current_score"] > 0,
              owned["current_score"])
        check("equipped gid is owned",
              item.id in equipped.owned_gids)


def _market_checks(repo: Repository) -> None:
    gids = sorted(repo.items)
    gid_a, gid_b = gids[0], gids[1]
    payload = {
        "captured_at": 1700000000000,
        "average_at": 1699999990000,
        "average_age_ms": 10000,
        "average_count": 2,
        "listing_count": 2,
        "requested": True,
        "prices": [[gid_a, 1000], [gid_b, 500]],
        "listings": {
            str(gid_a): {"count": 3, "min": 500, "median": 600, "max": 700,
                         "age_ms": 1000},
            str(gid_b): {"count": 1, "min": 300, "median": 300, "max": 300,
                         "age_ms": 1000},
        },
    }
    market = MarketSnapshot.from_bridge(payload)
    check("market prices parsed", market.average(gid_a) == 1000)
    check("market listings parsed", market.best_listing(gid_b) == 300)

    character = CharacterSnapshot.from_bridge(character_payload(
        inventory_gids={str(gid_b): 2}))
    report = analyze_market(repo, character, market, min_price=100,
                            min_discount=0.15)
    buy_gids = {signal.gid for signal in report.buy_signals}
    sell_gids = {signal.gid for signal in report.sell_signals}
    check("market buy signal", gid_a in buy_gids, buy_gids)
    check("market sell signal", gid_b in sell_gids, sell_gids)
    check("market liquidation", abs(report.owned_liquidation_value - 1000.0) < 1e-6,
          report.owned_liquidation_value)
    sell_b = next(signal for signal in report.sell_signals if signal.gid == gid_b)
    check("market sell verdict", sell_b.sell_verdict == "sell_high",
          sell_b.sell_verdict)
    check("market quick sale undercuts", sell_b.quick_sale == 299,
          sell_b.quick_sale)

    empty = analyze_market(repo, character, MarketSnapshot())
    check("empty market warns", any("no average prices" in w.lower()
                                    for w in empty.warnings))

    merged = merge_market(market, MarketSnapshot(prices={gid_a: 1200}))
    check("merge keeps newest price", merged.average(gid_a) == 1200,
          merged.average(gid_a))
    check("merge keeps old listing", merged.best_listing(gid_b) == 300)


def _report_checks(repo: Repository) -> None:
    gid = sorted(repo.items)[0]
    market = MarketSnapshot.from_bridge({
        "captured_at": 1700000000000,
        "average_at": 1699999990000,
        "average_age_ms": 10000,
        "prices": [[gid, 1000]],
        "listings": {str(gid): {"count": 2, "min": 600, "median": 650,
                                "max": 700, "age_ms": 1000}},
    })
    character = CharacterSnapshot.from_bridge(character_payload(
        spells=[spell_row(8105, 4, 5), spell_row(8121, 3, 5)]))
    report = build_report(repo, character, market, focus="auto", budget=6)
    data = report.to_dict()
    check("report summary", len(data["summary"]) >= 4)
    check("report has character", data["character"]["level"] == 60)
    check("report has gear", "set_candidates" in data["gear"])
    check("report has market", data["market"]["average_count"] == 1)
    check("report warnings are strings", all(isinstance(w, str) for w in data["warnings"]))
    text = report.render_text()
    for header in ("Advisor report", "ELEMENT", "SPELLS", "STATS", "GEAR", "MARKET"):
        check("text section %s" % header, header in text)
    try:
        json.dumps(data, ensure_ascii=False)
        serialisable = True
        error = ""
    except (TypeError, ValueError) as exc:  # pragma: no cover - diagnostic path
        serialisable = False
        error = str(exc)
    check("report JSON serialisable", serialisable, error)


def _cli_checks() -> None:
    from . import cli

    check("bridge envelope unwrapped",
          cli._client_data({"ok": True, "result": {"ok": True, "data": {"a": 1}}})
          == {"a": 1})
    check("saved envelope unwrapped",
          cli._payload_data({"result": {"ok": True, "data": {"b": 2}}})
          == {"b": 2})
    try:
        cli._client_data({"ok": True, "result": {"ok": False, "error": "no npc"}})
        raised = False
    except RuntimeError as exc:
        raised = "no npc" in str(exc)
    check("game refusal is an error", raised)

    # Page the fake average table without a bridge or a game.
    original = cli.run_directive
    calls: List[Dict[str, Any]] = []

    def fake(tool, directive, args, timeout=30.0, instance=None):
        calls.append(dict(args))
        offset = int(args.get("offset") or 0)
        total = 45000
        rows = [[1000 + index, 100 + index]
                for index in range(offset, min(offset + 20000, total))]
        return {
            "average_count": total,
            "prices": rows,
            "listings": {} if offset else {"1000": {"count": 1, "min": 90,
                                                   "median": 90, "max": 90}},
            "average_at": 1700000000000,
            "requested": False,
            "request_error": None,
        }

    try:
        cli.run_directive = fake
        market = cli.capture_market(refresh=False, wait=0, pages=5)
    finally:
        cli.run_directive = original
    check("capture pages all averages", market["average_count"] == 45000,
          market["average_count"])
    check("capture page count", market["pages"] == 3, market["pages"])
    check("capture keeps listings", market["listing_count"] == 1,
          market["listing_count"])
    check("capture requests correct offsets", [row.get("offset") for row in calls]
          == [0, 20000, 40000], [row.get("offset") for row in calls])


def run() -> int:
    _FAILURES.clear()
    global _CHECKS
    _CHECKS = 0
    print("[advisor] loading repository ...")
    repo = Repository()
    print("[advisor] %d items, %d sets, %d spells"
          % (len(repo.items), len(repo.sets), len(repo.spells)))
    _cost_checks(repo)
    _repository_checks(repo)
    _element_checks(repo)
    _budget_checks()
    _spell_checks(repo)
    _gear_checks(repo)
    _market_checks(repo)
    _report_checks(repo)
    _cli_checks()
    if _FAILURES:
        print("[advisor] selftest FAILED: %d of %d checks" % (len(_FAILURES), _CHECKS))
        return len(_FAILURES)
    print("[advisor] selftest passed: %d checks" % _CHECKS)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
