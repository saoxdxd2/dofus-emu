"""The live learning signals must reach the decisions, not just exist.

Two numbers were trained and never consumed: `LiveLearner.price` (what one enemy
turn costs us) and the shadow damage calibration (what the server's spell price
is worth in reality). These tests fail if either hook is unwired, and they fail
if a hook is wired somewhere it cannot change an answer.

Each test names the observable that has to move. A test that only asserts "the
hook was called" would pass on a solver that ignores the value, which is the
exact bug being fixed.
"""

from __future__ import annotations

import json

import pytest

from combat import live_learn, threat
from combat.battle_solver import BattleSolver, LiveEffect
from combat.fight_estimate import (
    EnemyFacts,
    EnemyMember,
    FightAdvisor,
    PlayerFacts,
    estimate_from_facts,
)
from combat.live_learn import LiveShadowAuditor, calibrate_residuals, shadow_residuals
from combat.test_battle_solver import board_with, cell, live_spell


def _calibration(scale=0.4, bias=0.0, rows=20, status="ready", spell=1001, effect=100):
    bucket = {"status": status, "scale": scale, "bias": bias, "rows": rows}
    return {"version": 1, "spells": {str(spell): dict(bucket)},
            "effects": {str(effect): dict(bucket)}, "overall": dict(bucket)}


def _board(foe_hp=200, **kwargs):
    return board_with(cell(20, 20), cell(22, 20), foe_hp=foe_hp, **kwargs)


def _damage(solver, board):
    templates = solver._templates(board, board.my_cell, board.character_stats(),
                                  frozenset())
    return [t for t in templates if t.damage > 0]


# --------------------------------------------------------------------------
# 1. The damage calibration reaches the spell evaluator
# --------------------------------------------------------------------------

def test_calibration_changes_the_kill_test():
    """A spell the server over-prices must stop reading as a finishing blow."""
    prior = BattleSolver()
    board = _board()
    base = _damage(prior, board)[0].damage
    # Put the foe exactly on the prior's price: uncorrected it dies in one cast.
    lethal = _board(foe_hp=int(base), foe_max_hp=int(base))
    assert _damage(prior, lethal)[0].damage >= int(base)

    corrected = BattleSolver(damage_calibration=_calibration(scale=0.4))
    template = _damage(corrected, lethal)[0]
    assert template.damage == pytest.approx(base * 0.4, abs=0.05)
    assert template.damage < int(base), "the corrected price still kills"
    assert template.damage_int < int(base)


def test_calibration_changes_ap_efficiency():
    board = _board()
    prior = _damage(BattleSolver(), board)[0]
    corrected = _damage(BattleSolver(damage_calibration=_calibration(scale=0.4)), board)[0]
    assert corrected.damage < prior.damage
    assert corrected.damage / max(1, prior.damage) == pytest.approx(0.4, abs=0.02)


def test_calibration_changes_the_emitted_plan():
    """The correction has to survive into the plan the executor sends."""
    base = _damage(BattleSolver(), _board())[0].damage
    board = _board(foe_hp=2 * int(base), foe_max_hp=2 * int(base), my_ap=6)
    prior = BattleSolver().plan_turn(board)
    corrected = BattleSolver(damage_calibration=_calibration(scale=0.25)).plan_turn(board)
    assert prior.kills, "fixture must be a two-cast kill under the prior"
    assert not corrected.kills, "the corrected plan still claims the kill"
    assert corrected.expected_damage < prior.expected_damage


def test_estimate_stays_uncalibrated_so_the_auditor_has_a_reference():
    """`derived` is what the shadow audit measures against; correcting it too
    would make the scale chase its own tail back to 1.0."""
    solver = BattleSolver(damage_calibration=_calibration(scale=0.4))
    board = _board()
    me = board.me
    spell = board.spells[0]
    foe = next(f for f in board.fighters if f.team == "enemy")
    raw = solver._estimate(board, spell, board.character_stats(), foe, "damage")
    assert raw.expected_value == pytest.approx(_damage(BattleSolver(), board)[0].damage)
    assert me is not None


