"""Shadow differential: solver prices vs the board the server actually sent.

The fixtures are minimal versions of `live_policy_decisions.jsonl`: an
observation carries the board before the action, `derived` carries the
solver's per-target price, and the next board carries the hp the action
produced. The tests assert the pairing and bucketing rules, not a particular
model's numbers.
"""

from __future__ import annotations

from combat.live_learn import (
    apply_damage_calibration,
    calibrate_residuals,
    prediction_for_action,
    shadow_residuals,
)


def _fighter(fid, hp, *, team="enemy", cell=300, spells=None):
    return {"id": fid, "team": team, "hp": hp, "max_hp": 200, "cell": cell,
            "alive": hp > 0, "level": 5, "spells": spells or []}


def _observation(hp, *, fight_id=7, round_no=0, derived=None, spells=None):
    return {
        "fight": {"fight_id": fight_id, "round": round_no, "in_fight": True},
        "self": _fighter(1, 60, team="player", spells=spells or []),
        "allies": [],
        "enemies": [_fighter(-1, hp)],
        "derived": derived or {},
    }


def _row(time, observation, action):
    return {"time": time, "fight_id": 7, "round": 0,
            "decision": {"observation": observation, "action": action}}


def _cast(spell_id, target=-1):
    return {"type": "cast", "spellId": spell_id, "targetId": target, "cell": 316}


def _spell(spell_id, effect_id, low, high):
    return {"id": spell_id, "name": "test", "effects": [
        {"effect_id": effect_id, "kind": "damage", "element": "earth",
         "min": low, "max": high}]}


def test_prediction_prefers_derived_then_slot_then_effects():
    spells = [_spell(61, 97, 10, 20)]
    observation = _observation(
        200, spells=spells,
        derived={"self": {"61": {"targets": {"-1": {"damage": {"expected": 17.5}}}}}})
    price = prediction_for_action(_row(1.0, observation, _cast(61)))
    assert price["source"] == "derived" and price["predicted"] == 17.5
    assert price["effect_ids"] == [97]

    no_derived = _observation(200, spells=spells)
    price = prediction_for_action(_row(1.0, no_derived, _cast(61)))
    assert price["source"] == "effects" and price["predicted"] == 15.0


def test_multiple_casts_share_one_board_update():
    spells = [_spell(61, 97, 10, 20)]
    before = _observation(200, spells=spells,
                          derived={"self": {"61": {"targets": {"-1": {
                              "damage": {"expected": 20.0}}}}}})
    # Two casts before the server publishes the new board; 40 hp total.
    after = _observation(160, round_no=1)
    rows = [
        _row(1.0, before, _cast(61)),
        _row(1.1, before, _cast(61)),
        _row(1.2, after, {"type": "pass"}),
    ]
    residuals = shadow_residuals(rows)
    assert len(residuals) == 2
    assert abs(sum(row["actual"] for row in residuals) - 40.0) < 1e-6
    assert all(row["pairing"] == "hp_update" for row in residuals)


def test_calibration_buckets_by_spell_and_effect():
    residuals = [
        {"spell_id": 61, "effect_ids": [97], "predicted": 20.0, "actual": 10.0,
         "residual": -10.0},
        {"spell_id": 61, "effect_ids": [97], "predicted": 20.0, "actual": 10.0,
         "residual": -10.0},
        {"spell_id": 61, "effect_ids": [97], "predicted": 20.0, "actual": 10.0,
         "residual": -10.0},
    ]
    calibration = calibrate_residuals(residuals)
    spell = calibration["spells"]["61"]
    effect = calibration["effects"]["97"]
    assert spell["status"] == "ready" and effect["status"] == "ready"
    assert 0.25 < spell["scale"] < 1.0
    assert abs(effect["scale"] - spell["scale"]) < 1e-6
    corrected = apply_damage_calibration(100.0, 61, [97], calibration)
    assert corrected < 100.0


def test_calibration_is_a_noop_below_min_rows():
    calibration = calibrate_residuals([
        {"spell_id": 61, "effect_ids": [97], "predicted": 20.0, "actual": 10.0,
         "residual": -10.0}])
    assert calibration["spells"]["61"]["status"] == "collecting"
    assert apply_damage_calibration(100.0, 61, [97], calibration) == 100.0


def test_fight_estimate_applies_the_correction():
    from combat.fight_estimate import EnemyFacts, EnemyMember, PlayerFacts, estimate_from_facts

    player = PlayerFacts(level=10, current_hp=100, max_hp=100,
                         dpt_by_element={"earth": 100.0}, damage_lines=1)
    enemy = EnemyFacts(members=[EnemyMember(monster_id=1, name="x", level=5,
                                            hp=100, ap=6)])
    plain = estimate_from_facts(player, enemy)
    calibration = {"version": 1, "overall": {"status": "ready", "scale": 0.5,
                                             "bias": 0.0, "rows": 10}}
    corrected = estimate_from_facts(player, enemy, damage_calibration=calibration)
    assert corrected["player"]["damage_per_turn"] == plain["player"]["damage_per_turn"]
    # The reported turns-to-kill is the observable that must move.
    assert corrected["features"]["turns_to_kill"] > plain["features"]["turns_to_kill"]
    assert any("shadow residuals" in note for note in corrected.get("assumptions") or [])