def test_absent_hook_and_identity_calibration_are_the_prior():
    board = _board()
    reference = _damage(BattleSolver(), board)[0].damage
    for solver in (BattleSolver(),
                   BattleSolver(damage_calibration=None),
                   BattleSolver(damage_calibration=lambda: None),
                   BattleSolver(damage_calibration=_calibration(scale=1.0, bias=0.0)),
                   BattleSolver(damage_calibration=_calibration(status="collecting")),
                   BattleSolver(damage_calibration=lambda: {"version": 1})):
        assert _damage(solver, board)[0].damage == pytest.approx(reference)


def test_a_broken_hook_degrades_to_the_prior():
    def explode():
        raise RuntimeError("model file is corrupt")

    board = _board()
    reference = _damage(BattleSolver(), board)[0].damage
    assert _damage(BattleSolver(damage_calibration=explode), board)[0].damage == (
        pytest.approx(reference))


def test_the_hook_is_asked_again_each_turn():
    """A calibration captured at construction would price the rest of the
    session with the residuals of the previous one."""
    seen = []

    def hook():
        seen.append(len(seen))
        return _calibration(scale=0.4 if len(seen) > 1 else 1.0)

    board = _board()
    solver = BattleSolver(damage_calibration=hook)
    first = _damage(solver, board)[0].damage
    second = _damage(solver, board)[0].damage
    assert seen, "the calibration hook was never called"
    assert second < first


# --------------------------------------------------------------------------
# 2. The learned enemy-turn price reaches the cell threat
# --------------------------------------------------------------------------

def test_turn_price_rescales_the_per_cell_prices():
    board = _board()
    plain = threat.build(board)
    here = plain.at(board.my_cell)
    assert here > 0, "fixture must expose the character, or the scale is unidentifiable"
    priced = threat.build(board, turn_price=here * 3.0)
    assert priced.turn_scale == pytest.approx(3.0)
    assert priced.at(board.my_cell) == pytest.approx(here * 3.0, rel=1e-6)
    assert plain.turn_scale == 1.0 and plain.turn_price is None


def test_turn_price_is_clamped_and_rejects_nonsense():
    board = _board()
    here = threat.build(board).at(board.my_cell)
    assert threat.build(board, turn_price=here * 1000).turn_scale == threat.MAX_TURN_SCALE
    assert threat.build(board, turn_price=0.0).turn_scale == threat.MIN_TURN_SCALE
    for bad in (float("nan"), float("inf"), -5.0):
        model = threat.build(board, turn_price=bad)
        assert model.turn_scale == 1.0 and model.turn_price is None


def test_a_cell_nothing_can_reach_has_no_scale():
    far = board_with(cell(19, 0), cell(0, 20))
    model = threat.build(far, turn_price=999.0)
    assert model.at(far.my_cell) == 0.0
    assert model.turn_scale == 1.0


def test_turn_price_reaches_the_plan_the_solver_scores():
    """The measured turn cost has to arrive at the plan, not stop at the model.

    This asserts exposure, not the chosen cell. On the 333 recorded live boards
    the rescale moved the emitted action 0 times: `swing` is a difference of two
    prices from the same model, so a uniform rescale largely cancels out of the
    ranking. Exposure is the observable that does carry it.
    """
    board = _board(foe_hp=400, foe_max_hp=400, my_mp=4)
    plain = BattleSolver().plan_turn(board)
    priced = BattleSolver(enemy_turn_price=lambda payload: 400.0).plan_turn(board)
    assert plain.exposure > 0, "fixture must expose the character"
    assert priced.exposure > plain.exposure


def test_the_learner_is_fed_the_record_not_a_reconstruction():
    """`to_fight_state` invents `my_ap`/`my_mp` the page omits. The learner is
    fitted on the page's payloads, so a reconstruction is out of distribution:
    measured on 333 live boards it moved those features on 98 and 64 boards and
    drove the price to 0.0 where it mattered."""
    from combat.battle_solver import Board

    payload = _board(foe_hp=400, foe_max_hp=400).to_fight_state()
    payload.pop("my_ap", None)
    payload.pop("my_mp", None)
    board = Board.from_fight_state(payload)
    assert board.source_payload is payload
    seen = []
    BattleSolver(enemy_turn_price=lambda p: seen.append(p) or 1.0).plan_turn(board)
    assert seen, "the hook was never called"
    assert seen[0] is payload, "the hook got a reconstruction, not the record"
    assert "my_ap" not in seen[0] and "my_mp" not in seen[0]


def test_turn_price_hook_receives_the_live_payload_shape():
    seen = []

    def hook(payload):
        seen.append(payload)
        return None

    board = _board()
    BattleSolver(enemy_turn_price=hook).plan_turn(board)
    assert seen, "the turn-price hook was never called"
    assert "fighters" in seen[0] and "my_cell" in seen[0]
    assert seen[0]["my_cell"] == board.my_cell


def test_absent_turn_price_is_the_prior():
    board = _board(foe_hp=400, foe_max_hp=400, my_mp=4)
    reference = BattleSolver().plan_turn(board)
    for solver in (BattleSolver(enemy_turn_price=None),
                   BattleSolver(enemy_turn_price=lambda payload: None)):
        plan = solver.plan_turn(board)
        assert plan.final_cell == reference.final_cell
        assert [(a.kind, a.move_to, a.spell_id, a.target_id) for a in plan.actions] == [
            (a.kind, a.move_to, a.spell_id, a.target_id) for a in reference.actions]


# --------------------------------------------------------------------------
# 3. The pre-fight estimate uses the measured incoming damage
# --------------------------------------------------------------------------

def _facts():
    player = PlayerFacts(level=10, current_hp=100, max_hp=100, mp=3,
                         dpt_by_element={"earth": 100.0}, damage_lines=1)
    enemy = EnemyFacts(members=[EnemyMember(monster_id=1, name="x", level=5,
                                            hp=100, ap=6)])
    return player, enemy


def test_learned_price_replaces_the_level_heuristic():
    player, enemy = _facts()
    plain = estimate_from_facts(player, enemy)
    priced = estimate_from_facts(player, enemy, enemy_turn_price=42.0)
    assert plain["incoming_source"] == "level_ap_heuristic"
    assert priced["incoming_source"] == "live_learner"
    assert priced["incoming_per_turn"] == pytest.approx(42.0)
    assert priced["incoming_per_turn"] != plain["incoming_per_turn"]
    assert priced["features"]["turns_to_die"] != plain["features"]["turns_to_die"]


def test_learned_price_is_not_discounted_by_resist_again():
    """The learned number is life already lost, so it is after resistances."""
    player, enemy = _facts()
    player.resist_percent = {"earth": 50.0}
    priced = estimate_from_facts(player, enemy, enemy_turn_price=42.0)
    assert priced["incoming_per_turn"] == pytest.approx(42.0)


def test_no_learned_price_keeps_the_heuristic():
    player, enemy = _facts()
    reference = estimate_from_facts(player, enemy)
    assert estimate_from_facts(player, enemy, enemy_turn_price=None) == reference


def test_a_clamped_zero_price_is_not_a_measurement():
    """`OnlineValue.predict` floors a negative dot at 0.0. Read as a measured
    incoming damage that says the fight is free: on the 333 recorded boards 103
    prices were 0.0, all from negative dots, and they flipped 34 bootstrap gates
    to `allowed`. The accessor must report "no measurement" instead."""
    class _Model:
        ready = True

        def predict(self, features):
            return 0.0

    class _Learner:
        model = _Model()

        def price(self, board):
            return 0.0

    import combat.live_learn as ll

    original = ll.default_learner
    ll.default_learner = lambda: _Learner()
    try:
        assert ll.enemy_turn_price({}) is None
    finally:
        ll.default_learner = original

    player, enemy = _facts()
    reference = estimate_from_facts(player, enemy, bootstrap=True)
    zeroed = estimate_from_facts(player, enemy, bootstrap=True, enemy_turn_price=0.0)
    assert zeroed["incoming_per_turn"] == 0.0
    assert zeroed["allowed"] != reference["allowed"] or reference["allowed"], (
        "fixture must be a fight the heuristic refuses, or the flip proves nothing")


def test_advisor_prices_a_group_before_the_fight_is_joined():
    """`annotate` runs on the map, where there is no board yet. The hook must
    still be fed a live-shaped payload, or the pre-fight gate never learns."""
    seen = []

    def hook(payload):
        seen.append(payload)
        return 30.0

    advisor = FightAdvisor(store=None, enemy_turn_price=hook)
    player, enemy = _facts()
    advisor.character = object()
    advisor._player_cache = player
    advisor._spellbook = ([], {})
    priced = advisor._enemy_turn_price(player, enemy, None)
    assert priced == 30.0
    assert seen and seen[0]["fighters"], "no board was synthesised for the hook"
    assert any(f.get("is_me") for f in seen[0]["fighters"])


# --------------------------------------------------------------------------
# 4. The live server actually produces the calibration
# --------------------------------------------------------------------------

def _decision_rows():
    def fighter(fid, hp, team):
        return {"id": fid, "team": team, "hp": hp, "max_hp": 200, "cell": 300,
                "alive": hp > 0, "level": 5,
                "spells": [{"id": 61, "effects": [
                    {"effect_id": 97, "kind": "damage", "min": 10, "max": 30}]}]
                if team == "player" else []}

    def observation(hp, rnd):
        return {"fight": {"fight_id": 7, "round": rnd}, "my_id": 1,
                "self": fighter(1, 60, "player"), "allies": [],
                "enemies": [fighter(-1, hp, "enemy")],
                "derived": {"self": {"61": {"targets": {
                    "-1": {"damage": {"expected": 20.0}}}}}}}

    rows = []
    hp = 200
    for index in range(12):
        rows.append({"time": float(index), "fight_id": 7, "round": index, "my_id": 1,
                     "decision": {"observation": observation(hp, index),
                                  "action": {"type": "cast", "spellId": 61,
                                             "targetId": -1}}})
        hp -= 10
    rows.append({"time": 12.0, "fight_id": 7, "round": 12, "my_id": 1,
                 "decision": {"observation": observation(hp, 12),
                              "action": {"type": "pass"}}})
    # Two fights whose board updates arrive out of cast order. Fight 8 casts
    # second but is credited first, so arrival order and time order disagree --
    # which is the only situation where the bucket EWMAs can diverge.
    rows.extend([
        {"time": 20.0, "fight_id": 7, "round": 20, "my_id": 1,
         "decision": {"observation": observation(200, 20),
                      "action": {"type": "cast", "spellId": 61, "targetId": -1}}},
        {"time": 21.0, "fight_id": 8, "round": 20, "my_id": 2,
         "decision": {"observation": observation(200, 20),
                      "action": {"type": "cast", "spellId": 61, "targetId": -1}}},
        {"time": 22.0, "fight_id": 8, "round": 21, "my_id": 2,
         "decision": {"observation": observation(180, 21),
                      "action": {"type": "pass"}}},
        {"time": 23.0, "fight_id": 7, "round": 21, "my_id": 1,
         "decision": {"observation": observation(170, 21),
                      "action": {"type": "pass"}}},
    ])
    return rows


def test_live_auditor_matches_the_offline_audit(tmp_path):
    rows = _decision_rows()
    batch = calibrate_residuals(shadow_residuals(rows))
    path = tmp_path / "cal.json"
    live = LiveShadowAuditor(calibration_path=path, every=1)
    for row in rows:
        live.observe(row)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["rows"] == batch["rows"] > 0
    for section in ("overall", "spells", "effects"):
        assert written[section] == batch[section], section


def test_live_auditor_never_trades_a_richer_file_for_a_thinner_one(tmp_path):
    rows = _decision_rows()
    rich = calibrate_residuals(shadow_residuals(rows))
    path = tmp_path / "cal.json"
    path.write_text(json.dumps(rich), encoding="utf-8")
    live = LiveShadowAuditor(calibration_path=path, max_residuals=2, every=1)
    for row in rows:
        live.observe(row)
    assert live.writes == 0 and live.skipped_thinner > 0
    assert json.loads(path.read_text(encoding="utf-8"))["rows"] == rich["rows"]


def test_flush_writes_the_tail_the_cadence_left(tmp_path):
    rows = _decision_rows()
    path = tmp_path / "cal.json"
    live = LiveShadowAuditor(calibration_path=path, every=1000)
    for row in rows:
        live.observe(row)
    assert not path.exists(), "the cadence should not have fired yet"
    assert live.flush() is not None
    assert json.loads(path.read_text(encoding="utf-8"))["rows"] > 0
    assert live.flush() is None, "a second flush with nothing new must be a no-op"


def test_the_server_decision_path_feeds_the_auditor(tmp_path, monkeypatch):
    """The CLI-only auditor was the gap: nothing in a running install wrote the
    file the solver reads. This goes through `decide`, not around it."""
    from combat import combat_ai_server as server

    rows = _decision_rows()
    path = tmp_path / "cal.json"
    auditor = LiveShadowAuditor(calibration_path=path, every=1)
    monkeypatch.setattr(server, "_SHADOW_AUDITOR", auditor)
    monkeypatch.setattr(server, "_log_decision",
                        lambda payload, decision: {"time": 0.0, "fight_id": 7,
                                                   "round": 0, "my_id": 1,
                                                   "decision": decision})
    monkeypatch.setattr(server, "_learner", lambda: _NoopLearner())
    monkeypatch.setattr(server, "_threat_observer", lambda: _NoopLearner())

    class _Policy:
        def __init__(self):
            self.index = 0

        def decide(self, payload):
            row = rows[self.index]["decision"]
            self.index += 1
            return dict(row)

    monkeypatch.setattr(server, "POLICY", _Policy())
    for row in rows:
        server.decide({"fight_id": 7, "round": row["round"], "my_id": 1})
    assert auditor.decisions == len(rows)
    assert auditor.paired > 0, "the server never paired a cast"
    assert path.exists(), "the server never wrote the calibration"


class _NoopLearner:
    def observe(self, payload):
        return None

    def report(self):
        return {}


# --------------------------------------------------------------------------
# 5. The live entry points inject the hooks
# --------------------------------------------------------------------------

def test_live_policy_injects_both_hooks():
    from combat.live_policy import LivePolicy

    try:
        policy = LivePolicy()
    except Exception:
        pytest.skip("no policy checkpoint available in this checkout")
    assert policy.solver.enemy_turn_price is live_learn.enemy_turn_price
    assert policy.solver.damage_calibration is live_learn.default_damage_calibration


def test_the_tool_executor_injects_the_price_hook():
    """`fight_advisor` is the desktop side's entry to the pre-fight gate."""
    from agent.executor import ToolExecutor

    executor = ToolExecutor.__new__(ToolExecutor)
    executor.fights = None
    advisor = ToolExecutor.fight_advisor(executor)
    assert advisor.enemy_turn_price is live_learn.enemy_turn_price


def test_default_accessors_reload_when_the_file_changes(tmp_path, monkeypatch):
    path = tmp_path / "cal.json"
    monkeypatch.setattr(live_learn, "DEFAULT_DAMAGE_CALIBRATION", path)
    monkeypatch.setattr(live_learn, "_CALIBRATION", None)
    monkeypatch.setattr(live_learn, "_CALIBRATION_PATH", None)
    monkeypatch.setattr(live_learn, "_CALIBRATION_STAMP", None)
    assert live_learn.default_damage_calibration() is None
    path.write_text(json.dumps(_calibration(scale=0.4)), encoding="utf-8")
    first = live_learn.default_damage_calibration()
    assert first is not None and first["spells"]["1001"]["scale"] == 0.4
    assert live_learn.default_damage_calibration() is first, "unchanged file: same object"
    import os
    import time
    path.write_text(json.dumps(_calibration(scale=0.9)), encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))
    second = live_learn.default_damage_calibration()
    assert second is not first and second["spells"]["1001"]["scale"] == 0.9